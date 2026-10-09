#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
🍓 YorozuyaPanel 万事屋 — lightweight config panel with Gintama soul.

Users + subscriptions + nodes + outbound IP/country display.
Deploy-ready for Render.com (reads $PORT, JSON persistence, no DB needed).

Inspired by SpiderPanel (github.com/amirh00sain/SpiderPanel) — rebuilt tiny.
Fan-made Gintama theme. Not affiliated with the anime/manga publishers.
"""
import asyncio
import base64
import io
import json
import os
import secrets
import socket
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import FastAPI, Request, HTTPException, Depends
from fastapi.responses import JSONResponse, FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware

try:
    import qrcode

    QR_AVAILABLE = True
except ImportError:  # pragma: no cover
    QR_AVAILABLE = False

# ── Config ────────────────────────────────────────────────────────────────
VERSION = "1.0.0"
APP_DIR = Path(__file__).resolve().parent
STATIC_DIR = APP_DIR / "static"

PORT = int(os.environ.get("PORT", "8080"))
DATA_DIR = Path(os.environ.get("DATA_DIR", str(APP_DIR / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
STATE_FILE = DATA_DIR / "yorozuya_state.json"

ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "yorozuya")
DEFAULT_PASSWORD = ADMIN_PASSWORD == "yorozuya"
SECRET_KEY = os.environ.get("SECRET_KEY", secrets.token_hex(32))
SESSION_TTL = 12 * 3600  # 12 hours

IP_API_FIELDS = "status,message,country,countryCode,city,query,org,isp"
IP_CACHE_TTL = 7 * 24 * 3600  # 7 days for node/country lookups
OUTBOUND_CACHE_TTL = 10 * 60  # 10 min for server outbound info

GINTOKI_LINES = [
    "🍓 شير توت‌فرنگى خوردی امروز؟ نه؟ پس چطور انتظار داری کانفیگت کار کنه؟",
    "😎 یوروزویا همه‌کار می‌کنه… حتی سابسکریپشن!",
    "🐶 ساداهارو سرور رو بو کرد و تأیید کرد. مشکلی نیست.",
    "🗡️ ترافیکت مثل شمشیر چوبی منه: ظاهر ساده، باطن مرگبار.",
    "🍫 پینگ بالا؟ یه شکلات پارفی بخور، صبر کن، دوباره چک کن.",
    "📿 شینپاچی می‌گه: «گین‌سان! اون نود آفلاینه!» — چک کن ببین راست می‌گه.",
    "🥛 قانون اول یوروزویا: هرگز به یوزر اکسپایرشده قول تمدید نده.",
    "💸 اجاره عقبه، ولی پنل سرپاست. اولویت‌ها مشخصه!",
    "🌍 آی‌پی خروجی عوض شده؟ دنیا عوض می‌شه، ما هم عوض می‌شیم.",
    "⚔️ جنگجو واقعی کسیه که با یه نود تنها، صد یوزر رو ساپورت می‌کنه.",
]

# ── State ─────────────────────────────────────────────────────────────────
SAVE_LOCK = asyncio.Lock()
DIRTY = False
SESSIONS: dict[str, float] = {}  # token -> expiry ts
OUTBOUND_CACHE: dict = {"ts": 0, "data": None}

STATE = {
    "users": [],  # {id,username,uuid,sub_key,traffic_limit_gb,traffic_used_gb,expires_at,enabled,note,created_at}
    "nodes": [],  # {id,name,address,port,protocol,network,security,sni,path,host,fp,alpn,
    #             reality_pbk,reality_sid,reality_spx,flow,ss_method,ss_password,enabled,created_at,
    #             last_check,online,latency_ms,resolved_ip,country,country_code}
    "settings": {"sub_base_url": ""},
    "ip_cache": {},  # ip -> {country,country_code,city,isp,ts}
}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return uuid.uuid4().hex[:12]


def flag(cc: str) -> str:
    cc = (cc or "").upper()
    if len(cc) != 2 or not cc.isalpha():
        return "🏳️"
    return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65)


def mark_dirty():
    global DIRTY
    DIRTY = True


async def save_state(force: bool = False):
    global DIRTY
    if not DIRTY and not force:
        return
    async with SAVE_LOCK:
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(STATE, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(STATE_FILE)
        DIRTY = False


def load_state():
    global DIRTY
    if STATE_FILE.is_file():
        try:
            data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
            for k in ("users", "nodes", "settings", "ip_cache"):
                if k in data:
                    STATE[k] = data[k]
            # merge settings defaults
            STATE["settings"] = {"sub_base_url": "", **STATE.get("settings", {})}
            print(f"🍓 loaded state: {len(STATE['users'])} users, {len(STATE['nodes'])} nodes")
        except Exception as e:  # corrupted file -> start fresh, keep backup
            bak = STATE_FILE.with_suffix(".json.corrupt")
            try:
                STATE_FILE.replace(bak)
                print(f"⚠️ corrupt state file, backed up to {bak}: {e}")
            except Exception:
                print(f"⚠️ corrupt state file: {e}")
    DIRTY = False


def find_user(uid: str) -> dict | None:
    return next((u for u in STATE["users"] if u["id"] == uid), None)


def find_user_by_sub(sub_key: str) -> dict | None:
    return next((u for u in STATE["users"] if u.get("sub_key") == sub_key), None)


def find_node(nid: str) -> dict | None:
    return next((n for n in STATE["nodes"] if n["id"] == nid), None)


def is_expired(user: dict) -> bool:
    exp = (user.get("expires_at") or "").strip()
    if not exp:
        return False
    try:
        dt = datetime.fromisoformat(exp.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= dt
    except Exception:
        return False


def public_user(u: dict) -> dict:
    return {
        "id": u["id"],
        "username": u["username"],
        "uuid": u["uuid"],
        "sub_key": u.get("sub_key", ""),
        "sub_path": f"/api/sub/{u.get('sub_key', '')}",
        "traffic_limit_gb": float(u.get("traffic_limit_gb", 0) or 0),
        "traffic_used_gb": float(u.get("traffic_used_gb", 0) or 0),
        "expires_at": u.get("expires_at", ""),
        "enabled": bool(u.get("enabled", True)),
        "expired": is_expired(u),
        "note": u.get("note", ""),
        "created_at": u.get("created_at", ""),
    }


def public_node(n: dict) -> dict:
    return {
        "id": n["id"],
        "name": n["name"],
        "address": n.get("address", ""),
        "port": int(n.get("port", 443) or 443),
        "protocol": n.get("protocol", "vless"),
        "network": n.get("network", "tcp"),
        "security": n.get("security", "none"),
        "sni": n.get("sni", ""),
        "path": n.get("path", ""),
        "host": n.get("host", ""),
        "fp": n.get("fp", ""),
        "alpn": n.get("alpn", ""),
        "reality_pbk": n.get("reality_pbk", ""),
        "reality_sid": n.get("reality_sid", ""),
        "reality_spx": n.get("reality_spx", ""),
        "flow": n.get("flow", ""),
        "ss_method": n.get("ss_method", "chacha20-ietf-poly1305"),
        "ss_password": n.get("ss_password", ""),
        "enabled": bool(n.get("enabled", True)),
        "created_at": n.get("created_at", ""),
        "last_check": n.get("last_check", ""),
        "online": n.get("online"),
        "latency_ms": n.get("latency_ms"),
        "resolved_ip": n.get("resolved_ip", ""),
        "country": n.get("country", ""),
        "country_code": n.get("country_code", ""),
        "flag": flag(n.get("country_code", "")) if n.get("country_code") else "",
    }


# ── Auth ──────────────────────────────────────────────────────────────────
def new_session() -> str:
    token = secrets.token_urlsafe(32)
    SESSIONS[token] = time.time() + SESSION_TTL
    return token


def check_session(token: str | None) -> bool:
    if not token or token not in SESSIONS:
        return False
    if SESSIONS[token] < time.time():
        SESSIONS.pop(token, None)
        return False
    return True


async def require_auth(request: Request):
    if not check_session(request.cookies.get("yorozuya_session")):
        raise HTTPException(status_code=401, detail="login required")
    return True


# ── Geo / network helpers ─────────────────────────────────────────────────
async def ip_lookup(ip: str, client: httpx.AsyncClient | None = None) -> dict:
    """Country info for an IP, persistent-cached. Returns {country,country_code,city,isp}."""
    cached = STATE["ip_cache"].get(ip)
    if cached and time.time() - cached.get("ts", 0) < IP_CACHE_TTL:
        return cached
    info = {"country": "", "country_code": "", "city": "", "isp": ""}
    try:
        close = False
        if client is None:
            client = httpx.AsyncClient(timeout=10)
            close = True
        r = await client.get(f"http://ip-api.com/json/{quote(ip)}?fields={IP_API_FIELDS}")
        d = r.json()
        if d.get("status") == "success":
            info = {
                "country": d.get("country", ""),
                "country_code": d.get("countryCode", ""),
                "city": d.get("city", ""),
                "isp": d.get("isp") or d.get("org", ""),
            }
            STATE["ip_cache"][ip] = {**info, "ts": time.time()}
            mark_dirty()
        if close:
            await client.aclose()
    except Exception:
        pass
    return info


async def ip_lookup_batch(ips: list[str]) -> dict[str, dict]:
    """Batch country lookup via ip-api.com. Returns {ip: info}."""
    out: dict[str, dict] = {}
    todo = []
    for ip in ips:
        cached = STATE["ip_cache"].get(ip)
        if cached and time.time() - cached.get("ts", 0) < IP_CACHE_TTL:
            out[ip] = cached
        elif ip:
            todo.append(ip)
    if todo:
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                payload = [{"query": ip, "fields": "status,country,countryCode,city,query,isp"} for ip in todo[:100]]
                r = await client.post("http://ip-api.com/batch", json=payload)
                for d in r.json():
                    if isinstance(d, dict) and d.get("status") == "success" and d.get("query"):
                        info = {
                            "country": d.get("country", ""),
                            "country_code": d.get("countryCode", ""),
                            "city": d.get("city", ""),
                            "isp": d.get("isp", ""),
                            "ts": time.time(),
                        }
                        STATE["ip_cache"][d["query"]] = info
                        out[d["query"]] = info
            mark_dirty()
        except Exception:
            pass
    return out


async def get_outbound_info(force: bool = False) -> dict:
    """Server's own outbound IP + country (what the configs exit through)."""
    if not force and OUTBOUND_CACHE["data"] and time.time() - OUTBOUND_CACHE["ts"] < OUTBOUND_CACHE_TTL:
        return OUTBOUND_CACHE["data"]
    info = {"ip": "", "country": "", "country_code": "", "city": "", "isp": ""}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(f"http://ip-api.com/json/?fields={IP_API_FIELDS}")
            d = r.json()
            if d.get("status") == "success":
                info = {
                    "ip": d.get("query", ""),
                    "country": d.get("country", ""),
                    "country_code": d.get("countryCode", ""),
                    "city": d.get("city", ""),
                    "isp": d.get("isp") or d.get("org", ""),
                }
    except Exception:
        pass
    if not info["ip"]:  # fallback: IP only
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get("https://api.ipify.org")
                if r.status_code == 200 and r.text.strip():
                    info["ip"] = r.text.strip()
        except Exception:
            pass
    info["flag"] = flag(info["country_code"]) if info["country_code"] else "🏳️"
    OUTBOUND_CACHE.update(ts=time.time(), data=info)
    return info


async def resolve_host(host: str) -> str:
    host = (host or "").strip()
    if not host:
        return ""
    try:
        import ipaddress

        ipaddress.ip_address(host)
        return host
    except ValueError:
        pass
    try:
        loop = asyncio.get_event_loop()
        infos = await asyncio.wait_for(
            loop.getaddrinfo(host, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM), 6
        )
        for fam, _, _, _, sa in infos:
            if fam == socket.AF_INET:
                return sa[0]
        return infos[0][4][0] if infos else ""
    except Exception:
        return ""


async def tcp_ping(host: str, port: int, timeout: float = 5.0) -> tuple[bool, int | None]:
    t0 = time.perf_counter()
    try:
        _r, w = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        w.close()
        try:
            await w.wait_closed()
        except Exception:
            pass
        return True, round((time.perf_counter() - t0) * 1000)
    except Exception:
        return False, None


async def check_node(node: dict, geo: dict | None = None) -> dict:
    """TCP check + resolve + country. Mutates node, returns it."""
    ip = await resolve_host(node.get("address", ""))
    port = int(node.get("port", 443) or 443)
    ok, ms = (False, None)
    if ip:
        ok, ms = await tcp_ping(ip, port)
    info = geo or (await ip_lookup(ip) if ip else {"country": "", "country_code": "", "city": "", "isp": ""})
    node.update(
        last_check=now_iso(),
        online=ok,
        latency_ms=ms,
        resolved_ip=ip,
        country=info.get("country", ""),
        country_code=info.get("country_code", ""),
    )
    mark_dirty()
    return node


# ── Config link builders ──────────────────────────────────────────────────
def _q(params: dict) -> str:
    return "&".join(f"{k}={quote(str(v), safe='')}" for k, v in params.items() if str(v) != "")


def build_link(user_uuid: str, remark: str, n: dict) -> str:
    addr, port = n.get("address", ""), int(n.get("port", 443) or 443)
    proto = (n.get("protocol") or "vless").lower()
    net, sec = n.get("network", "tcp") or "tcp", n.get("security", "none") or "none"
    rm = quote(remark, safe="")
    if proto == "vless":
        p = {"encryption": "none", "security": sec}
        if n.get("sni"):
            p["sni"] = n["sni"]
        if n.get("fp"):
            p["fp"] = n["fp"]
        if n.get("alpn"):
            p["alpn"] = n["alpn"]
        p["type"] = net
        if net in ("ws", "grpc", "xhttp") and n.get("path"):
            p["path" if net != "grpc" else "serviceName"] = n["path"]
        if n.get("host"):
            p["host"] = n["host"]
        if sec == "reality":
            if n.get("reality_pbk"):
                p["pbk"] = n["reality_pbk"]
            if n.get("reality_sid"):
                p["sid"] = n["reality_sid"]
            if n.get("reality_spx"):
                p["spx"] = n["reality_spx"]
        if n.get("flow"):
            p["flow"] = n["flow"]
        return f"vless://{user_uuid}@{addr}:{port}?{_q(p)}#{rm}"
    if proto == "vmess":
        cfg = {
            "v": "2", "ps": remark, "add": addr, "port": str(port), "id": user_uuid,
            "aid": "0", "scy": "auto", "net": net, "type": "none",
            "host": n.get("host", "") or n.get("sni", ""), "path": n.get("path", ""),
            "tls": "tls" if sec == "tls" else "", "sni": n.get("sni", ""),
            "fp": n.get("fp", ""), "alpn": n.get("alpn", ""),
        }
        b64 = base64.b64encode(json.dumps(cfg, ensure_ascii=False).encode()).decode()
        return f"vmess://{b64}"
    if proto == "trojan":
        p = {"security": sec if sec != "none" else "tls"}
        if n.get("sni"):
            p["sni"] = n["sni"]
        if n.get("fp"):
            p["fp"] = n["fp"]
        if net != "tcp":
            p["type"] = net
        if n.get("path"):
            p["path"] = n["path"]
        if n.get("host"):
            p["host"] = n["host"]
        return f"trojan://{user_uuid}@{addr}:{port}?{_q(p)}#{rm}"
    if proto == "ss":
        method = n.get("ss_method", "chacha20-ietf-poly1305") or "chacha20-ietf-poly1305"
        pwd = n.get("ss_password", "") or user_uuid
        creds = base64.urlsafe_b64encode(f"{method}:{pwd}".encode()).decode().rstrip("=")
        return f"ss://{creds}@{addr}:{port}#{rm}"
    return ""


def user_links(user: dict) -> list[dict]:
    out = []
    for n in STATE["nodes"]:
        if not n.get("enabled", True):
            continue
        cc = n.get("country_code", "")
        remark = f"🍓 {user['username']}｜{n['name']}" + (f"｜{cc}" if cc else "")
        link = build_link(user["uuid"], remark, n)
        if link:
            out.append(
                {
                    "node": n["name"],
                    "protocol": n.get("protocol", "vless"),
                    "country": n.get("country", ""),
                    "country_code": cc,
                    "flag": flag(cc) if cc else "",
                    "link": link,
                }
            )
    return out


def sub_base(request: Request) -> str:
    custom = (STATE["settings"].get("sub_base_url") or "").strip().rstrip("/")
    if custom:
        return custom
    return str(request.base_url).rstrip("/")


def qr_png_bytes(text: str) -> bytes:
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(text)
    qr.make(fit=True)
    img = qr.make_image(fill_color="#1a1a2e", back_color="white").convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── App lifecycle ─────────────────────────────────────────────────────────
async def bg_loop():
    while True:
        try:
            changed = False
            for u in STATE["users"]:
                if u.get("enabled") and is_expired(u):
                    u["enabled"] = False
                    changed = True
            if changed:
                mark_dirty()
                print("⏳ expiry sweeper disabled an expired user")
            await save_state()
        except Exception as e:  # never kill the loop
            print(f"⚠️ bg loop: {e}")
        await asyncio.sleep(30)


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_state()
    task = asyncio.create_task(bg_loop())
    print(f"🍓 YorozuyaPanel v{VERSION} — Yorozuya does EVERYTHING (even subscriptions!)")
    if DEFAULT_PASSWORD:
        print("⚠️  Using default ADMIN_PASSWORD='yorozuya' — set ADMIN_PASSWORD env var!")
    yield
    task.cancel()
    await save_state(force=True)


app = FastAPI(title="YorozuyaPanel 🍓", docs_url=None, redoc_url=None, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"], allow_credentials=True, allow_methods=["*"], allow_headers=["*"]
)

# ── Pages ─────────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
async def root(request: Request):
    if check_session(request.cookies.get("yorozuya_session")):
        return RedirectResponse("/panel")
    return RedirectResponse("/login")


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"ok": True, "app": "yorozuya-panel", "version": VERSION}


@app.get("/login", include_in_schema=False)
async def login_page():
    return FileResponse(str(STATIC_DIR / "login.html"))


@app.get("/panel", include_in_schema=False)
async def panel_page():
    return FileResponse(str(STATIC_DIR / "panel.html"))


@app.get("/p/{sub_key}", include_in_schema=False)
async def sub_page(sub_key: str):
    if not find_user_by_sub(sub_key):
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(STATIC_DIR / "sub.html"))


# ── Auth API ──────────────────────────────────────────────────────────────
@app.post("/api/login")
async def api_login(request: Request):
    try:
        body = await request.json()
    except Exception:
        body = {}
    if secrets.compare_digest(str(body.get("password", "")), ADMIN_PASSWORD):
        token = new_session()
        resp = JSONResponse({"ok": True})
        resp.set_cookie("yorozuya_session", token, httponly=True, samesite="lax", max_age=SESSION_TTL, path="/")
        return resp
    raise HTTPException(status_code=401, detail="wrong password")


@app.post("/api/logout")
async def api_logout(request: Request):
    SESSIONS.pop(request.cookies.get("yorozuya_session", ""), None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie("yorozuya_session", path="/")
    return resp


@app.get("/api/me")
async def api_me(_: bool = Depends(require_auth)):
    return {"ok": True, "user": "admin"}


@app.get("/api/fun/line")
async def fun_line(_: bool = Depends(require_auth)):
    return {"line": secrets.choice(GINTOKI_LINES)}


# ── Server info (outbound IP + country) ───────────────────────────────────
@app.get("/api/server-info")
async def server_info(request: Request, _: bool = Depends(require_auth)):
    out = await get_outbound_info()
    users = STATE["users"]
    nodes = STATE["nodes"]
    return {
        "ok": True,
        "version": VERSION,
        "now": now_iso(),
        "hostname": socket.gethostname(),
        "default_password": DEFAULT_PASSWORD,
        "outbound": out,
        "counts": {
            "users": len(users),
            "active_users": sum(1 for u in users if u.get("enabled") and not is_expired(u)),
            "expired_users": sum(1 for u in users if is_expired(u)),
            "nodes": len(nodes),
            "online_nodes": sum(1 for n in nodes if n.get("online") is True),
        },
        "fun_line": secrets.choice(GINTOKI_LINES),
    }


@app.post("/api/server-info/refresh")
async def server_info_refresh(_: bool = Depends(require_auth)):
    return {"ok": True, "outbound": await get_outbound_info(force=True)}


# ── Users ─────────────────────────────────────────────────────────────────
@app.get("/api/users")
async def list_users(_: bool = Depends(require_auth)):
    return {"users": [public_user(u) for u in STATE["users"]]}


@app.post("/api/users")
async def create_user(request: Request, _: bool = Depends(require_auth)):
    body = await request.json()
    username = str(body.get("username", "")).strip()
    if not username:
        raise HTTPException(status_code=400, detail="username required")
    if any(u["username"] == username for u in STATE["users"]):
        raise HTTPException(status_code=400, detail="username exists")
    user = {
        "id": new_id(),
        "username": username,
        "uuid": str(body.get("uuid") or uuid.uuid4()),
        "sub_key": secrets.token_urlsafe(12),
        "traffic_limit_gb": float(body.get("traffic_limit_gb", 0) or 0),
        "traffic_used_gb": 0,
        "expires_at": str(body.get("expires_at", "") or ""),
        "enabled": bool(body.get("enabled", True)),
        "note": str(body.get("note", "") or ""),
        "created_at": now_iso(),
    }
    STATE["users"].append(user)
    mark_dirty()
    await save_state()
    return {"ok": True, "user": public_user(user)}


@app.patch("/api/users/{uid}")
async def update_user(uid: str, request: Request, _: bool = Depends(require_auth)):
    u = find_user(uid)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    body = await request.json()
    if "username" in body:
        name = str(body["username"]).strip()
        if not name:
            raise HTTPException(status_code=400, detail="username required")
        if any(x["username"] == name and x["id"] != uid for x in STATE["users"]):
            raise HTTPException(status_code=400, detail="username exists")
        u["username"] = name
    for f in ("expires_at", "note"):
        if f in body:
            u[f] = str(body[f] or "")
    for f in ("traffic_limit_gb", "traffic_used_gb"):
        if f in body:
            try:
                u[f] = max(0, float(body[f] or 0))
            except (TypeError, ValueError):
                pass
    if "enabled" in body:
        u["enabled"] = bool(body["enabled"])
    if "uuid" in body and body["uuid"]:
        u["uuid"] = str(body["uuid"])
    mark_dirty()
    await save_state()
    return {"ok": True, "user": public_user(u)}


@app.delete("/api/users/{uid}")
async def delete_user(uid: str, _: bool = Depends(require_auth)):
    u = find_user(uid)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    STATE["users"].remove(u)
    mark_dirty()
    await save_state()
    return {"ok": True}


@app.post("/api/users/{uid}/reset-traffic")
async def reset_traffic(uid: str, _: bool = Depends(require_auth)):
    u = find_user(uid)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    u["traffic_used_gb"] = 0
    mark_dirty()
    await save_state()
    return {"ok": True, "user": public_user(u)}


@app.post("/api/users/{uid}/rotate-sub")
async def rotate_sub(uid: str, _: bool = Depends(require_auth)):
    u = find_user(uid)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    u["sub_key"] = secrets.token_urlsafe(12)
    mark_dirty()
    await save_state()
    return {"ok": True, "user": public_user(u)}


@app.get("/api/users/{uid}/subscription")
async def user_subscription(uid: str, request: Request, _: bool = Depends(require_auth)):
    u = find_user(uid)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    base = sub_base(request)
    return {
        "ok": True,
        "user": public_user(u),
        "sub_url": f"{base}/api/sub/{u['sub_key']}",
        "page_url": f"{base}/p/{u['sub_key']}",
        "qr_url": f"{base}/api/sub/{u['sub_key']}/qr",
        "links": user_links(u),
    }


@app.get("/api/users/{uid}/qr")
async def user_qr(uid: str, request: Request, _: bool = Depends(require_auth)):
    if not QR_AVAILABLE:
        raise HTTPException(status_code=501, detail="QR not installed")
    u = find_user(uid)
    if not u:
        raise HTTPException(status_code=404, detail="user not found")
    png = qr_png_bytes(f"{sub_base(request)}/api/sub/{u['sub_key']}")
    return Response(content=png, media_type="image/png")


# ── Nodes ─────────────────────────────────────────────────────────────────
NODE_FIELDS = (
    "name", "address", "port", "protocol", "network", "security", "sni", "path",
    "host", "fp", "alpn", "reality_pbk", "reality_sid", "reality_spx", "flow",
    "ss_method", "ss_password", "enabled",
)


@app.get("/api/nodes")
async def list_nodes(_: bool = Depends(require_auth)):
    return {"nodes": [public_node(n) for n in STATE["nodes"]]}


@app.post("/api/nodes")
async def create_node(request: Request, _: bool = Depends(require_auth)):
    body = await request.json()
    name = str(body.get("name", "")).strip()
    address = str(body.get("address", "")).strip()
    if not name or not address:
        raise HTTPException(status_code=400, detail="name and address required")
    node = {
        "id": new_id(), "name": name, "address": address,
        "port": int(body.get("port", 443) or 443),
        "protocol": str(body.get("protocol", "vless") or "vless").lower(),
        "network": str(body.get("network", "tcp") or "tcp").lower(),
        "security": str(body.get("security", "none") or "none").lower(),
        "sni": str(body.get("sni", "") or ""), "path": str(body.get("path", "") or ""),
        "host": str(body.get("host", "") or ""), "fp": str(body.get("fp", "") or ""),
        "alpn": str(body.get("alpn", "") or ""),
        "reality_pbk": str(body.get("reality_pbk", "") or ""),
        "reality_sid": str(body.get("reality_sid", "") or ""),
        "reality_spx": str(body.get("reality_spx", "") or ""),
        "flow": str(body.get("flow", "") or ""),
        "ss_method": str(body.get("ss_method", "chacha20-ietf-poly1305") or "chacha20-ietf-poly1305"),
        "ss_password": str(body.get("ss_password", "") or ""),
        "enabled": bool(body.get("enabled", True)),
        "created_at": now_iso(), "last_check": "", "online": None,
        "latency_ms": None, "resolved_ip": "", "country": "", "country_code": "",
    }
    STATE["nodes"].append(node)
    mark_dirty()
    await save_state()
    asyncio.create_task(check_node(node))  # first check in background
    return {"ok": True, "node": public_node(node)}


@app.patch("/api/nodes/{nid}")
async def update_node(nid: str, request: Request, _: bool = Depends(require_auth)):
    n = find_node(nid)
    if not n:
        raise HTTPException(status_code=404, detail="node not found")
    body = await request.json()
    for f in NODE_FIELDS:
        if f in body and body[f] is not None:
            n[f] = body[f]
    try:
        n["port"] = int(n.get("port", 443) or 443)
    except (TypeError, ValueError):
        n["port"] = 443
    mark_dirty()
    await save_state()
    return {"ok": True, "node": public_node(n)}


@app.delete("/api/nodes/{nid}")
async def delete_node(nid: str, _: bool = Depends(require_auth)):
    n = find_node(nid)
    if not n:
        raise HTTPException(status_code=404, detail="node not found")
    STATE["nodes"].remove(n)
    mark_dirty()
    await save_state()
    return {"ok": True}


@app.post("/api/nodes/{nid}/check")
async def check_one_node(nid: str, _: bool = Depends(require_auth)):
    n = find_node(nid)
    if not n:
        raise HTTPException(status_code=404, detail="node not found")
    await check_node(n)
    await save_state()
    return {"ok": True, "node": public_node(n)}


@app.post("/api/nodes/check-all")
async def check_all_nodes(_: bool = Depends(require_auth)):
    nodes = STATE["nodes"]
    if not nodes:
        return {"ok": True, "nodes": []}

    async def _resolve(n):
        return n, await resolve_host(n.get("address", ""))

    resolved = await asyncio.gather(*(_resolve(n) for n in nodes))
    geo = await ip_lookup_batch([ip for _, ip in resolved if ip])
    sem = asyncio.Semaphore(20)

    async def _check(n, ip):
        async with sem:
            ok, ms = (False, None)
            if ip:
                ok, ms = await tcp_ping(ip, int(n.get("port", 443) or 443))
            info = geo.get(ip, {"country": "", "country_code": "", "city": "", "isp": ""})
            n.update(last_check=now_iso(), online=ok, latency_ms=ms, resolved_ip=ip,
                     country=info.get("country", ""), country_code=info.get("country_code", ""))

    await asyncio.gather(*(_check(n, ip) for n, ip in resolved))
    mark_dirty()
    await save_state()
    return {"ok": True, "nodes": [public_node(n) for n in nodes]}


# ── Tools ─────────────────────────────────────────────────────────────────
@app.post("/api/tools/lookup")
async def tool_lookup(request: Request, _: bool = Depends(require_auth)):
    body = await request.json()
    query = str(body.get("query", "")).strip()
    if not query:
        raise HTTPException(status_code=400, detail="query required")
    ip = await resolve_host(query)
    info = await ip_lookup(ip) if ip else {"country": "", "country_code": "", "city": "", "isp": ""}
    return {
        "ok": True,
        "result": {
            "query": query, "resolved_ip": ip,
            "country": info.get("country", ""), "country_code": info.get("country_code", ""),
            "city": info.get("city", ""), "isp": info.get("isp", ""),
            "flag": flag(info.get("country_code", "")) if info.get("country_code") else "🏳️",
        },
    }


# ── Settings + backup ─────────────────────────────────────────────────────
@app.get("/api/settings")
async def get_settings(_: bool = Depends(require_auth)):
    return {"settings": STATE["settings"]}


@app.post("/api/settings")
async def post_settings(request: Request, _: bool = Depends(require_auth)):
    body = await request.json()
    if "sub_base_url" in body:
        STATE["settings"]["sub_base_url"] = str(body["sub_base_url"] or "").strip().rstrip("/")
    mark_dirty()
    await save_state()
    return {"ok": True, "settings": STATE["settings"]}


@app.get("/api/settings/backup")
async def backup(_: bool = Depends(require_auth)):
    data = json.dumps(STATE, ensure_ascii=False, indent=1).encode()
    return Response(
        content=data, media_type="application/json",
        headers={"Content-Disposition": "attachment; filename=yorozuya-backup.json"},
    )


@app.post("/api/settings/restore")
async def restore(request: Request, _: bool = Depends(require_auth)):
    form = await request.form()
    f = form.get("file")
    if f is None or not hasattr(f, "read"):
        raise HTTPException(status_code=400, detail="file required")
    try:
        data = json.loads((await f.read()).decode("utf-8"))
    except Exception:
        raise HTTPException(status_code=400, detail="invalid backup file")
    if not isinstance(data, dict) or "users" not in data or "nodes" not in data:
        raise HTTPException(status_code=400, detail="invalid backup file")
    STATE["users"] = data["users"] if isinstance(data["users"], list) else []
    STATE["nodes"] = data["nodes"] if isinstance(data["nodes"], list) else []
    if isinstance(data.get("settings"), dict):
        STATE["settings"] = {"sub_base_url": "", **data["settings"]}
    if isinstance(data.get("ip_cache"), dict):
        STATE["ip_cache"] = data["ip_cache"]
    mark_dirty()
    await save_state()
    return {"ok": True, "imported": {"users": len(STATE["users"]), "nodes": len(STATE["nodes"])}}


# ── Public subscription endpoints (no auth) ───────────────────────────────
@app.get("/api/sub/{sub_key}")
async def api_sub(sub_key: str, request: Request):
    u = find_user_by_sub(sub_key)
    if not u or not u.get("enabled", True) or is_expired(u):
        raise HTTPException(status_code=404, detail="not found")
    links = [x["link"] for x in user_links(u)]
    body = "\n".join(links)
    fmt = request.query_params.get("format", "")
    if fmt == "text":
        return Response(content=body + ("\n" if body else ""), media_type="text/plain; charset=utf-8")
    b64 = base64.b64encode(body.encode()).decode()
    return Response(
        content=b64,
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": f'inline; filename="{u["username"]}.txt',
                 "Subscription-Userinfo": f"upload=0; download={int(float(u.get('traffic_used_gb', 0) or 0) * 1073741824)}; total={int(float(u.get('traffic_limit_gb', 0) or 0) * 1073741824)}; expire=0"},
    )


@app.get("/api/sub/{sub_key}/qr")
async def api_sub_qr(sub_key: str, request: Request):
    if not QR_AVAILABLE:
        raise HTTPException(status_code=501, detail="QR not installed")
    u = find_user_by_sub(sub_key)
    if not u or not u.get("enabled", True) or is_expired(u):
        raise HTTPException(status_code=404, detail="not found")
    png = qr_png_bytes(f"{sub_base(request)}/api/sub/{sub_key}")
    return Response(content=png, media_type="image/png")


@app.get("/api/public/sub/{sub_key}")
async def api_public_sub(sub_key: str, request: Request):
    u = find_user_by_sub(sub_key)
    if not u or not u.get("enabled", True) or is_expired(u):
        raise HTTPException(status_code=404, detail="not found")
    base = sub_base(request)
    return {
        "ok": True,
        "username": u["username"],
        "expires_at": u.get("expires_at", ""),
        "expired": is_expired(u),
        "traffic_limit_gb": float(u.get("traffic_limit_gb", 0) or 0),
        "traffic_used_gb": float(u.get("traffic_used_gb", 0) or 0),
        "sub_url": f"{base}/api/sub/{sub_key}",
        "qr_url": f"{base}/api/sub/{sub_key}/qr",
        "links": user_links(u),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=PORT)
