
<?php
// === HTTP Basic Auth ===
$user = 'opencode';
$pass = 'ksqxllup';
if (!isset($_SERVER['PHP_AUTH_PW']) || $_SERVER['PHP_AUTH_USER'] !== $user || $_SERVER['PHP_AUTH_PW'] !== $pass) {
    // 认证失败一律返回 JSON（前端 fetch 拿到 JSON 错误，不会当 HTML 解析）
    header('WWW-Authenticate: Basic realm="IP Whitelist"');
    http_response_code(401);
    header('Content-Type: application/json; charset=utf-8');
    echo json_encode(['message' => '401 Unauthorized — 需要认证 (opencode)', 'type' => 'error'], JSON_UNESCAPED_UNICODE);
    exit;
}

/**
 * IP 白名单管理页面
 * 认证: HTTP Basic Auth (opencode/ksqxllup)
 * 功能: 显示 IP 列表, 添加 IP/网段, 删除 IP, 查看最后更新时间
 * API:
 *   GET  ?action=json            → 列表 JSON
 *   POST ?action=add  ip=...     → 添加（JSON 响应）
 *   GET  ?action=remove&ip=...   → 删除（JSON 响应，自动传播三站）
 *   GET  其他/无 action          → 独立 HTML 页面（浏览器直接打开用）
 */

// === 统一 JSON 响应助手 ===
function jsonRespond($payload, $statusCode = 200) {
    if (!headers_sent()) {
        http_response_code($statusCode);
        header('Content-Type: application/json; charset=utf-8');
    }
    echo json_encode($payload, JSON_UNESCAPED_UNICODE);
    exit;
}

// === 数据 / 锁文件 ===
$dataFile = __DIR__ . '/ip_whitelist.txt';
$lockFile = __DIR__ . '/ip_whitelist.lock';

// 确保数据文件存在
if (!file_exists($dataFile)) {
    file_put_contents($dataFile, "# IP whitelist\n# last_update: " . date('Y-m-d H:i:s') . "\n");
}

// 文件锁
function acquireLock($lockFile) {
    $fp = fopen($lockFile, 'c');
    if ($fp && flock($fp, LOCK_EX | LOCK_NB)) {
        return $fp;
    }
    return null;
}

function releaseLock($fp) {
    if ($fp) {
        flock($fp, LOCK_UN);
        fclose($fp);
    }
}

// 读取 IP 列表
function readWhitelist($dataFile) {
    $lines = file($dataFile, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
    $ips = [];
    $lastUpdate = '';
    foreach ($lines as $line) {
        $line = trim($line);
        if ($line === '' || strpos($line, '#') === 0) {
            if (strpos($line, 'last_update:') !== false) {
                $lastUpdate = $line;
            }
            continue;
        }
        // 验证 IP 或 CIDR 格式
        if (filter_var($line, FILTER_VALIDATE_IP) || preg_match('/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\/\d{1,2}$/', $line)) {
            $ips[] = $line;
        }
    }
    return ['ips' => $ips, 'last_update' => $lastUpdate];
}

// 写入 IP 列表
function writeWhitelist($dataFile, $ips) {
    $content = "# IP whitelist — auto-managed\n";
    $content .= "# Format: one IP or CIDR per line, # for comments\n\n";
    foreach ($ips as $ip) {
        $content .= $ip . "\n";
    }
    $content .= "\n# last_update: " . date('Y-m-d H:i:s T') . "\n";
    file_put_contents($dataFile, $content, LOCK_EX);
}

$action = isset($_GET['action']) ? $_GET['action'] : 'view';

// === JSON API: 列表 ===
if ($action === 'json') {
    $data = readWhitelist($dataFile);
    jsonRespond(['ips' => $data['ips'], 'last_update' => $data['last_update'] ?: '', 'count' => count($data['ips'])]);
}

// === 写操作：add / remove 一律返回 JSON（前端 fetch 依赖，浏览器直开时用户也能看懂 JSON）===
if ($action === 'add' && isset($_POST['ip'])) {
    $ip = trim($_POST['ip']);
    // 验证
    $valid = filter_var($ip, FILTER_VALIDATE_IP) || preg_match('/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\/\d{1,2}$/', $ip);
    if (!$valid) {
        jsonRespond(['message' => "无效 IP 或网段: $ip", 'type' => 'error'], 400);
    }
    $lock = acquireLock($lockFile);
    if (!$lock) {
        jsonRespond(['message' => '文件被锁定，请稍后重试', 'type' => 'error'], 503);
    }
    $data = readWhitelist($dataFile);
    if (in_array($ip, $data['ips'])) {
        releaseLock($lock);
        jsonRespond(['message' => "$ip 已在白名单中", 'type' => 'info']);
    }
    $data['ips'][] = $ip;
    writeWhitelist($dataFile, $data['ips']);
    releaseLock($lock);
    jsonRespond(['message' => "已添加: $ip (当前 " . count($data['ips']) . " 个条目)", 'type' => 'success']);
}

if ($action === 'remove' && isset($_GET['ip'])) {
    $ip = trim($_GET['ip']);
    $lock = acquireLock($lockFile);
    if (!$lock) {
        jsonRespond(['message' => '文件被锁定，请稍后重试', 'type' => 'error'], 503);
    }
    $data = readWhitelist($dataFile);
    if (!in_array($ip, $data['ips'])) {
        releaseLock($lock);
        jsonRespond(['message' => "$ip 不在白名单中", 'type' => 'info'], 404);
    }
    $data['ips'] = array_values(array_diff($data['ips'], [$ip]));
    writeWhitelist($dataFile, $data['ips']);
    releaseLock($lock);

    // 传播删除到对端（防止 union 同步把已删 IP 拉回来）
    // 优先用本机 shell 脚本（root 权限、带 XHR 头），失败则回退 PHP 直连
    $isPropagated = isset($_SERVER['HTTP_X_PROPAGATED_DELETE']);
    if (!$isPropagated) {
        $script = '/root/scripts/propagate-delete.sh';
        $ran = false;
        if (file_exists($script) && function_exists('shell_exec')) {
            $cmd = 'timeout 30 ' . escapeshellarg($script) . ' ' . escapeshellarg($ip) . ' 2>&1';
            $output = @shell_exec($cmd);
            if ($output !== null) {
                $ran = true;
                error_log("[ip-whitelist] propagate via shell: $output");
            }
        }
        // 回退：PHP 直连（shell 不可用时）
        if (!$ran) {
            $peerHosts = ['qqcmd.cn', 'nowcoding.cn', 'ktcoding.cn'];
            $selfHost = $_SERVER['HTTP_HOST'] ?? 'ktcoding.cn';
            foreach ($peerHosts as $ph) {
                if ($ph === $selfHost) continue;
                $scheme = ($ph === 'qqcmd.cn') ? 'http' : 'https';
                $peerUrl = $scheme . '://' . $ph . '/ipwhitelist/ip_whitelist.php?action=remove&ip=' . urlencode($ip);
                $ctx = stream_context_create(['http' => ['timeout' => 8, 'ignore_errors' => true,
                    'header' => "Authorization: Basic " . base64_encode($user . ':' . $pass) .
                                "\r\nX-Propagated-Delete: 1\r\nX-Requested-With: XMLHttpRequest\r\n"],
                    'ssl' => ['verify_peer' => false, 'verify_peer_name' => false]]);
                @file_get_contents($peerUrl, false, $ctx);
            }
        }
    }
    jsonRespond(['message' => "已删除: $ip (当前 " . count($data['ips']) . " 个条目)", 'type' => 'success']);
}

// === 默认：独立 HTML 页面（浏览器直开 ?action= 无/或 view）===
header('Content-Type: text/html; charset=utf-8');
$data = readWhitelist($dataFile);
$ips = $data['ips'];
$lastUpdate = $data['last_update'];
?>
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>IP 白名单管理</title>
<style>
* { margin: 0; padding: 0; box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #0d1117; color: #c9d1d9; min-height: 100vh; padding: 20px; }
.container { max-width: 800px; margin: 0 auto; }
h1 { font-size: 22px; margin-bottom: 6px; color: #58a6ff; }
.subtitle { font-size: 13px; color: #8b949e; margin-bottom: 24px; }
.card { background: #161b22; border: 1px solid #30363d; border-radius: 8px; padding: 20px; margin-bottom: 16px; }
.card h2 { font-size: 16px; margin-bottom: 12px; color: #c9d1d9; }
.message { padding: 10px 14px; margin-bottom: 16px; font-size: 14px; border-radius: 6px; }
.message.success { background: #0d4429; border: 1px solid #238636; color: #3fb950; }
.message.error { background: #4d1117; border: 1px solid #f85149; color: #f85149; }
.message.info { background: #0c2d6b; border: 1px solid #58a6ff; color: #58a6ff; }
form.add-form { display: flex; gap: 8px; }
form.add-form input[type="text"] { flex: 1; padding: 10px 14px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #c9d1d9; font-size: 14px; font-family: monospace; }
form.add-form input[type="text"]:focus { outline: none; border-color: #58a6ff; }
form.add-form button { padding: 10px 20px; background: #238636; border: none; border-radius: 6px; color: #fff; font-size: 14px; cursor: pointer; }
form.add-form button:hover { background: #2ea043; }
table { width: 100%; border-collapse: collapse; }
th { text-align: left; font-size: 12px; color: #8b949e; padding: 8px 10px; border-bottom: 1px solid #30363d; text-transform: uppercase; }
td { padding: 8px 10px; border-bottom: 1px solid #21262d; font-size: 14px; }
tr:hover td { background: #1c2128; }
.ip-text { font-family: 'SF Mono', Monaco, Consolas, monospace; color: #58a6ff; }
.del-btn { padding: 3px 10px; background: transparent; border: 1px solid #f85149; border-radius: 4px; color: #f85149; font-size: 12px; cursor: pointer; text-decoration: none; display: inline-block; }
.del-btn:hover { background: #f85149; color: #fff; }
.stats { display: flex; gap: 24px; margin-bottom: 16px; }
.stat-item { text-align: center; }
.stat-num { font-size: 28px; font-weight: 700; color: #58a6ff; }
.stat-label { font-size: 12px; color: #8b949e; margin-top: 2px; }
.count-badge { display: inline-block; background: #1f6feb; color: #fff; font-size: 11px; padding: 1px 8px; border-radius: 10px; margin-left: 8px; }
</style>
</head>
<body>
<div class="container">
<h1>🔒 IP 白名单管理（服务端渲染版）</h1>
<p class="subtitle">阿贝云 (ktcoding.cn:156.227.27.58) 防火墙白名单同步源 — 最后更新: <?php echo htmlspecialchars($lastUpdate ?: '未知'); ?></p>

<div class="stats">
<div class="stat-item"><div class="stat-num"><?php echo count($ips); ?></div><div class="stat-label">总条目</div></div>
<div class="stat-item"><div class="stat-num"><?php echo count(array_filter($ips, fn($i) => strpos($i, '/') === false)); ?></div><div class="stat-label">单 IP</div></div>
<div class="stat-item"><div class="stat-num"><?php echo count(array_filter($ips, fn($i) => strpos($i, '/') !== false)); ?></div><div class="stat-label">网段</div></div>
</div>

<div class="card">
<h2>➕ 添加 IP / 网段</h2>
<form class="add-form" method="POST" action="?action=add">
<input type="text" name="ip" placeholder="192.168.1.1 或 192.168.1.0/24" required>
<button type="submit">添加</button>
</form>
<p style="font-size:12px;color:#8b949e;margin-top:8px">⚠️ 提交后跳转到 JSON 响应（写操作 API 统一返回 JSON）。日常操作请用前端页面 <a href="ip_whitelist.html" style="color:#58a6ff">ip_whitelist.html</a></p>
</div>

<div class="card">
<h2>📋 当前白名单 <span class="count-badge"><?php echo count($ips); ?> 条</span></h2>
<table>
<thead><tr><th>#</th><th>IP / 网段</th><th>类型</th><th>操作</th></tr></thead>
<tbody>
<?php foreach ($ips as $i => $ip): $isCidr = strpos($ip, '/') !== false; ?>
<tr>
<td><?php echo $i + 1; ?></td>
<td class="ip-text"><?php echo htmlspecialchars($ip); ?></td>
<td><?php echo $isCidr ? '网段' : 'IP'; ?></td>
<td><a class="del-btn" href="?action=remove&ip=<?php echo urlencode($ip); ?>" onclick="return confirm('确认删除 <?php echo htmlspecialchars($ip); ?> ?（将返回 JSON 并同步到三站）')">删除</a></td>
</tr>
<?php endforeach; ?>
</tbody>
</table>
</div>

<div class="card" style="font-size:12px;color:#8b949e;">
<p>⚙️ 阿贝云 + 新网107 每 5 分钟自动同步此页面。添加的 IP/网段会立即加入防火墙白名单。</p>
<p style="margin-top:6px;">📌 删除 IP 后，该 IP 将在下次同步时从防火墙移除（约 5 分钟延迟），并自动传播到 qqcmd.cn / nowcoding.cn。</p>
</div>
</div>
</body>
</html>
