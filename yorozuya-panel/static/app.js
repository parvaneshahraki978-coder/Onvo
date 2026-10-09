/* 🍓 YorozuyaPanel frontend */
let USERS = [], NODES = [], SETTINGS = {sub_base_url: ''};

function escapeHtml(s) {
  return String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
function toast(msg, err = false) {
  const t = document.createElement('div');
  t.className = 'toast' + (err ? ' err' : '');
  t.textContent = msg;
  document.getElementById('toasts').appendChild(t);
  setTimeout(() => t.remove(), 3400);
}
async function api(method, path, body) {
  const opts = {method, headers: {}};
  if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  if (r.status === 401) { location.href = '/login'; throw new Error('login'); }
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.detail || ('خطا ' + r.status));
  return d;
}
async function copyText(txt, msg) {
  try { await navigator.clipboard.writeText(txt); }
  catch (e) {
    const ta = document.createElement('textarea');
    ta.value = txt; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
  }
  toast(msg || '📋 کپی شد!');
}
function faDate(iso) {
  if (!iso) return '♾️ نامحدود';
  try {
    const d = new Date(iso);
    const left = Math.ceil((d - Date.now()) / 86400000);
    return d.toLocaleDateString('fa-IR') + (left >= 0 ? ` (${left} روز ⏳)` : ' (تموم!)');
  } catch (e) { return iso; }
}
function isoToLocal(iso) {
  if (!iso) return '';
  try { const d = new Date(iso); const p = n => String(n).padStart(2, '0');
    return `${d.getFullYear()}-${p(d.getMonth()+1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}`;
  } catch (e) { return ''; }
}
function localToIso(v) {
  if (!v) return '';
  try { return new Date(v).toISOString(); } catch (e) { return ''; }
}

// ── clock + tabs ──────────────────────────────────────────
setInterval(() => {
  document.getElementById('clock').textContent =
    new Date().toLocaleTimeString('fa-IR', {hour: '2-digit', minute: '2-digit'});
}, 1000);
function showTab(name) {
  document.querySelectorAll('.tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
  document.querySelectorAll('.tabpane').forEach(p => p.classList.remove('active'));
  document.getElementById('pane-' + name).classList.add('active');
}
function logout() { fetch('/api/logout', {method: 'POST'}).finally(() => location.href = '/login'); }

// ── modal ─────────────────────────────────────────────────
function openModal(title, html, wide = false) {
  document.getElementById('modal-title').textContent = title;
  document.getElementById('modal-body').innerHTML = html;
  document.getElementById('modal-box').classList.toggle('wide', wide);
  document.getElementById('modal-bg').classList.add('open');
}
function closeModal() { document.getElementById('modal-bg').classList.remove('open'); }
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeModal(); });
function val(id) { return document.getElementById(id)?.value ?? ''; }

// ── dashboard ─────────────────────────────────────────────
async function loadServerInfo() {
  try {
    const d = await api('GET', '/api/server-info');
    document.getElementById('ver').textContent = 'v' + d.version;
    document.getElementById('fun-line').textContent = d.fun_line;
    document.getElementById('pw-warn').style.display = d.default_password ? 'block' : 'none';
    renderOutbound(d.outbound);
    document.getElementById('c-users').textContent = d.counts.users;
    document.getElementById('c-active').textContent = d.counts.active_users;
    document.getElementById('c-nodes').textContent = d.counts.nodes;
    document.getElementById('c-online').textContent = d.counts.online_nodes;
  } catch (e) { /* handled by api() */ }
}
function renderOutbound(o) {
  document.getElementById('out-flag').textContent = o.flag || '🏳️';
  document.getElementById('out-ip').textContent = o.ip || 'نامشخص';
  document.getElementById('out-country').textContent = o.country ? `${o.flag || ''} ${o.country}` : 'کشور نامشخص';
  const city = document.getElementById('out-city');
  if (o.city) { city.style.display = ''; city.textContent = '📍 ' + o.city; } else city.style.display = 'none';
  document.getElementById('out-isp').textContent = o.isp || '';
}
async function refreshOutbound(btn) {
  btn.disabled = true;
  try {
    const d = await api('POST', '/api/server-info/refresh');
    renderOutbound(d.outbound);
    toast('🌍 آی‌پی خروجی رفرش شد!');
  } catch (e) { toast('❌ ' + e.message, true); }
  btn.disabled = false;
}
function renderDashNodes() {
  const box = document.getElementById('dash-nodes');
  if (!NODES.length) { box.innerHTML = '<span class="muted">هنوز نودی نیست… برو تب 🛰 نودها و یکی بساز!</span>'; return; }
  box.innerHTML = NODES.map(n => {
    const st = n.online === true ? '<span class="badge on">آنلاین 💚</span>'
      : n.online === false ? '<span class="badge off">آفلاین 💔</span>' : '<span class="badge warn">چک نشده</span>';
    return `<div class="kv"><span>${n.flag || '🏳️'} <b>${escapeHtml(n.name)}</b> <span class="badge info">${escapeHtml(n.protocol)}</span> <span class="muted mono">${escapeHtml(n.address)}:${n.port}</span></span><span>${st} <span class="muted">${n.latency_ms != null ? n.latency_ms + 'ms' : ''}</span></span></div>`;
  }).join('');
}

// ── users ─────────────────────────────────────────────────
async function loadUsers() {
  const d = await api('GET', '/api/users');
  USERS = d.users;
  renderUsers();
}
function renderUsers() {
  const q = (document.getElementById('user-search').value || '').toLowerCase();
  const tb = document.getElementById('users-body');
  const list = USERS.filter(u => u.username.toLowerCase().includes(q) || (u.note || '').toLowerCase().includes(q));
  if (!list.length) { tb.innerHTML = '<tr><td colspan="5" class="muted">یوزری نیست… ساداهارو حوصله‌ش سر رفته 🐶</td></tr>'; return; }
  tb.innerHTML = list.map(u => {
    const st = u.expired ? '<span class="badge warn">منقضی ⏳</span>'
      : u.enabled ? '<span class="badge on">فعال 💚</span>' : '<span class="badge off">غیرفعال 💔</span>';
    let traf;
    if (u.traffic_limit_gb > 0) {
      const pct = Math.min(100, u.traffic_used_gb / u.traffic_limit_gb * 100);
      traf = `<div style="display:flex;align-items:center;gap:6px"><div class="bar${pct > 85 ? ' hot' : ''}" style="flex:1"><i style="width:${pct}%"></i></div><small class="muted">${u.traffic_used_gb}/${u.traffic_limit_gb}G</small></div>`;
    } else traf = `<small class="muted">${u.traffic_used_gb} GB ♾️</small>`;
    return `<tr>
      <td><b>${escapeHtml(u.username)}</b><br><small class="muted">${escapeHtml(u.note || '')}</small></td>
      <td>${st}</td><td>${traf}</td><td><small>${faDate(u.expires_at)}</small></td>
      <td><div class="row-actions">
        <button class="btn small" onclick="openLinks('${u.id}')">🔗 لینک‌ها</button>
        <button class="btn small blue" onclick="openUserModal('${u.id}')">✏️</button>
        <button class="btn small ghost" onclick="toggleUser('${u.id}')">${u.enabled ? '⏸' : '▶️'}</button>
        <button class="btn small danger" onclick="delUser('${u.id}')">🗑</button>
      </div></td></tr>`;
  }).join('');
}
function openUserModal(id) {
  const u = id ? USERS.find(x => x.id === id) : null;
  openModal(u ? `✏️ ویرایش ${u.username}` : '➕ کاربر جدید', `
    <label class="lbl">نام کاربری *</label>
    <input id="f-username" value="${escapeHtml(u?.username || '')}" placeholder="gintoki">
    <div class="form-grid">
      <div><label class="lbl">سقف ترافیک (گیگ، 0 = نامحدود ♾️)</label>
      <input id="f-limit" type="number" min="0" step="0.5" value="${u?.traffic_limit_gb ?? 0}"></div>
      ${u ? `<div><label class="lbl">ترافیک مصرف‌شده (گیگ)</label>
      <input id="f-used" type="number" min="0" step="0.1" value="${u.traffic_used_gb}"></div>` : ''}
    </div>
    <label class="lbl">انقضا (خالی = نامحدود)</label>
    <input id="f-exp" type="datetime-local" value="${isoToLocal(u?.expires_at || '')}">
    <label class="lbl">یادداشت</label>
    <input id="f-note" value="${escapeHtml(u?.note || '')}" placeholder="…">
    <label class="lbl"><input type="checkbox" id="f-enabled" ${!u || u.enabled ? 'checked' : ''} style="margin-left:8px"> فعال باشه؟</label>
    <div class="mfoot">
      <button class="btn green" onclick="saveUser('${id || ''}')">💾 ذخیره</button>
      ${u ? `<button class="btn ghost" onclick="resetTraffic('${u.id}')">🔄 ریست ترافیک</button>
      <button class="btn ghost" onclick="rotateSub('${u.id}')">🎲 لینک ساب جدید</button>` : ''}
    </div>`);
}
async function saveUser(id) {
  const body = {
    username: val('f-username').trim(),
    traffic_limit_gb: parseFloat(val('f-limit')) || 0,
    expires_at: localToIso(val('f-exp')),
    note: val('f-note'), enabled: document.getElementById('f-enabled').checked,
  };
  if (id && document.getElementById('f-used')) body.traffic_used_gb = parseFloat(val('f-used')) || 0;
  if (!body.username) { toast('❌ اسم کاربر لازمه!', true); return; }
  try {
    if (id) await api('PATCH', '/api/users/' + id, body);
    else await api('POST', '/api/users', body);
    closeModal(); await loadUsers(); await loadServerInfo();
    toast(id ? '✏️ ذخیره شد!' : '🎉 کاربر ساخته شد! ساداهارو دم تکون داد 🐶');
  } catch (e) { toast('❌ ' + e.message, true); }
}
async function delUser(id) {
  const u = USERS.find(x => x.id === id);
  if (!confirm(`یوزر «${u?.username}» حذف بشه؟ 🗑`)) return;
  try { await api('DELETE', '/api/users/' + id); await loadUsers(); await loadServerInfo(); toast('🗑 حذف شد!'); }
  catch (e) { toast('❌ ' + e.message, true); }
}
async function toggleUser(id) {
  const u = USERS.find(x => x.id === id);
  try { await api('PATCH', '/api/users/' + id, {enabled: !u.enabled}); await loadUsers(); await loadServerInfo(); }
  catch (e) { toast('❌ ' + e.message, true); }
}
async function resetTraffic(id) {
  try { await api('POST', `/api/users/${id}/reset-traffic`); closeModal(); await loadUsers(); toast('🔄 ترافیک صفر شد!'); }
  catch (e) { toast('❌ ' + e.message, true); }
}
async function rotateSub(id) {
  try { await api('POST', `/api/users/${id}/rotate-sub`); closeModal(); await loadUsers(); toast('🎲 لینک ساب عوض شد! قبلی باطله.'); }
  catch (e) { toast('❌ ' + e.message, true); }
}
async function openLinks(id) {
  const u = USERS.find(x => x.id === id);
  openModal(`🔗 لینک‌های ${u.username}`, '<div class="muted">در حال ساخت کانفیگ‌ها… 🍓</div>', true);
  try {
    const d = await api('GET', `/api/users/${id}/subscription`);
    const rows = d.links.length ? d.links.map(l => `
      <div class="link-row"><span class="meta">${l.flag} ${escapeHtml(l.node)} <span class="badge info">${escapeHtml(l.protocol)}</span></span><code>${escapeHtml(l.link.slice(0, 70))}…</code>
      <button class="btn small" onclick="copyText(decodeURIComponent('${encodeURIComponent(l.link)}'), '⚔️ کپی شد!')">📋</button></div>`).join('')
      : '<div class="muted">نود فعالی نیست… اول یه نود بساز 🛰</div>';
    document.getElementById('modal-body').innerHTML = `
      <div class="link-row"><span class="meta">🔗 لینک ساب</span><code>${escapeHtml(d.sub_url)}</code>
        <button class="btn small blue" onclick="copyText('${d.sub_url}', '🔗 لینک ساب کپی شد!')">📋</button></div>
      <div class="link-row"><span class="meta">🌐 صفحه اشتراک</span><code>${escapeHtml(d.page_url)}</code>
        <button class="btn small blue" onclick="copyText('${d.page_url}', '🌐 کپی شد!')">📋</button>
        <a class="btn small ghost" href="${d.page_url}" target="_blank">↗️</a></div>
      <div class="qr-box"><img src="${d.qr_url}" alt="QR"></div>
      <h3 style="margin-top:8px">⚔️ کانفیگ‌های تکی (${d.links.length})</h3>${rows}`;
  } catch (e) { document.getElementById('modal-body').innerHTML = '❌ ' + escapeHtml(e.message); }
}

// ── nodes ─────────────────────────────────────────────────
async function loadNodes() {
  const d = await api('GET', '/api/nodes');
  NODES = d.nodes;
  renderNodes(); renderDashNodes();
}
function renderNodes() {
  const g = document.getElementById('nodes-grid');
  if (!NODES.length) { g.innerHTML = '<div class="card muted">نودی نیست… یوروزویا بیکاره 😴 دکمه «نود جدید» رو بزن!</div>'; return; }
  g.innerHTML = NODES.map(n => {
    const st = n.online === true ? '<span class="badge on">آنلاین 💚</span>'
      : n.online === false ? '<span class="badge off">آفلاین 💔</span>' : '<span class="badge warn">چک نشده ❔</span>';
    const en = n.enabled ? '' : ' <span class="badge off">غیرفعال</span>';
    return `<div class="card node-card">
      <div class="head"><span class="flag">${n.flag || '🏳️'}</span>
        <div><div class="name">${escapeHtml(n.name)}${en}</div>
        <div class="muted" style="font-size:.78rem">${escapeHtml(n.country || 'کشور نامشخص')} · <span class="mono">${escapeHtml(n.resolved_ip || n.address)}:${n.port}</span></div></div>
        <span class="proto badge info">${escapeHtml(n.protocol)}${n.security !== 'none' ? '/' + escapeHtml(n.security) : ''}</span>
      </div>
      <div>${st} <span class="muted" style="font-size:.8rem">${n.latency_ms != null ? `⚡ ${n.latency_ms}ms` : ''} ${n.last_check ? '· ' + faDate(n.last_check) : ''}</span></div>
      <div class="muted mono" style="font-size:.75rem">${escapeHtml(n.address)}:${n.port} · ${escapeHtml(n.network)}${n.sni ? ' · sni:' + escapeHtml(n.sni) : ''}</div>
      <div class="foot">
        <button class="btn small blue" onclick="checkNode('${n.id}', this)">📡 چک</button>
        <button class="btn small ghost" onclick="openNodeModal('${n.id}')">✏️ ویرایش</button>
        <button class="btn small ghost" onclick="toggleNode('${n.id}')">${n.enabled ? '⏸' : '▶️'}</button>
        <button class="btn small danger" onclick="delNode('${n.id}')">🗑</button>
      </div></div>`;
  }).join('');
}
function openNodeModal(id) {
  const n = id ? NODES.find(x => x.id === id) : null;
  const g = (k, dflt) => escapeHtml(n?.[k] ?? dflt ?? '');
  const sel = (id_, opts, cur) => `<select id="${id_}">${opts.map(o => `<option value="${o}"${o === cur ? ' selected' : ''}>${o}</option>`).join('')}</select>`;
  openModal(n ? `✏️ ویرایش ${n.name}` : '➕ نود جدید', `
    <div class="form-grid">
      <div><label class="lbl">اسم نود *</label><input id="n-name" value="${g('name')}" placeholder="آلمان ۱ 🇩🇪"></div>
      <div><label class="lbl">آدرس (دامنه/آی‌پی) *</label><input id="n-address" value="${g('address')}" placeholder="de1.example.com" style="direction:ltr;text-align:left"></div>
      <div><label class="lbl">پورت</label><input id="n-port" type="number" value="${n?.port ?? 443}" style="direction:ltr"></div>
      <div><label class="lbl">پروتکل</label>${sel('n-protocol', ['vless', 'vmess', 'trojan', 'ss'], n?.protocol || 'vless')}</div>
      <div><label class="lbl">نتورک</label>${sel('n-network', ['tcp', 'ws', 'grpc'], n?.network || 'tcp')}</div>
      <div><label class="lbl">امنیت</label>${sel('n-security', ['none', 'tls', 'reality'], n?.security || 'none')}</div>
      <div><label class="lbl">SNI</label><input id="n-sni" value="${g('sni')}" placeholder="example.com" style="direction:ltr;text-align:left"></div>
      <div><label class="lbl">Path / ServiceName</label><input id="n-path" value="${g('path')}" placeholder="/ws" style="direction:ltr;text-align:left"></div>
      <div><label class="lbl">Host (هدر)</label><input id="n-host" value="${g('host')}" style="direction:ltr;text-align:left"></div>
      <div><label class="lbl">Fingerprint</label>${sel('n-fp', ['', 'chrome', 'firefox', 'safari', 'ios', 'android', 'edge', 'random'], n?.fp || '')}</div>
      <div><label class="lbl">ALPN</label><input id="n-alpn" value="${g('alpn')}" placeholder="h2,http/1.1" style="direction:ltr;text-align:left"></div>
      <div><label class="lbl">Flow (vless)</label><input id="n-flow" value="${g('flow')}" placeholder="xtls-rprx-vision" style="direction:ltr;text-align:left"></div>
    </div>
    <div class="card" style="margin-top:12px;padding:12px">
      <b style="font-size:.85rem">🌀 REALITY (فقط اگه امنیت = reality)</b>
      <div class="form-grid">
        <div class="full"><label class="lbl">Public Key (pbk)</label><input id="n-pbk" value="${g('reality_pbk')}" style="direction:ltr;text-align:left"></div>
        <div><label class="lbl">Short ID (sid)</label><input id="n-sid" value="${g('reality_sid')}" style="direction:ltr;text-align:left"></div>
        <div><label class="lbl">SpiderX (spx)</label><input id="n-spx" value="${g('reality_spx')}" style="direction:ltr;text-align:left"></div>
      </div>
    </div>
    <div class="card" style="margin-top:12px;padding:12px">
      <b style="font-size:.85rem">👻 Shadowsocks (فقط اگه پروتکل = ss)</b>
      <div class="form-grid">
        <div><label class="lbl">متد</label>${sel('n-ssmethod', ['chacha20-ietf-poly1305', 'aes-256-gcm', 'aes-128-gcm', '2022-blake3-aes-128-gcm', '2022-blake3-aes-256-gcm'], n?.ss_method || 'chacha20-ietf-poly1305')}</div>
        <div><label class="lbl">پسورد (خالی = uuid یوزر)</label><input id="n-sspass" value="${g('ss_password')}" style="direction:ltr;text-align:left"></div>
      </div>
    </div>
    <label class="lbl"><input type="checkbox" id="n-enabled" ${!n || n.enabled ? 'checked' : ''} style="margin-left:8px"> فعال باشه؟</label>
    <div class="mfoot"><button class="btn green" onclick="saveNode('${id || ''}')">💾 ذخیره</button></div>`, true);
}
async function saveNode(id) {
  const body = {
    name: val('n-name').trim(), address: val('n-address').trim(),
    port: parseInt(val('n-port')) || 443,
    protocol: val('n-protocol'), network: val('n-network'), security: val('n-security'),
    sni: val('n-sni').trim(), path: val('n-path').trim(), host: val('n-host').trim(),
    fp: val('n-fp'), alpn: val('n-alpn').trim(), flow: val('n-flow').trim(),
    reality_pbk: val('n-pbk').trim(), reality_sid: val('n-sid').trim(), reality_spx: val('n-spx').trim(),
    ss_method: val('n-ssmethod'), ss_password: val('n-sspass'),
    enabled: document.getElementById('n-enabled').checked,
  };
  if (!body.name || !body.address) { toast('❌ اسم و آدرس نود لازمه!', true); return; }
  try {
    if (id) await api('PATCH', '/api/nodes/' + id, body);
    else await api('POST', '/api/nodes', body);
    closeModal(); await loadNodes(); await loadServerInfo();
    toast(id ? '✏️ نود ذخیره شد!' : '🛰 نود ساخته شد! در حال چک سلامت…');
    if (!id) setTimeout(() => { loadNodes(); }, 6000);
  } catch (e) { toast('❌ ' + e.message, true); }
}
async function delNode(id) {
  const n = NODES.find(x => x.id === id);
  if (!confirm(`نود «${n?.name}» حذف بشه؟ 🗑`)) return;
  try { await api('DELETE', '/api/nodes/' + id); await loadNodes(); await loadServerInfo(); toast('🗑 نود حذف شد!'); }
  catch (e) { toast('❌ ' + e.message, true); }
}
async function toggleNode(id) {
  const n = NODES.find(x => x.id === id);
  try { await api('PATCH', '/api/nodes/' + id, {enabled: !n.enabled}); await loadNodes(); }
  catch (e) { toast('❌ ' + e.message, true); }
}
async function checkNode(id, btn) {
  if (btn) btn.disabled = true;
  try { await api('POST', '/api/nodes/' + id + '/check'); await loadNodes(); await loadServerInfo(); toast('📡 چک شد!'); }
  catch (e) { toast('❌ ' + e.message, true); }
  if (btn) btn.disabled = false;
}
async function checkAllNodes(btn) {
  btn.disabled = true; btn.textContent = '⏳ در حال چک…';
  try { await api('POST', '/api/nodes/check-all'); await loadNodes(); await loadServerInfo(); toast('📡 همه نودها چک شدن!'); }
  catch (e) { toast('❌ ' + e.message, true); }
  btn.disabled = false; btn.textContent = '📡 چک همه نودها';
}

// ── tools ─────────────────────────────────────────────────
async function doLookup() {
  const q = val('lookup-q').trim();
  if (!q) return;
  const box = document.getElementById('lookup-res');
  box.innerHTML = '<span class="muted">⏳ شینپاچی داره عینکش رو تمیز می‌کنه…</span>';
  try {
    const d = await api('POST', '/api/tools/lookup', {query: q});
    const r = d.result;
    box.innerHTML = `<div class="card" style="text-align:center">
      <div class="flag-big">${r.flag}</div>
      <div class="out-ip" style="font-size:1.2rem">${escapeHtml(r.resolved_ip || '؟')}</div>
      <div style="margin:8px 0"><span class="badge gold">${escapeHtml(r.country || 'نامشخص')}</span>
      ${r.city ? `<span class="badge info">📍 ${escapeHtml(r.city)}</span>` : ''}</div>
      <div class="muted" style="font-size:.82rem">${escapeHtml(r.isp || '')}</div></div>`;
  } catch (e) { box.innerHTML = '❌ ' + escapeHtml(e.message); }
}

// ── settings ──────────────────────────────────────────────
async function loadSettings() {
  const d = await api('GET', '/api/settings');
  SETTINGS = d.settings;
  document.getElementById('set-base').value = SETTINGS.sub_base_url || '';
}
async function saveSettings() {
  try {
    await api('POST', '/api/settings', {sub_base_url: val('set-base').trim()});
    toast('💾 ذخیره شد!');
  } catch (e) { toast('❌ ' + e.message, true); }
}
function downloadBackup() { location.href = '/api/settings/backup'; }
async function doRestore(input) {
  const f = input.files[0];
  if (!f) return;
  const fd = new FormData();
  fd.append('file', f);
  try {
    const r = await fetch('/api/settings/restore', {method: 'POST', body: fd});
    if (r.status === 401) { location.href = '/login'; return; }
    const d = await r.json();
    if (!r.ok) throw new Error(d.detail || 'خطا');
    await loadAll();
    toast(`⬆️ ریستور شد! ${d.imported.users} یوزر، ${d.imported.nodes} نود 🎉`);
  } catch (e) { toast('❌ ' + e.message, true); }
  input.value = '';
}

// ── boot ──────────────────────────────────────────────────
async function loadAll() {
  await loadServerInfo();
  await Promise.all([loadUsers(), loadNodes(), loadSettings()]);
}
loadAll();
setInterval(async () => { try { const d = await api('GET', '/api/fun/line'); document.getElementById('fun-line').textContent = d.line; } catch (e) {} }, 20000);
