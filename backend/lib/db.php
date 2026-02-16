<?php
declare(strict_types=1);

function db(): PDO
{
    static $pdo = null;
    if ($pdo instanceof PDO) {
        return $pdo;
    }
    $host = getenv('DB_HOST') ?: 'mysql';
    $name = getenv('DB_NAME') ?: 'Camellia';
    $user = getenv('DB_USER') ?: 'Camellia';
    $pass = getenv('DB_PASS') ?: 'Camellia1337';
    $dsn = "mysql:host={$host};dbname={$name};charset=utf8mb4";
    $pdo = new PDO($dsn, $user, $pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    return $pdo;
}

function ensure_schema(): void
{
    $pdo = db();
    $pdo->exec("CREATE TABLE IF NOT EXISTS config (
        config_key VARCHAR(64) PRIMARY KEY,
        config_value TEXT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    $pdo->exec("CREATE TABLE IF NOT EXISTS users (
        username VARCHAR(64) PRIMARY KEY,
        password_hash TEXT NOT NULL,
        role VARCHAR(16) NOT NULL DEFAULT 'user',
        created_at INT NOT NULL,
        activated TINYINT(1) NOT NULL DEFAULT 0,
        activated_until INT NOT NULL DEFAULT 0,
        failed_attempts INT NOT NULL DEFAULT 0,
        lock_until INT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    $pdo->exec("CREATE TABLE IF NOT EXISTS user_devices (
        id INT AUTO_INCREMENT PRIMARY KEY,
        username VARCHAR(64) NOT NULL,
        device_id VARCHAR(128) NOT NULL,
        added_at INT NOT NULL,
        last_ip VARCHAR(64) NOT NULL,
        last_seen INT NOT NULL,
        UNIQUE KEY user_device_unique (username, device_id)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    $pdo->exec("CREATE TABLE IF NOT EXISTS cards (
        code VARCHAR(64) PRIMARY KEY,
        category VARCHAR(64) NOT NULL DEFAULT '',
        created_at INT NOT NULL,
        expires_at INT NOT NULL DEFAULT 0,
        duration_seconds INT NOT NULL DEFAULT 0,
        max_uses INT NOT NULL DEFAULT 1,
        used INT NOT NULL DEFAULT 0,
        bound_user VARCHAR(64) NOT NULL DEFAULT '',
        bound_device VARCHAR(128) NOT NULL DEFAULT '',
        revoked TINYINT(1) NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    $pdo->exec("CREATE TABLE IF NOT EXISTS refresh_tokens (
        token_hash VARCHAR(128) PRIMARY KEY,
        username VARCHAR(64) NOT NULL,
        device_id VARCHAR(128) NOT NULL,
        ip VARCHAR(64) NOT NULL,
        ua TEXT NOT NULL,
        iat INT NOT NULL,
        exp INT NOT NULL,
        revoked TINYINT(1) NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    $pdo->exec("CREATE TABLE IF NOT EXISTS rate_limits (
        rate_key VARCHAR(128) PRIMARY KEY,
        data TEXT NOT NULL,
        updated_at INT NOT NULL
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    $pdo->exec("CREATE TABLE IF NOT EXISTS metrics (
        date_key CHAR(10) PRIMARY KEY,
        total INT NOT NULL DEFAULT 0,
        paths TEXT NOT NULL,
        status TEXT NOT NULL,
        methods TEXT NOT NULL,
        last_seen INT NOT NULL DEFAULT 0
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

    try {
        $pdo->exec("ALTER TABLE users ADD COLUMN activated_until INT NOT NULL DEFAULT 0");
    } catch (Throwable $ignored) {
    }
    try {
        $pdo->exec("ALTER TABLE cards ADD COLUMN duration_seconds INT NOT NULL DEFAULT 0");
    } catch (Throwable $ignored) {
    }
    try {
        $pdo->exec("ALTER TABLE cards ADD COLUMN category VARCHAR(64) NOT NULL DEFAULT ''");
    } catch (Throwable $ignored) {
    }
}
