<?php
declare(strict_types=1);

require_once __DIR__ . '/db.php';

function init_common_headers(): void
{
    header('Content-Type: application/json; charset=utf-8');
    header('X-Content-Type-Options: nosniff');
    header('X-Frame-Options: DENY');
    header('Cache-Control: no-store');

    $config = load_config();
    $allowedOrigins = $config['allowed_origins'] ?? '*';
    if (isset($_SERVER['HTTP_ORIGIN'])) {
        header('Access-Control-Allow-Origin: ' . $allowedOrigins);
        header('Access-Control-Allow-Headers: Content-Type, Authorization, X-Admin-Token');
        header('Access-Control-Allow-Methods: GET, POST, OPTIONS');
    }
}

function respond(int $status, array $payload): void
{
    http_response_code($status);
    record_request($status);
    echo json_encode($payload, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES);
    exit;
}

function read_json_body(): array
{
    $raw = file_get_contents('php://input');
    if ($raw === false || $raw === '') {
        return [];
    }
    $data = json_decode($raw, true);
    return is_array($data) ? $data : [];
}

function read_file_json(string $path): array
{
    if (!file_exists($path)) {
        return [];
    }
    $content = file_get_contents($path);
    if ($content === false || $content === '') {
        return [];
    }
    $data = json_decode($content, true);
    return is_array($data) ? $data : [];
}

function write_file_json(string $path, array $data): void
{
    $temp = $path . '.tmp';
    file_put_contents($temp, json_encode($data, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES));
    rename($temp, $path);
}

function rate_limit(string $key, int $max, int $windowSec, string $path): void
{
    $now = time();
    $pdo = db();
    $stmt = $pdo->prepare("SELECT data FROM rate_limits WHERE rate_key = :key");
    $stmt->execute(['key' => $key]);
    $row = $stmt->fetch();
    $list = [];
    if ($row && !empty($row['data'])) {
        $decoded = json_decode($row['data'], true);
        if (is_array($decoded)) {
            $list = $decoded;
        }
    }
    $list = array_values(array_filter($list, fn($ts) => ($now - $ts) < $windowSec));
    if (count($list) >= $max) {
        respond(429, ['success' => false, 'error' => 'too_many_requests']);
    }
    $list[] = $now;
    $pdo->prepare("REPLACE INTO rate_limits (rate_key, data, updated_at) VALUES (:key, :data, :updated)")
        ->execute([
            'key' => $key,
            'data' => json_encode($list, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            'updated' => $now,
        ]);
}

function get_client_ip(): string
{
    $cf = $_SERVER['HTTP_CF_CONNECTING_IP'] ?? '';
    if ($cf !== '') {
        return $cf;
    }
    $real = $_SERVER['HTTP_X_REAL_IP'] ?? '';
    if ($real !== '') {
        return $real;
    }
    $forwarded = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? '';
    if ($forwarded !== '') {
        $parts = array_map('trim', explode(',', $forwarded));
        if (!empty($parts)) {
            return $parts[0];
        }
    }
    $client = $_SERVER['HTTP_CLIENT_IP'] ?? '';
    if ($client !== '') {
        return $client;
    }
    $remote = $_SERVER['REMOTE_ADDR'] ?? 'unknown';
    return $remote;
}

function record_request(int $status): void
{
    if (php_sapi_name() === 'cli') {
        return;
    }
    $method = $_SERVER['REQUEST_METHOD'] ?? '';
    if ($method === 'OPTIONS') {
        return;
    }
    $path = parse_url($_SERVER['REQUEST_URI'] ?? '/', PHP_URL_PATH);
    if (!$path) {
        $path = '/';
    }
    $dateKey = date('Y-m-d');
    $pdo = db();
    $stmt = $pdo->prepare("SELECT total, paths, status, methods FROM metrics WHERE date_key = :date");
    $stmt->execute(['date' => $dateKey]);
    $row = $stmt->fetch();
    $paths = [];
    $statuses = [];
    $methods = [];
    $total = 0;
    if ($row) {
        $total = (int)($row['total'] ?? 0);
        $paths = json_decode((string)$row['paths'], true) ?: [];
        $statuses = json_decode((string)$row['status'], true) ?: [];
        $methods = json_decode((string)$row['methods'], true) ?: [];
    }
    $total += 1;
    $paths[$path] = (int)($paths[$path] ?? 0) + 1;
    $statuses[(string)$status] = (int)($statuses[(string)$status] ?? 0) + 1;
    if ($method) {
        $methods[$method] = (int)($methods[$method] ?? 0) + 1;
    }
    $pdo->prepare("REPLACE INTO metrics (date_key, total, paths, status, methods, last_seen) VALUES (:date, :total, :paths, :status, :methods, :seen)")
        ->execute([
            'date' => $dateKey,
            'total' => $total,
            'paths' => json_encode($paths, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            'status' => json_encode($statuses, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            'methods' => json_encode($methods, JSON_UNESCAPED_UNICODE | JSON_UNESCAPED_SLASHES),
            'seen' => time(),
        ]);
}

function normalize_username(string $raw): string
{
    $name = trim($raw);
    if ($name === '') {
        respond(400, ['success' => false, 'error' => 'missing_username']);
    }
    if (!preg_match('/^[a-zA-Z0-9_\\.\\-@]{3,64}$/', $name)) {
        respond(400, ['success' => false, 'error' => 'invalid_username']);
    }
    return strtolower($name);
}

function load_config(): array
{
    static $config = null;
    if ($config !== null) {
        return $config;
    }
    ensure_schema();
    $pdo = db();

    $defaults = [
        'jwt_secret' => bin2hex(random_bytes(32)),
        'access_ttl' => '3600',
        'refresh_ttl' => '1209600',
        'lock_attempts' => '5',
        'lock_seconds' => '600',
        'max_devices' => '3',
        'require_activation' => '1',
        'enable_register' => '1',
        'enable_activation' => '1',
        'admin_user' => 'admin',
        'admin_pass_hash' => password_hash('admin123', PASSWORD_BCRYPT),
        'bootstrap_admin' => '1',
        'admin_token' => '',
        'allowed_origins' => '*',
        'admin_allow_ip' => '104.245.12.20',
        'free_until' => '0',
    ];

    $rows = $pdo->query("SELECT config_key, config_value FROM config")->fetchAll();
    $map = [];
    foreach ($rows as $row) {
        $map[$row['config_key']] = $row['config_value'];
    }
    foreach ($defaults as $key => $value) {
        if (!isset($map[$key]) || $map[$key] === '') {
            $stmt = $pdo->prepare("REPLACE INTO config (config_key, config_value) VALUES (:key, :value)");
            $stmt->execute(['key' => $key, 'value' => (string)$value]);
            $map[$key] = (string)$value;
        }
    }

    $config = [
        'data_dir' => __DIR__ . '/../data',
        'jwt_secret' => $map['jwt_secret'],
        'access_ttl' => (int)$map['access_ttl'],
        'refresh_ttl' => (int)$map['refresh_ttl'],
        'lock_attempts' => (int)$map['lock_attempts'],
        'lock_seconds' => (int)$map['lock_seconds'],
        'max_devices' => (int)$map['max_devices'],
        'require_activation' => $map['require_activation'] !== '0',
        'enable_register' => $map['enable_register'] !== '0',
        'enable_activation' => $map['enable_activation'] !== '0',
        'admin_user' => $map['admin_user'],
        'admin_pass_hash' => $map['admin_pass_hash'],
        'bootstrap_admin' => $map['bootstrap_admin'] !== '0',
        'admin_token' => $map['admin_token'],
        'allowed_origins' => $map['allowed_origins'],
        'admin_allow_ip' => $map['admin_allow_ip'],
        'free_until' => (int)$map['free_until'],
    ];
    return $config;
}

function is_free_active(array $config): bool
{
    $freeUntil = (int)($config['free_until'] ?? 0);
    return $freeUntil > 0 && $freeUntil > time();
}

function auth_admin_token(array $config): bool
{
    $token = $config['admin_token'] ?? '';
    if ($token === '') {
        return false;
    }
    $header = $_SERVER['HTTP_AUTHORIZATION'] ?? '';
    if (stripos($header, 'bearer ') === 0) {
        $given = trim(substr($header, 7));
        return hash_equals($token, $given);
    }
    $given = $_SERVER['HTTP_X_ADMIN_TOKEN'] ?? '';
    if ($given === '') {
        return false;
    }
    return hash_equals($token, $given);
}
