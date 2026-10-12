'use strict';
/**
 * ====================================================================
 *  🍓 YOROZUYA PANEL — NODE.JS SINGLE-FILE EDITION  万事屋
 * ====================================================================
 *  The ENTIRE panel (backend + frontend) lives in this ONE file.
 *  Just 2 files to deploy: index.js + package.json
 *
 *    npm install
 *    node index.js
 *    → open the printed "Web Panel path" and set your admin password
 *      on first run (no env vars needed). ADMIN_PASSWORD env still
 *      works as an override.
 *
 *  Built to run on the WEAKEST servers: 2 deps, 1 process, no DB,
 *  debounced JSON writes, on-demand QR, tiny intervals.
 *
 *  Extras: real load balancer across nodes (round-robin / least-latency
 *  / random) + keep-alive cron (self-ping) so free hosts never sleep.
 *
 *  Architecture inspired by Blue-Knight-Panel-V3 (single-file Express
 *  bundle + embedded frontend + JSON persistence). All code here is
 *  original. Fan-made Gintama theme, not affiliated with publishers.
 * ====================================================================
 */
const Y_VERSION = '1.4.0';

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
  adminPassword: process.env.ADMIN_PASSWORD || '', // optional override; first-run setup otherwise
  publicUrl: String(process.env.PUBLIC_URL || process.env.RENDER_EXTERNAL_URL || process.env.APP_URL || process.env.DOMAIN || process.env.RAILWAY_PUBLIC_DOMAIN || '').replace(/\/+$/, ''),
  keepaliveMinutes: parseFloat(process.env.KEEPALIVE_MINUTES !== undefined ? process.env.KEEPALIVE_MINUTES : '10') || 0,
  nodeCheckMinutes: parseFloat(process.env.NODE_CHECK_MINUTES !== undefined ? process.env.NODE_CHECK_MINUTES : '5') || 0,
  dataDirHint: process.env.DATA_DIR || '',
};
const ENV_PASSWORD_SET = !!process.env.ADMIN_PASSWORD;
const DEFAULT_PASSWORD = ENV_PASSWORD_SET && Y_CONFIG.adminPassword === 'yorozuya';
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
const STATE = {
  users: [], nodes: [],
  settings: { sub_base_url: '', admin_hash: '' },
  lb: { strategy: 'round-robin', rr: 0, picks: {} },
  ip_cache: {},
};
let dirty = false;
let saveTimer = null;
function loadState() {
  try {
    if (!fs.existsSync(STATE_FILE)) return;
    const d = JSON.parse(fs.readFileSync(STATE_FILE, 'utf8'));
    if (Array.isArray(d.users)) STATE.users = d.users;
    if (Array.isArray(d.nodes)) STATE.nodes = d.nodes;
    if (d.settings && typeof d.settings === 'object') STATE.settings = Object.assign({ sub_base_url: '', admin_hash: '' }, d.settings);
    if (d.lb && typeof d.lb === 'object') STATE.lb = Object.assign({ strategy: 'round-robin', rr: 0, picks: {} }, d.lb);
    if (d.ip_cache && typeof d.ip_cache === 'object') STATE.ip_cache = d.ip_cache;
    console.log('🍓 state: ' + STATE.users.length + ' users, ' + STATE.nodes.length + ' nodes');
  } catch (e) {
    try { fs.renameSync(STATE_FILE, STATE_FILE + '.corrupt'); } catch (e2) {}
    console.log('⚠️ corrupt state file, starting fresh: ' + e.message);
  }
}
// debounced write — weak servers shouldn't fsync on every tiny change
function markDirty() {
  dirty = true;
  if (saveTimer) return;
  saveTimer = setTimeout(function () { saveTimer = null; saveState(false); }, 1000);
  if (saveTimer.unref) saveTimer.unref();
}
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
    mode: n.mode || '', header: n.header || '',
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
function sha256(s) { return crypto.createHash('sha256').update(String(s)).digest('hex'); }
function activeHash() {
  if (ENV_PASSWORD_SET) return sha256(Y_CONFIG.adminPassword);
  return String(STATE.settings.admin_hash || '');
}
function passwordSet() { return !!activeHash(); }

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
  if (!passwordSet()) return res.status(503).json({ detail: 'setup required — open /setup' });
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
    if (netw === 'ws' || netw === 'grpc' || netw === 'xhttp' || netw === 'h2' || netw === 'httpupgrade') {
      if (n.path) p[netw === 'grpc' ? 'serviceName' : 'path'] = n.path;
      if (n.host) p.host = n.host;
      if (netw === 'h2' && !n.host && n.sni) p.host = n.sni;
      if ((netw === 'grpc' || netw === 'xhttp') && n.mode) p.mode = n.mode;
    }
    if (netw === 'kcp') p.header = n.header || 'none';
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
      aid: '0', scy: 'auto', net: netw, type: (netw === 'kcp' && n.header) ? n.header : 'none',
      host: n.host || n.sni || '', path: n.path || '',
      tls: sec === 'tls' ? 'tls' : '', sni: n.sni || '', fp: n.fp || '', alpn: n.alpn || '',
    };
    return 'vmess://' + Buffer.from(JSON.stringify(cfg)).toString('base64');
  }
  if (proto === 'trojan') {
    const p = { security: sec };
    if (n.sni) p.sni = n.sni;
    if (n.fp) p.fp = n.fp;
    if (netw !== 'tcp') p.type = netw;
    if (netw === 'grpc') {
      if (n.path) p.serviceName = n.path;
      if (n.mode) p.mode = n.mode;
    } else if (n.path) p.path = n.path;
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
function linkEntry(user, n) {
  const cc = n.country_code || '';
  const remark = '🍓 ' + user.username + '｜' + n.name + (cc ? '｜' + cc : '');
  const link = buildLink(user.uuid, remark, n);
  if (!link) return null;
  return { node: n.name, node_id: n.id, protocol: n.protocol || 'vless', network: n.network || 'tcp', country: n.country || '', country_code: cc, flag: cc ? flag(cc) : '', link: link };
}
function userLinks(user, mode) {
  const out = [];
  if (mode === 'lb') {
    const p = pickNode();
    if (p) { const e = linkEntry(user, p); if (e) out.push(e); }
    return out;
  }
  STATE.nodes.forEach(function (n) {
    if (n.enabled === false) return;
    if (mode && n.id !== mode) return;
    const e = linkEntry(user, n);
    if (e) out.push(e);
  });
  return out;
}

// ============ BLUE KNIGHT DEFAULT PROTOCOL VARIANTS (per user + node) ============
function variantNode(node, overrides) {
  const n = {};
  Object.keys(node).forEach(function (k) { n[k] = node[k]; });
  Object.keys(overrides).forEach(function (k) { n[k] = overrides[k]; });
  return n;
}
function buildVariants(user, node) {
  const out = [];
  const path = node.path || '/';
  function add(name, label, overrides) {
    const vn = variantNode(node, overrides);
    const link = buildLink(user.uuid, node.name + ' - ' + name, vn);
    if (link) out.push({ node_id: node.id, node: node.name, name: name, label: label, protocol: vn.protocol, network: vn.network, security: vn.security, link: link });
  }
  add('VLESS-WS', 'WebSocket Direct / CDN', { protocol: 'vless', network: 'ws', security: 'none', path: path, host: node.host || '', sni: '', fp: '', alpn: '' });
  add('VLESS-WS-TLS', 'CDN Cloudflare Compatible', { protocol: 'vless', network: 'ws', security: 'tls', path: path, host: node.host || '', sni: node.sni || '', fp: node.fp || 'chrome', alpn: node.alpn || 'http/1.1' });
  add('VLESS-HTTPUpgrade', 'HTTP Upgrade', { protocol: 'vless', network: 'httpupgrade', security: 'none', path: path, host: '', sni: '', fp: '', alpn: '' });
  add('VLESS-gRPC', 'gRPC Direct', { protocol: 'vless', network: 'grpc', security: 'none', path: path, host: '', sni: '', fp: '', alpn: '' });
  add('Trojan-WS', 'Trojan WebSocket Direct', { protocol: 'trojan', network: 'ws', security: 'none', path: path, host: node.host || '', sni: '', fp: '', alpn: '' });
  add('Trojan-gRPC-TLS', 'CDN gRPC Alternative', { protocol: 'trojan', network: 'grpc', security: 'tls', path: path, host: '', sni: node.sni || node.address || '', fp: 'chrome', alpn: 'h2' });
  add('VMess-WS', 'VMess WebSocket Direct', { protocol: 'vmess', network: 'ws', security: 'none', path: path, host: node.host || '', sni: '', fp: '', alpn: '' });
  add('VMess-WS-TLS', 'VMess WebSocket TLS', { protocol: 'vmess', network: 'ws', security: 'tls', path: path, host: node.host || '', sni: node.sni || '', fp: node.fp || 'chrome', alpn: node.alpn || 'http/1.1' });
  if (node.reality_pbk) {
    add('VLESS-REALITY', 'VLESS Reality', { protocol: 'vless', network: 'tcp', security: 'reality', path: '', host: '', sni: node.sni || node.address || '', fp: 'chrome', flow: 'xtls-rprx-vision', reality_pbk: node.reality_pbk, reality_sid: node.reality_sid || '', reality_spx: node.reality_spx || '' });
  }
  add('Shadowsocks', 'Shadowsocks', { protocol: 'ss', network: 'tcp', security: 'none', ss_method: node.ss_method || 'aes-256-gcm', ss_password: node.ss_password || '' });
  return out;
}

// ============ BLUE KNIGHT AUTO NODE (default node = this server itself, real port — never 443) ============
function defaultNodeFor(req) {
  let host = '';
  if (Y_CONFIG.publicUrl) {
    try { host = new URL(Y_CONFIG.publicUrl).hostname; } catch (e) { host = Y_CONFIG.publicUrl.split(':')[0]; }
  }
  if (!host) {
    const h = String(req.get('x-forwarded-host') || req.get('host') || '').split(':')[0];
    host = h || '127.0.0.1';
  }
  return {
    id: newId(), name: 'default', address: host, port: Y_CONFIG.port,
    protocol: 'vless', network: 'ws', security: 'tls',
    sni: host, path: '/', host: '', fp: 'chrome', alpn: 'http/1.1',
    reality_pbk: '', reality_sid: '', reality_spx: '', flow: '',
    ss_method: 'aes-256-gcm', ss_password: '',
    enabled: true, created_at: nowIso(),
    last_check: '', online: null, latency_ms: null, resolved_ip: '', country: '', country_code: '',
  };
}
function ensureDefaultNode(req) {
  if (STATE.nodes.length) return null;
  const node = defaultNodeFor(req);
  STATE.nodes.push(node);
  markDirty();
  checkNode(node).then(function () { saveState(false); }).catch(function () {});
  return node;
}

// ============ LOAD BALANCER (real balancing across healthy nodes) ============
const LB_STRATEGIES = ['round-robin', 'least-latency', 'random'];
function lbState() {
  if (!STATE.lb || typeof STATE.lb !== 'object') STATE.lb = { strategy: 'round-robin', rr: 0, picks: {} };
  if (!STATE.lb.picks || typeof STATE.lb.picks !== 'object') STATE.lb.picks = {};
  if (LB_STRATEGIES.indexOf(STATE.lb.strategy) < 0) STATE.lb.strategy = 'round-robin';
  return STATE.lb;
}
function lbCandidates() {
  const enabled = STATE.nodes.filter(function (n) { return n.enabled !== false; });
  const online = enabled.filter(function (n) { return n.online === true; });
  return online.length ? online : enabled; // fall back to enabled when health unknown
}
function pickNode() {
  const lb = lbState();
  const cands = lbCandidates();
  if (!cands.length) return null;
  let node;
  if (lb.strategy === 'least-latency') {
    node = cands.slice().sort(function (a, b) {
      const la = (a.latency_ms === null || a.latency_ms === undefined) ? Infinity : a.latency_ms;
      const lbb = (b.latency_ms === null || b.latency_ms === undefined) ? Infinity : b.latency_ms;
      return la - lbb;
    })[0];
  } else if (lb.strategy === 'random') {
    node = cands[crypto.randomInt(cands.length)];
  } else {
    lb.rr = Number(lb.rr || 0) % cands.length;
    node = cands[lb.rr];
    lb.rr = (lb.rr + 1) % cands.length;
  }
  lb.picks[node.id] = (lb.picks[node.id] || 0) + 1;
  markDirty();
  return node;
}
function subBase(req) {
  const custom = String(STATE.settings.sub_base_url || '').trim().replace(/\/+$/, '');
  if (custom) return custom;
  if (Y_CONFIG.publicUrl) return Y_CONFIG.publicUrl;
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
app.disable('x-powered-by');
app.set('etag', false);
app.set('trust proxy', 1);
app.use(express.json({ limit: '2mb' }));
app.use(function (req, res, next) {
  res.header('Access-Control-Allow-Origin', '*');
  res.header('Access-Control-Allow-Methods', '*');
  res.header('Access-Control-Allow-Headers', '*');
  if (req.method === 'OPTIONS') return res.sendStatus(204);
  next();
});

// ---- pages ----
app.get('/', function (req, res) {
  res.redirect(!passwordSet() ? '/setup' : (checkSession(req) ? '/panel' : '/login'));
});
app.get('/healthz', function (req, res) { res.json({ ok: true, app: 'yorozuya-panel', version: Y_VERSION }); });
app.get('/login', function (req, res) {
  if (!passwordSet()) return res.redirect('/setup');
  res.type('html').send(LOGIN_PAGE);
});
app.get('/setup', function (req, res) {
  if (passwordSet()) return res.redirect(checkSession(req) ? '/panel' : '/login');
  res.type('html').send(SETUP_PAGE);
});
app.get('/panel', function (req, res) {
  if (!passwordSet()) return res.redirect('/setup');
  if (!checkSession(req)) return res.redirect('/login');
  res.type('html').send(PANEL_PAGE);
});
app.get('/p/:sub_key', function (req, res) {
  if (!findUserBySub(req.params.sub_key)) return res.status(404).type('html').send('<h1>404 — not found 🍓</h1>');
  res.type('html').send(SUB_PAGE);
});

// ---- auth ----
app.post('/api/setup', function (req, res) {
  if (passwordSet()) return res.status(403).json({ detail: 'password already set' });
  const pw = String((req.body && req.body.password) || '');
  if (pw.length < 4) return res.status(400).json({ detail: 'password too short (min 4 chars)' });
  STATE.settings.admin_hash = sha256(pw);
  if (!STATE.users.length) {
    STATE.users.push({ id: newId(), username: 'default', uuid: crypto.randomUUID(), sub_key: newSubKey(), traffic_limit_gb: 0, traffic_used_gb: 0, expires_at: '', enabled: true, note: '🍓 کاربر پیش‌فرض — خودکار (الگوی Blue Knight)', created_at: nowIso() });
  }
  ensureDefaultNode(req);
  markDirty(); saveState(true);
  const token = crypto.randomBytes(32).toString('base64url');
  SESSIONS.set(token, Date.now() + SESSION_TTL);
  res.setHeader('Set-Cookie', 'yorozuya_session=' + token + '; Path=/; HttpOnly; SameSite=Lax; Max-Age=' + Math.floor(SESSION_TTL / 1000));
  res.json({ ok: true });
});
app.post('/api/login', function (req, res) {
  const hash = activeHash();
  if (hash && passOk(sha256(String((req.body && req.body.password) || '')), hash)) {
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
    setup_required: !passwordSet(),
    public_url: Y_CONFIG.publicUrl || null,
    keepalive: { enabled: Y_CONFIG.keepaliveMinutes > 0, interval_minutes: Y_CONFIG.keepaliveMinutes, pings: KEEPALIVE.pings, last_ok: KEEPALIVE.lastOk, last_at: KEEPALIVE.lastAt },
    lb: { strategy: lbState().strategy, candidates: lbCandidates().length },
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
  const username = String(b.username || '').trim() || ('user-' + crypto.randomBytes(4).toString('hex'));
  if (STATE.users.some(function (u) { return u.username === username; })) return res.status(400).json({ detail: 'username exists' });
  const user = {
    id: newId(), username: username, uuid: String(b.uuid || crypto.randomUUID()),
    sub_key: newSubKey(), traffic_limit_gb: Number(b.traffic_limit_gb || 0),
    traffic_used_gb: 0, expires_at: String(b.expires_at || ''),
    enabled: b.enabled !== false, note: String(b.note || ''), created_at: nowIso(),
  };
  STATE.users.push(user);
  ensureDefaultNode(req);
  markDirty();
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
  markDirty();
  res.json({ ok: true, user: publicUser(u) });
});
app.delete('/api/users/:id', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  STATE.users.splice(STATE.users.indexOf(u), 1);
  markDirty();
  res.json({ ok: true });
});
app.post('/api/users/:id/reset-traffic', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  u.traffic_used_gb = 0;
  markDirty();
  res.json({ ok: true, user: publicUser(u) });
});
app.post('/api/users/:id/rotate-sub', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  u.sub_key = newSubKey();
  markDirty();
  res.json({ ok: true, user: publicUser(u) });
});
app.get('/api/users/:id/subscription', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  const base = subBase(req);
  const mode = req.query.node === 'lb' ? 'lb' : undefined;
  res.json({
    ok: true, user: publicUser(u),
    sub_url: base + '/api/sub/' + u.sub_key,
    page_url: base + '/p/' + u.sub_key,
    qr_url: base + '/api/sub/' + u.sub_key + '/qr',
    lb_url: base + '/api/lb/sub/' + u.sub_key,
    links: userLinks(u, mode),
  });
});
app.get('/api/users/:id/qr', requireAuth, async function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  const png = await qrPng(subBase(req) + '/api/sub/' + u.sub_key);
  res.type('png').send(png);
});

app.get('/api/users/:id/variants', requireAuth, function (req, res) {
  const u = findUser(req.params.id);
  if (!u) return res.status(404).json({ detail: 'user not found' });
  const base = subBase(req);
  const variants = [];
  STATE.nodes.forEach(function (n) {
    if (n.enabled === false) return;
    buildVariants(u, n).forEach(function (v) { variants.push(v); });
  });
  res.json({
    ok: true, user: publicUser(u),
    sub_url: base + '/api/sub/' + u.sub_key,
    page_url: base + '/p/' + u.sub_key,
    qr_url: base + '/api/sub/' + u.sub_key + '/qr',
    lb_url: base + '/api/lb/sub/' + u.sub_key,
    variants: variants,
  });
});

// ---- nodes ----
const NODE_FIELDS = ['name', 'address', 'port', 'protocol', 'network', 'security', 'sni', 'path', 'host', 'fp', 'alpn', 'reality_pbk', 'reality_sid', 'reality_spx', 'flow', 'ss_method', 'ss_password', 'mode', 'header', 'enabled'];
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
    mode: String(b.mode || ''), header: String(b.header || ''),
    ss_method: String(b.ss_method || 'chacha20-ietf-poly1305'), ss_password: String(b.ss_password || ''),
    enabled: b.enabled !== false, created_at: nowIso(),
    last_check: '', online: null, latency_ms: null, resolved_ip: '', country: '', country_code: '',
  };
  STATE.nodes.push(node);
  markDirty();
  checkNode(node).then(function () { saveState(false); }).catch(function () {});
  res.json({ ok: true, node: publicNode(node) });
});
app.patch('/api/nodes/:id', requireAuth, function (req, res) {
  const n = findNode(req.params.id);
  if (!n) return res.status(404).json({ detail: 'node not found' });
  const b = req.body || {};
  NODE_FIELDS.forEach(function (f) { if (b[f] !== undefined && b[f] !== null) n[f] = b[f]; });
  n.port = parseInt(n.port || 443, 10) || 443;
  markDirty();
  res.json({ ok: true, node: publicNode(n) });
});
app.delete('/api/nodes/:id', requireAuth, function (req, res) {
  const n = findNode(req.params.id);
  if (!n) return res.status(404).json({ detail: 'node not found' });
  STATE.nodes.splice(STATE.nodes.indexOf(n), 1);
  markDirty();
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
  markDirty();
  res.json({ ok: true, nodes: STATE.nodes.map(publicNode) });
});

// ---- load balancer ----
app.get('/api/lb', requireAuth, function (req, res) {
  const lb = lbState();
  res.json({
    ok: true, strategy: lb.strategy, rr: lb.rr || 0, picks: lb.picks,
    candidates: lbCandidates().map(publicNode),
  });
});
app.post('/api/lb', requireAuth, function (req, res) {
  const lb = lbState();
  const s = String((req.body && req.body.strategy) || '');
  if (LB_STRATEGIES.indexOf(s) < 0) return res.status(400).json({ detail: 'strategy must be one of: ' + LB_STRATEGIES.join(', ') });
  lb.strategy = s;
  markDirty();
  res.json({ ok: true, strategy: lb.strategy, rr: lb.rr || 0, picks: lb.picks, candidates: lbCandidates().map(publicNode) });
});
app.get('/api/lb/pick', requireAuth, function (req, res) {
  const n = pickNode();
  if (!n) return res.status(404).json({ detail: 'no enabled nodes' });
  const out = { ok: true, strategy: lbState().strategy, node: publicNode(n) };
  const uname = String(req.query.username || '').trim();
  const u = uname ? STATE.users.find(function (x) { return x.username === uname; }) : null;
  if (u) { const e = linkEntry(u, n); if (e) out.link = e.link; }
  res.json(out);
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
  markDirty();
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
  if (d.settings && typeof d.settings === 'object') STATE.settings = Object.assign({ sub_base_url: '', admin_hash: '' }, d.settings);
  if (d.ip_cache && typeof d.ip_cache === 'object') STATE.ip_cache = d.ip_cache;
  if (d.lb && typeof d.lb === 'object') STATE.lb = Object.assign({ strategy: 'round-robin', rr: 0, picks: {} }, d.lb);
  markDirty(); saveState(true);
  res.json({ ok: true, imported: { users: STATE.users.length, nodes: STATE.nodes.length } });
});

// ---- public subscription (no auth) — Blue-Knight-exact response (same headers/encoding as Blue-Knight-Panel-V3) ----
function subLinksFor(u, req) {
  const mode = req.query.node === 'lb' ? 'lb' : (req.query.node || undefined);
  return userLinks(u, mode);
}
function findActiveUser(sub_key) {
  const u = findUserBySub(sub_key);
  if (!u || !u.enabled || isExpired(u)) return null;
  return u;
}
function serveSub(req, res, u, links, forceFormat, lbNodeName) {
  const body = links.map(function (x) { return x.link; }).join('\n');
  if (lbNodeName) res.setHeader('X-Yorozuya-LB-Node', lbNodeName);
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Content-Type', 'text/plain; charset=utf-8');
  res.setHeader('Content-Disposition', 'attachment; filename="BlueKnight.txt"');
  res.setHeader('Profile-Update-Interval', '24');
  const usedBytes = Math.floor(Number(u.traffic_used_gb || 0) * 1073741824);
  const totalBytes = Math.floor(Number(u.traffic_limit_gb || 0) * 1073741824);
  const expMs = u.expires_at ? Date.parse(u.expires_at) : NaN;
  const expTs = isNaN(expMs) ? 0 : Math.floor(expMs / 1000);
  res.setHeader('Subscription-Userinfo', 'upload=0; download=' + usedBytes + '; total=' + totalBytes + '; expire=' + expTs);
  const fmt = forceFormat || req.query.format || '';
  if (fmt === 'text') return res.send(body ? body + '\n' : '');
  if (fmt === 'raw') return res.send(body);
  res.send(Buffer.from(body).toString('base64'));
}
app.get('/api/sub/:sub_key', function (req, res) {
  const u = findActiveUser(req.params.sub_key);
  if (!u) return res.status(404).json({ detail: 'not found' });
  serveSub(req, res, u, subLinksFor(u, req), null, null);
});
// Blue-Knight-style subscription paths (same output as Blue-Knight-Panel-V3: /sub + /base64 → base64, /raw → plain text)
app.get('/sub/:sub_key', function (req, res) {
  const u = findActiveUser(req.params.sub_key);
  if (!u) return res.status(404).json({ detail: 'not found' });
  serveSub(req, res, u, subLinksFor(u, req), 'b64', null);
});
app.get('/base64/:sub_key', function (req, res) {
  const u = findActiveUser(req.params.sub_key);
  if (!u) return res.status(404).json({ detail: 'not found' });
  serveSub(req, res, u, subLinksFor(u, req), 'b64', null);
});
app.get('/raw/:sub_key', function (req, res) {
  const u = findActiveUser(req.params.sub_key);
  if (!u) return res.status(404).json({ detail: 'not found' });
  serveSub(req, res, u, subLinksFor(u, req), 'raw', null);
});
// load-balanced subscription: every fetch picks a healthy node (round-robin / least-latency / random)
app.get('/api/lb/sub/:sub_key', function (req, res) {
  const u = findActiveUser(req.params.sub_key);
  if (!u) return res.status(404).json({ detail: 'not found' });
  const links = userLinks(u, 'lb');
  serveSub(req, res, u, links, req.query.format || null, links.length ? links[0].node : null);
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
  const variants = [];
  STATE.nodes.forEach(function (n) {
    if (n.enabled === false) return;
    buildVariants(u, n).forEach(function (v) { variants.push(v); });
  });
  res.json({
    ok: true, username: u.username, expires_at: u.expires_at || '', expired: isExpired(u),
    created_at: u.created_at || '',
    traffic_limit_gb: Number(u.traffic_limit_gb || 0), traffic_used_gb: Number(u.traffic_used_gb || 0),
    sub_url: base + '/api/sub/' + u.sub_key, qr_url: base + '/api/sub/' + u.sub_key + '/qr',
    links: subLinksFor(u, req),
    variants: variants,
  });
});

// ---- background loop (sweep + health-aware node re-check for the LB) ----
let LAST_AUTO_CHECK = 0;
setInterval(function () {
  try { sweep(); saveState(false); } catch (e) { console.log('⚠️ bg loop: ' + e.message); }
  if (Y_CONFIG.nodeCheckMinutes > 0 && Date.now() - LAST_AUTO_CHECK >= Y_CONFIG.nodeCheckMinutes * 60000) {
    LAST_AUTO_CHECK = Date.now();
    STATE.nodes.forEach(function (n, i) {
      if (n.enabled === false) return;
      setTimeout(function () { checkNode(n).catch(function () {}); }, i * 400); // staggered — weak-server friendly
    });
  }
}, 30000);

// ---- cron: keep-alive self-ping (keeps free hosts awake) ----
const KEEPALIVE = { pings: 0, lastOk: false, lastAt: '' };
function selfPingUrl() {
  if (Y_CONFIG.publicUrl) return Y_CONFIG.publicUrl + '/healthz';
  return 'http://127.0.0.1:' + Y_CONFIG.port + '/healthz';
}
async function selfPing() {
  KEEPALIVE.pings++;
  try {
    const r = await fetch(selfPingUrl());
    KEEPALIVE.lastOk = r.ok;
  } catch (e) { KEEPALIVE.lastOk = false; }
  KEEPALIVE.lastAt = nowIso();
  if (!KEEPALIVE.lastOk) console.log('⚠️ keep-alive ping failed (' + selfPingUrl() + ')');
}
if (Y_CONFIG.keepaliveMinutes > 0) {
  setInterval(function () { selfPing().catch(function () {}); }, Y_CONFIG.keepaliveMinutes * 60000);
}

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

const SETUP_PAGE = `<!DOCTYPE html>
<html lang="fa" dir="rtl">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>🍓 ساخت رمز | یوروزویا پنل</title>
<style>` + SHARED_CSS + `</style>
</head>
<body>
<div class="strawberry-rain" id="rain"></div>
<div class="login-wrap">
  <div class="card login-card wood" id="card" style="padding-top:26px">
    <div class="logo" style="margin:0 auto">` + LOGO_SVG + `</div>
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
  fetch('/api/setup', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ password: pw }) })
    .then(function (r) {
      if (r.ok) { location.href = '/panel'; return; }
      return r.json().then(function (d) { err.textContent = '❌ ' + (d.detail || 'خطا'); btn.disabled = false; shake(); });
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
  if (name === 'subs') { fillSubsUsers(); loadVariants(); }
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
  return api('GET', '/api/users').then(function (d) { USERS = d.users; renderUsers(); fillSubsUsers(); });
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
function openUserModal(id) {
  var u = id ? findU(id) : null;
  if (!u) {
    openModal('➕ کاربر جدید (سریع)',
      '<div class="muted" style="font-size:.8rem;margin-bottom:8px">فقط حجم و انقضا رو بده — اسم، uuid و لینک ساب خودکار ساخته می‌شه 🍓</div>' +
      '<div class="form-grid"><div><label class="lbl">سقف ترافیک (گیگ، 0 = نامحدود ♾️)</label>' +
      '<input id="f-limit" type="number" min="0" step="0.5" value="0"></div></div>' +
      '<label class="lbl">انقضا (خالی = نامحدود)</label>' +
      '<input id="f-exp" type="datetime-local" value="">' +
      '<div class="mfoot"><button class="btn green" onclick="saveUser()">💾 ایجاد و نمایش ساب</button></div>');
    return;
  }
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
  if (id && !body.username) { toast('❌ اسم کاربر لازمه!', true); return; }
  (id ? api('PATCH', '/api/users/' + id, body) : api('POST', '/api/users', body)).then(function (r) {
    closeModal();
    return loadUsers().then(loadServerInfo).then(function () {
      toast(id ? '✏️ ذخیره شد!' : '🎉 کاربر ساخته شد! ساداهارو دم تکون داد 🐶');
      if (!id && r && r.user) { SUBS_USER = r.user.id; showTab('subs'); loadVariants(); }
    });
  }).catch(function (e) { toast('❌ ' + e.message, true); });
}
var SUBS_USER = '';
var VARIANTS_CACHE = [];
function fillSubsUsers() {
  var s = document.getElementById('subs-user');
  if (!s) return;
  var cur = SUBS_USER;
  var def = null;
  USERS.forEach(function (u) { if (u.username === 'default' && !def) def = u; });
  if (!cur) cur = def ? def.id : (USERS.length ? USERS[0].id : '');
  var html = '';
  USERS.forEach(function (u) {
    html += '<option value="' + u.id + '"' + (u.id === cur ? ' selected' : '') + '>' + esc(u.username) + (u.username === 'default' ? ' (پیش‌فرض)' : '') + '</option>';
  });
  s.innerHTML = html;
  SUBS_USER = cur;
  s.value = cur;
}
function loadVariants() {
  var box = document.getElementById('subs-variants');
  if (!SUBS_USER) { if (box) box.innerHTML = '<span class="muted">هنوز کاربری نیست… اول یه کاربر بساز 🍓</span>'; return; }
  if (box) box.innerHTML = '<span class="muted">⏳ در حال بارگذاری…</span>';
  api('GET', '/api/users/' + SUBS_USER + '/variants').then(renderSubs).catch(function (e) { if (box) box.innerHTML = '❌ ' + esc(e.message); });
}
function renderSubs(d) {
  var u = d.user || {};
  var days = u.expires_at ? Math.max(0, Math.ceil((new Date(u.expires_at) - Date.now()) / 86400000)) : null;
  var left = u.traffic_limit_gb > 0 ? Math.max(0, u.traffic_limit_gb - u.traffic_used_gb) : null;
  var info = document.getElementById('subs-info');
  if (info) {
    info.innerHTML = '<div class="muted" style="font-size:.8rem;margin:6px 0 10px">👤 ' + esc(u.username || '') +
      ' · 📅 تاریخ ایجاد: ' + (u.created_at ? esc(new Date(u.created_at).toLocaleDateString('fa-IR')) : '—') +
      ' · ⏳ روزهای باقیمانده: ' + (days === null ? '♾️' : days) +
      ' · 📦 حجم باقیمانده: ' + (left === null ? '♾️' : left.toFixed(1) + ' GB') +
      ' · ⏰ انقضا: ' + (u.expires_at ? esc(new Date(u.expires_at).toLocaleDateString('fa-IR')) : '♾️') + '</div>';
  }
  var urls = document.getElementById('subs-urls');
  if (urls) {
    urls.innerHTML =
      '<div class="link-row"><span class="meta">🔗 لینک ساب (base64)</span><code>' + esc(d.sub_url) + '</code><button class="btn small blue" id="subs-cp-sub">📋</button></div>' +
      '<div class="link-row"><span class="meta">🌐 صفحه اشتراک</span><code>' + esc(d.page_url) + '</code><a class="btn small ghost" href="' + esc(d.page_url) + '" target="_blank">↗️</a></div>' +
      '<div class="link-row"><span class="meta">⚖️ لودبالانسر</span><code>' + esc(d.lb_url) + '</code><button class="btn small gold" id="subs-cp-lb">📋</button></div>' +
      '<div class="qr-box" data-qr="' + esc(d.sub_url) + '"></div>';
    document.getElementById('subs-cp-sub').onclick = function () { copyText(d.sub_url, '🔗 کپی شد!'); };
    document.getElementById('subs-cp-lb').onclick = function () { copyText(d.lb_url, '⚖️ کپی شد!'); };
    renderQr();
  }
  var box = document.getElementById('subs-variants');
  if (!box) return;
  VARIANTS_CACHE = d.variants || [];
  if (!VARIANTS_CACHE.length) { box.innerHTML = '<span class="muted">نودی نیست… یه نود بساز 🛰</span>'; return; }
  var order = [];
  var byNode = {};
  VARIANTS_CACHE.forEach(function (v) {
    if (!byNode[v.node]) { byNode[v.node] = []; order.push(v.node); }
    byNode[v.node].push(v);
  });
  var html = '';
  var gi = 0;
  order.forEach(function (nodeName) {
    var vs = byNode[nodeName];
    html += '<div class="card" style="margin-top:12px"><div class="head"><span>🛰 ' + esc(nodeName) + '</span><span class="muted">' + vs.length + ' پروتکل</span></div>';
    vs.forEach(function (v) {
      var idx = gi++;
      html += '<div class="link-row"><span class="meta">' + esc(v.name) + ' <span class="badge info">' + esc(v.protocol) + '/' + esc(v.network) + '</span></span>' +
        '<code>' + esc(v.link.slice(0, 64)) + '…</code>' +
        '<button class="btn small" onclick="copyVariant(' + idx + ')">📋</button>' +
        '<button class="btn small ghost" onclick="showVariantQr(' + idx + ')">🖼</button></div>';
    });
    html += '</div>';
  });
  box.innerHTML = html;
}
function copyVariant(i) { if (VARIANTS_CACHE[i]) copyText(VARIANTS_CACHE[i].link, '📋 کپی شد!'); }
function showVariantQr(i) {
  var v = VARIANTS_CACHE[i];
  if (!v) return;
  openModal('🖼 QR — ' + v.name, '<div class="qr-box" data-qr="' + esc(v.link) + '"></div>');
  renderQr();
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
      + '<div class="qr-box"><img src="' + esc(d.qr_url) + '" alt="QR"></div>'
      + '<h3 style="margin-top:8px">⚔️ کانفیگ‌های تکی (' + d.links.length + ')</h3>' + rows;
    document.getElementById('cp-sub').onclick = function () { copyText(d.sub_url, '🔗 لینک ساب کپی شد!'); };
    document.getElementById('cp-page').onclick = function () { copyText(d.page_url, '🌐 کپی شد!'); };
    document.getElementById('cp-lb').onclick = function () { copyText(d.lb_url, '⚖️ لینک LB کپی شد!'); };
  }).catch(function (e) { document.getElementById('modal-body').innerHTML = '❌ ' + esc(e.message); });
}
function loadNodes() {
  return api('GET', '/api/nodes').then(function (d) { NODES = d.nodes; renderDashNodes(); });
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
    <button class="tab" data-tab="subs" onclick="showTab('subs')">🔗 ساب</button>
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
  <div class="tabpane" id="pane-subs">
    <div class="card">
      <div class="toolbar">
        <h3 style="margin:0">🔗 ساب + همه پروتکل‌ها</h3>
        <div class="spacer"></div>
        <select id="subs-user" onchange="loadVariants()"></select>
        <button class="btn small ghost" onclick="loadVariants()">🔄</button>
      </div>
      <div class="muted" style="font-size:.8rem;margin:6px 0 10px">الگوی Blue Knight Panel V3 — همه پروتکل و ترانسپورتر برای هر نود. بعد از ساخت کاربر خودکار اینجا میاد 🍓</div>
      <div id="subs-info"></div>
      <div id="subs-urls"></div>
      <div id="subs-variants"><span class="muted">…</span></div>
    </div>
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
var SUB_URL = '', SUB_LINKS = [], SUB_VARIANTS = [];
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
function copyVariant(i) { if (SUB_VARIANTS[i]) copyText(SUB_VARIANTS[i].link, '📋 ' + SUB_VARIANTS[i].name + ' کپی شد!'); }
function showVariantQr(i) {
  var v = SUB_VARIANTS[i];
  if (!v) return;
  document.getElementById('qr-modal-title').textContent = '🖼 QR — ' + v.name;
  document.getElementById('qr-modal-body').innerHTML = '<div class="qr-box" data-qr="' + esc(v.link) + '"></div>';
  document.getElementById('qr-modal').classList.add('open');
  renderQr();
}
function faDateOnly(iso) {
  if (!iso) return '—';
  try { return new Date(iso).toLocaleDateString('fa-IR'); } catch (e) { return iso; }
}
function daysLeft(iso) {
  if (!iso) return '♾️';
  var d = new Date(iso);
  var left = Math.ceil((d - Date.now()) / 86400000);
  return left >= 0 ? (left + ' روز') : 'تموم شده!';
}
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
  SUB_LINKS = d.links || [];
  SUB_VARIANTS = d.variants || [];
  document.getElementById('u-name').textContent = d.username;
  document.getElementById('info-card').style.display = 'block';
  document.getElementById('u-created').textContent = faDateOnly(d.created_at);
  document.getElementById('u-days').textContent = daysLeft(d.expires_at);
  document.getElementById('u-left').textContent = d.traffic_limit_gb > 0
    ? Math.max(0, d.traffic_limit_gb - d.traffic_used_gb).toFixed(1) + ' GB'
    : '♾️';
  document.getElementById('u-exp').textContent = faDate(d.expires_at);
  document.getElementById('u-traf').textContent = d.traffic_limit_gb > 0
    ? (d.traffic_used_gb + ' / ' + d.traffic_limit_gb + ' GB')
    : (d.traffic_used_gb + ' GB ♾️');
  document.getElementById('u-count').textContent = SUB_VARIANTS.length + ' ⚔️';
  if (d.traffic_limit_gb > 0) {
    var pct = Math.min(100, d.traffic_used_gb / d.traffic_limit_gb * 100);
    document.getElementById('u-bar').style.width = pct + '%';
    if (pct > 85) document.getElementById('u-bar-wrap').classList.add('hot');
  } else { document.getElementById('u-bar').style.width = '100%'; }
  document.getElementById('qr').src = d.qr_url;
  document.title = '🍓 اشتراک ' + d.username + ' | یوروزویا';
  var box = document.getElementById('sub-variants');
  box.innerHTML = '';
  if (!SUB_VARIANTS.length) { box.innerHTML = '<span class="muted">هنوز کانفیگی نیست… گینتاما خوابه 😴</span>'; return; }
  var order = [];
  var byNode = {};
  SUB_VARIANTS.forEach(function (v) {
    if (!byNode[v.node]) { byNode[v.node] = []; order.push(v.node); }
    byNode[v.node].push(v);
  });
  var html = '';
  var gi = 0;
  order.forEach(function (nodeName) {
    var vs = byNode[nodeName];
    html += '<div class="card" style="margin-top:12px"><div class="head"><span>🛰 ' + esc(nodeName) + '</span><span class="muted">' + vs.length + ' ترانسپورتر</span></div>';
    vs.forEach(function (v) {
      var idx = gi++;
      html += '<div class="link-row"><span class="meta">' + esc(v.name) + ' <span class="badge info">' + esc(v.protocol) + '/' + esc(v.network) + '</span></span>' +
        '<code>' + esc(v.link.slice(0, 56)) + '…</code>' +
        '<button class="btn small" onclick="copyVariant(' + idx + ')">📋</button>' +
        '<button class="btn small ghost" onclick="showVariantQr(' + idx + ')">🖼</button></div>';
    });
    html += '</div>';
  });
  box.innerHTML = html;
}).catch(function () { document.getElementById('sub-variants').textContent = '❌ خطا در بارگذاری'; });
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
      <div><div class="muted" style="font-size:.78rem">📅 تاریخ ایجاد</div><b id="u-created">–</b></div>
      <div><div class="muted" style="font-size:.78rem">⏳ روزهای باقیمانده</div><b id="u-days">–</b></div>
      <div><div class="muted" style="font-size:.78rem">📦 حجم باقیمانده</div><b id="u-left">–</b></div>
      <div><div class="muted" style="font-size:.78rem">⏰ انقضا</div><b id="u-exp">♾️</b></div>
      <div><div class="muted" style="font-size:.78rem">📊 ترافیک مصرف‌شده</div><b id="u-traf">–</b></div>
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
    <h3>🌐 همه ترانسپورترها (همه پروتکل‌ها)</h3>
    <div class="muted" style="font-size:.8rem;margin-bottom:8px">هر نود با همه‌ی پروتکل‌ها و ترانسپورترها — کپی یا QR هر کدام.</div>
    <div id="sub-variants"><span class="muted">در حال بارگذاری…</span></div>
  </div>
  <footer class="foot">🍓 <span class="kanji">万事屋</span> یوروزویا — ما همه‌کار می‌کنیم!</footer>
</div>
<div id="toasts"></div>
<script>` + SUB_JS + `</script>
<div class="modal-bg" id="qr-modal" onclick="if(event.target===this)this.classList.remove('open')">
  <div class="modal card" id="qr-modal-box" style="margin:auto 0">
    <div class="mhead"><h3 id="qr-modal-title"></h3><button class="x" onclick="document.getElementById('qr-modal').classList.remove('open')">✕</button></div>
    <div id="qr-modal-body"></div>
  </div>
</div>
<script>/* qrcode-generator (c) Kazuhiko Arase, MIT license — inlined for offline QR rendering */
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
</script>
</body>
</html>`;

// ============ STARTUP ADDRESS (domain from env → auto-detected public IP → localhost; port always obeys PORT/SERVER_PORT) ============
async function detectPublicIp() {
  try {
    const d = await fetchJson('http://ip-api.com/json/?fields=query', null, 4000);
    if (d && d.query) return String(d.query);
  } catch (e) {}
  try {
    const c = new AbortController();
    const t = setTimeout(function () { c.abort(); }, 4000);
    const r = await fetch('https://api.ipify.org', { signal: c.signal });
    clearTimeout(t);
    const txt = (await r.text()).trim();
    if (r.ok && txt) return txt;
  } catch (e) {}
  return '';
}
async function startupBase() {
  if (Y_CONFIG.publicUrl) return Y_CONFIG.publicUrl;
  const ip = await detectPublicIp();
  if (ip) return 'http://' + ip + ':' + Y_CONFIG.port;
  return 'http://localhost:' + Y_CONFIG.port;
}

// ============ START ============
function shutdown() { try { saveState(true); } catch (e) {} process.exit(0); }
process.on('SIGTERM', shutdown);
process.on('SIGINT', shutdown);

if (require.main === module) {
  app.listen(Y_CONFIG.port, '0.0.0.0', function () {
    console.log('🍓 YorozuyaPanel Node v' + Y_VERSION + ' · 万事屋');
    console.log('⚙️  protocols ready: vless · vmess · trojan · ss');
    console.log('🌐 listening on 0.0.0.0:' + Y_CONFIG.port + ' · data: ' + DATA_DIR);
    // Web Panel path: domain from env → auto-detected public IP → localhost (port obeys PORT/SERVER_PORT)
    startupBase().then(function (base) {
      if (!passwordSet()) console.log('🔐 first run — set admin password at ' + base + '/setup');
      else if (DEFAULT_PASSWORD) console.log('⚠️  default password active — change ADMIN_PASSWORD!');
      if (Y_CONFIG.keepaliveMinutes > 0) console.log('⏰ keep-alive cron: self-ping every ' + Y_CONFIG.keepaliveMinutes + ' min');
      console.log('📍 Web Panel path: /panel  →  ' + base + '/panel');
    }).catch(function () {
      console.log('📍 Web Panel path: /panel  →  http://localhost:' + Y_CONFIG.port + '/panel');
    });
  });
}

module.exports = { app: app, Y_CONFIG: Y_CONFIG, detectPublicIp: detectPublicIp, startupBase: startupBase };
