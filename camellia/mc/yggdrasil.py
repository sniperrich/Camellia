from __future__ import annotations

import json
import os
import socket
import struct
import time
import zlib
from dataclasses import dataclass, field
from typing import List, Tuple

from ..crypto.chacha import ChaChaPacker
from ..crypto.http_crypto import aes_ecb_no_padding
from ..crypto.rsa_util import load_private_key, load_public_key, rsa_with_pkcs1
from ..crypto.skip32 import encrypt as skip32_encrypt


_CHACHA_NONCE = b"163 NetEase\n"
_PRC_CHECK = "[]"
_CLIENT_KEY_LENGTH = 19
_CHECKSUM_LENGTH = 32
_MC_VERSION_SALT = bytes([0x01, 0x00, 0x04, 0x80, 0xD2, 0x3E, 0xF7, 0x11, 0x01])
_TCP_SALT = bytes(
    [0x2F, 0x84, 0xAE, 0xA3, 0x99, 0x21, 0x29, 0x26, 0xDA, 0xBF, 0x95, 0xA3, 0xAB, 0xAF, 0x37, 0xE0]
)

_DEBUG_DUMP = os.getenv("NEL_YGG_DUMP") == "1"
_DEBUG_DUMP_PATH = os.getenv("NEL_YGG_DUMP_PATH", os.path.join("logs", "yggdrasil-compare.log"))

_PUBLIC_KEY = load_public_key(
    "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA4HJFrYdVTeoSvH6qsnJElfXuf7FnxxFQdz3gRCs66LDr"
    "ZfaoGoWt2e/aGIOv8uGHliBWnZP42Ike9Qf5aiYVtQ4mlj2bXZjifHG35LlS1Bq6yCA6k1WevWcrGWOuLzny3jo8"
    "Wbdi0lIFMTT2hN98sF2k4YcvyE9zhqxfRNFGVI5kLyxm9CeTKAXGBU5mw3yQWJ8cPRR4866jpGGOhBWlJdilWt2N"
    "ES9bid8SbhTT55wqumnVO5J5/DaMyTgKIQngH7NyZQljAhdK5I23dzpGop322n2eQ+mTNLuquwU453o1cbyQobgC"
    "6vh5/F1QT2INBR2qYCnRzzJ6hrhE5kIMZwIDAQAB"
)

_PRIVATE_KEY = load_private_key(
    "MIIEvAIBADANBgkqhkiG9w0BAQEFAASCBKYwggSiAgEAAoIBAQDobJGIddxcs08xzTIVFanc4/84J1DbxiW7wLokI"
    "rap3txzyyMXj+AcDa8jLopLJkMg9rZLzL50Dwp+hmTTgiIcjkM1DVREsRBltzqjVNyiYPL2VGHLn+/eEivjhLNWUM"
    "cWrlAoJ5JJBi22oGPLlKDVJpg33JPI5nPZpwufdB0ecn2V0CAeeGyswQyAaqIjoiYOoP3HipjEYGQHp1RsADf4ozG"
    "Rgv+2HGiSOyhlv00ixnF0nRUTzVh18ka0N3LdLMpAN/YAPkO8tmoWp0asyU3X+nyZFd29povvrRy4rL1lYmo8jdpf"
    "pSL+Yk+9+RybjBAhXRx6uDoBxaa2kwE4fyrrAgMBAAECggEAWT42szruHfoLkofDjyz+R/6TZLBT788pdeoOjwl1M"
    "cyMwTlihA2Oc7cdZFjeaPSMGgAhBwHarx2HXgWkeUIibuyBCcHQdX+3WBb+wPA4t3CaWdMUqecDZzV6/KVbZu0lRK"
    "QxyvlGxhtFOjZjmyu6hZ2IHQrpA97Y5N2rLNKcy69W+QYJZappmBfbVgWM0NRmgmpg6siQ0Cm6Ryil3SBAPBVv+EU"
    "PiD9jdXbtVq7VwN4YmwUGScp2Fib1oUnqEAja1hfihVnRFQ246nKXhIc/YVrNmwBrxAVwaaPFRka6XjKkSF0WVbpqQ"
    "LXhbY1fS8FoXGpVhiF6o4rTQJbpQxhQgQKBgQD7XysfPNt9G63gTrkZvjvEk5LKsRG42MAYEkuzxEal9E/AQv0jJr"
    "k0f1WO09hCcYQeaOQXhM9mezNQv5jXEnmepXqM3NTw1Di5yh3uvjSXQjdUt+7haNw+QjggBqZxyQZjtadYairSzfmW"
    "e7OwJIkCmdgaJKxm4qMExk9kUgApGwKBgQDstBdJHU/KEBqVpsIlu185vFFuaAvxiHjXHqGwytJMQ/5aVqaphIiQCA"
    "xaEogPSzPSm28UHVQiZeFO059EKOpSJscW4pV95Dr5BAbHuYecacqnZKbQqb69//Cfpne9tGYXlmP6QnYfPoc4wYfT"
    "fPyU2x3KtDhVxtEDutpSNU1ycQKBgBabWn92s66uvJZ9vfvotetZ8ku0XQmoxK3lh1Vlg40NSdbar3Vn2CQ2h3VO7B"
    "Ydq2oouMq8sQJgdh7+/DnreXChJUJh4ey+yVM8MDD2fjhURjGiUSOIkLYwsmd+8Z0uHRr+jUxQUAWhbJ7yBRkEUCYh"
    "u+OuBKtEGrElPKKjFUydAoGAQLj9pQBe0OGWY1U1wRt67k6P9aB9o42tfSTjEXRkDHaLFiibab7TmI6a0gY/Le9iPD"
    "REKzvZxY4WDXfQFNMbP1tbFObf+Yxuk6iGMhaI/jvvLdZXxrajcVCKex0JoNWzFMAKlmOV6PUwBFTmzu1eI1XGz6Z3"
    "wPycKmjtSY1JoAECgYBBOfaUDMaG1xLzv+q1jPPs2U4lXPK2BXFE5RaliUGC+LIQREXPishII2LYFW3gtXj5QWfIGq"
    "6x0d6ca6Bja2vYRDDe5tlT/2VbZahiHpb2PYL/2WgeoHl7sT9Bb/nsKyo85Sv+doop6huy4+aeTiQHgrGR9JYMVBSI"
    "x6P8Tt5phA=="
)


def _sha256(data: bytes) -> bytes:
    import hashlib

    return hashlib.sha256(data).digest()


def _dump_debug(message: str) -> None:
    if not _DEBUG_DUMP:
        return
    try:
        folder = os.path.dirname(_DEBUG_DUMP_PATH)
        if folder:
            os.makedirs(folder, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(_DEBUG_DUMP_PATH, "a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except OSError:
        return


def _xor_bytes(a: bytes, b: bytes) -> bytes:
    if len(a) != len(b):
        raise ValueError("xor requires equal length")
    return bytes(x ^ y for x, y in zip(a, b))


def _write_short(value: int, little_endian: bool = True) -> bytes:
    mask = value & 0xFFFF
    return struct.pack("<H" if little_endian else ">H", mask)


def _write_int(value: int, little_endian: bool = True) -> bytes:
    mask = value & 0xFFFFFFFF
    return struct.pack("<I" if little_endian else ">I", mask)


def _write_long(value: int, little_endian: bool = True) -> bytes:
    mask = value & 0xFFFFFFFFFFFFFFFF
    return struct.pack("<Q" if little_endian else ">Q", mask)


def _write_string(value: str) -> bytes:
    data = value.encode("utf-8")
    return bytes([len(data)]) + data


def _write_byte_length_string(value: str) -> bytes:
    return _write_string(value)


def _write_short_string(value: str, little_endian: bool = True) -> bytes:
    data = value.encode("utf-8")
    return _write_short(len(data), little_endian) + data


def _write_short_bytes(value: bytes, little_endian: bool = True) -> bytes:
    return _write_short(len(value), little_endian) + value


def _read_exact(sock: socket.socket, size: int) -> bytes:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = sock.recv(remaining)
        if not chunk:
            raise ConnectionError("socket closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _read_stream_with_int16(sock: socket.socket) -> bytes:
    length_bytes = _read_exact(sock, 2)
    length = struct.unpack("<h", length_bytes)[0]
    if length < 0:
        raise ValueError("invalid length")
    return _read_exact(sock, length)


@dataclass
class YggdrasilData:
    launcher_version: str
    channel: str
    crc_salt: str


@dataclass
class Mod:
    modPath: str
    name: str
    id: str
    iid: str
    md5: str
    version: str

    def to_dict(self) -> dict:
        return {
            "modPath": self.modPath,
            "name": self.name,
            "id": self.id,
            "iid": self.iid,
            "md5": self.md5,
            "version": self.version,
        }


@dataclass
class ModList:
    mods: List[Mod] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"mods": [mod.to_dict() for mod in self.mods]}


@dataclass
class UserProfile:
    user_id: int
    user_token: str
    use_skip32: bool = True
    use_xor: bool = True
    use_hex_token: bool = False

    _token_key = bytes(
        [0xAC, 0x24, 0x9C, 0x69, 0xC7, 0x2C, 0xB3, 0xB4, 0x4E, 0xC0, 0xCC, 0x6C, 0x54, 0x3A, 0x81, 0x95]
    )

    def get_auth_id(self) -> int:
        if not self.use_skip32:
            return self.user_id
        return skip32_encrypt(self.user_id, b"SaintSteve")

    def get_auth_token(self) -> bytes:
        token_bytes = self._decode_token()
        if self.use_xor:
            if len(token_bytes) != len(self._token_key):
                raise ValueError("user token length must be 16 bytes for Yggdrasil auth")
            return _xor_bytes(token_bytes, self._token_key)
        return token_bytes

    def _decode_token(self) -> bytes:
        token = self.user_token
        if self.use_hex_token or _looks_like_hex_32(token):
            try:
                return bytes.fromhex(token)
            except ValueError as exc:
                raise ValueError("invalid hex token for Yggdrasil auth") from exc
        return token.encode("ascii")


@dataclass
class GameProfile:
    game_id: str
    game_version: str
    bootstrap_md5: str
    dat_file_md5: str
    mods: ModList
    user: UserProfile

    def get_mod_info(self) -> str:
        # Match C# JsonSerializer output (no spaces) to keep Yggdrasil hash consistent.
        return json.dumps(self.mods.to_dict(), ensure_ascii=False, separators=(",", ":"))


class YggdrasilGenerator:
    def __init__(self, data: YggdrasilData) -> None:
        self.data = data

    def generate_join_message(self, profile: GameProfile, server_id: str, login_seed: bytes) -> bytes:
        current_time = int(time.time())
        hash_data = self._build_hash_data(profile, current_time, profile.user.user_id, login_seed)
        output = bytearray()
        output += _write_long(int(profile.game_id))
        output += _write_string(server_id)
        output += _write_string(self.data.launcher_version)
        output += _write_string(profile.game_version)
        output += _write_int(current_time)
        output += hash_data
        output += _write_short_string(profile.get_mod_info())
        output += _write_short_string(_PRC_CHECK)
        output += _write_short(0)
        output += _write_byte_length_string(self.data.channel)
        return bytes(output)

    def generate_initialize_message(self, profile: GameProfile, login_seed: bytes, sign_content: bytes) -> bytes:
        auth_id = profile.user.get_auth_id()
        token = profile.user.get_auth_token()
        seed = aes_ecb_no_padding(login_seed, token)
        sign = _sha256(self._build_sign(profile, auth_id, seed))

        client = rsa_with_pkcs1(_PUBLIC_KEY, sign_content, False)
        if len(client) < _CLIENT_KEY_LENGTH + _CHECKSUM_LENGTH:
            raise ValueError("invalid sign content length")

        client_key = client[:_CLIENT_KEY_LENGTH]
        checksum = client[_CLIENT_KEY_LENGTH : _CLIENT_KEY_LENGTH + _CHECKSUM_LENGTH]
        if checksum != _sha256(login_seed):
            raise ValueError("checksum mismatch")

        sign_data = rsa_with_pkcs1(_PRIVATE_KEY, client_key + sign, True)

        stream = bytearray()
        stream += _write_int(auth_id)
        stream += seed
        stream += _write_short_string(self.data.launcher_version, little_endian=False)
        stream += _write_byte_length_string(self.data.channel)
        stream += _TCP_SALT
        stream += _write_short_bytes(sign_data)
        stream += _write_byte_length_string(profile.game_version)
        stream += _MC_VERSION_SALT

        message = bytearray()
        message += _write_short(len(stream))
        message += stream
        return bytes(message)

    def _build_sign(self, profile: GameProfile, auth_id: int, seed: bytes) -> bytes:
        output = bytearray()
        output += _write_int(auth_id)
        output += seed
        output += self.data.launcher_version.encode("utf-8")
        output += self.data.channel.encode("utf-8")
        output += self.data.crc_salt.encode("utf-8")
        output += profile.game_version.encode("utf-8")
        output += _MC_VERSION_SALT
        return bytes(output)

    def _build_hash_data(self, profile: GameProfile, current_time: int, auth_id: int, login_seed: bytes) -> bytes:
        join_message = (
            f"{self.data.launcher_version}{profile.game_version}{current_time}{self.data.crc_salt}"
            f"{profile.get_mod_info()}{profile.bootstrap_md5}{profile.dat_file_md5}{_PRC_CHECK}"
        )
        data = join_message.encode("utf-8") + struct.pack("<i", auth_id) + login_seed
        return _sha256(data)


class StandardYggdrasil:
    def __init__(self, data: YggdrasilData, address: str, port: int) -> None:
        self.data = data
        self.address = address
        self.port = port
        self.generator = YggdrasilGenerator(data)

    @staticmethod
    def auth_servers() -> List[Tuple[str, int]]:
        import json as _json
        import urllib.request

        with urllib.request.urlopen("https://x19.update.netease.com/authserver.list", timeout=5) as resp:
            payload = _json.loads(resp.read().decode("utf-8"))
        if not payload:
            raise RuntimeError("no auth servers available")
        return [(server["IP"], int(server["Port"])) for server in payload]

    @staticmethod
    def random_auth_server() -> Tuple[str, int]:
        import random

        servers = StandardYggdrasil.auth_servers()
        return random.choice(servers)

    @classmethod
    def with_random_server(cls, data: YggdrasilData) -> "StandardYggdrasil":
        address, port = cls.random_auth_server()
        return cls(data, address, port)

    def join_server(
        self,
        profile: GameProfile,
        server_id: str,
        login_only: bool = False,
        *,
        counter_start: int = 0,
        crc_little_endian: bool = False,
    ) -> Tuple[bool, str]:
        try:
            with socket.create_connection((self.address, self.port), timeout=10) as sock:
                try:
                    login_seed = self._initialize_connection(sock, profile)
                except Exception as exc:  # pylint: disable=broad-except
                    return False, f"init: {exc}"
                if login_only:
                    return True, ""
                try:
                    return self._make_request(
                        sock,
                        profile,
                        server_id,
                        login_seed,
                        counter_start=counter_start,
                        crc_little_endian=crc_little_endian,
                    )
                except Exception as exc:  # pylint: disable=broad-except
                    return False, f"join: {exc}"
        except Exception as exc:  # pylint: disable=broad-except
            return False, f"connect: {exc}"

    def _initialize_connection(self, sock: socket.socket, profile: GameProfile) -> bytes:
        try:
            received = _read_stream_with_int16(sock)
        except Exception as e:
            raise RuntimeError(f"read initial response failed: {e}")
        if len(received) < 272:
            raise RuntimeError(f"invalid response length: {len(received)} < 272")
        login_seed = received[:16]
        sign_content = received[16:16 + 256]
        message = self.generator.generate_initialize_message(profile, login_seed, sign_content)
        try:
            sock.sendall(message)
        except Exception as e:
            raise RuntimeError(f"send init message failed: {e}")
        try:
            response = _read_stream_with_int16(sock)
        except Exception as e:
            raise RuntimeError(f"read init response failed: {e}")
        if not response:
            raise RuntimeError("empty response")
        status = response[0]
        if status != 0x00:
            raise RuntimeError(f"init failed: 0x{status:02X}")
        return login_seed

    def _make_request(
        self,
        sock: socket.socket,
        profile: GameProfile,
        server_id: str,
        login_seed: bytes,
        *,
        counter_start: int,
        crc_little_endian: bool,
    ) -> Tuple[bool, str]:
        token = profile.user.get_auth_token()
        auth_id = profile.user.get_auth_id()
        if _DEBUG_DUMP:
            _dump_debug(
                "auth_id=%s token_hex=%s server_id=%s login_seed=%s counter_start=%s crc_le=%s"
                % (
                    auth_id,
                    token.hex(),
                    server_id,
                    login_seed.hex(),
                    counter_start,
                    crc_little_endian,
                )
            )
        packer = ChaChaPacker(token + login_seed, _CHACHA_NONCE, rounds=8, counter_start=counter_start)
        unpacker = ChaChaPacker(login_seed + token, _CHACHA_NONCE, rounds=8, counter_start=counter_start)
        join_payload = self.generator.generate_join_message(profile, server_id, login_seed)
        if _DEBUG_DUMP:
            _dump_debug(
                "join_len=%s join_sha256=%s join_hex=%s"
                % (
                    len(join_payload),
                    _sha256(join_payload).hex(),
                    join_payload.hex(),
                )
            )
        message = _pack_message(
            packer,
            9,
            join_payload,
            crc_little_endian=crc_little_endian,
        )
        if _DEBUG_DUMP:
            _dump_debug(
                "pack_len=%s pack_sha256=%s pack_hex=%s"
                % (
                    len(message),
                    _sha256(message).hex(),
                    message.hex(),
                )
            )
        try:
            sock.sendall(message)
        except Exception as e:
            return False, f"send: {e}"
        try:
            response = _read_stream_with_int16(sock)
        except Exception as e:
            return False, f"recv: {e}"
        try:
            packet_type, payload = _unpack_message(unpacker, response, crc_little_endian=crc_little_endian)
        except Exception as e:
            return False, f"unpack: {e}"
        if packet_type != 9 or not payload or payload[0] != 0x00:
            err = payload[0] if payload else 0xFF
            return False, f"{err:02X}"
        return True, ""


def _pack_message(packer: ChaChaPacker, packet_type: int, data: bytes, *, crc_little_endian: bool) -> bytes:
    message = bytearray(len(data) + 10)
    length = len(message) - 2
    message[0:2] = struct.pack("<h", length)
    message[6] = packet_type
    message[7] = 0x88
    message[8] = 0x88
    message[9] = 0x88
    message[10:] = data
    crc = zlib.crc32(message[6:]) & 0xFFFFFFFF
    message[2:6] = crc.to_bytes(4, "little" if crc_little_endian else "big")
    packer.process_bytes(message, 2, len(message) - 2)
    return bytes(message)


def _unpack_message(packer: ChaChaPacker, data: bytes, *, crc_little_endian: bool) -> Tuple[int, bytes]:
    message = bytearray(data)
    packer.process_bytes(message, 0, len(message))
    crc = zlib.crc32(message[4:]) & 0xFFFFFFFF
    if crc.to_bytes(4, "little" if crc_little_endian else "big") != bytes(message[0:4]):
        raise RuntimeError("unpack crc mismatch")
    packet_type = message[4]
    payload = bytes(message[8:])
    return packet_type, payload


def _looks_like_hex_32(value: str) -> bool:
    if len(value) != 32:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in value)
