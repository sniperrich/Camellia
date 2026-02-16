<?php
declare(strict_types=1);

// Simple CRC salt fetcher (reads token from env).
// Usage: CRC_AUTH_TOKEN=... php -S localhost:8000 backend/crc_salt.php

require_once __DIR__ . '/lib/common.php';

init_common_headers();

$config = load_config();
$token = $config['crc_auth_token'] ?? '';
if (!$token) {
    respond(500, [
        'success' => false,
        'error' => 'missing crc_auth_token config',
    ]);
}

$url = 'http://crcsalt.taylorswift.fit/';
$headers = [
    'User-Agent: Mozilla/5.0 (PHP)',
    'Accept: application/json',
];
if ($token) {
    $headers[] = 'Authorization: Bearer ' . $token;
}

$cacheFile = __DIR__ . DIRECTORY_SEPARATOR . 'data' . DIRECTORY_SEPARATOR . 'crc_cache.json';
$ttl = 3 * 60 * 60; // 3 hours
$now = time();
$cached = null;

if (is_file($cacheFile)) {
    $raw = @file_get_contents($cacheFile);
    if ($raw !== false) {
        $cached = json_decode($raw, true);
    }
}

if (is_array($cached) && isset($cached['fetched_at'], $cached['response'])) {
    if ($now - intval($cached['fetched_at']) < $ttl) {
        respond(200, $cached['response']);
    }
}

$ch = curl_init();
curl_setopt_array($ch, [
    CURLOPT_URL => $url,
    CURLOPT_HTTPHEADER => $headers,
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_TIMEOUT => 10,
]);

$body = curl_exec($ch);
if ($body === false) {
    $err = curl_error($ch);
    curl_close($ch);
    if (is_array($cached) && isset($cached['response'])) {
        respond(200, $cached['response']);
    }
    respond(502, [
        'success' => false,
        'error' => 'curl error: ' . $err,
    ]);
}

$status = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

if ($status < 200 || $status >= 300) {
    if (is_array($cached) && isset($cached['response'])) {
        respond(200, $cached['response']);
    }
    respond(502, [
        'success' => false,
        'error' => 'upstream status ' . $status,
        'raw' => $body,
    ]);
}

$json = json_decode($body, true);
if (!is_array($json)) {
    if (is_array($cached) && isset($cached['response'])) {
        respond(200, $cached['response']);
    }
    respond(502, [
        'success' => false,
        'error' => 'invalid upstream json',
        'raw' => $body,
    ]);
}
@file_put_contents($cacheFile, json_encode([
    'fetched_at' => $now,
    'response' => $json,
], JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
respond(200, $json);
