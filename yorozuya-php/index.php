<?php
/**
 * ====================================================================
 *  🍓 YOROZUYA PANEL — PHP SINGLE-FILE EDITION  万事屋  (v1.1.0)
 * ====================================================================
 *  The ENTIRE panel (backend + frontend) lives in this ONE file.
 *  No composer, no extensions beyond the PHP standard set, no DB.
 *
 *    upload index.php to any free PHP host (000webhost, InfinityFree,
 *    AwardSpace, ...) and open it:
 *      https://your-host/?p=setup   → set the admin password (first run)
 *      https://your-host/?p=panel   → the web panel
 *    All routes use the ?p= query parameter so the file works on ANY
 *    host (Apache, nginx, php -S) with zero configuration.
 *
 *  Built to run on the WEAKEST servers: 1 file, no deps, JSON state,
 *  on-demand node checks, stale-ping keep-alive, client-side QR.
 *
 *  Extras: real load balancer across nodes (round-robin / least-latency
 *  / random) + keep-alive self-ping so free hosts never sleep.
 *
 *  Env vars (all optional): PORT/SERVER_PORT (php -S), PUBLIC_URL /
 *  RENDER_EXTERNAL_URL / APP_URL / DOMAIN, ADMIN_PASSWORD (override),
 *  KEEPALIVE_MINUTES (default 10, 0=disable), NODE_CHECK_MINUTES
 *  (default 5), DATA_DIR.
 *
 *  Architecture inspired by Blue-Knight-Panel-V3. All code is original.
 *  Fan-made Gintama theme, not affiliated with publishers.
 * ====================================================================
 */

$Y_VERSION = '1.1.0';

// ============ CONFIG (env-driven, BK-style) ============
function y_env($name, $default = '') {
    $v = getenv($name);
    return ($v === false || $v === '') ? $default : $v;
}
$CONFIG = array(
    'brand' => 'YorozuyaPanel',
    'kanji' => '万事屋',
    'port' => (int)(y_env('PORT', y_env('SERVER_PORT', '8080')) ?: 8080),
    'admin_password' => y_env('ADMIN_PASSWORD'),
    'public_url' => rtrim(y_env('PUBLIC_URL', y_env('RENDER_EXTERNAL_URL', y_env('APP_URL', y_env('DOMAIN')))), '/'),
    'keepalive_minutes' => (float)y_env('KEEPALIVE_MINUTES', '10'),
    'node_check_minutes' => (float)y_env('NODE_CHECK_MINUTES', '5'),
    'data_dir_hint' => y_env('DATA_DIR'),
);
$ENV_PASSWORD_SET = getenv('ADMIN_PASSWORD') !== false && getenv('ADMIN_PASSWORD') !== '';
$DEFAULT_PASSWORD = $ENV_PASSWORD_SET && $CONFIG['admin_password'] === 'yorozuya';
define('Y_SESSION_TTL', 12 * 3600); // 12h
define('Y_IP_API_FIELDS', 'status,message,country,countryCode,city,query,org,isp');
define('Y_IP_CACHE_TTL', 7 * 24 * 3600);
define('Y_OUTBOUND_CACHE_TTL', 10 * 60);
date_default_timezone_set('UTC');

function y_log($msg) {
    if (defined('STDERR')) @fwrite(STDERR, $msg);
}


$GINTOKI_LINES = array(
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
);

// ============ DATA DIR (fallbacks: DATA_DIR → script dir → tmp) ============
function y_pick_data_dir() {
    global $CONFIG;
    $cands = array();
    if ($CONFIG['data_dir_hint'] !== '') $cands[] = $CONFIG['data_dir_hint'];
    $cands[] = __DIR__ . '/yorozuya-data';
    $cands[] = sys_get_temp_dir() . '/yorozuya-data';
    foreach ($cands as $d) {
        if (!is_dir($d)) @mkdir($d, 0777, true);
        $probe = $d . '/.probe';
        if (@file_put_contents($probe, 'ok') !== false) {
            @unlink($probe);
            return $d;
        }
    }
    return sys_get_temp_dir() . '/yorozuya-data';
}
$DATA_DIR = y_pick_data_dir();
$STATE_FILE = $DATA_DIR . '/state.json';
$SESSIONS_FILE = $DATA_DIR . '/sessions.json';
$KEEPALIVE_FILE = $DATA_DIR . '/keepalive.json';
$LAST_PING_FILE = $DATA_DIR . '/last_ping';
$BANNER_FLAG = $DATA_DIR . '/banner';

// ============ STATE ============
$STATE = array(
    'users' => array(), 'nodes' => array(),
    'settings' => array('sub_base_url' => '', 'admin_hash' => ''),
    'lb' => array('strategy' => 'round-robin', 'rr' => 0, 'picks' => array()),
    'ip_cache' => array(),
);
$SESSIONS = array();
$OUTBOUND_CACHE = array('ts' => 0, 'data' => null);

function y_load_state() {
    global $STATE, $STATE_FILE;
    if (!is_file($STATE_FILE)) return;
    $d = json_decode(@file_get_contents($STATE_FILE), true);
    if (!is_array($d)) {
        @rename($STATE_FILE, $STATE_FILE . '.corrupt');
        return;
    }
    if (isset($d['users']) && is_array($d['users'])) $STATE['users'] = $d['users'];
    if (isset($d['nodes']) && is_array($d['nodes'])) $STATE['nodes'] = $d['nodes'];
    if (isset($d['settings']) && is_array($d['settings'])) $STATE['settings'] = array_merge(array('sub_base_url' => '', 'admin_hash' => ''), $d['settings']);
    if (isset($d['lb']) && is_array($d['lb'])) $STATE['lb'] = array_merge(array('strategy' => 'round-robin', 'rr' => 0, 'picks' => array()), $d['lb']);
    if (isset($d['ip_cache']) && is_array($d['ip_cache'])) $STATE['ip_cache'] = $d['ip_cache'];
    y_log('🍓 state: ' . count($STATE['users']) . ' users, ' . count($STATE['nodes']) . ' nodes' . "\n");
}

function y_save_state() {
    global $STATE, $STATE_FILE;
    $tmp = $STATE_FILE . '.tmp';
    if (@file_put_contents($tmp, json_encode($STATE, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), LOCK_EX) === false) return;
    @rename($tmp, $STATE_FILE);
}

function y_load_sessions() {
    global $SESSIONS, $SESSIONS_FILE;
    $SESSIONS = array();
    if (is_file($SESSIONS_FILE)) {
        $d = json_decode(@file_get_contents($SESSIONS_FILE), true);
        if (is_array($d)) $SESSIONS = $d;
    }
}

function y_save_sessions() {
    global $SESSIONS, $SESSIONS_FILE;
    @file_put_contents($SESSIONS_FILE, json_encode($SESSIONS, JSON_UNESCAPED_UNICODE), LOCK_EX);
}

// ============ HELPERS ============
function y_now_iso() {
    return (new DateTime('now', new DateTimeZone('UTC')))->format('Y-m-d\TH:i:s.v\Z');
}

function y_new_id() {
    return bin2hex(random_bytes(6));
}

function y_new_sub_key() {
    return rtrim(strtr(base64_encode(random_bytes(9)), '+/', '-_'), '=');
}

function y_new_uuid() {
    $b = random_bytes(16);
    $b[6] = chr((ord($b[6]) & 0x0F) | 0x40);
    $b[8] = chr((ord($b[8]) & 0x3F) | 0x80);
    return vsprintf('%s%s-%s-%s-%s-%s%s%s', str_split(bin2hex($b), 4));
}

function y_b64url($data) {
    return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
}

function y_utf8_chr($cp) {
    if ($cp < 0x80) return chr($cp);
    if ($cp < 0x800) return chr(0xC0 | ($cp >> 6)) . chr(0x80 | ($cp & 0x3F));
    if ($cp < 0x10000) return chr(0xE0 | ($cp >> 12)) . chr(0x80 | (($cp >> 6) & 0x3F)) . chr(0x80 | ($cp & 0x3F));
    return chr(0xF0 | ($cp >> 18)) . chr(0x80 | (($cp >> 12) & 0x3F)) . chr(0x80 | (($cp >> 6) & 0x3F)) . chr(0x80 | ($cp & 0x3F));
}

function y_flag($cc) {
    $cc = strtoupper((string)$cc);
    if (strlen($cc) !== 2 || !preg_match('/^[A-Z]{2}$/', $cc)) return '🏳️';
    return y_utf8_chr(0x1F1E6 + ord($cc[0]) - 65) . y_utf8_chr(0x1F1E6 + ord($cc[1]) - 65);
}

function y_find_user($id) {
    global $STATE;
    foreach ($STATE['users'] as $u) if (($u['id'] ?? null) === $id) return $u;
    return null;
}

function y_find_user_by_sub($k) {
    global $STATE;
    foreach ($STATE['users'] as $u) if (($u['sub_key'] ?? null) === $k) return $u;
    return null;
}

function y_find_node($id) {
    global $STATE;
    foreach ($STATE['nodes'] as $n) if (($n['id'] ?? null) === $id) return $n;
    return null;
}

function y_is_expired($u) {
    $exp = trim((string)($u['expires_at'] ?? ''));
    if ($exp === '') return false;
    $t = strtotime($exp);
    return $t !== false && time() >= $t;
}

function y_num($v, $default = 0) {
    if (is_int($v) || is_float($v)) $f = (float)$v;
    elseif (is_bool($v)) $f = $v ? 1.0 : 0.0;
    elseif (is_string($v) && $v !== '' && is_numeric($v)) $f = (float)$v;
    else return $default;
    return fmod($f, 1.0) == 0.0 ? (int)$f : $f;
}

function y_public_user($u) {
    return array(
        'id' => $u['id'] ?? null, 'username' => $u['username'] ?? null, 'uuid' => $u['uuid'] ?? null,
        'sub_key' => $u['sub_key'] ?? '',
        'sub_path' => '/api/sub/' . ($u['sub_key'] ?? ''),
        'traffic_limit_gb' => y_num($u['traffic_limit_gb'] ?? 0),
        'traffic_used_gb' => y_num($u['traffic_used_gb'] ?? 0),
        'expires_at' => $u['expires_at'] ?? '', 'enabled' => !empty($u['enabled']), 'expired' => y_is_expired($u),
        'note' => $u['note'] ?? '', 'created_at' => $u['created_at'] ?? '',
    );
}

function y_public_node($n) {
    $online = array_key_exists('online', $n) ? $n['online'] : null;
    $lat = array_key_exists('latency_ms', $n) ? $n['latency_ms'] : null;
    $cc = $n['country_code'] ?? '';
    return array(
        'id' => $n['id'] ?? null, 'name' => $n['name'] ?? null, 'address' => $n['address'] ?? '', 'port' => y_num($n['port'] ?? 443, 443),
        'protocol' => $n['protocol'] ?? 'vless', 'network' => $n['network'] ?? 'tcp', 'security' => $n['security'] ?? 'none',
        'sni' => $n['sni'] ?? '', 'path' => $n['path'] ?? '', 'host' => $n['host'] ?? '', 'fp' => $n['fp'] ?? '', 'alpn' => $n['alpn'] ?? '',
        'reality_pbk' => $n['reality_pbk'] ?? '', 'reality_sid' => $n['reality_sid'] ?? '', 'reality_spx' => $n['reality_spx'] ?? '',
        'flow' => $n['flow'] ?? '', 'ss_method' => $n['ss_method'] ?? 'chacha20-ietf-poly1305', 'ss_password' => $n['ss_password'] ?? '',
        'enabled' => !array_key_exists('enabled', $n) || $n['enabled'] !== false, 'created_at' => $n['created_at'] ?? '',
        'last_check' => $n['last_check'] ?? '',
        'online' => $online === null ? null : (bool)$online,
        'latency_ms' => $lat === null ? null : $lat,
        'resolved_ip' => $n['resolved_ip'] ?? '', 'country' => $n['country'] ?? '', 'country_code' => $cc,
        'flag' => $cc !== '' ? y_flag($cc) : '',
    );
}

function y_sha256($s) {
    return hash('sha256', (string)$s);
}

function y_active_hash() {
    global $STATE, $ENV_PASSWORD_SET, $CONFIG;
    if ($ENV_PASSWORD_SET) return y_sha256($CONFIG['admin_password']);
    return (string)($STATE['settings']['admin_hash'] ?? '');
}

function y_password_set() {
    return y_active_hash() !== '';
}

// ============ SESSIONS (file-based — works on every host) ============
function y_get_cookies() {
    $out = array();
    $h = $_SERVER['HTTP_COOKIE'] ?? '';
    foreach (explode(';', (string)$h) as $p) {
        $i = strpos($p, '=');
        if ($i > 0) $out[trim(substr($p, 0, $i))] = urldecode(trim(substr($p, $i + 1)));
    }
    return $out;
}

function y_check_session() {
    global $SESSIONS;
    $t = y_get_cookies();
    $t = $t['yorozuya_session'] ?? '';
    if ($t === '' || !isset($SESSIONS[$t])) return false;
    if ($SESSIONS[$t] < time()) {
        unset($SESSIONS[$t]);
        y_save_sessions();
        return false;
    }
    return true;
}

function y_make_session() {
    global $SESSIONS;
    $token = y_b64url(random_bytes(32));
    $SESSIONS[$token] = time() + Y_SESSION_TTL;
    y_save_sessions();
    return $token;
}

function y_session_cookie($token) {
    return 'yorozuya_session=' . $token . '; Path=/; HttpOnly; SameSite=Lax; Max-Age=' . Y_SESSION_TTL;
}

// ============ GEO / NETWORK ============
function y_fetch_json($url, $data = null, $headers = array(), $timeoutMs = 10000, $method = null) {
    $hdrs = array();
    foreach ($headers as $k => $v) $hdrs[] = $k . ': ' . $v;
    $opts = array('http' => array(
        'method' => $method ?: ($data !== null ? 'POST' : 'GET'),
        'timeout' => $timeoutMs / 1000,
        'ignore_errors' => true,
        'header' => implode("\r\n", $hdrs),
    ));
    if ($data !== null) $opts['http']['content'] = $data;
    $raw = @file_get_contents($url, false, stream_context_create($opts));
    if ($raw === false) throw new Exception('fetch failed: ' . $url);
    return json_decode($raw, true);
}

function y_ip_lookup($ip) {
    global $STATE;
    $cached = $STATE['ip_cache'][$ip] ?? null;
    if (is_array($cached) && (time() - ($cached['ts'] ?? 0)) < Y_IP_CACHE_TTL) return $cached;
    $info = array('country' => '', 'country_code' => '', 'city' => '', 'isp' => '');
    try {
        $d = y_fetch_json('http://ip-api.com/json/' . rawurlencode((string)$ip) . '?fields=' . Y_IP_API_FIELDS, null, array(), 10000);
        if (is_array($d) && ($d['status'] ?? '') === 'success') {
            $info['country'] = $d['country'] ?? '';
            $info['country_code'] = $d['countryCode'] ?? '';
            $info['city'] = $d['city'] ?? '';
            $info['isp'] = ($d['isp'] ?? '') ?: ($d['org'] ?? '');
            $STATE['ip_cache'][$ip] = array_merge($info, array('ts' => time()));
            y_save_state();
        }
    } catch (Exception $e) {}
    return $info;
}

function y_ip_lookup_batch($ips) {
    global $STATE;
    $out = array();
    $todo = array();
    foreach ($ips as $ip) {
        if (!$ip) continue;
        $cached = $STATE['ip_cache'][$ip] ?? null;
        if (is_array($cached) && (time() - ($cached['ts'] ?? 0)) < Y_IP_CACHE_TTL) $out[$ip] = $cached;
        else $todo[] = $ip;
    }
    if ($todo) {
        try {
            $payload = json_encode(array_map(function ($ip) {
                return array('query' => $ip, 'fields' => 'status,country,countryCode,city,query,isp');
            }, array_slice($todo, 0, 100)));
            $arr = y_fetch_json('http://ip-api.com/batch', $payload, array('Content-Type' => 'application/json'), 15000, 'POST');
            foreach ((is_array($arr) ? $arr : array()) as $d) {
                if (is_array($d) && ($d['status'] ?? '') === 'success' && !empty($d['query'])) {
                    $info = array('country' => $d['country'] ?? '', 'country_code' => $d['countryCode'] ?? '',
                        'city' => $d['city'] ?? '', 'isp' => $d['isp'] ?? '', 'ts' => time());
                    $STATE['ip_cache'][$d['query']] = $info;
                    $out[$d['query']] = $info;
                }
            }
            y_save_state();
        } catch (Exception $e) {}
    }
    return $out;
}

function y_get_outbound_info($force = false) {
    global $OUTBOUND_CACHE;
    if (!$force && $OUTBOUND_CACHE['data'] && (time() - $OUTBOUND_CACHE['ts']) < Y_OUTBOUND_CACHE_TTL) return $OUTBOUND_CACHE['data'];
    $info = array('ip' => '', 'country' => '', 'country_code' => '', 'city' => '', 'isp' => '');
    try {
        $d = y_fetch_json('http://ip-api.com/json/?fields=' . Y_IP_API_FIELDS, null, array(), 10000);
        if (is_array($d) && ($d['status'] ?? '') === 'success') {
            $info['ip'] = $d['query'] ?? '';
            $info['country'] = $d['country'] ?? '';
            $info['country_code'] = $d['countryCode'] ?? '';
            $info['city'] = $d['city'] ?? '';
            $info['isp'] = ($d['isp'] ?? '') ?: ($d['org'] ?? '');
        }
    } catch (Exception $e) {}
    if ($info['ip'] === '') {
        try {
            $raw = @file_get_contents('https://api.ipify.org', false, stream_context_create(array('http' => array('timeout' => 8))));
            if (is_string($raw)) {
                $txt = trim($raw);
                if ($txt !== '') $info['ip'] = $txt;
            }
        } catch (Exception $e) {}
    }
    $info['flag'] = $info['country_code'] !== '' ? y_flag($info['country_code']) : '🏳️';
    $OUTBOUND_CACHE['ts'] = time();
    $OUTBOUND_CACHE['data'] = $info;
    return $info;
}

function y_resolve_host($host) {
    $host = trim((string)$host);
    if ($host === '') return '';
    if (@inet_pton($host) !== false) return $host;
    $ip = @gethostbyname($host);
    return ($ip === $host) ? '' : $ip;
}

function y_tcp_ping($host, $port, $timeoutMs = 5000) {
    $t0 = microtime(true);
    $errno = 0; $errstr = '';
    $fp = @stream_socket_client('tcp://' . $host . ':' . $port, $errno, $errstr, $timeoutMs / 1000);
    if ($fp) {
        @fclose($fp);
        return array('ok' => true, 'ms' => (int)round((microtime(true) - $t0) * 1000));
    }
    return array('ok' => false, 'ms' => null);
}

function y_check_node(&$node, $geo = null) {
    $ip = y_resolve_host($node['address'] ?? '');
    $port = y_num($node['port'] ?? 443, 443) ?: 443;
    $ok = false;
    $ms = null;
    if ($ip !== '') {
        $r = y_tcp_ping($ip, $port, 5000);
        $ok = $r['ok'];
        $ms = $r['ms'];
    }
    $info = $geo ?: ($ip !== '' ? y_ip_lookup($ip) : array('country' => '', 'country_code' => '', 'city' => '', 'isp' => ''));
    $node['last_check'] = y_now_iso();
    $node['online'] = $ok;
    $node['latency_ms'] = $ms;
    $node['resolved_ip'] = $ip;
    $node['country'] = $info['country'] ?? '';
    $node['country_code'] = $info['country_code'] ?? '';
    y_save_state();
    return $node;
}

// ============ LINK BUILDERS ============
function y_js_uri($s) {
    // encodeURIComponent equivalent
    return str_replace(array('%21', '%2A', '%27', '%28', '%29'), array('!', '*', "'", '(', ')'), rawurlencode((string)$s));
}

function y_q($params) {
    $parts = array();
    foreach ($params as $k => $v) {
        if ((string)$v !== '') $parts[] = $k . '=' . y_js_uri($v);
    }
    return implode('&', $parts);
}

function y_build_link($userUuid, $remark, $n) {
    $addr = $n['address'] ?? '';
    $port = y_num($n['port'] ?? 443, 443) ?: 443;
    $proto = strtolower((string)($n['protocol'] ?? 'vless'));
    $netw = $n['network'] ?? 'tcp';
    $sec = $n['security'] ?? 'none';
    $rm = y_js_uri($remark);
    if ($proto === 'vless') {
        $p = array('encryption' => 'none', 'security' => $sec);
        if (!empty($n['sni'])) $p['sni'] = $n['sni'];
        if (!empty($n['fp'])) $p['fp'] = $n['fp'];
        if (!empty($n['alpn'])) $p['alpn'] = $n['alpn'];
        $p['type'] = $netw;
        if (in_array($netw, array('ws', 'grpc', 'xhttp'), true) && !empty($n['path'])) {
            $p[$netw === 'grpc' ? 'serviceName' : 'path'] = $n['path'];
        }
        if (!empty($n['host'])) $p['host'] = $n['host'];
        if ($sec === 'reality') {
            if (!empty($n['reality_pbk'])) $p['pbk'] = $n['reality_pbk'];
            if (!empty($n['reality_sid'])) $p['sid'] = $n['reality_sid'];
            if (!empty($n['reality_spx'])) $p['spx'] = $n['reality_spx'];
        }
        if (!empty($n['flow'])) $p['flow'] = $n['flow'];
        return 'vless://' . $userUuid . '@' . $addr . ':' . $port . '?' . y_q($p) . '#' . $rm;
    }
    if ($proto === 'vmess') {
        $cfg = array(
            'v' => '2', 'ps' => $remark, 'add' => $addr, 'port' => (string)$port, 'id' => $userUuid,
            'aid' => '0', 'scy' => 'auto', 'net' => $netw, 'type' => 'none',
            'host' => ($n['host'] ?? '') ?: ($n['sni'] ?? ''), 'path' => $n['path'] ?? '',
            'tls' => $sec === 'tls' ? 'tls' : '', 'sni' => $n['sni'] ?? '', 'fp' => $n['fp'] ?? '', 'alpn' => $n['alpn'] ?? '',
        );
        return 'vmess://' . base64_encode(json_encode($cfg, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
    }
    if ($proto === 'trojan') {
        $p = array('security' => $sec !== 'none' ? $sec : 'tls');
        if (!empty($n['sni'])) $p['sni'] = $n['sni'];
        if (!empty($n['fp'])) $p['fp'] = $n['fp'];
        if ($netw !== 'tcp') $p['type'] = $netw;
        if (!empty($n['path'])) $p['path'] = $n['path'];
        if (!empty($n['host'])) $p['host'] = $n['host'];
        return 'trojan://' . $userUuid . '@' . $addr . ':' . $port . '?' . y_q($p) . '#' . $rm;
    }
    if ($proto === 'ss') {
        $method = $n['ss_method'] ?? 'chacha20-ietf-poly1305';
        $pwd = ($n['ss_password'] ?? '') ?: $userUuid;
        return 'ss://' . y_b64url($method . ':' . $pwd) . '@' . $addr . ':' . $port . '#' . $rm;
    }
    return '';
}

function y_link_entry($user, $n) {
    $cc = $n['country_code'] ?? '';
    $remark = '🍓 ' . $user['username'] . '｜' . $n['name'] . ($cc !== '' ? '｜' . $cc : '');
    $link = y_build_link($user['uuid'], $remark, $n);
    if ($link === '') return null;
    return array(
        'node' => $n['name'], 'node_id' => $n['id'], 'protocol' => $n['protocol'] ?? 'vless',
        'country' => $n['country'] ?? '', 'country_code' => $cc,
        'flag' => $cc !== '' ? y_flag($cc) : '', 'link' => $link,
    );
}

function y_user_links($user, $mode = null) {
    global $STATE;
    $out = array();
    if ($mode === 'lb') {
        $p = y_pick_node();
        if ($p) {
            $e = y_link_entry($user, $p);
            if ($e) $out[] = $e;
        }
        return $out;
    }
    foreach ($STATE['nodes'] as $n) {
        if (array_key_exists('enabled', $n) && $n['enabled'] === false) continue;
        if ($mode && ($n['id'] ?? null) !== $mode) continue;
        $e = y_link_entry($user, $n);
        if ($e) $out[] = $e;
    }
    return $out;
}

// ============ LOAD BALANCER (real balancing across healthy nodes) ============
$LB_STRATEGIES = array('round-robin', 'least-latency', 'random');

function y_lb_state() {
    global $STATE, $LB_STRATEGIES;
    if (!isset($STATE['lb']) || !is_array($STATE['lb'])) $STATE['lb'] = array('strategy' => 'round-robin', 'rr' => 0, 'picks' => array());
    if (!isset($STATE['lb']['picks']) || !is_array($STATE['lb']['picks'])) $STATE['lb']['picks'] = array();
    if (!in_array($STATE['lb']['strategy'], $LB_STRATEGIES, true)) $STATE['lb']['strategy'] = 'round-robin';
    return $STATE['lb'];
}

function y_lb_candidates() {
    global $STATE;
    $enabled = array();
    $online = array();
    foreach ($STATE['nodes'] as $n) {
        if (array_key_exists('enabled', $n) && $n['enabled'] === false) continue;
        $enabled[] = $n;
        if (($n['online'] ?? null) === true) $online[] = $n;
    }
    return $online ? $online : $enabled; // fall back to enabled when health unknown
}

function y_pick_node() {
    global $STATE;
    $lb = y_lb_state();
    $cands = y_lb_candidates();
    if (!$cands) return null;
    if ($lb['strategy'] === 'least-latency') {
        usort($cands, function ($a, $b) {
            $la = is_numeric($a['latency_ms'] ?? null) ? (float)$a['latency_ms'] : INF;
            $lb2 = is_numeric($b['latency_ms'] ?? null) ? (float)$b['latency_ms'] : INF;
            return $la <=> $lb2;
        });
        $node = $cands[0];
    } elseif ($lb['strategy'] === 'random') {
        $node = $cands[random_int(0, count($cands) - 1)];
    } else {
        $rr = ((int)($lb['rr'] ?? 0)) % count($cands);
        $node = $cands[$rr];
        $lb['rr'] = ($rr + 1) % count($cands);
        $STATE['lb']['rr'] = $lb['rr'];
    }
    $nid = $node['id'];
    $STATE['lb']['picks'][$nid] = (int)($STATE['lb']['picks'][$nid] ?? 0) + 1;
    y_save_state();
    return $node;
}

function y_proto() {
    $p = $_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '';
    if ($p !== '') {
        $parts = explode(',', $p);
        return trim($parts[0]);
    }
    return (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') ? 'https' : 'http';
}

function y_host() {
    return $_SERVER['HTTP_X_FORWARDED_HOST'] ?? ($_SERVER['HTTP_HOST'] ?? 'localhost');
}

function y_sub_base() {
    global $STATE, $CONFIG;
    $custom = rtrim(trim((string)($STATE['settings']['sub_base_url'] ?? '')), '/');
    if ($custom !== '') return $custom;
    if ($CONFIG['public_url']) return $CONFIG['public_url'];
    return y_proto() . '://' . y_host();
}

function y_url($route) {
    // script-aware ?p= URL builder (works at root, in subdirs, or renamed)
    $base = y_sub_base();
    $script = str_replace('\\', '/', (string)($_SERVER['SCRIPT_NAME'] ?? '/index.php'));
    if (basename($script) === 'index.php') {
        $prefix = rtrim(dirname($script), '/');
        return $base . $prefix . '/?p=' . $route;
    }
    return $base . $script . '?p=' . $route;
}

// ============ SWEEP (expiry + session prune) ============
function y_sweep() {
    global $STATE, $SESSIONS;
    $changed = false;
    foreach ($STATE['users'] as $i => $u) {
        if (!empty($u['enabled']) && y_is_expired($u)) {
            $STATE['users'][$i]['enabled'] = false;
            $changed = true;
        }
    }
    $now = time();
    $pruned = false;
    foreach ($SESSIONS as $tok => $exp) {
        if ($exp < $now) {
            unset($SESSIONS[$tok]);
            $pruned = true;
        }
    }
    if ($changed) {
        y_save_state();
        y_log("⏳ expiry sweeper disabled an expired user\n");
    }
    if ($pruned) y_save_sessions();
}

// ============ EMBEDDED FRONTEND (3 pages + public sub page, Gintama theme) ============
$PAGE_LOGIN = <<<'YOR_PAGE_EOF'
<!DOCTYPE html>
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
var API_BASE = '?p=';
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
</html>
YOR_PAGE_EOF;
$PAGE_SETUP = <<<'YOR_PAGE_EOF'
<!DOCTYPE html>
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
var API_BASE = '?p=';
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
</html>
YOR_PAGE_EOF;
$PAGE_PANEL = <<<'YOR_PAGE_EOF'
<!DOCTYPE html>
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
      <div class="sub">مغازه همه‌کاره گینتاما — نسخه PHP 🐘</div>
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
var API_BASE = '?p=';
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
    document.getElementById('ver').textContent = 'v' + d.version + ' · php 🐘';
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
      rows += '<div class="link-row"><span class="meta">' + l.flag + ' ' + esc(l.node) + ' <span class="badge info">' + esc(l.protocol) + '</span></span><code>' + esc(l.link.slice(0, 70)) + '…</code>'
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
      + '<div class="muted mono" style="font-size:.75rem">' + esc(n.address) + ':' + n.port + ' · ' + esc(n.network) + (n.sni ? ' · sni:' + esc(n.sni) : '') + '</div>'
      + '<div class="foot"><button class="btn small blue" onclick="checkNode(\'' + n.id + '\',this)">📡 چک</button>'
      + '<button class="btn small ghost" onclick="openNodeModal(\'' + n.id + '\')">✏️ ویرایش</button>'
      + '<button class="btn small ghost" onclick="toggleNode(\'' + n.id + '\')">' + (n.enabled ? '⏸' : '▶️') + '</button>'
      + '<button class="btn small danger" onclick="delNode(\'' + n.id + '\')">🗑</button></div></div>';
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
    + '<div class="mfoot"><button class="btn green" onclick="saveNode(\'' + (id || '') + '\')">💾 ذخیره</button></div>', true);
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
</html>
YOR_PAGE_EOF;
$PAGE_SUB = <<<'YOR_PAGE_EOF'
<!DOCTYPE html>
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
var API_BASE = '?p=';
var KEY = (new URLSearchParams(location.search).get('key') || '');
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
    row.innerHTML = '<span class="meta">' + l.flag + ' ' + esc(l.node) + ' <span class="badge info">' + esc(l.protocol) + '</span></span><code>' + esc(l.link.slice(0, 60)) + '…</code>';
    var b = document.createElement('button');
    b.className = 'btn small'; b.textContent = '📋 کپی';
    b.onclick = (function (idx) { return function () { copyCfg(idx); }; })(i);
    row.appendChild(b);
    box.appendChild(row);
  });
}).catch(function () { document.getElementById('links').textContent = '❌ خطا در بارگذاری'; });
</script>
</body>
</html>
YOR_PAGE_EOF;

// ============ HTTP HELPERS ============
function y_send($code, $body, $contentType = 'application/json; charset=utf-8', $headers = array()) {
    if (!headers_sent()) {
        http_response_code($code);
        header('Content-Type: ' . $contentType);
        header('Access-Control-Allow-Origin: *');
        header('Access-Control-Allow-Methods: *');
        header('Access-Control-Allow-Headers: *');
        foreach ($headers as $k => $v) header($k . ': ' . $v);
    }
    echo $body;
}

function y_json($code, $obj, $headers = array()) {
    y_send($code, json_encode($obj, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), 'application/json; charset=utf-8', $headers);
}

function y_redirect($loc) {
    if (!headers_sent()) {
        http_response_code(302);
        header('Location: ' . $loc);
        header('Access-Control-Allow-Origin: *');
    }
    exit;
}

// ============ KEEP-ALIVE (stale-ping self-request — keeps free hosts awake) ============
function y_self_ping_url() {
    global $CONFIG;
    if ($CONFIG['public_url']) return $CONFIG['public_url'] . '/healthz';
    return y_proto() . '://' . y_host() . '/healthz';
}

function y_keepalive_tick() {
    global $CONFIG, $LAST_PING_FILE, $KEEPALIVE_FILE;
    if ($CONFIG['keepalive_minutes'] <= 0) return;
    $last = @filemtime($LAST_PING_FILE);
    if ($last === false) $last = 0;
    if ((time() - $last) < $CONFIG['keepalive_minutes'] * 60) return;
    @touch($LAST_PING_FILE);
    $ka = array('pings' => 0, 'last_ok' => false, 'last_at' => '');
    if (is_file($KEEPALIVE_FILE)) {
        $d = json_decode(@file_get_contents($KEEPALIVE_FILE), true);
        if (is_array($d)) $ka = array_merge($ka, $d);
    }
    $ka['pings'] = (int)$ka['pings'] + 1;
    $url = y_self_ping_url();
    $ok = false;
    $hp = parse_url($url);
    $host = $hp['host'] ?? '127.0.0.1';
    $port = $hp['port'] ?? 80;
    $path = $hp['path'] ?? '/';
    $errno = 0; $errstr = '';
    $fp = @stream_socket_client('tcp://' . $host . ':' . $port, $errno, $errstr, 2);
    if ($fp) {
        @fwrite($fp, 'GET ' . $path . " HTTP/1.0\r\nHost: " . $host . "\r\nConnection: close\r\n\r\n");
        $resp = @fread($fp, 256);
        @fclose($fp);
        $ok = is_string($resp) && strpos($resp, ' 200 ') !== false;
    }
    $ka['last_ok'] = $ok;
    $ka['last_at'] = y_now_iso();
    @file_put_contents($KEEPALIVE_FILE, json_encode($ka), LOCK_EX);
    if (!$ok) y_log('⚠️ keep-alive ping failed (' . $url . ')' . "\n");
}

function y_keepalive_stats() {
    global $KEEPALIVE_FILE, $CONFIG;
    $ka = array('pings' => 0, 'last_ok' => false, 'last_at' => '');
    if (is_file($KEEPALIVE_FILE)) {
        $d = json_decode(@file_get_contents($KEEPALIVE_FILE), true);
        if (is_array($d)) $ka = array_merge($ka, $d);
    }
    return array(
        'enabled' => $CONFIG['keepalive_minutes'] > 0,
        'interval_minutes' => $CONFIG['keepalive_minutes'],
        'pings' => (int)$ka['pings'],
        'last_ok' => (bool)$ka['last_ok'],
        'last_at' => $ka['last_at'],
    );
}

// ============ ON-DEMAND NODE RE-CHECK (PHP has no background threads) ============
function y_auto_check_nodes() {
    global $STATE, $CONFIG;
    if ($CONFIG['node_check_minutes'] <= 0) return;
    $checked = 0;
    foreach ($STATE['nodes'] as $i => $n) {
        if ($checked >= 2) break;
        if (array_key_exists('enabled', $n) && $n['enabled'] === false) continue;
        $lc = strtotime((string)($n['last_check'] ?? '')) ?: 0;
        if ($lc && (time() - $lc) < $CONFIG['node_check_minutes'] * 60) continue;
        y_check_node($STATE['nodes'][$i]);
        $checked++;
    }
}

// ============ ROUTES ============
$ROUTES = array(
    array('GET', '#^$#', 'root', false),
    array('GET', '#^healthz$#', 'healthz', false),
    array('GET', '#^login$#', 'page_login', false),
    array('GET', '#^setup$#', 'page_setup', false),
    array('GET', '#^panel$#', 'page_panel', false),
    array('GET', '#^s$#', 'page_sub', false),
    array('POST', '#^api/setup$#', 'api_setup', false),
    array('POST', '#^api/login$#', 'api_login', false),
    array('POST', '#^api/logout$#', 'api_logout', false),
    array('GET', '#^api/me$#', 'api_me', true),
    array('GET', '#^api/fun/line$#', 'api_fun_line', true),
    array('GET', '#^api/server-info$#', 'api_server_info', true),
    array('POST', '#^api/server-info/refresh$#', 'api_server_info_refresh', true),
    array('GET', '#^api/users$#', 'api_users_list', true),
    array('POST', '#^api/users$#', 'api_users_create', true),
    array('PATCH', '#^api/users/([^/]+)$#', 'api_users_patch', true),
    array('DELETE', '#^api/users/([^/]+)$#', 'api_users_delete', true),
    array('POST', '#^api/users/([^/]+)/reset-traffic$#', 'api_users_reset_traffic', true),
    array('POST', '#^api/users/([^/]+)/rotate-sub$#', 'api_users_rotate_sub', true),
    array('GET', '#^api/users/([^/]+)/subscription$#', 'api_users_subscription', true),
    array('GET', '#^api/users/([^/]+)/qr$#', 'api_qr_stub', true),
    array('GET', '#^api/nodes$#', 'api_nodes_list', true),
    array('POST', '#^api/nodes$#', 'api_nodes_create', true),
    array('PATCH', '#^api/nodes/([^/]+)$#', 'api_nodes_patch', true),
    array('DELETE', '#^api/nodes/([^/]+)$#', 'api_nodes_delete', true),
    array('POST', '#^api/nodes/([^/]+)/check$#', 'api_nodes_check', true),
    array('POST', '#^api/nodes/check-all$#', 'api_nodes_check_all', true),
    array('GET', '#^api/lb$#', 'api_lb_get', true),
    array('POST', '#^api/lb$#', 'api_lb_set', true),
    array('GET', '#^api/lb/pick$#', 'api_lb_pick', true),
    array('POST', '#^api/tools/lookup$#', 'api_tools_lookup', true),
    array('GET', '#^api/settings$#', 'api_settings_get', true),
    array('POST', '#^api/settings$#', 'api_settings_post', true),
    array('GET', '#^api/settings/backup$#', 'api_backup', true),
    array('POST', '#^api/settings/restore$#', 'api_restore', true),
    array('GET', '#^api/sub/([^/]+)$#', 'api_sub', false),
    array('GET', '#^api/lb/sub/([^/]+)$#', 'api_lb_sub', false),
    array('GET', '#^api/sub/([^/]+)/qr$#', 'api_qr_stub', false),
    array('GET', '#^api/public/sub/([^/]+)$#', 'api_public_sub', false),
);

// ============ HANDLERS: pages ============
function y_h_root() {
    if (!y_password_set()) y_redirect('?p=setup');
    y_redirect(y_check_session() ? '?p=panel' : '?p=login');
}

function y_h_healthz() {
    global $Y_VERSION;
    y_json(200, array('ok' => true, 'app' => 'yorozuya-panel', 'version' => $Y_VERSION));
}

function y_h_page_login() {
    global $PAGE_LOGIN;
    if (!y_password_set()) y_redirect('?p=setup');
    y_send(200, $PAGE_LOGIN, 'text/html; charset=utf-8');
}

function y_h_page_setup() {
    global $PAGE_SETUP;
    if (y_password_set()) y_redirect(y_check_session() ? '?p=panel' : '?p=login');
    y_send(200, $PAGE_SETUP, 'text/html; charset=utf-8');
}

function y_h_page_panel() {
    global $PAGE_PANEL;
    if (!y_password_set()) y_redirect('?p=setup');
    if (!y_check_session()) y_redirect('?p=login');
    y_send(200, $PAGE_PANEL, 'text/html; charset=utf-8');
}

function y_h_page_sub() {
    global $PAGE_SUB;
    $key = (string)($_GET['key'] ?? '');
    if (!y_find_user_by_sub($key)) {
        y_send(404, '<h1>404 — not found 🍓</h1>', 'text/html; charset=utf-8');
        return;
    }
    y_send(200, $PAGE_SUB, 'text/html; charset=utf-8');
}

// ============ HANDLERS: auth ============
function y_h_api_setup() {
    global $STATE, $Y_BODY;
    if (y_password_set()) {
        y_json(403, array('detail' => 'password already set'));
        return;
    }
    $pw = (string)($Y_BODY['password'] ?? '');
    if (strlen($pw) < 4) {
        y_json(400, array('detail' => 'password too short (min 4 chars)'));
        return;
    }
    $STATE['settings']['admin_hash'] = y_sha256($pw);
    y_save_state();
    $token = y_make_session();
    y_json(200, array('ok' => true), array('Set-Cookie' => y_session_cookie($token)));
}

function y_h_api_login() {
    global $Y_BODY;
    $h = y_active_hash();
    $pw = (string)($Y_BODY['password'] ?? '');
    if ($h !== '' && hash_equals($h, y_sha256($pw))) {
        $token = y_make_session();
        y_json(200, array('ok' => true), array('Set-Cookie' => y_session_cookie($token)));
        return;
    }
    y_json(401, array('detail' => 'wrong password'));
}

function y_h_api_logout() {
    global $SESSIONS;
    $c = y_get_cookies();
    $t = $c['yorozuya_session'] ?? '';
    if ($t !== '' && isset($SESSIONS[$t])) {
        unset($SESSIONS[$t]);
        y_save_sessions();
    }
    y_json(200, array('ok' => true), array('Set-Cookie' => 'yorozuya_session=; Path=/; HttpOnly; SameSite=Lax; Max-Age=0'));
}

function y_h_api_me() {
    y_json(200, array('ok' => true, 'user' => 'admin'));
}

function y_h_api_fun_line() {
    global $GINTOKI_LINES;
    y_json(200, array('line' => $GINTOKI_LINES[random_int(0, count($GINTOKI_LINES) - 1)]));
}

// ============ HANDLERS: server info ============
function y_h_api_server_info() {
    global $STATE, $Y_VERSION, $CONFIG, $DEFAULT_PASSWORD, $GINTOKI_LINES;
    y_auto_check_nodes();
    $out = y_get_outbound_info(false);
    $active = 0;
    $expired = 0;
    foreach ($STATE['users'] as $u) {
        if (!empty($u['enabled']) && !y_is_expired($u)) $active++;
        if (y_is_expired($u)) $expired++;
    }
    $onlineNodes = 0;
    foreach ($STATE['nodes'] as $n) if (($n['online'] ?? null) === true) $onlineNodes++;
    y_json(200, array(
        'ok' => true, 'version' => $Y_VERSION, 'now' => y_now_iso(), 'hostname' => gethostname(),
        'default_password' => $DEFAULT_PASSWORD, 'outbound' => $out,
        'counts' => array(
            'users' => count($STATE['users']),
            'active_users' => $active,
            'expired_users' => $expired,
            'nodes' => count($STATE['nodes']),
            'online_nodes' => $onlineNodes,
        ),
        'fun_line' => $GINTOKI_LINES[random_int(0, count($GINTOKI_LINES) - 1)],
        'setup_required' => !y_password_set(),
        'public_url' => $CONFIG['public_url'] ?: null,
        'keepalive' => y_keepalive_stats(),
        'lb' => array('strategy' => y_lb_state()['strategy'], 'candidates' => count(y_lb_candidates())),
    ));
}

function y_h_api_server_info_refresh() {
    y_json(200, array('ok' => true, 'outbound' => y_get_outbound_info(true)));
}

// ============ HANDLERS: users ============
function y_h_api_users_list() {
    global $STATE;
    y_json(200, array('users' => array_map('y_public_user', $STATE['users'])));
}

function y_h_api_users_create() {
    global $STATE, $Y_BODY;
    $b = $Y_BODY;
    $username = trim((string)($b['username'] ?? ''));
    if ($username === '') {
        y_json(400, array('detail' => 'username required'));
        return;
    }
    foreach ($STATE['users'] as $u) {
        if (($u['username'] ?? null) === $username) {
            y_json(400, array('detail' => 'username exists'));
            return;
        }
    }
    $user = array(
        'id' => y_new_id(), 'username' => $username,
        'uuid' => (string)($b['uuid'] ?? '') ?: y_new_uuid(),
        'sub_key' => y_new_sub_key(), 'traffic_limit_gb' => y_num($b['traffic_limit_gb'] ?? 0),
        'traffic_used_gb' => 0, 'expires_at' => (string)($b['expires_at'] ?? ''),
        'enabled' => !array_key_exists('enabled', $b) || $b['enabled'] !== false,
        'note' => (string)($b['note'] ?? ''), 'created_at' => y_now_iso(),
    );
    $STATE['users'][] = $user;
    y_save_state();
    y_json(200, array('ok' => true, 'user' => y_public_user($user)));
}

function y_h_api_users_patch($id) {
    global $STATE, $Y_BODY;
    $u = null;
    foreach ($STATE['users'] as $i => $x) {
        if (($x['id'] ?? null) === $id) {
            $u = $i;
            break;
        }
    }
    if ($u === null) {
        y_json(404, array('detail' => 'user not found'));
        return;
    }
    $b = $Y_BODY;
    $user = $STATE['users'][$u];
    if (array_key_exists('username', $b)) {
        $name = trim((string)($b['username'] ?? ''));
        if ($name === '') {
            y_json(400, array('detail' => 'username required'));
            return;
        }
        foreach ($STATE['users'] as $x) {
            if (($x['username'] ?? null) === $name && ($x['id'] ?? null) !== $user['id']) {
                y_json(400, array('detail' => 'username exists'));
                return;
            }
        }
        $user['username'] = $name;
    }
    if (array_key_exists('expires_at', $b)) $user['expires_at'] = (string)($b['expires_at'] ?? '');
    if (array_key_exists('note', $b)) $user['note'] = (string)($b['note'] ?? '');
    if (array_key_exists('traffic_limit_gb', $b) && is_numeric($b['traffic_limit_gb'])) {
        $v = max(0.0, (float)$b['traffic_limit_gb']);
        $user['traffic_limit_gb'] = fmod($v, 1.0) == 0.0 ? (int)$v : $v;
    }
    if (array_key_exists('traffic_used_gb', $b) && is_numeric($b['traffic_used_gb'])) {
        $v = max(0.0, (float)$b['traffic_used_gb']);
        $user['traffic_used_gb'] = fmod($v, 1.0) == 0.0 ? (int)$v : $v;
    }
    if (array_key_exists('enabled', $b)) $user['enabled'] = (bool)$b['enabled'];
    if (!empty($b['uuid'])) $user['uuid'] = (string)$b['uuid'];
    $STATE['users'][$u] = $user;
    y_save_state();
    y_json(200, array('ok' => true, 'user' => y_public_user($user)));
}

function y_h_api_users_delete($id) {
    global $STATE;
    foreach ($STATE['users'] as $i => $u) {
        if (($u['id'] ?? null) === $id) {
            array_splice($STATE['users'], $i, 1);
            y_save_state();
            y_json(200, array('ok' => true));
            return;
        }
    }
    y_json(404, array('detail' => 'user not found'));
}

function y_h_api_users_reset_traffic($id) {
    global $STATE;
    foreach ($STATE['users'] as $i => $u) {
        if (($u['id'] ?? null) === $id) {
            $STATE['users'][$i]['traffic_used_gb'] = 0;
            y_save_state();
            y_json(200, array('ok' => true, 'user' => y_public_user($STATE['users'][$i])));
            return;
        }
    }
    y_json(404, array('detail' => 'user not found'));
}

function y_h_api_users_rotate_sub($id) {
    global $STATE;
    foreach ($STATE['users'] as $i => $u) {
        if (($u['id'] ?? null) === $id) {
            $STATE['users'][$i]['sub_key'] = y_new_sub_key();
            y_save_state();
            y_json(200, array('ok' => true, 'user' => y_public_user($STATE['users'][$i])));
            return;
        }
    }
    y_json(404, array('detail' => 'user not found'));
}

function y_h_api_users_subscription($id) {
    global $STATE;
    foreach ($STATE['users'] as $u) {
        if (($u['id'] ?? null) === $id) {
            $mode = ($_GET['node'] ?? '') === 'lb' ? 'lb' : null;
            y_json(200, array(
                'ok' => true, 'user' => y_public_user($u),
                'sub_url' => y_url('api/sub/' . $u['sub_key']),
                'page_url' => y_url('s') . '&key=' . $u['sub_key'],
                'qr_url' => y_url('api/sub/' . $u['sub_key'] . '/qr'),
                'lb_url' => y_url('api/lb/sub/' . $u['sub_key']),
                'links' => y_user_links($u, $mode),
            ));
            return;
        }
    }
    y_json(404, array('detail' => 'user not found'));
}

function y_h_api_qr_stub() {
    // QR is rendered client-side in this edition (zero server dependencies)
    y_json(501, array('detail' => 'QR is rendered client-side in this edition'));
}

// ============ HANDLERS: nodes ============
$NODE_FIELDS = array('name', 'address', 'port', 'protocol', 'network', 'security', 'sni', 'path', 'host', 'fp', 'alpn',
    'reality_pbk', 'reality_sid', 'reality_spx', 'flow', 'ss_method', 'ss_password', 'enabled');

function y_h_api_nodes_list() {
    global $STATE;
    y_auto_check_nodes();
    y_json(200, array('nodes' => array_map('y_public_node', $STATE['nodes'])));
}

function y_h_api_nodes_create() {
    global $STATE, $Y_BODY, $NODE_FIELDS;
    $b = $Y_BODY;
    $name = trim((string)($b['name'] ?? ''));
    $address = trim((string)($b['address'] ?? ''));
    if ($name === '' || $address === '') {
        y_json(400, array('detail' => 'name and address required'));
        return;
    }
    $node = array(
        'id' => y_new_id(), 'name' => $name, 'address' => $address,
        'port' => y_num($b['port'] ?? 443, 443) ?: 443,
        'protocol' => strtolower((string)($b['protocol'] ?? 'vless')),
        'network' => strtolower((string)($b['network'] ?? 'tcp')),
        'security' => strtolower((string)($b['security'] ?? 'none')),
        'sni' => (string)($b['sni'] ?? ''), 'path' => (string)($b['path'] ?? ''), 'host' => (string)($b['host'] ?? ''),
        'fp' => (string)($b['fp'] ?? ''), 'alpn' => (string)($b['alpn'] ?? ''),
        'reality_pbk' => (string)($b['reality_pbk'] ?? ''), 'reality_sid' => (string)($b['reality_sid'] ?? ''), 'reality_spx' => (string)($b['reality_spx'] ?? ''),
        'flow' => (string)($b['flow'] ?? ''),
        'ss_method' => (string)($b['ss_method'] ?? 'chacha20-ietf-poly1305'), 'ss_password' => (string)($b['ss_password'] ?? ''),
        'enabled' => !array_key_exists('enabled', $b) || $b['enabled'] !== false,
        'created_at' => y_now_iso(),
        'last_check' => '', 'online' => null, 'latency_ms' => null, 'resolved_ip' => '', 'country' => '', 'country_code' => '',
    );
    $STATE['nodes'][] = $node;
    y_save_state();
    y_json(200, array('ok' => true, 'node' => y_public_node($node)));
}

function y_h_api_nodes_patch($id) {
    global $STATE, $Y_BODY, $NODE_FIELDS;
    foreach ($STATE['nodes'] as $i => $n) {
        if (($n['id'] ?? null) === $id) {
            $b = $Y_BODY;
            foreach ($NODE_FIELDS as $f) {
                if (array_key_exists($f, $b) && $b[$f] !== null) $STATE['nodes'][$i][$f] = $b[$f];
            }
            $STATE['nodes'][$i]['port'] = y_num($STATE['nodes'][$i]['port'] ?? 443, 443) ?: 443;
            y_save_state();
            y_json(200, array('ok' => true, 'node' => y_public_node($STATE['nodes'][$i])));
            return;
        }
    }
    y_json(404, array('detail' => 'node not found'));
}

function y_h_api_nodes_delete($id) {
    global $STATE;
    foreach ($STATE['nodes'] as $i => $n) {
        if (($n['id'] ?? null) === $id) {
            array_splice($STATE['nodes'], $i, 1);
            y_save_state();
            y_json(200, array('ok' => true));
            return;
        }
    }
    y_json(404, array('detail' => 'node not found'));
}

function y_h_api_nodes_check($id) {
    global $STATE;
    foreach ($STATE['nodes'] as $i => $n) {
        if (($n['id'] ?? null) === $id) {
            y_check_node($STATE['nodes'][$i]);
            y_json(200, array('ok' => true, 'node' => y_public_node($STATE['nodes'][$i])));
            return;
        }
    }
    y_json(404, array('detail' => 'node not found'));
}

function y_h_api_nodes_check_all() {
    global $STATE;
    if (!$STATE['nodes']) {
        y_json(200, array('ok' => true, 'nodes' => array()));
        return;
    }
    $resolved = array();
    foreach ($STATE['nodes'] as $n) $resolved[] = array('n' => $n, 'ip' => y_resolve_host($n['address'] ?? ''));
    $geo = y_ip_lookup_batch(array_map(function ($r) { return $r['ip']; }, $resolved));
    foreach ($resolved as $r) {
        $n = $r['n'];
        $ip = $r['ip'];
        $ok = false;
        $ms = null;
        if ($ip !== '') {
            $t = y_tcp_ping($ip, y_num($n['port'] ?? 443, 443) ?: 443, 5000);
            $ok = $t['ok'];
            $ms = $t['ms'];
        }
        $info = $geo[$ip] ?? array('country' => '', 'country_code' => '', 'city' => '', 'isp' => '');
        $n['last_check'] = y_now_iso();
        $n['online'] = $ok;
        $n['latency_ms'] = $ms;
        $n['resolved_ip'] = $ip;
        $n['country'] = $info['country'] ?? '';
        $n['country_code'] = $info['country_code'] ?? '';
        foreach ($STATE['nodes'] as $i => $x) {
            if (($x['id'] ?? null) === ($n['id'] ?? null)) {
                $STATE['nodes'][$i] = $n;
                break;
            }
        }
    }
    y_save_state();
    y_json(200, array('ok' => true, 'nodes' => array_map('y_public_node', $STATE['nodes'])));
}

// ============ HANDLERS: load balancer ============
function y_h_api_lb_get() {
    $lb = y_lb_state();
    y_json(200, array(
        'ok' => true, 'strategy' => $lb['strategy'], 'rr' => (int)($lb['rr'] ?? 0), 'picks' => $lb['picks'],
        'candidates' => array_map('y_public_node', y_lb_candidates()),
    ));
}

function y_h_api_lb_set() {
    global $STATE, $Y_BODY, $LB_STRATEGIES;
    $s = (string)($Y_BODY['strategy'] ?? '');
    if (!in_array($s, $LB_STRATEGIES, true)) {
        y_json(400, array('detail' => 'strategy must be one of: ' . implode(', ', $LB_STRATEGIES)));
        return;
    }
    $STATE['lb']['strategy'] = $s;
    y_save_state();
    $lb = y_lb_state();
    y_json(200, array(
        'ok' => true, 'strategy' => $lb['strategy'], 'rr' => (int)($lb['rr'] ?? 0), 'picks' => $lb['picks'],
        'candidates' => array_map('y_public_node', y_lb_candidates()),
    ));
}

function y_h_api_lb_pick() {
    global $STATE;
    $n = y_pick_node();
    if (!$n) {
        y_json(404, array('detail' => 'no enabled nodes'));
        return;
    }
    $out = array('ok' => true, 'strategy' => y_lb_state()['strategy'], 'node' => y_public_node($n));
    $uname = trim((string)($_GET['username'] ?? ''));
    $u = null;
    if ($uname !== '') {
        foreach ($STATE['users'] as $x) {
            if (($x['username'] ?? null) === $uname) {
                $u = $x;
                break;
            }
        }
    }
    if ($u) {
        $e = y_link_entry($u, $n);
        if ($e) $out['link'] = $e['link'];
    }
    y_json(200, $out);
}

// ============ HANDLERS: tools ============
function y_h_api_tools_lookup() {
    global $Y_BODY;
    $query = trim((string)($Y_BODY['query'] ?? ''));
    if ($query === '') {
        y_json(400, array('detail' => 'query required'));
        return;
    }
    $ip = y_resolve_host($query);
    $info = $ip !== '' ? y_ip_lookup($ip) : array('country' => '', 'country_code' => '', 'city' => '', 'isp' => '');
    y_json(200, array(
        'ok' => true,
        'result' => array(
            'query' => $query, 'resolved_ip' => $ip, 'country' => $info['country'] ?? '', 'country_code' => $info['country_code'] ?? '',
            'city' => $info['city'] ?? '', 'isp' => $info['isp'] ?? '',
            'flag' => ($info['country_code'] ?? '') !== '' ? y_flag($info['country_code']) : '🏳️',
        ),
    ));
}

// ============ HANDLERS: settings + backup ============
function y_h_api_settings_get() {
    global $STATE;
    y_json(200, array('settings' => $STATE['settings']));
}

function y_h_api_settings_post() {
    global $STATE, $Y_BODY;
    if (array_key_exists('sub_base_url', $Y_BODY)) {
        $STATE['settings']['sub_base_url'] = rtrim(trim((string)($Y_BODY['sub_base_url'] ?? '')), '/');
    }
    y_save_state();
    y_json(200, array('ok' => true, 'settings' => $STATE['settings']));
}

function y_h_api_backup() {
    global $STATE;
    y_send(200, json_encode($STATE, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES), 'application/json; charset=utf-8',
        array('Content-Disposition' => 'attachment; filename=yorozuya-backup.json'));
}

function y_h_api_restore() {
    global $STATE, $Y_BODY;
    $d = (isset($Y_BODY['data']) && is_array($Y_BODY['data'])) ? $Y_BODY['data'] : $Y_BODY;
    if (!is_array($d) || !isset($d['users']) || !is_array($d['users']) || !isset($d['nodes']) || !is_array($d['nodes'])) {
        y_json(400, array('detail' => 'invalid backup file'));
        return;
    }
    $STATE['users'] = $d['users'];
    $STATE['nodes'] = $d['nodes'];
    if (isset($d['settings']) && is_array($d['settings'])) $STATE['settings'] = array_merge(array('sub_base_url' => '', 'admin_hash' => ''), $d['settings']);
    if (isset($d['ip_cache']) && is_array($d['ip_cache'])) $STATE['ip_cache'] = $d['ip_cache'];
    if (isset($d['lb']) && is_array($d['lb'])) $STATE['lb'] = array_merge(array('strategy' => 'round-robin', 'rr' => 0, 'picks' => array()), $d['lb']);
    y_save_state();
    y_json(200, array('ok' => true, 'imported' => array('users' => count($STATE['users']), 'nodes' => count($STATE['nodes']))));
}

// ============ HANDLERS: public subscription (no auth) ============
function y_send_sub_body($u, $links, $lbNodeName) {
    $body = implode("\n", array_map(function ($x) { return $x['link']; }, $links));
    $b64 = base64_encode($body);
    $headers = array(
        'Content-Disposition' => 'inline; filename="' . $u['username'] . '.txt"',
        'Subscription-Userinfo' => 'upload=0; download=' . (int)((float)($u['traffic_used_gb'] ?? 0) * 1073741824)
            . '; total=' . (int)((float)($u['traffic_limit_gb'] ?? 0) * 1073741824) . '; expire=0',
    );
    if ($lbNodeName) $headers['X-Yorozuya-LB-Node'] = $lbNodeName;
    y_send(200, $b64, 'text/plain; charset=utf-8', $headers);
}

function y_sub_links_for($u) {
    $node = (string)($_GET['node'] ?? '');
    $mode = $node === 'lb' ? 'lb' : ($node !== '' ? $node : null);
    return y_user_links($u, $mode);
}

function y_h_api_sub($sub_key) {
    $u = y_find_user_by_sub($sub_key);
    if (!$u || empty($u['enabled']) || y_is_expired($u)) {
        y_json(404, array('detail' => 'not found'));
        return;
    }
    $links = y_sub_links_for($u);
    if (($_GET['format'] ?? '') === 'text') {
        $t = implode("\n", array_map(function ($x) { return $x['link']; }, $links));
        y_send(200, $t !== '' ? $t . "\n" : '', 'text/plain; charset=utf-8');
        return;
    }
    y_send_sub_body($u, $links, null);
}

function y_h_api_lb_sub($sub_key) {
    // load-balanced subscription: every fetch picks a healthy node
    $u = y_find_user_by_sub($sub_key);
    if (!$u || empty($u['enabled']) || y_is_expired($u)) {
        y_json(404, array('detail' => 'not found'));
        return;
    }
    $links = y_user_links($u, 'lb');
    if (($_GET['format'] ?? '') === 'text') {
        $t = implode("\n", array_map(function ($x) { return $x['link']; }, $links));
        y_send(200, $t !== '' ? $t . "\n" : '', 'text/plain; charset=utf-8');
        return;
    }
    y_send_sub_body($u, $links, $links ? $links[0]['node'] : null);
}

function y_h_api_public_sub($sub_key) {
    $u = y_find_user_by_sub($sub_key);
    if (!$u || empty($u['enabled']) || y_is_expired($u)) {
        y_json(404, array('detail' => 'not found'));
        return;
    }
    y_json(200, array(
        'ok' => true, 'username' => $u['username'], 'expires_at' => $u['expires_at'] ?? '', 'expired' => y_is_expired($u),
        'traffic_limit_gb' => y_num($u['traffic_limit_gb'] ?? 0), 'traffic_used_gb' => y_num($u['traffic_used_gb'] ?? 0),
        'sub_url' => y_url('api/sub/' . $u['sub_key']), 'qr_url' => y_url('api/sub/' . $u['sub_key'] . '/qr'),
        'links' => y_sub_links_for($u),
    ));
}

// ============ DISPATCH ============
y_load_state();
y_load_sessions();
y_sweep();
y_keepalive_tick();

if (PHP_SAPI === 'cli-server' && !is_file($BANNER_FLAG)) {
    global $Y_VERSION, $CONFIG, $DATA_DIR;
    @touch($BANNER_FLAG);
    y_log('🍓 YorozuyaPanel PHP v' . $Y_VERSION . ' · 万事屋' . "\n");
    y_log('⚙️  protocols ready: vless · vmess · trojan · ss' . "\n");
    y_log('🌐 php -S (cli-server) · data: ' . $DATA_DIR . "\n");
    if (!y_password_set()) y_log('🔐 first run — set admin password at ?p=setup' . "\n");
    if ($CONFIG['keepalive_minutes'] > 0) y_log('⏰ keep-alive cron: self-ping every ' . $CONFIG['keepalive_minutes'] . ' min' . "\n");
    y_log('📍 Web Panel path: ?p=panel' . "\n");
}

$method = $_SERVER['REQUEST_METHOD'] ?? 'GET';
if ($method === 'OPTIONS') {
    y_send(204, '', 'text/plain; charset=utf-8');
    exit;
}

$Y_BODY = array();
if (in_array($method, array('POST', 'PATCH', 'PUT', 'DELETE'), true)) {
    $cl = (int)($_SERVER['CONTENT_LENGTH'] ?? 0);
    if ($cl > 2 * 1024 * 1024) {
        y_json(413, array('detail' => 'request body too large (2mb limit)'));
        exit;
    }
    $raw = file_get_contents('php://input');
    if (is_string($raw) && $raw !== '') {
        $d = json_decode($raw, true);
        if (!is_array($d)) {
            y_json(400, array('detail' => 'invalid JSON body'));
            exit;
        }
        $Y_BODY = $d;
    }
}

$route = isset($_GET['p']) ? trim((string)$_GET['p'], '/') : '';
foreach ($ROUTES as $r) {
    if ($r[0] !== $method) continue;
    if (!preg_match($r[1], $route, $m)) continue;
    if ($r[3]) {
        if (!y_password_set()) {
            y_json(503, array('detail' => 'setup required — open /setup'));
            exit;
        }
        if (!y_check_session()) {
            y_json(401, array('detail' => 'login required'));
            exit;
        }
    }
    array_shift($m);
    call_user_func_array('y_h_' . $r[2], $m);
    exit;
}
y_json(404, array('detail' => 'not found'));
