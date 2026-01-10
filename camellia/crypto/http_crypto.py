import base64
import hashlib
import json
import random
from typing import Dict, Optional


try:
    from Crypto.Cipher import AES
except ImportError:  # pragma: no cover - depends on local env
    try:
        from Cryptodome.Cipher import AES  # type: ignore
    except ImportError as exc:  # pragma: no cover - depends on local env
        raise ImportError(
            "pycryptodome is required (pip install pycryptodome)"
        ) from exc


_HTTP_S_KEYS = (
    "MK6mipwmOUedplb6,OtEylfId6dyhrfdn,VNbhn5mvUaQaeOo9,bIEoQGQYjKd02U0J,"
    "fuaJrPwaH2cfXXLP,LEkdyiroouKQ4XN1,jM1h27H4UROu427W,DhReQada7gZybTDk,"
    "ZGXfpSTYUvcdKqdY,AZwKf7MWZrJpGR5W,amuvbcHw38TcSyPU,SI4QotspbjhyFdT0,"
    "VP4dhjKnDGlSJtbB,UXDZx4KhZywQ2tcn,NIK73ZNvNqzva4kd,WeiW7qU766Q1YQZI"
)

_HTTP_KEYS = [key.encode("ascii") for key in _HTTP_S_KEYS.split(",")]
_HTTP_IV = b"szkgpbyimxavqjcn"

_TOKEN_SALT = b"0eGsBkhl"


class CryptoError(RuntimeError):
    pass


def md5_hex(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def md5_hex_str(text: str) -> str:
    return md5_hex(text.encode("utf-8"))


def base64_encode(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def hex_to_binary(hex_string: str) -> str:
    return "".join(format(ord(ch), "08b") for ch in hex_string)


def aes_ecb_encrypt(data: bytes, key: bytes) -> bytes:
    pad_len = 16 - (len(data) % 16)
    padded = data + bytes([pad_len] * pad_len)
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(padded)


def aes_ecb_no_padding(data: bytes, key: bytes) -> bytes:
    if len(data) % 16 != 0:
        raise CryptoError("data length must be multiple of 16 for no-padding AES")
    cipher = AES.new(key, AES.MODE_ECB)
    return cipher.encrypt(data)


def http_encrypt(body_in: bytes) -> bytes:
    body_len = len(body_in)
    padded_len = ((body_len + 16 + 15) // 16) * 16
    body = bytearray(padded_len)
    body[:body_len] = body_in
    body[body_len:body_len + 16] = _HTTP_IV

    if len(_HTTP_KEYS) < 2:
        raise CryptoError("HTTP key set is too short")

    # Match .NET Random.Shared.Next(0, len(keys) - 1)
    key_index = random.randrange(0, len(_HTTP_KEYS) - 1)
    key_byte = ((key_index << 4) | 2) & 0xFF

    cipher = AES.new(_HTTP_KEYS[key_index], AES.MODE_CBC, iv=_HTTP_IV)
    encrypted = cipher.encrypt(bytes(body))

    result = bytearray(16 + len(encrypted) + 1)
    result[:16] = _HTTP_IV
    result[16:16 + len(encrypted)] = encrypted
    result[-1] = key_byte
    return bytes(result)


def http_decrypt(body: bytes) -> Optional[bytes]:
    if len(body) < 0x12:
        return None

    encrypted = body[16:-1]
    key_index = (body[-1] >> 4) & 0xF

    cipher = AES.new(_HTTP_KEYS[key_index], AES.MODE_CBC, iv=body[:16])
    decrypted = cipher.decrypt(encrypted)

    # Trim trailing padding + IV tail
    scissor = 0
    scissor_pos = len(decrypted) - 1
    while scissor < 16 and scissor_pos >= 0:
        if decrypted[scissor_pos] != 0x00:
            scissor += 1
        scissor_pos -= 1

    return decrypted[: scissor_pos + 1]


def compute_dynamic_token(request_path: str, send_body: str, user_id: str, user_token: str) -> Dict[str, str]:
    if not request_path.startswith("/"):
        request_path = "/" + request_path

    token_md5 = md5_hex_str(user_token).lower()
    payload = token_md5.encode("utf-8") + send_body.encode("utf-8") + _TOKEN_SALT + request_path.encode("utf-8")
    secret_md5 = md5_hex(payload).lower()

    secret_bin = hex_to_binary(secret_md5)
    secret_bin = secret_bin[6:] + secret_bin[:6]

    http_token = bytearray(secret_md5.encode("utf-8"))
    for i in range(0, len(secret_bin) // 8):
        block = secret_bin[i * 8 : i * 8 + 8]
        xor_value = 0
        for j, bit in enumerate(reversed(block)):
            if bit == "1":
                xor_value |= 1 << j
        http_token[i] ^= xor_value

    dynamic_token = base64.b64encode(bytes(http_token[:12])).decode("ascii") + "1"
    dynamic_token = dynamic_token.replace("+", "m").replace("/", "o")

    return {"user-id": user_id, "user-token": dynamic_token}


def load_cookie_json(raw_text: str) -> str:
    """Return the inner sauth_json if present, otherwise the raw text."""
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("empty cookie text")

    if raw_text.startswith("{"):
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            return raw_text

        if isinstance(data, dict) and "sauth_json" in data:
            return data["sauth_json"]

    return raw_text
