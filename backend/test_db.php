<?php
declare(strict_types=1);

// Temporary DB test. Remove after use.
$host = 'mysql';
$name = 'Camellia';
$user = 'Camellia';
$pass = 'Camellia1337';

header('Content-Type: text/plain; charset=utf-8');

try {
    $dsn = "mysql:host={$host};dbname={$name};charset=utf8mb4";
    $pdo = new PDO($dsn, $user, $pass, [
        PDO::ATTR_ERRMODE => PDO::ERRMODE_EXCEPTION,
        PDO::ATTR_DEFAULT_FETCH_MODE => PDO::FETCH_ASSOC,
    ]);
    echo "OK: connected\n";
    $row = $pdo->query("SELECT VERSION() AS v")->fetch();
    echo "MySQL: " . ($row['v'] ?? 'unknown') . "\n";
    $tables = $pdo->query("SHOW TABLES")->fetchAll(PDO::FETCH_NUM);
    echo "Tables:\n";
    foreach ($tables as $table) {
        echo " - " . $table[0] . "\n";
    }
} catch (Throwable $e) {
    echo "ERROR: " . $e->getMessage() . "\n";
}
