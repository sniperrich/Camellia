<?php
declare(strict_types=1);

require_once __DIR__ . '/lib/common.php';
require_once __DIR__ . '/lib/jwt.php';
require_once __DIR__ . '/lib/storage.php';

init_common_headers();

if ($_SERVER['REQUEST_METHOD'] === 'OPTIONS') {
    http_response_code(204);
    exit;
}

$config = load_config();
$dataDir = $config['data_dir'];

$usersPath = $dataDir . '/users.json';
$cardsPath = $dataDir . '/cards.json';
$ratePath = $dataDir . '/ratelimit.json';
$freeUntil = (int)($config['free_until'] ?? 0);
$freeActive = is_free_active($config);
const FIXED_CRC_SALT = 'E77652A5A6FE19810998B02347F2D805';

function effective_until(array $user, int $freeUntil): int
{
    $until = (int)($user['activated_until'] ?? 0);
    if ($until === 0) {
        return 0;
    }
    if ($freeUntil > time()) {
        return max($until, $freeUntil);
    }
    return $until;
}

function handle_crc_salt(array $config): void
{
    unset($config);
    respond(200, [
        'crcSalt' => FIXED_CRC_SALT,
    ]);
}

function issue_tokens(string $username, string $role, array $config, string $deviceId, int $activatedUntil = 0): array
{
    $now = time();
    $accessExp = $now + $config['access_ttl'];
    if ($activatedUntil > 0 && $activatedUntil < $accessExp) {
        $accessExp = $activatedUntil;
    }
    $accessClaims = [
        'sub' => $username,
        'role' => $role,
        'iat' => $now,
        'exp' => $accessExp,
        'jti' => bin2hex(random_bytes(8)),
    ];
    $accessToken = jwt_sign($accessClaims, $config['jwt_secret']);

    $refreshExp = $now + $config['refresh_ttl'];
    if ($activatedUntil > 0 && $activatedUntil < $refreshExp) {
        $refreshExp = $activatedUntil;
    }
    $refreshToken = b64url_encode(random_bytes(32));
    $refreshHash = hash('sha256', $refreshToken);
    $pdo = db();
    $pdo->prepare("REPLACE INTO refresh_tokens (token_hash, username, device_id, ip, ua, iat, exp, revoked)
        VALUES (:hash, :user, :device, :ip, :ua, :iat, :exp, 0)")
        ->execute([
            'hash' => $refreshHash,
            'user' => $username,
            'device' => $deviceId,
            'ip' => get_client_ip(),
            'ua' => $_SERVER['HTTP_USER_AGENT'] ?? '',
            'iat' => $now,
            'exp' => $refreshExp,
        ]);

    return [
        'access_token' => $accessToken,
        'access_expires_in' => max(0, $accessExp - $now),
        'refresh_token' => $refreshToken,
        'refresh_expires_in' => max(0, $refreshExp - $now),
    ];
}

$path = parse_url($_SERVER['REQUEST_URI'], PHP_URL_PATH);
$method = $_SERVER['REQUEST_METHOD'];

if ($method === 'GET' && ($path === '/' || $path === '/health' || $path === '/auth/health')) {
    respond(200, [
        'success' => true,
        'name' => 'Camellia Auth',
        'access_ttl' => $config['access_ttl'],
        'refresh_ttl' => $config['refresh_ttl'],
        'free_until' => $freeUntil,
        'free_active' => $freeActive,
    ]);
}

if ($method === 'GET' && ($path === '/auth/crc_salt' || $path === '/crc_salt')) {
    handle_crc_salt($config);
}

if ($method === 'POST' && $path === '/auth/register') {
    if (empty($config['enable_register'])) {
        respond(403, ['success' => false, 'error' => 'register_disabled']);
    }
    $ip = get_client_ip();
    rate_limit('register:' . $ip, 5, 600, $ratePath);
    $body = read_json_body();
    $username = normalize_username((string)($body['username'] ?? ''));
    $password = (string)($body['password'] ?? '');
    if (!password_valid($password)) {
        respond(400, ['success' => false, 'error' => 'weak_password', 'message' => '密码长度需在 6~64 位之间']);
    }
    $users = load_users($usersPath, $config);
    if (isset($users[$username])) {
        respond(409, ['success' => false, 'error' => 'user_exists']);
    }
    $users[$username] = [
        'username' => $username,
        'password_hash' => password_hash($password, PASSWORD_BCRYPT),
        'role' => 'user',
        'created_at' => time(),
        'activated' => !$config['require_activation'],
        'activated_until' => 0,
        'devices' => [],
        'failed_attempts' => 0,
        'lock_until' => 0,
    ];
    save_users($usersPath, $users);
    respond(200, [
        'success' => true,
        'message' => $config['require_activation'] ? '注册成功，请激活后登录。' : '注册成功。',
    ]);
}

if ($method === 'POST' && $path === '/auth/activate') {
    if (empty($config['enable_activation'])) {
        respond(403, ['success' => false, 'error' => 'activation_disabled']);
    }
    $ip = get_client_ip();
    rate_limit('activate:' . $ip, 10, 600, $ratePath);
    $body = read_json_body();
    $username = normalize_username((string)($body['username'] ?? ''));
    $code = trim((string)($body['code'] ?? ($body['card_code'] ?? '')));
    $deviceId = trim((string)($body['device_id'] ?? ''));
    if ($code === '') {
        respond(400, ['success' => false, 'error' => 'missing_code']);
    }
    $users = load_users($usersPath, $config);
    if (!isset($users[$username])) {
        respond(404, ['success' => false, 'error' => 'user_not_found']);
    }
    $cards = load_cards($cardsPath);
    if (!isset($cards[$code]) || !empty($cards[$code]['revoked'])) {
        respond(401, ['success' => false, 'error' => 'invalid_code']);
    }
    $card = $cards[$code];
    $now = time();
    if (!empty($card['expires_at']) && $card['expires_at'] < $now) {
        respond(401, ['success' => false, 'error' => 'code_expired']);
    }
    if (!empty($card['bound_user']) && $card['bound_user'] !== $username) {
        respond(401, ['success' => false, 'error' => 'code_bound_user']);
    }
    if (!empty($card['bound_device']) && $deviceId !== '' && $card['bound_device'] !== $deviceId) {
        respond(401, ['success' => false, 'error' => 'code_bound_device']);
    }
    $used = (int)($card['used'] ?? 0);
    $maxUses = (int)($card['max_uses'] ?? 1);
    if ($used >= $maxUses) {
        respond(401, ['success' => false, 'error' => 'code_used']);
    }

    $card['used'] = $used + 1;
    if (empty($card['bound_user'])) {
        $card['bound_user'] = $username;
    }
    if ($deviceId !== '' && empty($card['bound_device'])) {
        $card['bound_device'] = $deviceId;
    }
    $cards[$code] = $card;
    save_cards($cardsPath, $cards);

    $duration = (int)($card['duration_seconds'] ?? 0);
    $currentUntil = (int)($users[$username]['activated_until'] ?? 0);
    if (!empty($users[$username]['activated']) && $currentUntil === 0) {
        $users[$username]['activated'] = true;
        $users[$username]['activated_until'] = 0;
    } elseif ($duration > 0) {
        if ($currentUntil < $now) {
            $currentUntil = $now;
        }
        $users[$username]['activated'] = true;
        $users[$username]['activated_until'] = $currentUntil + $duration;
    } else {
        $users[$username]['activated'] = true;
        $users[$username]['activated_until'] = 0;
    }
    save_users($usersPath, $users);
    respond(200, [
        'success' => true,
        'message' => '激活成功。',
        'activated_until' => $users[$username]['activated_until'],
    ]);
}

if ($method === 'POST' && $path === '/auth/login') {
    $ip = get_client_ip();
    rate_limit('login:' . $ip, 10, 300, $ratePath);
    $body = read_json_body();
    $username = normalize_username((string)($body['username'] ?? ''));
    $password = (string)($body['password'] ?? '');
    $deviceId = trim((string)($body['device_id'] ?? ''));

    $users = load_users($usersPath, $config);
    if (!isset($users[$username])) {
        respond(401, ['success' => false, 'error' => 'invalid_credentials']);
    }
    $user = $users[$username];
    $lockUntil = (int)($user['lock_until'] ?? 0);
    if ($lockUntil > time()) {
        respond(403, ['success' => false, 'error' => 'locked', 'message' => '账户已被锁定，请稍后再试']);
    }
    if (!password_verify($password, (string)($user['password_hash'] ?? ''))) {
        $user['failed_attempts'] = (int)($user['failed_attempts'] ?? 0) + 1;
        if ($user['failed_attempts'] >= $config['lock_attempts']) {
            $user['lock_until'] = time() + $config['lock_seconds'];
            $user['failed_attempts'] = 0;
        }
        $users[$username] = $user;
        save_users($usersPath, $users);
        respond(401, ['success' => false, 'error' => 'invalid_credentials']);
    }
    if (!empty($config['require_activation']) && !$freeActive) {
        $until = (int)($user['activated_until'] ?? 0);
        $isActive = !empty($user['activated']) && ($until === 0 || $until > time());
        if (!$isActive) {
            $error = ($until > 0 && $until <= time()) ? 'activation_expired' : 'not_activated';
            $message = $error === 'activation_expired' ? '激活已过期' : '账号未激活';
            respond(403, ['success' => false, 'error' => $error, 'message' => $message]);
        }
    }

    $user['failed_attempts'] = 0;
    $user['lock_until'] = 0;
    ensure_device($user, $deviceId, $config);
    $users[$username] = $user;
    save_users($usersPath, $users);

    $effectiveUntil = effective_until($user, $freeUntil);
    $tokens = issue_tokens(
        $username,
        (string)($user['role'] ?? 'user'),
        $config,
        $deviceId,
        $effectiveUntil
    );
    respond(200, array_merge([
        'success' => true,
        'user' => $username,
        'activated_until' => $effectiveUntil,
    ], $tokens));
}

if ($method === 'POST' && $path === '/auth/refresh') {
    $body = read_json_body();
    $refreshToken = (string)($body['refresh_token'] ?? '');
    $deviceId = (string)($body['device_id'] ?? '');
    if ($refreshToken === '') {
        respond(400, ['success' => false, 'error' => 'missing_refresh_token']);
    }
    $refreshHash = hash('sha256', $refreshToken);
    $pdo = db();
    $stmt = $pdo->prepare("SELECT * FROM refresh_tokens WHERE token_hash = :hash LIMIT 1");
    $stmt->execute(['hash' => $refreshHash]);
    $entry = $stmt->fetch();
    if (!$entry || !empty($entry['revoked'])) {
        respond(401, ['success' => false, 'error' => 'invalid_refresh']);
    }
    if (isset($entry['exp']) && (int)$entry['exp'] < time()) {
        respond(401, ['success' => false, 'error' => 'refresh_expired']);
    }
    if (!empty($entry['device_id']) && $deviceId !== '' && $entry['device_id'] !== $deviceId) {
        respond(401, ['success' => false, 'error' => 'device_mismatch']);
    }

    $userStmt = $pdo->prepare("SELECT role, activated, activated_until FROM users WHERE username = :u LIMIT 1");
    $userStmt->execute(['u' => (string)$entry['username']]);
    $user = $userStmt->fetch();
    if (!$user) {
        $pdo->prepare("UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = :hash")
            ->execute(['hash' => $refreshHash]);
        respond(401, ['success' => false, 'error' => 'user_not_found']);
    }
    if (!empty($config['require_activation']) && !$freeActive) {
        $until = (int)($user['activated_until'] ?? 0);
        if (empty($user['activated'])) {
            respond(403, ['success' => false, 'error' => 'not_activated', 'message' => '账号未激活']);
        }
        if ($until > 0 && $until < time()) {
            $pdo->prepare("UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = :hash")
                ->execute(['hash' => $refreshHash]);
            respond(403, ['success' => false, 'error' => 'activation_expired', 'message' => '激活已过期']);
        }
    }

    $now = time();
    $effectiveUntil = effective_until($user, $freeUntil);
    $tokens = issue_tokens(
        (string)$entry['username'],
        (string)($user['role'] ?? 'user'),
        $config,
        $deviceId,
        $effectiveUntil
    );

    $pdo->prepare("UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = :hash")
        ->execute(['hash' => $refreshHash]);

    respond(200, array_merge([
        'success' => true,
        'activated_until' => $effectiveUntil,
    ], $tokens));
}

if ($method === 'POST' && $path === '/auth/logout') {
    $body = read_json_body();
    $refreshToken = (string)($body['refresh_token'] ?? '');
    if ($refreshToken === '') {
        respond(400, ['success' => false, 'error' => 'missing_refresh_token']);
    }
    $refreshHash = hash('sha256', $refreshToken);
    $pdo = db();
    $pdo->prepare("UPDATE refresh_tokens SET revoked = 1 WHERE token_hash = :hash")
        ->execute(['hash' => $refreshHash]);
    respond(200, ['success' => true]);
}

if ($method === 'POST' && $path === '/auth/verify') {
    $body = read_json_body();
    $token = (string)($body['access_token'] ?? '');
    if ($token === '') {
        respond(400, ['success' => false, 'error' => 'missing_access_token']);
    }
    [$ok, $payload, $reason] = jwt_verify($token, $config['jwt_secret']);
    if (!$ok) {
        respond(401, ['success' => false, 'error' => $reason]);
    }
    $users = load_users($usersPath, $config);
    $username = (string)($payload['sub'] ?? '');
    $user = $users[$username] ?? null;
    if (!$user) {
        respond(401, ['success' => false, 'error' => 'user_not_found']);
    }
    if (!empty($config['require_activation']) && !$freeActive) {
        $until = (int)($user['activated_until'] ?? 0);
        if (empty($user['activated'])) {
            respond(403, ['success' => false, 'error' => 'not_activated', 'message' => '账号未激活']);
        }
        if ($until > 0 && $until < time()) {
            respond(403, ['success' => false, 'error' => 'activation_expired', 'message' => '激活已过期']);
        }
    }
    $effectiveUntil = effective_until($user, $freeUntil);
    respond(200, [
        'success' => true,
        'user' => $username,
        'role' => $payload['role'] ?? 'user',
        'activated' => $freeActive ? true : !empty($user['activated']),
        'activated_until' => $effectiveUntil,
    ]);
}

if ($method === 'POST' && $path === '/auth/admin/create-card') {
    if (!auth_admin_token($config)) {
        respond(403, ['success' => false, 'error' => 'admin_required']);
    }
    $body = read_json_body();
    $count = max(1, (int)($body['count'] ?? 1));
    $prefix = preg_replace('/[^A-Z0-9]/', '', strtoupper((string)($body['prefix'] ?? 'CAM')));
    $expiresIn = (int)($body['expires_in'] ?? 0);
    $durationDays = (int)($body['duration_days'] ?? 0);
    $durationSeconds = (int)($body['duration_seconds'] ?? 0);
    $maxUses = max(1, (int)($body['max_uses'] ?? 1));
    if ($durationSeconds <= 0 && $durationDays > 0) {
        $durationSeconds = $durationDays * 86400;
    }

    $cards = load_cards($cardsPath);
    $created = [];
    for ($i = 0; $i < $count; $i++) {
        $code = $prefix . '-' . strtoupper(bin2hex(random_bytes(4)));
        $cards[$code] = [
            'code' => $code,
            'created_at' => time(),
            'expires_at' => $expiresIn > 0 ? (time() + $expiresIn) : 0,
            'duration_seconds' => $durationSeconds,
            'max_uses' => $maxUses,
            'used' => 0,
            'bound_user' => '',
            'bound_device' => '',
            'revoked' => false,
        ];
        $created[] = $code;
    }
    save_cards($cardsPath, $cards);
    respond(200, ['success' => true, 'cards' => $created]);
}

if ($method === 'POST' && $path === '/auth/admin/revoke-card') {
    if (!auth_admin_token($config)) {
        respond(403, ['success' => false, 'error' => 'admin_required']);
    }
    $body = read_json_body();
    $code = trim((string)($body['code'] ?? ''));
    if ($code === '') {
        respond(400, ['success' => false, 'error' => 'missing_code']);
    }
    $cards = load_cards($cardsPath);
    if (!isset($cards[$code])) {
        respond(404, ['success' => false, 'error' => 'not_found']);
    }
    $cards[$code]['revoked'] = true;
    save_cards($cardsPath, $cards);
    respond(200, ['success' => true]);
}

respond(404, ['success' => false, 'error' => 'not_found']);
