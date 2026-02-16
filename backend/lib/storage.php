<?php
declare(strict_types=1);

require_once __DIR__ . '/common.php';
require_once __DIR__ . '/db.php';

function load_users(string $path, array $config): array
{
    $pdo = db();
    $stmt = $pdo->query("SELECT * FROM users");
    $users = [];
    foreach ($stmt->fetchAll() as $row) {
        $users[$row['username']] = [
            'username' => $row['username'],
            'password_hash' => $row['password_hash'],
            'role' => $row['role'],
            'created_at' => (int)$row['created_at'],
            'activated' => (bool)$row['activated'],
            'activated_until' => (int)($row['activated_until'] ?? 0),
            'devices' => [],
            'failed_attempts' => (int)$row['failed_attempts'],
            'lock_until' => (int)$row['lock_until'],
        ];
    }

    $devStmt = $pdo->query("SELECT username, device_id, added_at, last_ip, last_seen FROM user_devices");
    foreach ($devStmt->fetchAll() as $dev) {
        if (!isset($users[$dev['username']])) {
            continue;
        }
        $users[$dev['username']]['devices'][$dev['device_id']] = [
            'added_at' => (int)$dev['added_at'],
            'last_ip' => $dev['last_ip'],
            'last_seen' => (int)$dev['last_seen'],
        ];
    }

    if (!empty($config['bootstrap_admin'])) {
        $adminUser = strtolower($config['admin_user'] ?? 'admin');
        if (!isset($users[$adminUser])) {
            $hash = $config['admin_pass_hash'] ?? password_hash('admin123', PASSWORD_BCRYPT);
            $pdo->prepare("INSERT INTO users (username, password_hash, role, created_at, activated, failed_attempts, lock_until)
                VALUES (:u, :h, 'admin', :created, 1, 0, 0)")
                ->execute([
                    'u' => $adminUser,
                    'h' => $hash,
                    'created' => time(),
                ]);
            $users[$adminUser] = [
                'username' => $adminUser,
                'password_hash' => $hash,
                'role' => 'admin',
                'created_at' => time(),
                'activated' => true,
                'activated_until' => 0,
                'devices' => [],
                'failed_attempts' => 0,
                'lock_until' => 0,
            ];
        }
    }

    return $users;
}

function save_users(string $path, array $users): void
{
    $pdo = db();
    foreach ($users as $user) {
        $pdo->prepare("REPLACE INTO users (username, password_hash, role, created_at, activated, activated_until, failed_attempts, lock_until)
            VALUES (:u, :h, :r, :created, :activated, :activated_until, :failed, :lock_until)")
            ->execute([
                'u' => $user['username'],
                'h' => $user['password_hash'],
                'r' => $user['role'] ?? 'user',
                'created' => (int)($user['created_at'] ?? time()),
                'activated' => !empty($user['activated']) ? 1 : 0,
                'activated_until' => (int)($user['activated_until'] ?? 0),
                'failed' => (int)($user['failed_attempts'] ?? 0),
                'lock_until' => (int)($user['lock_until'] ?? 0),
            ]);

        if (!empty($user['devices']) && is_array($user['devices'])) {
            foreach ($user['devices'] as $deviceId => $info) {
                $pdo->prepare("REPLACE INTO user_devices (username, device_id, added_at, last_ip, last_seen)
                    VALUES (:u, :d, :added, :ip, :seen)")
                    ->execute([
                        'u' => $user['username'],
                        'd' => $deviceId,
                        'added' => (int)($info['added_at'] ?? time()),
                        'ip' => (string)($info['last_ip'] ?? ''),
                        'seen' => (int)($info['last_seen'] ?? time()),
                    ]);
            }
        }
    }
}

function load_cards(string $path): array
{
    $pdo = db();
    $stmt = $pdo->query("SELECT * FROM cards");
    $cards = [];
    foreach ($stmt->fetchAll() as $row) {
        $cards[$row['code']] = [
            'code' => $row['code'],
            'category' => $row['category'] ?? '',
            'created_at' => (int)$row['created_at'],
            'expires_at' => (int)$row['expires_at'],
            'duration_seconds' => (int)($row['duration_seconds'] ?? 0),
            'max_uses' => (int)$row['max_uses'],
            'used' => (int)$row['used'],
            'bound_user' => $row['bound_user'],
            'bound_device' => $row['bound_device'],
            'revoked' => (bool)$row['revoked'],
        ];
    }
    return $cards;
}

function save_cards(string $path, array $cards): void
{
    $pdo = db();
    foreach ($cards as $card) {
        $pdo->prepare("REPLACE INTO cards (code, category, created_at, expires_at, duration_seconds, max_uses, used, bound_user, bound_device, revoked)
            VALUES (:code, :category, :created, :expires, :duration, :max_uses, :used, :bound_user, :bound_device, :revoked)")
            ->execute([
                'code' => $card['code'],
                'category' => (string)($card['category'] ?? ''),
                'created' => (int)($card['created_at'] ?? time()),
                'expires' => (int)($card['expires_at'] ?? 0),
                'duration' => (int)($card['duration_seconds'] ?? 0),
                'max_uses' => (int)($card['max_uses'] ?? 1),
                'used' => (int)($card['used'] ?? 0),
                'bound_user' => (string)($card['bound_user'] ?? ''),
                'bound_device' => (string)($card['bound_device'] ?? ''),
                'revoked' => !empty($card['revoked']) ? 1 : 0,
            ]);
    }
}

function password_valid(string $password): bool
{
    if (strlen($password) < 6 || strlen($password) > 64) {
        return false;
    }
    return true;
}

function ensure_device(array &$user, string $deviceId, array $config): void
{
    if ($deviceId === '') {
        return;
    }
    $devices = $user['devices'] ?? [];
    if (!isset($devices[$deviceId])) {
        if (count($devices) >= $config['max_devices']) {
            respond(403, [
                'success' => false,
                'error' => 'device_limit',
                'message' => '设备数量超过限制',
            ]);
        }
        $devices[$deviceId] = [
            'added_at' => time(),
            'last_ip' => get_client_ip(),
            'last_seen' => time(),
        ];
    } else {
        $devices[$deviceId]['last_ip'] = get_client_ip();
        $devices[$deviceId]['last_seen'] = time();
    }
    $user['devices'] = $devices;
}
