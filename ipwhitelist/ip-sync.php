<?php
// 三站互相同步脚本 - 由 cron 每 5 分钟调用
// 合并逻辑: 取三站数据的并集(去重)，写回本站
$dataFile = $argv[1] ?? '/var/www/ip_whitelist.txt';
$selfHost = $argv[2] ?? '';

$peers = [
    "http://qqcmd.cn/ipwhitelist/ip_whitelist.txt",
    "https://nowcoding.cn/ipwhitelist/ip_whitelist.txt",
    "http://ktcoding.cn/ipwhitelist/ip_whitelist.txt",
];
// Basic Auth（与 ip_whitelist.php 保持一致）
$authHeader = "Authorization: Basic " . base64_encode("opencode:ksqxllup") . "\r\n";
// 排除自己
$peers = array_filter($peers, function($u) use ($selfHost) {
    return parse_url($u, PHP_URL_HOST) !== $selfHost;
});

// 读取本站数据
$local_lines = [];
if (file_exists($dataFile)) {
    $local_lines = file($dataFile, FILE_IGNORE_NEW_LINES | FILE_SKIP_EMPTY_LINES);
}
$local_entries = array_filter($local_lines, function($l) {
    $l = trim($l);
    return $l && $l[0] !== '#' && preg_match('/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(\/\d{1,2})?$/', $l);
});
$local_set = array_unique($local_entries);

// 拉取对端数据，合并
$all_entries = $local_set;
$synced_from = [];
foreach ($peers as $peer) {
    $ctx = stream_context_create([
        'http' => ['timeout' => 10, 'ignore_errors' => true, 'header' => $authHeader],
        'ssl'  => ['verify_peer' => false, 'verify_peer_name' => false]
    ]);
    $data = @file_get_contents($peer, false, $ctx);
    if ($data && strlen($data) > 10) {
        $lines = array_filter(explode("\n", $data), function($l) {
            $l = trim($l);
            return $l && $l[0] !== '#' && preg_match('/^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(\/\d{1,2})?$/', $l);
        });
        $new = array_diff($lines, $all_entries);
        if ($new) {
            $all_entries = array_merge($all_entries, $new);
            $synced_from[] = parse_url($peer, PHP_URL_HOST) . '(' . count($new) . ')';
        }
    }
}

// 有变化则写回
$all_entries = array_unique($all_entries);
$sorted = array_values($all_entries);
usort($sorted, function($a, $b) {
    $pa = array_map('intval', explode('.', preg_replace('/\/.*/', '', $a)));
    $pb = array_map('intval', explode('.', preg_replace('/\/.*/', '', $b)));
    return $pa <=> $pb ?: strcmp($a, $b);
});

$header = "# IP whitelist - auto-managed\n# Format: one IP or CIDR per line\n\n";
$footer = "\n# last_update: " . date('Y-m-d H:i:s T') . "\n";
$content = $header . implode("\n", $sorted) . $footer;

if ($content !== (file_exists($dataFile) ? file_get_contents($dataFile) : '')) {
    file_put_contents($dataFile, $content, LOCK_EX);
    file_put_contents($dataFile . '.synced_at', date('c'));
    echo "merged: " . count($sorted) . " entries" . ($synced_from ? " from " . implode(', ', $synced_from) : '') . "\n";
} else {
    echo "no changes\n";
}
