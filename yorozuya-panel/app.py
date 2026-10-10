#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
====================================================================
 🍓 YOROZUYA PANEL — PYTHON SINGLE-FILE EDITION  万事屋  (v1.1.0)
====================================================================
 The ENTIRE panel (backend + frontend) lives in this ONE file.
 Pure Python 3 standard library — ZERO pip packages, ZERO database.

    python3 app.py
    → open the printed "Web Panel path" and set your admin password
      on first run (no env vars needed). ADMIN_PASSWORD env still
      works as an override.

 Built to run on the WEAKEST free servers (Render / Railway / Replit /
 PythonAnywhere / Wispbyte / Oracle free VPS): 1 process, no deps,
 debounced JSON writes, staggered node checks, client-side QR.

 Extras: real load balancer across nodes (round-robin / least-latency
 / random) + keep-alive cron (self-ping) so free hosts never sleep.

 Env vars (all optional):
   PORT or SERVER_PORT     listen port (default 8080)
   PUBLIC_URL / RENDER_EXTERNAL_URL / APP_URL / DOMAIN   public base URL
   ADMIN_PASSWORD          admin password override (else first-run /setup)
   KEEPALIVE_MINUTES       self-ping interval, 0 = disable (default 10)
   NODE_CHECK_MINUTES      auto node re-check interval (default 5)
   DATA_DIR                where state.json lives (default ./yorozuya-data)

 Architecture inspired by Blue-Knight-Panel-V3. All code is original.
 Fan-made Gintama theme, not affiliated with publishers.
====================================================================
"""
import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import signal
import socket
import sys
import tempfile
import threading
import time
import urllib.parse
import urllib.request
import uuid as uuidlib
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

Y_VERSION = '1.2.0'

# ============ CONFIG (env-driven, BK-style) ============
def _env(name, default=''):
    v = os.environ.get(name)
    return v if v is not None else default

CONFIG = {
    'brand': 'YorozuyaPanel',
    'kanji': '万事屋',
    'port': int(_env('PORT') or _env('SERVER_PORT') or '8080') or 8080,
    'admin_password': _env('ADMIN_PASSWORD'),
    'public_url': (_env('PUBLIC_URL') or _env('RENDER_EXTERNAL_URL') or _env('APP_URL') or _env('DOMAIN')).rstrip('/'),
    'keepalive_minutes': float(_env('KEEPALIVE_MINUTES', '10') or 0),
    'node_check_minutes': float(_env('NODE_CHECK_MINUTES', '5') or 0),
    'data_dir_hint': _env('DATA_DIR'),
}
ENV_PASSWORD_SET = bool(os.environ.get('ADMIN_PASSWORD'))
DEFAULT_PASSWORD = ENV_PASSWORD_SET and CONFIG['admin_password'] == 'yorozuya'
SESSION_TTL = 12 * 3600  # seconds (12h)
IP_API_FIELDS = 'status,message,country,countryCode,city,query,org,isp'
IP_CACHE_TTL = 7 * 24 * 3600
OUTBOUND_CACHE_TTL = 10 * 60

GINTOKI_LINES = [
    '🍓 شير توت‌فرنگى خوردی امروز؟ نه؟ پس چطور انتظار داری کانفیگت کار کنه؟',
    '😎 یوروزویا همه‌کار می‌کنه… حتی سابسکریپشن!',
    '🐶 ساداهارو سرور رو بو کرد و تأیید کرد. مشکلی نیست.',
    '🗡️ ترافیکت مثل شمشیر چوبی منه: ظاهر ساده، باطن مرگبار.',
    '🍫 پینگ بالا؟ یه شکلات پارفی بخور، صبر کن، دوباره چک کن.',
    '📿 شینپاچی می‌گه: «گین‌سان! اون نود آفلاینه!» — چک کن ببین راست می‌گه.',
    '🥛 قانون اول یوروزویا: هرگز به یوزر اکسپایرشده قول تمدید نده.',
    '💸 اجاره عقبه، ولی پنل سرپاست. اولویت‌ها مشخصه!',
    '🌍 آی‌پی خروجی عوض شده؟ دنیا عوض می‌شه، ما هم عوض می‌شیم.',
    '⚔️ جنگجو واقعی کسیه که با یه نود تنها، صد یوزر رو ساپورت می‌کنه.',
]

# ============ DATA DIR (fallbacks like BK: cwd → appdir → tmp) ============
def pick_data_dir():
    cands = []
    if CONFIG['data_dir_hint']:
        cands.append(CONFIG['data_dir_hint'])
    cands.append(os.path.join(os.getcwd(), 'yorozuya-data'))
    cands.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'yorozuya-data'))
    cands.append(os.path.join(tempfile.gettempdir(), 'yorozuya-data'))
    for d in cands:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, '.probe')
            with open(probe, 'w') as f:
                f.write('ok')
            os.unlink(probe)
            return d
        except OSError:
            continue
    raise RuntimeError('no writable data dir found')

DATA_DIR = pick_data_dir()
STATE_FILE = os.path.join(DATA_DIR, 'state.json')

# ============ STATE ============
LOCK = threading.RLock()
STATE = {
    'users': [], 'nodes': [],
    'settings': {'sub_base_url': '', 'admin_hash': ''},
    'lb': {'strategy': 'round-robin', 'rr': 0, 'picks': {}},
    'ip_cache': {},
}
_dirty = False
_save_timer = None

def load_state():
    try:
        if not os.path.exists(STATE_FILE):
            return
        with open(STATE_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
        if isinstance(d.get('users'), list):
            STATE['users'] = d['users']
        if isinstance(d.get('nodes'), list):
            STATE['nodes'] = d['nodes']
        if isinstance(d.get('settings'), dict):
            STATE['settings'] = {'sub_base_url': '', 'admin_hash': ''}
            STATE['settings'].update(d['settings'])
        if isinstance(d.get('lb'), dict):
            STATE['lb'] = {'strategy': 'round-robin', 'rr': 0, 'picks': {}}
            STATE['lb'].update(d['lb'])
        if isinstance(d.get('ip_cache'), dict):
            STATE['ip_cache'] = d['ip_cache']
        print('🍓 state: %d users, %d nodes' % (len(STATE['users']), len(STATE['nodes'])))
    except Exception as e:
        try:
            os.rename(STATE_FILE, STATE_FILE + '.corrupt')
        except OSError:
            pass
        print('⚠️ corrupt state file, starting fresh: %s' % e)

def save_state(force=False):
    global _dirty
    with LOCK:
        if not _dirty and not force:
            return
        _dirty = False
        try:
            tmp = STATE_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(STATE, f, ensure_ascii=False, separators=(',', ':'))
            os.replace(tmp, STATE_FILE)
        except OSError as e:
            print('⚠️ save failed: %s' % e)

def mark_dirty():
    # debounced write — weak servers shouldn't fsync on every tiny change
    global _dirty, _save_timer
    with LOCK:
        _dirty = True
        if _save_timer is not None:
            return
        def _later():
            global _save_timer
            with LOCK:
                _save_timer = None
            save_state(False)
        _save_timer = threading.Timer(1.0, _later)
        _save_timer.daemon = True
        _save_timer.start()

# ============ HELPERS ============
def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')

def new_id():
    return secrets.token_hex(6)

def new_sub_key():
    return base64.urlsafe_b64encode(secrets.token_bytes(9)).decode('ascii').rstrip('=')

def flag(cc):
    cc = str(cc or '').upper()
    if len(cc) != 2 or not cc.isascii() or not cc.isalpha():
        return '🏳️'
    return chr(0x1F1E6 + ord(cc[0]) - 65) + chr(0x1F1E6 + ord(cc[1]) - 65)

def find_user(uid):
    for u in STATE['users']:
        if u.get('id') == uid:
            return u
    return None

def find_user_by_sub(k):
    for u in STATE['users']:
        if u.get('sub_key') == k:
            return u
    return None

def find_node(nid):
    for n in STATE['nodes']:
        if n.get('id') == nid:
            return n
    return None

def is_expired(u):
    exp = str(u.get('expires_at') or '').strip()
    if not exp:
        return False
    try:
        t = datetime.fromisoformat(exp.replace('Z', '+00:00'))
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return datetime.now(timezone.utc) >= t
    except ValueError:
        return False

def public_user(u):
    return {
        'id': u.get('id'), 'username': u.get('username'), 'uuid': u.get('uuid'), 'sub_key': u.get('sub_key') or '',
        'sub_path': '/api/sub/' + (u.get('sub_key') or ''),
        'traffic_limit_gb': to_num(u.get('traffic_limit_gb'), 0),
        'traffic_used_gb': to_num(u.get('traffic_used_gb'), 0),
        'expires_at': u.get('expires_at') or '', 'enabled': bool(u.get('enabled')), 'expired': is_expired(u),
        'note': u.get('note') or '', 'created_at': u.get('created_at') or '',
    }

def public_node(n):
    online = n.get('online')
    lat = n.get('latency_ms')
    cc = n.get('country_code') or ''
    return {
        'id': n.get('id'), 'name': n.get('name'), 'address': n.get('address') or '', 'port': to_num(n.get('port'), 443),
        'protocol': n.get('protocol') or 'vless', 'network': n.get('network') or 'tcp', 'security': n.get('security') or 'none',
        'sni': n.get('sni') or '', 'path': n.get('path') or '', 'host': n.get('host') or '', 'fp': n.get('fp') or '', 'alpn': n.get('alpn') or '',
        'reality_pbk': n.get('reality_pbk') or '', 'reality_sid': n.get('reality_sid') or '', 'reality_spx': n.get('reality_spx') or '',
        'flow': n.get('flow') or '', 'ss_method': n.get('ss_method') or 'chacha20-ietf-poly1305', 'ss_password': n.get('ss_password') or '',
        'mode': n.get('mode') or '', 'header': n.get('header') or '',
        'enabled': n.get('enabled') is not False, 'created_at': n.get('created_at') or '',
        'last_check': n.get('last_check') or '',
        'online': None if online is None else bool(online),
        'latency_ms': None if lat is None else lat,
        'resolved_ip': n.get('resolved_ip') or '', 'country': n.get('country') or '', 'country_code': cc,
        'flag': flag(cc) if cc else '',
    }

def to_num(v, default=0):
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return int(f) if f.is_integer() else f

def pass_ok(a, b):
    if len(a) != len(b):
        return False
    return hmac.compare_digest(a, b)

def sha256(s):
    return hashlib.sha256(str(s).encode('utf-8')).hexdigest()

def active_hash():
    if ENV_PASSWORD_SET:
        return sha256(CONFIG['admin_password'])
    return str(STATE['settings'].get('admin_hash') or '')

def password_set():
    return bool(active_hash())

# ============ SESSIONS ============
SESSIONS = {}  # token -> exp ts (epoch seconds)

def get_cookies(header):
    out = {}
    for p in str(header or '').split(';'):
        i = p.find('=')
        if i > 0:
            k = p[:i].strip()
            v = p[i + 1:].strip()
            try:
                out[k] = urllib.parse.unquote(v)
            except Exception:
                out[k] = v
    return out

def check_session(cookie_header):
    t = get_cookies(cookie_header).get('yorozuya_session')
    if not t:
        return False
    with LOCK:
        if t not in SESSIONS:
            return False
        if SESSIONS[t] < time.time():
            SESSIONS.pop(t, None)
            return False
    return True

def make_session():
    token = secrets.token_urlsafe(32)
    with LOCK:
        SESSIONS[token] = time.time() + SESSION_TTL
    return token

SESSION_COOKIE = 'yorozuya_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d'

# ============ GEO / NETWORK ============
def fetch_json(url, data=None, headers=None, timeout_ms=10000, method=None):
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    with urllib.request.urlopen(req, timeout=timeout_ms / 1000.0) as r:
        return json.loads(r.read().decode('utf-8'))

def ip_lookup(ip):
    cached = STATE['ip_cache'].get(ip)
    if cached and time.time() - (cached.get('ts') or 0) < IP_CACHE_TTL:
        return cached
    info = {'country': '', 'country_code': '', 'city': '', 'isp': ''}
    try:
        d = fetch_json('http://ip-api.com/json/' + urllib.parse.quote(str(ip)) + '?fields=' + IP_API_FIELDS, timeout_ms=10000)
        if d and d.get('status') == 'success':
            info['country'] = d.get('country') or ''
            info['country_code'] = d.get('countryCode') or ''
            info['city'] = d.get('city') or ''
            info['isp'] = d.get('isp') or d.get('org') or ''
            with LOCK:
                STATE['ip_cache'][ip] = dict(info, ts=time.time())
            mark_dirty()
    except Exception:
        pass
    return info

def ip_lookup_batch(ips):
    out = {}
    todo = []
    for ip in ips:
        if not ip:
            continue
        cached = STATE['ip_cache'].get(ip)
        if cached and time.time() - (cached.get('ts') or 0) < IP_CACHE_TTL:
            out[ip] = cached
        else:
            todo.append(ip)
    if todo:
        try:
            payload = json.dumps([{'query': ip, 'fields': 'status,country,countryCode,city,query,isp'} for ip in todo[:100]]).encode('utf-8')
            arr = fetch_json('http://ip-api.com/batch', data=payload,
                             headers={'Content-Type': 'application/json'}, timeout_ms=15000, method='POST')
            for d in (arr or []):
                if d and d.get('status') == 'success' and d.get('query'):
                    info = {'country': d.get('country') or '', 'country_code': d.get('countryCode') or '',
                            'city': d.get('city') or '', 'isp': d.get('isp') or '', 'ts': time.time()}
                    with LOCK:
                        STATE['ip_cache'][d['query']] = info
                    out[d['query']] = info
            mark_dirty()
        except Exception:
            pass
    return out

_OUTBOUND_CACHE = {'ts': 0, 'data': None}

def get_outbound_info(force=False):
    if not force and _OUTBOUND_CACHE['data'] and time.time() - _OUTBOUND_CACHE['ts'] < OUTBOUND_CACHE_TTL:
        return _OUTBOUND_CACHE['data']
    info = {'ip': '', 'country': '', 'country_code': '', 'city': '', 'isp': ''}
    try:
        d = fetch_json('http://ip-api.com/json/?fields=' + IP_API_FIELDS, timeout_ms=10000)
        if d and d.get('status') == 'success':
            info['ip'] = d.get('query') or ''
            info['country'] = d.get('country') or ''
            info['country_code'] = d.get('countryCode') or ''
            info['city'] = d.get('city') or ''
            info['isp'] = d.get('isp') or d.get('org') or ''
    except Exception:
        pass
    if not info['ip']:
        try:
            with urllib.request.urlopen('https://api.ipify.org', timeout=8) as r:
                txt = r.read().decode('utf-8', 'replace').strip()
            if txt:
                info['ip'] = txt
        except Exception:
            pass
    info['flag'] = flag(info['country_code']) if info['country_code'] else '🏳️'
    _OUTBOUND_CACHE['ts'] = time.time()
    _OUTBOUND_CACHE['data'] = info
    return info

def is_ip(host):
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False

def resolve_host(host):
    host = str(host or '').strip()
    if not host:
        return ''
    if is_ip(host):
        return host
    try:
        infos = socket.getaddrinfo(host, None)
        return infos[0][4][0]
    except OSError:
        return ''

def tcp_ping(host, port, timeout_ms=5000):
    t0 = time.time()
    try:
        with socket.create_connection((host, int(port)), timeout=timeout_ms / 1000.0):
            return {'ok': True, 'ms': int((time.time() - t0) * 1000)}
    except OSError:
        return {'ok': False, 'ms': None}

def check_node(node, geo=None):
    ip = resolve_host(node.get('address'))
    port = to_num(node.get('port'), 443) or 443
    ok = False
    ms = None
    if ip:
        r = tcp_ping(ip, port, 5000)
        ok = r['ok']
        ms = r['ms']
    info = geo or (ip_lookup(ip) if ip else {'country': '', 'country_code': '', 'city': '', 'isp': ''})
    node['last_check'] = now_iso()
    node['online'] = ok
    node['latency_ms'] = ms
    node['resolved_ip'] = ip
    node['country'] = info.get('country') or ''
    node['country_code'] = info.get('country_code') or ''
    mark_dirty()
    return node

# ============ LINK BUILDERS ============
def js_uri(s):
    # encodeURIComponent equivalent
    return urllib.parse.quote(str(s), safe="-_.!~*'()")

def q(params):
    parts = []
    for k in params:
        if str(params[k]) != '':
            parts.append(k + '=' + js_uri(params[k]))
    return '&'.join(parts)

def build_link(user_uuid, remark, n):
    addr = n.get('address') or ''
    port = to_num(n.get('port'), 443) or 443
    proto = str(n.get('protocol') or 'vless').lower()
    netw = n.get('network') or 'tcp'
    sec = n.get('security') or 'none'
    rm = js_uri(remark)
    if proto == 'vless':
        p = {'encryption': 'none', 'security': sec}
        if n.get('sni'):
            p['sni'] = n['sni']
        if n.get('fp'):
            p['fp'] = n['fp']
        if n.get('alpn'):
            p['alpn'] = n['alpn']
        p['type'] = netw
        if netw in ('ws', 'grpc', 'xhttp', 'h2'):
            if n.get('path'):
                p['serviceName' if netw == 'grpc' else 'path'] = n['path']
            if n.get('host'):
                p['host'] = n['host']
            if netw == 'h2' and not n.get('host') and n.get('sni'):
                p['host'] = n['sni']
            if netw in ('grpc', 'xhttp') and n.get('mode'):
                p['mode'] = n['mode']
        if netw == 'kcp':
            p['header'] = n.get('header') or 'none'
        if sec == 'reality':
            if n.get('reality_pbk'):
                p['pbk'] = n['reality_pbk']
            if n.get('reality_sid'):
                p['sid'] = n['reality_sid']
            if n.get('reality_spx'):
                p['spx'] = n['reality_spx']
        if n.get('flow'):
            p['flow'] = n['flow']
        return 'vless://' + user_uuid + '@' + addr + ':' + str(port) + '?' + q(p) + '#' + rm
    if proto == 'vmess':
        cfg = {
            'v': '2', 'ps': remark, 'add': addr, 'port': str(port), 'id': user_uuid,
            'aid': '0', 'scy': 'auto', 'net': netw, 'type': n['header'] if (netw == 'kcp' and n.get('header')) else 'none',
            'host': n.get('host') or n.get('sni') or '', 'path': n.get('path') or '',
            'tls': 'tls' if sec == 'tls' else '', 'sni': n.get('sni') or '', 'fp': n.get('fp') or '', 'alpn': n.get('alpn') or '',
        }
        raw = json.dumps(cfg, separators=(',', ':'), ensure_ascii=False).encode('utf-8')
        return 'vmess://' + base64.b64encode(raw).decode('ascii')
    if proto == 'trojan':
        p = {'security': sec if sec != 'none' else 'tls'}
        if n.get('sni'):
            p['sni'] = n['sni']
        if n.get('fp'):
            p['fp'] = n['fp']
        if netw != 'tcp':
            p['type'] = netw
        if netw == 'grpc':
            if n.get('path'):
                p['serviceName'] = n['path']
            if n.get('mode'):
                p['mode'] = n['mode']
        elif n.get('path'):
            p['path'] = n['path']
        if n.get('host'):
            p['host'] = n['host']
        return 'trojan://' + user_uuid + '@' + addr + ':' + str(port) + '?' + q(p) + '#' + rm
    if proto == 'ss':
        method = n.get('ss_method') or 'chacha20-ietf-poly1305'
        pwd = n.get('ss_password') or user_uuid
        creds = base64.urlsafe_b64encode((method + ':' + pwd).encode('utf-8')).decode('ascii').rstrip('=')
        return 'ss://' + creds + '@' + addr + ':' + str(port) + '#' + rm
    return ''

def link_entry(user, n):
    cc = n.get('country_code') or ''
    remark = '🍓 ' + user['username'] + '｜' + n['name'] + ('｜' + cc if cc else '')
    link = build_link(user['uuid'], remark, n)
    if not link:
        return None
    return {'node': n['name'], 'node_id': n['id'], 'protocol': n.get('protocol') or 'vless',
            'network': n.get('network') or 'tcp',
            'country': n.get('country') or '', 'country_code': cc,
            'flag': flag(cc) if cc else '', 'link': link}

def user_links(user, mode=None):
    out = []
    if mode == 'lb':
        p = pick_node()
        if p:
            e = link_entry(user, p)
            if e:
                out.append(e)
        return out
    for n in STATE['nodes']:
        if n.get('enabled') is False:
            continue
        if mode and n.get('id') != mode:
            continue
        e = link_entry(user, n)
        if e:
            out.append(e)
    return out

# ============ LOAD BALANCER (real balancing across healthy nodes) ============
LB_STRATEGIES = ['round-robin', 'least-latency', 'random']

def lb_state():
    if not isinstance(STATE.get('lb'), dict):
        STATE['lb'] = {'strategy': 'round-robin', 'rr': 0, 'picks': {}}
    if not isinstance(STATE['lb'].get('picks'), dict):
        STATE['lb']['picks'] = {}
    if STATE['lb'].get('strategy') not in LB_STRATEGIES:
        STATE['lb']['strategy'] = 'round-robin'
    return STATE['lb']

def lb_candidates():
    enabled = [n for n in STATE['nodes'] if n.get('enabled') is not False]
    online = [n for n in enabled if n.get('online') is True]
    return online if online else enabled  # fall back to enabled when health unknown

def pick_node():
    lb = lb_state()
    cands = lb_candidates()
    if not cands:
        return None
    if lb['strategy'] == 'least-latency':
        def lat(n):
            v = n.get('latency_ms')
            return v if isinstance(v, (int, float)) else float('inf')
        node = sorted(cands, key=lat)[0]
    elif lb['strategy'] == 'random':
        node = cands[secrets.randbelow(len(cands))]
    else:
        rr = int(lb.get('rr') or 0) % len(cands)
        node = cands[rr]
        lb['rr'] = (rr + 1) % len(cands)
    lb['picks'][node['id']] = (lb['picks'].get(node['id']) or 0) + 1
    mark_dirty()
    return node

def req_proto(headers):
    return str(headers.get('X-Forwarded-Proto') or 'http').split(',')[0].strip()

def req_host(headers):
    return str(headers.get('X-Forwarded-Host') or headers.get('Host') or 'localhost')

def sub_base(headers):
    custom = str(STATE['settings'].get('sub_base_url') or '').strip().rstrip('/')
    if custom:
        return custom
    if CONFIG['public_url']:
        return CONFIG['public_url']
    return req_proto(headers) + '://' + req_host(headers)

# ============ SWEEP (expiry + session prune) ============
def sweep():
    changed = False
    for u in STATE['users']:
        if u.get('enabled') and is_expired(u):
            u['enabled'] = False
            changed = True
    now = time.time()
    with LOCK:
        for tok in [t for t, exp in SESSIONS.items() if exp < now]:
            SESSIONS.pop(tok, None)
    if changed:
        mark_dirty()
        print('⏳ expiry sweeper disabled an expired user')

# ============ EMBEDDED FRONTEND (3 pages + public sub page, Gintama theme) ============
PAGE_LOGIN = r'''<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍓 ورود | یوروزویا پنل</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
:root{--bg:#0b1020;--bg2:#101731;--card:#151c3a;--card2:#1b2350;--line:rgba(255,255,255,.09);--ink:#eef1ff;--muted:#9aa3c7;--red:#e63946;--pink:#ff7b9c;--blue:#4aa3ff;--gold:#f4d35e;--green:#2ec4b6;--orange:#ff9f45;--milk:#fff7ec;--wood1:#9c6a3c;--wood2:#6f4525;--radius:16px;--shadow:0 10px 40px rgba(0,0,0,.45)}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{font-family:'Vazirmatn',Tahoma,Arial,sans-serif;color:var(--ink);min-height:100vh;background:radial-gradient(1200px 600px at 85% -10%,rgba(230,57,70,.16),transparent 60%),radial-gradient(900px 500px at 10% 110%,rgba(74,163,255,.14),transparent 60%),var(--bg)}
body::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.5;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='60' viewBox='0 0 120 60'%3E%3Cg fill='none' stroke='rgba(255,255,255,0.05)' stroke-width='1.5'%3E%3Ccircle cx='30' cy='60' r='28'/%3E%3Ccircle cx='30' cy='60' r='20'/%3E%3Ccircle cx='30' cy='60' r='12'/%3E%3Ccircle cx='90' cy='60' r='28'/%3E%3Ccircle cx='90' cy='60' r='20'/%3E%3Ccircle cx='90' cy='60' r='12'/%3E%3Ccircle cx='60' cy='30' r='28'/%3E%3Ccircle cx='60' cy='30' r='20'/%3E%3Ccircle cx='60' cy='30' r='12'/%3E%3C/g%3E%3C/svg%3E")}
a{color:var(--blue)}::selection{background:var(--red);color:#fff}
.wood{background:repeating-linear-gradient(93deg,rgba(0,0,0,.12) 0 3px,transparent 3px 9px),linear-gradient(180deg,var(--wood1),var(--wood2));border:2px solid #3d2712;border-radius:12px;box-shadow:inset 0 1px 0 rgba(255,255,255,.25),inset 0 -6px 14px rgba(0,0,0,.35),var(--shadow);color:var(--milk);text-shadow:0 2px 0 rgba(0,0,0,.5);position:relative}
.wood::before,.wood::after{content:"";position:absolute;top:8px;width:10px;height:10px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#ffe9b0,#8a6a2a 70%);box-shadow:0 1px 2px rgba(0,0,0,.6)}
.wood::before{right:10px}.wood::after{left:10px}
.kanji{font-weight:900;letter-spacing:.35em;color:var(--gold);text-shadow:0 2px 0 rgba(0,0,0,.55),0 0 18px rgba(244,211,94,.35)}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 60px;position:relative;z-index:1}
.topbar{display:flex;align-items:center;gap:14px;padding:12px 18px;margin-bottom:22px}
.topbar .logo{width:52px;height:52px;flex:none;filter:drop-shadow(0 4px 8px rgba(0,0,0,.5))}
.topbar h1{font-size:1.35rem;margin:0}
.topbar h1 .kanji{font-size:.95rem;margin-right:8px}
.topbar .sub{color:var(--milk);opacity:.85;font-size:.85rem;margin-top:2px}
.topbar .spacer{flex:1}
.clock{font-variant-numeric:tabular-nums;color:var(--gold);font-weight:700}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px}
.card h2,.card h3{margin:0 0 12px}
.muted{color:var(--muted)}
.grid{display:grid;gap:16px}.grid.c2{grid-template-columns:repeat(2,1fr)}.grid.c3{grid-template-columns:repeat(3,1fr)}.grid.c4{grid-template-columns:repeat(4,1fr)}
@media (max-width:900px){.grid.c4{grid-template-columns:repeat(2,1fr)}.grid.c3{grid-template-columns:1fr}.grid.c2{grid-template-columns:1fr}}
.tabs{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}
.tab{border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--ink);padding:10px 18px;border-radius:999px;cursor:pointer;font-family:inherit;font-size:.95rem;transition:all .18s ease}
.tab:hover{background:rgba(255,255,255,.09);transform:translateY(-1px)}
.tab.active{background:linear-gradient(180deg,#f25460,var(--red));border-color:#ff8a93;color:#fff;font-weight:700;box-shadow:0 6px 20px rgba(230,57,70,.4)}
.tabpane{display:none;animation:fade .25s ease}.tabpane.active{display:block}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.btn{font-family:inherit;border:none;border-radius:12px;padding:10px 18px;cursor:pointer;font-size:.92rem;font-weight:700;color:#fff;background:linear-gradient(180deg,#f25460,var(--red));box-shadow:0 4px 16px rgba(230,57,70,.35),inset 0 1px 0 rgba(255,255,255,.3);transition:all .15s ease;display:inline-flex;align-items:center;gap:6px}
.btn:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(230,57,70,.45)}
.btn:active{transform:translateY(1px)}
.btn.blue{background:linear-gradient(180deg,#63b3ff,#2f7fe0);box-shadow:0 4px 16px rgba(74,163,255,.35)}
.btn.green{background:linear-gradient(180deg,#3fd6c5,#1d9c8f);box-shadow:0 4px 16px rgba(46,196,182,.3)}
.btn.gold{background:linear-gradient(180deg,#f7dd7e,#d9a93b);color:#3a2a00}
.btn.ghost{background:rgba(255,255,255,.07);box-shadow:none;border:1px solid var(--line);color:var(--ink);font-weight:500}
.btn.danger{background:linear-gradient(180deg,#6b7280,#374151);box-shadow:none}
.btn.small{padding:6px 12px;font-size:.8rem;border-radius:9px}
.btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.toolbar .spacer{flex:1}
input,select,textarea{font-family:inherit;background:rgba(0,0,0,.35);border:1px solid var(--line);color:var(--ink);border-radius:10px;padding:10px 12px;font-size:.9rem;width:100%;outline:none;transition:border .15s;color-scheme:dark}
input:focus,select:focus,textarea:focus{border-color:var(--pink);box-shadow:0 0 0 3px rgba(255,123,156,.15)}
input[type="checkbox"]{width:auto;transform:scale(1.3);accent-color:var(--red)}
label.lbl{display:block;font-size:.8rem;color:var(--muted);margin:10px 0 5px}
.form-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:0 14px}
.form-grid .full{grid-column:1/-1}
@media (max-width:640px){.form-grid{grid-template-columns:1fr}}
.search{max-width:260px}
.table-wrap{overflow-x:auto;border-radius:12px;border:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:.88rem;min-width:760px}
th{background:rgba(0,0,0,.35);color:var(--gold);font-weight:700;text-align:right;padding:12px 10px;white-space:nowrap}
td{padding:10px;border-top:1px solid var(--line);vertical-align:middle}
tr:hover td{background:rgba(255,255,255,.03)}
.mono{font-family:'Courier New',monospace;direction:ltr;unicode-bidi:embed;font-size:.82rem}
.row-actions{display:flex;gap:6px;flex-wrap:wrap}
.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.75rem;font-weight:700;white-space:nowrap}
.badge.on{background:rgba(46,196,182,.18);color:#5eead4;border:1px solid rgba(46,196,182,.5)}
.badge.off{background:rgba(230,57,70,.15);color:#ff8a93;border:1px solid rgba(230,57,70,.5)}
.badge.warn{background:rgba(255,159,69,.15);color:var(--orange);border:1px solid rgba(255,159,69,.5)}
.badge.info{background:rgba(74,163,255,.15);color:#8ec5ff;border:1px solid rgba(74,163,255,.5)}
.badge.gold{background:rgba(244,211,94,.14);color:var(--gold);border:1px solid rgba(244,211,94,.45)}
.bar{height:8px;border-radius:99px;background:rgba(255,255,255,.1);overflow:hidden;min-width:90px}
.bar>i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--blue),var(--pink));transition:width .4s}
.bar.hot>i{background:linear-gradient(90deg,var(--orange),var(--red))}
.stat{text-align:center;padding:16px 10px}
.stat .num{font-size:2rem;font-weight:900;color:var(--milk);line-height:1.2}
.stat .cap{color:var(--muted);font-size:.82rem;margin-top:4px}
.stat .ico{font-size:1.6rem}
.flag-big{font-size:3.2rem;line-height:1;filter:drop-shadow(0 4px 10px rgba(0,0,0,.5))}
.out-ip{font-size:1.5rem;font-weight:900;direction:ltr;color:var(--gold);font-variant-numeric:tabular-nums}
.kv{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px dashed rgba(255,255,255,.12);font-size:.88rem}
.kv:first-of-type{border-top:none}
.kv b{color:var(--milk)}
.node-card{display:flex;flex-direction:column;gap:10px}
.node-card .head{display:flex;align-items:center;gap:10px}
.node-card .head .flag{font-size:1.8rem}
.node-card .head .name{font-weight:800;font-size:1.05rem}
.node-card .head .proto{margin-right:auto}
.node-card .foot{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;position:relative;z-index:1}
.login-card{width:100%;max-width:420px;text-align:center;padding:30px 28px}
.login-card .logo{width:110px;height:110px;filter:drop-shadow(0 8px 20px rgba(230,57,70,.35));animation:float 3.5s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-10px) rotate(2deg)}}
.login-card h1{margin:10px 0 2px;font-size:1.6rem}
.login-card .tag{color:var(--muted);font-size:.88rem;margin-bottom:18px}
.login-card input{text-align:center;font-size:1.1rem;letter-spacing:2px;padding:13px}
.login-card .btn{width:100%;justify-content:center;margin-top:12px;padding:13px;font-size:1rem}
.login-err{color:#ff8a93;min-height:24px;font-size:.85rem;margin-top:8px}
.shake{animation:shake .4s}
@keyframes shake{0%,100%{transform:translateX(0)}20%,60%{transform:translateX(-8px)}40%,80%{transform:translateX(8px)}}
.strawberry-rain{position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:0}
.strawberry-rain span{position:absolute;top:-40px;animation:fall linear infinite;opacity:.7}
@keyframes fall{to{transform:translateY(110vh) rotate(360deg)}}
.modal-bg{position:fixed;inset:0;background:rgba(4,6,16,.75);backdrop-filter:blur(4px);display:none;align-items:flex-start;justify-content:center;z-index:50;padding:30px 14px;overflow-y:auto}
.modal-bg.open{display:flex}
.modal{width:100%;max-width:640px;animation:pop .22s ease}
.modal.wide{max-width:860px}
@keyframes pop{from{opacity:0;transform:scale(.95) translateY(10px)}to{opacity:1;transform:none}}
.modal .mhead{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.modal .mhead h3{margin:0;flex:1}
.x{background:rgba(255,255,255,.08);border:none;color:var(--ink);width:34px;height:34px;border-radius:10px;cursor:pointer;font-size:1.1rem}
.x:hover{background:var(--red)}
.mfoot{display:flex;gap:10px;justify-content:flex-start;margin-top:18px}
.link-row{display:flex;gap:8px;align-items:center;background:rgba(0,0,0,.3);border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin-bottom:8px}
.link-row .meta{flex:none;font-size:.78rem;color:var(--muted);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.link-row code{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr;font-size:.75rem;color:var(--blue)}
.qr-box{text-align:center;margin:12px 0}
.qr-box img{width:200px;height:200px;border-radius:14px;border:4px solid #fff;background:#fff}
#toasts{position:fixed;bottom:20px;right:20px;z-index:99;display:flex;flex-direction:column;gap:10px;max-width:min(360px,90vw)}
.toast{background:linear-gradient(180deg,#232c5e,#1a2147);border:1px solid var(--pink);border-radius:12px;padding:12px 16px;box-shadow:var(--shadow);animation:slidein .25s ease;font-size:.88rem}
.toast.err{border-color:var(--red)}
@keyframes slidein{from{opacity:0;transform:translateX(-30px)}to{opacity:1;transform:none}}
.fun{display:flex;gap:12px;align-items:center;background:linear-gradient(90deg,rgba(230,57,70,.18),rgba(74,163,255,.12));border:1px solid rgba(255,123,156,.35);border-radius:var(--radius);padding:14px 18px;margin-bottom:18px;font-size:.92rem}
.fun .face{font-size:1.8rem;animation:float 4s ease-in-out infinite}
.warn-bar{background:rgba(255,159,69,.12);border:1px solid var(--orange);color:var(--orange);border-radius:12px;padding:10px 16px;margin-bottom:16px;font-size:.87rem}
.import-btns{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
footer.foot{text-align:center;color:var(--muted);font-size:.78rem;margin-top:34px;opacity:.8}
footer.foot .kanji{font-size:.8rem}
</style>
</head>
<body>
<div class="strawberry-rain" id="rain"></div>
<div class="login-wrap">
  <div class="card login-card wood" id="card" style="padding-top:26px">
    <div class="logo" style="margin:0 auto"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="milk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff7ec"/><stop offset="1" stop-color="#ffd9e3"/></linearGradient><linearGradient id="wood" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9c6a3c"/><stop offset="1" stop-color="#6f4525"/></linearGradient><linearGradient id="berry" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff7b9c"/><stop offset="1" stop-color="#e63946"/></linearGradient></defs><path d="M78 96 L178 96 L196 226 Q197 236 187 236 L69 236 Q59 236 60 226 Z" fill="url(#milk)" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M78 96 L108 52 L188 52 L178 96 Z" fill="#ffc7d6" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M108 52 L128 96" stroke="#3d2712" stroke-width="5" stroke-linecap="round"/><rect x="158" y="14" width="16" height="62" rx="8" fill="#4aa3ff" stroke="#3d2712" stroke-width="6" transform="rotate(18 166 45)"/><path d="M128 130 C108 130 96 144 98 160 C100 182 114 200 128 200 C142 200 156 182 158 160 C160 144 148 130 128 130 Z" fill="url(#berry)" stroke="#7a1020" stroke-width="6"/><path d="M112 132 L118 118 L126 128 L134 116 L140 128 L148 120 L144 132 Z" fill="#2ec4b6" stroke="#0d5c55" stroke-width="5" stroke-linejoin="round"/><circle cx="118" cy="158" r="3.5" fill="#ffe9b0"/><circle cx="132" cy="150" r="3.5" fill="#ffe9b0"/><circle cx="140" cy="166" r="3.5" fill="#ffe9b0"/><circle cx="124" cy="176" r="3.5" fill="#ffe9b0"/><rect x="52" y="196" width="152" height="34" rx="8" fill="url(#wood)" stroke="#3d2712" stroke-width="6"/><text x="128" y="221" text-anchor="middle" font-family="serif" font-weight="900" font-size="20" fill="#f4d35e" letter-spacing="4">万事屋</text></svg></div>
    <div class="kanji">万事屋</div>
    <h1>🍓 یوروزویا پنل</h1>
    <div class="tag">هر کاری می‌کنیم… حتی سابسکریپشن 😎</div>
    <input type="password" id="pw" placeholder="رمز پنل…" autocomplete="current-password" onkeydown="if(event.key==='Enter')login()">
    <button class="btn" onclick="login()" id="btn">⚔️ ورود به مغازه</button>
    <div class="login-err" id="err"></div>
    <div class="tag" style="margin-top:6px;font-size:.75rem">🍓 شیر توت‌فرنگی یادت نره! (Node.js edition ⚡)</div>
  </div>
</div>
<script>
var API_BASE = '/';
var rain = document.getElementById('rain');
var fruits = ['🍓','🥛','🍓','🌸','🍓'];
for (var i = 0; i < 24; i++) {
  var s = document.createElement('span');
  s.textContent = fruits[i % 5];
  s.style.right = Math.random() * 100 + '%';
  s.style.fontSize = (14 + Math.random() * 22) + 'px';
  s.style.animationDuration = (5 + Math.random() * 7) + 's';
  s.style.animationDelay = (-Math.random() * 10) + 's';
  rain.appendChild(s);
}
function login() {
  var pw = document.getElementById('pw').value;
  var err = document.getElementById('err');
  var btn = document.getElementById('btn');
  var card = document.getElementById('card');
  err.textContent = '';
  btn.disabled = true;
  fetch(API_BASE + 'api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) })
    .then(function (r) {
      if (r.ok) { location.href = API_BASE + 'panel'; return; }
      err.textContent = '❌ رمز اشتباهه! ساداهارو هم نتونست تأییدت کنه 🐶';
      card.classList.remove('shake'); void card.offsetWidth; card.classList.add('shake');
      btn.disabled = false;
    })
    .catch(function () { err.textContent = '❌ خطای اتصال به سرور'; btn.disabled = false; });
}
document.getElementById('pw').focus();
</script>
</body>
</html>'''
PAGE_SETUP = r'''<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍓 ساخت رمز | یوروزویا پنل</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
:root{--bg:#0b1020;--bg2:#101731;--card:#151c3a;--card2:#1b2350;--line:rgba(255,255,255,.09);--ink:#eef1ff;--muted:#9aa3c7;--red:#e63946;--pink:#ff7b9c;--blue:#4aa3ff;--gold:#f4d35e;--green:#2ec4b6;--orange:#ff9f45;--milk:#fff7ec;--wood1:#9c6a3c;--wood2:#6f4525;--radius:16px;--shadow:0 10px 40px rgba(0,0,0,.45)}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{font-family:'Vazirmatn',Tahoma,Arial,sans-serif;color:var(--ink);min-height:100vh;background:radial-gradient(1200px 600px at 85% -10%,rgba(230,57,70,.16),transparent 60%),radial-gradient(900px 500px at 10% 110%,rgba(74,163,255,.14),transparent 60%),var(--bg)}
body::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.5;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='60' viewBox='0 0 120 60'%3E%3Cg fill='none' stroke='rgba(255,255,255,0.05)' stroke-width='1.5'%3E%3Ccircle cx='30' cy='60' r='28'/%3E%3Ccircle cx='30' cy='60' r='20'/%3E%3Ccircle cx='30' cy='60' r='12'/%3E%3Ccircle cx='90' cy='60' r='28'/%3E%3Ccircle cx='90' cy='60' r='20'/%3E%3Ccircle cx='90' cy='60' r='12'/%3E%3Ccircle cx='60' cy='30' r='28'/%3E%3Ccircle cx='60' cy='30' r='20'/%3E%3Ccircle cx='60' cy='30' r='12'/%3E%3C/g%3E%3C/svg%3E")}
a{color:var(--blue)}::selection{background:var(--red);color:#fff}
.wood{background:repeating-linear-gradient(93deg,rgba(0,0,0,.12) 0 3px,transparent 3px 9px),linear-gradient(180deg,var(--wood1),var(--wood2));border:2px solid #3d2712;border-radius:12px;box-shadow:inset 0 1px 0 rgba(255,255,255,.25),inset 0 -6px 14px rgba(0,0,0,.35),var(--shadow);color:var(--milk);text-shadow:0 2px 0 rgba(0,0,0,.5);position:relative}
.wood::before,.wood::after{content:"";position:absolute;top:8px;width:10px;height:10px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#ffe9b0,#8a6a2a 70%);box-shadow:0 1px 2px rgba(0,0,0,.6)}
.wood::before{right:10px}.wood::after{left:10px}
.kanji{font-weight:900;letter-spacing:.35em;color:var(--gold);text-shadow:0 2px 0 rgba(0,0,0,.55),0 0 18px rgba(244,211,94,.35)}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 60px;position:relative;z-index:1}
.topbar{display:flex;align-items:center;gap:14px;padding:12px 18px;margin-bottom:22px}
.topbar .logo{width:52px;height:52px;flex:none;filter:drop-shadow(0 4px 8px rgba(0,0,0,.5))}
.topbar h1{font-size:1.35rem;margin:0}
.topbar h1 .kanji{font-size:.95rem;margin-right:8px}
.topbar .sub{color:var(--milk);opacity:.85;font-size:.85rem;margin-top:2px}
.topbar .spacer{flex:1}
.clock{font-variant-numeric:tabular-nums;color:var(--gold);font-weight:700}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px}
.card h2,.card h3{margin:0 0 12px}
.muted{color:var(--muted)}
.grid{display:grid;gap:16px}.grid.c2{grid-template-columns:repeat(2,1fr)}.grid.c3{grid-template-columns:repeat(3,1fr)}.grid.c4{grid-template-columns:repeat(4,1fr)}
@media (max-width:900px){.grid.c4{grid-template-columns:repeat(2,1fr)}.grid.c3{grid-template-columns:1fr}.grid.c2{grid-template-columns:1fr}}
.tabs{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}
.tab{border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--ink);padding:10px 18px;border-radius:999px;cursor:pointer;font-family:inherit;font-size:.95rem;transition:all .18s ease}
.tab:hover{background:rgba(255,255,255,.09);transform:translateY(-1px)}
.tab.active{background:linear-gradient(180deg,#f25460,var(--red));border-color:#ff8a93;color:#fff;font-weight:700;box-shadow:0 6px 20px rgba(230,57,70,.4)}
.tabpane{display:none;animation:fade .25s ease}.tabpane.active{display:block}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.btn{font-family:inherit;border:none;border-radius:12px;padding:10px 18px;cursor:pointer;font-size:.92rem;font-weight:700;color:#fff;background:linear-gradient(180deg,#f25460,var(--red));box-shadow:0 4px 16px rgba(230,57,70,.35),inset 0 1px 0 rgba(255,255,255,.3);transition:all .15s ease;display:inline-flex;align-items:center;gap:6px}
.btn:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(230,57,70,.45)}
.btn:active{transform:translateY(1px)}
.btn.blue{background:linear-gradient(180deg,#63b3ff,#2f7fe0);box-shadow:0 4px 16px rgba(74,163,255,.35)}
.btn.green{background:linear-gradient(180deg,#3fd6c5,#1d9c8f);box-shadow:0 4px 16px rgba(46,196,182,.3)}
.btn.gold{background:linear-gradient(180deg,#f7dd7e,#d9a93b);color:#3a2a00}
.btn.ghost{background:rgba(255,255,255,.07);box-shadow:none;border:1px solid var(--line);color:var(--ink);font-weight:500}
.btn.danger{background:linear-gradient(180deg,#6b7280,#374151);box-shadow:none}
.btn.small{padding:6px 12px;font-size:.8rem;border-radius:9px}
.btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.toolbar .spacer{flex:1}
input,select,textarea{font-family:inherit;background:rgba(0,0,0,.35);border:1px solid var(--line);color:var(--ink);border-radius:10px;padding:10px 12px;font-size:.9rem;width:100%;outline:none;transition:border .15s;color-scheme:dark}
input:focus,select:focus,textarea:focus{border-color:var(--pink);box-shadow:0 0 0 3px rgba(255,123,156,.15)}
input[type="checkbox"]{width:auto;transform:scale(1.3);accent-color:var(--red)}
label.lbl{display:block;font-size:.8rem;color:var(--muted);margin:10px 0 5px}
.form-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:0 14px}
.form-grid .full{grid-column:1/-1}
@media (max-width:640px){.form-grid{grid-template-columns:1fr}}
.search{max-width:260px}
.table-wrap{overflow-x:auto;border-radius:12px;border:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:.88rem;min-width:760px}
th{background:rgba(0,0,0,.35);color:var(--gold);font-weight:700;text-align:right;padding:12px 10px;white-space:nowrap}
td{padding:10px;border-top:1px solid var(--line);vertical-align:middle}
tr:hover td{background:rgba(255,255,255,.03)}
.mono{font-family:'Courier New',monospace;direction:ltr;unicode-bidi:embed;font-size:.82rem}
.row-actions{display:flex;gap:6px;flex-wrap:wrap}
.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.75rem;font-weight:700;white-space:nowrap}
.badge.on{background:rgba(46,196,182,.18);color:#5eead4;border:1px solid rgba(46,196,182,.5)}
.badge.off{background:rgba(230,57,70,.15);color:#ff8a93;border:1px solid rgba(230,57,70,.5)}
.badge.warn{background:rgba(255,159,69,.15);color:var(--orange);border:1px solid rgba(255,159,69,.5)}
.badge.info{background:rgba(74,163,255,.15);color:#8ec5ff;border:1px solid rgba(74,163,255,.5)}
.badge.gold{background:rgba(244,211,94,.14);color:var(--gold);border:1px solid rgba(244,211,94,.45)}
.bar{height:8px;border-radius:99px;background:rgba(255,255,255,.1);overflow:hidden;min-width:90px}
.bar>i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--blue),var(--pink));transition:width .4s}
.bar.hot>i{background:linear-gradient(90deg,var(--orange),var(--red))}
.stat{text-align:center;padding:16px 10px}
.stat .num{font-size:2rem;font-weight:900;color:var(--milk);line-height:1.2}
.stat .cap{color:var(--muted);font-size:.82rem;margin-top:4px}
.stat .ico{font-size:1.6rem}
.flag-big{font-size:3.2rem;line-height:1;filter:drop-shadow(0 4px 10px rgba(0,0,0,.5))}
.out-ip{font-size:1.5rem;font-weight:900;direction:ltr;color:var(--gold);font-variant-numeric:tabular-nums}
.kv{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px dashed rgba(255,255,255,.12);font-size:.88rem}
.kv:first-of-type{border-top:none}
.kv b{color:var(--milk)}
.node-card{display:flex;flex-direction:column;gap:10px}
.node-card .head{display:flex;align-items:center;gap:10px}
.node-card .head .flag{font-size:1.8rem}
.node-card .head .name{font-weight:800;font-size:1.05rem}
.node-card .head .proto{margin-right:auto}
.node-card .foot{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;position:relative;z-index:1}
.login-card{width:100%;max-width:420px;text-align:center;padding:30px 28px}
.login-card .logo{width:110px;height:110px;filter:drop-shadow(0 8px 20px rgba(230,57,70,.35));animation:float 3.5s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-10px) rotate(2deg)}}
.login-card h1{margin:10px 0 2px;font-size:1.6rem}
.login-card .tag{color:var(--muted);font-size:.88rem;margin-bottom:18px}
.login-card input{text-align:center;font-size:1.1rem;letter-spacing:2px;padding:13px}
.login-card .btn{width:100%;justify-content:center;margin-top:12px;padding:13px;font-size:1rem}
.login-err{color:#ff8a93;min-height:24px;font-size:.85rem;margin-top:8px}
.shake{animation:shake .4s}
@keyframes shake{0%,100%{transform:translateX(0)}20%,60%{transform:translateX(-8px)}40%,80%{transform:translateX(8px)}}
.strawberry-rain{position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:0}
.strawberry-rain span{position:absolute;top:-40px;animation:fall linear infinite;opacity:.7}
@keyframes fall{to{transform:translateY(110vh) rotate(360deg)}}
.modal-bg{position:fixed;inset:0;background:rgba(4,6,16,.75);backdrop-filter:blur(4px);display:none;align-items:flex-start;justify-content:center;z-index:50;padding:30px 14px;overflow-y:auto}
.modal-bg.open{display:flex}
.modal{width:100%;max-width:640px;animation:pop .22s ease}
.modal.wide{max-width:860px}
@keyframes pop{from{opacity:0;transform:scale(.95) translateY(10px)}to{opacity:1;transform:none}}
.modal .mhead{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.modal .mhead h3{margin:0;flex:1}
.x{background:rgba(255,255,255,.08);border:none;color:var(--ink);width:34px;height:34px;border-radius:10px;cursor:pointer;font-size:1.1rem}
.x:hover{background:var(--red)}
.mfoot{display:flex;gap:10px;justify-content:flex-start;margin-top:18px}
.link-row{display:flex;gap:8px;align-items:center;background:rgba(0,0,0,.3);border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin-bottom:8px}
.link-row .meta{flex:none;font-size:.78rem;color:var(--muted);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.link-row code{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr;font-size:.75rem;color:var(--blue)}
.qr-box{text-align:center;margin:12px 0}
.qr-box img{width:200px;height:200px;border-radius:14px;border:4px solid #fff;background:#fff}
#toasts{position:fixed;bottom:20px;right:20px;z-index:99;display:flex;flex-direction:column;gap:10px;max-width:min(360px,90vw)}
.toast{background:linear-gradient(180deg,#232c5e,#1a2147);border:1px solid var(--pink);border-radius:12px;padding:12px 16px;box-shadow:var(--shadow);animation:slidein .25s ease;font-size:.88rem}
.toast.err{border-color:var(--red)}
@keyframes slidein{from{opacity:0;transform:translateX(-30px)}to{opacity:1;transform:none}}
.fun{display:flex;gap:12px;align-items:center;background:linear-gradient(90deg,rgba(230,57,70,.18),rgba(74,163,255,.12));border:1px solid rgba(255,123,156,.35);border-radius:var(--radius);padding:14px 18px;margin-bottom:18px;font-size:.92rem}
.fun .face{font-size:1.8rem;animation:float 4s ease-in-out infinite}
.warn-bar{background:rgba(255,159,69,.12);border:1px solid var(--orange);color:var(--orange);border-radius:12px;padding:10px 16px;margin-bottom:16px;font-size:.87rem}
.import-btns{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
footer.foot{text-align:center;color:var(--muted);font-size:.78rem;margin-top:34px;opacity:.8}
footer.foot .kanji{font-size:.8rem}
</style>
</head>
<body>
<div class="strawberry-rain" id="rain"></div>
<div class="login-wrap">
  <div class="card login-card wood" id="card" style="padding-top:26px">
    <div class="logo" style="margin:0 auto"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="milk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff7ec"/><stop offset="1" stop-color="#ffd9e3"/></linearGradient><linearGradient id="wood" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9c6a3c"/><stop offset="1" stop-color="#6f4525"/></linearGradient><linearGradient id="berry" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff7b9c"/><stop offset="1" stop-color="#e63946"/></linearGradient></defs><path d="M78 96 L178 96 L196 226 Q197 236 187 236 L69 236 Q59 236 60 226 Z" fill="url(#milk)" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M78 96 L108 52 L188 52 L178 96 Z" fill="#ffc7d6" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M108 52 L128 96" stroke="#3d2712" stroke-width="5" stroke-linecap="round"/><rect x="158" y="14" width="16" height="62" rx="8" fill="#4aa3ff" stroke="#3d2712" stroke-width="6" transform="rotate(18 166 45)"/><path d="M128 130 C108 130 96 144 98 160 C100 182 114 200 128 200 C142 200 156 182 158 160 C160 144 148 130 128 130 Z" fill="url(#berry)" stroke="#7a1020" stroke-width="6"/><path d="M112 132 L118 118 L126 128 L134 116 L140 128 L148 120 L144 132 Z" fill="#2ec4b6" stroke="#0d5c55" stroke-width="5" stroke-linejoin="round"/><circle cx="118" cy="158" r="3.5" fill="#ffe9b0"/><circle cx="132" cy="150" r="3.5" fill="#ffe9b0"/><circle cx="140" cy="166" r="3.5" fill="#ffe9b0"/><circle cx="124" cy="176" r="3.5" fill="#ffe9b0"/><rect x="52" y="196" width="152" height="34" rx="8" fill="url(#wood)" stroke="#3d2712" stroke-width="6"/><text x="128" y="221" text-anchor="middle" font-family="serif" font-weight="900" font-size="20" fill="#f4d35e" letter-spacing="4">万事屋</text></svg></div>
    <div class="kanji">万事屋</div>
    <h1>🍓 یوروزویا پنل</h1>
    <div class="tag">اولین ورود! یه رمز برای مغازه انتخاب کن — فقط همین یه بار 🔐</div>
    <input type="password" id="pw" placeholder="رمز جدید (حداقل ۴ کاراکتر)…" autocomplete="new-password">
    <input type="password" id="pw2" placeholder="تکرار رمز…" autocomplete="new-password" style="margin-top:10px" onkeydown="if(event.key==='Enter')setup()">
    <button class="btn" onclick="setup()" id="btn">🔐 ساخت رمز و ورود به مغازه</button>
    <div class="login-err" id="err"></div>
    <div class="tag" style="margin-top:6px;font-size:.75rem">🔐 رمز به‌صورت هش (SHA-256) ذخیره می‌شه — یادت نره! 😅</div>
  </div>
</div>
<script>
var API_BASE = '/';
var rain = document.getElementById('rain');
var fruits = ['🍓','🥛','🍓','🌸','🍓'];
for (var i = 0; i < 24; i++) {
  var s = document.createElement('span');
  s.textContent = fruits[i % 5];
  s.style.right = Math.random() * 100 + '%';
  s.style.fontSize = (14 + Math.random() * 22) + 'px';
  s.style.animationDuration = (5 + Math.random() * 7) + 's';
  s.style.animationDelay = (-Math.random() * 10) + 's';
  rain.appendChild(s);
}
function setup() {
  var pw = document.getElementById('pw').value;
  var pw2 = document.getElementById('pw2').value;
  var err = document.getElementById('err');
  var btn = document.getElementById('btn');
  var card = document.getElementById('card');
  err.textContent = '';
  function shake() { card.classList.remove('shake'); void card.offsetWidth; card.classList.add('shake'); }
  if (pw.length < 4) { err.textContent = '❌ رمز باید حداقل ۴ کاراکتر باشه!'; shake(); return; }
  if (pw !== pw2) { err.textContent = '❌ تکرار رمز اشتباهه!'; shake(); return; }
  btn.disabled = true;
  fetch(API_BASE + 'api/setup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) })
    .then(function (r) {
      if (r.ok) { location.href = API_BASE + 'panel'; return; }
      return r.json().then(function (d) { err.textContent = '❌ ' + (d.detail || 'خطا'); btn.disabled = false; shake(); });
    })
    .catch(function () { err.textContent = '❌ خطای اتصال به سرور'; btn.disabled = false; });
}
document.getElementById('pw').focus();
</script>
</body>
</html>'''
PAGE_PANEL = r'''<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍓 یوروزویا پنل</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
:root{--bg:#0b1020;--bg2:#101731;--card:#151c3a;--card2:#1b2350;--line:rgba(255,255,255,.09);--ink:#eef1ff;--muted:#9aa3c7;--red:#e63946;--pink:#ff7b9c;--blue:#4aa3ff;--gold:#f4d35e;--green:#2ec4b6;--orange:#ff9f45;--milk:#fff7ec;--wood1:#9c6a3c;--wood2:#6f4525;--radius:16px;--shadow:0 10px 40px rgba(0,0,0,.45)}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{font-family:'Vazirmatn',Tahoma,Arial,sans-serif;color:var(--ink);min-height:100vh;background:radial-gradient(1200px 600px at 85% -10%,rgba(230,57,70,.16),transparent 60%),radial-gradient(900px 500px at 10% 110%,rgba(74,163,255,.14),transparent 60%),var(--bg)}
body::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.5;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='60' viewBox='0 0 120 60'%3E%3Cg fill='none' stroke='rgba(255,255,255,0.05)' stroke-width='1.5'%3E%3Ccircle cx='30' cy='60' r='28'/%3E%3Ccircle cx='30' cy='60' r='20'/%3E%3Ccircle cx='30' cy='60' r='12'/%3E%3Ccircle cx='90' cy='60' r='28'/%3E%3Ccircle cx='90' cy='60' r='20'/%3E%3Ccircle cx='90' cy='60' r='12'/%3E%3Ccircle cx='60' cy='30' r='28'/%3E%3Ccircle cx='60' cy='30' r='20'/%3E%3Ccircle cx='60' cy='30' r='12'/%3E%3C/g%3E%3C/svg%3E")}
a{color:var(--blue)}::selection{background:var(--red);color:#fff}
.wood{background:repeating-linear-gradient(93deg,rgba(0,0,0,.12) 0 3px,transparent 3px 9px),linear-gradient(180deg,var(--wood1),var(--wood2));border:2px solid #3d2712;border-radius:12px;box-shadow:inset 0 1px 0 rgba(255,255,255,.25),inset 0 -6px 14px rgba(0,0,0,.35),var(--shadow);color:var(--milk);text-shadow:0 2px 0 rgba(0,0,0,.5);position:relative}
.wood::before,.wood::after{content:"";position:absolute;top:8px;width:10px;height:10px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#ffe9b0,#8a6a2a 70%);box-shadow:0 1px 2px rgba(0,0,0,.6)}
.wood::before{right:10px}.wood::after{left:10px}
.kanji{font-weight:900;letter-spacing:.35em;color:var(--gold);text-shadow:0 2px 0 rgba(0,0,0,.55),0 0 18px rgba(244,211,94,.35)}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 60px;position:relative;z-index:1}
.topbar{display:flex;align-items:center;gap:14px;padding:12px 18px;margin-bottom:22px}
.topbar .logo{width:52px;height:52px;flex:none;filter:drop-shadow(0 4px 8px rgba(0,0,0,.5))}
.topbar h1{font-size:1.35rem;margin:0}
.topbar h1 .kanji{font-size:.95rem;margin-right:8px}
.topbar .sub{color:var(--milk);opacity:.85;font-size:.85rem;margin-top:2px}
.topbar .spacer{flex:1}
.clock{font-variant-numeric:tabular-nums;color:var(--gold);font-weight:700}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px}
.card h2,.card h3{margin:0 0 12px}
.muted{color:var(--muted)}
.grid{display:grid;gap:16px}.grid.c2{grid-template-columns:repeat(2,1fr)}.grid.c3{grid-template-columns:repeat(3,1fr)}.grid.c4{grid-template-columns:repeat(4,1fr)}
@media (max-width:900px){.grid.c4{grid-template-columns:repeat(2,1fr)}.grid.c3{grid-template-columns:1fr}.grid.c2{grid-template-columns:1fr}}
.tabs{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}
.tab{border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--ink);padding:10px 18px;border-radius:999px;cursor:pointer;font-family:inherit;font-size:.95rem;transition:all .18s ease}
.tab:hover{background:rgba(255,255,255,.09);transform:translateY(-1px)}
.tab.active{background:linear-gradient(180deg,#f25460,var(--red));border-color:#ff8a93;color:#fff;font-weight:700;box-shadow:0 6px 20px rgba(230,57,70,.4)}
.tabpane{display:none;animation:fade .25s ease}.tabpane.active{display:block}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.btn{font-family:inherit;border:none;border-radius:12px;padding:10px 18px;cursor:pointer;font-size:.92rem;font-weight:700;color:#fff;background:linear-gradient(180deg,#f25460,var(--red));box-shadow:0 4px 16px rgba(230,57,70,.35),inset 0 1px 0 rgba(255,255,255,.3);transition:all .15s ease;display:inline-flex;align-items:center;gap:6px}
.btn:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(230,57,70,.45)}
.btn:active{transform:translateY(1px)}
.btn.blue{background:linear-gradient(180deg,#63b3ff,#2f7fe0);box-shadow:0 4px 16px rgba(74,163,255,.35)}
.btn.green{background:linear-gradient(180deg,#3fd6c5,#1d9c8f);box-shadow:0 4px 16px rgba(46,196,182,.3)}
.btn.gold{background:linear-gradient(180deg,#f7dd7e,#d9a93b);color:#3a2a00}
.btn.ghost{background:rgba(255,255,255,.07);box-shadow:none;border:1px solid var(--line);color:var(--ink);font-weight:500}
.btn.danger{background:linear-gradient(180deg,#6b7280,#374151);box-shadow:none}
.btn.small{padding:6px 12px;font-size:.8rem;border-radius:9px}
.btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.toolbar .spacer{flex:1}
input,select,textarea{font-family:inherit;background:rgba(0,0,0,.35);border:1px solid var(--line);color:var(--ink);border-radius:10px;padding:10px 12px;font-size:.9rem;width:100%;outline:none;transition:border .15s;color-scheme:dark}
input:focus,select:focus,textarea:focus{border-color:var(--pink);box-shadow:0 0 0 3px rgba(255,123,156,.15)}
input[type="checkbox"]{width:auto;transform:scale(1.3);accent-color:var(--red)}
label.lbl{display:block;font-size:.8rem;color:var(--muted);margin:10px 0 5px}
.form-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:0 14px}
.form-grid .full{grid-column:1/-1}
@media (max-width:640px){.form-grid{grid-template-columns:1fr}}
.search{max-width:260px}
.table-wrap{overflow-x:auto;border-radius:12px;border:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:.88rem;min-width:760px}
th{background:rgba(0,0,0,.35);color:var(--gold);font-weight:700;text-align:right;padding:12px 10px;white-space:nowrap}
td{padding:10px;border-top:1px solid var(--line);vertical-align:middle}
tr:hover td{background:rgba(255,255,255,.03)}
.mono{font-family:'Courier New',monospace;direction:ltr;unicode-bidi:embed;font-size:.82rem}
.row-actions{display:flex;gap:6px;flex-wrap:wrap}
.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.75rem;font-weight:700;white-space:nowrap}
.badge.on{background:rgba(46,196,182,.18);color:#5eead4;border:1px solid rgba(46,196,182,.5)}
.badge.off{background:rgba(230,57,70,.15);color:#ff8a93;border:1px solid rgba(230,57,70,.5)}
.badge.warn{background:rgba(255,159,69,.15);color:var(--orange);border:1px solid rgba(255,159,69,.5)}
.badge.info{background:rgba(74,163,255,.15);color:#8ec5ff;border:1px solid rgba(74,163,255,.5)}
.badge.gold{background:rgba(244,211,94,.14);color:var(--gold);border:1px solid rgba(244,211,94,.45)}
.bar{height:8px;border-radius:99px;background:rgba(255,255,255,.1);overflow:hidden;min-width:90px}
.bar>i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--blue),var(--pink));transition:width .4s}
.bar.hot>i{background:linear-gradient(90deg,var(--orange),var(--red))}
.stat{text-align:center;padding:16px 10px}
.stat .num{font-size:2rem;font-weight:900;color:var(--milk);line-height:1.2}
.stat .cap{color:var(--muted);font-size:.82rem;margin-top:4px}
.stat .ico{font-size:1.6rem}
.flag-big{font-size:3.2rem;line-height:1;filter:drop-shadow(0 4px 10px rgba(0,0,0,.5))}
.out-ip{font-size:1.5rem;font-weight:900;direction:ltr;color:var(--gold);font-variant-numeric:tabular-nums}
.kv{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px dashed rgba(255,255,255,.12);font-size:.88rem}
.kv:first-of-type{border-top:none}
.kv b{color:var(--milk)}
.node-card{display:flex;flex-direction:column;gap:10px}
.node-card .head{display:flex;align-items:center;gap:10px}
.node-card .head .flag{font-size:1.8rem}
.node-card .head .name{font-weight:800;font-size:1.05rem}
.node-card .head .proto{margin-right:auto}
.node-card .foot{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;position:relative;z-index:1}
.login-card{width:100%;max-width:420px;text-align:center;padding:30px 28px}
.login-card .logo{width:110px;height:110px;filter:drop-shadow(0 8px 20px rgba(230,57,70,.35));animation:float 3.5s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-10px) rotate(2deg)}}
.login-card h1{margin:10px 0 2px;font-size:1.6rem}
.login-card .tag{color:var(--muted);font-size:.88rem;margin-bottom:18px}
.login-card input{text-align:center;font-size:1.1rem;letter-spacing:2px;padding:13px}
.login-card .btn{width:100%;justify-content:center;margin-top:12px;padding:13px;font-size:1rem}
.login-err{color:#ff8a93;min-height:24px;font-size:.85rem;margin-top:8px}
.shake{animation:shake .4s}
@keyframes shake{0%,100%{transform:translateX(0)}20%,60%{transform:translateX(-8px)}40%,80%{transform:translateX(8px)}}
.strawberry-rain{position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:0}
.strawberry-rain span{position:absolute;top:-40px;animation:fall linear infinite;opacity:.7}
@keyframes fall{to{transform:translateY(110vh) rotate(360deg)}}
.modal-bg{position:fixed;inset:0;background:rgba(4,6,16,.75);backdrop-filter:blur(4px);display:none;align-items:flex-start;justify-content:center;z-index:50;padding:30px 14px;overflow-y:auto}
.modal-bg.open{display:flex}
.modal{width:100%;max-width:640px;animation:pop .22s ease}
.modal.wide{max-width:860px}
@keyframes pop{from{opacity:0;transform:scale(.95) translateY(10px)}to{opacity:1;transform:none}}
.modal .mhead{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.modal .mhead h3{margin:0;flex:1}
.x{background:rgba(255,255,255,.08);border:none;color:var(--ink);width:34px;height:34px;border-radius:10px;cursor:pointer;font-size:1.1rem}
.x:hover{background:var(--red)}
.mfoot{display:flex;gap:10px;justify-content:flex-start;margin-top:18px}
.link-row{display:flex;gap:8px;align-items:center;background:rgba(0,0,0,.3);border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin-bottom:8px}
.link-row .meta{flex:none;font-size:.78rem;color:var(--muted);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.link-row code{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr;font-size:.75rem;color:var(--blue)}
.qr-box{text-align:center;margin:12px 0}
.qr-box img{width:200px;height:200px;border-radius:14px;border:4px solid #fff;background:#fff}.qr-box svg{width:200px;height:200px;border-radius:14px;border:4px solid #fff;background:#fff}
#toasts{position:fixed;bottom:20px;right:20px;z-index:99;display:flex;flex-direction:column;gap:10px;max-width:min(360px,90vw)}
.toast{background:linear-gradient(180deg,#232c5e,#1a2147);border:1px solid var(--pink);border-radius:12px;padding:12px 16px;box-shadow:var(--shadow);animation:slidein .25s ease;font-size:.88rem}
.toast.err{border-color:var(--red)}
@keyframes slidein{from{opacity:0;transform:translateX(-30px)}to{opacity:1;transform:none}}
.fun{display:flex;gap:12px;align-items:center;background:linear-gradient(90deg,rgba(230,57,70,.18),rgba(74,163,255,.12));border:1px solid rgba(255,123,156,.35);border-radius:var(--radius);padding:14px 18px;margin-bottom:18px;font-size:.92rem}
.fun .face{font-size:1.8rem;animation:float 4s ease-in-out infinite}
.warn-bar{background:rgba(255,159,69,.12);border:1px solid var(--orange);color:var(--orange);border-radius:12px;padding:10px 16px;margin-bottom:16px;font-size:.87rem}
.import-btns{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
footer.foot{text-align:center;color:var(--muted);font-size:.78rem;margin-top:34px;opacity:.8}
footer.foot .kanji{font-size:.8rem}
</style>
<script>
/* qrcode-generator (c) Kazuhiko Arase, MIT license — inlined for offline QR rendering */
//---------------------------------------------------------------------
//
// QR Code Generator for JavaScript
//
// Copyright (c) 2009 Kazuhiko Arase
//
// URL: http://www.d-project.com/
//
// Licensed under the MIT license:
//  http://www.opensource.org/licenses/mit-license.php
//
// The word 'QR Code' is registered trademark of
// DENSO WAVE INCORPORATED
//  http://www.denso-wave.com/qrcode/faqpatent-e.html
//
//---------------------------------------------------------------------

var qrcode = function() {

  //---------------------------------------------------------------------
  // qrcode
  //---------------------------------------------------------------------

  /**
   * qrcode
   * @param typeNumber 1 to 40
   * @param errorCorrectionLevel 'L','M','Q','H'
   */
  var qrcode = function(typeNumber, errorCorrectionLevel) {

    var PAD0 = 0xEC;
    var PAD1 = 0x11;

    var _typeNumber = typeNumber;
    var _errorCorrectionLevel = QRErrorCorrectionLevel[errorCorrectionLevel];
    var _modules = null;
    var _moduleCount = 0;
    var _dataCache = null;
    var _dataList = [];

    var _this = {};

    var makeImpl = function(test, maskPattern) {

      _moduleCount = _typeNumber * 4 + 17;
      _modules = function(moduleCount) {
        var modules = new Array(moduleCount);
        for (var row = 0; row < moduleCount; row += 1) {
          modules[row] = new Array(moduleCount);
          for (var col = 0; col < moduleCount; col += 1) {
            modules[row][col] = null;
          }
        }
        return modules;
      }(_moduleCount);

      setupPositionProbePattern(0, 0);
      setupPositionProbePattern(_moduleCount - 7, 0);
      setupPositionProbePattern(0, _moduleCount - 7);
      setupPositionAdjustPattern();
      setupTimingPattern();
      setupTypeInfo(test, maskPattern);

      if (_typeNumber >= 7) {
        setupTypeNumber(test);
      }

      if (_dataCache == null) {
        _dataCache = createData(_typeNumber, _errorCorrectionLevel, _dataList);
      }

      mapData(_dataCache, maskPattern);
    };

    var setupPositionProbePattern = function(row, col) {

      for (var r = -1; r <= 7; r += 1) {

        if (row + r <= -1 || _moduleCount <= row + r) continue;

        for (var c = -1; c <= 7; c += 1) {

          if (col + c <= -1 || _moduleCount <= col + c) continue;

          if ( (0 <= r && r <= 6 && (c == 0 || c == 6) )
              || (0 <= c && c <= 6 && (r == 0 || r == 6) )
              || (2 <= r && r <= 4 && 2 <= c && c <= 4) ) {
            _modules[row + r][col + c] = true;
          } else {
            _modules[row + r][col + c] = false;
          }
        }
      }
    };

    var getBestMaskPattern = function() {

      var minLostPoint = 0;
      var pattern = 0;

      for (var i = 0; i < 8; i += 1) {

        makeImpl(true, i);

        var lostPoint = QRUtil.getLostPoint(_this);

        if (i == 0 || minLostPoint > lostPoint) {
          minLostPoint = lostPoint;
          pattern = i;
        }
      }

      return pattern;
    };

    var setupTimingPattern = function() {

      for (var r = 8; r < _moduleCount - 8; r += 1) {
        if (_modules[r][6] != null) {
          continue;
        }
        _modules[r][6] = (r % 2 == 0);
      }

      for (var c = 8; c < _moduleCount - 8; c += 1) {
        if (_modules[6][c] != null) {
          continue;
        }
        _modules[6][c] = (c % 2 == 0);
      }
    };

    var setupPositionAdjustPattern = function() {

      var pos = QRUtil.getPatternPosition(_typeNumber);

      for (var i = 0; i < pos.length; i += 1) {

        for (var j = 0; j < pos.length; j += 1) {

          var row = pos[i];
          var col = pos[j];

          if (_modules[row][col] != null) {
            continue;
          }

          for (var r = -2; r <= 2; r += 1) {

            for (var c = -2; c <= 2; c += 1) {

              if (r == -2 || r == 2 || c == -2 || c == 2
                  || (r == 0 && c == 0) ) {
                _modules[row + r][col + c] = true;
              } else {
                _modules[row + r][col + c] = false;
              }
            }
          }
        }
      }
    };

    var setupTypeNumber = function(test) {

      var bits = QRUtil.getBCHTypeNumber(_typeNumber);

      for (var i = 0; i < 18; i += 1) {
        var mod = (!test && ( (bits >> i) & 1) == 1);
        _modules[Math.floor(i / 3)][i % 3 + _moduleCount - 8 - 3] = mod;
      }

      for (var i = 0; i < 18; i += 1) {
        var mod = (!test && ( (bits >> i) & 1) == 1);
        _modules[i % 3 + _moduleCount - 8 - 3][Math.floor(i / 3)] = mod;
      }
    };

    var setupTypeInfo = function(test, maskPattern) {

      var data = (_errorCorrectionLevel << 3) | maskPattern;
      var bits = QRUtil.getBCHTypeInfo(data);

      // vertical
      for (var i = 0; i < 15; i += 1) {

        var mod = (!test && ( (bits >> i) & 1) == 1);

        if (i < 6) {
          _modules[i][8] = mod;
        } else if (i < 8) {
          _modules[i + 1][8] = mod;
        } else {
          _modules[_moduleCount - 15 + i][8] = mod;
        }
      }

      // horizontal
      for (var i = 0; i < 15; i += 1) {

        var mod = (!test && ( (bits >> i) & 1) == 1);

        if (i < 8) {
          _modules[8][_moduleCount - i - 1] = mod;
        } else if (i < 9) {
          _modules[8][15 - i - 1 + 1] = mod;
        } else {
          _modules[8][15 - i - 1] = mod;
        }
      }

      // fixed module
      _modules[_moduleCount - 8][8] = (!test);
    };

    var mapData = function(data, maskPattern) {

      var inc = -1;
      var row = _moduleCount - 1;
      var bitIndex = 7;
      var byteIndex = 0;
      var maskFunc = QRUtil.getMaskFunction(maskPattern);

      for (var col = _moduleCount - 1; col > 0; col -= 2) {

        if (col == 6) col -= 1;

        while (true) {

          for (var c = 0; c < 2; c += 1) {

            if (_modules[row][col - c] == null) {

              var dark = false;

              if (byteIndex < data.length) {
                dark = ( ( (data[byteIndex] >>> bitIndex) & 1) == 1);
              }

              var mask = maskFunc(row, col - c);

              if (mask) {
                dark = !dark;
              }

              _modules[row][col - c] = dark;
              bitIndex -= 1;

              if (bitIndex == -1) {
                byteIndex += 1;
                bitIndex = 7;
              }
            }
          }

          row += inc;

          if (row < 0 || _moduleCount <= row) {
            row -= inc;
            inc = -inc;
            break;
          }
        }
      }
    };

    var createBytes = function(buffer, rsBlocks) {

      var offset = 0;

      var maxDcCount = 0;
      var maxEcCount = 0;

      var dcdata = new Array(rsBlocks.length);
      var ecdata = new Array(rsBlocks.length);

      for (var r = 0; r < rsBlocks.length; r += 1) {

        var dcCount = rsBlocks[r].dataCount;
        var ecCount = rsBlocks[r].totalCount - dcCount;

        maxDcCount = Math.max(maxDcCount, dcCount);
        maxEcCount = Math.max(maxEcCount, ecCount);

        dcdata[r] = new Array(dcCount);

        for (var i = 0; i < dcdata[r].length; i += 1) {
          dcdata[r][i] = 0xff & buffer.getBuffer()[i + offset];
        }
        offset += dcCount;

        var rsPoly = QRUtil.getErrorCorrectPolynomial(ecCount);
        var rawPoly = qrPolynomial(dcdata[r], rsPoly.getLength() - 1);

        var modPoly = rawPoly.mod(rsPoly);
        ecdata[r] = new Array(rsPoly.getLength() - 1);
        for (var i = 0; i < ecdata[r].length; i += 1) {
          var modIndex = i + modPoly.getLength() - ecdata[r].length;
          ecdata[r][i] = (modIndex >= 0)? modPoly.getAt(modIndex) : 0;
        }
      }

      var totalCodeCount = 0;
      for (var i = 0; i < rsBlocks.length; i += 1) {
        totalCodeCount += rsBlocks[i].totalCount;
      }

      var data = new Array(totalCodeCount);
      var index = 0;

      for (var i = 0; i < maxDcCount; i += 1) {
        for (var r = 0; r < rsBlocks.length; r += 1) {
          if (i < dcdata[r].length) {
            data[index] = dcdata[r][i];
            index += 1;
          }
        }
      }

      for (var i = 0; i < maxEcCount; i += 1) {
        for (var r = 0; r < rsBlocks.length; r += 1) {
          if (i < ecdata[r].length) {
            data[index] = ecdata[r][i];
            index += 1;
          }
        }
      }

      return data;
    };

    var createData = function(typeNumber, errorCorrectionLevel, dataList) {

      var rsBlocks = QRRSBlock.getRSBlocks(typeNumber, errorCorrectionLevel);

      var buffer = qrBitBuffer();

      for (var i = 0; i < dataList.length; i += 1) {
        var data = dataList[i];
        buffer.put(data.getMode(), 4);
        buffer.put(data.getLength(), QRUtil.getLengthInBits(data.getMode(), typeNumber) );
        data.write(buffer);
      }

      // calc num max data.
      var totalDataCount = 0;
      for (var i = 0; i < rsBlocks.length; i += 1) {
        totalDataCount += rsBlocks[i].dataCount;
      }

      if (buffer.getLengthInBits() > totalDataCount * 8) {
        throw 'code length overflow. ('
          + buffer.getLengthInBits()
          + '>'
          + totalDataCount * 8
          + ')';
      }

      // end code
      if (buffer.getLengthInBits() + 4 <= totalDataCount * 8) {
        buffer.put(0, 4);
      }

      // padding
      while (buffer.getLengthInBits() % 8 != 0) {
        buffer.putBit(false);
      }

      // padding
      while (true) {

        if (buffer.getLengthInBits() >= totalDataCount * 8) {
          break;
        }
        buffer.put(PAD0, 8);

        if (buffer.getLengthInBits() >= totalDataCount * 8) {
          break;
        }
        buffer.put(PAD1, 8);
      }

      return createBytes(buffer, rsBlocks);
    };

    _this.addData = function(data, mode) {

      mode = mode || 'Byte';

      var newData = null;

      switch(mode) {
      case 'Numeric' :
        newData = qrNumber(data);
        break;
      case 'Alphanumeric' :
        newData = qrAlphaNum(data);
        break;
      case 'Byte' :
        newData = qr8BitByte(data);
        break;
      case 'Kanji' :
        newData = qrKanji(data);
        break;
      default :
        throw 'mode:' + mode;
      }

      _dataList.push(newData);
      _dataCache = null;
    };

    _this.isDark = function(row, col) {
      if (row < 0 || _moduleCount <= row || col < 0 || _moduleCount <= col) {
        throw row + ',' + col;
      }
      return _modules[row][col];
    };

    _this.getModuleCount = function() {
      return _moduleCount;
    };

    _this.make = function() {
      if (_typeNumber < 1) {
        var typeNumber = 1;

        for (; typeNumber < 40; typeNumber++) {
          var rsBlocks = QRRSBlock.getRSBlocks(typeNumber, _errorCorrectionLevel);
          var buffer = qrBitBuffer();

          for (var i = 0; i < _dataList.length; i++) {
            var data = _dataList[i];
            buffer.put(data.getMode(), 4);
            buffer.put(data.getLength(), QRUtil.getLengthInBits(data.getMode(), typeNumber) );
            data.write(buffer);
          }

          var totalDataCount = 0;
          for (var i = 0; i < rsBlocks.length; i++) {
            totalDataCount += rsBlocks[i].dataCount;
          }

          if (buffer.getLengthInBits() <= totalDataCount * 8) {
            break;
          }
        }

        _typeNumber = typeNumber;
      }

      makeImpl(false, getBestMaskPattern() );
    };

    _this.createTableTag = function(cellSize, margin) {

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      var qrHtml = '';

      qrHtml += '<table style="';
      qrHtml += ' border-width: 0px; border-style: none;';
      qrHtml += ' border-collapse: collapse;';
      qrHtml += ' padding: 0px; margin: ' + margin + 'px;';
      qrHtml += '">';
      qrHtml += '<tbody>';

      for (var r = 0; r < _this.getModuleCount(); r += 1) {

        qrHtml += '<tr>';

        for (var c = 0; c < _this.getModuleCount(); c += 1) {
          qrHtml += '<td style="';
          qrHtml += ' border-width: 0px; border-style: none;';
          qrHtml += ' border-collapse: collapse;';
          qrHtml += ' padding: 0px; margin: 0px;';
          qrHtml += ' width: ' + cellSize + 'px;';
          qrHtml += ' height: ' + cellSize + 'px;';
          qrHtml += ' background-color: ';
          qrHtml += _this.isDark(r, c)? '#000000' : '#ffffff';
          qrHtml += ';';
          qrHtml += '"/>';
        }

        qrHtml += '</tr>';
      }

      qrHtml += '</tbody>';
      qrHtml += '</table>';

      return qrHtml;
    };

    _this.createSvgTag = function(cellSize, margin, alt, title) {

      var opts = {};
      if (typeof arguments[0] == 'object') {
        // Called by options.
        opts = arguments[0];
        // overwrite cellSize and margin.
        cellSize = opts.cellSize;
        margin = opts.margin;
        alt = opts.alt;
        title = opts.title;
      }

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      // Compose alt property surrogate
      alt = (typeof alt === 'string') ? {text: alt} : alt || {};
      alt.text = alt.text || null;
      alt.id = (alt.text) ? alt.id || 'qrcode-description' : null;

      // Compose title property surrogate
      title = (typeof title === 'string') ? {text: title} : title || {};
      title.text = title.text || null;
      title.id = (title.text) ? title.id || 'qrcode-title' : null;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var c, mc, r, mr, qrSvg='', rect;

      rect = 'l' + cellSize + ',0 0,' + cellSize +
        ' -' + cellSize + ',0 0,-' + cellSize + 'z ';

      qrSvg += '<svg version="1.1" xmlns="http://www.w3.org/2000/svg"';
      qrSvg += !opts.scalable ? ' width="' + size + 'px" height="' + size + 'px"' : '';
      qrSvg += ' viewBox="0 0 ' + size + ' ' + size + '" ';
      qrSvg += ' preserveAspectRatio="xMinYMin meet"';
      qrSvg += (title.text || alt.text) ? ' role="img" aria-labelledby="' +
          escapeXml([title.id, alt.id].join(' ').trim() ) + '"' : '';
      qrSvg += '>';
      qrSvg += (title.text) ? '<title id="' + escapeXml(title.id) + '">' +
          escapeXml(title.text) + '</title>' : '';
      qrSvg += (alt.text) ? '<description id="' + escapeXml(alt.id) + '">' +
          escapeXml(alt.text) + '</description>' : '';
      qrSvg += '<rect width="100%" height="100%" fill="white" cx="0" cy="0"/>';
      qrSvg += '<path d="';

      for (r = 0; r < _this.getModuleCount(); r += 1) {
        mr = r * cellSize + margin;
        for (c = 0; c < _this.getModuleCount(); c += 1) {
          if (_this.isDark(r, c) ) {
            mc = c*cellSize+margin;
            qrSvg += 'M' + mc + ',' + mr + rect;
          }
        }
      }

      qrSvg += '" stroke="transparent" fill="black"/>';
      qrSvg += '</svg>';

      return qrSvg;
    };

    _this.createDataURL = function(cellSize, margin) {

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var min = margin;
      var max = size - margin;

      return createDataURL(size, size, function(x, y) {
        if (min <= x && x < max && min <= y && y < max) {
          var c = Math.floor( (x - min) / cellSize);
          var r = Math.floor( (y - min) / cellSize);
          return _this.isDark(r, c)? 0 : 1;
        } else {
          return 1;
        }
      } );
    };

    _this.createImgTag = function(cellSize, margin, alt) {

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;

      var img = '';
      img += '<img';
      img += '\u0020src="';
      img += _this.createDataURL(cellSize, margin);
      img += '"';
      img += '\u0020width="';
      img += size;
      img += '"';
      img += '\u0020height="';
      img += size;
      img += '"';
      if (alt) {
        img += '\u0020alt="';
        img += escapeXml(alt);
        img += '"';
      }
      img += '/>';

      return img;
    };

    var escapeXml = function(s) {
      var escaped = '';
      for (var i = 0; i < s.length; i += 1) {
        var c = s.charAt(i);
        switch(c) {
        case '<': escaped += '&lt;'; break;
        case '>': escaped += '&gt;'; break;
        case '&': escaped += '&amp;'; break;
        case '"': escaped += '&quot;'; break;
        default : escaped += c; break;
        }
      }
      return escaped;
    };

    var _createHalfASCII = function(margin) {
      var cellSize = 1;
      margin = (typeof margin == 'undefined')? cellSize * 2 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var min = margin;
      var max = size - margin;

      var y, x, r1, r2, p;

      var blocks = {
        '██': '█',
        '█ ': '▀',
        ' █': '▄',
        '  ': ' '
      };

      var blocksLastLineNoMargin = {
        '██': '▀',
        '█ ': '▀',
        ' █': ' ',
        '  ': ' '
      };

      var ascii = '';
      for (y = 0; y < size; y += 2) {
        r1 = Math.floor((y - min) / cellSize);
        r2 = Math.floor((y + 1 - min) / cellSize);
        for (x = 0; x < size; x += 1) {
          p = '█';

          if (min <= x && x < max && min <= y && y < max && _this.isDark(r1, Math.floor((x - min) / cellSize))) {
            p = ' ';
          }

          if (min <= x && x < max && min <= y+1 && y+1 < max && _this.isDark(r2, Math.floor((x - min) / cellSize))) {
            p += ' ';
          }
          else {
            p += '█';
          }

          // Output 2 characters per pixel, to create full square. 1 character per pixels gives only half width of square.
          ascii += (margin < 1 && y+1 >= max) ? blocksLastLineNoMargin[p] : blocks[p];
        }

        ascii += '\n';
      }

      if (size % 2 && margin > 0) {
        return ascii.substring(0, ascii.length - size - 1) + Array(size+1).join('▀');
      }

      return ascii.substring(0, ascii.length-1);
    };

    _this.createASCII = function(cellSize, margin) {
      cellSize = cellSize || 1;

      if (cellSize < 2) {
        return _createHalfASCII(margin);
      }

      cellSize -= 1;
      margin = (typeof margin == 'undefined')? cellSize * 2 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var min = margin;
      var max = size - margin;

      var y, x, r, p;

      var white = Array(cellSize+1).join('██');
      var black = Array(cellSize+1).join('  ');

      var ascii = '';
      var line = '';
      for (y = 0; y < size; y += 1) {
        r = Math.floor( (y - min) / cellSize);
        line = '';
        for (x = 0; x < size; x += 1) {
          p = 1;

          if (min <= x && x < max && min <= y && y < max && _this.isDark(r, Math.floor((x - min) / cellSize))) {
            p = 0;
          }

          // Output 2 characters per pixel, to create full square. 1 character per pixels gives only half width of square.
          line += p ? white : black;
        }

        for (r = 0; r < cellSize; r += 1) {
          ascii += line + '\n';
        }
      }

      return ascii.substring(0, ascii.length-1);
    };

    _this.renderTo2dContext = function(context, cellSize) {
      cellSize = cellSize || 2;
      var length = _this.getModuleCount();
      for (var row = 0; row < length; row++) {
        for (var col = 0; col < length; col++) {
          context.fillStyle = _this.isDark(row, col) ? 'black' : 'white';
          context.fillRect(col * cellSize, row * cellSize, cellSize, cellSize);
        }
      }
    }

    return _this;
  };

  //---------------------------------------------------------------------
  // qrcode.stringToBytes
  //---------------------------------------------------------------------

  qrcode.stringToBytesFuncs = {
    'default' : function(s) {
      var bytes = [];
      for (var i = 0; i < s.length; i += 1) {
        var c = s.charCodeAt(i);
        bytes.push(c & 0xff);
      }
      return bytes;
    }
  };

  qrcode.stringToBytes = qrcode.stringToBytesFuncs['default'];

  //---------------------------------------------------------------------
  // qrcode.createStringToBytes
  //---------------------------------------------------------------------

  /**
   * @param unicodeData base64 string of byte array.
   * [16bit Unicode],[16bit Bytes], ...
   * @param numChars
   */
  qrcode.createStringToBytes = function(unicodeData, numChars) {

    // create conversion map.

    var unicodeMap = function() {

      var bin = base64DecodeInputStream(unicodeData);
      var read = function() {
        var b = bin.read();
        if (b == -1) throw 'eof';
        return b;
      };

      var count = 0;
      var unicodeMap = {};
      while (true) {
        var b0 = bin.read();
        if (b0 == -1) break;
        var b1 = read();
        var b2 = read();
        var b3 = read();
        var k = String.fromCharCode( (b0 << 8) | b1);
        var v = (b2 << 8) | b3;
        unicodeMap[k] = v;
        count += 1;
      }
      if (count != numChars) {
        throw count + ' != ' + numChars;
      }

      return unicodeMap;
    }();

    var unknownChar = '?'.charCodeAt(0);

    return function(s) {
      var bytes = [];
      for (var i = 0; i < s.length; i += 1) {
        var c = s.charCodeAt(i);
        if (c < 128) {
          bytes.push(c);
        } else {
          var b = unicodeMap[s.charAt(i)];
          if (typeof b == 'number') {
            if ( (b & 0xff) == b) {
              // 1byte
              bytes.push(b);
            } else {
              // 2bytes
              bytes.push(b >>> 8);
              bytes.push(b & 0xff);
            }
          } else {
            bytes.push(unknownChar);
          }
        }
      }
      return bytes;
    };
  };

  //---------------------------------------------------------------------
  // QRMode
  //---------------------------------------------------------------------

  var QRMode = {
    MODE_NUMBER :    1 << 0,
    MODE_ALPHA_NUM : 1 << 1,
    MODE_8BIT_BYTE : 1 << 2,
    MODE_KANJI :     1 << 3
  };

  //---------------------------------------------------------------------
  // QRErrorCorrectionLevel
  //---------------------------------------------------------------------

  var QRErrorCorrectionLevel = {
    L : 1,
    M : 0,
    Q : 3,
    H : 2
  };

  //---------------------------------------------------------------------
  // QRMaskPattern
  //---------------------------------------------------------------------

  var QRMaskPattern = {
    PATTERN000 : 0,
    PATTERN001 : 1,
    PATTERN010 : 2,
    PATTERN011 : 3,
    PATTERN100 : 4,
    PATTERN101 : 5,
    PATTERN110 : 6,
    PATTERN111 : 7
  };

  //---------------------------------------------------------------------
  // QRUtil
  //---------------------------------------------------------------------

  var QRUtil = function() {

    var PATTERN_POSITION_TABLE = [
      [],
      [6, 18],
      [6, 22],
      [6, 26],
      [6, 30],
      [6, 34],
      [6, 22, 38],
      [6, 24, 42],
      [6, 26, 46],
      [6, 28, 50],
      [6, 30, 54],
      [6, 32, 58],
      [6, 34, 62],
      [6, 26, 46, 66],
      [6, 26, 48, 70],
      [6, 26, 50, 74],
      [6, 30, 54, 78],
      [6, 30, 56, 82],
      [6, 30, 58, 86],
      [6, 34, 62, 90],
      [6, 28, 50, 72, 94],
      [6, 26, 50, 74, 98],
      [6, 30, 54, 78, 102],
      [6, 28, 54, 80, 106],
      [6, 32, 58, 84, 110],
      [6, 30, 58, 86, 114],
      [6, 34, 62, 90, 118],
      [6, 26, 50, 74, 98, 122],
      [6, 30, 54, 78, 102, 126],
      [6, 26, 52, 78, 104, 130],
      [6, 30, 56, 82, 108, 134],
      [6, 34, 60, 86, 112, 138],
      [6, 30, 58, 86, 114, 142],
      [6, 34, 62, 90, 118, 146],
      [6, 30, 54, 78, 102, 126, 150],
      [6, 24, 50, 76, 102, 128, 154],
      [6, 28, 54, 80, 106, 132, 158],
      [6, 32, 58, 84, 110, 136, 162],
      [6, 26, 54, 82, 110, 138, 166],
      [6, 30, 58, 86, 114, 142, 170]
    ];
    var G15 = (1 << 10) | (1 << 8) | (1 << 5) | (1 << 4) | (1 << 2) | (1 << 1) | (1 << 0);
    var G18 = (1 << 12) | (1 << 11) | (1 << 10) | (1 << 9) | (1 << 8) | (1 << 5) | (1 << 2) | (1 << 0);
    var G15_MASK = (1 << 14) | (1 << 12) | (1 << 10) | (1 << 4) | (1 << 1);

    var _this = {};

    var getBCHDigit = function(data) {
      var digit = 0;
      while (data != 0) {
        digit += 1;
        data >>>= 1;
      }
      return digit;
    };

    _this.getBCHTypeInfo = function(data) {
      var d = data << 10;
      while (getBCHDigit(d) - getBCHDigit(G15) >= 0) {
        d ^= (G15 << (getBCHDigit(d) - getBCHDigit(G15) ) );
      }
      return ( (data << 10) | d) ^ G15_MASK;
    };

    _this.getBCHTypeNumber = function(data) {
      var d = data << 12;
      while (getBCHDigit(d) - getBCHDigit(G18) >= 0) {
        d ^= (G18 << (getBCHDigit(d) - getBCHDigit(G18) ) );
      }
      return (data << 12) | d;
    };

    _this.getPatternPosition = function(typeNumber) {
      return PATTERN_POSITION_TABLE[typeNumber - 1];
    };

    _this.getMaskFunction = function(maskPattern) {

      switch (maskPattern) {

      case QRMaskPattern.PATTERN000 :
        return function(i, j) { return (i + j) % 2 == 0; };
      case QRMaskPattern.PATTERN001 :
        return function(i, j) { return i % 2 == 0; };
      case QRMaskPattern.PATTERN010 :
        return function(i, j) { return j % 3 == 0; };
      case QRMaskPattern.PATTERN011 :
        return function(i, j) { return (i + j) % 3 == 0; };
      case QRMaskPattern.PATTERN100 :
        return function(i, j) { return (Math.floor(i / 2) + Math.floor(j / 3) ) % 2 == 0; };
      case QRMaskPattern.PATTERN101 :
        return function(i, j) { return (i * j) % 2 + (i * j) % 3 == 0; };
      case QRMaskPattern.PATTERN110 :
        return function(i, j) { return ( (i * j) % 2 + (i * j) % 3) % 2 == 0; };
      case QRMaskPattern.PATTERN111 :
        return function(i, j) { return ( (i * j) % 3 + (i + j) % 2) % 2 == 0; };

      default :
        throw 'bad maskPattern:' + maskPattern;
      }
    };

    _this.getErrorCorrectPolynomial = function(errorCorrectLength) {
      var a = qrPolynomial([1], 0);
      for (var i = 0; i < errorCorrectLength; i += 1) {
        a = a.multiply(qrPolynomial([1, QRMath.gexp(i)], 0) );
      }
      return a;
    };

    _this.getLengthInBits = function(mode, type) {

      if (1 <= type && type < 10) {

        // 1 - 9

        switch(mode) {
        case QRMode.MODE_NUMBER    : return 10;
        case QRMode.MODE_ALPHA_NUM : return 9;
        case QRMode.MODE_8BIT_BYTE : return 8;
        case QRMode.MODE_KANJI     : return 8;
        default :
          throw 'mode:' + mode;
        }

      } else if (type < 27) {

        // 10 - 26

        switch(mode) {
        case QRMode.MODE_NUMBER    : return 12;
        case QRMode.MODE_ALPHA_NUM : return 11;
        case QRMode.MODE_8BIT_BYTE : return 16;
        case QRMode.MODE_KANJI     : return 10;
        default :
          throw 'mode:' + mode;
        }

      } else if (type < 41) {

        // 27 - 40

        switch(mode) {
        case QRMode.MODE_NUMBER    : return 14;
        case QRMode.MODE_ALPHA_NUM : return 13;
        case QRMode.MODE_8BIT_BYTE : return 16;
        case QRMode.MODE_KANJI     : return 12;
        default :
          throw 'mode:' + mode;
        }

      } else {
        throw 'type:' + type;
      }
    };

    _this.getLostPoint = function(qrcode) {

      var moduleCount = qrcode.getModuleCount();

      var lostPoint = 0;

      // LEVEL1

      for (var row = 0; row < moduleCount; row += 1) {
        for (var col = 0; col < moduleCount; col += 1) {

          var sameCount = 0;
          var dark = qrcode.isDark(row, col);

          for (var r = -1; r <= 1; r += 1) {

            if (row + r < 0 || moduleCount <= row + r) {
              continue;
            }

            for (var c = -1; c <= 1; c += 1) {

              if (col + c < 0 || moduleCount <= col + c) {
                continue;
              }

              if (r == 0 && c == 0) {
                continue;
              }

              if (dark == qrcode.isDark(row + r, col + c) ) {
                sameCount += 1;
              }
            }
          }

          if (sameCount > 5) {
            lostPoint += (3 + sameCount - 5);
          }
        }
      };

      // LEVEL2

      for (var row = 0; row < moduleCount - 1; row += 1) {
        for (var col = 0; col < moduleCount - 1; col += 1) {
          var count = 0;
          if (qrcode.isDark(row, col) ) count += 1;
          if (qrcode.isDark(row + 1, col) ) count += 1;
          if (qrcode.isDark(row, col + 1) ) count += 1;
          if (qrcode.isDark(row + 1, col + 1) ) count += 1;
          if (count == 0 || count == 4) {
            lostPoint += 3;
          }
        }
      }

      // LEVEL3

      for (var row = 0; row < moduleCount; row += 1) {
        for (var col = 0; col < moduleCount - 6; col += 1) {
          if (qrcode.isDark(row, col)
              && !qrcode.isDark(row, col + 1)
              &&  qrcode.isDark(row, col + 2)
              &&  qrcode.isDark(row, col + 3)
              &&  qrcode.isDark(row, col + 4)
              && !qrcode.isDark(row, col + 5)
              &&  qrcode.isDark(row, col + 6) ) {
            lostPoint += 40;
          }
        }
      }

      for (var col = 0; col < moduleCount; col += 1) {
        for (var row = 0; row < moduleCount - 6; row += 1) {
          if (qrcode.isDark(row, col)
              && !qrcode.isDark(row + 1, col)
              &&  qrcode.isDark(row + 2, col)
              &&  qrcode.isDark(row + 3, col)
              &&  qrcode.isDark(row + 4, col)
              && !qrcode.isDark(row + 5, col)
              &&  qrcode.isDark(row + 6, col) ) {
            lostPoint += 40;
          }
        }
      }

      // LEVEL4

      var darkCount = 0;

      for (var col = 0; col < moduleCount; col += 1) {
        for (var row = 0; row < moduleCount; row += 1) {
          if (qrcode.isDark(row, col) ) {
            darkCount += 1;
          }
        }
      }

      var ratio = Math.abs(100 * darkCount / moduleCount / moduleCount - 50) / 5;
      lostPoint += ratio * 10;

      return lostPoint;
    };

    return _this;
  }();

  //---------------------------------------------------------------------
  // QRMath
  //---------------------------------------------------------------------

  var QRMath = function() {

    var EXP_TABLE = new Array(256);
    var LOG_TABLE = new Array(256);

    // initialize tables
    for (var i = 0; i < 8; i += 1) {
      EXP_TABLE[i] = 1 << i;
    }
    for (var i = 8; i < 256; i += 1) {
      EXP_TABLE[i] = EXP_TABLE[i - 4]
        ^ EXP_TABLE[i - 5]
        ^ EXP_TABLE[i - 6]
        ^ EXP_TABLE[i - 8];
    }
    for (var i = 0; i < 255; i += 1) {
      LOG_TABLE[EXP_TABLE[i] ] = i;
    }

    var _this = {};

    _this.glog = function(n) {

      if (n < 1) {
        throw 'glog(' + n + ')';
      }

      return LOG_TABLE[n];
    };

    _this.gexp = function(n) {

      while (n < 0) {
        n += 255;
      }

      while (n >= 256) {
        n -= 255;
      }

      return EXP_TABLE[n];
    };

    return _this;
  }();

  //---------------------------------------------------------------------
  // qrPolynomial
  //---------------------------------------------------------------------

  function qrPolynomial(num, shift) {

    if (typeof num.length == 'undefined') {
      throw num.length + '/' + shift;
    }

    var _num = function() {
      var offset = 0;
      while (offset < num.length && num[offset] == 0) {
        offset += 1;
      }
      var _num = new Array(num.length - offset + shift);
      for (var i = 0; i < num.length - offset; i += 1) {
        _num[i] = num[i + offset];
      }
      return _num;
    }();

    var _this = {};

    _this.getAt = function(index) {
      return _num[index];
    };

    _this.getLength = function() {
      return _num.length;
    };

    _this.multiply = function(e) {

      var num = new Array(_this.getLength() + e.getLength() - 1);

      for (var i = 0; i < _this.getLength(); i += 1) {
        for (var j = 0; j < e.getLength(); j += 1) {
          num[i + j] ^= QRMath.gexp(QRMath.glog(_this.getAt(i) ) + QRMath.glog(e.getAt(j) ) );
        }
      }

      return qrPolynomial(num, 0);
    };

    _this.mod = function(e) {

      if (_this.getLength() - e.getLength() < 0) {
        return _this;
      }

      var ratio = QRMath.glog(_this.getAt(0) ) - QRMath.glog(e.getAt(0) );

      var num = new Array(_this.getLength() );
      for (var i = 0; i < _this.getLength(); i += 1) {
        num[i] = _this.getAt(i);
      }

      for (var i = 0; i < e.getLength(); i += 1) {
        num[i] ^= QRMath.gexp(QRMath.glog(e.getAt(i) ) + ratio);
      }

      // recursive call
      return qrPolynomial(num, 0).mod(e);
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // QRRSBlock
  //---------------------------------------------------------------------

  var QRRSBlock = function() {

    var RS_BLOCK_TABLE = [

      // L
      // M
      // Q
      // H

      // 1
      [1, 26, 19],
      [1, 26, 16],
      [1, 26, 13],
      [1, 26, 9],

      // 2
      [1, 44, 34],
      [1, 44, 28],
      [1, 44, 22],
      [1, 44, 16],

      // 3
      [1, 70, 55],
      [1, 70, 44],
      [2, 35, 17],
      [2, 35, 13],

      // 4
      [1, 100, 80],
      [2, 50, 32],
      [2, 50, 24],
      [4, 25, 9],

      // 5
      [1, 134, 108],
      [2, 67, 43],
      [2, 33, 15, 2, 34, 16],
      [2, 33, 11, 2, 34, 12],

      // 6
      [2, 86, 68],
      [4, 43, 27],
      [4, 43, 19],
      [4, 43, 15],

      // 7
      [2, 98, 78],
      [4, 49, 31],
      [2, 32, 14, 4, 33, 15],
      [4, 39, 13, 1, 40, 14],

      // 8
      [2, 121, 97],
      [2, 60, 38, 2, 61, 39],
      [4, 40, 18, 2, 41, 19],
      [4, 40, 14, 2, 41, 15],

      // 9
      [2, 146, 116],
      [3, 58, 36, 2, 59, 37],
      [4, 36, 16, 4, 37, 17],
      [4, 36, 12, 4, 37, 13],

      // 10
      [2, 86, 68, 2, 87, 69],
      [4, 69, 43, 1, 70, 44],
      [6, 43, 19, 2, 44, 20],
      [6, 43, 15, 2, 44, 16],

      // 11
      [4, 101, 81],
      [1, 80, 50, 4, 81, 51],
      [4, 50, 22, 4, 51, 23],
      [3, 36, 12, 8, 37, 13],

      // 12
      [2, 116, 92, 2, 117, 93],
      [6, 58, 36, 2, 59, 37],
      [4, 46, 20, 6, 47, 21],
      [7, 42, 14, 4, 43, 15],

      // 13
      [4, 133, 107],
      [8, 59, 37, 1, 60, 38],
      [8, 44, 20, 4, 45, 21],
      [12, 33, 11, 4, 34, 12],

      // 14
      [3, 145, 115, 1, 146, 116],
      [4, 64, 40, 5, 65, 41],
      [11, 36, 16, 5, 37, 17],
      [11, 36, 12, 5, 37, 13],

      // 15
      [5, 109, 87, 1, 110, 88],
      [5, 65, 41, 5, 66, 42],
      [5, 54, 24, 7, 55, 25],
      [11, 36, 12, 7, 37, 13],

      // 16
      [5, 122, 98, 1, 123, 99],
      [7, 73, 45, 3, 74, 46],
      [15, 43, 19, 2, 44, 20],
      [3, 45, 15, 13, 46, 16],

      // 17
      [1, 135, 107, 5, 136, 108],
      [10, 74, 46, 1, 75, 47],
      [1, 50, 22, 15, 51, 23],
      [2, 42, 14, 17, 43, 15],

      // 18
      [5, 150, 120, 1, 151, 121],
      [9, 69, 43, 4, 70, 44],
      [17, 50, 22, 1, 51, 23],
      [2, 42, 14, 19, 43, 15],

      // 19
      [3, 141, 113, 4, 142, 114],
      [3, 70, 44, 11, 71, 45],
      [17, 47, 21, 4, 48, 22],
      [9, 39, 13, 16, 40, 14],

      // 20
      [3, 135, 107, 5, 136, 108],
      [3, 67, 41, 13, 68, 42],
      [15, 54, 24, 5, 55, 25],
      [15, 43, 15, 10, 44, 16],

      // 21
      [4, 144, 116, 4, 145, 117],
      [17, 68, 42],
      [17, 50, 22, 6, 51, 23],
      [19, 46, 16, 6, 47, 17],

      // 22
      [2, 139, 111, 7, 140, 112],
      [17, 74, 46],
      [7, 54, 24, 16, 55, 25],
      [34, 37, 13],

      // 23
      [4, 151, 121, 5, 152, 122],
      [4, 75, 47, 14, 76, 48],
      [11, 54, 24, 14, 55, 25],
      [16, 45, 15, 14, 46, 16],

      // 24
      [6, 147, 117, 4, 148, 118],
      [6, 73, 45, 14, 74, 46],
      [11, 54, 24, 16, 55, 25],
      [30, 46, 16, 2, 47, 17],

      // 25
      [8, 132, 106, 4, 133, 107],
      [8, 75, 47, 13, 76, 48],
      [7, 54, 24, 22, 55, 25],
      [22, 45, 15, 13, 46, 16],

      // 26
      [10, 142, 114, 2, 143, 115],
      [19, 74, 46, 4, 75, 47],
      [28, 50, 22, 6, 51, 23],
      [33, 46, 16, 4, 47, 17],

      // 27
      [8, 152, 122, 4, 153, 123],
      [22, 73, 45, 3, 74, 46],
      [8, 53, 23, 26, 54, 24],
      [12, 45, 15, 28, 46, 16],

      // 28
      [3, 147, 117, 10, 148, 118],
      [3, 73, 45, 23, 74, 46],
      [4, 54, 24, 31, 55, 25],
      [11, 45, 15, 31, 46, 16],

      // 29
      [7, 146, 116, 7, 147, 117],
      [21, 73, 45, 7, 74, 46],
      [1, 53, 23, 37, 54, 24],
      [19, 45, 15, 26, 46, 16],

      // 30
      [5, 145, 115, 10, 146, 116],
      [19, 75, 47, 10, 76, 48],
      [15, 54, 24, 25, 55, 25],
      [23, 45, 15, 25, 46, 16],

      // 31
      [13, 145, 115, 3, 146, 116],
      [2, 74, 46, 29, 75, 47],
      [42, 54, 24, 1, 55, 25],
      [23, 45, 15, 28, 46, 16],

      // 32
      [17, 145, 115],
      [10, 74, 46, 23, 75, 47],
      [10, 54, 24, 35, 55, 25],
      [19, 45, 15, 35, 46, 16],

      // 33
      [17, 145, 115, 1, 146, 116],
      [14, 74, 46, 21, 75, 47],
      [29, 54, 24, 19, 55, 25],
      [11, 45, 15, 46, 46, 16],

      // 34
      [13, 145, 115, 6, 146, 116],
      [14, 74, 46, 23, 75, 47],
      [44, 54, 24, 7, 55, 25],
      [59, 46, 16, 1, 47, 17],

      // 35
      [12, 151, 121, 7, 152, 122],
      [12, 75, 47, 26, 76, 48],
      [39, 54, 24, 14, 55, 25],
      [22, 45, 15, 41, 46, 16],

      // 36
      [6, 151, 121, 14, 152, 122],
      [6, 75, 47, 34, 76, 48],
      [46, 54, 24, 10, 55, 25],
      [2, 45, 15, 64, 46, 16],

      // 37
      [17, 152, 122, 4, 153, 123],
      [29, 74, 46, 14, 75, 47],
      [49, 54, 24, 10, 55, 25],
      [24, 45, 15, 46, 46, 16],

      // 38
      [4, 152, 122, 18, 153, 123],
      [13, 74, 46, 32, 75, 47],
      [48, 54, 24, 14, 55, 25],
      [42, 45, 15, 32, 46, 16],

      // 39
      [20, 147, 117, 4, 148, 118],
      [40, 75, 47, 7, 76, 48],
      [43, 54, 24, 22, 55, 25],
      [10, 45, 15, 67, 46, 16],

      // 40
      [19, 148, 118, 6, 149, 119],
      [18, 75, 47, 31, 76, 48],
      [34, 54, 24, 34, 55, 25],
      [20, 45, 15, 61, 46, 16]
    ];

    var qrRSBlock = function(totalCount, dataCount) {
      var _this = {};
      _this.totalCount = totalCount;
      _this.dataCount = dataCount;
      return _this;
    };

    var _this = {};

    var getRsBlockTable = function(typeNumber, errorCorrectionLevel) {

      switch(errorCorrectionLevel) {
      case QRErrorCorrectionLevel.L :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 0];
      case QRErrorCorrectionLevel.M :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 1];
      case QRErrorCorrectionLevel.Q :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 2];
      case QRErrorCorrectionLevel.H :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 3];
      default :
        return undefined;
      }
    };

    _this.getRSBlocks = function(typeNumber, errorCorrectionLevel) {

      var rsBlock = getRsBlockTable(typeNumber, errorCorrectionLevel);

      if (typeof rsBlock == 'undefined') {
        throw 'bad rs block @ typeNumber:' + typeNumber +
            '/errorCorrectionLevel:' + errorCorrectionLevel;
      }

      var length = rsBlock.length / 3;

      var list = [];

      for (var i = 0; i < length; i += 1) {

        var count = rsBlock[i * 3 + 0];
        var totalCount = rsBlock[i * 3 + 1];
        var dataCount = rsBlock[i * 3 + 2];

        for (var j = 0; j < count; j += 1) {
          list.push(qrRSBlock(totalCount, dataCount) );
        }
      }

      return list;
    };

    return _this;
  }();

  //---------------------------------------------------------------------
  // qrBitBuffer
  //---------------------------------------------------------------------

  var qrBitBuffer = function() {

    var _buffer = [];
    var _length = 0;

    var _this = {};

    _this.getBuffer = function() {
      return _buffer;
    };

    _this.getAt = function(index) {
      var bufIndex = Math.floor(index / 8);
      return ( (_buffer[bufIndex] >>> (7 - index % 8) ) & 1) == 1;
    };

    _this.put = function(num, length) {
      for (var i = 0; i < length; i += 1) {
        _this.putBit( ( (num >>> (length - i - 1) ) & 1) == 1);
      }
    };

    _this.getLengthInBits = function() {
      return _length;
    };

    _this.putBit = function(bit) {

      var bufIndex = Math.floor(_length / 8);
      if (_buffer.length <= bufIndex) {
        _buffer.push(0);
      }

      if (bit) {
        _buffer[bufIndex] |= (0x80 >>> (_length % 8) );
      }

      _length += 1;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qrNumber
  //---------------------------------------------------------------------

  var qrNumber = function(data) {

    var _mode = QRMode.MODE_NUMBER;
    var _data = data;

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return _data.length;
    };

    _this.write = function(buffer) {

      var data = _data;

      var i = 0;

      while (i + 2 < data.length) {
        buffer.put(strToNum(data.substring(i, i + 3) ), 10);
        i += 3;
      }

      if (i < data.length) {
        if (data.length - i == 1) {
          buffer.put(strToNum(data.substring(i, i + 1) ), 4);
        } else if (data.length - i == 2) {
          buffer.put(strToNum(data.substring(i, i + 2) ), 7);
        }
      }
    };

    var strToNum = function(s) {
      var num = 0;
      for (var i = 0; i < s.length; i += 1) {
        num = num * 10 + chatToNum(s.charAt(i) );
      }
      return num;
    };

    var chatToNum = function(c) {
      if ('0' <= c && c <= '9') {
        return c.charCodeAt(0) - '0'.charCodeAt(0);
      }
      throw 'illegal char :' + c;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qrAlphaNum
  //---------------------------------------------------------------------

  var qrAlphaNum = function(data) {

    var _mode = QRMode.MODE_ALPHA_NUM;
    var _data = data;

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return _data.length;
    };

    _this.write = function(buffer) {

      var s = _data;

      var i = 0;

      while (i + 1 < s.length) {
        buffer.put(
          getCode(s.charAt(i) ) * 45 +
          getCode(s.charAt(i + 1) ), 11);
        i += 2;
      }

      if (i < s.length) {
        buffer.put(getCode(s.charAt(i) ), 6);
      }
    };

    var getCode = function(c) {

      if ('0' <= c && c <= '9') {
        return c.charCodeAt(0) - '0'.charCodeAt(0);
      } else if ('A' <= c && c <= 'Z') {
        return c.charCodeAt(0) - 'A'.charCodeAt(0) + 10;
      } else {
        switch (c) {
        case ' ' : return 36;
        case '$' : return 37;
        case '%' : return 38;
        case '*' : return 39;
        case '+' : return 40;
        case '-' : return 41;
        case '.' : return 42;
        case '/' : return 43;
        case ':' : return 44;
        default :
          throw 'illegal char :' + c;
        }
      }
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qr8BitByte
  //---------------------------------------------------------------------

  var qr8BitByte = function(data) {

    var _mode = QRMode.MODE_8BIT_BYTE;
    var _data = data;
    var _bytes = qrcode.stringToBytes(data);

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return _bytes.length;
    };

    _this.write = function(buffer) {
      for (var i = 0; i < _bytes.length; i += 1) {
        buffer.put(_bytes[i], 8);
      }
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qrKanji
  //---------------------------------------------------------------------

  var qrKanji = function(data) {

    var _mode = QRMode.MODE_KANJI;
    var _data = data;

    var stringToBytes = qrcode.stringToBytesFuncs['SJIS'];
    if (!stringToBytes) {
      throw 'sjis not supported.';
    }
    !function(c, code) {
      // self test for sjis support.
      var test = stringToBytes(c);
      if (test.length != 2 || ( (test[0] << 8) | test[1]) != code) {
        throw 'sjis not supported.';
      }
    }('\u53cb', 0x9746);

    var _bytes = stringToBytes(data);

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return ~~(_bytes.length / 2);
    };

    _this.write = function(buffer) {

      var data = _bytes;

      var i = 0;

      while (i + 1 < data.length) {

        var c = ( (0xff & data[i]) << 8) | (0xff & data[i + 1]);

        if (0x8140 <= c && c <= 0x9FFC) {
          c -= 0x8140;
        } else if (0xE040 <= c && c <= 0xEBBF) {
          c -= 0xC140;
        } else {
          throw 'illegal char at ' + (i + 1) + '/' + c;
        }

        c = ( (c >>> 8) & 0xff) * 0xC0 + (c & 0xff);

        buffer.put(c, 13);

        i += 2;
      }

      if (i < data.length) {
        throw 'illegal char at ' + (i + 1);
      }
    };

    return _this;
  };

  //=====================================================================
  // GIF Support etc.
  //

  //---------------------------------------------------------------------
  // byteArrayOutputStream
  //---------------------------------------------------------------------

  var byteArrayOutputStream = function() {

    var _bytes = [];

    var _this = {};

    _this.writeByte = function(b) {
      _bytes.push(b & 0xff);
    };

    _this.writeShort = function(i) {
      _this.writeByte(i);
      _this.writeByte(i >>> 8);
    };

    _this.writeBytes = function(b, off, len) {
      off = off || 0;
      len = len || b.length;
      for (var i = 0; i < len; i += 1) {
        _this.writeByte(b[i + off]);
      }
    };

    _this.writeString = function(s) {
      for (var i = 0; i < s.length; i += 1) {
        _this.writeByte(s.charCodeAt(i) );
      }
    };

    _this.toByteArray = function() {
      return _bytes;
    };

    _this.toString = function() {
      var s = '';
      s += '[';
      for (var i = 0; i < _bytes.length; i += 1) {
        if (i > 0) {
          s += ',';
        }
        s += _bytes[i];
      }
      s += ']';
      return s;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // base64EncodeOutputStream
  //---------------------------------------------------------------------

  var base64EncodeOutputStream = function() {

    var _buffer = 0;
    var _buflen = 0;
    var _length = 0;
    var _base64 = '';

    var _this = {};

    var writeEncoded = function(b) {
      _base64 += String.fromCharCode(encode(b & 0x3f) );
    };

    var encode = function(n) {
      if (n < 0) {
        // error.
      } else if (n < 26) {
        return 0x41 + n;
      } else if (n < 52) {
        return 0x61 + (n - 26);
      } else if (n < 62) {
        return 0x30 + (n - 52);
      } else if (n == 62) {
        return 0x2b;
      } else if (n == 63) {
        return 0x2f;
      }
      throw 'n:' + n;
    };

    _this.writeByte = function(n) {

      _buffer = (_buffer << 8) | (n & 0xff);
      _buflen += 8;
      _length += 1;

      while (_buflen >= 6) {
        writeEncoded(_buffer >>> (_buflen - 6) );
        _buflen -= 6;
      }
    };

    _this.flush = function() {

      if (_buflen > 0) {
        writeEncoded(_buffer << (6 - _buflen) );
        _buffer = 0;
        _buflen = 0;
      }

      if (_length % 3 != 0) {
        // padding
        var padlen = 3 - _length % 3;
        for (var i = 0; i < padlen; i += 1) {
          _base64 += '=';
        }
      }
    };

    _this.toString = function() {
      return _base64;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // base64DecodeInputStream
  //---------------------------------------------------------------------

  var base64DecodeInputStream = function(str) {

    var _str = str;
    var _pos = 0;
    var _buffer = 0;
    var _buflen = 0;

    var _this = {};

    _this.read = function() {

      while (_buflen < 8) {

        if (_pos >= _str.length) {
          if (_buflen == 0) {
            return -1;
          }
          throw 'unexpected end of file./' + _buflen;
        }

        var c = _str.charAt(_pos);
        _pos += 1;

        if (c == '=') {
          _buflen = 0;
          return -1;
        } else if (c.match(/^\s$/) ) {
          // ignore if whitespace.
          continue;
        }

        _buffer = (_buffer << 6) | decode(c.charCodeAt(0) );
        _buflen += 6;
      }

      var n = (_buffer >>> (_buflen - 8) ) & 0xff;
      _buflen -= 8;
      return n;
    };

    var decode = function(c) {
      if (0x41 <= c && c <= 0x5a) {
        return c - 0x41;
      } else if (0x61 <= c && c <= 0x7a) {
        return c - 0x61 + 26;
      } else if (0x30 <= c && c <= 0x39) {
        return c - 0x30 + 52;
      } else if (c == 0x2b) {
        return 62;
      } else if (c == 0x2f) {
        return 63;
      } else {
        throw 'c:' + c;
      }
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // gifImage (B/W)
  //---------------------------------------------------------------------

  var gifImage = function(width, height) {

    var _width = width;
    var _height = height;
    var _data = new Array(width * height);

    var _this = {};

    _this.setPixel = function(x, y, pixel) {
      _data[y * _width + x] = pixel;
    };

    _this.write = function(out) {

      //---------------------------------
      // GIF Signature

      out.writeString('GIF87a');

      //---------------------------------
      // Screen Descriptor

      out.writeShort(_width);
      out.writeShort(_height);

      out.writeByte(0x80); // 2bit
      out.writeByte(0);
      out.writeByte(0);

      //---------------------------------
      // Global Color Map

      // black
      out.writeByte(0x00);
      out.writeByte(0x00);
      out.writeByte(0x00);

      // white
      out.writeByte(0xff);
      out.writeByte(0xff);
      out.writeByte(0xff);

      //---------------------------------
      // Image Descriptor

      out.writeString(',');
      out.writeShort(0);
      out.writeShort(0);
      out.writeShort(_width);
      out.writeShort(_height);
      out.writeByte(0);

      //---------------------------------
      // Local Color Map

      //---------------------------------
      // Raster Data

      var lzwMinCodeSize = 2;
      var raster = getLZWRaster(lzwMinCodeSize);

      out.writeByte(lzwMinCodeSize);

      var offset = 0;

      while (raster.length - offset > 255) {
        out.writeByte(255);
        out.writeBytes(raster, offset, 255);
        offset += 255;
      }

      out.writeByte(raster.length - offset);
      out.writeBytes(raster, offset, raster.length - offset);
      out.writeByte(0x00);

      //---------------------------------
      // GIF Terminator
      out.writeString(';');
    };

    var bitOutputStream = function(out) {

      var _out = out;
      var _bitLength = 0;
      var _bitBuffer = 0;

      var _this = {};

      _this.write = function(data, length) {

        if ( (data >>> length) != 0) {
          throw 'length over';
        }

        while (_bitLength + length >= 8) {
          _out.writeByte(0xff & ( (data << _bitLength) | _bitBuffer) );
          length -= (8 - _bitLength);
          data >>>= (8 - _bitLength);
          _bitBuffer = 0;
          _bitLength = 0;
        }

        _bitBuffer = (data << _bitLength) | _bitBuffer;
        _bitLength = _bitLength + length;
      };

      _this.flush = function() {
        if (_bitLength > 0) {
          _out.writeByte(_bitBuffer);
        }
      };

      return _this;
    };

    var getLZWRaster = function(lzwMinCodeSize) {

      var clearCode = 1 << lzwMinCodeSize;
      var endCode = (1 << lzwMinCodeSize) + 1;
      var bitLength = lzwMinCodeSize + 1;

      // Setup LZWTable
      var table = lzwTable();

      for (var i = 0; i < clearCode; i += 1) {
        table.add(String.fromCharCode(i) );
      }
      table.add(String.fromCharCode(clearCode) );
      table.add(String.fromCharCode(endCode) );

      var byteOut = byteArrayOutputStream();
      var bitOut = bitOutputStream(byteOut);

      // clear code
      bitOut.write(clearCode, bitLength);

      var dataIndex = 0;

      var s = String.fromCharCode(_data[dataIndex]);
      dataIndex += 1;

      while (dataIndex < _data.length) {

        var c = String.fromCharCode(_data[dataIndex]);
        dataIndex += 1;

        if (table.contains(s + c) ) {

          s = s + c;

        } else {

          bitOut.write(table.indexOf(s), bitLength);

          if (table.size() < 0xfff) {

            if (table.size() == (1 << bitLength) ) {
              bitLength += 1;
            }

            table.add(s + c);
          }

          s = c;
        }
      }

      bitOut.write(table.indexOf(s), bitLength);

      // end code
      bitOut.write(endCode, bitLength);

      bitOut.flush();

      return byteOut.toByteArray();
    };

    var lzwTable = function() {

      var _map = {};
      var _size = 0;

      var _this = {};

      _this.add = function(key) {
        if (_this.contains(key) ) {
          throw 'dup key:' + key;
        }
        _map[key] = _size;
        _size += 1;
      };

      _this.size = function() {
        return _size;
      };

      _this.indexOf = function(key) {
        return _map[key];
      };

      _this.contains = function(key) {
        return typeof _map[key] != 'undefined';
      };

      return _this;
    };

    return _this;
  };

  var createDataURL = function(width, height, getPixel) {
    var gif = gifImage(width, height);
    for (var y = 0; y < height; y += 1) {
      for (var x = 0; x < width; x += 1) {
        gif.setPixel(x, y, getPixel(x, y) );
      }
    }

    var b = byteArrayOutputStream();
    gif.write(b);

    var base64 = base64EncodeOutputStream();
    var bytes = b.toByteArray();
    for (var i = 0; i < bytes.length; i += 1) {
      base64.writeByte(bytes[i]);
    }
    base64.flush();

    return 'data:image/gif;base64,' + base64;
  };

  //---------------------------------------------------------------------
  // returns qrcode function.

  return qrcode;
}();

// multibyte support
!function() {

  qrcode.stringToBytesFuncs['UTF-8'] = function(s) {
    // http://stackoverflow.com/questions/18729405/how-to-convert-utf8-string-to-byte-array
    function toUTF8Array(str) {
      var utf8 = [];
      for (var i=0; i < str.length; i++) {
        var charcode = str.charCodeAt(i);
        if (charcode < 0x80) utf8.push(charcode);
        else if (charcode < 0x800) {
          utf8.push(0xc0 | (charcode >> 6),
              0x80 | (charcode & 0x3f));
        }
        else if (charcode < 0xd800 || charcode >= 0xe000) {
          utf8.push(0xe0 | (charcode >> 12),
              0x80 | ((charcode>>6) & 0x3f),
              0x80 | (charcode & 0x3f));
        }
        // surrogate pair
        else {
          i++;
          // UTF-16 encodes 0x10000-0x10FFFF by
          // subtracting 0x10000 and splitting the
          // 20 bits of 0x0-0xFFFFF into two halves
          charcode = 0x10000 + (((charcode & 0x3ff)<<10)
            | (str.charCodeAt(i) & 0x3ff));
          utf8.push(0xf0 | (charcode >>18),
              0x80 | ((charcode>>12) & 0x3f),
              0x80 | ((charcode>>6) & 0x3f),
              0x80 | (charcode & 0x3f));
        }
      }
      return utf8;
    }
    return toUTF8Array(s);
  };

}();

(function (factory) {
  if (typeof define === 'function' && define.amd) {
      define([], factory);
  } else if (typeof exports === 'object') {
      module.exports = factory();
  }
}(function () {
    return qrcode;
}));


/* YorozuyaPanel: client-side QR rendering (no server-side dependency) */
function renderQr() {
  var els = document.querySelectorAll('[data-qr]');
  for (var i = 0; i < els.length; i++) {
    var el = els[i];
    if (el.getAttribute('data-qr-done')) continue;
    var text = el.getAttribute('data-qr');
    if (!text) continue;
    try {
      var qr = qrcode(0, 'M');
      qr.addData(text);
      qr.make();
      el.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
      el.setAttribute('data-qr-done', '1');
    } catch (e) {}
  }
}
function renderQrSet(id, text) {
  var el = document.getElementById(id);
  if (el && text) { el.setAttribute('data-qr', text); }
  renderQr();
}
</script>
</head>
<body>
<div class="wrap">
  <div class="topbar wood">
    <div class="logo"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="milk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff7ec"/><stop offset="1" stop-color="#ffd9e3"/></linearGradient><linearGradient id="wood" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9c6a3c"/><stop offset="1" stop-color="#6f4525"/></linearGradient><linearGradient id="berry" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff7b9c"/><stop offset="1" stop-color="#e63946"/></linearGradient></defs><path d="M78 96 L178 96 L196 226 Q197 236 187 236 L69 236 Q59 236 60 226 Z" fill="url(#milk)" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M78 96 L108 52 L188 52 L178 96 Z" fill="#ffc7d6" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M108 52 L128 96" stroke="#3d2712" stroke-width="5" stroke-linecap="round"/><rect x="158" y="14" width="16" height="62" rx="8" fill="#4aa3ff" stroke="#3d2712" stroke-width="6" transform="rotate(18 166 45)"/><path d="M128 130 C108 130 96 144 98 160 C100 182 114 200 128 200 C142 200 156 182 158 160 C160 144 148 130 128 130 Z" fill="url(#berry)" stroke="#7a1020" stroke-width="6"/><path d="M112 132 L118 118 L126 128 L134 116 L140 128 L148 120 L144 132 Z" fill="#2ec4b6" stroke="#0d5c55" stroke-width="5" stroke-linejoin="round"/><circle cx="118" cy="158" r="3.5" fill="#ffe9b0"/><circle cx="132" cy="150" r="3.5" fill="#ffe9b0"/><circle cx="140" cy="166" r="3.5" fill="#ffe9b0"/><circle cx="124" cy="176" r="3.5" fill="#ffe9b0"/><rect x="52" y="196" width="152" height="34" rx="8" fill="url(#wood)" stroke="#3d2712" stroke-width="6"/><text x="128" y="221" text-anchor="middle" font-family="serif" font-weight="900" font-size="20" fill="#f4d35e" letter-spacing="4">万事屋</text></svg></div>
    <div>
      <h1>یوروزویا پنل <span class="kanji">万事屋</span></h1>
      <div class="sub">مغازه همه‌کاره گینتاما — نسخه Python 🐍</div>
    </div>
    <div class="spacer"></div>
    <div class="clock" id="clock">--:--</div>
    <button class="btn small danger" onclick="logout()">🚪 خروج</button>
  </div>
  <div class="warn-bar" id="pw-warn" style="display:none">⚠️ رمز پیش‌فرض فعاله! حتماً متغیر <span class="mono">ADMIN_PASSWORD</span> رو عوض کن.</div>
  <div class="fun"><span class="face">🍓</span><span id="fun-line">در حال آماده‌سازی شیر توت‌فرنگی…</span></div>
  <div class="tabs">
    <button class="tab active" data-tab="dash" onclick="showTab('dash')">📊 داشبورد</button>
    <button class="tab" data-tab="users" onclick="showTab('users')">👥 کاربران</button>
    <button class="tab" data-tab="nodes" onclick="showTab('nodes')">🛰 نودها</button>
    <button class="tab" data-tab="lb" onclick="showTab('lb')">⚖️ لودبالانسر</button>
    <button class="tab" data-tab="tools" onclick="showTab('tools')">🛠 ابزار</button>
    <button class="tab" data-tab="settings" onclick="showTab('settings')">⚙️ تنظیمات</button>
  </div>
  <div class="tabpane active" id="pane-dash">
    <div class="grid c2">
      <div class="card" style="text-align:center">
        <h3>🌍 آی‌پی خروجی کانفیگ‌ها</h3>
        <div class="flag-big" id="out-flag">🏳️</div>
        <div class="out-ip" id="out-ip">…</div>
        <div style="margin:8px 0">
          <span class="badge gold" id="out-country">…</span>
          <span class="badge info" id="out-city" style="display:none"></span>
        </div>
        <div class="muted" style="font-size:.82rem" id="out-isp"></div>
        <div style="margin-top:12px"><button class="btn small blue" onclick="refreshOutbound(this)">🔄 رفرش آی‌پی</button></div>
      </div>
      <div class="grid c2">
        <div class="card stat"><div class="ico">👥</div><div class="num" id="c-users">–</div><div class="cap">کل کاربران</div></div>
        <div class="card stat"><div class="ico">✅</div><div class="num" id="c-active">–</div><div class="cap">کاربران فعال</div></div>
        <div class="card stat"><div class="ico">🛰</div><div class="num" id="c-nodes">–</div><div class="cap">نودها</div></div>
        <div class="card stat"><div class="ico">💚</div><div class="num" id="c-online">–</div><div class="cap">نودهای آنلاین</div></div>
      </div>
    </div>
    <div class="card" style="margin-top:16px">
      <h3>📜 تابلوی مغازه</h3>
      <div id="dash-nodes"><span class="muted">در حال بارگذاری…</span></div>
    </div>
  </div>
  <div class="tabpane" id="pane-users">
    <div class="card">
      <div class="toolbar">
        <button class="btn" onclick="openUserModal()">➕ کاربر جدید</button>
        <div class="spacer"></div>
        <input class="search" id="user-search" placeholder="🔍 جستجو…" oninput="renderUsers()">
      </div>
      <div class="table-wrap">
        <table>
          <thead><tr><th>کاربر</th><th>وضعیت</th><th>ترافیک</th><th>انقضا</th><th>عملیات</th></tr></thead>
          <tbody id="users-body"><tr><td colspan="5" class="muted">در حال بارگذاری…</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>
  <div class="tabpane" id="pane-nodes">
    <div class="toolbar">
      <button class="btn" onclick="openNodeModal()">➕ نود جدید</button>
      <button class="btn blue" onclick="checkAllNodes(this)">📡 چک همه نودها</button>
    </div>
    <div class="grid c2" id="nodes-grid"><div class="card muted">در حال بارگذاری…</div></div>
  </div>
  <div class="tabpane" id="pane-lb">
    <div class="grid c2">
      <div class="card">
        <h3>⚖️ استراتژی لودبالانسر</h3>
        <div class="muted" style="font-size:.85rem;margin-bottom:10px">هر بار که یه کانفیگ درخواست می‌شه، یه نود سالم انتخاب می‌شه و بار بین نودها پخش می‌شه.</div>
        <label class="lbl">استراتژی</label>
        <select id="lb-strategy">
          <option value="round-robin">🌀 گردشی (Round-Robin)</option>
          <option value="least-latency">⚡ کمترین پینگ (Least-Latency)</option>
          <option value="random">🎲 تصادفی (Random)</option>
        </select>
        <div style="margin-top:12px"><button class="btn green" onclick="saveLb()">💾 ذخیره استراتژی</button></div>
      </div>
      <div class="card">
        <h3>🔗 لینک ساب لودبالانسر</h3>
        <div class="muted" style="font-size:.85rem;margin-bottom:10px">از بین لینک‌های هر کاربر (دکمه 🔗 لینک‌ها) یه لینک ⚖️ LB هم هست — هر بار یه نود سالم می‌ده. عالیه برای ربات‌های دیسکورد 🤖</div>
        <div class="kv"><span>استراتژی فعلی</span><b id="lb-cur">–</b></div>
        <div class="kv"><span>نودهای کاندید</span><b id="lb-cands">–</b></div>
        <div class="kv"><span>کل انتخاب‌ها</span><b id="lb-total">–</b></div>
      </div>
    </div>
    <div class="card" style="margin-top:16px">
      <h3>📊 آمار انتخاب</h3>
      <div class="table-wrap">
        <table>
          <thead><tr><th>نود</th><th>وضعیت</th><th>پینگ</th><th>تعداد انتخاب</th></tr></thead>
          <tbody id="lb-body"><tr><td colspan="4" class="muted">در حال بارگذاری…</td></tr></tbody>
        </table>
      </div>
    </div>
  </div>
  <div class="tabpane" id="pane-tools">
    <div class="card" style="max-width:640px">
      <h3>🔎 آی‌پی / هاست مال کدوم کشوره؟</h3>
      <div class="muted" style="font-size:.85rem;margin-bottom:12px">یه آی‌پی یا دامنه بده تا بگم کجای دنیاست 🌍</div>
      <div style="display:flex;gap:8px">
        <input id="lookup-q" placeholder="مثال: 8.8.8.8 یا google.com" style="direction:ltr;text-align:left" onkeydown="if(event.key==='Enter')doLookup()">
        <button class="btn blue" onclick="doLookup()" style="flex:none">🔍 بگرد</button>
      </div>
      <div id="lookup-res" style="margin-top:14px"></div>
    </div>
  </div>
  <div class="tabpane" id="pane-settings">
    <div class="grid c2">
      <div class="card">
        <h3>🔗 دامنه عمومی ساب</h3>
        <div class="muted" style="font-size:.85rem;margin-bottom:10px">اگه خالی باشه، خودکار از آدرس همین صفحه ساخته می‌شه.</div>
        <input id="set-base" placeholder="https://yorozuya-panel.onrender.com" style="direction:ltr;text-align:left">
        <div style="margin-top:12px"><button class="btn green" onclick="saveSettings()">💾 ذخیره</button></div>
      </div>
      <div class="card">
        <h3>💾 بکاپ / ریستور</h3>
        <div class="muted" style="font-size:.85rem;margin-bottom:12px">کل یوزرها و نودها یه فایل JSON می‌شن.</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap">
          <button class="btn blue" onclick="downloadBackup()">⬇️ دانلود بکاپ</button>
          <label class="btn gold" style="cursor:pointer">⬆️ ریستور<input type="file" id="restore-file" accept=".json" style="display:none" onchange="doRestore(this)"></label>
        </div>
      </div>
    </div>
  </div>
  <footer class="foot">🍓 ساخته شده با شیر توت‌فرنگی و تنبلی گینتاما <span class="kanji">万事屋</span> · <span id="ver"></span></footer>
</div>
<div class="modal-bg" id="modal-bg" onclick="if(event.target===this)closeModal()">
  <div class="modal card" id="modal-box" style="margin:auto 0">
    <div class="mhead"><h3 id="modal-title"></h3><button class="x" onclick="closeModal()">✕</button></div>
    <div id="modal-body"></div>
  </div>
</div>
<div id="toasts"></div>
<script>
var API_BASE = '/';
var USERS = [], NODES = [], SETTINGS = { sub_base_url: '' }, LINKS_CACHE = [];
function esc(s) {
  return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function toast(msg, err) {
  var t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.getElementById('toasts').appendChild(t);
  setTimeout(function () { t.remove(); }, 3400);
}
function api(method, path, body) {
  var opts = { method: method, headers: {} };
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  return fetch(API_BASE + path, opts).then(function (r) {
    if (r.status === 401) { location.href = API_BASE + 'login'; throw new Error('login'); }
    return r.json().catch(function () { return {}; }).then(function (d) {
      if (!r.ok) throw new Error(d.detail || ('خطا ' + r.status));
      return d;
    });
  });
}
function copyText(txt, msg) {
  function done() { toast(msg || '📋 کپی شد!'); }
  if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(txt).then(done).catch(function () { fallback(); }); }
  else fallback();
  function fallback() {
    var ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    ta.remove(); done();
  }
}
function copyLink(i) { if (LINKS_CACHE[i]) copyText(LINKS_CACHE[i].link, '⚔️ کانفیگ ' + LINKS_CACHE[i].node + ' کپی شد!'); }
function faDate(iso) {
  if (!iso) return '♾️ نامحدود';
  try {
    var d = new Date(iso);
    var left = Math.ceil((d - Date.now()) / 86400000);
    return d.toLocaleDateString('fa-IR') + (left >= 0 ? ' (' + left + ' روز ⏳)' : ' (تموم!)');
  } catch (e) { return iso; }
}
function isoToLocal(iso) {
  if (!iso) return '';
  try {
    var d = new Date(iso);
    function p(n) { return String(n).padStart(2, '0'); }
    return d.getFullYear() + '-' + p(d.getMonth() + 1) + '-' + p(d.getDate()) + 'T' + p(d.getHours()) + ':' + p(d.getMinutes());
  } catch (e) { return ''; }
}
function localToIso(v) {
  if (!v) return '';
  try { return new Date(v).toISOString(); } catch (e) { return ''; }
}
setInterval(function () {
  document.getElementById('clock').textContent = new Date().toLocaleTimeString('fa-IR', { hour: '2-digit', minute: '2-digit' });
}, 1000);
function showTab(name) {
  var tabs = document.querySelectorAll('.tab');
  for (var i = 0; i < tabs.length; i++) tabs[i].classList.toggle('active', tabs[i].getAttribute('data-tab') === name);
  var panes = document.querySelectorAll('.tabpane');
  for (var j = 0; j < panes.length; j++) panes[j].classList.remove('active');
  document.getElementById('pane-' + name).classList.add('active');
}
function logout() { fetch(API_BASE + 'api/logout', { method: 'POST' }).then(function () { location.href = API_BASE + 'login'; }).catch(function () { location.href = API_BASE + 'login'; }); }
function openModal(title, html, wide) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = html;
  var box = document.getElementById('modal-box');
  if (wide) box.classList.add('wide'); else box.classList.remove('wide');
  document.getElementById('modal-bg').classList.add('open');
}
function closeModal() { document.getElementById('modal-bg').classList.remove('open'); }
document.addEventListener('keydown', function (e) { if (e.key === 'Escape') closeModal(); });
function val(id) { var el = document.getElementById(id); return el ? el.value : ''; }
function loadServerInfo() {
  return api('GET', '/api/server-info').then(function (d) {
    document.getElementById('ver').textContent = 'v' + d.version + ' · python 🐍';
    document.getElementById('fun-line').textContent = d.fun_line;
    document.getElementById('pw-warn').style.display = d.default_password ? 'block' : 'none';
    renderOutbound(d.outbound);
    document.getElementById('c-users').textContent = d.counts.users;
    document.getElementById('c-active').textContent = d.counts.active_users;
    document.getElementById('c-nodes').textContent = d.counts.nodes;
    document.getElementById('c-online').textContent = d.counts.online_nodes;
  }).catch(function () {});
}
function renderOutbound(o) {
  document.getElementById('out-flag').textContent = o.flag || '🏳️';
  document.getElementById('out-ip').textContent = o.ip || 'نامشخص';
  document.getElementById('out-country').textContent = o.country ? (o.flag || '') + ' ' + o.country : 'کشور نامشخص';
  var city = document.getElementById('out-city');
  if (o.city) { city.style.display = ''; city.textContent = '📍 ' + o.city; } else city.style.display = 'none';
  document.getElementById('out-isp').textContent = o.isp || '';
}
function refreshOutbound(btn) {
  btn.disabled = true;
  api('POST', '/api/server-info/refresh').then(function (d) {
    renderOutbound(d.outbound);
    toast('🌍 آی‌پی خروجی رفرش شد!');
  }).catch(function (e) { toast('❌ ' + e.message, true); })
    .then(function () { btn.disabled = false; });
}
function renderDashNodes() {
  var box = document.getElementById('dash-nodes');
  if (!NODES.length) { box.innerHTML = '<span class="muted">هنوز نودی نیست… برو تب 🛰 نودها و یکی بساز!</span>'; return; }
  var html = '';
  NODES.forEach(function (n) {
    var st = n.online === true ? '<span class="badge on">آنلاین 💚</span>'
      : n.online === false ? '<span class="badge off">آفلاین 💔</span>' : '<span class="badge warn">چک نشده</span>';
    html += '<div class="kv"><span>' + (n.flag || '🏳️') + ' <b>' + esc(n.name) + '</b> <span class="badge info">' + esc(n.protocol) + '</span> <span class="muted mono">' + esc(n.address) + ':' + n.port + '</span></span><span>' + st + ' <span class="muted">' + (n.latency_ms !== null && n.latency_ms !== undefined ? n.latency_ms + 'ms' : '') + '</span></span></div>';
  });
  box.innerHTML = html;
}
function loadUsers() {
  return api('GET', '/api/users').then(function (d) { USERS = d.users; renderUsers(); });
}
function renderUsers() {
  var q = (document.getElementById('user-search').value || '').toLowerCase();
  var tb = document.getElementById('users-body');
  var list = USERS.filter(function (u) { return u.username.toLowerCase().indexOf(q) >= 0 || (u.note || '').toLowerCase().indexOf(q) >= 0; });
  if (!list.length) { tb.innerHTML = '<tr><td colspan="5" class="muted">یوزری نیست… ساداهارو حوصله‌ش سر رفته 🐶</td></tr>'; return; }
  var html = '';
  list.forEach(function (u) {
    var st = u.expired ? '<span class="badge warn">منقضی ⏳</span>'
      : u.enabled ? '<span class="badge on">فعال 💚</span>' : '<span class="badge off">غیرفعال 💔</span>';
    var traf;
    if (u.traffic_limit_gb > 0) {
      var pct = Math.min(100, u.traffic_used_gb / u.traffic_limit_gb * 100);
      traf = '<div style="display:flex;align-items:center;gap:6px"><div class="bar' + (pct > 85 ? ' hot' : '') + '" style="flex:1"><i style="width:' + pct + '%"></i></div><small class="muted">' + u.traffic_used_gb + '/' + u.traffic_limit_gb + 'G</small></div>';
    } else { traf = '<small class="muted">' + u.traffic_used_gb + ' GB ♾️</small>'; }
    html += '<tr><td><b>' + esc(u.username) + '</b><br><small class="muted">' + esc(u.note || '') + '</small></td>'
      + '<td>' + st + '</td><td>' + traf + '</td><td><small>' + faDate(u.expires_at) + '</small></td>'
      + '<td><div class="row-actions">'
      + '<button class="btn small" onclick="openLinks(\'' + u.id + '\')">🔗 لینک‌ها</button>'
      + '<button class="btn small blue" onclick="openUserModal(\'' + u.id + '\')">✏️</button>'
      + '<button class="btn small ghost" onclick="toggleUser(\'' + u.id + '\')">' + (u.enabled ? '⏸' : '▶️') + '</button>'
      + '<button class="btn small danger" onclick="delUser(\'' + u.id + '\')">🗑</button>'
      + '</div></td></tr>';
  });
  tb.innerHTML = html;
}
function findU(id) { for (var i = 0; i < USERS.length; i++) if (USERS[i].id === id) return USERS[i]; return null; }
function findN(id) { for (var i = 0; i < NODES.length; i++) if (NODES[i].id === id) return NODES[i]; return null; }
function openUserModal(id) {
  var u = id ? findU(id) : null;
  var usedRow = u ? '<div><label class="lbl">ترافیک مصرف‌شده (گیگ)</label><input id="f-used" type="number" min="0" step="0.1" value="' + u.traffic_used_gb + '"></div>' : '';
  var extraBtns = u ? '<button class="btn ghost" onclick="resetTraffic(\'' + u.id + '\')">🔄 ریست ترافیک</button><button class="btn ghost" onclick="rotateSub(\'' + u.id + '\')">🎲 لینک ساب جدید</button>' : '';
  openModal(u ? '✏️ ویرایش ' + u.username : '➕ کاربر جدید',
    '<label class="lbl">نام کاربری *</label>'
    + '<input id="f-username" value="' + esc(u ? u.username : '') + '" placeholder="gintoki">'
    + '<div class="form-grid"><div><label class="lbl">سقف ترافیک (گیگ، 0 = نامحدود ♾️)</label>'
    + '<input id="f-limit" type="number" min="0" step="0.5" value="' + (u ? u.traffic_limit_gb : 0) + '"></div>'
    + usedRow + '</div>'
    + '<label class="lbl">انقضا (خالی = نامحدود)</label>'
    + '<input id="f-exp" type="datetime-local" value="' + isoToLocal(u ? u.expires_at : '') + '">'
    + '<label class="lbl">یادداشت</label>'
    + '<input id="f-note" value="' + esc(u ? u.note : '') + '" placeholder="…">'
    + '<label class="lbl"><input type="checkbox" id="f-enabled"' + ((!u || u.enabled) ? ' checked' : '') + ' style="margin-left:8px"> فعال باشه؟</label>'
    + '<div class="mfoot"><button class="btn green" onclick="saveUser(\'' + (id || '') + '\')">💾 ذخیره</button>' + extraBtns + '</div>');
}
function saveUser(id) {
  var usedEl = document.getElementById('f-used');
  var body = {
    username: val('f-username').trim(),
    traffic_limit_gb: parseFloat(val('f-limit')) || 0,
    expires_at: localToIso(val('f-exp')),
    note: val('f-note'),
    enabled: document.getElementById('f-enabled').checked,
  };
  if (id && usedEl) body.traffic_used_gb = parseFloat(val('f-used')) || 0;
  if (!body.username) { toast('❌ اسم کاربر لازمه!', true); return; }
  (id ? api('PATCH', '/api/users/' + id, body) : api('POST', '/api/users', body)).then(function () {
    closeModal();
    return loadUsers().then(loadServerInfo).then(function () {
      toast(id ? '✏️ ذخیره شد!' : '🎉 کاربر ساخته شد! ساداهارو دم تکون داد 🐶');
    });
  }).catch(function (e) { toast('❌ ' + e.message, true); });
}
function delUser(id) {
  var u = findU(id);
  if (!confirm('یوزر «' + (u ? u.username : '') + '» حذف بشه؟ 🗑')) return;
  api('DELETE', '/api/users/' + id).then(function () {
    return loadUsers().then(loadServerInfo).then(function () { toast('🗑 حذف شد!'); });
  }).catch(function (e) { toast('❌ ' + e.message, true); });
}
function toggleUser(id) {
  var u = findU(id);
  api('PATCH', '/api/users/' + id, { enabled: !u.enabled }).then(function () { return loadUsers().then(loadServerInfo); })
    .catch(function (e) { toast('❌ ' + e.message, true); });
}
function resetTraffic(id) {
  api('POST', '/api/users/' + id + '/reset-traffic').then(function () { closeModal(); return loadUsers(); })
    .then(function () { toast('🔄 ترافیک صفر شد!'); })
    .catch(function (e) { toast('❌ ' + e.message, true); });
}
function rotateSub(id) {
  api('POST', '/api/users/' + id + '/rotate-sub').then(function () { closeModal(); return loadUsers(); })
    .then(function () { toast('🎲 لینک ساب عوض شد! قبلی باطله.'); })
    .catch(function (e) { toast('❌ ' + e.message, true); });
}
function openLinks(id) {
  var u = findU(id);
  openModal('🔗 لینک‌های ' + u.username, '<div class="muted">در حال ساخت کانفیگ‌ها… 🍓</div>', true);
  api('GET', '/api/users/' + id + '/subscription').then(function (d) {
    LINKS_CACHE = d.links;
    var rows = '';
    if (!d.links.length) { rows = '<div class="muted">نود فعالی نیست… اول یه نود بساز 🛰</div>'; }
    d.links.forEach(function (l, i) {
      rows += '<div class="link-row"><span class="meta">' + l.flag + ' ' + esc(l.node) + ' <span class="badge info">' + esc(l.protocol) + (l.network ? '/' + esc(l.network) : '') + '</span></span><code>' + esc(l.link.slice(0, 70)) + '…</code>'
        + '<button class="btn small" onclick="copyLink(' + i + ')">📋</button></div>';
    });
    document.getElementById('modal-body').innerHTML =
      '<div class="link-row"><span class="meta">🔗 لینک ساب</span><code>' + esc(d.sub_url) + '</code>'
      + '<button class="btn small blue" id="cp-sub">📋</button></div>'
      + '<div class="link-row"><span class="meta">🌐 صفحه اشتراک</span><code>' + esc(d.page_url) + '</code>'
      + '<button class="btn small blue" id="cp-page">📋</button>'
      + '<a class="btn small ghost" href="' + esc(d.page_url) + '" target="_blank">↗️</a></div>'
      + '<div class="link-row"><span class="meta">⚖️ لودبالانسر (هر بار یه نود)</span><code>' + esc(d.lb_url) + '</code>'
      + '<button class="btn small gold" id="cp-lb">📋</button></div>'
      + '<div class="qr-box" data-qr="' + esc(d.sub_url) + '"></div>'
      + '<h3 style="margin-top:8px">⚔️ کانفیگ‌های تکی (' + d.links.length + ')</h3>' + rows;
    renderQr();
    document.getElementById('cp-sub').onclick = function () { copyText(d.sub_url, '🔗 لینک ساب کپی شد!'); };
    document.getElementById('cp-page').onclick = function () { copyText(d.page_url, '🌐 کپی شد!'); };
    document.getElementById('cp-lb').onclick = function () { copyText(d.lb_url, '⚖️ لینک LB کپی شد!'); };
  }).catch(function (e) { document.getElementById('modal-body').innerHTML = '❌ ' + esc(e.message); });
}
function loadNodes() {
  return api('GET', '/api/nodes').then(function (d) { NODES = d.nodes; renderNodes(); renderDashNodes(); });
}
function renderNodes() {
  var g = document.getElementById('nodes-grid');
  if (!NODES.length) { g.innerHTML = '<div class="card muted">نودی نیست… یوروزویا بیکاره 😴 دکمه «نود جدید» رو بزن!</div>'; return; }
  var html = '';
  NODES.forEach(function (n) {
    var st = n.online === true ? '<span class="badge on">آنلاین 💚</span>'
      : n.online === false ? '<span class="badge off">آفلاین 💔</span>' : '<span class="badge warn">چک نشده ❔</span>';
    var en = n.enabled ? '' : ' <span class="badge off">غیرفعال</span>';
    html += '<div class="card node-card"><div class="head"><span class="flag">' + (n.flag || '🏳️') + '</span>'
      + '<div><div class="name">' + esc(n.name) + en + '</div>'
      + '<div class="muted" style="font-size:.78rem">' + esc(n.country || 'کشور نامشخص') + ' · <span class="mono">' + esc(n.resolved_ip || n.address) + ':' + n.port + '</span></div></div>'
      + '<span class="proto badge info">' + esc(n.protocol) + (n.security !== 'none' ? '/' + esc(n.security) : '') + '</span></div>'
      + '<div>' + st + ' <span class="muted" style="font-size:.8rem">' + (n.latency_ms !== null && n.latency_ms !== undefined ? '⚡ ' + n.latency_ms + 'ms' : '') + (n.last_check ? ' · ' + faDate(n.last_check) : '') + '</span></div>'
      + '<div class="muted mono" style="font-size:.75rem">' + esc(n.address) + ':' + n.port + ' · ' + esc(n.network) + (n.mode ? ' · mode:' + esc(n.mode) : '') + (n.sni ? ' · sni:' + esc(n.sni) : '') + '</div>'
      + '<div class="foot"><button class="btn small blue" onclick="checkNode(\'' + n.id + '\',this)">📡 چک</button>'
      + '<button class="btn small ghost" onclick="openNodeModal(\'' + n.id + '\')">✏️ ویرایش</button>'
      + '<button class="btn small ghost" onclick="toggleNode(\'' + n.id + '\')">' + (n.enabled ? '⏸' : '▶️') + '</button>'
      + '<button class="btn small danger" onclick="delNode(\'' + n.id + '\')">🗑</button></div></div>';
  });
  g.innerHTML = html;
}
function sel(id, opts, cur, onchange) {
  var h = '<select id="' + id + '"' + (onchange ? ' onchange="' + onchange + '"' : '') + '>';
  opts.forEach(function (o) { h += '<option value="' + o + '"' + (o === cur ? ' selected' : '') + '>' + (o === '' ? '(خالی)' : o) + '</option>'; });
  return h + '</select>';
}
function gfield(n, k) { return esc(n ? (n[k] === undefined || n[k] === null ? '' : n[k]) : ''); }
var NETWORKS_BY_PROTO = {
  vless: ['tcp', 'ws', 'grpc', 'xhttp', 'kcp'],
  vmess: ['tcp', 'ws', 'grpc', 'h2', 'kcp'],
  trojan: ['tcp', 'ws', 'grpc'],
  ss: ['tcp'],
};
function filterNetwork() {
  var s = document.getElementById('n-network');
  if (!s) return;
  var allowed = NETWORKS_BY_PROTO[val('n-protocol')] || ['tcp'];
  var cur = s.value;
  if (allowed.indexOf(cur) < 0) cur = allowed[0];
  var opts = '';
  allowed.forEach(function (o) { opts += '<option value="' + o + '"' + (o === cur ? ' selected' : '') + '>' + o + '</option>'; });
  s.innerHTML = opts;
}
function openNodeModal(id) {
  var n = id ? findN(id) : null;
  openModal(n ? '✏️ ویرایش ' + n.name : '➕ نود جدید',
    '<div class="form-grid">'
    + '<div><label class="lbl">اسم نود *</label><input id="n-name" value="' + gfield(n, 'name') + '" placeholder="آلمان ۱ 🇩🇪"></div>'
    + '<div><label class="lbl">آدرس (دامنه/آی‌پی) *</label><input id="n-address" value="' + gfield(n, 'address') + '" placeholder="de1.example.com" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">پورت</label><input id="n-port" type="number" value="' + (n ? n.port : 443) + '" style="direction:ltr"></div>'
    + '<div><label class="lbl">پروتکل</label>' + sel('n-protocol', ['vless', 'vmess', 'trojan', 'ss'], n ? n.protocol : 'vless', 'filterNetwork()') + '</div>'
    + '<div><label class="lbl">نتورک</label>' + sel('n-network', NETWORKS_BY_PROTO[(n && n.protocol) || 'vless'], n ? n.network : 'tcp') + '</div>'
    + '<div><label class="lbl">امنیت</label>' + sel('n-security', ['none', 'tls', 'reality'], n ? n.security : 'none') + '</div>'
    + '<div><label class="lbl">SNI</label><input id="n-sni" value="' + gfield(n, 'sni') + '" placeholder="example.com" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Path / ServiceName</label><input id="n-path" value="' + gfield(n, 'path') + '" placeholder="/ws" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Host (هدر)</label><input id="n-host" value="' + gfield(n, 'host') + '" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Fingerprint</label>' + sel('n-fp', ['', 'chrome', 'firefox', 'safari', 'ios', 'android', 'edge', 'random'], n ? n.fp : '') + '</div>'
    + '<div><label class="lbl">ALPN</label><input id="n-alpn" value="' + gfield(n, 'alpn') + '" placeholder="h2,http/1.1" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Flow (vless)</label><input id="n-flow" value="' + gfield(n, 'flow') + '" placeholder="xtls-rprx-vision" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Mode (grpc/xhttp)</label><input id="n-mode" value="' + gfield(n, 'mode') + '" placeholder="gun / multi / auto" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Header (kcp)</label>' + sel('n-header', ['', 'none', 'wechat', 'utp', 'srtp', 'dtls', 'wireguard'], n ? n.header : '') + '</div>'
    + '</div>'
    + '<div class="card" style="margin-top:12px;padding:12px"><b style="font-size:.85rem">🌀 REALITY (فقط اگه امنیت = reality)</b><div class="form-grid">'
    + '<div class="full"><label class="lbl">Public Key (pbk)</label><input id="n-pbk" value="' + gfield(n, 'reality_pbk') + '" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Short ID (sid)</label><input id="n-sid" value="' + gfield(n, 'reality_sid') + '" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">SpiderX (spx)</label><input id="n-spx" value="' + gfield(n, 'reality_spx') + '" style="direction:ltr;text-align:left"></div>'
    + '</div></div>'
    + '<div class="card" style="margin-top:12px;padding:12px"><b style="font-size:.85rem">👻 Shadowsocks (فقط اگه پروتکل = ss)</b><div class="form-grid">'
    + '<div><label class="lbl">متد</label>' + sel('n-ssmethod', ['chacha20-ietf-poly1305', 'aes-256-gcm', 'aes-128-gcm', '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm'], n ? n.ss_method : 'chacha20-ietf-poly1305') + '</div>'
    + '<div><label class="lbl">پسورد (خالی = uuid یوزر)</label><input id="n-sspass" value="' + gfield(n, 'ss_password') + '" style="direction:ltr;text-align:left"></div>'
    + '</div></div>'
    + '<label class="lbl"><input type="checkbox" id="n-enabled"' + ((!n || n.enabled) ? ' checked' : '') + ' style="margin-left:8px"> فعال باشه؟</label>'
    + '<div class="mfoot"><button class="btn green" onclick="saveNode(\'' + (id || '') + '\')">💾 ذخیره</button></div>', true);
}
function saveNode(id) {
  var body = {
    name: val('n-name').trim(), address: val('n-address').trim(),
    port: parseInt(val('n-port'), 10) || 443,
    protocol: val('n-protocol'), network: val('n-network'), security: val('n-security'),
    sni: val('n-sni').trim(), path: val('n-path').trim(), host: val('n-host').trim(),
    fp: val('n-fp'), alpn: val('n-alpn').trim(), flow: val('n-flow').trim(),
    mode: val('n-mode').trim(), header: val('n-header').trim(),
    reality_pbk: val('n-pbk').trim(), reality_sid: val('n-sid').trim(), reality_spx: val('n-spx').trim(),
    ss_method: val('n-ssmethod'), ss_password: val('n-sspass'),
    enabled: document.getElementById('n-enabled').checked,
  };
  if (!body.name || !body.address) { toast('❌ اسم و آدرس نود لازمه!', true); return; }
  (id ? api('PATCH', '/api/nodes/' + id, body) : api('POST', '/api/nodes', body)).then(function () {
    closeModal();
    return loadNodes().then(loadServerInfo).then(function () {
      toast(id ? '✏️ نود ذخیره شد!' : '🛰 نود ساخته شد! در حال چک سلامت…');
      if (!id) setTimeout(function () { loadNodes(); }, 6000);
    });
  }).catch(function (e) { toast('❌ ' + e.message, true); });
}
function delNode(id) {
  var n = findN(id);
  if (!confirm('نود «' + (n ? n.name : '') + '» حذف بشه؟ 🗑')) return;
  api('DELETE', '/api/nodes/' + id).then(function () {
    return loadNodes().then(loadServerInfo).then(function () { toast('🗑 نود حذف شد!'); });
  }).catch(function (e) { toast('❌ ' + e.message, true); });
}
function toggleNode(id) {
  var n = findN(id);
  api('PATCH', '/api/nodes/' + id, { enabled: !n.enabled }).then(function () { return loadNodes(); })
    .catch(function (e) { toast('❌ ' + e.message, true); });
}
function checkNode(id, btn) {
  if (btn) btn.disabled = true;
  api('POST', '/api/nodes/' + id + '/check').then(function () { return loadNodes().then(loadServerInfo); })
    .then(function () { toast('📡 چک شد!'); })
    .catch(function (e) { toast('❌ ' + e.message, true); })
    .then(function () { if (btn) btn.disabled = false; });
}
function checkAllNodes(btn) {
  btn.disabled = true; btn.textContent = '⏳ در حال چک…';
  api('POST', '/api/nodes/check-all').then(function () { return loadNodes().then(loadServerInfo); })
    .then(function () { toast('📡 همه نودها چک شدن!'); })
    .catch(function (e) { toast('❌ ' + e.message, true); })
    .then(function () { btn.disabled = false; btn.textContent = '📡 چک همه نودها'; });
}
function doLookup() {
  var q = val('lookup-q').trim();
  if (!q) return;
  var box = document.getElementById('lookup-res');
  box.innerHTML = '<span class="muted">⏳ شینپاچی داره عینکش رو تمیز می‌کنه…</span>';
  api('POST', '/api/tools/lookup', { query: q }).then(function (d) {
    var r = d.result;
    box.innerHTML = '<div class="card" style="text-align:center"><div class="flag-big">' + r.flag + '</div>'
      + '<div class="out-ip" style="font-size:1.2rem">' + esc(r.resolved_ip || '؟') + '</div>'
      + '<div style="margin:8px 0"><span class="badge gold">' + esc(r.country || 'نامشخص') + '</span>'
      + (r.city ? '<span class="badge info">📍 ' + esc(r.city) + '</span>' : '') + '</div>'
      + '<div class="muted" style="font-size:.82rem">' + esc(r.isp || '') + '</div></div>';
  }).catch(function (e) { box.innerHTML = '❌ ' + esc(e.message); });
}
function loadSettings() {
  return api('GET', '/api/settings').then(function (d) {
    SETTINGS = d.settings;
    document.getElementById('set-base').value = SETTINGS.sub_base_url || '';
  });
}
function saveSettings() {
  api('POST', '/api/settings', { sub_base_url: val('set-base').trim() })
    .then(function () { toast('💾 ذخیره شد!'); })
    .catch(function (e) { toast('❌ ' + e.message, true); });
}
function downloadBackup() { location.href = API_BASE + 'api/settings/backup'; }
function doRestore(input) {
  var f = input.files[0];
  if (!f) return;
  var rd = new FileReader();
  rd.onload = function () {
    var data;
    try { data = JSON.parse(rd.result); } catch (e) { toast('❌ فایل معتبر نیست', true); input.value = ''; return; }
    api('POST', '/api/settings/restore', { data: data }).then(function (d) {
      return loadAll().then(function () { toast('⬆️ ریستور شد! ' + d.imported.users + ' یوزر، ' + d.imported.nodes + ' نود 🎉'); });
    }).catch(function (e) { toast('❌ ' + e.message, true); });
    input.value = '';
  };
  rd.readAsText(f);
}
function loadLb() {
  return api('GET', '/api/lb').then(function (d) {
    document.getElementById('lb-strategy').value = d.strategy;
    document.getElementById('lb-cur').textContent = d.strategy;
    document.getElementById('lb-cands').textContent = d.candidates.length + ' نود';
    var total = 0;
    Object.keys(d.picks || {}).forEach(function (k) { total += d.picks[k]; });
    document.getElementById('lb-total').textContent = total;
    var tb = document.getElementById('lb-body');
    if (!d.candidates.length) { tb.innerHTML = '<tr><td colspan="4" class="muted">هنوز نودی نیست…</td></tr>'; return; }
    var html = '';
    d.candidates.forEach(function (n) {
      var st = n.online === true ? '<span class="badge on">آنلاین 💚</span>'
        : n.online === false ? '<span class="badge off">آفلاین 💔</span>' : '<span class="badge warn">چک نشده</span>';
      html += '<tr><td>' + (n.flag || '🏳️') + ' <b>' + esc(n.name) + '</b></td><td>' + st + '</td><td>' + (n.latency_ms !== null && n.latency_ms !== undefined ? '⚡ ' + n.latency_ms + 'ms' : '–') + '</td><td>' + (d.picks[n.id] || 0) + ' بار</td></tr>';
    });
    tb.innerHTML = html;
  }).catch(function () {});
}
function saveLb() {
  api('POST', '/api/lb', { strategy: val('lb-strategy') }).then(function (d) {
    toast('💾 استراتژی لودبالانسر ذخیره شد: ' + d.strategy);
    return loadLb();
  }).catch(function (e) { toast('❌ ' + e.message, true); });
}
function loadAll() {
  return loadServerInfo().then(function () { return Promise.all([loadUsers(), loadNodes(), loadSettings(), loadLb()]); });
}
loadAll();
setInterval(function () {
  api('GET', '/api/fun/line').then(function (d) { document.getElementById('fun-line').textContent = d.line; }).catch(function () {});
}, 20000);
</script>
</body>
</html>'''
PAGE_SUB = r'''<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍓 اشتراک من | یوروزویا</title>
<style>
@import url('https://cdn.jsdelivr.net/gh/rastikerdar/vazirmatn@v33.003/Vazirmatn-font-face.css');
:root{--bg:#0b1020;--bg2:#101731;--card:#151c3a;--card2:#1b2350;--line:rgba(255,255,255,.09);--ink:#eef1ff;--muted:#9aa3c7;--red:#e63946;--pink:#ff7b9c;--blue:#4aa3ff;--gold:#f4d35e;--green:#2ec4b6;--orange:#ff9f45;--milk:#fff7ec;--wood1:#9c6a3c;--wood2:#6f4525;--radius:16px;--shadow:0 10px 40px rgba(0,0,0,.45)}
*{box-sizing:border-box}html,body{margin:0;padding:0}
body{font-family:'Vazirmatn',Tahoma,Arial,sans-serif;color:var(--ink);min-height:100vh;background:radial-gradient(1200px 600px at 85% -10%,rgba(230,57,70,.16),transparent 60%),radial-gradient(900px 500px at 10% 110%,rgba(74,163,255,.14),transparent 60%),var(--bg)}
body::before{content:"";position:fixed;inset:0;pointer-events:none;opacity:.5;background-image:url("data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='120' height='60' viewBox='0 0 120 60'%3E%3Cg fill='none' stroke='rgba(255,255,255,0.05)' stroke-width='1.5'%3E%3Ccircle cx='30' cy='60' r='28'/%3E%3Ccircle cx='30' cy='60' r='20'/%3E%3Ccircle cx='30' cy='60' r='12'/%3E%3Ccircle cx='90' cy='60' r='28'/%3E%3Ccircle cx='90' cy='60' r='20'/%3E%3Ccircle cx='90' cy='60' r='12'/%3E%3Ccircle cx='60' cy='30' r='28'/%3E%3Ccircle cx='60' cy='30' r='20'/%3E%3Ccircle cx='60' cy='30' r='12'/%3E%3C/g%3E%3C/svg%3E")}
a{color:var(--blue)}::selection{background:var(--red);color:#fff}
.wood{background:repeating-linear-gradient(93deg,rgba(0,0,0,.12) 0 3px,transparent 3px 9px),linear-gradient(180deg,var(--wood1),var(--wood2));border:2px solid #3d2712;border-radius:12px;box-shadow:inset 0 1px 0 rgba(255,255,255,.25),inset 0 -6px 14px rgba(0,0,0,.35),var(--shadow);color:var(--milk);text-shadow:0 2px 0 rgba(0,0,0,.5);position:relative}
.wood::before,.wood::after{content:"";position:absolute;top:8px;width:10px;height:10px;border-radius:50%;background:radial-gradient(circle at 35% 30%,#ffe9b0,#8a6a2a 70%);box-shadow:0 1px 2px rgba(0,0,0,.6)}
.wood::before{right:10px}.wood::after{left:10px}
.kanji{font-weight:900;letter-spacing:.35em;color:var(--gold);text-shadow:0 2px 0 rgba(0,0,0,.55),0 0 18px rgba(244,211,94,.35)}
.wrap{max-width:1180px;margin:0 auto;padding:24px 16px 60px;position:relative;z-index:1}
.topbar{display:flex;align-items:center;gap:14px;padding:12px 18px;margin-bottom:22px}
.topbar .logo{width:52px;height:52px;flex:none;filter:drop-shadow(0 4px 8px rgba(0,0,0,.5))}
.topbar h1{font-size:1.35rem;margin:0}
.topbar h1 .kanji{font-size:.95rem;margin-right:8px}
.topbar .sub{color:var(--milk);opacity:.85;font-size:.85rem;margin-top:2px}
.topbar .spacer{flex:1}
.clock{font-variant-numeric:tabular-nums;color:var(--gold);font-weight:700}
.card{background:linear-gradient(180deg,var(--card2),var(--card));border:1px solid var(--line);border-radius:var(--radius);box-shadow:var(--shadow);padding:18px}
.card h2,.card h3{margin:0 0 12px}
.muted{color:var(--muted)}
.grid{display:grid;gap:16px}.grid.c2{grid-template-columns:repeat(2,1fr)}.grid.c3{grid-template-columns:repeat(3,1fr)}.grid.c4{grid-template-columns:repeat(4,1fr)}
@media (max-width:900px){.grid.c4{grid-template-columns:repeat(2,1fr)}.grid.c3{grid-template-columns:1fr}.grid.c2{grid-template-columns:1fr}}
.tabs{display:flex;gap:8px;margin-bottom:18px;flex-wrap:wrap}
.tab{border:1px solid var(--line);background:rgba(255,255,255,.04);color:var(--ink);padding:10px 18px;border-radius:999px;cursor:pointer;font-family:inherit;font-size:.95rem;transition:all .18s ease}
.tab:hover{background:rgba(255,255,255,.09);transform:translateY(-1px)}
.tab.active{background:linear-gradient(180deg,#f25460,var(--red));border-color:#ff8a93;color:#fff;font-weight:700;box-shadow:0 6px 20px rgba(230,57,70,.4)}
.tabpane{display:none;animation:fade .25s ease}.tabpane.active{display:block}
@keyframes fade{from{opacity:0;transform:translateY(6px)}to{opacity:1;transform:none}}
.btn{font-family:inherit;border:none;border-radius:12px;padding:10px 18px;cursor:pointer;font-size:.92rem;font-weight:700;color:#fff;background:linear-gradient(180deg,#f25460,var(--red));box-shadow:0 4px 16px rgba(230,57,70,.35),inset 0 1px 0 rgba(255,255,255,.3);transition:all .15s ease;display:inline-flex;align-items:center;gap:6px}
.btn:hover{transform:translateY(-1px);box-shadow:0 8px 22px rgba(230,57,70,.45)}
.btn:active{transform:translateY(1px)}
.btn.blue{background:linear-gradient(180deg,#63b3ff,#2f7fe0);box-shadow:0 4px 16px rgba(74,163,255,.35)}
.btn.green{background:linear-gradient(180deg,#3fd6c5,#1d9c8f);box-shadow:0 4px 16px rgba(46,196,182,.3)}
.btn.gold{background:linear-gradient(180deg,#f7dd7e,#d9a93b);color:#3a2a00}
.btn.ghost{background:rgba(255,255,255,.07);box-shadow:none;border:1px solid var(--line);color:var(--ink);font-weight:500}
.btn.danger{background:linear-gradient(180deg,#6b7280,#374151);box-shadow:none}
.btn.small{padding:6px 12px;font-size:.8rem;border-radius:9px}
.btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:14px;flex-wrap:wrap}
.toolbar .spacer{flex:1}
input,select,textarea{font-family:inherit;background:rgba(0,0,0,.35);border:1px solid var(--line);color:var(--ink);border-radius:10px;padding:10px 12px;font-size:.9rem;width:100%;outline:none;transition:border .15s;color-scheme:dark}
input:focus,select:focus,textarea:focus{border-color:var(--pink);box-shadow:0 0 0 3px rgba(255,123,156,.15)}
input[type="checkbox"]{width:auto;transform:scale(1.3);accent-color:var(--red)}
label.lbl{display:block;font-size:.8rem;color:var(--muted);margin:10px 0 5px}
.form-grid{display:grid;grid-template-columns:repeat(2,1fr);gap:0 14px}
.form-grid .full{grid-column:1/-1}
@media (max-width:640px){.form-grid{grid-template-columns:1fr}}
.search{max-width:260px}
.table-wrap{overflow-x:auto;border-radius:12px;border:1px solid var(--line)}
table{width:100%;border-collapse:collapse;font-size:.88rem;min-width:760px}
th{background:rgba(0,0,0,.35);color:var(--gold);font-weight:700;text-align:right;padding:12px 10px;white-space:nowrap}
td{padding:10px;border-top:1px solid var(--line);vertical-align:middle}
tr:hover td{background:rgba(255,255,255,.03)}
.mono{font-family:'Courier New',monospace;direction:ltr;unicode-bidi:embed;font-size:.82rem}
.row-actions{display:flex;gap:6px;flex-wrap:wrap}
.badge{display:inline-block;padding:3px 11px;border-radius:999px;font-size:.75rem;font-weight:700;white-space:nowrap}
.badge.on{background:rgba(46,196,182,.18);color:#5eead4;border:1px solid rgba(46,196,182,.5)}
.badge.off{background:rgba(230,57,70,.15);color:#ff8a93;border:1px solid rgba(230,57,70,.5)}
.badge.warn{background:rgba(255,159,69,.15);color:var(--orange);border:1px solid rgba(255,159,69,.5)}
.badge.info{background:rgba(74,163,255,.15);color:#8ec5ff;border:1px solid rgba(74,163,255,.5)}
.badge.gold{background:rgba(244,211,94,.14);color:var(--gold);border:1px solid rgba(244,211,94,.45)}
.bar{height:8px;border-radius:99px;background:rgba(255,255,255,.1);overflow:hidden;min-width:90px}
.bar>i{display:block;height:100%;border-radius:99px;background:linear-gradient(90deg,var(--blue),var(--pink));transition:width .4s}
.bar.hot>i{background:linear-gradient(90deg,var(--orange),var(--red))}
.stat{text-align:center;padding:16px 10px}
.stat .num{font-size:2rem;font-weight:900;color:var(--milk);line-height:1.2}
.stat .cap{color:var(--muted);font-size:.82rem;margin-top:4px}
.stat .ico{font-size:1.6rem}
.flag-big{font-size:3.2rem;line-height:1;filter:drop-shadow(0 4px 10px rgba(0,0,0,.5))}
.out-ip{font-size:1.5rem;font-weight:900;direction:ltr;color:var(--gold);font-variant-numeric:tabular-nums}
.kv{display:flex;justify-content:space-between;gap:10px;padding:7px 0;border-top:1px dashed rgba(255,255,255,.12);font-size:.88rem}
.kv:first-of-type{border-top:none}
.kv b{color:var(--milk)}
.node-card{display:flex;flex-direction:column;gap:10px}
.node-card .head{display:flex;align-items:center;gap:10px}
.node-card .head .flag{font-size:1.8rem}
.node-card .head .name{font-weight:800;font-size:1.05rem}
.node-card .head .proto{margin-right:auto}
.node-card .foot{display:flex;gap:6px;flex-wrap:wrap;margin-top:4px}
.login-wrap{min-height:100vh;display:flex;align-items:center;justify-content:center;padding:20px;position:relative;z-index:1}
.login-card{width:100%;max-width:420px;text-align:center;padding:30px 28px}
.login-card .logo{width:110px;height:110px;filter:drop-shadow(0 8px 20px rgba(230,57,70,.35));animation:float 3.5s ease-in-out infinite}
@keyframes float{0%,100%{transform:translateY(0) rotate(-2deg)}50%{transform:translateY(-10px) rotate(2deg)}}
.login-card h1{margin:10px 0 2px;font-size:1.6rem}
.login-card .tag{color:var(--muted);font-size:.88rem;margin-bottom:18px}
.login-card input{text-align:center;font-size:1.1rem;letter-spacing:2px;padding:13px}
.login-card .btn{width:100%;justify-content:center;margin-top:12px;padding:13px;font-size:1rem}
.login-err{color:#ff8a93;min-height:24px;font-size:.85rem;margin-top:8px}
.shake{animation:shake .4s}
@keyframes shake{0%,100%{transform:translateX(0)}20%,60%{transform:translateX(-8px)}40%,80%{transform:translateX(8px)}}
.strawberry-rain{position:fixed;inset:0;pointer-events:none;overflow:hidden;z-index:0}
.strawberry-rain span{position:absolute;top:-40px;animation:fall linear infinite;opacity:.7}
@keyframes fall{to{transform:translateY(110vh) rotate(360deg)}}
.modal-bg{position:fixed;inset:0;background:rgba(4,6,16,.75);backdrop-filter:blur(4px);display:none;align-items:flex-start;justify-content:center;z-index:50;padding:30px 14px;overflow-y:auto}
.modal-bg.open{display:flex}
.modal{width:100%;max-width:640px;animation:pop .22s ease}
.modal.wide{max-width:860px}
@keyframes pop{from{opacity:0;transform:scale(.95) translateY(10px)}to{opacity:1;transform:none}}
.modal .mhead{display:flex;align-items:center;gap:10px;margin-bottom:14px}
.modal .mhead h3{margin:0;flex:1}
.x{background:rgba(255,255,255,.08);border:none;color:var(--ink);width:34px;height:34px;border-radius:10px;cursor:pointer;font-size:1.1rem}
.x:hover{background:var(--red)}
.mfoot{display:flex;gap:10px;justify-content:flex-start;margin-top:18px}
.link-row{display:flex;gap:8px;align-items:center;background:rgba(0,0,0,.3);border:1px solid var(--line);border-radius:10px;padding:8px 10px;margin-bottom:8px}
.link-row .meta{flex:none;font-size:.78rem;color:var(--muted);max-width:220px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.link-row code{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;direction:ltr;font-size:.75rem;color:var(--blue)}
.qr-box{text-align:center;margin:12px 0}
.qr-box img{width:200px;height:200px;border-radius:14px;border:4px solid #fff;background:#fff}.qr-box svg{width:200px;height:200px;border-radius:14px;border:4px solid #fff;background:#fff}
#toasts{position:fixed;bottom:20px;right:20px;z-index:99;display:flex;flex-direction:column;gap:10px;max-width:min(360px,90vw)}
.toast{background:linear-gradient(180deg,#232c5e,#1a2147);border:1px solid var(--pink);border-radius:12px;padding:12px 16px;box-shadow:var(--shadow);animation:slidein .25s ease;font-size:.88rem}
.toast.err{border-color:var(--red)}
@keyframes slidein{from{opacity:0;transform:translateX(-30px)}to{opacity:1;transform:none}}
.fun{display:flex;gap:12px;align-items:center;background:linear-gradient(90deg,rgba(230,57,70,.18),rgba(74,163,255,.12));border:1px solid rgba(255,123,156,.35);border-radius:var(--radius);padding:14px 18px;margin-bottom:18px;font-size:.92rem}
.fun .face{font-size:1.8rem;animation:float 4s ease-in-out infinite}
.warn-bar{background:rgba(255,159,69,.12);border:1px solid var(--orange);color:var(--orange);border-radius:12px;padding:10px 16px;margin-bottom:16px;font-size:.87rem}
.import-btns{display:flex;gap:8px;flex-wrap:wrap;margin:12px 0}
footer.foot{text-align:center;color:var(--muted);font-size:.78rem;margin-top:34px;opacity:.8}
footer.foot .kanji{font-size:.8rem}
</style>
<script>
/* qrcode-generator (c) Kazuhiko Arase, MIT license — inlined for offline QR rendering */
//---------------------------------------------------------------------
//
// QR Code Generator for JavaScript
//
// Copyright (c) 2009 Kazuhiko Arase
//
// URL: http://www.d-project.com/
//
// Licensed under the MIT license:
//  http://www.opensource.org/licenses/mit-license.php
//
// The word 'QR Code' is registered trademark of
// DENSO WAVE INCORPORATED
//  http://www.denso-wave.com/qrcode/faqpatent-e.html
//
//---------------------------------------------------------------------

var qrcode = function() {

  //---------------------------------------------------------------------
  // qrcode
  //---------------------------------------------------------------------

  /**
   * qrcode
   * @param typeNumber 1 to 40
   * @param errorCorrectionLevel 'L','M','Q','H'
   */
  var qrcode = function(typeNumber, errorCorrectionLevel) {

    var PAD0 = 0xEC;
    var PAD1 = 0x11;

    var _typeNumber = typeNumber;
    var _errorCorrectionLevel = QRErrorCorrectionLevel[errorCorrectionLevel];
    var _modules = null;
    var _moduleCount = 0;
    var _dataCache = null;
    var _dataList = [];

    var _this = {};

    var makeImpl = function(test, maskPattern) {

      _moduleCount = _typeNumber * 4 + 17;
      _modules = function(moduleCount) {
        var modules = new Array(moduleCount);
        for (var row = 0; row < moduleCount; row += 1) {
          modules[row] = new Array(moduleCount);
          for (var col = 0; col < moduleCount; col += 1) {
            modules[row][col] = null;
          }
        }
        return modules;
      }(_moduleCount);

      setupPositionProbePattern(0, 0);
      setupPositionProbePattern(_moduleCount - 7, 0);
      setupPositionProbePattern(0, _moduleCount - 7);
      setupPositionAdjustPattern();
      setupTimingPattern();
      setupTypeInfo(test, maskPattern);

      if (_typeNumber >= 7) {
        setupTypeNumber(test);
      }

      if (_dataCache == null) {
        _dataCache = createData(_typeNumber, _errorCorrectionLevel, _dataList);
      }

      mapData(_dataCache, maskPattern);
    };

    var setupPositionProbePattern = function(row, col) {

      for (var r = -1; r <= 7; r += 1) {

        if (row + r <= -1 || _moduleCount <= row + r) continue;

        for (var c = -1; c <= 7; c += 1) {

          if (col + c <= -1 || _moduleCount <= col + c) continue;

          if ( (0 <= r && r <= 6 && (c == 0 || c == 6) )
              || (0 <= c && c <= 6 && (r == 0 || r == 6) )
              || (2 <= r && r <= 4 && 2 <= c && c <= 4) ) {
            _modules[row + r][col + c] = true;
          } else {
            _modules[row + r][col + c] = false;
          }
        }
      }
    };

    var getBestMaskPattern = function() {

      var minLostPoint = 0;
      var pattern = 0;

      for (var i = 0; i < 8; i += 1) {

        makeImpl(true, i);

        var lostPoint = QRUtil.getLostPoint(_this);

        if (i == 0 || minLostPoint > lostPoint) {
          minLostPoint = lostPoint;
          pattern = i;
        }
      }

      return pattern;
    };

    var setupTimingPattern = function() {

      for (var r = 8; r < _moduleCount - 8; r += 1) {
        if (_modules[r][6] != null) {
          continue;
        }
        _modules[r][6] = (r % 2 == 0);
      }

      for (var c = 8; c < _moduleCount - 8; c += 1) {
        if (_modules[6][c] != null) {
          continue;
        }
        _modules[6][c] = (c % 2 == 0);
      }
    };

    var setupPositionAdjustPattern = function() {

      var pos = QRUtil.getPatternPosition(_typeNumber);

      for (var i = 0; i < pos.length; i += 1) {

        for (var j = 0; j < pos.length; j += 1) {

          var row = pos[i];
          var col = pos[j];

          if (_modules[row][col] != null) {
            continue;
          }

          for (var r = -2; r <= 2; r += 1) {

            for (var c = -2; c <= 2; c += 1) {

              if (r == -2 || r == 2 || c == -2 || c == 2
                  || (r == 0 && c == 0) ) {
                _modules[row + r][col + c] = true;
              } else {
                _modules[row + r][col + c] = false;
              }
            }
          }
        }
      }
    };

    var setupTypeNumber = function(test) {

      var bits = QRUtil.getBCHTypeNumber(_typeNumber);

      for (var i = 0; i < 18; i += 1) {
        var mod = (!test && ( (bits >> i) & 1) == 1);
        _modules[Math.floor(i / 3)][i % 3 + _moduleCount - 8 - 3] = mod;
      }

      for (var i = 0; i < 18; i += 1) {
        var mod = (!test && ( (bits >> i) & 1) == 1);
        _modules[i % 3 + _moduleCount - 8 - 3][Math.floor(i / 3)] = mod;
      }
    };

    var setupTypeInfo = function(test, maskPattern) {

      var data = (_errorCorrectionLevel << 3) | maskPattern;
      var bits = QRUtil.getBCHTypeInfo(data);

      // vertical
      for (var i = 0; i < 15; i += 1) {

        var mod = (!test && ( (bits >> i) & 1) == 1);

        if (i < 6) {
          _modules[i][8] = mod;
        } else if (i < 8) {
          _modules[i + 1][8] = mod;
        } else {
          _modules[_moduleCount - 15 + i][8] = mod;
        }
      }

      // horizontal
      for (var i = 0; i < 15; i += 1) {

        var mod = (!test && ( (bits >> i) & 1) == 1);

        if (i < 8) {
          _modules[8][_moduleCount - i - 1] = mod;
        } else if (i < 9) {
          _modules[8][15 - i - 1 + 1] = mod;
        } else {
          _modules[8][15 - i - 1] = mod;
        }
      }

      // fixed module
      _modules[_moduleCount - 8][8] = (!test);
    };

    var mapData = function(data, maskPattern) {

      var inc = -1;
      var row = _moduleCount - 1;
      var bitIndex = 7;
      var byteIndex = 0;
      var maskFunc = QRUtil.getMaskFunction(maskPattern);

      for (var col = _moduleCount - 1; col > 0; col -= 2) {

        if (col == 6) col -= 1;

        while (true) {

          for (var c = 0; c < 2; c += 1) {

            if (_modules[row][col - c] == null) {

              var dark = false;

              if (byteIndex < data.length) {
                dark = ( ( (data[byteIndex] >>> bitIndex) & 1) == 1);
              }

              var mask = maskFunc(row, col - c);

              if (mask) {
                dark = !dark;
              }

              _modules[row][col - c] = dark;
              bitIndex -= 1;

              if (bitIndex == -1) {
                byteIndex += 1;
                bitIndex = 7;
              }
            }
          }

          row += inc;

          if (row < 0 || _moduleCount <= row) {
            row -= inc;
            inc = -inc;
            break;
          }
        }
      }
    };

    var createBytes = function(buffer, rsBlocks) {

      var offset = 0;

      var maxDcCount = 0;
      var maxEcCount = 0;

      var dcdata = new Array(rsBlocks.length);
      var ecdata = new Array(rsBlocks.length);

      for (var r = 0; r < rsBlocks.length; r += 1) {

        var dcCount = rsBlocks[r].dataCount;
        var ecCount = rsBlocks[r].totalCount - dcCount;

        maxDcCount = Math.max(maxDcCount, dcCount);
        maxEcCount = Math.max(maxEcCount, ecCount);

        dcdata[r] = new Array(dcCount);

        for (var i = 0; i < dcdata[r].length; i += 1) {
          dcdata[r][i] = 0xff & buffer.getBuffer()[i + offset];
        }
        offset += dcCount;

        var rsPoly = QRUtil.getErrorCorrectPolynomial(ecCount);
        var rawPoly = qrPolynomial(dcdata[r], rsPoly.getLength() - 1);

        var modPoly = rawPoly.mod(rsPoly);
        ecdata[r] = new Array(rsPoly.getLength() - 1);
        for (var i = 0; i < ecdata[r].length; i += 1) {
          var modIndex = i + modPoly.getLength() - ecdata[r].length;
          ecdata[r][i] = (modIndex >= 0)? modPoly.getAt(modIndex) : 0;
        }
      }

      var totalCodeCount = 0;
      for (var i = 0; i < rsBlocks.length; i += 1) {
        totalCodeCount += rsBlocks[i].totalCount;
      }

      var data = new Array(totalCodeCount);
      var index = 0;

      for (var i = 0; i < maxDcCount; i += 1) {
        for (var r = 0; r < rsBlocks.length; r += 1) {
          if (i < dcdata[r].length) {
            data[index] = dcdata[r][i];
            index += 1;
          }
        }
      }

      for (var i = 0; i < maxEcCount; i += 1) {
        for (var r = 0; r < rsBlocks.length; r += 1) {
          if (i < ecdata[r].length) {
            data[index] = ecdata[r][i];
            index += 1;
          }
        }
      }

      return data;
    };

    var createData = function(typeNumber, errorCorrectionLevel, dataList) {

      var rsBlocks = QRRSBlock.getRSBlocks(typeNumber, errorCorrectionLevel);

      var buffer = qrBitBuffer();

      for (var i = 0; i < dataList.length; i += 1) {
        var data = dataList[i];
        buffer.put(data.getMode(), 4);
        buffer.put(data.getLength(), QRUtil.getLengthInBits(data.getMode(), typeNumber) );
        data.write(buffer);
      }

      // calc num max data.
      var totalDataCount = 0;
      for (var i = 0; i < rsBlocks.length; i += 1) {
        totalDataCount += rsBlocks[i].dataCount;
      }

      if (buffer.getLengthInBits() > totalDataCount * 8) {
        throw 'code length overflow. ('
          + buffer.getLengthInBits()
          + '>'
          + totalDataCount * 8
          + ')';
      }

      // end code
      if (buffer.getLengthInBits() + 4 <= totalDataCount * 8) {
        buffer.put(0, 4);
      }

      // padding
      while (buffer.getLengthInBits() % 8 != 0) {
        buffer.putBit(false);
      }

      // padding
      while (true) {

        if (buffer.getLengthInBits() >= totalDataCount * 8) {
          break;
        }
        buffer.put(PAD0, 8);

        if (buffer.getLengthInBits() >= totalDataCount * 8) {
          break;
        }
        buffer.put(PAD1, 8);
      }

      return createBytes(buffer, rsBlocks);
    };

    _this.addData = function(data, mode) {

      mode = mode || 'Byte';

      var newData = null;

      switch(mode) {
      case 'Numeric' :
        newData = qrNumber(data);
        break;
      case 'Alphanumeric' :
        newData = qrAlphaNum(data);
        break;
      case 'Byte' :
        newData = qr8BitByte(data);
        break;
      case 'Kanji' :
        newData = qrKanji(data);
        break;
      default :
        throw 'mode:' + mode;
      }

      _dataList.push(newData);
      _dataCache = null;
    };

    _this.isDark = function(row, col) {
      if (row < 0 || _moduleCount <= row || col < 0 || _moduleCount <= col) {
        throw row + ',' + col;
      }
      return _modules[row][col];
    };

    _this.getModuleCount = function() {
      return _moduleCount;
    };

    _this.make = function() {
      if (_typeNumber < 1) {
        var typeNumber = 1;

        for (; typeNumber < 40; typeNumber++) {
          var rsBlocks = QRRSBlock.getRSBlocks(typeNumber, _errorCorrectionLevel);
          var buffer = qrBitBuffer();

          for (var i = 0; i < _dataList.length; i++) {
            var data = _dataList[i];
            buffer.put(data.getMode(), 4);
            buffer.put(data.getLength(), QRUtil.getLengthInBits(data.getMode(), typeNumber) );
            data.write(buffer);
          }

          var totalDataCount = 0;
          for (var i = 0; i < rsBlocks.length; i++) {
            totalDataCount += rsBlocks[i].dataCount;
          }

          if (buffer.getLengthInBits() <= totalDataCount * 8) {
            break;
          }
        }

        _typeNumber = typeNumber;
      }

      makeImpl(false, getBestMaskPattern() );
    };

    _this.createTableTag = function(cellSize, margin) {

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      var qrHtml = '';

      qrHtml += '<table style="';
      qrHtml += ' border-width: 0px; border-style: none;';
      qrHtml += ' border-collapse: collapse;';
      qrHtml += ' padding: 0px; margin: ' + margin + 'px;';
      qrHtml += '">';
      qrHtml += '<tbody>';

      for (var r = 0; r < _this.getModuleCount(); r += 1) {

        qrHtml += '<tr>';

        for (var c = 0; c < _this.getModuleCount(); c += 1) {
          qrHtml += '<td style="';
          qrHtml += ' border-width: 0px; border-style: none;';
          qrHtml += ' border-collapse: collapse;';
          qrHtml += ' padding: 0px; margin: 0px;';
          qrHtml += ' width: ' + cellSize + 'px;';
          qrHtml += ' height: ' + cellSize + 'px;';
          qrHtml += ' background-color: ';
          qrHtml += _this.isDark(r, c)? '#000000' : '#ffffff';
          qrHtml += ';';
          qrHtml += '"/>';
        }

        qrHtml += '</tr>';
      }

      qrHtml += '</tbody>';
      qrHtml += '</table>';

      return qrHtml;
    };

    _this.createSvgTag = function(cellSize, margin, alt, title) {

      var opts = {};
      if (typeof arguments[0] == 'object') {
        // Called by options.
        opts = arguments[0];
        // overwrite cellSize and margin.
        cellSize = opts.cellSize;
        margin = opts.margin;
        alt = opts.alt;
        title = opts.title;
      }

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      // Compose alt property surrogate
      alt = (typeof alt === 'string') ? {text: alt} : alt || {};
      alt.text = alt.text || null;
      alt.id = (alt.text) ? alt.id || 'qrcode-description' : null;

      // Compose title property surrogate
      title = (typeof title === 'string') ? {text: title} : title || {};
      title.text = title.text || null;
      title.id = (title.text) ? title.id || 'qrcode-title' : null;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var c, mc, r, mr, qrSvg='', rect;

      rect = 'l' + cellSize + ',0 0,' + cellSize +
        ' -' + cellSize + ',0 0,-' + cellSize + 'z ';

      qrSvg += '<svg version="1.1" xmlns="http://www.w3.org/2000/svg"';
      qrSvg += !opts.scalable ? ' width="' + size + 'px" height="' + size + 'px"' : '';
      qrSvg += ' viewBox="0 0 ' + size + ' ' + size + '" ';
      qrSvg += ' preserveAspectRatio="xMinYMin meet"';
      qrSvg += (title.text || alt.text) ? ' role="img" aria-labelledby="' +
          escapeXml([title.id, alt.id].join(' ').trim() ) + '"' : '';
      qrSvg += '>';
      qrSvg += (title.text) ? '<title id="' + escapeXml(title.id) + '">' +
          escapeXml(title.text) + '</title>' : '';
      qrSvg += (alt.text) ? '<description id="' + escapeXml(alt.id) + '">' +
          escapeXml(alt.text) + '</description>' : '';
      qrSvg += '<rect width="100%" height="100%" fill="white" cx="0" cy="0"/>';
      qrSvg += '<path d="';

      for (r = 0; r < _this.getModuleCount(); r += 1) {
        mr = r * cellSize + margin;
        for (c = 0; c < _this.getModuleCount(); c += 1) {
          if (_this.isDark(r, c) ) {
            mc = c*cellSize+margin;
            qrSvg += 'M' + mc + ',' + mr + rect;
          }
        }
      }

      qrSvg += '" stroke="transparent" fill="black"/>';
      qrSvg += '</svg>';

      return qrSvg;
    };

    _this.createDataURL = function(cellSize, margin) {

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var min = margin;
      var max = size - margin;

      return createDataURL(size, size, function(x, y) {
        if (min <= x && x < max && min <= y && y < max) {
          var c = Math.floor( (x - min) / cellSize);
          var r = Math.floor( (y - min) / cellSize);
          return _this.isDark(r, c)? 0 : 1;
        } else {
          return 1;
        }
      } );
    };

    _this.createImgTag = function(cellSize, margin, alt) {

      cellSize = cellSize || 2;
      margin = (typeof margin == 'undefined')? cellSize * 4 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;

      var img = '';
      img += '<img';
      img += '\u0020src="';
      img += _this.createDataURL(cellSize, margin);
      img += '"';
      img += '\u0020width="';
      img += size;
      img += '"';
      img += '\u0020height="';
      img += size;
      img += '"';
      if (alt) {
        img += '\u0020alt="';
        img += escapeXml(alt);
        img += '"';
      }
      img += '/>';

      return img;
    };

    var escapeXml = function(s) {
      var escaped = '';
      for (var i = 0; i < s.length; i += 1) {
        var c = s.charAt(i);
        switch(c) {
        case '<': escaped += '&lt;'; break;
        case '>': escaped += '&gt;'; break;
        case '&': escaped += '&amp;'; break;
        case '"': escaped += '&quot;'; break;
        default : escaped += c; break;
        }
      }
      return escaped;
    };

    var _createHalfASCII = function(margin) {
      var cellSize = 1;
      margin = (typeof margin == 'undefined')? cellSize * 2 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var min = margin;
      var max = size - margin;

      var y, x, r1, r2, p;

      var blocks = {
        '██': '█',
        '█ ': '▀',
        ' █': '▄',
        '  ': ' '
      };

      var blocksLastLineNoMargin = {
        '██': '▀',
        '█ ': '▀',
        ' █': ' ',
        '  ': ' '
      };

      var ascii = '';
      for (y = 0; y < size; y += 2) {
        r1 = Math.floor((y - min) / cellSize);
        r2 = Math.floor((y + 1 - min) / cellSize);
        for (x = 0; x < size; x += 1) {
          p = '█';

          if (min <= x && x < max && min <= y && y < max && _this.isDark(r1, Math.floor((x - min) / cellSize))) {
            p = ' ';
          }

          if (min <= x && x < max && min <= y+1 && y+1 < max && _this.isDark(r2, Math.floor((x - min) / cellSize))) {
            p += ' ';
          }
          else {
            p += '█';
          }

          // Output 2 characters per pixel, to create full square. 1 character per pixels gives only half width of square.
          ascii += (margin < 1 && y+1 >= max) ? blocksLastLineNoMargin[p] : blocks[p];
        }

        ascii += '\n';
      }

      if (size % 2 && margin > 0) {
        return ascii.substring(0, ascii.length - size - 1) + Array(size+1).join('▀');
      }

      return ascii.substring(0, ascii.length-1);
    };

    _this.createASCII = function(cellSize, margin) {
      cellSize = cellSize || 1;

      if (cellSize < 2) {
        return _createHalfASCII(margin);
      }

      cellSize -= 1;
      margin = (typeof margin == 'undefined')? cellSize * 2 : margin;

      var size = _this.getModuleCount() * cellSize + margin * 2;
      var min = margin;
      var max = size - margin;

      var y, x, r, p;

      var white = Array(cellSize+1).join('██');
      var black = Array(cellSize+1).join('  ');

      var ascii = '';
      var line = '';
      for (y = 0; y < size; y += 1) {
        r = Math.floor( (y - min) / cellSize);
        line = '';
        for (x = 0; x < size; x += 1) {
          p = 1;

          if (min <= x && x < max && min <= y && y < max && _this.isDark(r, Math.floor((x - min) / cellSize))) {
            p = 0;
          }

          // Output 2 characters per pixel, to create full square. 1 character per pixels gives only half width of square.
          line += p ? white : black;
        }

        for (r = 0; r < cellSize; r += 1) {
          ascii += line + '\n';
        }
      }

      return ascii.substring(0, ascii.length-1);
    };

    _this.renderTo2dContext = function(context, cellSize) {
      cellSize = cellSize || 2;
      var length = _this.getModuleCount();
      for (var row = 0; row < length; row++) {
        for (var col = 0; col < length; col++) {
          context.fillStyle = _this.isDark(row, col) ? 'black' : 'white';
          context.fillRect(col * cellSize, row * cellSize, cellSize, cellSize);
        }
      }
    }

    return _this;
  };

  //---------------------------------------------------------------------
  // qrcode.stringToBytes
  //---------------------------------------------------------------------

  qrcode.stringToBytesFuncs = {
    'default' : function(s) {
      var bytes = [];
      for (var i = 0; i < s.length; i += 1) {
        var c = s.charCodeAt(i);
        bytes.push(c & 0xff);
      }
      return bytes;
    }
  };

  qrcode.stringToBytes = qrcode.stringToBytesFuncs['default'];

  //---------------------------------------------------------------------
  // qrcode.createStringToBytes
  //---------------------------------------------------------------------

  /**
   * @param unicodeData base64 string of byte array.
   * [16bit Unicode],[16bit Bytes], ...
   * @param numChars
   */
  qrcode.createStringToBytes = function(unicodeData, numChars) {

    // create conversion map.

    var unicodeMap = function() {

      var bin = base64DecodeInputStream(unicodeData);
      var read = function() {
        var b = bin.read();
        if (b == -1) throw 'eof';
        return b;
      };

      var count = 0;
      var unicodeMap = {};
      while (true) {
        var b0 = bin.read();
        if (b0 == -1) break;
        var b1 = read();
        var b2 = read();
        var b3 = read();
        var k = String.fromCharCode( (b0 << 8) | b1);
        var v = (b2 << 8) | b3;
        unicodeMap[k] = v;
        count += 1;
      }
      if (count != numChars) {
        throw count + ' != ' + numChars;
      }

      return unicodeMap;
    }();

    var unknownChar = '?'.charCodeAt(0);

    return function(s) {
      var bytes = [];
      for (var i = 0; i < s.length; i += 1) {
        var c = s.charCodeAt(i);
        if (c < 128) {
          bytes.push(c);
        } else {
          var b = unicodeMap[s.charAt(i)];
          if (typeof b == 'number') {
            if ( (b & 0xff) == b) {
              // 1byte
              bytes.push(b);
            } else {
              // 2bytes
              bytes.push(b >>> 8);
              bytes.push(b & 0xff);
            }
          } else {
            bytes.push(unknownChar);
          }
        }
      }
      return bytes;
    };
  };

  //---------------------------------------------------------------------
  // QRMode
  //---------------------------------------------------------------------

  var QRMode = {
    MODE_NUMBER :    1 << 0,
    MODE_ALPHA_NUM : 1 << 1,
    MODE_8BIT_BYTE : 1 << 2,
    MODE_KANJI :     1 << 3
  };

  //---------------------------------------------------------------------
  // QRErrorCorrectionLevel
  //---------------------------------------------------------------------

  var QRErrorCorrectionLevel = {
    L : 1,
    M : 0,
    Q : 3,
    H : 2
  };

  //---------------------------------------------------------------------
  // QRMaskPattern
  //---------------------------------------------------------------------

  var QRMaskPattern = {
    PATTERN000 : 0,
    PATTERN001 : 1,
    PATTERN010 : 2,
    PATTERN011 : 3,
    PATTERN100 : 4,
    PATTERN101 : 5,
    PATTERN110 : 6,
    PATTERN111 : 7
  };

  //---------------------------------------------------------------------
  // QRUtil
  //---------------------------------------------------------------------

  var QRUtil = function() {

    var PATTERN_POSITION_TABLE = [
      [],
      [6, 18],
      [6, 22],
      [6, 26],
      [6, 30],
      [6, 34],
      [6, 22, 38],
      [6, 24, 42],
      [6, 26, 46],
      [6, 28, 50],
      [6, 30, 54],
      [6, 32, 58],
      [6, 34, 62],
      [6, 26, 46, 66],
      [6, 26, 48, 70],
      [6, 26, 50, 74],
      [6, 30, 54, 78],
      [6, 30, 56, 82],
      [6, 30, 58, 86],
      [6, 34, 62, 90],
      [6, 28, 50, 72, 94],
      [6, 26, 50, 74, 98],
      [6, 30, 54, 78, 102],
      [6, 28, 54, 80, 106],
      [6, 32, 58, 84, 110],
      [6, 30, 58, 86, 114],
      [6, 34, 62, 90, 118],
      [6, 26, 50, 74, 98, 122],
      [6, 30, 54, 78, 102, 126],
      [6, 26, 52, 78, 104, 130],
      [6, 30, 56, 82, 108, 134],
      [6, 34, 60, 86, 112, 138],
      [6, 30, 58, 86, 114, 142],
      [6, 34, 62, 90, 118, 146],
      [6, 30, 54, 78, 102, 126, 150],
      [6, 24, 50, 76, 102, 128, 154],
      [6, 28, 54, 80, 106, 132, 158],
      [6, 32, 58, 84, 110, 136, 162],
      [6, 26, 54, 82, 110, 138, 166],
      [6, 30, 58, 86, 114, 142, 170]
    ];
    var G15 = (1 << 10) | (1 << 8) | (1 << 5) | (1 << 4) | (1 << 2) | (1 << 1) | (1 << 0);
    var G18 = (1 << 12) | (1 << 11) | (1 << 10) | (1 << 9) | (1 << 8) | (1 << 5) | (1 << 2) | (1 << 0);
    var G15_MASK = (1 << 14) | (1 << 12) | (1 << 10) | (1 << 4) | (1 << 1);

    var _this = {};

    var getBCHDigit = function(data) {
      var digit = 0;
      while (data != 0) {
        digit += 1;
        data >>>= 1;
      }
      return digit;
    };

    _this.getBCHTypeInfo = function(data) {
      var d = data << 10;
      while (getBCHDigit(d) - getBCHDigit(G15) >= 0) {
        d ^= (G15 << (getBCHDigit(d) - getBCHDigit(G15) ) );
      }
      return ( (data << 10) | d) ^ G15_MASK;
    };

    _this.getBCHTypeNumber = function(data) {
      var d = data << 12;
      while (getBCHDigit(d) - getBCHDigit(G18) >= 0) {
        d ^= (G18 << (getBCHDigit(d) - getBCHDigit(G18) ) );
      }
      return (data << 12) | d;
    };

    _this.getPatternPosition = function(typeNumber) {
      return PATTERN_POSITION_TABLE[typeNumber - 1];
    };

    _this.getMaskFunction = function(maskPattern) {

      switch (maskPattern) {

      case QRMaskPattern.PATTERN000 :
        return function(i, j) { return (i + j) % 2 == 0; };
      case QRMaskPattern.PATTERN001 :
        return function(i, j) { return i % 2 == 0; };
      case QRMaskPattern.PATTERN010 :
        return function(i, j) { return j % 3 == 0; };
      case QRMaskPattern.PATTERN011 :
        return function(i, j) { return (i + j) % 3 == 0; };
      case QRMaskPattern.PATTERN100 :
        return function(i, j) { return (Math.floor(i / 2) + Math.floor(j / 3) ) % 2 == 0; };
      case QRMaskPattern.PATTERN101 :
        return function(i, j) { return (i * j) % 2 + (i * j) % 3 == 0; };
      case QRMaskPattern.PATTERN110 :
        return function(i, j) { return ( (i * j) % 2 + (i * j) % 3) % 2 == 0; };
      case QRMaskPattern.PATTERN111 :
        return function(i, j) { return ( (i * j) % 3 + (i + j) % 2) % 2 == 0; };

      default :
        throw 'bad maskPattern:' + maskPattern;
      }
    };

    _this.getErrorCorrectPolynomial = function(errorCorrectLength) {
      var a = qrPolynomial([1], 0);
      for (var i = 0; i < errorCorrectLength; i += 1) {
        a = a.multiply(qrPolynomial([1, QRMath.gexp(i)], 0) );
      }
      return a;
    };

    _this.getLengthInBits = function(mode, type) {

      if (1 <= type && type < 10) {

        // 1 - 9

        switch(mode) {
        case QRMode.MODE_NUMBER    : return 10;
        case QRMode.MODE_ALPHA_NUM : return 9;
        case QRMode.MODE_8BIT_BYTE : return 8;
        case QRMode.MODE_KANJI     : return 8;
        default :
          throw 'mode:' + mode;
        }

      } else if (type < 27) {

        // 10 - 26

        switch(mode) {
        case QRMode.MODE_NUMBER    : return 12;
        case QRMode.MODE_ALPHA_NUM : return 11;
        case QRMode.MODE_8BIT_BYTE : return 16;
        case QRMode.MODE_KANJI     : return 10;
        default :
          throw 'mode:' + mode;
        }

      } else if (type < 41) {

        // 27 - 40

        switch(mode) {
        case QRMode.MODE_NUMBER    : return 14;
        case QRMode.MODE_ALPHA_NUM : return 13;
        case QRMode.MODE_8BIT_BYTE : return 16;
        case QRMode.MODE_KANJI     : return 12;
        default :
          throw 'mode:' + mode;
        }

      } else {
        throw 'type:' + type;
      }
    };

    _this.getLostPoint = function(qrcode) {

      var moduleCount = qrcode.getModuleCount();

      var lostPoint = 0;

      // LEVEL1

      for (var row = 0; row < moduleCount; row += 1) {
        for (var col = 0; col < moduleCount; col += 1) {

          var sameCount = 0;
          var dark = qrcode.isDark(row, col);

          for (var r = -1; r <= 1; r += 1) {

            if (row + r < 0 || moduleCount <= row + r) {
              continue;
            }

            for (var c = -1; c <= 1; c += 1) {

              if (col + c < 0 || moduleCount <= col + c) {
                continue;
              }

              if (r == 0 && c == 0) {
                continue;
              }

              if (dark == qrcode.isDark(row + r, col + c) ) {
                sameCount += 1;
              }
            }
          }

          if (sameCount > 5) {
            lostPoint += (3 + sameCount - 5);
          }
        }
      };

      // LEVEL2

      for (var row = 0; row < moduleCount - 1; row += 1) {
        for (var col = 0; col < moduleCount - 1; col += 1) {
          var count = 0;
          if (qrcode.isDark(row, col) ) count += 1;
          if (qrcode.isDark(row + 1, col) ) count += 1;
          if (qrcode.isDark(row, col + 1) ) count += 1;
          if (qrcode.isDark(row + 1, col + 1) ) count += 1;
          if (count == 0 || count == 4) {
            lostPoint += 3;
          }
        }
      }

      // LEVEL3

      for (var row = 0; row < moduleCount; row += 1) {
        for (var col = 0; col < moduleCount - 6; col += 1) {
          if (qrcode.isDark(row, col)
              && !qrcode.isDark(row, col + 1)
              &&  qrcode.isDark(row, col + 2)
              &&  qrcode.isDark(row, col + 3)
              &&  qrcode.isDark(row, col + 4)
              && !qrcode.isDark(row, col + 5)
              &&  qrcode.isDark(row, col + 6) ) {
            lostPoint += 40;
          }
        }
      }

      for (var col = 0; col < moduleCount; col += 1) {
        for (var row = 0; row < moduleCount - 6; row += 1) {
          if (qrcode.isDark(row, col)
              && !qrcode.isDark(row + 1, col)
              &&  qrcode.isDark(row + 2, col)
              &&  qrcode.isDark(row + 3, col)
              &&  qrcode.isDark(row + 4, col)
              && !qrcode.isDark(row + 5, col)
              &&  qrcode.isDark(row + 6, col) ) {
            lostPoint += 40;
          }
        }
      }

      // LEVEL4

      var darkCount = 0;

      for (var col = 0; col < moduleCount; col += 1) {
        for (var row = 0; row < moduleCount; row += 1) {
          if (qrcode.isDark(row, col) ) {
            darkCount += 1;
          }
        }
      }

      var ratio = Math.abs(100 * darkCount / moduleCount / moduleCount - 50) / 5;
      lostPoint += ratio * 10;

      return lostPoint;
    };

    return _this;
  }();

  //---------------------------------------------------------------------
  // QRMath
  //---------------------------------------------------------------------

  var QRMath = function() {

    var EXP_TABLE = new Array(256);
    var LOG_TABLE = new Array(256);

    // initialize tables
    for (var i = 0; i < 8; i += 1) {
      EXP_TABLE[i] = 1 << i;
    }
    for (var i = 8; i < 256; i += 1) {
      EXP_TABLE[i] = EXP_TABLE[i - 4]
        ^ EXP_TABLE[i - 5]
        ^ EXP_TABLE[i - 6]
        ^ EXP_TABLE[i - 8];
    }
    for (var i = 0; i < 255; i += 1) {
      LOG_TABLE[EXP_TABLE[i] ] = i;
    }

    var _this = {};

    _this.glog = function(n) {

      if (n < 1) {
        throw 'glog(' + n + ')';
      }

      return LOG_TABLE[n];
    };

    _this.gexp = function(n) {

      while (n < 0) {
        n += 255;
      }

      while (n >= 256) {
        n -= 255;
      }

      return EXP_TABLE[n];
    };

    return _this;
  }();

  //---------------------------------------------------------------------
  // qrPolynomial
  //---------------------------------------------------------------------

  function qrPolynomial(num, shift) {

    if (typeof num.length == 'undefined') {
      throw num.length + '/' + shift;
    }

    var _num = function() {
      var offset = 0;
      while (offset < num.length && num[offset] == 0) {
        offset += 1;
      }
      var _num = new Array(num.length - offset + shift);
      for (var i = 0; i < num.length - offset; i += 1) {
        _num[i] = num[i + offset];
      }
      return _num;
    }();

    var _this = {};

    _this.getAt = function(index) {
      return _num[index];
    };

    _this.getLength = function() {
      return _num.length;
    };

    _this.multiply = function(e) {

      var num = new Array(_this.getLength() + e.getLength() - 1);

      for (var i = 0; i < _this.getLength(); i += 1) {
        for (var j = 0; j < e.getLength(); j += 1) {
          num[i + j] ^= QRMath.gexp(QRMath.glog(_this.getAt(i) ) + QRMath.glog(e.getAt(j) ) );
        }
      }

      return qrPolynomial(num, 0);
    };

    _this.mod = function(e) {

      if (_this.getLength() - e.getLength() < 0) {
        return _this;
      }

      var ratio = QRMath.glog(_this.getAt(0) ) - QRMath.glog(e.getAt(0) );

      var num = new Array(_this.getLength() );
      for (var i = 0; i < _this.getLength(); i += 1) {
        num[i] = _this.getAt(i);
      }

      for (var i = 0; i < e.getLength(); i += 1) {
        num[i] ^= QRMath.gexp(QRMath.glog(e.getAt(i) ) + ratio);
      }

      // recursive call
      return qrPolynomial(num, 0).mod(e);
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // QRRSBlock
  //---------------------------------------------------------------------

  var QRRSBlock = function() {

    var RS_BLOCK_TABLE = [

      // L
      // M
      // Q
      // H

      // 1
      [1, 26, 19],
      [1, 26, 16],
      [1, 26, 13],
      [1, 26, 9],

      // 2
      [1, 44, 34],
      [1, 44, 28],
      [1, 44, 22],
      [1, 44, 16],

      // 3
      [1, 70, 55],
      [1, 70, 44],
      [2, 35, 17],
      [2, 35, 13],

      // 4
      [1, 100, 80],
      [2, 50, 32],
      [2, 50, 24],
      [4, 25, 9],

      // 5
      [1, 134, 108],
      [2, 67, 43],
      [2, 33, 15, 2, 34, 16],
      [2, 33, 11, 2, 34, 12],

      // 6
      [2, 86, 68],
      [4, 43, 27],
      [4, 43, 19],
      [4, 43, 15],

      // 7
      [2, 98, 78],
      [4, 49, 31],
      [2, 32, 14, 4, 33, 15],
      [4, 39, 13, 1, 40, 14],

      // 8
      [2, 121, 97],
      [2, 60, 38, 2, 61, 39],
      [4, 40, 18, 2, 41, 19],
      [4, 40, 14, 2, 41, 15],

      // 9
      [2, 146, 116],
      [3, 58, 36, 2, 59, 37],
      [4, 36, 16, 4, 37, 17],
      [4, 36, 12, 4, 37, 13],

      // 10
      [2, 86, 68, 2, 87, 69],
      [4, 69, 43, 1, 70, 44],
      [6, 43, 19, 2, 44, 20],
      [6, 43, 15, 2, 44, 16],

      // 11
      [4, 101, 81],
      [1, 80, 50, 4, 81, 51],
      [4, 50, 22, 4, 51, 23],
      [3, 36, 12, 8, 37, 13],

      // 12
      [2, 116, 92, 2, 117, 93],
      [6, 58, 36, 2, 59, 37],
      [4, 46, 20, 6, 47, 21],
      [7, 42, 14, 4, 43, 15],

      // 13
      [4, 133, 107],
      [8, 59, 37, 1, 60, 38],
      [8, 44, 20, 4, 45, 21],
      [12, 33, 11, 4, 34, 12],

      // 14
      [3, 145, 115, 1, 146, 116],
      [4, 64, 40, 5, 65, 41],
      [11, 36, 16, 5, 37, 17],
      [11, 36, 12, 5, 37, 13],

      // 15
      [5, 109, 87, 1, 110, 88],
      [5, 65, 41, 5, 66, 42],
      [5, 54, 24, 7, 55, 25],
      [11, 36, 12, 7, 37, 13],

      // 16
      [5, 122, 98, 1, 123, 99],
      [7, 73, 45, 3, 74, 46],
      [15, 43, 19, 2, 44, 20],
      [3, 45, 15, 13, 46, 16],

      // 17
      [1, 135, 107, 5, 136, 108],
      [10, 74, 46, 1, 75, 47],
      [1, 50, 22, 15, 51, 23],
      [2, 42, 14, 17, 43, 15],

      // 18
      [5, 150, 120, 1, 151, 121],
      [9, 69, 43, 4, 70, 44],
      [17, 50, 22, 1, 51, 23],
      [2, 42, 14, 19, 43, 15],

      // 19
      [3, 141, 113, 4, 142, 114],
      [3, 70, 44, 11, 71, 45],
      [17, 47, 21, 4, 48, 22],
      [9, 39, 13, 16, 40, 14],

      // 20
      [3, 135, 107, 5, 136, 108],
      [3, 67, 41, 13, 68, 42],
      [15, 54, 24, 5, 55, 25],
      [15, 43, 15, 10, 44, 16],

      // 21
      [4, 144, 116, 4, 145, 117],
      [17, 68, 42],
      [17, 50, 22, 6, 51, 23],
      [19, 46, 16, 6, 47, 17],

      // 22
      [2, 139, 111, 7, 140, 112],
      [17, 74, 46],
      [7, 54, 24, 16, 55, 25],
      [34, 37, 13],

      // 23
      [4, 151, 121, 5, 152, 122],
      [4, 75, 47, 14, 76, 48],
      [11, 54, 24, 14, 55, 25],
      [16, 45, 15, 14, 46, 16],

      // 24
      [6, 147, 117, 4, 148, 118],
      [6, 73, 45, 14, 74, 46],
      [11, 54, 24, 16, 55, 25],
      [30, 46, 16, 2, 47, 17],

      // 25
      [8, 132, 106, 4, 133, 107],
      [8, 75, 47, 13, 76, 48],
      [7, 54, 24, 22, 55, 25],
      [22, 45, 15, 13, 46, 16],

      // 26
      [10, 142, 114, 2, 143, 115],
      [19, 74, 46, 4, 75, 47],
      [28, 50, 22, 6, 51, 23],
      [33, 46, 16, 4, 47, 17],

      // 27
      [8, 152, 122, 4, 153, 123],
      [22, 73, 45, 3, 74, 46],
      [8, 53, 23, 26, 54, 24],
      [12, 45, 15, 28, 46, 16],

      // 28
      [3, 147, 117, 10, 148, 118],
      [3, 73, 45, 23, 74, 46],
      [4, 54, 24, 31, 55, 25],
      [11, 45, 15, 31, 46, 16],

      // 29
      [7, 146, 116, 7, 147, 117],
      [21, 73, 45, 7, 74, 46],
      [1, 53, 23, 37, 54, 24],
      [19, 45, 15, 26, 46, 16],

      // 30
      [5, 145, 115, 10, 146, 116],
      [19, 75, 47, 10, 76, 48],
      [15, 54, 24, 25, 55, 25],
      [23, 45, 15, 25, 46, 16],

      // 31
      [13, 145, 115, 3, 146, 116],
      [2, 74, 46, 29, 75, 47],
      [42, 54, 24, 1, 55, 25],
      [23, 45, 15, 28, 46, 16],

      // 32
      [17, 145, 115],
      [10, 74, 46, 23, 75, 47],
      [10, 54, 24, 35, 55, 25],
      [19, 45, 15, 35, 46, 16],

      // 33
      [17, 145, 115, 1, 146, 116],
      [14, 74, 46, 21, 75, 47],
      [29, 54, 24, 19, 55, 25],
      [11, 45, 15, 46, 46, 16],

      // 34
      [13, 145, 115, 6, 146, 116],
      [14, 74, 46, 23, 75, 47],
      [44, 54, 24, 7, 55, 25],
      [59, 46, 16, 1, 47, 17],

      // 35
      [12, 151, 121, 7, 152, 122],
      [12, 75, 47, 26, 76, 48],
      [39, 54, 24, 14, 55, 25],
      [22, 45, 15, 41, 46, 16],

      // 36
      [6, 151, 121, 14, 152, 122],
      [6, 75, 47, 34, 76, 48],
      [46, 54, 24, 10, 55, 25],
      [2, 45, 15, 64, 46, 16],

      // 37
      [17, 152, 122, 4, 153, 123],
      [29, 74, 46, 14, 75, 47],
      [49, 54, 24, 10, 55, 25],
      [24, 45, 15, 46, 46, 16],

      // 38
      [4, 152, 122, 18, 153, 123],
      [13, 74, 46, 32, 75, 47],
      [48, 54, 24, 14, 55, 25],
      [42, 45, 15, 32, 46, 16],

      // 39
      [20, 147, 117, 4, 148, 118],
      [40, 75, 47, 7, 76, 48],
      [43, 54, 24, 22, 55, 25],
      [10, 45, 15, 67, 46, 16],

      // 40
      [19, 148, 118, 6, 149, 119],
      [18, 75, 47, 31, 76, 48],
      [34, 54, 24, 34, 55, 25],
      [20, 45, 15, 61, 46, 16]
    ];

    var qrRSBlock = function(totalCount, dataCount) {
      var _this = {};
      _this.totalCount = totalCount;
      _this.dataCount = dataCount;
      return _this;
    };

    var _this = {};

    var getRsBlockTable = function(typeNumber, errorCorrectionLevel) {

      switch(errorCorrectionLevel) {
      case QRErrorCorrectionLevel.L :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 0];
      case QRErrorCorrectionLevel.M :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 1];
      case QRErrorCorrectionLevel.Q :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 2];
      case QRErrorCorrectionLevel.H :
        return RS_BLOCK_TABLE[(typeNumber - 1) * 4 + 3];
      default :
        return undefined;
      }
    };

    _this.getRSBlocks = function(typeNumber, errorCorrectionLevel) {

      var rsBlock = getRsBlockTable(typeNumber, errorCorrectionLevel);

      if (typeof rsBlock == 'undefined') {
        throw 'bad rs block @ typeNumber:' + typeNumber +
            '/errorCorrectionLevel:' + errorCorrectionLevel;
      }

      var length = rsBlock.length / 3;

      var list = [];

      for (var i = 0; i < length; i += 1) {

        var count = rsBlock[i * 3 + 0];
        var totalCount = rsBlock[i * 3 + 1];
        var dataCount = rsBlock[i * 3 + 2];

        for (var j = 0; j < count; j += 1) {
          list.push(qrRSBlock(totalCount, dataCount) );
        }
      }

      return list;
    };

    return _this;
  }();

  //---------------------------------------------------------------------
  // qrBitBuffer
  //---------------------------------------------------------------------

  var qrBitBuffer = function() {

    var _buffer = [];
    var _length = 0;

    var _this = {};

    _this.getBuffer = function() {
      return _buffer;
    };

    _this.getAt = function(index) {
      var bufIndex = Math.floor(index / 8);
      return ( (_buffer[bufIndex] >>> (7 - index % 8) ) & 1) == 1;
    };

    _this.put = function(num, length) {
      for (var i = 0; i < length; i += 1) {
        _this.putBit( ( (num >>> (length - i - 1) ) & 1) == 1);
      }
    };

    _this.getLengthInBits = function() {
      return _length;
    };

    _this.putBit = function(bit) {

      var bufIndex = Math.floor(_length / 8);
      if (_buffer.length <= bufIndex) {
        _buffer.push(0);
      }

      if (bit) {
        _buffer[bufIndex] |= (0x80 >>> (_length % 8) );
      }

      _length += 1;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qrNumber
  //---------------------------------------------------------------------

  var qrNumber = function(data) {

    var _mode = QRMode.MODE_NUMBER;
    var _data = data;

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return _data.length;
    };

    _this.write = function(buffer) {

      var data = _data;

      var i = 0;

      while (i + 2 < data.length) {
        buffer.put(strToNum(data.substring(i, i + 3) ), 10);
        i += 3;
      }

      if (i < data.length) {
        if (data.length - i == 1) {
          buffer.put(strToNum(data.substring(i, i + 1) ), 4);
        } else if (data.length - i == 2) {
          buffer.put(strToNum(data.substring(i, i + 2) ), 7);
        }
      }
    };

    var strToNum = function(s) {
      var num = 0;
      for (var i = 0; i < s.length; i += 1) {
        num = num * 10 + chatToNum(s.charAt(i) );
      }
      return num;
    };

    var chatToNum = function(c) {
      if ('0' <= c && c <= '9') {
        return c.charCodeAt(0) - '0'.charCodeAt(0);
      }
      throw 'illegal char :' + c;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qrAlphaNum
  //---------------------------------------------------------------------

  var qrAlphaNum = function(data) {

    var _mode = QRMode.MODE_ALPHA_NUM;
    var _data = data;

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return _data.length;
    };

    _this.write = function(buffer) {

      var s = _data;

      var i = 0;

      while (i + 1 < s.length) {
        buffer.put(
          getCode(s.charAt(i) ) * 45 +
          getCode(s.charAt(i + 1) ), 11);
        i += 2;
      }

      if (i < s.length) {
        buffer.put(getCode(s.charAt(i) ), 6);
      }
    };

    var getCode = function(c) {

      if ('0' <= c && c <= '9') {
        return c.charCodeAt(0) - '0'.charCodeAt(0);
      } else if ('A' <= c && c <= 'Z') {
        return c.charCodeAt(0) - 'A'.charCodeAt(0) + 10;
      } else {
        switch (c) {
        case ' ' : return 36;
        case '$' : return 37;
        case '%' : return 38;
        case '*' : return 39;
        case '+' : return 40;
        case '-' : return 41;
        case '.' : return 42;
        case '/' : return 43;
        case ':' : return 44;
        default :
          throw 'illegal char :' + c;
        }
      }
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qr8BitByte
  //---------------------------------------------------------------------

  var qr8BitByte = function(data) {

    var _mode = QRMode.MODE_8BIT_BYTE;
    var _data = data;
    var _bytes = qrcode.stringToBytes(data);

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return _bytes.length;
    };

    _this.write = function(buffer) {
      for (var i = 0; i < _bytes.length; i += 1) {
        buffer.put(_bytes[i], 8);
      }
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // qrKanji
  //---------------------------------------------------------------------

  var qrKanji = function(data) {

    var _mode = QRMode.MODE_KANJI;
    var _data = data;

    var stringToBytes = qrcode.stringToBytesFuncs['SJIS'];
    if (!stringToBytes) {
      throw 'sjis not supported.';
    }
    !function(c, code) {
      // self test for sjis support.
      var test = stringToBytes(c);
      if (test.length != 2 || ( (test[0] << 8) | test[1]) != code) {
        throw 'sjis not supported.';
      }
    }('\u53cb', 0x9746);

    var _bytes = stringToBytes(data);

    var _this = {};

    _this.getMode = function() {
      return _mode;
    };

    _this.getLength = function(buffer) {
      return ~~(_bytes.length / 2);
    };

    _this.write = function(buffer) {

      var data = _bytes;

      var i = 0;

      while (i + 1 < data.length) {

        var c = ( (0xff & data[i]) << 8) | (0xff & data[i + 1]);

        if (0x8140 <= c && c <= 0x9FFC) {
          c -= 0x8140;
        } else if (0xE040 <= c && c <= 0xEBBF) {
          c -= 0xC140;
        } else {
          throw 'illegal char at ' + (i + 1) + '/' + c;
        }

        c = ( (c >>> 8) & 0xff) * 0xC0 + (c & 0xff);

        buffer.put(c, 13);

        i += 2;
      }

      if (i < data.length) {
        throw 'illegal char at ' + (i + 1);
      }
    };

    return _this;
  };

  //=====================================================================
  // GIF Support etc.
  //

  //---------------------------------------------------------------------
  // byteArrayOutputStream
  //---------------------------------------------------------------------

  var byteArrayOutputStream = function() {

    var _bytes = [];

    var _this = {};

    _this.writeByte = function(b) {
      _bytes.push(b & 0xff);
    };

    _this.writeShort = function(i) {
      _this.writeByte(i);
      _this.writeByte(i >>> 8);
    };

    _this.writeBytes = function(b, off, len) {
      off = off || 0;
      len = len || b.length;
      for (var i = 0; i < len; i += 1) {
        _this.writeByte(b[i + off]);
      }
    };

    _this.writeString = function(s) {
      for (var i = 0; i < s.length; i += 1) {
        _this.writeByte(s.charCodeAt(i) );
      }
    };

    _this.toByteArray = function() {
      return _bytes;
    };

    _this.toString = function() {
      var s = '';
      s += '[';
      for (var i = 0; i < _bytes.length; i += 1) {
        if (i > 0) {
          s += ',';
        }
        s += _bytes[i];
      }
      s += ']';
      return s;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // base64EncodeOutputStream
  //---------------------------------------------------------------------

  var base64EncodeOutputStream = function() {

    var _buffer = 0;
    var _buflen = 0;
    var _length = 0;
    var _base64 = '';

    var _this = {};

    var writeEncoded = function(b) {
      _base64 += String.fromCharCode(encode(b & 0x3f) );
    };

    var encode = function(n) {
      if (n < 0) {
        // error.
      } else if (n < 26) {
        return 0x41 + n;
      } else if (n < 52) {
        return 0x61 + (n - 26);
      } else if (n < 62) {
        return 0x30 + (n - 52);
      } else if (n == 62) {
        return 0x2b;
      } else if (n == 63) {
        return 0x2f;
      }
      throw 'n:' + n;
    };

    _this.writeByte = function(n) {

      _buffer = (_buffer << 8) | (n & 0xff);
      _buflen += 8;
      _length += 1;

      while (_buflen >= 6) {
        writeEncoded(_buffer >>> (_buflen - 6) );
        _buflen -= 6;
      }
    };

    _this.flush = function() {

      if (_buflen > 0) {
        writeEncoded(_buffer << (6 - _buflen) );
        _buffer = 0;
        _buflen = 0;
      }

      if (_length % 3 != 0) {
        // padding
        var padlen = 3 - _length % 3;
        for (var i = 0; i < padlen; i += 1) {
          _base64 += '=';
        }
      }
    };

    _this.toString = function() {
      return _base64;
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // base64DecodeInputStream
  //---------------------------------------------------------------------

  var base64DecodeInputStream = function(str) {

    var _str = str;
    var _pos = 0;
    var _buffer = 0;
    var _buflen = 0;

    var _this = {};

    _this.read = function() {

      while (_buflen < 8) {

        if (_pos >= _str.length) {
          if (_buflen == 0) {
            return -1;
          }
          throw 'unexpected end of file./' + _buflen;
        }

        var c = _str.charAt(_pos);
        _pos += 1;

        if (c == '=') {
          _buflen = 0;
          return -1;
        } else if (c.match(/^\s$/) ) {
          // ignore if whitespace.
          continue;
        }

        _buffer = (_buffer << 6) | decode(c.charCodeAt(0) );
        _buflen += 6;
      }

      var n = (_buffer >>> (_buflen - 8) ) & 0xff;
      _buflen -= 8;
      return n;
    };

    var decode = function(c) {
      if (0x41 <= c && c <= 0x5a) {
        return c - 0x41;
      } else if (0x61 <= c && c <= 0x7a) {
        return c - 0x61 + 26;
      } else if (0x30 <= c && c <= 0x39) {
        return c - 0x30 + 52;
      } else if (c == 0x2b) {
        return 62;
      } else if (c == 0x2f) {
        return 63;
      } else {
        throw 'c:' + c;
      }
    };

    return _this;
  };

  //---------------------------------------------------------------------
  // gifImage (B/W)
  //---------------------------------------------------------------------

  var gifImage = function(width, height) {

    var _width = width;
    var _height = height;
    var _data = new Array(width * height);

    var _this = {};

    _this.setPixel = function(x, y, pixel) {
      _data[y * _width + x] = pixel;
    };

    _this.write = function(out) {

      //---------------------------------
      // GIF Signature

      out.writeString('GIF87a');

      //---------------------------------
      // Screen Descriptor

      out.writeShort(_width);
      out.writeShort(_height);

      out.writeByte(0x80); // 2bit
      out.writeByte(0);
      out.writeByte(0);

      //---------------------------------
      // Global Color Map

      // black
      out.writeByte(0x00);
      out.writeByte(0x00);
      out.writeByte(0x00);

      // white
      out.writeByte(0xff);
      out.writeByte(0xff);
      out.writeByte(0xff);

      //---------------------------------
      // Image Descriptor

      out.writeString(',');
      out.writeShort(0);
      out.writeShort(0);
      out.writeShort(_width);
      out.writeShort(_height);
      out.writeByte(0);

      //---------------------------------
      // Local Color Map

      //---------------------------------
      // Raster Data

      var lzwMinCodeSize = 2;
      var raster = getLZWRaster(lzwMinCodeSize);

      out.writeByte(lzwMinCodeSize);

      var offset = 0;

      while (raster.length - offset > 255) {
        out.writeByte(255);
        out.writeBytes(raster, offset, 255);
        offset += 255;
      }

      out.writeByte(raster.length - offset);
      out.writeBytes(raster, offset, raster.length - offset);
      out.writeByte(0x00);

      //---------------------------------
      // GIF Terminator
      out.writeString(';');
    };

    var bitOutputStream = function(out) {

      var _out = out;
      var _bitLength = 0;
      var _bitBuffer = 0;

      var _this = {};

      _this.write = function(data, length) {

        if ( (data >>> length) != 0) {
          throw 'length over';
        }

        while (_bitLength + length >= 8) {
          _out.writeByte(0xff & ( (data << _bitLength) | _bitBuffer) );
          length -= (8 - _bitLength);
          data >>>= (8 - _bitLength);
          _bitBuffer = 0;
          _bitLength = 0;
        }

        _bitBuffer = (data << _bitLength) | _bitBuffer;
        _bitLength = _bitLength + length;
      };

      _this.flush = function() {
        if (_bitLength > 0) {
          _out.writeByte(_bitBuffer);
        }
      };

      return _this;
    };

    var getLZWRaster = function(lzwMinCodeSize) {

      var clearCode = 1 << lzwMinCodeSize;
      var endCode = (1 << lzwMinCodeSize) + 1;
      var bitLength = lzwMinCodeSize + 1;

      // Setup LZWTable
      var table = lzwTable();

      for (var i = 0; i < clearCode; i += 1) {
        table.add(String.fromCharCode(i) );
      }
      table.add(String.fromCharCode(clearCode) );
      table.add(String.fromCharCode(endCode) );

      var byteOut = byteArrayOutputStream();
      var bitOut = bitOutputStream(byteOut);

      // clear code
      bitOut.write(clearCode, bitLength);

      var dataIndex = 0;

      var s = String.fromCharCode(_data[dataIndex]);
      dataIndex += 1;

      while (dataIndex < _data.length) {

        var c = String.fromCharCode(_data[dataIndex]);
        dataIndex += 1;

        if (table.contains(s + c) ) {

          s = s + c;

        } else {

          bitOut.write(table.indexOf(s), bitLength);

          if (table.size() < 0xfff) {

            if (table.size() == (1 << bitLength) ) {
              bitLength += 1;
            }

            table.add(s + c);
          }

          s = c;
        }
      }

      bitOut.write(table.indexOf(s), bitLength);

      // end code
      bitOut.write(endCode, bitLength);

      bitOut.flush();

      return byteOut.toByteArray();
    };

    var lzwTable = function() {

      var _map = {};
      var _size = 0;

      var _this = {};

      _this.add = function(key) {
        if (_this.contains(key) ) {
          throw 'dup key:' + key;
        }
        _map[key] = _size;
        _size += 1;
      };

      _this.size = function() {
        return _size;
      };

      _this.indexOf = function(key) {
        return _map[key];
      };

      _this.contains = function(key) {
        return typeof _map[key] != 'undefined';
      };

      return _this;
    };

    return _this;
  };

  var createDataURL = function(width, height, getPixel) {
    var gif = gifImage(width, height);
    for (var y = 0; y < height; y += 1) {
      for (var x = 0; x < width; x += 1) {
        gif.setPixel(x, y, getPixel(x, y) );
      }
    }

    var b = byteArrayOutputStream();
    gif.write(b);

    var base64 = base64EncodeOutputStream();
    var bytes = b.toByteArray();
    for (var i = 0; i < bytes.length; i += 1) {
      base64.writeByte(bytes[i]);
    }
    base64.flush();

    return 'data:image/gif;base64,' + base64;
  };

  //---------------------------------------------------------------------
  // returns qrcode function.

  return qrcode;
}();

// multibyte support
!function() {

  qrcode.stringToBytesFuncs['UTF-8'] = function(s) {
    // http://stackoverflow.com/questions/18729405/how-to-convert-utf8-string-to-byte-array
    function toUTF8Array(str) {
      var utf8 = [];
      for (var i=0; i < str.length; i++) {
        var charcode = str.charCodeAt(i);
        if (charcode < 0x80) utf8.push(charcode);
        else if (charcode < 0x800) {
          utf8.push(0xc0 | (charcode >> 6),
              0x80 | (charcode & 0x3f));
        }
        else if (charcode < 0xd800 || charcode >= 0xe000) {
          utf8.push(0xe0 | (charcode >> 12),
              0x80 | ((charcode>>6) & 0x3f),
              0x80 | (charcode & 0x3f));
        }
        // surrogate pair
        else {
          i++;
          // UTF-16 encodes 0x10000-0x10FFFF by
          // subtracting 0x10000 and splitting the
          // 20 bits of 0x0-0xFFFFF into two halves
          charcode = 0x10000 + (((charcode & 0x3ff)<<10)
            | (str.charCodeAt(i) & 0x3ff));
          utf8.push(0xf0 | (charcode >>18),
              0x80 | ((charcode>>12) & 0x3f),
              0x80 | ((charcode>>6) & 0x3f),
              0x80 | (charcode & 0x3f));
        }
      }
      return utf8;
    }
    return toUTF8Array(s);
  };

}();

(function (factory) {
  if (typeof define === 'function' && define.amd) {
      define([], factory);
  } else if (typeof exports === 'object') {
      module.exports = factory();
  }
}(function () {
    return qrcode;
}));


/* YorozuyaPanel: client-side QR rendering (no server-side dependency) */
function renderQr() {
  var els = document.querySelectorAll('[data-qr]');
  for (var i = 0; i < els.length; i++) {
    var el = els[i];
    if (el.getAttribute('data-qr-done')) continue;
    var text = el.getAttribute('data-qr');
    if (!text) continue;
    try {
      var qr = qrcode(0, 'M');
      qr.addData(text);
      qr.make();
      el.innerHTML = qr.createSvgTag({ cellSize: 4, margin: 2, scalable: true });
      el.setAttribute('data-qr-done', '1');
    } catch (e) {}
  }
}
function renderQrSet(id, text) {
  var el = document.getElementById(id);
  if (el && text) { el.setAttribute('data-qr', text); }
  renderQr();
}
</script>
</head>
<body>
<div class="wrap" style="max-width:720px">
  <div class="topbar wood" style="justify-content:center;text-align:center">
    <div class="logo"><svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="milk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff7ec"/><stop offset="1" stop-color="#ffd9e3"/></linearGradient><linearGradient id="wood" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9c6a3c"/><stop offset="1" stop-color="#6f4525"/></linearGradient><linearGradient id="berry" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff7b9c"/><stop offset="1" stop-color="#e63946"/></linearGradient></defs><path d="M78 96 L178 96 L196 226 Q197 236 187 236 L69 236 Q59 236 60 226 Z" fill="url(#milk)" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M78 96 L108 52 L188 52 L178 96 Z" fill="#ffc7d6" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M108 52 L128 96" stroke="#3d2712" stroke-width="5" stroke-linecap="round"/><rect x="158" y="14" width="16" height="62" rx="8" fill="#4aa3ff" stroke="#3d2712" stroke-width="6" transform="rotate(18 166 45)"/><path d="M128 130 C108 130 96 144 98 160 C100 182 114 200 128 200 C142 200 156 182 158 160 C160 144 148 130 128 130 Z" fill="url(#berry)" stroke="#7a1020" stroke-width="6"/><path d="M112 132 L118 118 L126 128 L134 116 L140 128 L148 120 L144 132 Z" fill="#2ec4b6" stroke="#0d5c55" stroke-width="5" stroke-linejoin="round"/><circle cx="118" cy="158" r="3.5" fill="#ffe9b0"/><circle cx="132" cy="150" r="3.5" fill="#ffe9b0"/><circle cx="140" cy="166" r="3.5" fill="#ffe9b0"/><circle cx="124" cy="176" r="3.5" fill="#ffe9b0"/><rect x="52" y="196" width="152" height="34" rx="8" fill="url(#wood)" stroke="#3d2712" stroke-width="6"/><text x="128" y="221" text-anchor="middle" font-family="serif" font-weight="900" font-size="20" fill="#f4d35e" letter-spacing="4">万事屋</text></svg></div>
    <div>
      <h1>اشتراک <span id="u-name">…</span> <span class="kanji">万事屋</span></h1>
      <div class="sub">یوروزویا تحویلت می‌ده، حتی وسط نبرد 😎</div>
    </div>
  </div>
  <div class="card" id="info-card" style="display:none">
    <div class="grid c3" style="text-align:center">
      <div><div class="muted" style="font-size:.78rem">⏳ انقضا</div><b id="u-exp">♾️</b></div>
      <div><div class="muted" style="font-size:.78rem">📦 ترافیک</div><b id="u-traf">–</b></div>
      <div><div class="muted" style="font-size:.78rem">🛰 تعداد کانفیگ</div><b id="u-count">–</b></div>
    </div>
    <div class="bar" id="u-bar-wrap" style="margin-top:10px"><i id="u-bar" style="width:0%"></i></div>
  </div>
  <div class="card" style="margin-top:16px;text-align:center" id="sub-card">
    <h3>🔗 لینک اشتراک</h3>
    <div class="qr-box" id="qr-box"></div>
    <div class="import-btns" style="justify-content:center">
      <button class="btn small blue" onclick="copySub()">📋 کپی لینک ساب</button>
      <button class="btn small ghost" onclick="importApp('v2rayng')">V2RayNG</button>
      <button class="btn small ghost" onclick="importApp('nekobox')">NekoBox</button>
      <button class="btn small ghost" onclick="importApp('streisand')">Streisand</button>
      <button class="btn small ghost" onclick="importApp('v2box')">V2Box</button>
      <button class="btn small ghost" onclick="importApp('hiddify')">Hiddify</button>
      <button class="btn small ghost" onclick="importApp('happ')">Happ</button>
    </div>
    <div class="muted" style="font-size:.78rem">دکمه اپ رو بزن تا ساب مستقیم ایمپورت بشه (اگه اپ نصبه) 📲</div>
  </div>
  <div class="card" style="margin-top:16px">
    <h3>⚔️ کانفیگ‌ها (تکی)</h3>
    <div id="links"><span class="muted">در حال بارگذاری…</span></div>
  </div>
  <footer class="foot">🍓 <span class="kanji">万事屋</span> یوروزویا — ما همه‌کار می‌کنیم!</footer>
</div>
<div id="toasts"></div>
<script>
var API_BASE = '/';
var KEY = (location.pathname.split('/p/')[1] || '').split('?')[0];
var SUB_URL = '', SUB_LINKS = [];
function toast(msg, err) {
  var t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.getElementById('toasts').appendChild(t);
  setTimeout(function () { t.remove(); }, 3200);
}
function copyText(txt, msg) {
  function done() { toast(msg || '📋 کپی شد!'); }
  if (navigator.clipboard && navigator.clipboard.writeText) { navigator.clipboard.writeText(txt).then(done).catch(function () { fallback(); }); }
  else fallback();
  function fallback() {
    var ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (e) {}
    ta.remove(); done();
  }
}
function copySub() { copyText(SUB_URL, '🔗 لینک ساب کپی شد!'); }
function copyCfg(i) { if (SUB_LINKS[i]) copyText(SUB_LINKS[i].link, '⚔️ کانفیگ ' + SUB_LINKS[i].node + ' کپی شد!'); }
function importApp(app) {
  var u = encodeURIComponent(SUB_URL);
  var schemes = {
    v2rayng: 'v2rayng://install-sub?url=' + u,
    nekobox: 'nekobox://install-sub?url=' + u,
    streisand: 'streisand://import/' + u,
    v2box: 'v2box://install-sub?url=' + u + '&name=Yorozuya',
    hiddify: 'hiddify://import/' + u,
    happ: 'happ://add/' + u
  };
  if (schemes[app]) location.href = schemes[app];
}
function esc(s) {
  return String(s === null || s === undefined ? '' : s).replace(/[&<>"']/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
  });
}
function faDate(iso) {
  if (!iso) return '♾️ نامحدود';
  try {
    var d = new Date(iso);
    var left = Math.ceil((d - Date.now()) / 86400000);
    return d.toLocaleDateString('fa-IR') + (left >= 0 ? ' (' + left + ' روز مونده ⏳)' : ' (تموم شده!)');
  } catch (e) { return iso; }
}
fetch(API_BASE + 'api/public/sub/' + encodeURIComponent(KEY)).then(function (r) {
  if (!r.ok) {
    document.getElementById('links').innerHTML = '❌ این اشتراک معتبر نیست یا منقضی شده.';
    document.getElementById('sub-card').style.display = 'none';
    return null;
  }
  return r.json();
}).then(function (d) {
  if (!d) return;
  SUB_URL = d.sub_url;
  SUB_LINKS = d.links;
  document.getElementById('u-name').textContent = d.username;
  document.getElementById('info-card').style.display = 'block';
  document.getElementById('u-exp').textContent = faDate(d.expires_at);
  document.getElementById('u-traf').textContent = d.traffic_limit_gb > 0
    ? (d.traffic_used_gb + ' / ' + d.traffic_limit_gb + ' GB')
    : (d.traffic_used_gb + ' GB ♾️');
  document.getElementById('u-count').textContent = d.links.length + ' ⚔️';
  if (d.traffic_limit_gb > 0) {
    var pct = Math.min(100, d.traffic_used_gb / d.traffic_limit_gb * 100);
    document.getElementById('u-bar').style.width = pct + '%';
    if (pct > 85) document.getElementById('u-bar-wrap').classList.add('hot');
  } else { document.getElementById('u-bar').style.width = '100%'; }
  renderQrSet('qr-box', d.sub_url);
  document.title = '🍓 اشتراک ' + d.username + ' | یوروزویا';
  var box = document.getElementById('links');
  box.innerHTML = '';
  if (!d.links.length) box.innerHTML = '<span class="muted">هنوز کانفیگی نیست… گینتاما خوابه 😴</span>';
  d.links.forEach(function (l, i) {
    var row = document.createElement('div');
    row.className = 'link-row';
    row.innerHTML = '<span class="meta">' + l.flag + ' ' + esc(l.node) + ' <span class="badge info">' + esc(l.protocol) + (l.network ? '/' + esc(l.network) : '') + '</span></span><code>' + esc(l.link.slice(0, 60)) + '…</code>';
    var b = document.createElement('button');
    b.className = 'btn small'; b.textContent = '📋 کپی';
    b.onclick = (function (idx) { return function () { copyCfg(idx); }; })(i);
    row.appendChild(b);
    box.appendChild(row);
  });
}).catch(function () { document.getElementById('links').textContent = '❌ خطا در بارگذاری'; });
</script>
</body>
</html>'''

# ============ HTTP ============
class BadRequest(Exception):
    pass

class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    server_version = 'YorozuyaPanel'
    sys_version = ''

    def log_message(self, fmt, *args):
        pass  # compact console — no per-request noise

    def version_string(self):
        return 'YorozuyaPanel/' + Y_VERSION

    # ---- low-level send ----
    def _send(self, status, body=b'', content_type='application/json; charset=utf-8', headers=None):
        if isinstance(body, str):
            body = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', '*')
        self.send_header('Access-Control-Allow-Headers', '*')
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != 'HEAD' and body:
            self.wfile.write(body)

    def json_response(self, status, obj, headers=None):
        self._send(status, json.dumps(obj, ensure_ascii=False, separators=(',', ':')),
                   'application/json; charset=utf-8', headers)

    def redirect(self, location):
        self._send(302, b'', 'text/plain; charset=utf-8', {'Location': location})

    def read_json_body(self):
        try:
            n = int(self.headers.get('Content-Length') or 0)
        except ValueError:
            n = 0
        if n <= 0:
            return {}
        if n > 2 * 1024 * 1024:
            raise BadRequest('request body too large (2mb limit)')
        raw = self.rfile.read(n)
        if not raw:
            return {}
        try:
            d = json.loads(raw.decode('utf-8'))
        except (ValueError, UnicodeDecodeError):
            raise BadRequest('invalid JSON body')
        return d if isinstance(d, dict) else {}

    # ---- dispatch ----
    def dispatch(self, method):
        try:
            if method == 'OPTIONS':
                return self._send(204, b'', 'text/plain; charset=utf-8')
            u = urllib.parse.urlsplit(self.path)
            path = urllib.parse.unquote(u.path)
            self._query = {k: v[0] for k, v in urllib.parse.parse_qs(u.query, keep_blank_values=True).items()}
            self._body = {}
            if method in ('POST', 'PATCH', 'PUT', 'DELETE'):
                self._body = self.read_json_body()
            for m, pattern, name, auth in ROUTES:
                if m != method:
                    continue
                mo = re.match(pattern, path)
                if not mo:
                    continue
                if auth:
                    if not password_set():
                        return self.json_response(503, {'detail': 'setup required — open /setup'})
                    if not check_session(self.headers.get('Cookie')):
                        return self.json_response(401, {'detail': 'login required'})
                return getattr(self, 'h_' + name)(*mo.groups())
            self.json_response(404, {'detail': 'not found'})
        except BadRequest as e:
            self.json_response(400, {'detail': str(e)})
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self.json_response(500, {'detail': 'server error: %s' % e})
            except Exception:
                pass

    def do_GET(self):
        self.dispatch('GET')

    def do_POST(self):
        self.dispatch('POST')

    def do_PATCH(self):
        self.dispatch('PATCH')

    def do_DELETE(self):
        self.dispatch('DELETE')

    def do_PUT(self):
        self.dispatch('PUT')

    def do_OPTIONS(self):
        self.dispatch('OPTIONS')

    # ---- pages ----
    def h_root(self):
        if not password_set():
            return self.redirect('/setup')
        self.redirect('/panel' if check_session(self.headers.get('Cookie')) else '/login')

    def h_healthz(self):
        self.json_response(200, {'ok': True, 'app': 'yorozuya-panel', 'version': Y_VERSION})

    def h_page_login(self):
        if not password_set():
            return self.redirect('/setup')
        self._send(200, PAGE_LOGIN, 'text/html; charset=utf-8')

    def h_page_setup(self):
        if password_set():
            return self.redirect('/panel' if check_session(self.headers.get('Cookie')) else '/login')
        self._send(200, PAGE_SETUP, 'text/html; charset=utf-8')

    def h_page_panel(self):
        if not password_set():
            return self.redirect('/setup')
        if not check_session(self.headers.get('Cookie')):
            return self.redirect('/login')
        self._send(200, PAGE_PANEL, 'text/html; charset=utf-8')

    def h_page_sub(self, sub_key):
        if not find_user_by_sub(sub_key):
            return self._send(404, '<h1>404 — not found 🍓</h1>', 'text/html; charset=utf-8')
        self._send(200, PAGE_SUB, 'text/html; charset=utf-8')

    # ---- auth ----
    def h_api_setup(self):
        if password_set():
            return self.json_response(403, {'detail': 'password already set'})
        pw = self._body.get('password')
        pw = '' if pw is None else str(pw)
        if len(pw) < 4:
            return self.json_response(400, {'detail': 'password too short (min 4 chars)'})
        STATE['settings']['admin_hash'] = sha256(pw)
        mark_dirty()
        save_state(True)
        token = make_session()
        self.json_response(200, {'ok': True}, {'Set-Cookie': SESSION_COOKIE % (token, SESSION_TTL)})

    def h_api_login(self):
        h = active_hash()
        pw = self._body.get('password')
        pw = '' if pw is None else str(pw)
        if h and pass_ok(sha256(pw), h):
            token = make_session()
            return self.json_response(200, {'ok': True}, {'Set-Cookie': SESSION_COOKIE % (token, SESSION_TTL)})
        self.json_response(401, {'detail': 'wrong password'})

    def h_api_logout(self):
        t = get_cookies(self.headers.get('Cookie')).get('yorozuya_session')
        if t:
            with LOCK:
                SESSIONS.pop(t, None)
        self.json_response(200, {'ok': True},
                           {'Set-Cookie': 'yorozuya_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0'})

    def h_api_me(self):
        self.json_response(200, {'ok': True, 'user': 'admin'})

    def h_api_fun_line(self):
        self.json_response(200, {'line': secrets.choice(GINTOKI_LINES)})

    # ---- server info ----
    def h_api_server_info(self):
        out = get_outbound_info(False)
        self.json_response(200, {
            'ok': True, 'version': Y_VERSION, 'now': now_iso(), 'hostname': socket.gethostname(),
            'default_password': DEFAULT_PASSWORD, 'outbound': out,
            'counts': {
                'users': len(STATE['users']),
                'active_users': len([u for u in STATE['users'] if u.get('enabled') and not is_expired(u)]),
                'expired_users': len([u for u in STATE['users'] if is_expired(u)]),
                'nodes': len(STATE['nodes']),
                'online_nodes': len([n for n in STATE['nodes'] if n.get('online') is True]),
            },
            'fun_line': secrets.choice(GINTOKI_LINES),
            'setup_required': not password_set(),
            'public_url': CONFIG['public_url'] or None,
            'keepalive': {'enabled': CONFIG['keepalive_minutes'] > 0, 'interval_minutes': CONFIG['keepalive_minutes'],
                          'pings': KEEPALIVE['pings'], 'last_ok': KEEPALIVE['lastOk'], 'last_at': KEEPALIVE['lastAt']},
            'lb': {'strategy': lb_state()['strategy'], 'candidates': len(lb_candidates())},
        })

    def h_api_server_info_refresh(self):
        self.json_response(200, {'ok': True, 'outbound': get_outbound_info(True)})

    # ---- users ----
    def h_api_users_list(self):
        self.json_response(200, {'users': [public_user(u) for u in STATE['users']]})

    def h_api_users_create(self):
        b = self._body
        username = str(b.get('username') or '').strip()
        if not username:
            return self.json_response(400, {'detail': 'username required'})
        if any(u.get('username') == username for u in STATE['users']):
            return self.json_response(400, {'detail': 'username exists'})
        user = {
            'id': new_id(), 'username': username, 'uuid': str(b.get('uuid') or uuidlib.uuid4()),
            'sub_key': new_sub_key(), 'traffic_limit_gb': to_num(b.get('traffic_limit_gb'), 0),
            'traffic_used_gb': 0, 'expires_at': str(b.get('expires_at') or ''),
            'enabled': b.get('enabled') is not False, 'note': str(b.get('note') or ''), 'created_at': now_iso(),
        }
        STATE['users'].append(user)
        mark_dirty()
        self.json_response(200, {'ok': True, 'user': public_user(user)})

    def h_api_users_patch(self, uid):
        u = find_user(uid)
        if not u:
            return self.json_response(404, {'detail': 'user not found'})
        b = self._body
        if 'username' in b:
            name = str(b.get('username') or '').strip()
            if not name:
                return self.json_response(400, {'detail': 'username required'})
            if any(x.get('username') == name and x.get('id') != u.get('id') for x in STATE['users']):
                return self.json_response(400, {'detail': 'username exists'})
            u['username'] = name
        if 'expires_at' in b:
            u['expires_at'] = str(b.get('expires_at') or '')
        if 'note' in b:
            u['note'] = str(b.get('note') or '')
        if 'traffic_limit_gb' in b:
            v = to_num(b.get('traffic_limit_gb'), 0)
            u['traffic_limit_gb'] = max(0, v)
        if 'traffic_used_gb' in b:
            v = to_num(b.get('traffic_used_gb'), 0)
            u['traffic_used_gb'] = max(0, v)
        if 'enabled' in b:
            u['enabled'] = bool(b.get('enabled'))
        if b.get('uuid'):
            u['uuid'] = str(b['uuid'])
        mark_dirty()
        self.json_response(200, {'ok': True, 'user': public_user(u)})

    def h_api_users_delete(self, uid):
        u = find_user(uid)
        if not u:
            return self.json_response(404, {'detail': 'user not found'})
        STATE['users'].remove(u)
        mark_dirty()
        self.json_response(200, {'ok': True})

    def h_api_users_reset_traffic(self, uid):
        u = find_user(uid)
        if not u:
            return self.json_response(404, {'detail': 'user not found'})
        u['traffic_used_gb'] = 0
        mark_dirty()
        self.json_response(200, {'ok': True, 'user': public_user(u)})

    def h_api_users_rotate_sub(self, uid):
        u = find_user(uid)
        if not u:
            return self.json_response(404, {'detail': 'user not found'})
        u['sub_key'] = new_sub_key()
        mark_dirty()
        self.json_response(200, {'ok': True, 'user': public_user(u)})

    def h_api_users_subscription(self, uid):
        u = find_user(uid)
        if not u:
            return self.json_response(404, {'detail': 'user not found'})
        base = sub_base(self.headers)
        mode = 'lb' if self._query.get('node') == 'lb' else None
        self.json_response(200, {
            'ok': True, 'user': public_user(u),
            'sub_url': base + '/api/sub/' + u['sub_key'],
            'page_url': base + '/p/' + u['sub_key'],
            'qr_url': base + '/api/sub/' + u['sub_key'] + '/qr',
            'lb_url': base + '/api/lb/sub/' + u['sub_key'],
            'links': user_links(u, mode),
        })

    def h_api_qr_stub(self, *args):
        # QR is rendered client-side in this edition (zero server dependencies)
        self.json_response(501, {'detail': 'QR is rendered client-side in this edition'})

    # ---- nodes ----
    NODE_FIELDS = ['name', 'address', 'port', 'protocol', 'network', 'security', 'sni', 'path', 'host', 'fp', 'alpn',
                   'reality_pbk', 'reality_sid', 'reality_spx', 'flow', 'ss_method', 'ss_password', 'mode', 'header', 'enabled']

    def h_api_nodes_list(self):
        self.json_response(200, {'nodes': [public_node(n) for n in STATE['nodes']]})

    def h_api_nodes_create(self):
        b = self._body
        name = str(b.get('name') or '').strip()
        address = str(b.get('address') or '').strip()
        if not name or not address:
            return self.json_response(400, {'detail': 'name and address required'})
        node = {
            'id': new_id(), 'name': name, 'address': address,
            'port': to_num(b.get('port'), 443) or 443,
            'protocol': str(b.get('protocol') or 'vless').lower(),
            'network': str(b.get('network') or 'tcp').lower(),
            'security': str(b.get('security') or 'none').lower(),
            'sni': str(b.get('sni') or ''), 'path': str(b.get('path') or ''), 'host': str(b.get('host') or ''),
            'fp': str(b.get('fp') or ''), 'alpn': str(b.get('alpn') or ''),
            'reality_pbk': str(b.get('reality_pbk') or ''), 'reality_sid': str(b.get('reality_sid') or ''), 'reality_spx': str(b.get('reality_spx') or ''),
            'flow': str(b.get('flow') or ''),
            'mode': str(b.get('mode') or ''), 'header': str(b.get('header') or ''),
            'ss_method': str(b.get('ss_method') or 'chacha20-ietf-poly1305'), 'ss_password': str(b.get('ss_password') or ''),
            'enabled': b.get('enabled') is not False, 'created_at': now_iso(),
            'last_check': '', 'online': None, 'latency_ms': None, 'resolved_ip': '', 'country': '', 'country_code': '',
        }
        STATE['nodes'].append(node)
        mark_dirty()
        threading.Thread(target=lambda: (check_node(node), save_state(False)), daemon=True).start()
        self.json_response(200, {'ok': True, 'node': public_node(node)})

    def h_api_nodes_patch(self, nid):
        n = find_node(nid)
        if not n:
            return self.json_response(404, {'detail': 'node not found'})
        b = self._body
        for f in self.NODE_FIELDS:
            if b.get(f) is not None:
                n[f] = b[f]
        n['port'] = to_num(n.get('port'), 443) or 443
        mark_dirty()
        self.json_response(200, {'ok': True, 'node': public_node(n)})

    def h_api_nodes_delete(self, nid):
        n = find_node(nid)
        if not n:
            return self.json_response(404, {'detail': 'node not found'})
        STATE['nodes'].remove(n)
        mark_dirty()
        self.json_response(200, {'ok': True})

    def h_api_nodes_check(self, nid):
        n = find_node(nid)
        if not n:
            return self.json_response(404, {'detail': 'node not found'})
        check_node(n)
        save_state(False)
        self.json_response(200, {'ok': True, 'node': public_node(n)})

    def h_api_nodes_check_all(self):
        if not STATE['nodes']:
            return self.json_response(200, {'ok': True, 'nodes': []})
        with ThreadPoolExecutor(max_workers=8) as ex:
            resolved = list(ex.map(lambda n: {'n': n, 'ip': resolve_host(n.get('address'))}, STATE['nodes']))
        geo = ip_lookup_batch([r['ip'] for r in resolved])
        def do(r):
            n, ip = r['n'], r['ip']
            ok = False
            ms = None
            if ip:
                t = tcp_ping(ip, to_num(n.get('port'), 443) or 443, 5000)
                ok = t['ok']
                ms = t['ms']
            info = geo.get(ip) or {'country': '', 'country_code': '', 'city': '', 'isp': ''}
            n['last_check'] = now_iso()
            n['online'] = ok
            n['latency_ms'] = ms
            n['resolved_ip'] = ip
            n['country'] = info.get('country') or ''
            n['country_code'] = info.get('country_code') or ''
        with ThreadPoolExecutor(max_workers=8) as ex:
            list(ex.map(do, resolved))
        mark_dirty()
        self.json_response(200, {'ok': True, 'nodes': [public_node(n) for n in STATE['nodes']]})

    # ---- load balancer ----
    def h_api_lb_get(self):
        lb = lb_state()
        self.json_response(200, {
            'ok': True, 'strategy': lb['strategy'], 'rr': lb.get('rr') or 0, 'picks': lb['picks'],
            'candidates': [public_node(n) for n in lb_candidates()],
        })

    def h_api_lb_set(self):
        lb = lb_state()
        s = str(self._body.get('strategy') or '')
        if s not in LB_STRATEGIES:
            return self.json_response(400, {'detail': 'strategy must be one of: ' + ', '.join(LB_STRATEGIES)})
        lb['strategy'] = s
        mark_dirty()
        self.json_response(200, {
            'ok': True, 'strategy': lb['strategy'], 'rr': lb.get('rr') or 0, 'picks': lb['picks'],
            'candidates': [public_node(n) for n in lb_candidates()],
        })

    def h_api_lb_pick(self):
        n = pick_node()
        if not n:
            return self.json_response(404, {'detail': 'no enabled nodes'})
        out = {'ok': True, 'strategy': lb_state()['strategy'], 'node': public_node(n)}
        uname = str(self._query.get('username') or '').strip()
        u = next((x for x in STATE['users'] if x.get('username') == uname), None) if uname else None
        if u:
            e = link_entry(u, n)
            if e:
                out['link'] = e['link']
        self.json_response(200, out)

    # ---- tools ----
    def h_api_tools_lookup(self):
        query = str(self._body.get('query') or '').strip()
        if not query:
            return self.json_response(400, {'detail': 'query required'})
        ip = resolve_host(query)
        info = ip_lookup(ip) if ip else {'country': '', 'country_code': '', 'city': '', 'isp': ''}
        self.json_response(200, {
            'ok': True,
            'result': {
                'query': query, 'resolved_ip': ip, 'country': info.get('country') or '', 'country_code': info.get('country_code') or '',
                'city': info.get('city') or '', 'isp': info.get('isp') or '',
                'flag': flag(info['country_code']) if info.get('country_code') else '🏳️',
            },
        })

    # ---- settings + backup ----
    def h_api_settings_get(self):
        self.json_response(200, {'settings': STATE['settings']})

    def h_api_settings_post(self):
        b = self._body
        if 'sub_base_url' in b:
            STATE['settings']['sub_base_url'] = str(b.get('sub_base_url') or '').strip().rstrip('/')
        mark_dirty()
        self.json_response(200, {'ok': True, 'settings': STATE['settings']})

    def h_api_backup(self):
        with LOCK:
            payload = json.dumps(STATE, ensure_ascii=False, separators=(',', ':'))
        self._send(200, payload, 'application/json; charset=utf-8',
                   {'Content-Disposition': 'attachment; filename=yorozuya-backup.json'})

    def h_api_restore(self):
        b = self._body
        d = b.get('data') if isinstance(b.get('data'), dict) else None
        if d is None:
            d = b
        if not isinstance(d, dict) or not isinstance(d.get('users'), list) or not isinstance(d.get('nodes'), list):
            return self.json_response(400, {'detail': 'invalid backup file'})
        STATE['users'] = d['users']
        STATE['nodes'] = d['nodes']
        if isinstance(d.get('settings'), dict):
            STATE['settings'] = {'sub_base_url': '', 'admin_hash': ''}
            STATE['settings'].update(d['settings'])
        if isinstance(d.get('ip_cache'), dict):
            STATE['ip_cache'] = d['ip_cache']
        if isinstance(d.get('lb'), dict):
            STATE['lb'] = {'strategy': 'round-robin', 'rr': 0, 'picks': {}}
            STATE['lb'].update(d['lb'])
        mark_dirty()
        save_state(True)
        self.json_response(200, {'ok': True, 'imported': {'users': len(STATE['users']), 'nodes': len(STATE['nodes'])}})

    # ---- public subscription (no auth) ----
    def send_sub_body(self, u, links, lb_node_name):
        body = '\n'.join(x['link'] for x in links)
        b64 = base64.b64encode(body.encode('utf-8')).decode('ascii')
        headers = {
            'Content-Disposition': 'inline; filename="' + u['username'] + '.txt"',
            'Subscription-Userinfo': 'upload=0; download=' + str(int(float(u.get('traffic_used_gb') or 0) * 1073741824))
                                     + '; total=' + str(int(float(u.get('traffic_limit_gb') or 0) * 1073741824)) + '; expire=0',
        }
        if lb_node_name:
            headers['X-Yorozuya-LB-Node'] = lb_node_name
        self._send(200, b64, 'text/plain; charset=utf-8', headers)

    def h_api_sub(self, sub_key):
        u = find_user_by_sub(sub_key)
        if not u or not u.get('enabled') or is_expired(u):
            return self.json_response(404, {'detail': 'not found'})
        mode = 'lb' if self._query.get('node') == 'lb' else (self._query.get('node') or None)
        links = user_links(u, mode)
        if self._query.get('format') == 'text':
            t = '\n'.join(x['link'] for x in links)
            return self._send(200, (t + '\n') if t else '', 'text/plain; charset=utf-8')
        self.send_sub_body(u, links, None)

    def h_api_lb_sub(self, sub_key):
        # load-balanced subscription: every fetch picks a healthy node
        u = find_user_by_sub(sub_key)
        if not u or not u.get('enabled') or is_expired(u):
            return self.json_response(404, {'detail': 'not found'})
        links = user_links(u, 'lb')
        if self._query.get('format') == 'text':
            t = '\n'.join(x['link'] for x in links)
            return self._send(200, (t + '\n') if t else '', 'text/plain; charset=utf-8')
        self.send_sub_body(u, links, links[0]['node'] if links else None)

    def h_api_public_sub(self, sub_key):
        u = find_user_by_sub(sub_key)
        if not u or not u.get('enabled') or is_expired(u):
            return self.json_response(404, {'detail': 'not found'})
        base = sub_base(self.headers)
        mode = 'lb' if self._query.get('node') == 'lb' else (self._query.get('node') or None)
        self.json_response(200, {
            'ok': True, 'username': u['username'], 'expires_at': u.get('expires_at') or '', 'expired': is_expired(u),
            'traffic_limit_gb': to_num(u.get('traffic_limit_gb'), 0), 'traffic_used_gb': to_num(u.get('traffic_used_gb'), 0),
            'sub_url': base + '/api/sub/' + u['sub_key'], 'qr_url': base + '/api/sub/' + u['sub_key'] + '/qr',
            'links': user_links(u, mode),
        })

# ============ ROUTES ============
ROUTES = [
    ('GET', r'^/$', 'root', False),
    ('GET', r'^/healthz$', 'healthz', False),
    ('GET', r'^/login$', 'page_login', False),
    ('GET', r'^/setup$', 'page_setup', False),
    ('GET', r'^/panel$', 'page_panel', False),
    ('GET', r'^/p/([^/]+)$', 'page_sub', False),
    ('POST', r'^/api/setup$', 'api_setup', False),
    ('POST', r'^/api/login$', 'api_login', False),
    ('POST', r'^/api/logout$', 'api_logout', False),
    ('GET', r'^/api/me$', 'api_me', True),
    ('GET', r'^/api/fun/line$', 'api_fun_line', True),
    ('GET', r'^/api/server-info$', 'api_server_info', True),
    ('POST', r'^/api/server-info/refresh$', 'api_server_info_refresh', True),
    ('GET', r'^/api/users$', 'api_users_list', True),
    ('POST', r'^/api/users$', 'api_users_create', True),
    ('PATCH', r'^/api/users/([^/]+)$', 'api_users_patch', True),
    ('DELETE', r'^/api/users/([^/]+)$', 'api_users_delete', True),
    ('POST', r'^/api/users/([^/]+)/reset-traffic$', 'api_users_reset_traffic', True),
    ('POST', r'^/api/users/([^/]+)/rotate-sub$', 'api_users_rotate_sub', True),
    ('GET', r'^/api/users/([^/]+)/subscription$', 'api_users_subscription', True),
    ('GET', r'^/api/users/([^/]+)/qr$', 'api_qr_stub', True),
    ('GET', r'^/api/nodes$', 'api_nodes_list', True),
    ('POST', r'^/api/nodes$', 'api_nodes_create', True),
    ('PATCH', r'^/api/nodes/([^/]+)$', 'api_nodes_patch', True),
    ('DELETE', r'^/api/nodes/([^/]+)$', 'api_nodes_delete', True),
    ('POST', r'^/api/nodes/([^/]+)/check$', 'api_nodes_check', True),
    ('POST', r'^/api/nodes/check-all$', 'api_nodes_check_all', True),
    ('GET', r'^/api/lb$', 'api_lb_get', True),
    ('POST', r'^/api/lb$', 'api_lb_set', True),
    ('GET', r'^/api/lb/pick$', 'api_lb_pick', True),
    ('POST', r'^/api/tools/lookup$', 'api_tools_lookup', True),
    ('GET', r'^/api/settings$', 'api_settings_get', True),
    ('POST', r'^/api/settings$', 'api_settings_post', True),
    ('GET', r'^/api/settings/backup$', 'api_backup', True),
    ('POST', r'^/api/settings/restore$', 'api_restore', True),
    ('GET', r'^/api/sub/([^/]+)$', 'api_sub', False),
    ('GET', r'^/api/lb/sub/([^/]+)$', 'api_lb_sub', False),
    ('GET', r'^/api/sub/([^/]+)/qr$', 'api_qr_stub', False),
    ('GET', r'^/api/public/sub/([^/]+)$', 'api_public_sub', False),
]

# ============ BACKGROUND LOOP (sweep + staggered node re-check) ============
def bg_loop():
    last_auto = [0.0]
    while True:
        try:
            sweep()
            save_state(False)
        except Exception as e:
            print('⚠️ bg loop: %s' % e)
        if CONFIG['node_check_minutes'] > 0 and time.time() - last_auto[0] >= CONFIG['node_check_minutes'] * 60:
            last_auto[0] = time.time()
            for i, n in enumerate(list(STATE['nodes'])):
                if n.get('enabled') is False:
                    continue
                threading.Timer(i * 0.4, lambda nn=n: check_node(nn)).start()  # staggered — weak-server friendly
        time.sleep(30)

# ============ CRON: keep-alive self-ping (keeps free hosts awake) ============
KEEPALIVE = {'pings': 0, 'lastOk': False, 'lastAt': ''}

def self_ping_url():
    if CONFIG['public_url']:
        return CONFIG['public_url'] + '/healthz'
    return 'http://127.0.0.1:' + str(CONFIG['port']) + '/healthz'

def self_ping():
    KEEPALIVE['pings'] += 1
    try:
        with urllib.request.urlopen(self_ping_url(), timeout=10) as r:
            KEEPALIVE['lastOk'] = 200 <= r.status < 300
    except Exception:
        KEEPALIVE['lastOk'] = False
    KEEPALIVE['lastAt'] = now_iso()
    if not KEEPALIVE['lastOk']:
        print('⚠️ keep-alive ping failed (' + self_ping_url() + ')')

def keepalive_loop():
    while True:
        time.sleep(CONFIG['keepalive_minutes'] * 60)
        try:
            self_ping()
        except Exception:
            pass

# ============ START ============
def main():
    load_state()
    threading.Thread(target=bg_loop, daemon=True).start()
    if CONFIG['keepalive_minutes'] > 0:
        threading.Thread(target=keepalive_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(('0.0.0.0', CONFIG['port']), Handler)
    httpd.daemon_threads = True
    base = CONFIG['public_url'] or ('http://localhost:' + str(CONFIG['port']))
    print('🍓 YorozuyaPanel Python v' + Y_VERSION + ' · 万事屋')
    print('⚙️  protocols ready: vless · vmess · trojan · ss')
    print('🌐 listening on 0.0.0.0:' + str(CONFIG['port']) + ' · data: ' + DATA_DIR)
    if not password_set():
        print('🔐 first run — set admin password at ' + base + '/setup')
    elif DEFAULT_PASSWORD:
        print('⚠️  default password active — change ADMIN_PASSWORD!')
    if CONFIG['keepalive_minutes'] > 0:
        print('⏰ keep-alive cron: self-ping every ' + str(CONFIG['keepalive_minutes']) + ' min')
    print('📍 Web Panel path: /panel  →  ' + base + '/panel')
    sys.stdout.flush()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        save_state(True)

def _shutdown(signum, frame):
    save_state(True)
    sys.exit(0)

signal.signal(signal.SIGTERM, _shutdown)
signal.signal(signal.SIGINT, _shutdown)

if __name__ == '__main__':
    main()
