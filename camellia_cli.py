#!/usr/bin/env python3
"""
Camellia CLI (standalone single-file)

This file intentionally contains a minimal, self-contained subset of Camellia's
library code so it can run without importing any other repo modules.

External dependencies:
- pycryptodome (provides `Crypto.*` modules; `Cryptodome.*` fallback supported)
"""

from __future__ import annotations

import asyncio
import base64
import gzip
import hashlib
import json
import logging
import os
import random
import socket
import struct
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple


# -----------------------------------------------------------------------------
# Runtime env tweaks (pycryptodome + conda)
# -----------------------------------------------------------------------------

os.environ.setdefault("PYCRYPTODOME_DISABLE_GMP", "1")
os.environ.setdefault("CONDA_PREFIX", sys.prefix)


# -----------------------------------------------------------------------------
# Config (inlined from camellia/config.py)
# -----------------------------------------------------------------------------

X19_API_GATEWAY = "https://x19apigatewayobt.nie.netease.com"
X19_CORE = "https://x19obtcore.nie.netease.com:8443"
X19_MCL = "https://x19mclobt.nie.netease.com"
X19_PATCH_LIST_URL = "https://x19.update.netease.com/pl/x19_java_patchlist"
X19_AUTH_SERVER_LIST = "https://x19.update.netease.com/authserver.list"
MGBSDK_BASE = "https://mgbsdk.matrix.netease.com"

# Camellia's CRC salt service (primary) + legacy fallback.
FANTNEL_INFO_URL = "http://crcsalt.taylorswift.fit/"
FANTNEL_INFO_FALLBACK_URL = "http://110.42.70.32:13423/fantnel.json"

DEFAULT_API_USER_AGENT = "WPFLauncher/0.0.0.0"

# Locked configuration for this standalone CLI build.
SUPPORTED_MC_VERSION = "1.8.9"
FIXED_CRC_SALT = "421C4417360637BA22478129C9AFB8C5"


# -----------------------------------------------------------------------------
# HTTP client (urllib-based, with HTTPError body capture)
# -----------------------------------------------------------------------------


class HttpResponse:
    def __init__(self, status: int, headers: Dict[str, str], body: bytes, url: str):
        self.status = int(status)
        self.headers = headers
        self.body = body
        self.url = url

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())


class HttpClient:
    def __init__(
        self,
        base_url: str = "",
        default_headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        cookie_jar: Optional[CookieJar] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_headers = default_headers or {}
        self.timeout = int(timeout)
        self.cookie_jar = cookie_jar
        if cookie_jar is None:
            self._opener = urllib.request.build_opener()
        else:
            self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookie_jar))

    def _build_url(self, path: str, params: Optional[Dict[str, str]] = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self.base_url}/{path.lstrip('/')}" if self.base_url else path
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}{'&' if '?' in url else '?'}{query}"
        return url

    def _merge_headers(self, headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        merged = dict(self.default_headers)
        if headers:
            merged.update(headers)
        return merged

    @staticmethod
    def _read_response(resp: Any) -> HttpResponse:
        body = resp.read()
        encoding = getattr(resp, "headers", {}).get("Content-Encoding", "") if hasattr(resp, "headers") else ""
        if isinstance(encoding, str) and encoding.lower() == "gzip":
            body = gzip.decompress(body)
        headers = {k: v for k, v in getattr(resp, "headers", {}).items()}
        status = getattr(resp, "status", None) or getattr(resp, "code", 0) or 0
        url = resp.geturl() if hasattr(resp, "geturl") else ""
        return HttpResponse(int(status), headers, body, url)

    def get(
        self,
        path: str,
        params: Optional[Dict[str, str]] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> HttpResponse:
        url = self._build_url(path, params)
        request = urllib.request.Request(url, method="GET")
        for k, v in self._merge_headers(headers).items():
            request.add_header(k, v)
        try:
            resp = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            resp = exc
        return self._read_response(resp)

    def post(
        self,
        path: str,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        content_type: str = "application/json",
    ) -> HttpResponse:
        url = self._build_url(path)
        request = urllib.request.Request(url, data=data or b"", method="POST")
        merged = self._merge_headers(headers)
        if "Content-Type" not in merged and content_type:
            merged["Content-Type"] = content_type
        for k, v in merged.items():
            request.add_header(k, v)
        try:
            resp = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            resp = exc
        return self._read_response(resp)

    def post_json(self, path: str, payload: Any, headers: Optional[Dict[str, str]] = None) -> HttpResponse:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.post(path, data=data, headers=headers, content_type="application/json")

    def post_form(self, path: str, form: Dict[str, str], headers: Optional[Dict[str, str]] = None) -> HttpResponse:
        data = urllib.parse.urlencode(form).encode("utf-8")
        return self.post(path, data=data, headers=headers, content_type="application/x-www-form-urlencoded")


def load_cookie_jar() -> CookieJar:
    return CookieJar()


# -----------------------------------------------------------------------------
# HTTP crypto (inlined from camellia/crypto/http_crypto.py)
# -----------------------------------------------------------------------------

try:
    from Crypto.Cipher import AES as _AES
except ImportError:
    try:
        from Cryptodome.Cipher import AES as _AES  # type: ignore
    except ImportError as exc:
        raise ImportError("pycryptodome is required (pip install pycryptodome)") from exc


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


def aes_ecb_no_padding(data: bytes, key: bytes) -> bytes:
    if len(data) % 16 != 0:
        raise CryptoError("data length must be multiple of 16 for no-padding AES")
    cipher = _AES.new(key, _AES.MODE_ECB)
    return cipher.encrypt(data)


def http_encrypt(body_in: bytes) -> bytes:
    body_len = len(body_in)
    padded_len = ((body_len + 16 + 15) // 16) * 16
    body = bytearray(padded_len)
    body[:body_len] = body_in
    body[body_len : body_len + 16] = _HTTP_IV

    if len(_HTTP_KEYS) < 2:
        raise CryptoError("HTTP key set is too short")

    # Match .NET Random.Shared.Next(0, len(keys) - 1)
    key_index = random.randrange(0, len(_HTTP_KEYS) - 1)
    key_byte = ((key_index << 4) | 2) & 0xFF

    cipher = _AES.new(_HTTP_KEYS[key_index], _AES.MODE_CBC, iv=_HTTP_IV)
    encrypted = cipher.encrypt(bytes(body))

    result = bytearray(16 + len(encrypted) + 1)
    result[:16] = _HTTP_IV
    result[16 : 16 + len(encrypted)] = encrypted
    result[-1] = key_byte
    return bytes(result)


def http_decrypt(body: bytes) -> Optional[bytes]:
    if len(body) < 0x12:
        return None
    encrypted = body[16:-1]
    key_index = (body[-1] >> 4) & 0xF
    cipher = _AES.new(_HTTP_KEYS[key_index], _AES.MODE_CBC, iv=body[:16])
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
    raw_text = raw_text.strip()
    if not raw_text:
        raise ValueError("empty cookie text")
    if raw_text.startswith("{"):
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError:
            return raw_text
        if isinstance(data, dict) and "sauth_json" in data:
            return str(data["sauth_json"])
    return raw_text


# -----------------------------------------------------------------------------
# X19 patch list (inlined from camellia/api/x19.py)
# -----------------------------------------------------------------------------


def _parse_patchlist(text: str) -> Dict[str, object]:
    text = text.strip()
    if not text:
        raise RuntimeError("empty patch list")
    last_comma = text.rfind(",")
    trimmed = text[:last_comma] if last_comma != -1 else text
    json_text = "{" + trimmed + "}"
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        last_newline = trimmed.rfind("\n")
        if last_newline != -1:
            json_text = "{" + trimmed[last_newline + 1 :] + "}"
            return json.loads(json_text)
        raise


def get_latest_version() -> str:
    client = HttpClient()
    response = client.get(X19_PATCH_LIST_URL)
    versions = _parse_patchlist(response.text())
    if not versions:
        raise RuntimeError("patch list is empty")
    return list(versions.keys())[-1]


# -----------------------------------------------------------------------------
# MGB SDK (inlined from camellia/api/mgb_sdk.py)
# -----------------------------------------------------------------------------


class MgbSdk:
    def __init__(self, game_id: str) -> None:
        self.game_id = game_id
        self.client = HttpClient(base_url=MGBSDK_BASE)

    def generate_sauth(
        self,
        device_id: str,
        user_id: str,
        sdk_uid: str,
        session_id: str,
        timestamp: str,
        channel: str,
        platform: str = "pc",
    ) -> str:
        upper = session_id.upper()
        payload: Dict[str, Any] = {
            "app_channel": channel,
            "client_login_sn": device_id.upper(),
            "deviceid": device_id.upper(),
            "gameid": self.game_id,
            "login_channel": channel,
            "sdkuid": sdk_uid,
            "sessionid": upper,
            "timestamp": timestamp,
            "platform": platform,
            "source_platform": platform,
            "udid": device_id.upper(),
            "userid": user_id,
            "aim_info": "{\"aim\":\"127.0.0.1\",\"tz\":\"+0800\",\"tzid\":\"\",\"country\":\"CN\"}",
            "gas_token": "",
            "ip": "127.0.0.1",
            "realname": "{\"realname_type\":\"0\"}",
            "sdk_version": "1.0.0",
        }
        return json.dumps(payload, ensure_ascii=False)

    def auth_session(self, cookie_json: str) -> None:
        response = self.client.post(f"/{self.game_id}/sdk/uni_sauth", data=cookie_json.encode("utf-8"))
        if response.status >= 400:
            raise RuntimeError(f"mgb sdk error: {response.status}")
        payload = json.loads(response.text())
        code = str(payload.get("code", ""))
        if code != "200":
            status = payload.get("status", "Unknown")
            raise RuntimeError(f"mgb sdk auth failed: {status}")


# -----------------------------------------------------------------------------
# 4399 login (inlined from camellia/api/n4399.py)
# -----------------------------------------------------------------------------


class LoginError(RuntimeError):
    pass


def _cookie_string(jar: CookieJar) -> str:
    parts = []
    for cookie in jar:
        parts.append(f"{cookie.name}={cookie.value}")
    return "; ".join(parts)


def _extract_error_tip(html: str) -> str:
    marker = "login_err_tip\">"
    start = html.find(marker)
    if start == -1:
        return ""
    start += len(marker)
    end = html.find("</div>", start)
    if end == -1:
        return ""
    return html[start:end].strip()


def _parse_query(url: str) -> Dict[str, str]:
    parsed = urllib.parse.urlparse(url)
    params = urllib.parse.parse_qs(parsed.query)
    return {k: v[0] for k, v in params.items() if v}


def _build_login_params(username: str, password: str) -> Dict[str, str]:
    return {
        "loginFrom": "uframe",
        "postLoginHandler": "default",
        "layoutSelfAdapting": "true",
        "externalLogin": "qq",
        "displayMode": "popup",
        "layout": "vertical",
        "bizId": "2100001792",
        "appId": "kid_wdsj",
        "gameId": "wd",
        "css": "https://microgame.5054399.net/v2/resource/cssSdk/default/login.css",
        "redirectUrl": "",
        "mainDivId": "popup_login_div",
        "includeFcmInfo": "false",
        "level": "8",
        "regLevel": "8",
        "userNameLabel": "4399 username",
        "userNameTip": "Enter 4399 username",
        "welcomeTip": "Welcome back",
        "sec": "1",
        "username": username,
        "password": password,
    }


def _check_login(client: HttpClient, cookie_header: str, rand_time: int) -> Dict[str, str]:
    check_url = (
        "https://ptlogin.4399.com/ptlogin/checkKidLoginUserCookie.do?"
        "appId=kid_wdsj&gameUrl=http://cdn.h5wan.4399sj.com/microterminal-h5-frame?"
        f"game_id=500352&rand_time={rand_time}&nick=null&onLineStart=false&"
        "show=1&isCrossDomain=1&retUrl=http%253A%252F%252Fptlogin.4399.com"
        "%252Fresource%252Fucenter.html%253Faction%253Dlogin%2526appId%253Dkid_wdsj%2526"
        "loginLevel%253D8%2526regLevel%253D8%2526bizId%253D2100001792%2526externalLogin%253D"
        "qq%2526qrLogin%253Dtrue%2526layout%253Dvertical%2526level%253D101%2526"
        "css%253Dhttp%253A%252F%252Fmicrogame.5054399.net%252Fv2%252Fresource%252F"
        "cssSdk%252Fdefault%252Flogin.css%2526v%253D2018_11_26_16%2526"
        "postLoginHandler%253Dredirect%2526checkLoginUserCookie%253Dtrue%2526"
        "redirectUrl%253Dhttp%25253A%25252F%25252Fcdn.h5wan.4399sj.com%25252F"
        "microterminal-h5-frame%25253Fgame_id%25253D500352%252526rand_time%25253D"
        f"{rand_time}"
    )
    response = client.get(check_url, headers={"Cookie": cookie_header})
    return _parse_query(response.url)


def _get_uni_auth(query_params: Dict[str, str], client: HttpClient) -> Dict[str, str]:
    sdk_url = (
        "https://microgame.5054399.net/v2/service/sdk/info?"
        "callback=&queryStr=game_id%3D500352%26nick%3Dnull%26sig%3D"
        + query_params.get("sig", "")
        + "%26uid%3D"
        + query_params.get("uid", "")
        + "%26fcm%3D0%26show%3D1%26isCrossDomain%3D1%26rand_time%3D"
        + query_params.get("rand_time", "")
        + "%26"
        + "ptusertype%3D4399%26time%3D"
        + query_params.get("time", "")
        + "%26validateState%3D"
        + query_params.get("validateState", "")
        + "%26username%3D"
        + query_params.get("username", "")
        + "&_="
        + query_params.get("time", "")
    )
    response = client.get(sdk_url)
    text = response.text().strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    data = json.loads(text)
    sdk_login_data = data.get("data", {}).get("sdk_login_data", "")
    params = urllib.parse.parse_qs(sdk_login_data, keep_blank_values=True)
    return {k: v[0] for k, v in params.items() if v}


def login_with_password(username: str, password: str) -> str:
    jar = load_cookie_jar()
    client = HttpClient(cookie_jar=jar)
    response = client.post_form("https://ptlogin.4399.com/ptlogin/login.do?v=1", _build_login_params(username, password))
    if response.status >= 400:
        raise LoginError("login request failed")
    error_tip = _extract_error_tip(response.text())
    if error_tip:
        raise LoginError(error_tip)

    cookie_header = _cookie_string(jar)
    if not cookie_header:
        raise LoginError("no cookies captured from login")

    rand_time = int(time.time())
    redirect_params = _check_login(client, cookie_header, rand_time)
    uni_auth = _get_uni_auth(redirect_params, client)

    sdk = MgbSdk("x19")
    device_id = uuid.uuid4().hex
    return sdk.generate_sauth(
        device_id=device_id,
        user_id=uni_auth.get("username", ""),
        sdk_uid=uni_auth.get("uid", ""),
        session_id=uni_auth.get("token", ""),
        timestamp=uni_auth.get("time", ""),
        channel="4399pc",
    )


# -----------------------------------------------------------------------------
# WPF launcher models (inlined from camellia/models/entities.py)
# -----------------------------------------------------------------------------


@dataclass
class LoginOtp:
    aid: int
    otp_token: str
    lock_time: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoginOtp":
        return cls(
            aid=int(data.get("aid", 0)),
            otp_token=str(data.get("otp_token", "")),
            lock_time=int(data.get("lock_time", 0)),
        )


@dataclass
class AuthOtp:
    entity_id: str
    token: str
    account: str
    login_channel: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any], login_channel: str) -> "AuthOtp":
        return cls(
            entity_id=str(data.get("entity_id", "")),
            token=str(data.get("token", "")),
            account=str(data.get("account", "")),
            login_channel=str(login_channel),
        )


@dataclass
class NetGameItem:
    entity_id: str
    name: str
    brief_summary: str
    online_count: str
    title_image_url: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetGameItem":
        return cls(
            entity_id=str(data.get("entity_id", "")),
            name=str(data.get("name", "")),
            brief_summary=str(data.get("brief_summary", "")),
            online_count=str(data.get("online_count", "")),
            title_image_url=str(data.get("title_image_url", "")),
        )


@dataclass
class McVersion:
    name: str
    mcversion_id: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "McVersion":
        return cls(name=str(data.get("name", "")), mcversion_id=int(data.get("mcversionid", 0)))


@dataclass
class NetGameDetail:
    mc_versions: List[McVersion]
    server_address: str
    server_port: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetGameDetail":
        mc_versions = [McVersion.from_dict(item) for item in data.get("mc_version_list", [])]
        return cls(
            mc_versions=mc_versions,
            server_address=str(data.get("server_address", "")),
            server_port=int(data.get("server_port", 0)),
        )


@dataclass
class NetGameServerAddress:
    host: str
    port: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetGameServerAddress":
        return cls(host=str(data.get("ip", "")), port=int(data.get("port", 0)))


@dataclass
class GameCharacter:
    name: str
    game_id: str
    user_id: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameCharacter":
        return cls(name=str(data.get("name", "")), game_id=str(data.get("game_id", "")), user_id=str(data.get("user_id", "")))


@dataclass
class FantnelInfo:
    crc_salt: Optional[str]
    game_version: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FantnelInfo":
        return cls(crc_salt=data.get("crcSalt"), game_version=data.get("gameVersion"))


# -----------------------------------------------------------------------------
# MD5 mapping (inlined from camellia/mc/md5_mapping.py)
# -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Md5Pair:
    bootstrap_md5: str
    dat_file_md5: str


_MD5_MAPPING: Dict[str, Md5Pair] = {
    SUPPORTED_MC_VERSION: Md5Pair("A895FE657915D58F55919CEACD30209D", "0CF2074AA7D4B543E35A3D6BB57AF861"),
}


def get_md5_pair(version: str) -> Md5Pair:
    if version not in _MD5_MAPPING:
        raise KeyError(f"unsupported game version: {version}")
    return _MD5_MAPPING[version]


# -----------------------------------------------------------------------------
# Yggdrasil crypto utilities (inlined from camellia/crypto/* + camellia/mc/yggdrasil.py)
# -----------------------------------------------------------------------------


try:
    from Crypto.PublicKey import RSA as _RSA
except ImportError:
    from Cryptodome.PublicKey import RSA as _RSA  # type: ignore


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
    key = _RSA.import_key(key_bytes)
    return RsaKey(n=key.n, e=key.e, d=None)


def load_private_key(base64_key: str) -> RsaKey:
    key_bytes = base64.b64decode(base64_key)
    key = _RSA.import_key(key_bytes)
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


_SIGMA = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) & 0xFFFFFFFF) | (value >> (32 - shift))


def _quarter_round(state: List[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl(state[d], 16)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl(state[b], 12)
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl(state[d], 8)
    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl(state[b], 7)


def _chacha_block(key: bytes, nonce: bytes, counter: int, rounds: int) -> bytes:
    if len(key) != 32:
        raise ValueError("ChaCha key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("ChaCha nonce must be 12 bytes")
    key_words = [int.from_bytes(key[i : i + 4], "little") for i in range(0, 32, 4)]
    nonce_words = [int.from_bytes(nonce[i : i + 4], "little") for i in range(0, 12, 4)]
    state = _SIGMA + key_words + [counter & 0xFFFFFFFF] + nonce_words
    working = state.copy()
    for _ in range(rounds // 2):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)
        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)
    output = [(working[i] + state[i]) & 0xFFFFFFFF for i in range(16)]
    return b"".join(word.to_bytes(4, "little") for word in output)


class ChaChaPacker:
    def __init__(self, key: bytes, nonce: bytes, rounds: int = 8, counter_start: int = 0) -> None:
        self.key = key
        self.nonce = nonce
        self.rounds = rounds
        self.counter = counter_start & 0xFFFFFFFF

    def _keystream(self, length: int) -> bytes:
        out = bytearray()
        while len(out) < length:
            block = _chacha_block(self.key, self.nonce, self.counter, self.rounds)
            self.counter = (self.counter + 1) & 0xFFFFFFFF
            out.extend(block)
        return bytes(out[:length])

    def process_bytes(self, data: bytearray, offset: int, length: int) -> None:
        stream = self._keystream(length)
        for i in range(length):
            data[offset + i] ^= stream[i]


_F_TABLE = [
    0xA3, 0xD7, 0x09, 0x83, 0xF8, 0x48, 0xF6, 0xF4, 0xB3, 0x21, 0x15, 0x78, 0x99, 0xB1, 0xAF, 0xF9,
    0xE7, 0x2D, 0x4D, 0x8A, 0xCE, 0x4C, 0xCA, 0x2E, 0x52, 0x95, 0xD9, 0x1E, 0x4E, 0x38, 0x44, 0x28,
    0x0A, 0xDF, 0x02, 0xA0, 0x17, 0xF1, 0x60, 0x68, 0x12, 0xB7, 0x7A, 0xC3, 0xE9, 0xFA, 0x3D, 0x53,
    0x96, 0x84, 0x6B, 0xBA, 0xF2, 0x63, 0x9A, 0x19, 0x7C, 0xAE, 0xE5, 0xF5, 0xF7, 0x16, 0x6A, 0xA2,
    0x39, 0xB6, 0x7B, 0x0F, 0xC1, 0x93, 0x81, 0x1B, 0xEE, 0xB4, 0x1A, 0xEA, 0xD0, 0x91, 0x2F, 0xB8,
    0x55, 0xB9, 0xDA, 0x85, 0x3F, 0x41, 0xBF, 0xE0, 0x5A, 0x58, 0x80, 0x5F, 0x66, 0x0B, 0xD8, 0x90,
    0x35, 0xD5, 0xC0, 0xA7, 0x33, 0x06, 0x65, 0x69, 0x45, 0x00, 0x94, 0x56, 0x6D, 0x98, 0x9B, 0x76,
    0x97, 0xFC, 0xB2, 0xC2, 0xB0, 0xFE, 0xDB, 0x20, 0xE1, 0xEB, 0xD6, 0xE4, 0xDD, 0x47, 0x4A, 0x1D,
    0x42, 0xED, 0x9E, 0x6E, 0x49, 0x3C, 0xCD, 0x43, 0x27, 0xD2, 0x07, 0xD4, 0xDE, 0xC7, 0x67, 0x18,
    0x89, 0xCB, 0x30, 0x1F, 0x8D, 0xC6, 0x8F, 0xAA, 0xC8, 0x74, 0xDC, 0xC9, 0x5D, 0x5C, 0x31, 0xA4,
    0x70, 0x88, 0x61, 0x2C, 0x9F, 0x0D, 0x2B, 0x87, 0x50, 0x82, 0x54, 0x64, 0x26, 0x7D, 0x03, 0x40,
    0x34, 0x4B, 0x1C, 0x73, 0xD1, 0xC4, 0xFD, 0x3B, 0xCC, 0xFB, 0x7F, 0xAB, 0xE6, 0x3E, 0x5B, 0xA5,
    0xAD, 0x04, 0x23, 0x9C, 0x14, 0x51, 0x22, 0xF0, 0x29, 0x79, 0x71, 0x7E, 0xFF, 0x8C, 0x0E, 0xE2,
    0x0C, 0xEF, 0xBC, 0x72, 0x75, 0x6F, 0x37, 0xA1, 0xEC, 0xD3, 0x8E, 0x62, 0x8B, 0x86, 0x10, 0xE8,
    0x08, 0x77, 0x11, 0xBE, 0x92, 0x4F, 0x24, 0xC5, 0x32, 0x36, 0x9D, 0xCF, 0xF3, 0xA6, 0xBB, 0xAC,
    0x5E, 0x6C, 0xA9, 0x13, 0x57, 0x25, 0xB5, 0xE3, 0xBD, 0xA8, 0x3A, 0x01, 0x05, 0x59, 0x2A, 0x46,
]


def _skip32_g(key: bytes, k: int, w: int) -> int:
    g1 = w >> 8
    g2 = w & 0xFF
    g3 = _F_TABLE[g2 ^ (key[(4 * k) % 10] & 0xFF)] ^ g1
    g4 = _F_TABLE[g3 ^ (key[(4 * k + 1) % 10] & 0xFF)] ^ g2
    g5 = _F_TABLE[g4 ^ (key[(4 * k + 2) % 10] & 0xFF)] ^ g3
    g6 = _F_TABLE[g5 ^ (key[(4 * k + 3) % 10] & 0xFF)] ^ g4
    return (g5 << 8) + g6


def _skip32_core(key: bytes, buf: List[int], encrypt: bool) -> None:
    if encrypt:
        step = 1
        k = 0
    else:
        step = -1
        k = 23

    wl = (buf[0] << 8) + buf[1]
    wr = (buf[2] << 8) + buf[3]
    i = 0
    while i < 12:
        wr ^= _skip32_g(key, k, wl) ^ k
        k += step
        wl ^= _skip32_g(key, k, wr) ^ k
        k += step
        i += 1

    buf[0] = wr >> 8
    buf[1] = wr & 0xFF
    buf[2] = wl >> 8
    buf[3] = wl & 0xFF


def skip32_encrypt(value: int, key: bytes) -> int:
    buf = [(value >> 24) & 0xFF, (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF]
    _skip32_core(key, buf, True)
    return ((buf[0] << 24) | (buf[1] << 16) | (buf[2] << 8) | buf[3])


_CHACHA_NONCE = b"163 NetEase\n"
_PRC_CHECK = "[]"
_CLIENT_KEY_LENGTH = 19
_CHECKSUM_LENGTH = 32
_MC_VERSION_SALT = bytes([0x01, 0x00, 0x04, 0x80, 0xD2, 0x3E, 0xF7, 0x11, 0x01])
_TCP_SALT = bytes(
    [0x2F, 0x84, 0xAE, 0xA3, 0x99, 0x21, 0x29, 0x26, 0xDA, 0xBF, 0x95, 0xA3, 0xAB, 0xAF, 0x37, 0xE0]
)

_YGG_DEBUG_DUMP = os.getenv("NEL_YGG_DUMP") == "1"
_YGG_DEBUG_DUMP_PATH = os.getenv("NEL_YGG_DUMP_PATH", os.path.join("logs", "yggdrasil-compare.log"))

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
    return hashlib.sha256(data).digest()


def _ygg_dump_debug(message: str) -> None:
    if not _YGG_DEBUG_DUMP:
        return
    try:
        folder = os.path.dirname(_YGG_DEBUG_DUMP_PATH)
        if folder:
            os.makedirs(folder, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with open(_YGG_DEBUG_DUMP_PATH, "a", encoding="utf-8") as handle:
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
        [
            0xAC,
            0x24,
            0x9C,
            0x69,
            0xC7,
            0x2C,
            0xB3,
            0xB4,
            0x4E,
            0xC0,
            0xCC,
            0x6C,
            0x54,
            0x3A,
            0x81,
            0x95,
        ]
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
        self.port = int(port)
        self.generator = YggdrasilGenerator(data)

    @staticmethod
    def auth_servers() -> List[Tuple[str, int]]:
        with urllib.request.urlopen(X19_AUTH_SERVER_LIST, timeout=5) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        if not payload:
            raise RuntimeError("no auth servers available")
        return [(server["IP"], int(server["Port"])) for server in payload]

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
                except Exception as exc:
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
                except Exception as exc:
                    return False, f"join: {exc}"
        except Exception as exc:
            return False, f"connect: {exc}"

    def _initialize_connection(self, sock: socket.socket, profile: GameProfile) -> bytes:
        received = _read_stream_with_int16(sock)
        if len(received) < 272:
            raise RuntimeError(f"invalid response length: {len(received)} < 272")
        login_seed = received[:16]
        sign_content = received[16 : 16 + 256]
        message = self.generator.generate_initialize_message(profile, login_seed, sign_content)
        sock.sendall(message)
        response = _read_stream_with_int16(sock)
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
        if _YGG_DEBUG_DUMP:
            _ygg_dump_debug(
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
        message = _ygg_pack_message(packer, 9, join_payload, crc_little_endian=crc_little_endian)
        sock.sendall(message)
        response = _read_stream_with_int16(sock)
        packet_type, payload = _ygg_unpack_message(unpacker, response, crc_little_endian=crc_little_endian)
        if packet_type != 9 or not payload or payload[0] != 0x00:
            err = payload[0] if payload else 0xFF
            return False, f"{err:02X}"
        return True, ""


def _ygg_pack_message(packer: ChaChaPacker, packet_type: int, data: bytes, *, crc_little_endian: bool) -> bytes:
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


def _ygg_unpack_message(packer: ChaChaPacker, data: bytes, *, crc_little_endian: bool) -> Tuple[int, bytes]:
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


# -----------------------------------------------------------------------------
# WPF launcher client (subset, inlined from camellia/api/wpf_launcher.py)
# -----------------------------------------------------------------------------


class ApiError(RuntimeError):
    pass


class ModFetchError(RuntimeError):
    pass


_GAME_VERSION_IDS = {
    SUPPORTED_MC_VERSION: 1008009,
}


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _generate_hex_string(length: int) -> str:
    return os.urandom(length).hex().upper()


def _generate_random_mac() -> str:
    mac = bytearray(os.urandom(6))
    mac[0] &= 0xFE
    mac[0] |= 0x02
    return "".join(f"{b:02X}" for b in mac)


def _download_file(url: str, dest: str) -> None:
    try:
        with urllib.request.urlopen(url, timeout=20) as resp, open(dest, "wb") as handle:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    except Exception as exc:
        raise ModFetchError(f"download failed: {exc}") from exc


def _file_md5(path: str) -> str:
    digest = hashlib.md5()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


_MOD_CACHE_DIR = Path.home() / ".camellia" / "mods_cache"


def _mod_cache_path(res_url: str) -> Path:
    digest = hashlib.sha1(res_url.encode("utf-8")).hexdigest()
    return _MOD_CACHE_DIR / f"mods_{digest}.json"


def _load_mod_cache(res_url: str) -> Optional[Dict[str, Mod]]:
    try:
        _MOD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    cache_path = _mod_cache_path(res_url)
    if not cache_path.exists():
        return None
    try:
        payload = json.loads(cache_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("res_url") != res_url:
        return None
    mods_raw = payload.get("mods")
    if not isinstance(mods_raw, list):
        return None
    mods: Dict[str, Mod] = {}
    for item in mods_raw:
        if not isinstance(item, dict):
            continue
        mod_path = str(item.get("modPath", ""))
        md5v = str(item.get("md5", "")).upper()
        iid = str(item.get("iid", ""))
        if not mod_path or not md5v:
            continue
        mods[mod_path] = Mod(
            modPath=mod_path,
            name=str(item.get("name", "")),
            id=str(item.get("id", mod_path)),
            iid=iid,
            md5=md5v,
            version=str(item.get("version", "")),
        )
    return mods or None


def _save_mod_cache(res_url: str, mods: Dict[str, Mod]) -> None:
    try:
        _MOD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    cache_path = _mod_cache_path(res_url)
    payload = {"res_url": res_url, "fetched_at": int(time.time()), "mods": [mod.to_dict() for mod in mods.values()]}
    try:
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def _download_mods_from_archive(res_url: str) -> Dict[str, Mod]:
    with tempfile.TemporaryDirectory(prefix="camellia_mods_") as temp_dir:
        archive_path = os.path.join(temp_dir, "mods.7z")
        _download_file(res_url, archive_path)
        extract_dir = os.path.join(temp_dir, "extract")
        os.makedirs(extract_dir, exist_ok=True)
        try:
            subprocess.run(
                ["7z", "x", "-y", f"-o{extract_dir}", archive_path],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            raise ModFetchError(f"extract failed: {exc}") from exc
        mods_dir = os.path.join(extract_dir, ".minecraft", "mods")
        if not os.path.isdir(mods_dir):
            return {}
        mods: Dict[str, Mod] = {}
        for filename in os.listdir(mods_dir):
            if not filename.lower().endswith(".jar"):
                continue
            path = os.path.join(mods_dir, filename)
            md5v = _file_md5(path).upper()
            iid = filename.split("@", 1)[0]
            mods[filename] = Mod(modPath=filename, name="", id=filename, iid=iid, md5=md5v, version="")
        return mods


class WPFLauncherClient:
    def __init__(self) -> None:
        self._logger = logging.getLogger("camellia.modinfo")
        self.game_version = get_latest_version()
        self.core = HttpClient(base_url=X19_CORE, default_headers={"User-Agent": f"WPFLauncher/{self.game_version}"})
        self.api = HttpClient(base_url=X19_API_GATEWAY, default_headers={"User-Agent": DEFAULT_API_USER_AGENT})
        self.mcl = HttpClient(base_url=X19_MCL, default_headers={"User-Agent": DEFAULT_API_USER_AGENT})
        self.user_id: Optional[str] = None
        self.user_token: Optional[str] = None

    def _ensure_login(self) -> None:
        if not self.user_id or not self.user_token:
            raise ApiError("not logged in")

    def _api_post(self, path: str, payload: Any) -> Dict[str, Any]:
        self._ensure_login()
        body = _json_dumps(payload)
        headers = compute_dynamic_token(path, body, self.user_id, self.user_token)
        response = self.api.post(path, data=body.encode("utf-8"), headers=headers)
        if response.status >= 400:
            raise ApiError(f"api error {response.status}: {response.text()}")
        data = json.loads(response.text())
        if data.get("code") != 0:
            raise ApiError(data.get("message", "api error"))
        return data

    def _core_post(self, path: str, payload: Any) -> Dict[str, Any]:
        body = _json_dumps(payload)
        response = self.core.post(path, data=body.encode("utf-8"))
        if response.status >= 400:
            raise ApiError(f"core error {response.status}: {response.text()}")
        return json.loads(response.text())

    def _core_post_auth(self, path: str, payload: Any) -> Dict[str, Any]:
        self._ensure_login()
        body = _json_dumps(payload)
        headers = compute_dynamic_token(path, body, self.user_id, self.user_token)
        response = self.core.post(path, data=body.encode("utf-8"), headers=headers)
        if response.status >= 400:
            raise ApiError(f"core error {response.status}: {response.text()}")
        return json.loads(response.text())

    def _mcl_post(self, path: str, payload: Any) -> Dict[str, Any]:
        self._ensure_login()
        body = _json_dumps(payload)
        headers = compute_dynamic_token(path, body, self.user_id, self.user_token)
        response = self.mcl.post(path, data=body.encode("utf-8"), headers=headers)
        if response.status >= 400:
            raise ApiError(f"mcl error {response.status}: {response.text()}")
        data = json.loads(response.text())
        if data.get("code") != 0:
            raise ApiError(data.get("message", "mcl error"))
        return data

    def login_with_cookie(self, raw_cookie: str) -> AuthOtp:
        sauth_json = load_cookie_json(raw_cookie)
        cookie = json.loads(sauth_json)
        login_channel = cookie.get("login_channel", "netease")
        if login_channel != "netease":
            # Third-party channels (e.g. 4399) may require MGB SDK pre-auth. When it fails,
            # surface a clearer error so users know they likely need a fresh `sessionid`.
            try:
                MgbSdk("x19").auth_session(sauth_json)
            except Exception as exc:
                session_id = str(cookie.get("sessionid", ""))
                ts_raw = str(cookie.get("timestamp", ""))
                age_hint = ""
                try:
                    ts_ms = int(ts_raw)
                    age_sec = max(0.0, time.time() - (ts_ms / 1000.0))
                    age_hint = f", age={age_sec/3600.0:.1f}h"
                except Exception:
                    pass
                raise ApiError(
                    "mgb sdk auth failed: %s (login_channel=%s, sessionid_len=%s%s). "
                    "The sessionid is usually short-lived; re-login to refresh it."
                    % (exc, login_channel, len(session_id), age_hint)
                ) from exc

        otp = self._login_otp(sauth_json)
        auth = self._authentication_otp(sauth_json, otp)
        self.user_id = auth.entity_id
        self.user_token = auth.token
        self.login_start()
        return auth

    def _login_otp(self, sauth_json: str) -> LoginOtp:
        payload = {"sauth_json": sauth_json}
        data = self._core_post("/login-otp", payload)
        if data.get("code") != 0 or data.get("entity") is None:
            raise ApiError(data.get("message", "login otp failed"))
        return LoginOtp.from_dict(data["entity"])

    def _authentication_otp(self, sauth_json: str, otp: LoginOtp) -> AuthOtp:
        cookie = json.loads(sauth_json)
        upper = _generate_hex_string(4)
        detail = {
            "os_name": "windows",
            "os_ver": "Microsoft Windows 11 Pro",
            "mac_addr": _generate_random_mac(),
            "udid": "0000000000000000" + upper,
            "app_ver": self.game_version,
            "sdk_ver": "",
            "network": "",
            "disk": upper,
            "is64bit": "1",
            "video_card1": "Microsoft Hyper-V Video",
            "video_card2": "Microsoft Remote Display Adapter",
            "video_card3": "",
            "video_card4": "",
            "launcher_type": "PC_java",
            "pay_channel": cookie.get("app_channel", "netease"),
            "dotnet_ver": "4.8.0",
            "cpu_type": "Intel(R) Core(TM) i9-14900KF",
            "ram_size": "8589934592",
            "device_width": "1920",
            "device_height": "1080",
            "os_detail": "10.0.26100",
        }

        auth_data = {
            "sa_data": _json_dumps(detail),
            "sauth_json": sauth_json,
            "version": {"version": self.game_version},
            "aid": str(otp.aid),
            "otp_token": otp.otp_token,
            "lock_time": 0,
        }

        encrypted = http_encrypt(_json_dumps(auth_data).encode("utf-8"))
        response = self.core.post("/authentication-otp", data=encrypted, content_type="application/octet-stream")
        decrypted = http_decrypt(response.body)
        if decrypted is None:
            raise ApiError("failed to decrypt auth response")
        entity = json.loads(decrypted.decode("utf-8"))
        if entity.get("code") != 0 or entity.get("entity") is None:
            raise ApiError(entity.get("message", "auth failed"))
        return AuthOtp.from_dict(entity["entity"], cookie.get("login_channel", "netease"))

    def login_start(self) -> None:
        data = self._core_post_auth("/interconn/web/game-play-v2/login-start", {"strict_mode": True})
        if data.get("code") not in (None, 0):
            raise ApiError(data.get("message", "login start failed"))

    def game_start(self, game_id: str) -> None:
        payload = {"game_id": game_id, "item_list": ["10000"], "game_type": "2", "strict_mode": True}
        data = self._core_post_auth("/interconn/web/game-play-v2/start", payload)
        if data.get("code") not in (None, 0):
            raise ApiError(data.get("message", "game start failed"))

    def get_available_servers(self, offset: int = 0, length: int = 10) -> List[NetGameItem]:
        payload = {
            "available_mc_versions": [],
            "item_type": 1,
            "length": length,
            "offset": offset,
            "master_type_id": "2",
            "secondary_type_id": "",
        }
        data = self._api_post("/item/query/available", payload)
        return [NetGameItem.from_dict(item) for item in data.get("entities", [])]

    def get_server_detail(self, game_id: str) -> NetGameDetail:
        data = self._api_post("/item-details/get_v2", {"item_id": game_id})
        return NetGameDetail.from_dict(data.get("entity", {}) or {})

    def get_server_address(self, game_id: str) -> NetGameServerAddress:
        data = self._api_post("/item-address/get", {"item_id": game_id})
        return NetGameServerAddress.from_dict(data.get("entity", {}) or {})

    def get_characters(self, game_id: str) -> List[GameCharacter]:
        self._ensure_login()
        payload = {"offset": 0, "length": 10, "user_id": self.user_id, "game_id": game_id, "game_type": "2"}
        data = self._api_post("/game-character/query/user-game-characters", payload)
        return [GameCharacter.from_dict(item) for item in data.get("entities", [])]

    def create_character(self, game_id: str, name: str) -> None:
        self._ensure_login()
        payload = {
            "game_id": game_id,
            "game_type": 2,
            "user_id": self.user_id,
            "name": name,
            "create_time": 555555,
            "expire_time": 0,
        }
        try:
            data = self._api_post("/game-character", payload)
        except ApiError as exc:
            # Some environments use an alternate endpoint.
            data = self._api_post("/game-character/create", payload)
            if data.get("code") not in (None, 0):
                raise ApiError(data.get("message", "create character failed")) from exc
        if data.get("code") not in (None, 0):
            raise ApiError(data.get("message", "create character failed"))

    def fetch_fantnel_info(self) -> FantnelInfo:
        # This standalone build locks crc_salt to a fixed value.
        return FantnelInfo(crc_salt=FIXED_CRC_SALT, game_version=self.game_version)

    def get_mod_list(self, game_id: str, version_name: str, include_assets: bool = True) -> ModList:
        if version_name != SUPPORTED_MC_VERSION:
            raise ApiError(f"unsupported game version: {version_name} (only {SUPPORTED_MC_VERSION} supported)")
        mods: Dict[str, Mod] = {}
        core_count = 0
        asset_count = 0

        version_id = _GAME_VERSION_IDS.get(version_name)
        if version_id is None:
            self._logger.warning("Unknown game version for core mods: %s", version_name)
        else:
            core = self._api_post("/game-auth-item-list/query/search-by-game", {"mc_version_id": version_id, "game_type": 2})
            iid_list = (core.get("entity") or {}).get("iid_list") or []
            if iid_list:
                details = self._api_post("/user-item-download-v2/get-list", {"item_id_list": iid_list})
                for item in details.get("entities") or []:
                    item_id = str(item.get("item_id", ""))
                    mtype = item.get("mtypeid", 0)
                    for sub in item.get("sub_entities") or []:
                        jar_md5 = sub.get("jar_md5")
                        if not jar_md5:
                            continue
                        mod_path = f"{item_id}@{mtype}@0.jar"
                        mods[mod_path] = Mod(modPath=mod_path, name="", id=mod_path, iid=item_id, md5=str(jar_md5).upper(), version="")
            core_count = len(mods)

        if include_assets:
            try:
                asset_mods = self._get_server_asset_mods(game_id)
                asset_count = len(asset_mods)
                mods.update(asset_mods)
            except ModFetchError as exc:
                self._logger.warning("Server asset mods skipped: %s", exc)

        self._logger.info("Mod list built: game_id=%s version=%s core=%s assets=%s total=%s", game_id, version_name, core_count, asset_count, len(mods))
        return ModList(list(mods.values()))

    def _get_server_asset_mods(self, game_id: str) -> Dict[str, Mod]:
        response = self._mcl_post("/user-item-download-v2", {"item_id": game_id, "length": 0, "offset": 0})
        entity = response.get("entity") or {}
        sub_entities = entity.get("sub_entities") or []
        if not sub_entities:
            return {}
        res_url = sub_entities[0].get("res_url")
        if not res_url:
            return {}
        cached = _load_mod_cache(res_url)
        if cached is not None:
            self._logger.info("Asset mod cache hit: %s (%s mods)", res_url, len(cached))
            return cached
        mods = _download_mods_from_archive(res_url)
        _save_mod_cache(res_url, mods)
        return mods


# -----------------------------------------------------------------------------
# Minecraft protocol helpers (inlined from camellia/mc/protocol.py)
# -----------------------------------------------------------------------------


class ProtocolError(ValueError):
    pass


class IncompleteVarInt(ProtocolError):
    pass


def read_varint(data: bytes, offset: int = 0) -> Tuple[int, int]:
    num = 0
    num_read = 0
    while True:
        if offset + num_read >= len(data):
            raise IncompleteVarInt("not enough bytes for varint")
        byte = data[offset + num_read]
        num |= (byte & 0x7F) << (7 * num_read)
        num_read += 1
        if num_read > 5:
            raise ProtocolError("varint too big")
        if (byte & 0x80) == 0:
            break
    return num, num_read


def write_varint(value: int) -> bytes:
    if value < 0:
        raise ProtocolError("varint cannot be negative")
    out = bytearray()
    while (value & -128) != 0:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0xFF)
    return bytes(out)


def read_bool(data: bytes, offset: int = 0) -> Tuple[bool, int]:
    if offset >= len(data):
        raise ProtocolError("bool out of range")
    return data[offset] != 0, 1


def read_ushort(data: bytes, offset: int = 0) -> Tuple[int, int]:
    end = offset + 2
    if end > len(data):
        raise ProtocolError("ushort out of range")
    return int.from_bytes(data[offset:end], "big"), 2


def write_ushort(value: int) -> bytes:
    if value < 0 or value > 0xFFFF:
        raise ProtocolError("ushort out of range")
    return int(value).to_bytes(2, "big")


def read_bytes(data: bytes, offset: int, length: int) -> Tuple[bytes, int]:
    end = offset + length
    if end > len(data):
        raise ProtocolError("bytes out of range")
    return data[offset:end], length


def read_string(data: bytes, offset: int = 0, max_length: int = 32767) -> Tuple[str, int]:
    length, size = read_varint(data, offset)
    if length < 0:
        raise ProtocolError("string length negative")
    if length > max_length * 4:
        raise ProtocolError("string length too big")
    start = offset + size
    end = start + length
    if end > len(data):
        raise ProtocolError("string out of range")
    value = data[start:end].decode("utf-8")
    if len(value) > max_length:
        raise ProtocolError("string exceeds max length")
    return value, size + length


def write_string(value: str, max_length: int = 32767) -> bytes:
    if len(value) > max_length:
        raise ProtocolError("string too long")
    raw = value.encode("utf-8")
    return write_varint(len(raw)) + raw


def read_byte_array(data: bytes, offset: int = 0) -> Tuple[bytes, int]:
    length, size = read_varint(data, offset)
    if length < 0:
        raise ProtocolError("byte array length negative")
    payload, consumed = read_bytes(data, offset + size, length)
    return payload, size + consumed


def write_byte_array(value: bytes) -> bytes:
    return write_varint(len(value)) + value


def decompress_packet(payload: bytes, threshold: int) -> bytes:
    data_length, size = read_varint(payload, 0)
    if data_length == 0:
        return payload[size:]
    if data_length < threshold:
        raise ProtocolError("compressed packet below threshold")
    decompressed = zlib.decompress(payload[size:])
    if len(decompressed) != data_length:
        raise ProtocolError("decompressed length mismatch")
    return decompressed


def compress_packet(payload: bytes, threshold: int) -> bytes:
    if len(payload) < threshold:
        return write_varint(0) + payload
    compressed = zlib.compress(payload)
    return write_varint(len(payload)) + compressed


@dataclass
class PacketFrame:
    payload: bytes


class PacketFramer:
    def __init__(self, max_varint_len: int = 3) -> None:
        self._buffer = bytearray()
        self._max_varint_len = max_varint_len

    def feed(self, data: bytes) -> Tuple[PacketFrame, ...]:
        if not data:
            return ()
        self._buffer.extend(data)
        frames: List[PacketFrame] = []
        while True:
            result = self._try_read_length()
            if result is None:
                break
            length, size = result
            if len(self._buffer) < size + length:
                break
            start = size
            end = size + length
            frames.append(PacketFrame(payload=bytes(self._buffer[start:end])))
            del self._buffer[:end]
        return tuple(frames)

    def _try_read_length(self) -> Optional[Tuple[int, int]]:
        num = 0
        num_read = 0
        while True:
            if num_read >= len(self._buffer):
                return None
            byte = self._buffer[num_read]
            num |= (byte & 0x7F) << (7 * num_read)
            num_read += 1
            if num_read > self._max_varint_len:
                raise ProtocolError("packet length varint too long")
            if (byte & 0x80) == 0:
                break
        return num, num_read


def wrap_packet(payload: bytes) -> bytes:
    length = len(payload)
    if length > 0x1FFFFF:
        raise ProtocolError("packet too large for 21-bit length")
    return write_varint(length) + payload


# -----------------------------------------------------------------------------
# Plugin event bus (inlined from camellia/plugins/events.py) - optional
# -----------------------------------------------------------------------------


class PacketDirection(str, Enum):
    SERVERBOUND = "serverbound"
    CLIENTBOUND = "clientbound"


@dataclass
class BaseEvent:
    session: Any
    cancelled: bool = False


@dataclass
class PluginMessageEvent(BaseEvent):
    direction: PacketDirection = PacketDirection.SERVERBOUND
    identifier: str = ""
    payload: bytes = b""


@dataclass
class ChatMessageEvent(BaseEvent):
    message: str = ""


@dataclass
class PlayerPositionEvent(BaseEvent):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    on_ground: bool = False


@dataclass
class AnimationEvent(BaseEvent):
    pass


@dataclass
class LoginSuccessEvent(BaseEvent):
    pass


@dataclass
class SwingArmEvent(BaseEvent):
    hand: int = 0


@dataclass
class UseItemEvent(BaseEvent):
    hand: int = 0
    sequence: int = 0


@dataclass
class UseItemOnEvent(BaseEvent):
    hand: int = 0
    location: Tuple[int, int, int] | None = None
    face: int = 0
    cursor_x: float = 0.0
    cursor_y: float = 0.0
    cursor_z: float = 0.0
    inside_block: bool = False
    sequence: int = 0


@dataclass
class GameJoinEvent(BaseEvent):
    player_id: int | None = None
    payload: bytes = b""


@dataclass
class InteractEvent(BaseEvent):
    entity_id: int = 0
    type: int = 0
    target_x: float | None = None
    target_y: float | None = None
    target_z: float | None = None
    hand: int | None = None
    sneaking: bool = False


@dataclass
class SetEntityMetadataEvent(BaseEvent):
    entity_id: int = 0
    raw_data: bytes = b""


Handler = Callable[[Any], Any]


@dataclass(frozen=True)
class _HandlerEntry:
    handler: Handler
    event_type: type | None
    priority: int


class PluginEventBus:
    def __init__(self) -> None:
        self._handlers: Dict[str, List[_HandlerEntry]] = {}
        self._logger = logging.getLogger("camellia.plugins.events")

    def on(self, name: str, handler: Handler, *, event_type: type | None = None, priority: int = 0) -> None:
        entries = self._handlers.setdefault(name, [])
        entries.append(_HandlerEntry(handler=handler, event_type=event_type, priority=priority))
        entries.sort(key=lambda entry: entry.priority, reverse=True)

    def reset(self) -> None:
        self._handlers.clear()

    async def emit(self, name: str, event: Any) -> None:
        entries = list(self._handlers.get(name, []))
        if not entries:
            return
        for entry in entries:
            if entry.event_type is not None and not isinstance(event, entry.event_type):
                continue
            try:
                result = entry.handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:
                self._logger.warning("Event handler failed: %s (%s)", name, exc)
            if getattr(event, "cancelled", False):
                break


_DEFAULT_BUS: PluginEventBus | None = None


def get_event_bus() -> PluginEventBus:
    global _DEFAULT_BUS
    if _DEFAULT_BUS is None:
        _DEFAULT_BUS = PluginEventBus()
    return _DEFAULT_BUS


# -----------------------------------------------------------------------------
# Minecraft proxy (inlined from camellia/mc/proxy.py)
# -----------------------------------------------------------------------------


import asyncio
import base64
import hashlib
import json
import logging
import os
import struct
import uuid
from datetime import datetime
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Optional

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA



class ConnectionState(IntEnum):
    HANDSHAKE = 0
    STATUS = 1
    LOGIN = 2
    PLAY = 3
    CONFIGURATION = 4


class ProtocolVersion(IntEnum):
    V1076 = 5
    V108X = 47
    V1122 = 340
    V1165 = 754
    V1180 = 757
    V1200 = 763
    V1206 = 766
    V1210 = 767


@dataclass
class ProxyConfig:
    listen_host: str
    listen_port: int
    forward_host: str
    forward_port: int
    nickname: str
    game_id: Optional[str] = None
    ygg_profile: Optional[GameProfile] = None
    ygg_data: Optional[YggdrasilData] = None


class _Cfb8Cipher:
    def __init__(self, key: bytes) -> None:
        self._encryptor = AES.new(key, AES.MODE_CFB, iv=key, segment_size=8)
        self._decryptor = AES.new(key, AES.MODE_CFB, iv=key, segment_size=8)

    def encrypt(self, data: bytes) -> bytes:
        return self._encryptor.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        return self._decryptor.decrypt(data)


class PacketLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._handle: Optional[Any] = None
        self._path: Optional[str] = None
        session_id = f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}-{id(self):x}"
        try:
            base_dir = os.path.abspath(os.path.join(os.getcwd(), "logs"))
            os.makedirs(base_dir, exist_ok=True)
            self._path = os.path.join(base_dir, f"packets-{session_id}.log")
            self._handle = open(self._path, "a", encoding="utf-8", buffering=1)
            self._handle.write(f"# packet_log {datetime.now().isoformat(timespec='seconds')}\n")
        except OSError as exc:
            self._logger.warning("Packet log disabled: %s", exc)
            self._handle = None
            self._path = None

    @property
    def path(self) -> Optional[str]:
        return self._path

    def log(
        self,
        direction: str,
        state: ConnectionState,
        proto: Optional[int],
        packet_id: int,
        raw_len: int,
        payload_len: int,
    ) -> None:
        if self._handle is None:
            return
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        proto_label = str(proto) if proto is not None else "-"
        line = (
            f"{timestamp} {direction} state={state.name} proto={proto_label} "
            f"id=0x{packet_id:02X} raw={raw_len} len={payload_len}"
        )
        try:
            self._handle.write(line + "\n")
        except OSError:
            self._handle = None

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        finally:
            self._handle = None


class MinecraftProxy:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self._logger = logging.getLogger("camellia.proxy")
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> asyncio.AbstractServer:
        self._server = await asyncio.start_server(self._handle_client, self.config.listen_host, self.config.listen_port)
        addrs = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        self._logger.info("Proxy listening on %s -> %s:%s", addrs, self.config.forward_host, self.config.forward_port)
        self._logger.info("[ProxyPhase] listening %s -> %s:%s", addrs, self.config.forward_host, self.config.forward_port)
        return self._server

    async def serve(self) -> None:
        server = await self.start()
        async with server:
            await server.serve_forever()

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()

    async def _handle_client(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        peer = client_writer.get_extra_info("peername")
        self._logger.info("Client connected: %s", peer)
        self._logger.info("[ProxyPhase] client connected %s", peer)
        try:
            self._logger.info("[ProxyPhase] connecting server %s:%s", self.config.forward_host, self.config.forward_port)
            server_reader, server_writer = await asyncio.open_connection(self.config.forward_host, self.config.forward_port)
        except OSError as exc:
            self._logger.error("Failed to connect to server: %s", exc)
            self._logger.warning("[ProxyPhase] connect failed: %s", exc)
            client_writer.close()
            await client_writer.wait_closed()
            return
        self._logger.info("[ProxyPhase] server connected %s:%s", self.config.forward_host, self.config.forward_port)

        session = _ProxySession(self.config, client_reader, client_writer, server_reader, server_writer, self._logger)
        await session.run()


class _ProxySession:
    def __init__(
        self,
        config: ProxyConfig,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        server_reader: asyncio.StreamReader,
        server_writer: asyncio.StreamWriter,
        logger: logging.Logger,
    ) -> None:
        self.config = config
        self.client_reader = client_reader
        self.client_writer = client_writer
        self.server_reader = server_reader
        self.server_writer = server_writer
        self.logger = logger

        self.state = ConnectionState.HANDSHAKE
        self.protocol_version: Optional[int] = None
        self.game_id = config.game_id

        self._packet_logger = PacketLogger(self.logger)
        if self._packet_logger.path:
            self.logger.info("Packet log: %s", self._packet_logger.path)

        self.server_compression_threshold: Optional[int] = None
        self.client_compression_threshold: Optional[int] = None
        self._cipher: Optional[_Cfb8Cipher] = None

        self._client_framer = PacketFramer()
        self._server_framer = PacketFramer()
        self._events = get_event_bus()
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self.plugin_data: dict[str, Any] = {}
        self._pending_client_packets: list[tuple[bytes, bool]] = []
        self._welcome_pending = False
        self._welcome_sent = False
        self._play_seen = False
        self._name_maps: dict[str, dict[str, str]] = {
            "objective": {},
            "team": {},
            "player": {},
        }
        self.player_uuid: str | None = None
        self.player_pos: tuple[float, float, float] | None = None
        self.player_rot: tuple[float, float] | None = None
        self.player_on_ground: bool | None = None
        self._session_id = f"{self.config.nickname}-{id(self):x}"

    def _phase(self, message: str) -> None:
        self.logger.info("[ProxyPhase] %s %s", self._session_id, message)

    async def run(self) -> None:
        self._phase(
            f"session start listen={self.config.listen_host}:{self.config.listen_port} "
            f"forward={self.config.forward_host}:{self.config.forward_port}"
        )
        client_task = asyncio.create_task(self._client_loop())
        server_task = asyncio.create_task(self._server_loop())
        done, pending = await asyncio.wait({client_task, server_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            exc = task.exception()
            if exc:
                self.logger.debug("Session task ended with error: %s", exc)
        await self._close()
        self._phase("session closed")

    async def _close(self) -> None:
        self._closed = True
        if self._tasks:
            for task in list(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._packet_logger.close()
        for writer in (self.client_writer, self.server_writer):
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()

    async def _client_loop(self) -> None:
        while True:
            data = await self.client_reader.read(4096)
            if not data:
                self._phase("client stream closed")
                break
            for frame in self._client_framer.feed(data):
                raw_payload = frame.payload
                raw_len = len(raw_payload)
                payload = raw_payload
                if self.client_compression_threshold is not None:
                    try:
                        payload = decompress_packet(payload, self.client_compression_threshold)
                    except ProtocolError as exc:
                        self.logger.warning("Client packet decompress failed: %s", exc)
                        continue
                try:
                    packet_id, size = read_varint(payload, 0)
                except ProtocolError as exc:
                    self.logger.warning("Client packet parse failed: %s", exc)
                    continue
                body = payload[size:]
                self._packet_logger.log(
                    "C->S",
                    self.state,
                    self.protocol_version,
                    packet_id,
                    raw_len,
                    len(payload),
                )
                new_payload = await self._handle_client_packet(packet_id, payload, body)
                if new_payload is None:
                    continue
                await self._send_to_server(new_payload)

    async def _server_loop(self) -> None:
        while True:
            data = await self.server_reader.read(4096)
            if not data:
                self._phase("server stream closed")
                break
            if self._cipher is not None:
                data = self._cipher.decrypt(data)
            for frame in self._server_framer.feed(data):
                raw_payload = frame.payload
                raw_len = len(raw_payload)
                payload = raw_payload
                if self.server_compression_threshold is not None:
                    try:
                        payload = decompress_packet(payload, self.server_compression_threshold)
                    except ProtocolError as exc:
                        self.logger.warning("Server packet decompress failed: %s", exc)
                        continue
                try:
                    packet_id, size = read_varint(payload, 0)
                except ProtocolError as exc:
                    self.logger.warning("Server packet parse failed: %s", exc)
                    continue
                body = payload[size:]
                state_before = self.state
                self._packet_logger.log(
                    "S->C",
                    self.state,
                    self.protocol_version,
                    packet_id,
                    raw_len,
                    len(payload),
                )
                new_payload = await self._handle_server_packet(packet_id, payload, body)
                if new_payload is None:
                    continue
                await self._send_to_client(new_payload)
                if state_before == ConnectionState.PLAY:
                    self._mark_play_packet(packet_id)
                await self._flush_pending_client_packets()

    async def _send_to_server(self, payload: bytes, *, force_uncompressed: bool = False, force_unencrypted: bool = False) -> None:
        data = payload
        if self.server_compression_threshold is not None and not force_uncompressed:
            data = compress_packet(payload, self.server_compression_threshold)
        data = wrap_packet(data)
        if self._cipher is not None and not force_unencrypted:
            data = self._cipher.encrypt(data)
        self.server_writer.write(data)
        await self.server_writer.drain()

    async def _send_to_client(self, payload: bytes, *, force_uncompressed: bool = False) -> None:
        data = payload
        if self.client_compression_threshold is not None and not force_uncompressed:
            data = compress_packet(payload, self.client_compression_threshold)
        data = wrap_packet(data)
        self.client_writer.write(data)
        await self.client_writer.drain()

    def _queue_client_packet(self, payload: bytes, *, force_uncompressed: bool = False) -> None:
        if payload:
            self._pending_client_packets.append((payload, force_uncompressed))

    async def _flush_pending_client_packets(self) -> None:
        if not self._pending_client_packets:
            return
        queued = self._pending_client_packets
        self._pending_client_packets = []
        for payload, force_uncompressed in queued:
            await self._send_to_client(payload, force_uncompressed=force_uncompressed)

    def create_task(self, coro: asyncio.Future) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @property
    def is_active(self) -> bool:
        return not self._closed and not self.client_writer.is_closing() and not self.server_writer.is_closing()

    async def send_plugin_message(self, direction: PacketDirection, identifier: str, payload: bytes) -> None:
        packet_id = self._get_plugin_message_id(direction)
        if packet_id is None:
            self.logger.warning("Unsupported protocol for plugin message: %s", self.protocol_version)
            return
        if isinstance(payload, memoryview):
            payload = payload.tobytes()
        elif isinstance(payload, bytearray):
            payload = bytes(payload)
        body = write_string(identifier, 32767) + (payload or b"")
        packet = write_varint(packet_id) + body
        if direction == PacketDirection.SERVERBOUND:
            await self._send_to_server(packet)
        else:
            await self._send_to_client(packet)

    async def _emit_login_success(self) -> None:
        event = LoginSuccessEvent(session=self)
        await self._events.emit("login_success", event)
        await self._events.emit("channel_v1122", event)
        if self._is_proto(ProtocolVersion.V1206):
            await self._events.emit("channel_v1206", event)
        self._welcome_pending = True

    def _mark_play_packet(self, packet_id: int) -> None:
        if self._play_seen:
            return
        if packet_id == self._get_play_disconnect_id():
            return
        self._play_seen = True
        if not self._welcome_pending or self._welcome_sent:
            return
        packet = self._build_welcome_packet()
        if packet is None:
            return
        self._queue_client_packet(packet)
        self._welcome_sent = True

    def _build_welcome_packet(self) -> Optional[bytes]:
        nickname = self.config.nickname or "玩家"
        message_text = f"欢迎{nickname}，祝你游戏愉快！Ft.Camellia"
        try:
            if self.protocol_version == int(ProtocolVersion.V108X):
                payload = {"text": message_text}
                message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                return write_varint(0x02) + write_string(message, 32767) + b"\x00"
            if self.protocol_version == int(ProtocolVersion.V1206):
                text_bytes = message_text.encode("utf-8")
                if len(text_bytes) > 32767:
                    raise ProtocolError("welcome message too long")
                body = b"\x08" + len(text_bytes).to_bytes(2, "big") + text_bytes + b"\x00"
                return write_varint(0x6C) + body
        except ProtocolError as exc:
            self.logger.debug("Welcome chat build failed: %s", exc)
        return None

    def _get_v120x_id(self, id_v1200: int, id_v1206: int) -> Optional[int]:
        if self.protocol_version == int(ProtocolVersion.V1200):
            return id_v1200
        if self.protocol_version == int(ProtocolVersion.V1206):
            return id_v1206
        return None

    @staticmethod
    def _read_double(data: bytes, offset: int) -> tuple[float, int]:
        end = offset + 8
        if end > len(data):
            raise ProtocolError("double out of range")
        return struct.unpack_from(">d", data, offset)[0], 8

    @staticmethod
    def _read_float(data: bytes, offset: int) -> tuple[float, int]:
        end = offset + 4
        if end > len(data):
            raise ProtocolError("float out of range")
        return struct.unpack_from(">f", data, offset)[0], 4

    @staticmethod
    def _read_long(data: bytes, offset: int) -> tuple[int, int]:
        end = offset + 8
        if end > len(data):
            raise ProtocolError("long out of range")
        return int.from_bytes(data[offset:end], "big", signed=False), 8

    @classmethod
    def _read_position(cls, data: bytes, offset: int) -> tuple[tuple[int, int, int], int]:
        value, size = cls._read_long(data, offset)
        x = value >> 38
        y = value & 0xFFF
        z = (value >> 12) & 0x3FFFFFF
        if x >= 1 << 25:
            x -= 1 << 26
        if y >= 1 << 11:
            y -= 1 << 12
        if z >= 1 << 25:
            z -= 1 << 26
        return (x, y, z), size

    @staticmethod
    def _write_position(pos: tuple[int, int, int]) -> bytes:
        x, y, z = pos
        value = ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)
        return int(value).to_bytes(8, "big", signed=False)

    def _update_player_state(self, packet_id: int, body: bytes) -> bool:
        pos_id = self._get_v120x_id(0x14, 0x1A)
        pos_rot_id = self._get_v120x_id(0x15, 0x1B)
        rot_id = self._get_v120x_id(0x16, 0x1C)
        on_ground_id = self._get_v120x_id(0x17, 0x1D)
        try:
            if pos_id is not None and packet_id == pos_id:
                offset = 0
                x, size = self._read_double(body, offset)
                offset += size
                y, size = self._read_double(body, offset)
                offset += size
                z, size = self._read_double(body, offset)
                offset += size
                on_ground, _ = read_bool(body, offset)
                self.player_pos = (x, y, z)
                self.player_on_ground = on_ground
                return True
            if pos_rot_id is not None and packet_id == pos_rot_id:
                offset = 0
                x, size = self._read_double(body, offset)
                offset += size
                y, size = self._read_double(body, offset)
                offset += size
                z, size = self._read_double(body, offset)
                offset += size
                yaw, size = self._read_float(body, offset)
                offset += size
                pitch, size = self._read_float(body, offset)
                offset += size
                on_ground, _ = read_bool(body, offset)
                self.player_pos = (x, y, z)
                self.player_rot = (yaw, pitch)
                self.player_on_ground = on_ground
                return True
            if rot_id is not None and packet_id == rot_id:
                offset = 0
                yaw, size = self._read_float(body, offset)
                offset += size
                pitch, size = self._read_float(body, offset)
                offset += size
                on_ground, _ = read_bool(body, offset)
                self.player_rot = (yaw, pitch)
                self.player_on_ground = on_ground
                return True
            if on_ground_id is not None and packet_id == on_ground_id:
                on_ground, _ = read_bool(body, 0)
                self.player_on_ground = on_ground
                return True
        except ProtocolError as exc:
            self.logger.debug("Player state parse failed: %s", exc)
        return False

    async def _handle_plugin_message(
        self,
        direction: PacketDirection,
        packet_id: int,
        payload: bytes,
        body: bytes,
    ) -> Optional[bytes]:
        try:
            identifier, size = read_string(body, 0, 32767)
        except ProtocolError as exc:
            self.logger.debug("Plugin message parse failed: %s", exc)
            return payload
        data = body[size:]
        event = PluginMessageEvent(
            session=self,
            direction=direction,
            identifier=identifier,
            payload=data,
        )
        await self._events.emit("plugin_message", event)
        if event.cancelled:
            return None
        new_identifier = event.identifier
        new_payload = event.payload
        if isinstance(new_payload, memoryview):
            new_payload = new_payload.tobytes()
        elif isinstance(new_payload, bytearray):
            new_payload = bytes(new_payload)
        if new_identifier != identifier or new_payload != data:
            return write_varint(packet_id) + write_string(new_identifier, 32767) + (new_payload or b"")
        return payload

    def _get_plugin_message_id(self, direction: PacketDirection) -> Optional[int]:
        if self.protocol_version == int(ProtocolVersion.V108X):
            return 0x17 if direction == PacketDirection.SERVERBOUND else 0x3F
        if self.protocol_version == int(ProtocolVersion.V1122):
            return 0x09 if direction == PacketDirection.SERVERBOUND else 0x18
        if self.protocol_version == int(ProtocolVersion.V1200):
            return 0x0D if direction == PacketDirection.SERVERBOUND else 0x17
        if self.protocol_version == int(ProtocolVersion.V1206):
            if self.state == ConnectionState.CONFIGURATION:
                return 0x02 if direction == PacketDirection.SERVERBOUND else 0x01
            return 0x12 if direction == PacketDirection.SERVERBOUND else 0x19
        return None

    async def _handle_client_packet(self, packet_id: int, payload: bytes, body: bytes) -> Optional[bytes]:
        if self.state == ConnectionState.HANDSHAKE and packet_id == 0:
            self._phase("client handshake")
            return self._rewrite_handshake(body)
        if self.state == ConnectionState.LOGIN and packet_id == 0:
            self._phase("client login start")
            return self._rewrite_login_start(body)
        if self.state == ConnectionState.LOGIN and packet_id == 3 and self._is_proto(ProtocolVersion.V1206):
            self.state = ConnectionState.CONFIGURATION
            self.logger.info("Client login acknowledged, entering configuration")
            self._phase("client login ack -> configuration")
            return payload
        if self.state == ConnectionState.CONFIGURATION and self._is_proto(ProtocolVersion.V1206):
            config_plugin_id = self._get_plugin_message_id(PacketDirection.SERVERBOUND)
            if config_plugin_id is not None and packet_id == config_plugin_id:
                return await self._handle_plugin_message(PacketDirection.SERVERBOUND, packet_id, payload, body)
        if self.state == ConnectionState.CONFIGURATION and packet_id == 3:
            self.state = ConnectionState.PLAY
            self.logger.info("Client finished configuration, entering play")
            self._phase("client finished configuration -> play")
            await self._emit_login_success()
            return payload
        if self.state == ConnectionState.PLAY:
            if self.protocol_version == int(ProtocolVersion.V108X):
                if packet_id == 0x17:
                    return await self._handle_plugin_message(PacketDirection.SERVERBOUND, packet_id, payload, body)
                if packet_id == 0x0A:
                    event = AnimationEvent(session=self)
                    await self._events.emit("animation", event)
                    if event.cancelled:
                        return None
                if packet_id == 0x02:
                    try:
                        offset = 0
                        entity_id, size = read_varint(body, offset)
                        offset += size
                        action_type, _ = read_varint(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Use entity parse failed: %s", exc)
                        return payload
                    event = InteractEvent(
                        session=self,
                        entity_id=entity_id,
                        type=action_type,
                        target_x=None,
                        target_y=None,
                        target_z=None,
                        hand=None,
                        sneaking=False,
                    )
                    await self._events.emit("interact", event)
                    if event.cancelled:
                        return None
            elif self.protocol_version == int(ProtocolVersion.V1122):
                play_plugin_id = self._get_plugin_message_id(PacketDirection.SERVERBOUND)
                if play_plugin_id is not None and packet_id == play_plugin_id:
                    return await self._handle_plugin_message(PacketDirection.SERVERBOUND, packet_id, payload, body)
                if packet_id == 0x02:
                    try:
                        message, size = read_string(body, 0, 256)
                    except ProtocolError as exc:
                        self.logger.debug("Chat message parse failed: %s", exc)
                        return payload
                    event = ChatMessageEvent(session=self, message=message)
                    await self._events.emit("chat_message", event)
                    if event.cancelled:
                        return None
                    if event.message != message:
                        try:
                            return write_varint(packet_id) + write_string(event.message, 256)
                        except ProtocolError as exc:
                            self.logger.debug("Chat message build failed: %s", exc)
                if packet_id == 0x0E:
                    try:
                        offset = 0
                        x, size = self._read_double(body, offset)
                        offset += size
                        y, size = self._read_double(body, offset)
                        offset += size
                        z, size = self._read_double(body, offset)
                        offset += size
                        yaw, size = self._read_float(body, offset)
                        offset += size
                        pitch, size = self._read_float(body, offset)
                        offset += size
                        on_ground, _ = read_bool(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Player position parse failed: %s", exc)
                        return payload
                    event = PlayerPositionEvent(
                        session=self,
                        x=x,
                        y=y,
                        z=z,
                        yaw=yaw,
                        pitch=pitch,
                        on_ground=on_ground,
                    )
                    await self._events.emit("player_position", event)
                    if event.cancelled:
                        return None
                    if (
                        event.x != x
                        or event.y != y
                        or event.z != z
                        or event.yaw != yaw
                        or event.pitch != pitch
                        or event.on_ground != on_ground
                    ):
                        new_body = struct.pack(
                            ">dddff?",
                            float(event.x),
                            float(event.y),
                            float(event.z),
                            float(event.yaw),
                            float(event.pitch),
                            bool(event.on_ground),
                        )
                        return write_varint(packet_id) + new_body
            elif self._is_proto(ProtocolVersion.V1200):
                play_plugin_id = self._get_plugin_message_id(PacketDirection.SERVERBOUND)
                if play_plugin_id is not None and packet_id == play_plugin_id:
                    return await self._handle_plugin_message(PacketDirection.SERVERBOUND, packet_id, payload, body)
                if self._update_player_state(packet_id, body):
                    return payload
                swing_id = self._get_v120x_id(0x2F, 0x36)
                if swing_id is not None and packet_id == swing_id:
                    try:
                        hand, _ = read_varint(body, 0)
                    except ProtocolError as exc:
                        self.logger.debug("Swing arm parse failed: %s", exc)
                        return payload
                    event = SwingArmEvent(session=self, hand=hand)
                    await self._events.emit("swing_arm", event)
                    if event.cancelled:
                        return None
                use_item_on_id = self._get_v120x_id(0x31, 0x38)
                if use_item_on_id is not None and packet_id == use_item_on_id:
                    try:
                        offset = 0
                        hand, size = read_varint(body, offset)
                        offset += size
                        location, size = self._read_position(body, offset)
                        offset += size
                        face, size = read_varint(body, offset)
                        offset += size
                        cursor_x, size = self._read_float(body, offset)
                        offset += size
                        cursor_y, size = self._read_float(body, offset)
                        offset += size
                        cursor_z, size = self._read_float(body, offset)
                        offset += size
                        inside, size = read_bool(body, offset)
                        offset += size
                        sequence, _ = read_varint(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Use item on parse failed: %s", exc)
                        return payload
                    event = UseItemOnEvent(
                        session=self,
                        hand=hand,
                        location=location,
                        face=face,
                        cursor_x=cursor_x,
                        cursor_y=cursor_y,
                        cursor_z=cursor_z,
                        inside_block=inside,
                        sequence=sequence,
                    )
                    await self._events.emit("use_item_on", event)
                    if event.cancelled:
                        return None
                use_item_id = self._get_v120x_id(0x32, 0x39)
                if use_item_id is not None and packet_id == use_item_id:
                    try:
                        offset = 0
                        hand, size = read_varint(body, offset)
                        offset += size
                        sequence, _ = read_varint(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Use item parse failed: %s", exc)
                        return payload
                    event = UseItemEvent(session=self, hand=hand, sequence=sequence)
                    await self._events.emit("use_item", event)
                    if event.cancelled:
                        return None
                if self.protocol_version == int(ProtocolVersion.V1200) and packet_id == 0x10:
                    try:
                        offset = 0
                        entity_id, size = read_varint(body, offset)
                        offset += size
                        action_type, size = read_varint(body, offset)
                        offset += size
                        target_x = target_y = target_z = None
                        if action_type == 2:
                            target_x, size = self._read_float(body, offset)
                            offset += size
                            target_y, size = self._read_float(body, offset)
                            offset += size
                            target_z, size = self._read_float(body, offset)
                            offset += size
                        hand = None
                        if action_type in (0, 2):
                            hand, size = read_varint(body, offset)
                            offset += size
                        sneaking, _ = read_bool(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Interact parse failed: %s", exc)
                        return payload
                    event = InteractEvent(
                        session=self,
                        entity_id=entity_id,
                        type=action_type,
                        target_x=target_x,
                        target_y=target_y,
                        target_z=target_z,
                        hand=hand,
                        sneaking=sneaking,
                    )
                    await self._events.emit("interact", event)
                    if event.cancelled:
                        return None
        return payload

    async def _handle_server_packet(self, packet_id: int, payload: bytes, body: bytes) -> Optional[bytes]:
        if self.state == ConnectionState.LOGIN and packet_id == 1:
            self._phase("server encryption request")
            await self._handle_encryption_request(body)
            return None
        if self.state == ConnectionState.LOGIN and packet_id == 0:
            self._phase("server login disconnect")
            self._handle_disconnect(body)
            return payload
        if self.state == ConnectionState.LOGIN and packet_id == 3 and self.protocol_version != ProtocolVersion.V1076:
            self._phase("server set compression")
            self._handle_set_compression(body)
            await self._send_to_client(payload, force_uncompressed=True)
            self.client_compression_threshold = self.server_compression_threshold
            return None
        if self.state == ConnectionState.LOGIN and packet_id == 2:
            self._phase("server login success")
            await self._handle_login_success()
            return payload
        if self.state == ConnectionState.CONFIGURATION and self._is_proto(ProtocolVersion.V1206):
            config_plugin_id = self._get_plugin_message_id(PacketDirection.CLIENTBOUND)
            if config_plugin_id is not None and packet_id == config_plugin_id:
                return await self._handle_plugin_message(PacketDirection.CLIENTBOUND, packet_id, payload, body)
        if self.state == ConnectionState.PLAY and packet_id == self._get_play_disconnect_id():
            self._phase("server play disconnect")
            self._handle_disconnect(body)
            return payload
        if self.state == ConnectionState.PLAY:
            if self.protocol_version == int(ProtocolVersion.V108X):
                if packet_id == 0x26 and len(payload) > 0x200000:
                    try:
                        split_packets = self._split_chunk_bulk_v108x(packet_id, body)
                    except ProtocolError as exc:
                        self.logger.warning(
                            "Chunk bulk split failed (payload=%s body=%s): %s",
                            len(payload),
                            len(body),
                            exc,
                        )
                        self.logger.warning("Dropping oversized chunk bulk to avoid client disconnect")
                        return None
                    else:
                        self.logger.warning(
                            "Chunk bulk too large (%s bytes), split into %s packets",
                            len(payload),
                            len(split_packets),
                        )
                        for packet in split_packets:
                            await self._send_to_client(packet)
                        return None
                if len(payload) > 0x200000:
                    self.logger.warning(
                        "Oversized v108x packet id=0x%02X payload=%s; dropping to avoid client disconnect",
                        packet_id,
                        len(payload),
                    )
                    return None
                if packet_id == 0x3F:
                    return await self._handle_plugin_message(PacketDirection.CLIENTBOUND, packet_id, payload, body)
                sanitized = self._sanitize_clientbound_play_v108x(packet_id, body)
                if sanitized is not None:
                    return write_varint(packet_id) + sanitized
            elif self.protocol_version == int(ProtocolVersion.V1122):
                play_plugin_id = self._get_plugin_message_id(PacketDirection.CLIENTBOUND)
                if play_plugin_id is not None and packet_id == play_plugin_id:
                    return await self._handle_plugin_message(PacketDirection.CLIENTBOUND, packet_id, payload, body)
                if packet_id == 0x23:
                    try:
                        if len(body) < 4:
                            raise ProtocolError("join game packet too short")
                        player_id = int.from_bytes(body[0:4], "big", signed=True)
                    except ProtocolError as exc:
                        self.logger.debug("Game join parse failed: %s", exc)
                        return payload
                    event = GameJoinEvent(session=self, player_id=player_id, payload=body[4:])
                    await self._events.emit("game_join", event)
                    if event.cancelled:
                        return None
            elif self._is_proto(ProtocolVersion.V1200):
                play_plugin_id = self._get_plugin_message_id(PacketDirection.CLIENTBOUND)
                if play_plugin_id is not None and packet_id == play_plugin_id:
                    return await self._handle_plugin_message(PacketDirection.CLIENTBOUND, packet_id, payload, body)
                game_join_id = self._get_v120x_id(0x28, 0x2B)
                if game_join_id is not None and packet_id == game_join_id:
                    try:
                        player_id, size = read_varint(body, 0)
                    except ProtocolError as exc:
                        self.logger.debug("Game join parse failed: %s", exc)
                        return payload
                    event = GameJoinEvent(session=self, player_id=player_id, payload=body[size:])
                    await self._events.emit("game_join", event)
                    if event.cancelled:
                        return None
                if self.protocol_version == int(ProtocolVersion.V1206) and packet_id == 0x58:
                    try:
                        entity_id, size = read_varint(body, 0)
                    except ProtocolError as exc:
                        self.logger.debug("Entity metadata parse failed: %s", exc)
                        return payload
                    event = SetEntityMetadataEvent(session=self, entity_id=entity_id, raw_data=body[size:])
                    await self._events.emit("set_entity_metadata", event)
                    if event.cancelled:
                        return None
        if self.state == ConnectionState.PLAY and packet_id == 105 and self._is_proto(ProtocolVersion.V1206):
            self.state = ConnectionState.CONFIGURATION
            self.logger.info("Server started configuration")
            self._phase("server started configuration")
            return payload
        return payload

    def _rewrite_handshake(self, body: bytes) -> bytes:
        offset = 0
        protocol, size = read_varint(body, offset)
        offset += size
        server_addr, size = read_string(body, offset, 255)
        offset += size
        _server_port, size = read_ushort(body, offset)
        offset += size
        next_state, size = read_varint(body, offset)
        offset += size

        self.protocol_version = protocol
        try:
            self.state = ConnectionState(next_state)
        except ValueError:
            self.state = ConnectionState.HANDSHAKE
        self._phase(f"handshake proto={protocol} next_state={self.state.name}")

        new_addr = _with_fml_marker(protocol, self.config.forward_host)
        self.logger.debug("Handshake addr %s -> %s", _safe_b64(server_addr), _safe_b64(new_addr))
        new_body = (
            write_varint(protocol)
            + write_string(new_addr)
            + write_ushort(self.config.forward_port)
            + write_varint(next_state)
        )
        return write_varint(0) + new_body

    def _rewrite_login_start(self, body: bytes) -> bytes:
        offset = 0
        proto = self.protocol_version or ProtocolVersion.V108X
        nickname = self.config.nickname
        if proto in (ProtocolVersion.V1206, ProtocolVersion.V1210):
            _profile, size = read_string(body, offset, 16)
            offset += size
            uuid_bytes, size = read_bytes(body, offset, 16)
            offset += size
            try:
                self.player_uuid = str(uuid.UUID(bytes=uuid_bytes))
            except ValueError:
                self.player_uuid = None
            new_body = write_string(nickname, 16) + uuid_bytes
        elif proto == ProtocolVersion.V1200:
            _profile, size = read_string(body, offset, 16)
            offset += size
            has_uuid, size = read_bool(body, offset)
            offset += size
            uuid_bytes = b""
            if has_uuid:
                uuid_bytes, size = read_bytes(body, offset, 16)
                offset += size
                try:
                    self.player_uuid = str(uuid.UUID(bytes=uuid_bytes))
                except ValueError:
                    self.player_uuid = None
            new_body = write_string(nickname, 16) + (b"\x01" if has_uuid else b"\x00") + uuid_bytes
        elif proto == ProtocolVersion.V1180:
            _profile, size = read_string(body, offset, 16)
            offset += size
            new_body = write_string(nickname)
        else:
            _profile, size = read_string(body, offset, 16)
            offset += size
            new_body = write_string(nickname, 16)
        self._phase(f"rewrite login start proto={int(proto)} nickname={nickname}")
        return write_varint(0) + new_body

    async def _handle_encryption_request(self, body: bytes) -> None:
        if self.protocol_version is None:
            self.logger.warning("Encryption request received before handshake")
            return
        server_id, public_key, verify_token, _should_auth = _parse_encryption_request(body, self.protocol_version)
        self._phase(f"encryption request server_id_len={len(server_id)} should_auth={_should_auth}")
        secret_key = os.urandom(16)
        server_hash = _compute_server_hash(server_id, secret_key, public_key)

        if self.config.ygg_profile and self.config.ygg_data:
            self._phase(f"yggdrasil join server_hash={server_hash[:12]}...")
            await self._join_yggdrasil(server_hash)
        else:
            self.logger.warning("Yggdrasil profile missing; skipping join")
            self._phase("yggdrasil join skipped (missing profile)")

        response_body = _build_encryption_response(self.protocol_version, secret_key, public_key, verify_token)
        response_payload = write_varint(1) + response_body
        await self._send_to_server(response_payload, force_uncompressed=True, force_unencrypted=True)
        self._cipher = _Cfb8Cipher(secret_key)
        self.logger.info("Encryption enabled for server connection")
        self._phase("encryption enabled")

    def _handle_set_compression(self, body: bytes) -> None:
        threshold, _ = read_varint(body, 0)
        self.server_compression_threshold = threshold
        self.logger.info("Compression enabled, threshold=%s", threshold)
        self._phase(f"compression enabled threshold={threshold}")

    async def _handle_login_success(self) -> None:
        if self._is_proto(ProtocolVersion.V1206):
            self.logger.info("Login success (configuration flow)")
            self._phase("login success (configuration flow)")
            return
        self.state = ConnectionState.PLAY
        self.logger.info("Login success, entering play")
        self._phase("login success -> play")
        await self._emit_login_success()

    def _get_play_disconnect_id(self) -> Optional[int]:
        if self.protocol_version is None:
            return None
        if self.protocol_version <= int(ProtocolVersion.V108X):
            return 0x40
        return 0x1A

    def _handle_disconnect(self, body: bytes) -> None:
        try:
            reason, _ = read_string(body, 0, 32767)
        except ProtocolError:
            reason = "<parse failed>"
        self.logger.warning("Server disconnect: %s", reason)
        self._phase(f"disconnect reason={reason}")

    def _is_proto(self, proto: ProtocolVersion) -> bool:
        if self.protocol_version is None:
            return False
        return self.protocol_version >= int(proto)

    def _sanitize_clientbound_play_v108x(self, packet_id: int, body: bytes) -> Optional[bytes]:
        try:
            if packet_id == 0x38:
                return self._sanitize_player_list(body)
            if packet_id == 0x3B:
                return self._sanitize_objective(body)
            if packet_id == 0x3C:
                return self._sanitize_score_update(body)
            if packet_id == 0x3D:
                return self._sanitize_display_score(body)
            if packet_id == 0x3E:
                return self._sanitize_team(body)
        except ProtocolError as exc:
            self.logger.debug("Sanitize packet failed: %s", exc)
        return None

    def _sanitize_player_list(self, body: bytes) -> Optional[bytes]:
        offset = 0
        action, size = read_varint(body, offset)
        offset += size
        count, size = read_varint(body, offset)
        offset += size
        out = bytearray()
        out += write_varint(action)
        out += write_varint(count)
        changed = False
        for _ in range(count):
            uuid_bytes, size = read_bytes(body, offset, 16)
            offset += size
            out += uuid_bytes
            if action == 0:
                name, size = read_string(body, offset, 32767)
                offset += size
                mapped = self._map_name("player", name, 16)
                if mapped != name:
                    changed = True
                out += write_string(mapped, 16)
                prop_count, size = read_varint(body, offset)
                offset += size
                out += write_varint(prop_count)
                for _ in range(prop_count):
                    prop_name, size = read_string(body, offset, 32767)
                    offset += size
                    prop_value, size = read_string(body, offset, 32767)
                    offset += size
                    out += write_string(prop_name)
                    out += write_string(prop_value)
                    has_sig, size = read_bool(body, offset)
                    offset += size
                    out += b"\x01" if has_sig else b"\x00"
                    if has_sig:
                        signature, size = read_string(body, offset, 32767)
                        offset += size
                        out += write_string(signature)
                gamemode, size = read_varint(body, offset)
                offset += size
                out += write_varint(gamemode)
                ping, size = read_varint(body, offset)
                offset += size
                out += write_varint(ping)
                has_display, size = read_bool(body, offset)
                offset += size
                out += b"\x01" if has_display else b"\x00"
                if has_display:
                    display, size = read_string(body, offset, 32767)
                    offset += size
                    out += write_string(display)
            elif action == 1:
                gamemode, size = read_varint(body, offset)
                offset += size
                out += write_varint(gamemode)
            elif action == 2:
                ping, size = read_varint(body, offset)
                offset += size
                out += write_varint(ping)
            elif action == 3:
                has_display, size = read_bool(body, offset)
                offset += size
                out += b"\x01" if has_display else b"\x00"
                if has_display:
                    display, size = read_string(body, offset, 32767)
                    offset += size
                    out += write_string(display)
            elif action == 4:
                continue
        if not changed:
            return None
        return bytes(out)

    def _sanitize_objective(self, body: bytes) -> Optional[bytes]:
        offset = 0
        name, size = read_string(body, offset, 32767)
        offset += size
        if offset >= len(body):
            return None
        mode = body[offset]
        offset += 1
        mapped = self._map_name("objective", name, 16)
        out = bytearray()
        out += write_string(mapped, 16)
        out.append(mode)
        changed = mapped != name
        if mode in (0, 2):
            display_raw, size = read_string(body, offset, 32767)
            offset += size
            display = self._truncate_string(display_raw, 32)
            out += write_string(display, 32)
            type_raw, size = read_string(body, offset, 32767)
            offset += size
            type_name = self._truncate_string(type_raw, 16)
            out += write_string(type_name, 16)
            if display != display_raw or type_name != type_raw:
                changed = True
        return bytes(out) if changed else None

    def _sanitize_score_update(self, body: bytes) -> Optional[bytes]:
        offset = 0
        score_name, size = read_string(body, offset, 32767)
        offset += size
        if offset >= len(body):
            return None
        action = body[offset]
        offset += 1
        objective, size = read_string(body, offset, 32767)
        offset += size
        mapped_objective = self._map_name("objective", objective, 16)
        out = bytearray()
        out += write_string(score_name)
        out.append(action)
        out += write_string(mapped_objective, 16)
        changed = mapped_objective != objective
        if action != 1:
            value, size = read_varint(body, offset)
            offset += size
            out += write_varint(value)
        return bytes(out) if changed else None

    def _sanitize_display_score(self, body: bytes) -> Optional[bytes]:
        if not body:
            return None
        position = body[0]
        offset = 1
        name, size = read_string(body, offset, 32767)
        offset += size
        mapped = self._map_name("objective", name, 16)
        if mapped == name:
            return None
        out = bytearray()
        out.append(position)
        out += write_string(mapped, 16)
        return bytes(out)

    def _sanitize_team(self, body: bytes) -> Optional[bytes]:
        offset = 0
        name, size = read_string(body, offset, 32767)
        offset += size
        if offset >= len(body):
            return None
        mode = body[offset]
        offset += 1
        mapped_name = self._map_name("team", name, 16)
        out = bytearray()
        out += write_string(mapped_name, 16)
        out.append(mode)
        changed = mapped_name != name
        if mode in (0, 2):
            display_raw, size = read_string(body, offset, 32767)
            offset += size
            display = self._truncate_string(display_raw, 32)
            out += write_string(display, 32)
            prefix_raw, size = read_string(body, offset, 32767)
            offset += size
            prefix = self._truncate_string(prefix_raw, 16)
            out += write_string(prefix, 16)
            suffix_raw, size = read_string(body, offset, 32767)
            offset += size
            suffix = self._truncate_string(suffix_raw, 16)
            out += write_string(suffix, 16)
            if prefix != prefix_raw or suffix != suffix_raw or display != display_raw:
                changed = True
            if offset < len(body):
                out.append(body[offset])
                offset += 1
            visibility_raw, size = read_string(body, offset, 32767)
            offset += size
            visibility = self._truncate_string(visibility_raw, 32)
            out += write_string(visibility, 32)
            if visibility != visibility_raw:
                changed = True
            if offset < len(body):
                out.append(body[offset])
                offset += 1
        if mode in (0, 3, 4):
            count, size = read_varint(body, offset)
            offset += size
            out += write_varint(count)
            for _ in range(count):
                player, size = read_string(body, offset, 32767)
                offset += size
                mapped_player = self._map_name("player", player, 16)
                if mapped_player != player:
                    changed = True
                out += write_string(mapped_player, 16)
        return bytes(out) if changed else None

    def _split_chunk_bulk_v108x(self, packet_id: int, body: bytes) -> list[bytes]:
        offset = 0
        skylight, size = read_bool(body, offset)
        offset += size
        count, size = read_varint(body, offset)
        offset += size
        metas: list[tuple[int, int, int, int]] = []
        for _ in range(count):
            if offset + 12 > len(body):
                raise ProtocolError("chunk bulk meta out of range")
            chunk_x = int.from_bytes(body[offset : offset + 4], "big", signed=True)
            offset += 4
            chunk_z = int.from_bytes(body[offset : offset + 4], "big", signed=True)
            offset += 4
            primary = int.from_bytes(body[offset : offset + 2], "big")
            offset += 2
            add = int.from_bytes(body[offset : offset + 2], "big")
            offset += 2
            metas.append((chunk_x, chunk_z, primary, add))

        data_blob = body[offset:]
        if len(data_blob) >= 4:
            possible_len = int.from_bytes(data_blob[:4], "big", signed=False)
            if possible_len == len(data_blob) - 4:
                data_blob = data_blob[4:]
        data_offset = 0
        chunks: list[tuple[int, int, int, int, bytes]] = []
        # 1.8.9 chunk section layout:
        # blocks: 4096 * 2 bytes (12-bit packed) = 8192
        # block light: 2048
        # sky light: 2048 (if skylight)
        section_size = 8192 + 2048 + (2048 if skylight else 0)
        for chunk_x, chunk_z, primary, add in metas:
            section_count = primary.bit_count()
            add_count = add.bit_count()
            chunk_size = section_count * section_size + add_count * 2048 + 256
            end = data_offset + chunk_size
            if end > len(data_blob):
                raise ProtocolError("chunk bulk data out of range")
            chunks.append((chunk_x, chunk_z, primary, add, data_blob[data_offset:end]))
            data_offset = end
        if data_offset != len(data_blob):
            raise ProtocolError("chunk bulk trailing data")

        packets: list[bytes] = []
        for chunk_x, chunk_z, primary, add, chunk_data in chunks:
            body_out = bytearray()
            body_out.append(1 if skylight else 0)
            body_out += write_varint(1)
            body_out += int(chunk_x).to_bytes(4, "big", signed=True)
            body_out += int(chunk_z).to_bytes(4, "big", signed=True)
            body_out += int(primary).to_bytes(2, "big")
            body_out += int(add).to_bytes(2, "big")
            body_out += chunk_data
            packets.append(write_varint(packet_id) + bytes(body_out))
        return packets

    def _map_name(self, category: str, value: str, max_len: int) -> str:
        if len(value) <= max_len:
            return value
        cache = self._name_maps.setdefault(category, {})
        if value in cache:
            return cache[value]
        prefix = category[:1].lower() if category else "x"
        suffix_len = max_len - len(prefix)
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
        mapped = prefix + digest[:suffix_len]
        cache[value] = mapped
        return mapped

    @staticmethod
    def _truncate_string(value: str, max_len: int) -> str:
        if len(value) <= max_len:
            return value
        return value[:max_len]

    async def _join_yggdrasil(self, server_hash: str) -> None:
        profile = self.config.ygg_profile
        data = self.config.ygg_data
        if profile is None or data is None:
            return
        try:
            servers = StandardYggdrasil.auth_servers()
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.warning("Yggdrasil auth server list failed: %s", exc)
            return
        import random

        random.shuffle(servers)
        strategies = self._build_auth_strategies(profile)
        wire_variants = [
            ("crc-be-ctr0", 0, False),
            ("crc-le-ctr0", 0, True),
            ("crc-be-ctr1", 1, False),
            ("crc-le-ctr1", 1, True),
        ]
        failures: list[str] = []
        successes: list[str] = []

        def _summarize(entries: list[str], limit: int = 6) -> str:
            if not entries:
                return "none"
            shown = entries[:limit]
            extra = len(entries) - limit
            return "; ".join(shown) + (f" (+{extra} more)" if extra > 0 else "")

        for strategy_name, strategy_profile in strategies:
            for host, port in servers:
                for wire_name, counter_start, crc_le in wire_variants:
                    ygg = StandardYggdrasil(data, host, port)
                    ok, err = await asyncio.to_thread(
                        ygg.join_server,
                        strategy_profile,
                        server_hash,
                        False,
                        counter_start=counter_start,
                        crc_little_endian=crc_le,
                    )
                    if ok:
                        successes.append(f"{host}:{port} {strategy_name}/{wire_name}")
                        self.logger.info(
                            "Yggdrasil join success (%s/%s) via %s:%s",
                            strategy_name,
                            wire_name,
                            host,
                            port,
                        )
                        if failures:
                            self.logger.info(
                                "Yggdrasil attempts before success (%d): %s",
                                len(failures),
                                _summarize(failures),
                            )
                        self.logger.info("Yggdrasil auth success list: %s", _summarize(successes))
                        return
                    failures.append(f"{host}:{port} {strategy_name}/{wire_name}")
                    self.logger.debug(
                        "Yggdrasil join failed (%s/%s) via %s:%s: %s",
                        strategy_name,
                        wire_name,
                        host,
                        port,
                        err,
                    )
        self.logger.warning("Yggdrasil join failed across all strategies")
        if failures:
            self.logger.warning("Yggdrasil auth failed list (%d): %s", len(failures), _summarize(failures))

    @staticmethod
    def _build_auth_strategies(profile: GameProfile) -> list[tuple[str, GameProfile]]:
        strategies: list[tuple[str, GameProfile]] = [("skip32+xor", profile)]
        user = profile.user
        if user.use_skip32:
            strategies.append((
                "plain+xor",
                GameProfile(
                    game_id=profile.game_id,
                    game_version=profile.game_version,
                    bootstrap_md5=profile.bootstrap_md5,
                    dat_file_md5=profile.dat_file_md5,
                    mods=profile.mods,
                    user=UserProfile(
                        user_id=user.user_id,
                        user_token=user.user_token,
                        use_skip32=False,
                        use_xor=user.use_xor,
                        use_hex_token=user.use_hex_token,
                    ),
                ),
            ))
        if _looks_like_hex_32(user.user_token):
            strategies.append((
                "skip32+hex",
                GameProfile(
                    game_id=profile.game_id,
                    game_version=profile.game_version,
                    bootstrap_md5=profile.bootstrap_md5,
                    dat_file_md5=profile.dat_file_md5,
                    mods=profile.mods,
                    user=UserProfile(
                        user_id=user.user_id,
                        user_token=user.user_token,
                        use_skip32=user.use_skip32,
                        use_xor=False,
                        use_hex_token=True,
                    ),
                ),
            ))
            strategies.append((
                "plain+hex",
                GameProfile(
                    game_id=profile.game_id,
                    game_version=profile.game_version,
                    bootstrap_md5=profile.bootstrap_md5,
                    dat_file_md5=profile.dat_file_md5,
                    mods=profile.mods,
                    user=UserProfile(
                        user_id=user.user_id,
                        user_token=user.user_token,
                        use_skip32=False,
                        use_xor=False,
                        use_hex_token=True,
                    ),
                ),
            ))
        return strategies


def _parse_encryption_request(
    body: bytes, protocol: int
) -> tuple[str, bytes, bytes, Optional[bool]]:
    offset = 0
    server_id, size = read_string(body, offset, 20)
    offset += size
    if protocol == ProtocolVersion.V1076:
        key_len, size = read_ushort(body, offset)
        offset += size
        public_key, size = read_bytes(body, offset, key_len)
        offset += size
        token_len, size = read_ushort(body, offset)
        offset += size
        verify_token, size = read_bytes(body, offset, token_len)
        offset += size
        return server_id, public_key, verify_token, None
    public_key, size = read_byte_array(body, offset)
    offset += size
    verify_token, size = read_byte_array(body, offset)
    offset += size
    should_auth = None
    if protocol in (ProtocolVersion.V1206, ProtocolVersion.V1210):
        should_auth, size = read_bool(body, offset)
        offset += size
    return server_id, public_key, verify_token, should_auth


def _build_encryption_response(protocol: int, secret_key: bytes, public_key: bytes, verify_token: bytes) -> bytes:
    rsa_key = RSA.import_key(public_key)
    cipher = PKCS1_v1_5.new(rsa_key)
    secret_enc = cipher.encrypt(secret_key)
    token_enc = cipher.encrypt(verify_token)
    if protocol == ProtocolVersion.V1076:
        return write_ushort(len(secret_enc)) + secret_enc + write_ushort(len(token_enc)) + token_enc
    return write_byte_array(secret_enc) + write_byte_array(token_enc)


def _compute_server_hash(server_id: str, secret_key: bytes, public_key: bytes) -> str:
    raw = server_id.encode("iso-8859-1") + secret_key + public_key
    digest = hashlib.sha1(raw).digest()
    num = int.from_bytes(digest, byteorder="big", signed=True)
    if num < 0:
        value = format(-num, "x").lstrip("0")
        return f"-{value}" if value else "-0"
    value = format(num, "x").lstrip("0")
    return value or "0"


def _with_fml_marker(protocol: int, address: str) -> str:
    if protocol > ProtocolVersion.V1180:
        if protocol <= ProtocolVersion.V1206:
            return f"{address}\x00FML3\x00"
        return f"{address}\x00FORGE"
    if protocol <= ProtocolVersion.V1122:
        return f"{address}\x00FML\x00"
    return f"{address}\x00FML2\x00"


def _safe_b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _looks_like_hex_32(value: str) -> bool:
    if len(value) != 32:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in value)


# -----------------------------------------------------------------------------
# CLI helpers + main()
# -----------------------------------------------------------------------------


def _read_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _extract_cookie(raw_text: str) -> str:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("{") and "sauth_json" in line:
            return line
    for idx, line in enumerate(lines):
        if line.lower().startswith("cookies"):
            for next_line in lines[idx + 1 :]:
                if next_line.startswith("{") and "sauth_json" in next_line:
                    return next_line
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw_text[start : end + 1].strip()
    raise ValueError("could not locate sauth_json in cookie file")


def _prompt(text: str) -> str:
    print(text)
    return input("> ").strip()


def _select_server(client: WPFLauncherClient) -> str:
    servers: List[NetGameItem] = []
    offset = 0
    page_size = 10
    while True:
        if len(servers) == offset:
            servers.extend(client.get_available_servers(offset, page_size))
        print("\nAvailable servers:")
        for idx, server in enumerate(servers, start=1):
            print(f"{idx}. {server.name} (id={server.entity_id})")
        choice = _prompt("Select server number, or 'n' for next page, or 's' to search in loaded list")
        if choice.lower() == "n":
            offset += page_size
            continue
        if choice.lower() == "s":
            keyword = _prompt("Search keyword")
            matches = [s for s in servers if keyword.lower() in s.name.lower() or keyword.lower() in s.brief_summary.lower()]
            if not matches:
                print("No matches in loaded list. Use 'n' to load more.")
                continue
            print("Matches:")
            for item in matches:
                idx = servers.index(item) + 1
                print(f"{idx}. {item.name} (id={item.entity_id})")
            continue
        try:
            index = int(choice)
        except ValueError:
            print("Invalid selection.")
            continue
        if index < 1 or index > len(servers):
            print("Out of range.")
            continue
        return servers[index - 1].entity_id


def _select_character(client: WPFLauncherClient, game_id: str) -> str:
    characters = client.get_characters(game_id)
    if characters:
        print("\nCharacters:")
        for idx, character in enumerate(characters, start=1):
            print(f"{idx}. {character.name}")
        choice = _prompt("Select character number or type 'new' to create")
        if choice.lower() != "new":
            try:
                index = int(choice)
                if 1 <= index <= len(characters):
                    return characters[index - 1].name
            except ValueError:
                pass
        print("Creating new character.")
    name = _prompt("Character name")
    if not name:
        raise ValueError("character name required")
    client.create_character(game_id, name)
    return name


def _try_yggdrasil(client: WPFLauncherClient, game_id: str, version_name: str, user_id: str, user_token: str) -> None:
    server_id = _prompt("Optional: enter serverId for Yggdrasil join (empty to skip)")
    if not server_id:
        return
    info = client.fetch_fantnel_info()
    if not info.crc_salt:
        print("CRC salt unavailable, cannot run Yggdrasil join.")
        return
    try:
        pair = get_md5_pair(version_name)
    except KeyError as exc:
        print(str(exc))
        return
    profile = GameProfile(
        game_id=game_id,
        game_version=version_name,
        bootstrap_md5=pair.bootstrap_md5,
        dat_file_md5=pair.dat_file_md5,
        mods=ModList([]),
        user=UserProfile(user_id=int(user_id), user_token=user_token),
    )
    ygg_data = YggdrasilData(launcher_version=client.game_version, channel="netease", crc_salt=info.crc_salt)
    host, port = random.choice(StandardYggdrasil.auth_servers())
    ygg = StandardYggdrasil(ygg_data, host, port)
    ok, err = ygg.join_server(profile, server_id)
    if ok:
        print("Yggdrasil join success.")
    else:
        print(f"Yggdrasil join failed: {err}")


def _build_yggdrasil_profile(
    client: WPFLauncherClient,
    game_id: str,
    version_name: str,
    user_id: str,
    user_token: str,
) -> Tuple[GameProfile | None, YggdrasilData | None]:
    info = client.fetch_fantnel_info()
    if not info.crc_salt:
        print("CRC salt unavailable; proxy will skip Yggdrasil join.")
        return None, None
    try:
        pair = get_md5_pair(version_name)
    except KeyError as exc:
        print(str(exc))
        return None, None
    try:
        mod_list = client.get_mod_list(game_id, version_name, include_assets=True)
    except ApiError as exc:
        print(f"Mod list fetch failed: {exc} (using empty list)")
        mod_list = ModList([])
    profile = GameProfile(
        game_id=game_id,
        game_version=version_name,
        bootstrap_md5=pair.bootstrap_md5,
        dat_file_md5=pair.dat_file_md5,
        mods=mod_list,
        user=UserProfile(user_id=int(user_id), user_token=user_token),
    )
    ygg_data = YggdrasilData(launcher_version=client.game_version, channel="netease", crc_salt=info.crc_salt)
    return profile, ygg_data


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    client = WPFLauncherClient()
    print("Camellia CLI (standalone single-file)")
    print("1) Cookie login (file)")
    print("2) 4399 login (account)")
    mode = _prompt("Choose login mode")

    try:
        if mode == "1":
            path = _prompt("Cookie file path [test_sauth]")
            if not path:
                path = "test_sauth"
            raw = _read_file(path)
            cookie = _extract_cookie(raw)
            auth = client.login_with_cookie(cookie)
        elif mode == "2":
            username = _prompt("4399 username")
            password = _prompt("4399 password")
            sauth_json = login_with_password(username, password)
            auth = client.login_with_cookie(sauth_json)
        else:
            print("Unsupported login mode.")
            return 1
    except (ApiError, LoginError, ValueError, RuntimeError) as exc:
        print(f"Login failed: {exc}")
        return 1

    print(f"Login success. Entity ID: {auth.entity_id} (channel={auth.login_channel})")

    game_id = _select_server(client)
    detail = client.get_server_detail(game_id)
    address = client.get_server_address(game_id)
    versions = [v.name for v in detail.mc_versions if v.name]
    if SUPPORTED_MC_VERSION in versions:
        version_name = SUPPORTED_MC_VERSION
    elif versions:
        print(f"Unsupported server version: {versions[0]} (only {SUPPORTED_MC_VERSION} supported)")
        return 1
    else:
        print(f"Server did not provide mc version; only {SUPPORTED_MC_VERSION} supported")
        return 1
    print(f"Server version: {version_name}")

    character_name = _select_character(client, game_id)
    print(f"Selected character: {character_name}")

    client.game_start(game_id)
    print("GameStart OK.")

    host = address.host or detail.server_address
    port = address.port or detail.server_port
    if host and port:
        print(f"Remote server: {host}:{port}")
    else:
        print("Server address not available from API.")

    print("\nConnection mode:")
    print("1) No proxy (direct connect)")
    print("2) Local proxy")
    mode = _prompt("Choose mode")

    if mode == "1":
        if host and port:
            print(f"Connect your client to: {host}:{port}")
        if version_name:
            _try_yggdrasil(client, game_id, version_name, auth.entity_id, auth.token)
        else:
            print("Server version unavailable; skipping Yggdrasil join.")
        return 0

    if mode != "2":
        print("Unknown mode.")
        return 1

    if not (host and port):
        print("Missing server address; cannot start proxy.")
        return 1

    local_host = _prompt("Local listen host [127.0.0.1]") or "127.0.0.1"
    local_port_raw = _prompt("Local listen port [25570]") or "25570"
    try:
        local_port = int(local_port_raw)
    except ValueError:
        print("Invalid port.")
        return 1

    profile = None
    ygg_data = None
    if version_name:
        profile, ygg_data = _build_yggdrasil_profile(client, game_id, version_name, auth.entity_id, auth.token)
    else:
        print("Server version unavailable; proxy will skip Yggdrasil join.")

    print(f"Starting proxy at {local_host}:{local_port} -> {host}:{port}")
    print("Connect your Minecraft client to the local address above.")

    proxy = MinecraftProxy(
        ProxyConfig(
            listen_host=local_host,
            listen_port=local_port,
            forward_host=host,
            forward_port=port,
            nickname=character_name,
            game_id=game_id,
            ygg_profile=profile,
            ygg_data=ygg_data,
        )
    )
    try:
        asyncio.run(proxy.serve())
    except KeyboardInterrupt:
        print("Proxy stopped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
