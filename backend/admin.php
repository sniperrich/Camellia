<?php
declare(strict_types=1);

require_once __DIR__ . '/lib/common.php';
require_once __DIR__ . '/lib/storage.php';
require_once __DIR__ . '/lib/db.php';

header('X-Content-Type-Options: nosniff');
header('X-Frame-Options: DENY');
header('Cache-Control: no-store');

$config = load_config();
$allowedIp = $config['admin_allow_ip'] ?? '104.245.12.20';
$ipList = array_filter(array_map('trim', explode(',', $allowedIp)));

$candidateIps = [];
$candidateIps[] = $_SERVER['HTTP_CF_CONNECTING_IP'] ?? '';
$candidateIps[] = $_SERVER['HTTP_X_FORWARDED_FOR'] ?? '';
$candidateIps[] = $_SERVER['HTTP_X_REAL_IP'] ?? '';
$candidateIps[] = $_SERVER['HTTP_CLIENT_IP'] ?? '';
$candidateIps[] = $_SERVER['REMOTE_ADDR'] ?? '';

$expanded = [];
foreach ($candidateIps as $value) {
    if (!$value) {
        continue;
    }
    $parts = array_map('trim', explode(',', $value));
    foreach ($parts as $part) {
        if ($part !== '') {
            $expanded[] = $part;
        }
    }
}
$expanded = array_values(array_unique($expanded));

$allowed = false;
foreach ($expanded as $ip) {
    if (in_array($ip, $ipList, true)) {
        $allowed = true;
        break;
    }
}

if (!$allowed) {
    http_response_code(403);
    echo 'Forbidden';
    exit;
}

session_start();

$loggedIn = is_admin_logged_in();

function set_flash(string $message, bool $ok = true, array $debug = []): void
{
    $_SESSION['flash'] = [
        'ok' => $ok,
        'message' => $message,
        'time' => time(),
    ];
    if ($debug) {
        $_SESSION['flash_debug'] = $debug;
    }
}

$users = load_users('', $config);

function is_admin_logged_in(): bool
{
    return !empty($_SESSION['admin_user']);
}

function require_admin_role(array $users, string $username): bool
{
    if (!isset($users[$username])) {
        return false;
    }
    return ($users[$username]['role'] ?? 'user') === 'admin';
}

function normalize_category(string $raw): string
{
    $cat = trim($raw);
    if ($cat === '') {
        return '';
    }
    $cat = preg_replace('/[\\r\\n\\t]+/', ' ', $cat);
    $cat = preg_replace('/\\s{2,}/', ' ', $cat);
    if (strlen($cat) > 64) {
        $cat = substr($cat, 0, 64);
    }
    return $cat;
}

function safe_category_filename(string $category): string
{
    $name = preg_replace('/[^A-Za-z0-9_-]+/', '_', $category);
    $name = trim($name, '_');
    return $name !== '' ? $name : 'uncategorized';
}

if ($loggedIn && $_SERVER['REQUEST_METHOD'] === 'GET' && isset($_GET['download_category'])) {
    $rawCategory = (string)($_GET['download_category'] ?? '');
    $category = normalize_category($rawCategory);
    $pdo = db();
    if ($rawCategory === '__all__') {
        $stmt = $pdo->query("SELECT code FROM cards ORDER BY created_at DESC");
        $filename = 'cards_all.txt';
    } elseif ($rawCategory === '__empty__' || $category === '') {
        $stmt = $pdo->prepare("SELECT code FROM cards WHERE category = '' ORDER BY created_at DESC");
        $stmt->execute();
        $filename = 'cards_' . safe_category_filename('uncategorized') . '.txt';
    } else {
        $stmt = $pdo->prepare("SELECT code FROM cards WHERE category = :cat ORDER BY created_at DESC");
        $stmt->execute(['cat' => $category]);
        $filename = 'cards_' . safe_category_filename($category) . '.txt';
    }
    $rows = $stmt->fetchAll();
    header('Content-Type: text/plain; charset=utf-8');
    header('Content-Disposition: attachment; filename="' . $filename . '"');
    foreach ($rows as $row) {
        echo $row['code'] . "\n";
    }
    exit;
}

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $action = $_POST['action'] ?? '';
    if ($action === 'login') {
        $rawUser = trim((string)($_POST['username'] ?? ''));
        if ($rawUser === '') {
            $error = '请输入账号';
            $username = '';
        } else {
            $username = strtolower($rawUser);
        }
        $password = (string)($_POST['password'] ?? '');
        if (!isset($users[$username])) {
            $error = '账号或密码错误';
        } else {
            $user = $users[$username];
            $ok = password_verify($password, (string)($user['password_hash'] ?? ''));
            if (!$ok || !require_admin_role($users, $username)) {
                $error = '账号或密码错误';
            } else {
                session_regenerate_id(true);
                $_SESSION['admin_user'] = $username;
                header('Location: /admin.php');
                exit;
            }
        }
    } elseif ($action === 'logout') {
        session_destroy();
        header('Location: /admin.php');
        exit;
    } elseif ($action === 'update_config' && $loggedIn) {
        foreach ([
            'allowed_origins',
            'admin_allow_ip',
            'admin_token',
            'access_ttl',
            'refresh_ttl',
            'lock_attempts',
            'lock_seconds',
            'max_devices',
            'require_activation',
            'enable_register',
            'enable_activation',
            'bootstrap_admin',
            'crc_auth_token',
            'free_until',
        ] as $key) {
            if (isset($_POST[$key])) {
                $stmt = db()->prepare("REPLACE INTO config (config_key, config_value) VALUES (:k, :v)");
                $stmt->execute(['k' => $key, 'v' => (string)$_POST[$key]]);
            }
        }
        $freeDays = (int)($_POST['free_days'] ?? 0);
        if ($freeDays > 0) {
            $now = time();
            $freeSeconds = $freeDays * 86400;
            $newUntil = $now + $freeSeconds;
            if (!empty($config['free_until']) && (int)$config['free_until'] > $newUntil) {
                $newUntil = (int)$config['free_until'];
            }
            db()->prepare("REPLACE INTO config (config_key, config_value) VALUES ('free_until', :v)")
                ->execute(['v' => (string)$newUntil]);

            // Extend activated users (skip perpetual activated_until=0)
            db()->prepare(
                "UPDATE users
                 SET activated_until = (CASE
                     WHEN activated_until = 0 THEN 0
                     WHEN activated_until < :now THEN :now
                     ELSE activated_until
                 END) + :delta
                 WHERE activated = 1 AND activated_until <> 0"
            )->execute([
                'now' => $now,
                'delta' => $freeSeconds,
            ]);
        }
        if (!empty($_POST['admin_user'])) {
            $newUser = strtolower(trim((string)$_POST['admin_user']));
            if ($newUser !== '') {
                db()->prepare("REPLACE INTO config (config_key, config_value) VALUES ('admin_user', :v)")
                    ->execute(['v' => $newUser]);
            }
        }
        if (!empty($_POST['admin_pass'])) {
            $hash = password_hash((string)$_POST['admin_pass'], PASSWORD_BCRYPT);
            db()->prepare("REPLACE INTO config (config_key, config_value) VALUES ('admin_pass_hash', :v)")
                ->execute(['v' => $hash]);
            $adminUser = $config['admin_user'] ?? 'admin';
            $stmt = db()->prepare(
                "INSERT INTO users (username, password_hash, role, created_at, activated, activated_until, failed_attempts, lock_until)
                 VALUES (:u, :h, 'admin', :created, 1, 0, 0, 0)
                 ON DUPLICATE KEY UPDATE password_hash = VALUES(password_hash), role = 'admin', activated = 1, activated_until = 0"
            );
            $stmt->execute([
                'u' => $adminUser,
                'h' => $hash,
                'created' => time(),
            ]);
        }
        set_flash('配置已保存', true, ['action' => 'update_config']);
        header('Location: /admin.php');
        exit;
    } elseif ($loggedIn && $action === 'create_user') {
        $username = strtolower(trim((string)($_POST['username'] ?? '')));
        $password = (string)($_POST['password'] ?? '');
        $role = (string)($_POST['role'] ?? 'user');
        $activated = isset($_POST['activated']) ? 1 : 0;
        if ($username === '' || !preg_match('/^[a-zA-Z0-9_\\.\\-@]{3,64}$/', $username)) {
            $error = '账号格式不合法';
        } elseif (strlen($password) < 6) {
            $error = '密码长度至少 6 位';
        } else {
            $stmt = db()->prepare("SELECT username FROM users WHERE username = :u");
            $stmt->execute(['u' => $username]);
            if ($stmt->fetch()) {
                $error = '账号已存在';
            } else {
                $hash = password_hash($password, PASSWORD_BCRYPT);
                db()->prepare("INSERT INTO users (username, password_hash, role, created_at, activated, activated_until, failed_attempts, lock_until)
                    VALUES (:u, :h, :r, :created, :activated, 0, 0, 0)")
                    ->execute([
                        'u' => $username,
                        'h' => $hash,
                        'r' => $role === 'admin' ? 'admin' : 'user',
                        'created' => time(),
                        'activated' => $activated,
                    ]);
                set_flash("用户 {$username} 已创建", true, ['action' => 'create_user', 'username' => $username]);
                header('Location: /admin.php');
                exit;
            }
        }
    } elseif ($loggedIn && $action === 'delete_user') {
        $username = strtolower(trim((string)($_POST['username'] ?? '')));
        if ($username && $username !== ($_SESSION['admin_user'] ?? '')) {
            db()->prepare("DELETE FROM users WHERE username = :u")->execute(['u' => $username]);
            db()->prepare("DELETE FROM user_devices WHERE username = :u")->execute(['u' => $username]);
        }
        set_flash("用户 {$username} 已删除", true, ['action' => 'delete_user', 'username' => $username]);
        header('Location: /admin.php');
        exit;
    } elseif ($loggedIn && $action === 'reset_user_password') {
        $username = strtolower(trim((string)($_POST['username'] ?? '')));
        $password = (string)($_POST['password'] ?? '');
        if ($username && $password !== '') {
            $hash = password_hash($password, PASSWORD_BCRYPT);
            db()->prepare("UPDATE users SET password_hash = :h WHERE username = :u")
                ->execute(['h' => $hash, 'u' => $username]);
        }
        set_flash("用户 {$username} 密码已更新", true, ['action' => 'reset_user_password', 'username' => $username]);
        header('Location: /admin.php');
        exit;
    } elseif ($loggedIn && $action === 'toggle_user_activation') {
        $username = strtolower(trim((string)($_POST['username'] ?? '')));
        $activated = (int)($_POST['activated'] ?? 0);
        if ($username) {
            $until = $activated ? 0 : 0;
            db()->prepare("UPDATE users SET activated = :a, activated_until = :uuntil WHERE username = :u")
                ->execute(['a' => $activated, 'uuntil' => $until, 'u' => $username]);
        }
        set_flash("用户 {$username} " . ($activated ? '已激活' : '已冻结'), true, ['action' => 'toggle_user_activation', 'username' => $username, 'activated' => $activated]);
        header('Location: /admin.php');
        exit;
    } elseif ($loggedIn && $action === 'create_cards') {
        $category = normalize_category((string)($_POST['category'] ?? ''));
        $count = max(1, (int)($_POST['count'] ?? 1));
        $prefix = preg_replace('/[^A-Z0-9]/', '', strtoupper((string)($_POST['prefix'] ?? 'CAM')));
        $expiresIn = (int)($_POST['expires_in'] ?? 0);
        $durationDays = max(0, (int)($_POST['duration_days'] ?? 0));
        $durationHours = max(0, (int)($_POST['duration_hours'] ?? 0));
        $durationMinutes = max(0, (int)($_POST['duration_minutes'] ?? 0));
        $durationSeconds = ($durationDays * 86400) + ($durationHours * 3600) + ($durationMinutes * 60);
        $maxUses = max(1, (int)($_POST['max_uses'] ?? 1));
        for ($i = 0; $i < $count; $i++) {
            $code = $prefix . '-' . strtoupper(bin2hex(random_bytes(4)));
            db()->prepare("REPLACE INTO cards (code, category, created_at, expires_at, duration_seconds, max_uses, used, bound_user, bound_device, revoked)
                VALUES (:code, :category, :created, :expires, :duration, :max_uses, 0, '', '', 0)")
                ->execute([
                    'code' => $code,
                    'category' => $category,
                    'created' => time(),
                    'expires' => $expiresIn > 0 ? (time() + $expiresIn) : 0,
                    'duration' => $durationSeconds,
                    'max_uses' => $maxUses,
                ]);
        }
        set_flash("已生成 {$count} 张卡密", true, [
            'action' => 'create_cards',
            'count' => $count,
            'expires_in' => $expiresIn,
            'duration_seconds' => $durationSeconds,
            'max_uses' => $maxUses,
            'category' => $category,
        ]);
        header('Location: /admin.php');
        exit;
    } elseif ($loggedIn && $action === 'bulk_create_cards') {
        $linesRaw = (string)($_POST['bulk_lines'] ?? '');
        $lines = preg_split('/\\r?\\n/', $linesRaw);
        $total = 0;
        $createdCategories = [];
        foreach ($lines as $line) {
            $line = trim($line);
            if ($line === '') {
                continue;
            }
            $parts = preg_split('/\\s*,\\s*|\\t+/', $line);
            $category = normalize_category((string)($parts[0] ?? ''));
            $prefix = preg_replace('/[^A-Z0-9]/', '', strtoupper((string)($parts[1] ?? '')));
            if ($prefix === '') {
                continue;
            }
            $count = max(1, (int)($parts[2] ?? 1));
            $expiresIn = max(0, (int)($parts[3] ?? 0));
            $durationDays = max(0, (int)($parts[4] ?? 0));
            $durationHours = max(0, (int)($parts[5] ?? 0));
            $durationMinutes = 0;
            if (count($parts) >= 8) {
                $durationMinutes = max(0, (int)($parts[6] ?? 0));
                $maxUses = max(1, (int)($parts[7] ?? 1));
            } else {
                $maxUses = max(1, (int)($parts[6] ?? 1));
            }
            $durationSeconds = ($durationDays * 86400) + ($durationHours * 3600) + ($durationMinutes * 60);
            for ($i = 0; $i < $count; $i++) {
                $code = $prefix . '-' . strtoupper(bin2hex(random_bytes(4)));
                db()->prepare("REPLACE INTO cards (code, category, created_at, expires_at, duration_seconds, max_uses, used, bound_user, bound_device, revoked)
                    VALUES (:code, :category, :created, :expires, :duration, :max_uses, 0, '', '', 0)")
                    ->execute([
                        'code' => $code,
                        'category' => $category,
                        'created' => time(),
                        'expires' => $expiresIn > 0 ? (time() + $expiresIn) : 0,
                        'duration' => $durationSeconds,
                        'max_uses' => $maxUses,
                    ]);
                $total += 1;
            }
            $createdCategories[$category !== '' ? $category : '未分类'] = true;
        }
        $categoryList = implode(', ', array_keys($createdCategories));
        set_flash("批量生成完成：{$total} 张卡密", true, [
            'action' => 'bulk_create_cards',
            'count' => $total,
            'categories' => $categoryList,
        ]);
        header('Location: /admin.php');
        exit;
    } elseif ($loggedIn && $action === 'revoke_card') {
        $code = trim((string)($_POST['code'] ?? ''));
        if ($code !== '') {
            db()->prepare("UPDATE cards SET revoked = 1 WHERE code = :code")->execute(['code' => $code]);
        }
        set_flash("卡密 {$code} 已撤销", true, ['action' => 'revoke_card', 'code' => $code]);
        header('Location: /admin.php');
        exit;
    } elseif ($loggedIn && $action === 'delete_card') {
        $code = trim((string)($_POST['code'] ?? ''));
        if ($code !== '') {
            db()->prepare("DELETE FROM cards WHERE code = :code")->execute(['code' => $code]);
        }
        set_flash("卡密 {$code} 已删除", true, ['action' => 'delete_card', 'code' => $code]);
        header('Location: /admin.php');
        exit;
    }
}

$flash = $_SESSION['flash'] ?? null;
$flashDebug = $_SESSION['flash_debug'] ?? null;
unset($_SESSION['flash'], $_SESSION['flash_debug']);

function format_count(int $value): string
{
    return number_format($value);
}

function user_is_active(array $user, int $now): bool
{
    if (empty($user['activated'])) {
        return false;
    }
    $until = (int)($user['activated_until'] ?? 0);
    return $until === 0 || $until > $now;
}

function format_until(int $timestamp): string
{
    if ($timestamp === 0) {
        return '永久';
    }
    return date('Y-m-d H:i', $timestamp);
}

function format_duration(int $seconds): string
{
    if ($seconds <= 0) {
        return '永久';
    }
    $days = intdiv($seconds, 86400);
    $hours = intdiv($seconds % 86400, 3600);
    if ($days > 0 && $hours > 0) {
        return "{$days} 天 {$hours} 小时";
    }
    if ($days > 0) {
        return "{$days} 天";
    }
    return "{$hours} 小时";
}

function compute_user_stats(array $users): array
{
    $now = time();
    $total = count($users);
    $activated = 0;
    $locked = 0;
    $admins = 0;
    $devices = 0;
    foreach ($users as $user) {
        if (user_is_active($user, $now)) {
            $activated++;
        }
        if (!empty($user['lock_until']) && $user['lock_until'] > $now) {
            $locked++;
        }
        if (($user['role'] ?? '') === 'admin') {
            $admins++;
        }
        $devices += is_array($user['devices'] ?? null) ? count($user['devices']) : 0;
    }
    return [
        'total' => $total,
        'activated' => $activated,
        'locked' => $locked,
        'admins' => $admins,
        'devices' => $devices,
    ];
}

function compute_card_stats(array $cards): array
{
    $now = time();
    $total = count($cards);
    $revoked = 0;
    $expired = 0;
    $used = 0;
    $unused = 0;
    $active = 0;
    $bound = 0;
    foreach ($cards as $card) {
        if (!empty($card['revoked'])) {
            $revoked++;
            continue;
        }
        $expiresAt = (int)($card['expires_at'] ?? 0);
        if ($expiresAt > 0 && $expiresAt < $now) {
            $expired++;
            continue;
        }
        $usedCount = (int)($card['used'] ?? 0);
        $maxUses = (int)($card['max_uses'] ?? 1);
        if ($usedCount >= $maxUses) {
            $used++;
        } elseif ($usedCount === 0) {
            $unused++;
            $active++;
        } else {
            $active++;
        }
        if (!empty($card['bound_user']) || !empty($card['bound_device'])) {
            $bound++;
        }
    }
    return [
        'total' => $total,
        'revoked' => $revoked,
        'expired' => $expired,
        'used' => $used,
        'unused' => $unused,
        'active' => $active,
        'bound' => $bound,
    ];
}

function compute_metrics(): array
{
    $pdo = db();
    $dateKey = date('Y-m-d');
    $stmt = $pdo->prepare("SELECT total, paths, status, methods FROM metrics WHERE date_key = :date");
    $stmt->execute(['date' => $dateKey]);
    $row = $stmt->fetch();
    $total = 0;
    $paths = [];
    $status = [];
    $methods = [];
    if ($row) {
        $total = (int)($row['total'] ?? 0);
        $paths = json_decode((string)$row['paths'], true) ?: [];
        $status = json_decode((string)$row['status'], true) ?: [];
        $methods = json_decode((string)$row['methods'], true) ?: [];
    }
    arsort($paths);
    arsort($status);
    return [
        'total' => $total,
        'paths' => $paths,
        'status' => $status,
        'methods' => $methods,
    ];
}

if (!$loggedIn) {
    ?>
    <!doctype html>
    <html lang="zh-cn">
    <head>
        <meta charset="utf-8"/>
        <title>Camellia 管理后台</title>
        <style>
            body {
                font-family: "Manrope","Noto Sans","PingFang SC","Microsoft YaHei",sans-serif;
                background: #f3f5f4;
                color: #1c1b1f;
                margin: 0;
                padding: 0;
                display: flex;
                align-items: center;
                justify-content: center;
                min-height: 100vh;
            }
            .card {
                width: 420px;
                background: #ffffff;
                border-radius: 16px;
                padding: 24px;
                box-shadow: 0 10px 30px rgba(17, 24, 39, 0.08);
            }
            .title {
                font-size: 18px;
                font-weight: 700;
                margin-bottom: 6px;
            }
            .subtitle {
                font-size: 13px;
                color: #6b6f76;
                margin-bottom: 20px;
            }
            input {
                width: 100%;
                padding: 10px 12px;
                margin-bottom: 12px;
                border-radius: 10px;
                border: 1px solid #d7e0dd;
                background: #f8faf9;
                outline: none;
                font-size: 14px;
            }
            button {
                width: 100%;
                padding: 10px 12px;
                border-radius: 10px;
                border: none;
                background: #2f8b7c;
                color: #fff;
                font-weight: 600;
                cursor: pointer;
            }
            .error {
                color: #b3261e;
                font-size: 13px;
                margin-bottom: 10px;
            }
        </style>
    </head>
    <body>
        <div class="card">
            <div class="title">Camellia 管理后台</div>
            <div class="subtitle">仅管理员可访问</div>
            <?php if (!empty($error)) : ?>
                <div class="error"><?= htmlspecialchars($error) ?></div>
            <?php endif; ?>
            <form method="post">
                <input type="hidden" name="action" value="login"/>
                <input name="username" placeholder="管理员账号" required />
                <input name="password" placeholder="管理员密码" type="password" required />
                <button type="submit">登录</button>
            </form>
        </div>
    </body>
    </html>
    <?php
    exit;
}

$config = load_config();
$userStats = compute_user_stats($users);
$cardStats = compute_card_stats(load_cards(''));
$metrics = compute_metrics();
$recentUsers = db()->query("SELECT username, role, activated, activated_until, created_at, lock_until FROM users ORDER BY created_at DESC LIMIT 50")->fetchAll();
$cardCategories = db()->query("SELECT DISTINCT category FROM cards ORDER BY category ASC")->fetchAll();
$cardCategoryFilter = trim((string)($_GET['card_category'] ?? ''));
if ($cardCategoryFilter !== '') {
    if ($cardCategoryFilter === '__empty__') {
        $stmt = db()->prepare("SELECT code, category, used, max_uses, revoked, expires_at, duration_seconds, bound_user, bound_device, created_at FROM cards WHERE category = '' ORDER BY created_at DESC LIMIT 200");
        $stmt->execute();
    } else {
        $stmt = db()->prepare("SELECT code, category, used, max_uses, revoked, expires_at, duration_seconds, bound_user, bound_device, created_at FROM cards WHERE category = :cat ORDER BY created_at DESC LIMIT 200");
        $stmt->execute(['cat' => $cardCategoryFilter]);
    }
    $recentCards = $stmt->fetchAll();
} else {
    $recentCards = db()->query("SELECT code, category, used, max_uses, revoked, expires_at, duration_seconds, bound_user, bound_device, created_at FROM cards ORDER BY created_at DESC LIMIT 50")->fetchAll();
}

?>
<!doctype html>
<html lang="zh-cn">
<head>
    <meta charset="utf-8"/>
    <title>Camellia 管理后台</title>
    <style>
        :root {
            --bg: #f2f4f3;
            --card: #ffffff;
            --border: #e0e5e2;
            --text: #1c1b1f;
            --muted: #6b6f76;
            --accent: #2f8b7c;
            --accent-strong: #237266;
            --danger: #c62828;
            --warn: #ef6c00;
        }
        body {
            font-family: "Manrope","Noto Sans","PingFang SC","Microsoft YaHei",sans-serif;
            background: var(--bg);
            color: var(--text);
            margin: 0;
        }
        .header {
            padding: 20px 28px;
            background: var(--card);
            border-bottom: 1px solid var(--border);
            display: flex;
            align-items: center;
            justify-content: space-between;
            position: sticky;
            top: 0;
            z-index: 10;
        }
        .header h1 {
            margin: 0;
            font-size: 20px;
        }
        .flash {
            max-width: 1180px;
            margin: 16px auto;
            padding: 12px 16px;
            border-radius: 12px;
            border: 1px solid transparent;
            font-size: 14px;
        }
        .flash.ok {
            background: #e7f5f1;
            color: #0f3d36;
            border-color: var(--accent);
        }
        .flash.error {
            background: #fdecea;
            color: #7f1d1d;
            border-color: #ef4444;
        }
        .debug-box {
            max-width: 1180px;
            margin: 0 auto 16px;
            padding: 12px 16px;
            border-radius: 12px;
            background: #111827;
            color: #e5e7eb;
            font-size: 12px;
            overflow: auto;
            white-space: pre-wrap;
        }
        .container {
            max-width: 1180px;
            margin: 0 auto;
            padding: 18px 28px;
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
            gap: 16px;
        }
        .card {
            background: var(--card);
            border-radius: 14px;
            padding: 18px;
            border: 1px solid var(--border);
            box-shadow: 0 6px 18px rgba(17, 24, 39, 0.05);
        }
        .card h3 {
            margin: 0 0 10px 0;
            font-size: 15px;
        }
        .stat {
            font-size: 22px;
            font-weight: 700;
            color: var(--accent);
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 12px;
        }
        .list {
            margin: 0;
            padding: 0;
            list-style: none;
            font-size: 13px;
        }
        .list li {
            display: flex;
            justify-content: space-between;
            padding: 4px 0;
            border-bottom: 1px dashed #eee;
            gap: 10px;
        }
        .list li span {
            flex: 1;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
        }
        .muted {
            color: var(--muted);
            font-size: 12px;
        }
        .grid {
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 12px;
            margin-top: 8px;
        }
        label {
            font-size: 12px;
            color: #5a5f66;
        }
        input {
            width: 100%;
            padding: 8px 10px;
            border-radius: 10px;
            border: 1px solid var(--border);
            background: #f8faf9;
            font-size: 13px;
        }
        .btn {
            border: 1px solid var(--border);
            background: #ffffff;
            padding: 6px 12px;
            border-radius: 10px;
            cursor: pointer;
            font-size: 12px;
            font-weight: 600;
            color: #1f2937;
        }
        .btn.primary {
            background: var(--accent);
            border-color: var(--accent);
            color: #fff;
        }
        .btn.primary:hover {
            background: var(--accent-strong);
        }
        .btn.ghost {
            background: transparent;
        }
        .btn.danger {
            color: #fff;
            background: var(--danger);
            border-color: var(--danger);
        }
        .btn.warn {
            color: #fff;
            background: var(--warn);
            border-color: var(--warn);
        }
        .table-wrap {
            width: 100%;
            overflow-x: auto;
            border-radius: 12px;
            border: 1px solid var(--border);
            margin-top: 12px;
        }
        table {
            width: 100%;
            border-collapse: collapse;
            font-size: 12.5px;
            min-width: 720px;
            background: #fff;
        }
        th, td {
            padding: 10px 12px;
            text-align: left;
            border-bottom: 1px solid #eef2f1;
            vertical-align: top;
        }
        th {
            background: #f6f8f7;
            color: #394150;
            font-weight: 600;
            font-size: 12px;
        }
        tr:hover td {
            background: #f9fbfa;
        }
        .badge {
            display: inline-flex;
            align-items: center;
            gap: 6px;
            padding: 2px 8px;
            border-radius: 999px;
            font-size: 11px;
            font-weight: 600;
            background: #edf7f5;
            color: #1b5e55;
            border: 1px solid #b8e3d8;
        }
        .badge.warn {
            background: #fff4e5;
            color: #8a4b00;
            border-color: #ffd7a6;
        }
        .badge.danger {
            background: #fdecea;
            color: #8b1d1d;
            border-color: #f5bcbc;
        }
        .code {
            font-family: "SFMono-Regular","Menlo","Consolas",monospace;
            font-size: 12px;
            word-break: break-all;
        }
        .actions {
            display: flex;
            gap: 6px;
            flex-wrap: wrap;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1>Camellia 管理后台</h1>
        <form method="post">
            <input type="hidden" name="action" value="logout"/>
            <button class="btn ghost" type="submit">退出</button>
        </form>
    </div>
    <?php if (!empty($flash)) : ?>
        <div class="flash <?= !empty($flash['ok']) ? 'ok' : 'error' ?>">
            <?= htmlspecialchars($flash['message'] ?? '') ?>
        </div>
        <?php if (!empty($flashDebug)) : ?>
            <div class="debug-box"><?= htmlspecialchars(json_encode($flashDebug, JSON_PRETTY_PRINT | JSON_UNESCAPED_UNICODE)) ?></div>
            <script>
                console.log('admin_debug', <?= json_encode($flashDebug, JSON_UNESCAPED_UNICODE) ?>);
            </script>
        <?php endif; ?>
    <?php endif; ?>

    <div class="container">
        <div class="card">
            <h3>今日请求统计</h3>
            <div class="stat"><?= format_count($metrics['total']) ?></div>
            <div class="muted">今日总请求数</div>
        </div>
        <div class="card">
            <h3>用户总数</h3>
            <div class="stat"><?= format_count($userStats['total']) ?></div>
            <div class="muted">已激活 <?= format_count($userStats['activated']) ?> / 锁定 <?= format_count($userStats['locked']) ?></div>
        </div>
        <div class="card">
            <h3>卡密总数</h3>
            <div class="stat"><?= format_count($cardStats['total']) ?></div>
            <div class="muted">有效 <?= format_count($cardStats['active']) ?> / 已用 <?= format_count($cardStats['used']) ?></div>
        </div>
        <div class="card">
            <h3>设备统计</h3>
            <div class="stat"><?= format_count($userStats['devices']) ?></div>
            <div class="muted">管理员 <?= format_count($userStats['admins']) ?></div>
        </div>
    </div>

    <div class="container">
        <div class="card">
            <h3>请求路径分布（今日 Top）</h3>
            <ul class="list">
                <?php foreach (array_slice($metrics['paths'], 0, 8) as $path => $count) : ?>
                    <li><span title="<?= htmlspecialchars($path) ?>"><?= htmlspecialchars($path) ?></span><strong><?= format_count((int)$count) ?></strong></li>
                <?php endforeach; ?>
            </ul>
        </div>
        <div class="card">
            <h3>状态码统计（今日）</h3>
            <ul class="list">
                <?php foreach ($metrics['status'] as $status => $count) : ?>
                    <li><span><?= htmlspecialchars((string)$status) ?></span><strong><?= format_count((int)$count) ?></strong></li>
                <?php endforeach; ?>
            </ul>
        </div>
        <div class="card">
            <h3>卡密细分</h3>
            <ul class="list">
                <li><span>可用</span><strong><?= format_count($cardStats['active']) ?></strong></li>
                <li><span>未使用</span><strong><?= format_count($cardStats['unused']) ?></strong></li>
                <li><span>已用尽</span><strong><?= format_count($cardStats['used']) ?></strong></li>
                <li><span>已绑定</span><strong><?= format_count($cardStats['bound']) ?></strong></li>
                <li><span>已撤销</span><strong><?= format_count($cardStats['revoked']) ?></strong></li>
                <li><span>已过期</span><strong><?= format_count($cardStats['expired']) ?></strong></li>
            </ul>
        </div>
    </div>

    <div class="container">
        <div class="card" style="grid-column: 1 / -1;">
            <h3>配置管理</h3>
            <form method="post">
                <input type="hidden" name="action" value="update_config"/>
                <div class="grid">
                    <div>
                        <label>管理员账号</label>
                        <input name="admin_user" value="<?= htmlspecialchars($config['admin_user'] ?? 'admin') ?>"/>
                    </div>
                    <div>
                        <label>管理员密码（明文）</label>
                        <input name="admin_pass" placeholder="留空不修改"/>
                    </div>
                    <div>
                        <label>后台允许 IP（逗号分隔）</label>
                        <input name="admin_allow_ip" value="<?= htmlspecialchars($config['admin_allow_ip'] ?? '') ?>"/>
                    </div>
                    <div>
                        <label>允许跨域</label>
                        <input name="allowed_origins" value="<?= htmlspecialchars($config['allowed_origins'] ?? '*') ?>"/>
                    </div>
                    <div>
                        <label>CRC Auth Token</label>
                        <input name="crc_auth_token" placeholder="留空不修改"/>
                    </div>
                    <div>
                        <label>Access TTL（秒）</label>
                        <input name="access_ttl" value="<?= htmlspecialchars((string)($config['access_ttl'] ?? '3600')) ?>"/>
                    </div>
                    <div>
                        <label>Refresh TTL（秒）</label>
                        <input name="refresh_ttl" value="<?= htmlspecialchars((string)($config['refresh_ttl'] ?? '1209600')) ?>"/>
                    </div>
                    <div>
                        <label>最大设备数</label>
                        <input name="max_devices" value="<?= htmlspecialchars((string)($config['max_devices'] ?? '3')) ?>"/>
                    </div>
                    <div>
                        <label>登录锁定次数</label>
                        <input name="lock_attempts" value="<?= htmlspecialchars((string)($config['lock_attempts'] ?? '5')) ?>"/>
                    </div>
                    <div>
                        <label>锁定秒数</label>
                        <input name="lock_seconds" value="<?= htmlspecialchars((string)($config['lock_seconds'] ?? '600')) ?>"/>
                    </div>
                    <div>
                        <label>允许注册（1/0）</label>
                        <input name="enable_register" value="<?= htmlspecialchars((string)($config['enable_register'] ?? '1')) ?>"/>
                    </div>
                    <div>
                        <label>允许激活（1/0）</label>
                        <input name="enable_activation" value="<?= htmlspecialchars((string)($config['enable_activation'] ?? '1')) ?>"/>
                    </div>
                    <div>
                        <label>需要激活（1/0）</label>
                        <input name="require_activation" value="<?= htmlspecialchars((string)($config['require_activation'] ?? '1')) ?>"/>
                    </div>
                    <div>
                        <label>全员免费截止（Unix 时间戳，0 为关闭）</label>
                        <input name="free_until" value="<?= htmlspecialchars((string)($config['free_until'] ?? '0')) ?>"/>
                    </div>
                    <div>
                        <label>快速开启免费（天数）</label>
                        <input name="free_days" placeholder="例如 3"/>
                    </div>
                    <div>
                        <label>Bootstrap Admin（1/0）</label>
                        <input name="bootstrap_admin" value="<?= htmlspecialchars((string)($config['bootstrap_admin'] ?? '1')) ?>"/>
                    </div>
                </div>
                <div style="margin-top: 12px;">
                    <button class="btn primary" type="submit">保存配置</button>
                </div>
            </form>
        </div>
    </div>

    <div class="container">
        <div class="card" style="grid-column: 1 / -1;">
            <h3>用户管理</h3>
            <?php if (!empty($error)) : ?>
                <div class="muted" style="color:#b3261e;"><?= htmlspecialchars($error) ?></div>
            <?php endif; ?>
            <form method="post" class="grid">
                <input type="hidden" name="action" value="create_user"/>
                <div>
                    <label>账号</label>
                    <input name="username" placeholder="用户名"/>
                </div>
                <div>
                    <label>密码</label>
                    <input name="password" placeholder="至少 6 位"/>
                </div>
                <div>
                    <label>角色</label>
                    <input name="role" placeholder="user/admin"/>
                </div>
                <div style="display:flex;align-items:flex-end;">
                    <label style="display:flex;gap:6px;align-items:center;">
                        <input type="checkbox" name="activated" value="1"/> 激活
                    </label>
                </div>
                <div style="display:flex;align-items:flex-end;">
                    <button class="btn primary" type="submit">新增用户</button>
                </div>
            </form>
            <div class="table-wrap">
            <table>
                <thead><tr><th>账号</th><th>角色</th><th>激活</th><th>到期</th><th>创建时间</th><th>操作</th></tr></thead>
                <tbody>
                <?php foreach ($recentUsers as $u): ?>
                    <?php
                        $uActive = user_is_active($u, time());
                        $uUntil = (int)($u['activated_until'] ?? 0);
                        $uStatus = $uActive ? '是' : ($uUntil > 0 ? '过期' : '否');
                    ?>
                    <tr>
                        <td><?= htmlspecialchars($u['username']) ?></td>
                        <td><?= htmlspecialchars($u['role']) ?></td>
                        <td>
                            <?php if ($uActive): ?>
                                <span class="badge">已激活</span>
                            <?php elseif ($uUntil > 0): ?>
                                <span class="badge warn">已过期</span>
                            <?php else: ?>
                                <span class="badge danger">未激活</span>
                            <?php endif; ?>
                        </td>
                        <td><?= $uActive ? format_until($uUntil) : '-' ?></td>
                        <td><?= date('Y-m-d H:i', (int)$u['created_at']) ?></td>
                        <td>
                            <div class="actions">
                                <form method="post">
                                    <input type="hidden" name="action" value="toggle_user_activation"/>
                                    <input type="hidden" name="username" value="<?= htmlspecialchars($u['username']) ?>"/>
                                    <input type="hidden" name="activated" value="<?= $uActive ? '0' : '1' ?>"/>
                                    <button class="btn <?= $uActive ? 'warn' : 'primary' ?>" type="submit"><?= $uActive ? '冻结' : '激活' ?></button>
                                </form>
                                <form method="post">
                                    <input type="hidden" name="action" value="reset_user_password"/>
                                    <input type="hidden" name="username" value="<?= htmlspecialchars($u['username']) ?>"/>
                                    <input name="password" placeholder="新密码" style="width:120px;"/>
                                    <button class="btn" type="submit">改密</button>
                                </form>
                                <form method="post">
                                    <input type="hidden" name="action" value="delete_user"/>
                                    <input type="hidden" name="username" value="<?= htmlspecialchars($u['username']) ?>"/>
                                    <button class="btn danger" type="submit">删除</button>
                                </form>
                            </div>
                        </td>
                    </tr>
                <?php endforeach; ?>
                </tbody>
            </table>
            </div>
        </div>
    </div>

    <div class="container">
        <div class="card" style="grid-column: 1 / -1;">
            <h3>卡密管理</h3>
            <form method="post" class="grid">
                <input type="hidden" name="action" value="create_cards"/>
                <div>
                    <label>分类</label>
                    <input name="category" placeholder="如：月卡/测试"/>
                </div>
                <div>
                    <label>前缀</label>
                    <input name="prefix" placeholder="CAM"/>
                </div>
                <div>
                    <label>数量</label>
                    <input name="count" value="1"/>
                </div>
                <div>
                    <label>有效期（秒，0=不过期）</label>
                    <input name="expires_in" value="0"/>
                </div>
                <div>
                    <label>激活时长（天，0=永久）</label>
                    <input name="duration_days" value="0"/>
                </div>
                <div>
                    <label>激活时长（小时，可选）</label>
                    <input name="duration_hours" value="0"/>
                </div>
                <div>
                    <label>激活时长（分钟，可选）</label>
                    <input name="duration_minutes" value="0"/>
                </div>
                <div>
                    <label>最大使用次数</label>
                    <input name="max_uses" value="1"/>
                </div>
                <div style="display:flex;align-items:flex-end;">
                    <button class="btn primary" type="submit">生成卡密</button>
                </div>
            </form>
            <form method="post" style="margin-top:14px;">
                <input type="hidden" name="action" value="bulk_create_cards"/>
                <label>批量生卡（每行一组，格式：分类,前缀,数量,有效期秒,天,小时,分钟,最大次数）</label>
                <textarea name="bulk_lines" rows="5" style="width:100%;margin-top:6px;" placeholder="测试,TEST,10,0,0,0,0,1&#10;月卡,MO,50,0,30,0,0,1"></textarea>
                <div style="display:flex;align-items:center;gap:8px;margin-top:8px;">
                    <button class="btn primary" type="submit">批量生成</button>
                    <span class="muted">未填的字段默认：数量=1、有效期=0、天=0、小时=0、分钟=0、次数=1</span>
                </div>
            </form>
            <div style="display:flex;flex-wrap:wrap;gap:10px;align-items:center;margin:16px 0 8px;">
                <form method="get" style="display:flex;gap:8px;align-items:center;">
                    <label>分类筛选</label>
                    <select name="card_category">
                        <option value="">全部</option>
                        <option value="__empty__" <?= $cardCategoryFilter === '__empty__' ? 'selected' : '' ?>>未分类</option>
                        <?php foreach ($cardCategories as $catRow): ?>
                            <?php $catName = (string)($catRow['category'] ?? ''); ?>
                            <?php if ($catName === '') { continue; } ?>
                            <option value="<?= htmlspecialchars($catName) ?>" <?= $cardCategoryFilter === $catName ? 'selected' : '' ?>><?= htmlspecialchars($catName) ?></option>
                        <?php endforeach; ?>
                    </select>
                    <button class="btn" type="submit">筛选</button>
                    <a class="btn" href="/admin.php">重置</a>
                </form>
                <div style="display:flex;gap:8px;align-items:center;">
                    <a class="btn" href="/admin.php?download_category=__all__">下载全部</a>
                    <?php if ($cardCategoryFilter !== ''): ?>
                        <a class="btn primary" href="/admin.php?download_category=<?= htmlspecialchars($cardCategoryFilter) ?>">下载当前分类</a>
                    <?php endif; ?>
                </div>
            </div>
            <div class="table-wrap">
            <table>
                <thead><tr><th>卡密</th><th>分类</th><th>使用</th><th>状态</th><th>激活时长</th><th>绑定</th><th>操作</th></tr></thead>
                <tbody>
                <?php foreach ($recentCards as $c): ?>
                    <tr>
                        <td class="code"><?= htmlspecialchars($c['code']) ?></td>
                        <td><?= htmlspecialchars($c['category'] ?? '') ?: '-' ?></td>
                        <td><?= (int)$c['used'] ?>/<?= (int)$c['max_uses'] ?></td>
                        <td>
                            <?php
                                $usedCount = (int)($c['used'] ?? 0);
                                $maxUses = (int)($c['max_uses'] ?? 1);
                            ?>
                            <?php if (!empty($c['revoked'])): ?>
                                <span class="badge danger">撤销</span>
                            <?php elseif ((int)$c['expires_at'] > 0 && (int)$c['expires_at'] < time()): ?>
                                <span class="badge warn">过期</span>
                            <?php elseif ($usedCount >= $maxUses): ?>
                                <span class="badge warn">已用尽</span>
                            <?php else: ?>
                                <span class="badge">可用</span>
                            <?php endif; ?>
                        </td>
                        <td><?= format_duration((int)($c['duration_seconds'] ?? 0)) ?></td>
                        <td><?= htmlspecialchars(($c['bound_user'] ?: '-')) ?></td>
                        <td>
                            <div class="actions">
                                <form method="post">
                                    <input type="hidden" name="action" value="revoke_card"/>
                                    <input type="hidden" name="code" value="<?= htmlspecialchars($c['code']) ?>"/>
                                    <button class="btn warn" type="submit">撤销</button>
                                </form>
                                <form method="post">
                                    <input type="hidden" name="action" value="delete_card"/>
                                    <input type="hidden" name="code" value="<?= htmlspecialchars($c['code']) ?>"/>
                                    <button class="btn danger" type="submit">删除</button>
                                </form>
                            </div>
                        </td>
                    </tr>
                <?php endforeach; ?>
                </tbody>
            </table>
            </div>
        </div>
    </div>
</body>
</html>
