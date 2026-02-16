<?php
declare(strict_types=1);

// Temporary helper page: add IP(s) to admin whitelist.
// Delete this file after use.

require_once __DIR__ . '/lib/db.php';

header('Content-Type: text/html; charset=utf-8');

// Set a one-time secret before uploading/running.
$TEMP_SECRET = 'CHANGE_ME';

ensure_schema();
$pdo = db();

function get_config_value(PDO $pdo, string $key, string $default = ''): string
{
    $stmt = $pdo->prepare('SELECT config_value FROM config WHERE config_key = :k');
    $stmt->execute(['k' => $key]);
    $row = $stmt->fetch();
    if (!$row) {
        return $default;
    }
    return (string)($row['config_value'] ?? $default);
}

function set_config_value(PDO $pdo, string $key, string $value): void
{
    $stmt = $pdo->prepare('REPLACE INTO config (config_key, config_value) VALUES (:k, :v)');
    $stmt->execute(['k' => $key, 'v' => $value]);
}

function parse_ip_list(string $raw): array
{
    $parts = preg_split('/[\s,]+/', $raw, -1, PREG_SPLIT_NO_EMPTY);
    $clean = [];
    foreach ($parts as $part) {
        $ip = trim((string)$part);
        if ($ip === '') {
            continue;
        }
        $clean[] = $ip;
    }
    return array_values(array_unique($clean));
}

function is_valid_ip(string $ip): bool
{
    return filter_var($ip, FILTER_VALIDATE_IP) !== false;
}

function html(string $value): string
{
    return htmlspecialchars($value, ENT_QUOTES | ENT_SUBSTITUTE, 'UTF-8');
}

$candidateIps = [];
$candidateIps['CF_CONNECTING_IP'] = (string)($_SERVER['HTTP_CF_CONNECTING_IP'] ?? '');
$candidateIps['X_FORWARDED_FOR'] = (string)($_SERVER['HTTP_X_FORWARDED_FOR'] ?? '');
$candidateIps['X_REAL_IP'] = (string)($_SERVER['HTTP_X_REAL_IP'] ?? '');
$candidateIps['CLIENT_IP'] = (string)($_SERVER['HTTP_CLIENT_IP'] ?? '');
$candidateIps['REMOTE_ADDR'] = (string)($_SERVER['REMOTE_ADDR'] ?? '');

$expandedIps = [];
foreach ($candidateIps as $value) {
    if ($value === '') {
        continue;
    }
    $parts = array_map('trim', explode(',', $value));
    foreach ($parts as $part) {
        if ($part !== '') {
            $expandedIps[] = $part;
        }
    }
}
$expandedIps = array_values(array_unique($expandedIps));

$errors = [];
$okMessage = '';

if ($TEMP_SECRET === 'CHANGE_ME') {
    $errors[] = 'Please edit tmp_whitelist_ip.php and set $TEMP_SECRET before use.';
}

$current = get_config_value($pdo, 'admin_allow_ip', '');
$list = parse_ip_list($current);

$ipInput = '';
if ($_SERVER['REQUEST_METHOD'] === 'POST') {
    $provided = (string)($_POST['secret'] ?? '');
    if ($TEMP_SECRET === 'CHANGE_ME' || !hash_equals($TEMP_SECRET, $provided)) {
        $errors[] = 'Invalid secret.';
    } else {
        $ipInput = trim((string)($_POST['ip'] ?? ''));
        $mode = (string)($_POST['mode'] ?? 'append');

        $toAdd = preg_split('/[\s,]+/', $ipInput, -1, PREG_SPLIT_NO_EMPTY);
        $valid = [];
        foreach ($toAdd as $ip) {
            $ip = trim((string)$ip);
            if ($ip === '') {
                continue;
            }
            if (!is_valid_ip($ip)) {
                $errors[] = "Invalid IP: {$ip}";
                continue;
            }
            $valid[] = $ip;
        }

        if (!$errors) {
            if ($mode === 'replace') {
                $list = array_values(array_unique($valid));
            } else {
                $list = array_values(array_unique(array_merge($list, $valid)));
            }
            $newValue = implode(', ', $list);
            set_config_value($pdo, 'admin_allow_ip', $newValue);
            $current = $newValue;
            $okMessage = 'Whitelist updated.';
        }
    }
}

?>
<!doctype html>
<html lang="zh-cn">
<head>
    <meta charset="utf-8"/>
    <meta name="viewport" content="width=device-width, initial-scale=1"/>
    <title>Camellia Temp IP Whitelist</title>
    <style>
        body { font-family: -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"PingFang SC","Microsoft YaHei",sans-serif; background: #f6f7fb; color: #1c1b1f; }
        .wrap { max-width: 820px; margin: 28px auto; padding: 0 16px; }
        .card { background: #fff; border-radius: 16px; box-shadow: 0 10px 30px rgba(17,24,39,0.08); padding: 18px 18px; }
        h1 { margin: 0 0 6px 0; font-size: 18px; }
        .muted { color: #6b6f76; font-size: 12px; }
        .row { display: flex; gap: 12px; flex-wrap: wrap; margin-top: 12px; }
        .col { flex: 1; min-width: 240px; }
        label { display:block; font-size: 12px; color:#43474e; margin-bottom: 6px; }
        input, select { width: 100%; box-sizing: border-box; padding: 10px 12px; border-radius: 12px; border: 1px solid #dde0e6; outline: none; font-size: 13px; }
        input:focus, select:focus { border-color: #6750A4; box-shadow: 0 0 0 3px rgba(103,80,164,0.18); }
        button { border: 0; padding: 10px 14px; border-radius: 12px; background: #6750A4; color: #fff; font-weight: 600; cursor: pointer; }
        button.secondary { background: #e8def8; color: #1c1b1f; }
        pre { background: #f5f6f7; border-radius: 12px; padding: 10px 12px; overflow: auto; font-size: 12px; }
        .alert { border-radius: 12px; padding: 10px 12px; margin-top: 12px; }
        .ok { background: #e6f4ea; color: #1e4620; }
        .err { background: #fde7e9; color: #7a1b1d; }
        .pill { display:inline-block; background:#f5f6f7; border-radius:999px; padding: 3px 8px; font-size: 12px; margin: 2px 6px 2px 0; }
    </style>
</head>
<body>
<div class="wrap">
    <div class="card">
        <h1>临时 IP 白名单工具</h1>
        <div class="muted">用于更新 <code>config.admin_allow_ip</code>（逗号分隔）。用完请删除本文件。</div>

        <?php if ($okMessage): ?>
            <div class="alert ok"><?= html($okMessage) ?></div>
        <?php endif; ?>
        <?php if ($errors): ?>
            <div class="alert err">
                <div><strong>错误：</strong></div>
                <ul>
                    <?php foreach ($errors as $e): ?>
                        <li><?= html((string)$e) ?></li>
                    <?php endforeach; ?>
                </ul>
            </div>
        <?php endif; ?>

        <div class="row">
            <div class="col">
                <div class="muted">检测到的 IP（供参考）</div>
                <div style="margin-top:8px;">
                    <?php foreach ($expandedIps as $ip): ?>
                        <span class="pill" data-ip="<?= html($ip) ?>"><?= html($ip) ?></span>
                    <?php endforeach; ?>
                    <?php if (!$expandedIps): ?>
                        <span class="muted">(none)</span>
                    <?php endif; ?>
                </div>
                <div style="margin-top:10px;" class="muted">原始头信息</div>
                <pre><?php
foreach ($candidateIps as $k => $v) {
    echo $k . ': ' . $v . "\n";
}
?></pre>
            </div>
            <div class="col">
                <form method="post">
                    <label>Secret</label>
                    <input name="secret" placeholder="Set $TEMP_SECRET first" value="" />

                    <div style="height:10px;"></div>
                    <label>IP（可输入多个，逗号/空格分隔）</label>
                    <input id="ip" name="ip" placeholder="104.245.12.20" value="<?= html($ipInput) ?>" />

                    <div style="height:10px;"></div>
                    <label>模式</label>
                    <select name="mode">
                        <option value="append">追加（append）</option>
                        <option value="replace">覆盖（replace）</option>
                    </select>

                    <div class="row" style="margin-top:12px;">
                        <button type="submit">更新白名单</button>
                        <button class="secondary" type="button" id="useDetected">使用检测到的第一个 IP</button>
                    </div>
                </form>
            </div>
        </div>

        <div style="margin-top:14px;" class="muted">当前 admin_allow_ip</div>
        <pre><?= html($current) ?></pre>
    </div>
</div>

<script>
    (function () {
        var btn = document.getElementById('useDetected');
        var ipInput = document.getElementById('ip');
        if (!btn || !ipInput) return;

        btn.addEventListener('click', function () {
            var first = document.querySelector('.pill[data-ip]');
            if (!first) return;
            ipInput.value = first.getAttribute('data-ip') || '';
        });
    })();
</script>
</body>
</html>

