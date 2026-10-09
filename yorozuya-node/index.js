'use strict';
/**
 * ====================================================================
 *  🍓 YOROZUYA PANEL — NODE.JS SINGLE-FILE EDITION  万事屋
 * ====================================================================
 *  The ENTIRE panel (backend + frontend) lives in this ONE file.
 *  Just 2 files to deploy: index.js + package.json
 *
 *    npm install
 *    ADMIN_PASSWORD=something-strong node index.js
 *    → http://localhost:8080/login
 *
 *  Architecture inspired by Blue-Knight-Panel-V3 (single-file Express
 *  bundle + embedded frontend + JSON persistence). All code here is
 *  original. Fan-made Gintama theme, not affiliated with publishers.
 * ====================================================================
 */
const Y_VERSION = '1.0.0';

const express = require('express');
const QRCode = require('qrcode');
const fs = require('fs');
const path = require('path');
const os = require('os');
const crypto = require('crypto');
const net = require('net');
const dns = require('dns').promises;

// ============ CONFIG (env-driven, BK-style) ============
const Y_CONFIG = {
  brand: 'YorozuyaPanel',
  kanji: '万事屋',
  port: parseInt(process.env.PORT || process.env.SERVER_PORT || '8080', 10) || 8080,
  adminPassword: process.env.ADMIN_PASSWORD || 'yorozuya',
  dataDirHint: process.env.DATA_DIR || '',
};
const DEFAULT_PASSWORD = Y_CONFIG.adminPassword === 'yorozuya';
const SESSION_TTL = 12 * 3600 * 1000; // 12h
const IP_API_FIELDS = 'status,message,country,countryCode,city,query,org,isp';
const IP_CACHE_TTL = 7 * 24 * 3600 * 1000;
const OUTBOUND_CACHE_TTL = 10 * 60 * 1000;

const GINTOKI_LINES = [
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
];

// ============ DATA DIR (fallbacks like BK: cwd → appdir → tmp) ============
function pickDataDir() {
  const cands = [];
  if (Y_CONFIG.dataDirHint) cands.push(Y_CONFIG.dataDirHint);
  cands.push(path.join(process.cwd(), 'yorozuya-data'));
  cands.push(path.join(__dirname, 'yorozuya-data'));
  cands.push(path.join(os.tmpdir(), 'yorozuya-data'));
  for (const d of cands) {
    try {
      fs.mkdirSync(d, { recursive: true });
      const probe = path.join(d, '.probe');
      fs.writeFileSync(probe, 'ok');
      fs.unlinkSync(probe);
      return d;
    } catch (e) { /* try next */ }
  }
  throw new Error('no writable data dir found');
}
const DATA_DIR = pickDataDir();
const STATE_FILE = path.join(DATA_DIR, 'state.json');

// ============ STATE ============
const STATE = { users: [], nodes: [], settings: { sub_base_url: '' }, ip_cache: {} };
let dirty = false;
function loadState() {
  try {
    if (!fs.existsSync(STATE_FILE)) return;
    const d = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    if (Array.isArray(d.users)) STATE.users = d.users;
    if (Array.isArray(d.nodes)) STATE.nodes = d.nodes;
    if (d.settings && typeof d.settings === 'object') STATE.settings = Object.assign({ sub_base_url: '' }, d.settings);
    if (d.ip_cache && typeof d.ip_cache === 'object') STATE.ip_cache = d.ip_cache;
    console.log('🍓 loaded state: ' + STATE.users.length + ' users, ' + STATE.nodes.length + ' nodes');
  } catch (e) {
    try { fs.renameSync(STATE_FILE, STATE_FILE + '.corrupt'); } catch (e2) {}
    console.log('⚠️ corrupt state file, starting fresh: ' + e.message);
  }
}
function markDirty() { dirty = true; }
function saveState(force) {
  if (!dirty && !force) return;
  dirty = false;
  try {
    const tmp = STATE_FILE + '.tmp';
    fs.writeFileSync(tmp, JSON.stringify(STATE));
    fs.renameSync(tmp, STATE_FILE);
  } catch (e) { console.log('⚠️ save failed: ' + e.message); }
}

// ============ HELPERS ============
function nowIso() { return new Date().toISOString(); }
function newId() { return crypto.randomBytes(6).toString('hex'); }
function newSubKey() { return crypto.randomBytes(9).toString('base64url'); }
function flag(cc) {
  cc = String(cc || '').toUpperCase();
  if (cc.length !== 2 || !/^[A-Z]+$/.test(cc)) return '🏳️';
  return String.fromCodePoint(0x1F1E6 + cc.charCodeAt(0) - 65) + String.fromCodePoint(0x1F1E6 + cc.charCodeAt(1) - 65);
}
function findUser(id) { return STATE.users.find(function (u) { return u.id === id; }) || null; }
function findUserBySub(k) { return STATE.users.find(function (u) { return u.sub_key === k; }) || null; }
function findNode(id) { return STATE.nodes.find(function (n) { return n.id === id; }) || null; }
function isExpired(u) {
  const exp = String(u.expires_at || '').trim();
  if (!exp) return false;
  const t = Date.parse(exp);
  return !isNaN(t) && Date.now() >= t;
}
function publicUser(u) {
  return {
    id: u.id, username: u.username, uuid: u.uuid, sub_key: u.sub_key || '',
    sub_path: '/api/sub/' + (u.sub_key || ''),
    traffic_limit_gb: Number(u.traffic_limit_gb || 0),
    traffic_used_gb: Number(u.traffic_used_gb || 0),
    expires_at: u.expires_at || '', enabled: !!u.enabled, expired: isExpired(u),
    note: u.note || '', created_at: u.created_at || '',
  };
}
function publicNode(n) {
  return {
    id: n.id, name: n.name, address: n.address || '', port: parseInt(n.port || 443, 10) || 443,
    protocol: n.protocol || 'vless', network: n.network || 'tcp', security: n.security || 'none',
    sni: n.sni || '', path: n.path || '', host: n.host || '', fp: n.fp || '', alpn: n.alpn || '',
    reality_pbk: n.reality_pbk || '', reality_sid: n.reality_sid || '', reality_spx: n.reality_spx || '',
    flow: n.flow || '', ss_method: n.ss_method || 'chacha20-ietf-poly1305', ss_password: n.ss_password || '',
    enabled: n.enabled !== false, created_at: n.created_at || '',
    last_check: n.last_check || '',
    online: n.online === undefined ? null : !!n.online,
    latency_ms: n.latency_ms === undefined ? null : n.latency_ms,
    resolved_ip: n.resolved_ip || '', country: n.country || '', country_code: n.country_code || '',
    flag: n.country_code ? flag(n.country_code) : '',
  };
}
function passOk(a, b) {
  const ab = Buffer.from(String(a)), bb = Buffer.from(String(b));
  if (ab.length !== bb.length) return false;
  return crypto.timingSafeEqual(ab, bb);
}

// ============ SESSIONS ============
const SESSIONS = new Map(); // token -> exp ts
function getCookies(req) {
  const out = {};
  const h = req.headers.cookie || '';
  h.split(';').forEach(function (p) {
    const i = p.indexOf('=');
    if (i > 0) {
      try { out[p.slice(0, i).trim()] = decodeURIComponent(p.slice(i + 1).trim()); }
      catch (e) { out[p.slice(0, i).trim()] = p.slice(i + 1).trim(); }
    }
  });
  return out;
}
function checkSession(req) {
  const t = getCookies(req).yorozuya_session;
  if (!t || !SESSIONS.has(t)) return false;
  if (SESSIONS.get(t) < Date.now()) { SESSIONS.delete(t); return false; }
  return true;
}
function requireAuth(req, res, next) {
  if (!checkSession(req)) return res.status(401).json({ detail: 'login required' });
  next();
}

// ============ GEO / NETWORK ============
async function fetchJson(url, opts, timeoutMs) {
  const c = new AbortController();
  const t = setTimeout(function () { c.abort(); }, timeoutMs || 10000);
  try {
    const r = await fetch(url, Object.assign({}, opts, { signal: c.signal }));
    return await r.json();
  } finally { clearTimeout(t); }
}
async function ipLookup(ip) {
  const cached = STATE.ip_cache[ip];
  if (cached && Date.now() - (cached.ts || 0) < IP_CACHE_TTL) return cached;
  const info = { country: '', country_code: '', city: '', isp: '' };
  try {
    const d = await fetchJson('http://ip-api.com/json/' + encodeURIComponent(ip) + '?fields=' + IP_API_FIELDS, null, 10000);
    if (d && d.status === 'success') {
      info.country = d.country || ''; info.country_code = d.countryCode || '';
      info.city = d.city || ''; info.isp = d.isp || d.org || '';
      STATE.ip_cache[ip] = Object.assign({}, info, { ts: Date.now() });
      markDirty();
    }
  } catch (e) {}
  return info;
}
async function ipLookupBatch(ips) {
  const out = {};
  const todo = [];
  ips.forEach(function (ip) {
    if (!ip) return;
    const cached = STATE.ip_cache[ip];
    if (cached && Date.now() - (cached.ts || 0) < IP_CACHE_TTL) out[ip] = cached;
    else todo.push(ip);
  });
  if (todo.length) {
    try {
      const payload = todo.slice(0, 100).map(function (ip) { return { query: ip, fields: 'status,country,countryCode,city,query,isp' }; });
      const arr = await fetchJson('http://ip-api.com/batch', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) }, 15000);
      (arr || []).forEach(function (d) {
        if (d && d.status === 'success' && d.query) {
          const info = { country: d.country || '', country_code: d.countryCode || '', city: d.city || '', isp: d.isp || '', ts: Date.now() };
          STATE.ip_cache[d.query] = info;
          out[d.query] = info;
        }
      });
      markDirty();
    } catch (e) {}
  }
  return out;
}
const OUTBOUND_CACHE = { ts: 0, data: null };
async function getOutboundInfo(force) {
  if (!force && OUTBOUND_CACHE.data && Date.now() - OUTBOUND_CACHE.ts < OUTBOUND_CACHE_TTL) return OUTBOUND_CACHE.data;
  const info = { ip: '', country: '', country_code: '', city: '', isp: '' };
  try {
    const d = await fetchJson('http://ip-api.com/json/?fields=' + IP_API_FIELDS, null, 10000);
    if (d && d.status === 'success') {
      info.ip = d.query || ''; info.country = d.country || ''; info.country_code = d.countryCode || '';
      info.city = d.city || ''; info.isp = d.isp || d.org || '';
    }
  } catch (e) {}
  if (!info.ip) {
    try {
      const c = new AbortController();
      const t = setTimeout(function () { c.abort(); }, 8000);
      const r = await fetch('https://api.ipify.org', { signal: c.signal });
      clearTimeout(t);
      const txt = (await r.text()).trim();
      if (r.ok && txt) info.ip = txt;
    } catch (e) {}
  }
  info.flag = info.country_code ? flag(info.country_code) : '🏳️';
  OUTBOUND_CACHE.ts = Date.now();
  OUTBOUND_CACHE.data = info;
  return info;
}
async function resolveHost(host) {
  host = String(host || '').trim();
  if (!host) return '';
  if (net.isIP(host)) return host;
  try {
    const r = await dns.lookup(host);
    return r.address || '';
  } catch (e) { return ''; }
}
function tcpPing(host, port, timeoutMs) {
  return new Promise(function (resolve) {
    const t0 = Date.now();
    const s = new net.Socket();
    let done = false;
    function fin(ok) {
      if (done) return;
      done = true;
      try { s.destroy(); } catch (e) {}
      resolve(ok ? { ok: true, ms: Date.now() - t0 } : { ok: false, ms: null });
    }
    s.setTimeout(timeoutMs || 5000);
    s.on('connect', function () { fin(true); });
    s.on('timeout', function () { fin(false); });
    s.on('error', function () { fin(false); });
    try { s.connect(port, host); } catch (e) { fin(false); }
  });
}
async function checkNode(node, geo) {
  const ip = await resolveHost(node.address);
  const port = parseInt(node.port || 443, 10) || 443;
  let ok = false, ms = null;
  if (ip) {
    const r = await tcpPing(ip, port, 5000);
    ok = r.ok; ms = r.ms;
  }
  const info = geo || (ip ? await ipLookup(ip) : { country: '', country_code: '', city: '', isp: '' });
  node.last_check = nowIso();
  node.online = ok;
  node.latency_ms = ms;
  node.resolved_ip = ip;
  node.country = info.country || '';
  node.country_code = info.country_code || '';
  markDirty();
  return node;
}

// ============ LINK BUILDERS ============
function q(params) {
  const parts = [];
  Object.keys(params).forEach(function (k) {
    if (String(params[k]) !== '') parts.push(k + '=' + encodeURIComponent(String(params[k])));
  });
  return parts.join('&');
}
function buildLink(userUuid, remark, n) {
  const addr = n.address || '', port = parseInt(n.port || 443, 10) || 443;
  const proto = String(n.protocol || 'vless').toLowerCase();
  const netw = n.network || 'tcp', sec = n.security || 'none';
  const rm = encodeURIComponent(remark);
  if (proto === 'vless') {
    const p = { encryption: 'none', security: sec };
    if (n.sni) p.sni = n.sni;
    if (n.fp) p.fp = n.fp;
    if (n.alpn) p.alpn = n.alpn;
    p.type = netw;
    if ((netw === 'ws' || netw === 'grpc' || netw === 'xhttp') && n.path) p[netw === 'grpc' ? 'serviceName' : 'path'] = n.path;
    if (n.host) p.host = n.host;
    if (sec === 'reality') {
      if (n.reality_pbk) p.pbk = n.reality_pbk;
      if (n.reality_sid) p.sid = n.reality_sid;
      if (n.reality_spx) p.spx = n.reality_spx;
    }
    if (n.flow) p.flow = n.flow;
    return 'vless://' + userUuid + '@' + addr + ':' + port + '?' + q(p) + '#' + rm;
  }
  if (proto === 'vmess') {
    const cfg = {
      v: '2', ps: remark, add: addr, port: String(port), id: userUuid,
      aid: '0', scy: 'auto', net: netw, type: 'none',
      host: n.host || n.sni || '', path: n.path || '',
      tls: sec === 'tls' ? 'tls' : '', sni: n.sni || '', fp: n.fp || '', alpn: n.alpn || '',
    };
    return 'vmess://' + Buffer.from(JSON.stringify(cfg)).toString('base64');
  }
  if (proto === 'trojan') {
    const p = { security: sec !== 'none' ? sec : 'tls' };
    if (n.sni) p.sni = n.sni;
    if (n.fp) p.fp = n.fp;
    if (netw !== 'tcp') p.type = netw;
    if (n.path) p.path = n.path;
    if (n.host) p.host = n.host;
    return 'trojan://' + userUuid + '@' + addr + ':' + port + '?' + q(p) + '#' + rm;
  }
  if (proto === 'ss') {
    const method = n.ss_method || 'chacha20-ietf-poly1305';
    const pwd = n.ss_password || userUuid;
    const creds = Buffer.from(method + ':' + pwd).toString('base64url');
    return 'ss://' + creds + '@' + addr + ':' + port + '#' + rm;
  }
  return '';
}
function userLinks(user) {
  const out = [];
  STATE.nodes.forEach(function (n) {
    if (n.enabled === false) return;
    const cc = n.country_code || '';
    const remark = '🍓 ' + user.username + '｜' + n.name + (cc ? '｜' + cc : '');
    const link = buildLink(user.uuid, remark, n);
    if (link) out.push({ node: n.name, protocol: n.protocol || 'vless', country: n.country || '', country_code: cc, flag: cc ? flag(cc) : '', link: link });
  });
  return out;
}
function subBase(req) {
  const custom = String(STATE.settings.sub_base_url || '').trim().replace(/\/+$/, '');
  if (custom) return custom;
  return req.protocol + '://' + req.get('host');
}
async function qrPng(text) {
  return QRCode.toBuffer(text, { width: 400, margin: 2, color: { dark: '#1a1a2e', light: '#ffffff' } });
}

// ============ SWEEP (expiry + session prune) ============
function sweep() {
  let changed = false;
  STATE.users.forEach(function (u) {
    if (u.enabled && isExpired(u)) { u.enabled = false; changed = true; }
  });
  const now = Date.now();
  SESSIONS.forEach(function (exp, tok) { if (exp < now) SESSIONS.delete(tok); });
  if (changed) { markDirty(); console.log('⏳ expiry sweeper disabled an expired user'); }
}

// ============ APP ============
loadState();
const app = express();
app.set('trust proxy', 1);
app.use(express.json({ limit: '5mb' }));
app.use(function (req, res, next) {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', '*');
  res.header('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

// ---- pages ----
app.get('/', function (req, res) {
  res.redirect(checkSession(req) ? '/panel' : '/login');
});
app.get('/healthz', function (req, res) { res.json({ ok: true, app: 'yorozuya-panel', version: Y_VERSION }); });
app.get('/login', function (req, res) { res.type('html').send(LOGIN_PAGE); });
app.get('/panel', function (req, res) { res.type('html').send(PANEL_PAGE); });
app.get('/p/:sub_key', function (req, res) {
  if (!findUserBySub(req.params.sub_key)) return res.status(404).type('html').send('<h1>404 — not found 🍓</h1>');
  res.type('html').send(SUB_PAGE);
});

// ---- auth ----
app.post('/api/login', function (req, res) {
  if (passOk((req.body && req.body.password) || '', Y_CONFIG.adminPassword)) {
    const token = crypto.randomBytes(32).toString('base64url');
    SESSIONS.set(token, Date.now() + SESSION_TTL);
    res.setHeader('Set-Cookie', 'yorozuya_session=' + token + '; Path=/; HttpOnly; SameSite=Lax; Max-Age=' + Math.floor(SESSION_TTL / 1000));
    return res.json({ ok: true });
  }
  res.status(401).json({ detail: 'wrong password' });
});
app.post('/api/logout', function (req, res) {
  SESSIONS.delete(getCookies(req).yorozuya_session || '');
  res.setHeader('Set-Cookie', 'yorozuya_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0');
  res.json({ ok: true });
});
app.get('/api/me', requireAuth, function (req, res) { res.json({ ok: true, user: 'admin' }); });
app.get('/api/fun/line', requireAuth, function (req, res) {
  res.json({ line: GINTOKI_LINES[crypto.randomInt(GINTOKI_LINES.length)] });
});

// ---- server info ----
app.get('/api/server-info', requireAuth, async function (req, res) {
  const out = await getOutboundInfo(false);
  res.json({
    ok: true, version: Y_VERSION, now: nowIso(), hostname: os.hostname(),
    default_password: DEFAULT_PASSWORD, outbound: out,
    counts: {
      users: STATE.users.length,
      active_users: STATE.users.filter(function (u) { return u.enabled && !isExpired(u); }).length,
      expired_users: STATE.users.filter(isExpired).length,
      nodes: STATE.nodes.length,
      online_nodes: STATE.nodes.filter(function (n) { return n.online === true; }).length,
    },
    fun_line: GINTOKI_LINES[crypto.randomInt(GINTOKI_LINES.length)],
  });
});
app.post('/api/server-info/refresh', requireAuth, async function (req, res) {
  res.json({ ok: true, outbound: await getOutboundInfo(true) });
});

// ---- users ----
app.get('/api/users', requireAuth, function (req, res) {
  res.json({ users: STATE.users.map(publicUser) });
});
app.post('/api/users', requireAuth, function (req, res) {
  const b = req.body || {};
  const username = String(b.username || '').trim();
  if (!username) return res.status(400).json({ detail: 'username required' });
  if (STATE.users.some(function (u) { return u.username === username; })) return res.status(400).json({ detail: 'username exists' });
  const user = {
    id: newId(), username: username, uuid: String(b.uuid || crypto.randomUUID()),
    sub_key: newSubKey(), traffic_limit_gb: Number(b.traffic_limit_gb || 0),
    traffic_used_gb: 0, expires_at: String(b.expires_at || ''),
    enabled: b.enabled !== false, note: String(b.note || ''), created_at: nowIso(),
  };
  STATE.users.push(user);
  markDirty(); saveState(false);
  res.json({ ok: true, user: publicUser(user) });
});
app.patch('/api/users/:id', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  const b = req.body || {};
  if (b.username !== undefined) {
    const name = String(b.username).trim();
    if (!name) return res.status(400).json({ detail: 'username required' });
    if (STATE.users.some(function (x) { return x.username === name && x.id !== u.id; })) return res.status(400).json({ detail: 'username exists' });
    u.username = name;
  }
  if (b.expires_at !== undefined) u.expires_at = String(b.expires_at || '');
  if (b.note !== undefined) u.note = String(b.note || '');
  if (b.traffic_limit_gb !== undefined) { const v = Number(b.traffic_limit_gb); if (isFinite(v)) u.traffic_limit_gb = Math.max(0, v); }
  if (b.traffic_used_gb !== undefined) { const v = Number(b.traffic_used_gb); if (isFinite(v)) u.traffic_used_gb = Math.max(0, v); }
  if (b.enabled !== undefined) u.enabled = !!b.enabled;
  if (b.uuid) u.uuid = String(b.uuid);
  markDirty(); saveState(false);
  res.json({ ok: true, user: publicUser(u) });
});
app.delete('/api/users/:id', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  STATE.users.splice(STATE.users.indexOf(u), 1);
  markDirty(); saveState(false);
  res.json({ ok: true });
});
app.post('/api/users/:id/reset-traffic', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  u.traffic_used_gb = 0;
  markDirty(); saveState(false);
  res.json({ ok: true, user: publicUser(u) });
});
app.post('/api/users/:id/rotate-sub', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  u.sub_key = newSubKey();
  markDirty(); saveState(false);
  res.json({ ok: true, user: publicUser(u) });
});
app.get('/api/users/:id/subscription', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  const base = subBase(req);
  res.json({
    ok: true, user: publicUser(u),
    sub_url: base + '/api/sub/' + u.sub_key,
    page_url: base + '/p/' + u.sub_key,
    qr_url: base + '/api/sub/' + u.sub_key + '/qr',
    links: userLinks(u),
  });
});
app.get('/api/users/:id/qr', requireAuth, async function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  const png = await qrPng(subBase(req) + '/api/sub/' + u.sub_key);
  res.type('png').send(png);
});

// ---- nodes ----
const NODE_FIELDS = ['name', 'address', 'port', 'protocol', 'network', 'security', 'sni', 'path', 'host', 'fp', 'alpn', 'reality_pbk', 'reality_sid', 'reality_spx', 'flow', 'ss_method', 'ss_password', 'enabled'];
app.get('/api/nodes', requireAuth, function (req, res) {
  res.json({ nodes: STATE.nodes.map(publicNode) });
});
app.post('/api/nodes', requireAuth, function (req, res) {
  const b = req.body || {};
  const name = String(b.name || '').trim(), address = String(b.address || '').trim();
  if (!name || !address) return res.status(400).json({ detail: 'name and address required' });
  const node = {
    id: newId(), name: name, address: address,
    port: parseInt(b.port || 443, 10) || 443,
    protocol: String(b.protocol || 'vless').toLowerCase(),
    network: String(b.network || 'tcp').toLowerCase(),
    security: String(b.security || 'none').toLowerCase(),
    sni: String(b.sni || ''), path: String(b.path || ''), host: String(b.host || ''),
    fp: String(b.fp || ''), alpn: String(b.alpn || ''),
    reality_pbk: String(b.reality_pbk || ''), reality_sid: String(b.reality_sid || ''), reality_spx: String(b.reality_spx || ''),
    flow: String(b.flow || ''),
    ss_method: String(b.ss_method || 'chacha20-ietf-poly1305'), ss_password: String(b.ss_password || ''),
    enabled: b.enabled !== false, created_at: nowIso(),
    last_check: '', online: null, latency_ms: null, resolved_ip: '', country: '', country_code: '',
  };
  STATE.nodes.push(node);
  markDirty(); saveState(false);
  checkNode(node).then(function () { saveState(false); }).catch(function () {});
  res.json({ ok: true, node: publicNode(node) });
});
app.patch('/api/nodes/:id', requireAuth, function (req, res) {
  const n = findNode(req.params.id);
  if (!n) return res.status(404).json({ detail: 'node not found' });
  const b = req.body || {};
  NODE_FIELDS.forEach(function (f) { if (b[f] !== undefined && b[f] !== null) n[f] = b[f]; });
  n.port = parseInt(n.port || 443, 10) || 443;
  markDirty(); saveState(false);
  res.json({ ok: true, node: publicNode(n) });
});
app.delete('/api/nodes/:id', requireAuth, function (req, res) {
  const n = findNode(req.params.id);
  if (!n) return res.status(404).json({ detail: 'node not found' });
  STATE.nodes.splice(STATE.nodes.indexOf(n), 1);
  markDirty(); saveState(false);
  res.json({ ok: true });
});
app.post('/api/nodes/:id/check', requireAuth, async function (req, res) {
  const n = findNode(req.params.id);
  if (!n) return res.status(404).json({ detail: 'node not found' });
  await checkNode(n);
  saveState(false);
  res.json({ ok: true, node: publicNode(n) });
});
app.post('/api/nodes/check-all', requireAuth, async function (req, res) {
  if (!STATE.nodes.length) return res.json({ ok: true, nodes: [] });
  const resolved = await Promise.all(STATE.nodes.map(async function (n) { return { n: n, ip: await resolveHost(n.address) }; }));
  const geo = await ipLookupBatch(resolved.map(function (r) { return r.ip; }));
  await Promise.all(resolved.map(async function (r) {
    const n = r.n, ip = r.ip;
    let ok = false, ms = null;
    if (ip) {
      const t = await tcpPing(ip, parseInt(n.port || 443, 10) || 443, 5000);
      ok = t.ok; ms = t.ms;
    }
    const info = geo[ip] || { country: '', country_code: '', city: '', isp: '' };
    n.last_check = nowIso(); n.online = ok; n.latency_ms = ms; n.resolved_ip = ip;
    n.country = info.country || ''; n.country_code = info.country_code || '';
  }));
  markDirty(); saveState(false);
  res.json({ ok: true, nodes: STATE.nodes.map(publicNode) });
});

// ---- tools ----
app.post('/api/tools/lookup', requireAuth, async function (req, res) {
  const query = String((req.body && req.body.query) || '').trim();
  if (!query) return res.status(400).json({ detail: 'query required' });
  const ip = await resolveHost(query);
  const info = ip ? await ipLookup(ip) : { country: '', country_code: '', city: '', isp: '' };
  res.json({
    ok: true,
    result: {
      query: query, resolved_ip: ip, country: info.country || '', country_code: info.country_code || '',
      city: info.city || '', isp: info.isp || '',
      flag: info.country_code ? flag(info.country_code) : '🏳️',
    },
  });
});

// ---- settings + backup (restore takes JSON body — no multipart dep needed) ----
app.get('/api/settings', requireAuth, function (req, res) { res.json({ settings: STATE.settings }); });
app.post('/api/settings', requireAuth, function (req, res) {
  const b = req.body || {};
  if (b.sub_base_url !== undefined) STATE.settings.sub_base_url = String(b.sub_base_url || '').trim().replace(/\/+$/, '');
  markDirty(); saveState(false);
  res.json({ ok: true, settings: STATE.settings });
});
app.get('/api/settings/backup', requireAuth, function (req, res) {
  res.setHeader('Content-Disposition', 'attachment; filename=yorozuya-backup.json');
  res.json(STATE);
});
app.post('/api/settings/restore', requireAuth, function (req, res) {
  const d = (req.body && req.body.data) || req.body || {};
  if (!d || !Array.isArray(d.users) || !Array.isArray(d.nodes)) return res.status(400).json({ detail: 'invalid backup file' });
  STATE.users = d.users;
  STATE.nodes = d.nodes;
  if (d.settings && typeof d.settings === 'object') STATE.settings = Object.assign({ sub_base_url: '' }, d.settings);
  if (d.ip_cache && typeof d.ip_cache === 'object') STATE.ip_cache = d.ip_cache;
  markDirty(); saveState(false);
  res.json({ ok: true, imported: { users: STATE.users.length, nodes: STATE.nodes.length } });
});

// ---- public subscription (no auth) ----
app.get('/api/sub/:sub_key', function (req, res) {
  const u = findUserBySub(req.params.sub_key);
  if (!u || !u.enabled || isExpired(u)) return res.status(404).json({ detail: 'not found' });
  const body = userLinks(u).map(function (x) { return x.link; }).join('\n');
  if (req.query.format === 'text') return res.type('text/plain; charset=utf-8').send(body ? body + '\n' : '');
  const b64 = Buffer.from(body).toString('base64');
  res.setHeader('Content-Disposition', 'inline; filename="' + u.username + '.txt"');
  res.setHeader('Subscription-Userinfo', 'upload=0; download=' + Math.floor(Number(u.traffic_used_gb || 0) * 1073741824) + '; total=' + Math.floor(Number(u.traffic_limit_gb || 0) * 1073741824) + '; expire=0');
  res.type('text/plain; charset=utf-8').send(b64);
});
app.get('/api/sub/:sub_key/qr', async function (req, res) {
  const u = findUserBySub(req.params.sub_key);
  if (!u || !u.enabled || isExpired(u)) return res.status(404).json({ detail: 'not found' });
  const png = await qrPng(subBase(req) + '/api/sub/' + u.sub_key);
  res.type('png').send(png);
});
app.get('/api/public/sub/:sub_key', function (req, res) {
  const u = findUserBySub(req.params.sub_key);
  if (!u || !u.enabled || isExpired(u)) return res.status(404).json({ detail: 'not found' });
  const base = subBase(req);
  res.json({
    ok: true, username: u.username, expires_at: u.expires_at || '', expired: isExpired(u),
    traffic_limit_gb: Number(u.traffic_limit_gb || 0), traffic_used_gb: Number(u.traffic_used_gb || 0),
    sub_url: base + '/api/sub/' + u.sub_key, qr_url: base + '/api/sub/' + u.sub_key + '/qr',
    links: userLinks(u),
  });
});

// ---- background loop ----
setInterval(function () { try { sweep(); saveState(false); } catch (e) { console.log('⚠️ bg loop: ' + e.message); } }, 30000);

// ============ EMBEDDED FRONTEND (3 pages, Gintama theme) ============
// NOTE: pages use template literals — inner code must avoid backticks and ${...}.
const SHARED_CSS = `
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
`;

const LOGO_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><defs><linearGradient id="milk" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#fff7ec"/><stop offset="1" stop-color="#ffd9e3"/></linearGradient><linearGradient id="wood" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#9c6a3c"/><stop offset="1" stop-color="#6f4525"/></linearGradient><linearGradient id="berry" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ff7b9c"/><stop offset="1" stop-color="#e63946"/></linearGradient></defs><path d="M78 96 L178 96 L196 226 Q197 236 187 236 L69 236 Q59 236 60 226 Z" fill="url(#milk)" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M78 96 L108 52 L188 52 L178 96 Z" fill="#ffc7d6" stroke="#3d2712" stroke-width="7" stroke-linejoin="round"/><path d="M108 52 L128 96" stroke="#3d2712" stroke-width="5" stroke-linecap="round"/><rect x="158" y="14" width="16" height="62" rx="8" fill="#4aa3ff" stroke="#3d2712" stroke-width="6" transform="rotate(18 166 45)"/><path d="M128 130 C108 130 96 144 98 160 C100 182 114 200 128 200 C142 200 156 182 158 160 C160 144 148 130 128 130 Z" fill="url(#berry)" stroke="#7a1020" stroke-width="6"/><path d="M112 132 L118 118 L126 128 L134 116 L140 128 L148 120 L144 132 Z" fill="#2ec4b6" stroke="#0d5c55" stroke-width="5" stroke-linejoin="round"/><circle cx="118" cy="158" r="3.5" fill="#ffe9b0"/><circle cx="132" cy="150" r="3.5" fill="#ffe9b0"/><circle cx="140" cy="166" r="3.5" fill="#ffe9b0"/><circle cx="124" cy="176" r="3.5" fill="#ffe9b0"/><rect x="52" y="196" width="152" height="34" rx="8" fill="url(#wood)" stroke="#3d2712" stroke-width="6"/><text x="128" y="221" text-anchor="middle" font-family="serif" font-weight="900" font-size="20" fill="#f4d35e" letter-spacing="4">万事屋</text></svg>`;

const LOGIN_PAGE = `<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍓 ورود | یوروزویا پنل</title>
<style>` + SHARED_CSS + `</style>
</head>
<body>
<div class="strawberry-rain" id="rain"></div>
<div class="login-wrap">
  <div class="card login-card wood" id="card" style="padding-top:26px">
    <div class="logo" style="margin:0 auto">` + LOGO_SVG + `</div>
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
  fetch('/api/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) })
    .then(function (r) {
      if (r.ok) { location.href = '/panel'; return; }
      err.textContent = '❌ رمز اشتباهه! ساداهارو هم نتونست تأییدت کنه 🐶';
      card.classList.remove('shake'); void card.offsetWidth; card.classList.add('shake');
      btn.disabled = false;
    })
    .catch(function () { err.textContent = '❌ خطای اتصال به سرور'; btn.disabled = false; });
}
document.getElementById('pw').focus();
</script>
</body>
</html>`;

const PANEL_JS = `
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
  return fetch(path, opts).then(function (r) {
    if (r.status === 401) { location.href = '/login'; throw new Error('login'); }
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
function logout() { fetch('/api/logout', { method: 'POST' }).then(function () { location.href = '/login'; }).catch(function () { location.href = '/login'; }); }
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
    document.getElementById('ver').textContent = 'v' + d.version + ' · node ⚡';
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
      + '<button class="btn small" onclick="openLinks(\\'' + u.id + '\\')">🔗 لینک‌ها</button>'
      + '<button class="btn small blue" onclick="openUserModal(\\'' + u.id + '\\')">✏️</button>'
      + '<button class="btn small ghost" onclick="toggleUser(\\'' + u.id + '\\')">' + (u.enabled ? '⏸' : '▶️') + '</button>'
      + '<button class="btn small danger" onclick="delUser(\\'' + u.id + '\\')">🗑</button>'
      + '</div></td></tr>';
  });
  tb.innerHTML = html;
}
function findU(id) { for (var i = 0; i < USERS.length; i++) if (USERS[i].id === id) return USERS[i]; return null; }
function findN(id) { for (var i = 0; i < NODES.length; i++) if (NODES[i].id === id) return NODES[i]; return null; }
function openUserModal(id) {
  var u = id ? findU(id) : null;
  var usedRow = u ? '<div><label class="lbl">ترافیک مصرف‌شده (گیگ)</label><input id="f-used" type="number" min="0" step="0.1" value="' + u.traffic_used_gb + '"></div>' : '';
  var extraBtns = u ? '<button class="btn ghost" onclick="resetTraffic(\\'' + u.id + '\\')">🔄 ریست ترافیک</button><button class="btn ghost" onclick="rotateSub(\\'' + u.id + '\\')">🎲 لینک ساب جدید</button>' : '';
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
    + '<div class="mfoot"><button class="btn green" onclick="saveUser(\\'' + (id || '') + '\\')">💾 ذخیره</button>' + extraBtns + '</div>');
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
      rows += '<div class="link-row"><span class="meta">' + l.flag + ' ' + esc(l.node) + ' <span class="badge info">' + esc(l.protocol) + '</span></span><code>' + esc(l.link.slice(0, 70)) + '…</code>'
        + '<button class="btn small" onclick="copyLink(' + i + ')">📋</button></div>';
    });
    document.getElementById('modal-body').innerHTML =
      '<div class="link-row"><span class="meta">🔗 لینک ساب</span><code>' + esc(d.sub_url) + '</code>'
      + '<button class="btn small blue" id="cp-sub">📋</button></div>'
      + '<div class="link-row"><span class="meta">🌐 صفحه اشتراک</span><code>' + esc(d.page_url) + '</code>'
      + '<button class="btn small blue" id="cp-page">📋</button>'
      + '<a class="btn small ghost" href="' + esc(d.page_url) + '" target="_blank">↗️</a></div>'
      + '<div class="qr-box"><img src="' + esc(d.qr_url) + '" alt="QR"></div>'
      + '<h3 style="margin-top:8px">⚔️ کانفیگ‌های تکی (' + d.links.length + ')</h3>' + rows;
    document.getElementById('cp-sub').onclick = function () { copyText(d.sub_url, '🔗 لینک ساب کپی شد!'); };
    document.getElementById('cp-page').onclick = function () { copyText(d.page_url, '🌐 کپی شد!'); };
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
      + '<div class="muted mono" style="font-size:.75rem">' + esc(n.address) + ':' + n.port + ' · ' + esc(n.network) + (n.sni ? ' · sni:' + esc(n.sni) : '') + '</div>'
      + '<div class="foot"><button class="btn small blue" onclick="checkNode(\\'' + n.id + '\\',this)">📡 چک</button>'
      + '<button class="btn small ghost" onclick="openNodeModal(\\'' + n.id + '\\')">✏️ ویرایش</button>'
      + '<button class="btn small ghost" onclick="toggleNode(\\'' + n.id + '\\')">' + (n.enabled ? '⏸' : '▶️') + '</button>'
      + '<button class="btn small danger" onclick="delNode(\\'' + n.id + '\\')">🗑</button></div></div>';
  });
  g.innerHTML = html;
}
function sel(id, opts, cur) {
  var h = '<select id="' + id + '">';
  opts.forEach(function (o) { h += '<option value="' + o + '"' + (o === cur ? ' selected' : '') + '>' + (o === '' ? '(خالی)' : o) + '</option>'; });
  return h + '</select>';
}
function gfield(n, k) { return esc(n ? (n[k] === undefined || n[k] === null ? '' : n[k]) : ''); }
function openNodeModal(id) {
  var n = id ? findN(id) : null;
  openModal(n ? '✏️ ویرایش ' + n.name : '➕ نود جدید',
    '<div class="form-grid">'
    + '<div><label class="lbl">اسم نود *</label><input id="n-name" value="' + gfield(n, 'name') + '" placeholder="آلمان ۱ 🇩🇪"></div>'
    + '<div><label class="lbl">آدرس (دامنه/آی‌پی) *</label><input id="n-address" value="' + gfield(n, 'address') + '" placeholder="de1.example.com" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">پورت</label><input id="n-port" type="number" value="' + (n ? n.port : 443) + '" style="direction:ltr"></div>'
    + '<div><label class="lbl">پروتکل</label>' + sel('n-protocol', ['vless', 'vmess', 'trojan', 'ss'], n ? n.protocol : 'vless') + '</div>'
    + '<div><label class="lbl">نتورک</label>' + sel('n-network', ['tcp', 'ws', 'grpc'], n ? n.network : 'tcp') + '</div>'
    + '<div><label class="lbl">امنیت</label>' + sel('n-security', ['none', 'tls', 'reality'], n ? n.security : 'none') + '</div>'
    + '<div><label class="lbl">SNI</label><input id="n-sni" value="' + gfield(n, 'sni') + '" placeholder="example.com" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Path / ServiceName</label><input id="n-path" value="' + gfield(n, 'path') + '" placeholder="/ws" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Host (هدر)</label><input id="n-host" value="' + gfield(n, 'host') + '" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Fingerprint</label>' + sel('n-fp', ['', 'chrome', 'firefox', 'safari', 'ios', 'android', 'edge', 'random'], n ? n.fp : '') + '</div>'
    + '<div><label class="lbl">ALPN</label><input id="n-alpn" value="' + gfield(n, 'alpn') + '" placeholder="h2,http/1.1" style="direction:ltr;text-align:left"></div>'
    + '<div><label class="lbl">Flow (vless)</label><input id="n-flow" value="' + gfield(n, 'flow') + '" placeholder="xtls-rprx-vision" style="direction:ltr;text-align:left"></div>'
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
    + '<div class="mfoot"><button class="btn green" onclick="saveNode(\\'' + (id || '') + '\\')">💾 ذخیره</button></div>', true);
}
function saveNode(id) {
  var body = {
    name: val('n-name').trim(), address: val('n-address').trim(),
    port: parseInt(val('n-port'), 10) || 443,
    protocol: val('n-protocol'), network: val('n-network'), security: val('n-security'),
    sni: val('n-sni').trim(), path: val('n-path').trim(), host: val('n-host').trim(),
    fp: val('n-fp'), alpn: val('n-alpn').trim(), flow: val('n-flow').trim(),
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
function downloadBackup() { location.href = '/api/settings/backup'; }
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
function loadAll() {
  return loadServerInfo().then(function () { return Promise.all([loadUsers(), loadNodes(), loadSettings()]); });
}
loadAll();
setInterval(function () {
  api('GET', '/api/fun/line').then(function (d) { document.getElementById('fun-line').textContent = d.line; }).catch(function () {});
}, 20000);
`;

const PANEL_PAGE = `<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍓 یوروزویا پنل</title>
<style>` + SHARED_CSS + `</style>
</head>
<body>
<div class="wrap">
  <div class="topbar wood">
    <div class="logo">` + LOGO_SVG + `</div>
    <div>
      <h1>یوروزویا پنل <span class="kanji">万事屋</span></h1>
      <div class="sub">مغازه همه‌کاره گینتاما — نسخه Node.js ⚡🍓</div>
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
<script>` + PANEL_JS + `</script>
</body>
</html>`;

const SUB_JS = `
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
fetch('/api/public/sub/' + encodeURIComponent(KEY)).then(function (r) {
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
  document.getElementById('qr').src = d.qr_url;
  document.title = '🍓 اشتراک ' + d.username + ' | یوروزویا';
  var box = document.getElementById('links');
  box.innerHTML = '';
  if (!d.links.length) box.innerHTML = '<span class="muted">هنوز کانفیگی نیست… گینتاما خوابه 😴</span>';
  d.links.forEach(function (l, i) {
    var row = document.createElement('div');
    row.className = 'link-row';
    row.innerHTML = '<span class="meta">' + l.flag + ' ' + esc(l.node) + ' <span class="badge info">' + esc(l.protocol) + '</span></span><code>' + esc(l.link.slice(0, 60)) + '…</code>';
    var b = document.createElement('button');
    b.className = 'btn small'; b.textContent = '📋 کپی';
    b.onclick = (function (idx) { return function () { copyCfg(idx); }; })(i);
    row.appendChild(b);
    box.appendChild(row);
  });
}).catch(function () { document.getElementById('links').textContent = '❌ خطا در بارگذاری'; });
`;

const SUB_PAGE = `<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍓 اشتراک من | یوروزویا</title>
<style>` + SHARED_CSS + `</style>
</head>
<body>
<div class="wrap" style="max-width:720px">
  <div class="topbar wood" style="justify-content:center;text-align:center">
    <div class="logo">` + LOGO_SVG + `</div>
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
    <div class="qr-box"><img id="qr" alt="QR"></div>
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
<script>` + SUB_JS + `</script>
</body>
</html>`;

// ============ START ============
if (require.main === module) {
  app.listen(Y_CONFIG.port, '0.0.0.0', function () {
    console.log('🍓 YorozuyaPanel Node v' + Y_VERSION + ' on :' + Y_CONFIG.port + ' — data: ' + DATA_DIR);
    if (DEFAULT_PASSWORD) console.log('⚠️  Using default ADMIN_PASSWORD — set ADMIN_PASSWORD env var!');
  });
}

module.exports = { app: app, Y_CONFIG: Y_CONFIG };
