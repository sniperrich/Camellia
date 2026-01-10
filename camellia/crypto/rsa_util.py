import base64
import os
from dataclasses import dataclass
from typing import Optional

try:
    from Crypto.PublicKey import RSA
except ImportError as exc:  # pragma: no cover - depends on local env
    raise ImportError("pycryptodome is required (pip install pycryptodome)") from exc


@dataclass
class RsaKey:
    n: int
    e: int
    d: Optional[int] = None

    @property
    def is_private(self) -> bool:
        return self.d is not None


def load_public_key(base64_key: str) -> RsaKey:
    key_bytes = base64.b64decode(base64_key)
    key = RSA.import_key(key_bytes)
    return RsaKey(n=key.n, e=key.e, d=None)


def load_private_key(base64_key: str) -> RsaKey:
    key_bytes = base64.b64decode(base64_key)
    key = RSA.import_key(key_bytes)
    return RsaKey(n=key.n, e=key.e, d=key.d)


def _pkcs1_pad(data: bytes, block_size: int, for_private: bool) -> bytes:
    if len(data) > block_size - 11:
        raise ValueError("data too long for RSA block")
    pad_len = block_size - len(data) - 3
    if for_private:
        padding = b"\xFF" * pad_len
        return b"\x00\x01" + padding + b"\x00" + data
    padding = bytearray()
    while len(padding) < pad_len:
        b = os.urandom(1)
        if b != b"\x00":
            padding.extend(b)
    return b"\x00\x02" + bytes(padding) + b"\x00" + data


def _pkcs1_unpad(block: bytes) -> bytes:
    if len(block) < 11 or block[0] != 0x00:
        raise ValueError("invalid RSA block")
    if block[1] not in (0x01, 0x02):
        raise ValueError("invalid RSA padding type")
    index = block.find(b"\x00", 2)
    if index < 0:
        raise ValueError("invalid RSA padding")
    return block[index + 1 :]


def rsa_with_pkcs1(key: RsaKey, data: bytes, for_encryption: bool) -> bytes:
    block_size = (key.n.bit_length() + 7) // 8
    if for_encryption:
        exponent = key.d if key.is_private else key.e
        if exponent is None:
            raise ValueError("private exponent missing")
        padded = _pkcs1_pad(data, block_size, key.is_private)
        m = int.from_bytes(padded, "big")
        c = pow(m, exponent, key.n)
        return c.to_bytes(block_size, "big")
    exponent = key.d if key.is_private else key.e
    c = int.from_bytes(data, "big")
    m = pow(c, exponent, key.n)
    return _pkcs1_unpad(m.to_bytes(block_size, "big"))
