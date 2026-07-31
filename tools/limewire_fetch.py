#!/usr/bin/env python3
"""Fetch and decrypt a LimeWire-shared file into the working directory.

Usage:
    python3 limewire_fetch.py <limewire-share-url> [output-path]

The URL must be a full LimeWire share link, e.g.
    https://limewire.com/d/4MK2j#3GzKbUBJZn

Flow (based on the public reverse-engineering documented in the MIT-licensed
`magsync` project, github.com/izzoa/magsync):
  1. GET https://limewire.com/d/<sharing_id>  -> SSR HTML + session JWT cookie
  2. parse `sharingBucketContentData` from the React-Router turbo-stream
  3. derive AES-256 key: fragment -> PBKDF2(SHA-256) -> AES-KW unwrap
     -> ECDH P-256 shared secret
  4. POST https://api.limewire.com/sharing/download/<bucket_id> -> presigned URL
  5. download ciphertext, decrypt AES-256-CTR
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives.keywrap import aes_key_unwrap

# --------------------------------------------------------------------------
# Encryption constants (LimeWire client-side). Auto-extracted below if the
# hardcoded values ever become stale.
# --------------------------------------------------------------------------
SHARING_SALT_B64 = "wvsoOvbI854RHQMiSiPmnw=="
FILE_IV_B64 = "C8aZG384/qPpBzg="
PBKDF2_ITERATIONS = 100_000

UUID_RE = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"


def parse_share_url(url: str) -> tuple[str, str]:
    parsed = urlsplit(url)
    if parsed.netloc not in ("limewire.com", "www.limewire.com"):
        raise ValueError(f"not a limewire.com URL: {url}")
    prefix = "/d/"
    if not parsed.path.startswith(prefix):
        raise ValueError(f"not a /d/ share URL: {url}")
    sharing_id = parsed.path[len(prefix):]
    fragment = parsed.fragment
    if not sharing_id or not fragment:
        raise ValueError("missing sharing id or fragment key")
    return sharing_id, fragment


def _is_uuid(s: str) -> bool:
    return len(s) == 36 and s.count("-") == 4


# --- React Router turbo-stream decoding (ported from magsync, MIT) ----------

_ENQUEUE_RE = re.compile(r"streamController\.enqueue\(")


def decode_react_stream(html: str):
    chunks: list[str] = []
    for m in _ENQUEUE_RE.finditer(html):
        i = m.end()
        if i >= len(html) or html[i] != '"':
            continue
        i += 1
        start = i
        while i < len(html):
            c = html[i]
            if c == "\\":
                i += 2
                continue
            if c == '"':
                break
            i += 1
        try:
            chunks.append(json.loads('"' + html[start:i] + '"'))
        except (ValueError, json.JSONDecodeError):
            continue
    if not chunks:
        return None
    try:
        arr = json.loads("".join(chunks))
    except (ValueError, json.JSONDecodeError):
        return None
    if not isinstance(arr, list) or not arr:
        return None

    memo: dict[int, object] = {}

    def resolve(index, active: frozenset):
        if not isinstance(index, int) or index < 0 or index >= len(arr):
            return None
        if index in memo:
            return memo[index]
        if index in active:
            return None
        node = arr[index]
        if isinstance(node, dict):
            active2 = active | {index}
            out = {}
            for k, v in node.items():
                key = arr[int(k[1:])] if isinstance(k, str) and k.startswith("_") else k
                out[key] = resolve(v, active2) if isinstance(v, int) else v
            memo[index] = out
            return out
        if isinstance(node, list):
            if node and isinstance(node[0], str):
                memo[index] = node[1] if (node[0] == "D" and len(node) > 1) else node
                return memo[index]
            active2 = active | {index}
            out = [resolve(e, active2) if isinstance(e, int) else e for e in node]
            memo[index] = out
            return out
        memo[index] = node
        return node

    return resolve(0, frozenset())


def _find_key_entry(obj, target) -> tuple[bool, object]:
    if isinstance(obj, dict):
        if target in obj:
            return True, obj[target]
        for value in obj.values():
            present, found = _find_key_entry(value, target)
            if present:
                return True, found
    elif isinstance(obj, list):
        for value in obj:
            present, found = _find_key_entry(value, target)
            if present:
                return True, found
    return False, None


def _ssr_field(html: str, field: str) -> str | None:
    pattern = r'\\"' + re.escape(field) + r'\\",\\"([^"\\]+)\\"'
    m = re.search(pattern, html)
    return m.group(1) if m else None


def extract_metadata(html: str, sharing_id: str) -> dict:
    decoded = decode_react_stream(html)
    if decoded is not None:
        present, container = _find_key_entry(decoded, "sharingBucketContentData")
        if present and isinstance(container, dict) and container.get("ok") is True:
            value = container.get("value") or {}
            bucket = value.get("sharingBucket") or {}
            items = value.get("contentItemList") or []
            keys = value.get("fileEncryptionKeys") or []
            meta: dict = {
                "bucket_id": bucket.get("id") or sharing_id,
                "content_item_id": None,
                "ephemeral_public_key": None,
                "passphrase_wrapped_pk": None,
                "file_name": bucket.get("name") or "",
                "file_size": bucket.get("totalFileSize") or 0,
            }
            for item in items:
                if isinstance(item, dict) and item.get("id") and item.get("ephemeralPublicKey"):
                    meta["content_item_id"] = item["id"]
                    meta["ephemeral_public_key"] = item["ephemeralPublicKey"]
                    base_key_id = item.get("baseFileEncryptionKeyId")
                    for key in keys:
                        if isinstance(key, dict) and key.get("id") == base_key_id:
                            meta["passphrase_wrapped_pk"] = key.get("passphraseWrappedPrivateKey")
                    break
            if meta["content_item_id"] and meta["ephemeral_public_key"]:
                return meta

    # Fallback: positional regex extraction (legacy stream shape)
    if _is_uuid(sharing_id):
        bucket_id = sharing_id
    else:
        sb_idx = html.find("sharingBucket")
        bucket_match = re.search(UUID_RE, html[sb_idx:]) if sb_idx > -1 else None
        bucket_id = bucket_match.group(0) if bucket_match else None
    ci = re.search(r"contentItemIds.*?(" + UUID_RE + ")", html)
    size_match = re.search(r'"totalFileSize",(\d+)', html)
    return {
        "bucket_id": bucket_id,
        "content_item_id": ci.group(1) if ci else None,
        "ephemeral_public_key": _ssr_field(html, "ephemeralPublicKey"),
        "passphrase_wrapped_pk": _ssr_field(html, "passphraseWrappedPrivateKey"),
        "file_name": _ssr_field(html, "name") or "",
        "file_size": int(size_match.group(1)) if size_match else 0,
    }


def derive_aes_key(sharing_id, fragment, wrapped_pk_b64, ephemeral_pub_b64,
                   salt_b64, iterations) -> bytes:
    if _is_uuid(sharing_id) or not wrapped_pk_b64:
        raw_private_key = _b64url_decode(fragment)
    else:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=base64.b64decode(salt_b64),
            iterations=iterations,
        )
        wrapping_key = kdf.derive(fragment.encode("utf-8"))
        raw_private_key = aes_key_unwrap(wrapping_key, base64.b64decode(wrapped_pk_b64))

    d_int = int.from_bytes(raw_private_key, "big")
    private_key = ec.derive_private_key(d_int, ec.SECP256R1())
    ephemeral_pub = ec.EllipticCurvePublicKey.from_encoded_point(
        ec.SECP256R1(), base64.b64decode(ephemeral_pub_b64)
    )
    return private_key.exchange(ec.ECDH(), ephemeral_pub)


def decrypt_file(encrypted: bytes, aes_key: bytes, iv_b64: str) -> bytes:
    iv = base64.b64decode(iv_b64)
    nonce = bytearray(16)
    nonce[: len(iv)] = iv
    cipher = Cipher(algorithms.AES(aes_key), modes.CTR(bytes(nonce)))
    d = cipher.decryptor()
    return d.update(encrypted) + d.finalize()


def _b64url_decode(s: str) -> bytes:
    s = s.replace("-", "+").replace("_", "/")
    s += "=" * (4 - len(s) % 4)
    return base64.b64decode(s)


def _extract_js_string(js_text: str, key: str) -> str | None:
    patterns = [
        re.escape(key) + r'["\s]*:\s*["\']([^"\']+)["\']',
        re.escape(key) + r'","([^"]+)"',
        re.escape(key) + r"','([^']+)'",
    ]
    for pattern in patterns:
        m = re.search(pattern, js_text)
        if m:
            return m.group(1)
    return None


async def auto_extract_constants(client: httpx.AsyncClient) -> tuple[str, str]:
    """Return (sharing_salt_b64, file_iv_b64) from LimeWire's JS bundles."""
    sw = await client.get("https://limewire.com/build/workers/service-worker.js")
    sw.raise_for_status()
    file_iv = _extract_js_string(sw.text, "mainFileBase64")
    if not file_iv:
        raise RuntimeError("could not extract mainFileBase64 from service worker")

    home = await client.get("https://limewire.com/")
    home.raise_for_status()
    chunk_urls = list(set(re.findall(r'(?:href|src)="(/build/chunks/[^"]+\.js)"', home.text)))

    salt = None
    for chunk_path in chunk_urls:
        try:
            r = await client.get(f"https://limewire.com{chunk_path}")
            if "saltBase64" in r.text:
                salt = _extract_js_string(r.text, "saltBase64")
                if salt:
                    break
        except httpx.HTTPError:
            continue
    if not salt:
        raise RuntimeError("could not extract saltBase64 from JS chunks")
    return salt, file_iv


async def fetch_and_decrypt(url: str, out_path: Path) -> None:
    sharing_id, fragment = parse_share_url(url)
    salt_b64, file_iv_b64 = SHARING_SALT_B64, FILE_IV_B64

    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=120.0,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        html = None
        meta = None
        for attempt in range(3):
            resp = await client.get(f"https://limewire.com/d/{sharing_id}")
            resp.raise_for_status()
            html = resp.text
            meta = extract_metadata(html, sharing_id)
            if meta and meta.get("content_item_id") and meta.get("ephemeral_public_key"):
                break
        if not meta or not meta.get("content_item_id"):
            raise RuntimeError(
                "could not extract share metadata from page "
                f"(size={len(html or '')} bytes). Sample: {(html or '')[:300]!r}"
            )
        print(f"[1/5] share page OK: bucket={meta['bucket_id']} "
              f"name={meta['file_name']!r} size={meta['file_size']}", flush=True)

        jwt_token = client.cookies.get("production_access_token")
        if not jwt_token:
            raise RuntimeError("no production_access_token cookie received")
        try:
            payload = json.loads(base64.b64decode(jwt_token.split(".")[1] + "==="))
            csrf = payload["csrfToken"]
        except (IndexError, KeyError, ValueError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"bad JWT payload: {exc}") from exc
        print("[2/5] session cookie + CSRF OK", flush=True)

        aes_key = derive_aes_key(
            sharing_id, fragment, meta.get("passphrase_wrapped_pk"),
            meta["ephemeral_public_key"], salt_b64, PBKDF2_ITERATIONS,
        )
        print("[3/5] AES key derived", flush=True)

        resp = await client.post(
            f"https://api.limewire.com/sharing/download/{meta['bucket_id']}",
            headers={
                "X-CSRF-Token": csrf,
                "Authorization": f"Bearer {jwt_token}",
                "Content-Type": "application/json",
            },
            json={"contentItems": [{"id": meta["content_item_id"]}]},
        )
        resp.raise_for_status()
        data = resp.json()
        items = data.get("contentItems") or []
        if not items:
            raise RuntimeError(f"no contentItems in API response: {data}")
        s3_url = items[0]["downloadUrl"]
        print("[4/5] presigned URL obtained", flush=True)

        enc = await client.get(s3_url)
        enc.raise_for_status()
        encrypted = enc.content
        print(f"[5/5] downloaded {len(encrypted)} encrypted bytes", flush=True)

        plain = decrypt_file(encrypted, aes_key, file_iv_b64)
        print(f"      decrypted {len(plain)} bytes, magic: {plain[:4]!r}", flush=True)

        if not plain[:4] == b"PK\x03\x04" and not plain[:5] == b"%PDF-" and not plain[:2] == b"PK":
            # constants may be stale -> re-extract once
            print("      magic mismatch -> re-extracting constants", flush=True)
            salt_b64, file_iv_b64 = await auto_extract_constants(client)
            aes_key = derive_aes_key(
                sharing_id, fragment, meta.get("passphrase_wrapped_pk"),
                meta["ephemeral_public_key"], salt_b64, PBKDF2_ITERATIONS,
            )
            plain = decrypt_file(encrypted, aes_key, file_iv_b64)
            print(f"      re-decrypted {len(plain)} bytes, magic: {plain[:4]!r}", flush=True)

        out_path.write_bytes(plain)
        print(f"SAVED {out_path} ({len(plain)} bytes)", flush=True)


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    url = sys.argv[1]
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("downloaded.bin")
    try:
        asyncio.run(fetch_and_decrypt(url, out))
    except Exception as exc:  # noqa: BLE001 - report everything to the log
        print(f"FAILED: {type(exc).__name__}: {exc}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
