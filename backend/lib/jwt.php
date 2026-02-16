<?php
declare(strict_types=1);

function b64url_encode(string $data): string
{
    return rtrim(strtr(base64_encode($data), '+/', '-_'), '=');
}

function b64url_decode(string $data): string
{
    $pad = strlen($data) % 4;
    if ($pad) {
        $data .= str_repeat('=', 4 - $pad);
    }
    return base64_decode(strtr($data, '-_', '+/'));
}

function jwt_sign(array $claims, string $secret): string
{
    $header = ['alg' => 'HS256', 'typ' => 'JWT'];
    $segments = [
        b64url_encode(json_encode($header, JSON_UNESCAPED_UNICODE)),
        b64url_encode(json_encode($claims, JSON_UNESCAPED_UNICODE)),
    ];
    $signature = hash_hmac('sha256', implode('.', $segments), $secret, true);
    $segments[] = b64url_encode($signature);
    return implode('.', $segments);
}

function jwt_verify(string $token, string $secret): array
{
    $parts = explode('.', $token);
    if (count($parts) !== 3) {
        return [false, null, 'invalid_token'];
    }
    [$h64, $p64, $s64] = $parts;
    $signed = $h64 . '.' . $p64;
    $expected = b64url_encode(hash_hmac('sha256', $signed, $secret, true));
    if (!hash_equals($expected, $s64)) {
        return [false, null, 'invalid_signature'];
    }
    $payload = json_decode(b64url_decode($p64), true);
    if (!is_array($payload)) {
        return [false, null, 'invalid_payload'];
    }
    $now = time();
    if (isset($payload['exp']) && $payload['exp'] < $now) {
        return [false, $payload, 'expired'];
    }
    return [true, $payload, 'ok'];
}
