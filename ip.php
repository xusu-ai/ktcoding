<?php
$scheme = (!empty($_SERVER['HTTPS']) && $_SERVER['HTTPS'] !== 'off') || ($_SERVER['SERVER_PORT'] == 443) ? 'https' : 'http';
$host = $_SERVER['HTTP_HOST'] ?: 'ktcoding.cn';
header('Location: ' . $scheme . '://' . $host . '/ipwhitelist/ip_whitelist.html');
exit;
