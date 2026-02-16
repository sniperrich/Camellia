<?php
declare(strict_types=1);

require_once __DIR__ . '/lib/common.php';
require_once __DIR__ . '/lib/db.php';

header('Content-Type: text/html; charset=utf-8');

ensure_schema();
$pdo = db();

$input = array_merge($_GET, $_POST);

function set_config_value(PDO $pdo, string $key, string $value): void
{
    $stmt = $pdo->prepare("REPLACE INTO config (config_key, config_value) VALUES (:k, :v)");
    $stmt->execute(['k' => $key, 'v' => $value]);
}

function get_config_map(PDO $pdo): array
{
    $rows = $pdo->query("SELECT config_key, config_value FROM config")->fetchAll();
    $map = [];
    foreach ($rows as $row) {
        $map[$row['config_key']] = $row['config_value'];
    }
    return $map;
}

function mask_value(string $value): string
{
    if ($value === '') {
        return '';
    }
    $len = strlen($value);
    if ($len <= 6) {
        return str_repeat('*', $len);
    }
    return substr($value, 0, 3) . str_repeat('*', $len - 6) . substr($value, -3);
}

$updated = [];
$config = get_config_map($pdo);

if (!empty($input['reset_jwt'])) {
    $newSecret = bin2hex(random_bytes(32));
    set_config_value($pdo, 'jwt_secret', $newSecret);
    $updated['jwt_secret'] = 'reset';
}

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
] as $key) {
    if (isset($input[$key])) {
        set_config_value($pdo, $key, (string)$input[$key]);
        $updated[$key] = 'updated';
    }
}

if (isset($input['admin_user'])) {
    $adminUser = strtolower(trim((string)$input['admin_user']));
    if ($adminUser !== '') {
        set_config_value($pdo, 'admin_user', $adminUser);
        $updated['admin_user'] = 'updated';
    }
}

if (isset($input['admin_pass'])) {
    $pass = (string)$input['admin_pass'];
    if ($pass !== '') {
        $hash = password_hash($pass, PASSWORD_BCRYPT);
        set_config_value($pdo, 'admin_pass_hash', $hash);
        $updated['admin_pass_hash'] = 'updated';

        $adminUser = $input['admin_user'] ?? ($config['admin_user'] ?? 'admin');
        $adminUser = strtolower(trim((string)$adminUser));
        if ($adminUser === '') {
            $adminUser = 'admin';
        }
        $stmt = $pdo->prepare(
            "INSERT INTO users (username, password_hash, role, created_at, activated, failed_attempts, lock_until)
             VALUES (:u, :h, 'admin', :created, 1, 0, 0)
             ON DUPLICATE KEY UPDATE password_hash = VALUES(password_hash), role = 'admin', activated = 1"
        );
        $stmt->execute([
            'u' => $adminUser,
            'h' => $hash,
            'created' => time(),
        ]);
    }
}

$config = get_config_map($pdo);

?>
<!doctype html>
<html lang="zh-cn">
<head>
    <meta charset="utf-8"/>
    <title>Camellia Init</title>
    <style>
        body { font-family: "Manrope","Noto Sans","PingFang SC","Microsoft YaHei",sans-serif; background: #f3f5f4; color: #1c1b1f; }
        .wrap { max-width: 920px; margin: 30px auto; background: #fff; padding: 24px; border-radius: 16px; box-shadow: 0 10px 30px rgba(17,24,39,0.08); }
        h1 { margin-top: 0; }
        code { background: #f5f6f7; padding: 2px 6px; border-radius: 6px; }
        table { width: 100%; border-collapse: collapse; margin-top: 12px; }
        th, td { padding: 8px 10px; border-bottom: 1px solid #eee; text-align: left; font-size: 13px; }
        .muted { color: #6b6f76; font-size: 12px; }
        .tag { display: inline-block; padding: 2px 8px; border-radius: 8px; background: #e3f2f0; color: #2f8b7c; font-size: 12px; }
        .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; margin-top: 12px; }
        label { font-size: 12px; color: #5a5f66; }
        input { width: 100%; padding: 9px 10px; border-radius: 10px; border: 1px solid #d7e0dd; background: #f8faf9; font-size: 13px; }
        .btn { background: #2f8b7c; color: #fff; border: none; padding: 10px 14px; border-radius: 10px; font-weight: 600; cursor: pointer; }
        .row { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; }
        .section { margin-top: 18px; }
    </style>
</head>
<body>
<div class="wrap">
    <h1>Camellia Init</h1>
    <p class="muted">临时初始化页面（用完即可删除）。</p>

    <?php if (!empty($updated)) : ?>
        <p><span class="tag">已更新配置</span></p>
    <?php else : ?>
        <p class="muted">未提交更新参数。</p>
    <?php endif; ?>

    <div class="section">
        <h3>初始化配置</h3>
        <form method="post">
            <div class="grid">
                <div>
                    <label>管理员账号</label>
                    <input name="admin_user" value="<?= htmlspecialchars($config['admin_user'] ?? 'admin') ?>"/>
                </div>
                <div>
                    <label>管理员密码（明文）</label>
                    <input name="admin_pass" placeholder="留空不修改" />
                </div>
                <div>
                    <label>后台允许 IP（逗号分隔）</label>
                    <input name="admin_allow_ip" value="<?= htmlspecialchars($config['admin_allow_ip'] ?? '') ?>"/>
                </div>
                <div>
                    <label>CRC Auth Token</label>
                    <input name="crc_auth_token" placeholder="填你的 CRC token" />
                </div>
                <div>
                    <label>Access TTL（秒）</label>
                    <input name="access_ttl" value="<?= htmlspecialchars($config['access_ttl'] ?? '3600') ?>"/>
                </div>
                <div>
                    <label>Refresh TTL（秒）</label>
                    <input name="refresh_ttl" value="<?= htmlspecialchars($config['refresh_ttl'] ?? '1209600') ?>"/>
                </div>
                <div>
                    <label>最大设备数</label>
                    <input name="max_devices" value="<?= htmlspecialchars($config['max_devices'] ?? '3') ?>"/>
                </div>
                <div>
                    <label>登录锁定次数</label>
                    <input name="lock_attempts" value="<?= htmlspecialchars($config['lock_attempts'] ?? '5') ?>"/>
                </div>
                <div>
                    <label>锁定秒数</label>
                    <input name="lock_seconds" value="<?= htmlspecialchars($config['lock_seconds'] ?? '600') ?>"/>
                </div>
                <div>
                    <label>允许注册（1/0）</label>
                    <input name="enable_register" value="<?= htmlspecialchars($config['enable_register'] ?? '1') ?>"/>
                </div>
                <div>
                    <label>允许激活（1/0）</label>
                    <input name="enable_activation" value="<?= htmlspecialchars($config['enable_activation'] ?? '1') ?>"/>
                </div>
                <div>
                    <label>需要激活（1/0）</label>
                    <input name="require_activation" value="<?= htmlspecialchars($config['require_activation'] ?? '1') ?>"/>
                </div>
                <div>
                    <label>允许跨域</label>
                    <input name="allowed_origins" value="<?= htmlspecialchars($config['allowed_origins'] ?? '*') ?>"/>
                </div>
            </div>
            <div class="row section">
                <button class="btn" type="submit">保存配置</button>
                <label>
                    <input type="checkbox" name="reset_jwt" value="1"/>
                    重置 JWT 密钥
                </label>
            </div>
        </form>
    </div>

    <div class="section">
        <h3>常用测试链接</h3>
        <ul>
            <li><a href="/auth/health">/auth/health</a></li>
            <li><a href="/admin.php">/admin.php</a></li>
            <li><a href="/auth/crc_salt">/auth/crc_salt</a></li>
        </ul>
    </div>

    <h3>配置项（已脱敏）</h3>
    <table>
        <thead>
        <tr><th>Key</th><th>Value</th></tr>
        </thead>
        <tbody>
        <?php foreach ($config as $key => $value) :
            $safe = $value;
            if (in_array($key, ['jwt_secret','admin_pass_hash','admin_token','crc_auth_token'], true)) {
                $safe = mask_value((string)$value);
            }
            ?>
            <tr>
                <td><?= htmlspecialchars($key) ?></td>
                <td><?= htmlspecialchars((string)$safe) ?></td>
            </tr>
        <?php endforeach; ?>
        </tbody>
    </table>

    <h3>快速初始化示例</h3>
    <p class="muted">通过参数设置（示例）：</p>
    <code>?admin_user=admin&amp;admin_pass=admin123&amp;admin_allow_ip=104.245.12.20&amp;crc_auth_token=YOUR_TOKEN</code>
</div>
</body>
</html>
