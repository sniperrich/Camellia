from __future__ import annotations

import base64
import hashlib
import json
import os
import random
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from Crypto.Cipher import AES
from Crypto.Util.Padding import pad

from ..config import DEFAULT_API_USER_AGENT
from .http_client import HttpClient
from .x19 import get_latest_version


MPAY_BASE = "https://service.mkey.163.com"
PROJECT_ID = "aecfrxodyqaaaajp-g-x19"
DEFAULT_SDK_VERSION = "4.17.2"
DEFAULT_APP_CHANNEL = "a50_sdk_cn"
DEFAULT_AIM_INFO = "{\"aim\":\"127.0.0.1\",\"country\":\"CN\",\"tz\":\"+0800\",\"tzid\":\"\"}"


class NeteaseLoginError(RuntimeError):
    pass


@dataclass
class MPayDevice:
    device_id: str
    key: str


class MPayClient:
    def __init__(self, project_id: str | None = None, game_version: str | None = None) -> None:
        self.project_id = project_id or PROJECT_ID
        self.game_version = game_version or get_latest_version()
        self._client = HttpClient(
            base_url=MPAY_BASE,
            # Match common launcher behavior: MPay requests are typically sent with
            # a launcher-like UA containing the game version.
            default_headers={"User-Agent": f"WPFLauncher/{self.game_version}" or DEFAULT_API_USER_AGENT},
        )
        self._cache_dir = Path.home() / ".camellia" / "mpay"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._unique_id = self._load_or_create_unique_id()
        self.device: MPayDevice | None = None

    def initialize_device(self) -> MPayDevice:
        if self.device is not None:
            return self.device
        self.device = self._load_or_create_device()
        return self.device

    def login_with_email(self, email: str, password: str) -> dict[str, Any]:
        device = self.initialize_device()
        encrypted = self._encrypt_login_params(email, password, device.key)
        params = self._build_base_params()
        params.update(
            {
                "opt_fields": "nickname,avatar,realname_status,mobile_bind_status,mask_related_mobile,related_login_status",
                "params": encrypted,
                "un": _encode_base64(email),
            }
        )
        path = f"/mpay/games/{self.project_id}/devices/{device.device_id}/users"
        resp = self._client.post_form(path, params)
        data = resp.text()
        if resp.status >= 400:
            raise NeteaseLoginError(_format_mpay_error(resp.status, data, fallback="网易邮箱登录失败"))
        return json.loads(data)

    def send_sms_code(self, phone_number: str) -> bool:
        device = self.initialize_device()
        params = self._build_base_params()
        params.update(
            {
                "device_id": device.device_id,
                "mobile": phone_number,
                "urs_udid": self._unique_id,
            }
        )
        resp = self._client.post_form("/mpay/api/users/login/mobile/get_sms", params)
        if resp.status >= 400:
            raise NeteaseLoginError(_format_mpay_error(resp.status, resp.text(), fallback="发送验证码失败"))
        return True

    def verify_sms_code(self, phone_number: str, code: str) -> dict[str, Any]:
        device = self.initialize_device()
        params = self._build_base_params()
        params.update(
            {
                "device_id": device.device_id,
                "mobile": phone_number,
                "smscode": code,
                "login_for": "1",
                "urs_udid": self._unique_id,
                "up_content": "",
            }
        )
        resp = self._client.post_form("/mpay/api/users/login/mobile/verify_sms", params)
        data = resp.text()
        if resp.status >= 400:
            raise NeteaseLoginError(_format_mpay_error(resp.status, data, fallback="验证码验证失败"))
        return json.loads(data)

    def complete_sms_login(self, phone_number: str, ticket: str) -> dict[str, Any]:
        device = self.initialize_device()
        params = self._build_base_params()
        params.update(
            {
                "device_id": device.device_id,
                "login_for": "1",
                "opt_fields": "nickname,avatar,realname_status,mobile_bind_status,mask_related_mobile,related_login_status",
                "ticket": ticket,
                "urs_udid": self._unique_id,
            }
        )
        encoded = _encode_base64(phone_number)
        resp = self._client.post_form(f"/mpay/api/users/login/mobile/finish?un={encoded}", params)
        data = resp.text()
        if resp.status >= 400:
            raise NeteaseLoginError(_format_mpay_error(resp.status, data, fallback="手机号登录失败"))
        return json.loads(data)

    def _encrypt_login_params(self, email: str, password: str, device_key_hex: str) -> str:
        payload = {
            "username": email,
            "password": _md5_hex(password),
            "unique_id": self._unique_id,
        }
        raw = json.dumps(payload, ensure_ascii=False)
        key = bytes.fromhex(device_key_hex)
        cipher = AES.new(key, AES.MODE_ECB)
        encrypted = cipher.encrypt(pad(raw.encode("utf-8"), 16))
        return encrypted.hex()

    def _build_base_params(self) -> dict[str, str]:
        return {
            "app_channel": "netease",
            "app_mode": "2",
            "app_type": "games",
            "arch": "win_x64",
            "cv": f"c{DEFAULT_SDK_VERSION}",
            "mcount_app_key": "EEkEEXLymcNjM42yLY3Bn6AO15aGy4yq",
            "mcount_transaction_id": "0",
            "process_id": str(os.getpid()),
            "sv": "10.0.22621",
            "updater_cv": "c1.0.0",
            "game_id": self.project_id,
            "gv": self.game_version,
        }

    def _load_or_create_device(self) -> MPayDevice:
        cache_path = self._cache_dir / f"{self.project_id}.device"
        if cache_path.exists():
            try:
                payload = json.loads(cache_path.read_text(encoding="utf-8"))
                device = payload.get("device") or payload
                device_id = device.get("id") or device.get("device_id")
                key = device.get("key")
                if device_id and key:
                    return MPayDevice(device_id=device_id, key=key)
            except json.JSONDecodeError:
                pass
        device = self._register_device()
        cache_path.write_text(json.dumps({"device": {"id": device.device_id, "key": device.key}}), encoding="utf-8")
        return device

    def _register_device(self) -> MPayDevice:
        params = self._build_device_registration_params()
        resp = self._client.post_form(f"/mpay/games/{self.project_id}/devices", params)
        if resp.status >= 400:
            raise NeteaseLoginError(_format_mpay_error(resp.status, resp.text(), fallback="设备注册失败"))
        payload = json.loads(resp.text())
        device = payload.get("device", payload)
        device_id = device.get("id")
        key = device.get("key")
        if not device_id or not key:
            raise NeteaseLoginError("invalid device response")
        return MPayDevice(device_id=device_id, key=key)

    def _build_device_registration_params(self) -> dict[str, str]:
        return {
            **self._build_base_params(),
            "unique_id": self._unique_id,
            "brand": "Microsoft",
            "device_model": "pc_mode",
            "device_name": f"PC-{_random_string(12)}",
            "device_type": "Computer",
            "init_urs_device": "0",
            "mac": _random_mac(),
            "resolution": "1920x1080",
            "system_name": "windows",
            "system_version": "10.0.22621",
        }

    def _load_or_create_unique_id(self) -> str:
        cache_path = self._cache_dir / f"{self.project_id}.id"
        if cache_path.exists():
            return cache_path.read_text(encoding="utf-8").strip()
        unique_id = uuid.uuid4().hex
        cache_path.write_text(unique_id, encoding="utf-8")
        return unique_id


def build_sauth_json(user_id: str, token: str, device_id: str, *, login_channel: str = "netease") -> str:
    payload = {
        "gameid": "x19",
        "login_channel": login_channel or "netease",
        "app_channel": DEFAULT_APP_CHANNEL,
        "platform": "pc",
        "sdkuid": user_id,
        "sessionid": token,
        "sdk_version": DEFAULT_SDK_VERSION,
        "udid": device_id,
        "deviceid": device_id,
        "aim_info": DEFAULT_AIM_INFO,
        "client_login_sn": uuid.uuid4().hex.upper(),
        "gas_token": "",
        "extra_channel": "",
        "source_platform": "pc",
        "ip": "",
        "get_access_token": "1",
    }
    return json.dumps(payload, ensure_ascii=False)


def login_with_netease_email(email: str, password: str) -> str:
    if not email or not password:
        raise NeteaseLoginError("邮箱或密码不能为空")
    client = MPayClient()
    wrapper = client.login_with_email(email, password)
    user = wrapper.get("user") or {}
    user_id = user.get("id")
    token = user.get("token")
    login_channel = user.get("login_channel") or "netease"
    if not user_id or not token:
        raise NeteaseLoginError("网易邮箱登录失败")
    return build_sauth_json(
        user_id,
        token,
        client.device.device_id if client.device else "",
        login_channel=login_channel,
    )


def send_netease_sms(phone_number: str) -> bool:
    if not phone_number:
        raise NeteaseLoginError("手机号不能为空")
    client = MPayClient()
    return client.send_sms_code(phone_number)


def login_with_netease_phone(phone_number: str, code: str) -> str:
    if not phone_number or not code:
        raise NeteaseLoginError("手机号或验证码不能为空")
    client = MPayClient()
    ticket_wrapper = client.verify_sms_code(phone_number, code)
    ticket = ticket_wrapper.get("ticket")
    if not ticket:
        raise NeteaseLoginError("验证码无效或已过期")
    user_wrapper = client.complete_sms_login(phone_number, ticket)
    user = user_wrapper.get("user") or {}
    user_id = user.get("id")
    token = user.get("token")
    login_channel = user.get("login_channel") or "netease"
    if not user_id or not token:
        raise NeteaseLoginError("手机号登录失败")
    return build_sauth_json(
        user_id,
        token,
        client.device.device_id if client.device else "",
        login_channel=login_channel,
    )


def _format_mpay_error(status: int, body: str, *, fallback: str) -> str:
    """Normalize MPay error payload for UI consumption.

    The GUI tries to parse JSON out of exception strings to show a rich dialog
    (reason/code/verify_url). Returning a pure JSON string here makes that
    robust and avoids losing server-provided error details.
    """
    text = (body or "").strip()
    try:
        payload = json.loads(text) if text.startswith("{") and text.endswith("}") else None
    except Exception:  # pylint: disable=broad-except
        payload = None

    if isinstance(payload, dict):
        reason = payload.get("reason") or payload.get("message") or ""
        verify_url = payload.get("verify_url") or payload.get("verifyUrl") or ""
        code = payload.get("code")
        normalized: dict[str, Any] = {}
        if reason:
            normalized["reason"] = reason
        if isinstance(code, int):
            normalized["code"] = code
        if verify_url:
            normalized["verify_url"] = verify_url
        if normalized:
            return json.dumps(normalized, ensure_ascii=False)

    # Non-JSON or unexpected payload: still return JSON for the UI.
    raw = text
    if len(raw) > 400:
        raw = raw[:400] + "..."
    return json.dumps(
        {
            "reason": f"{fallback}（HTTP {status}）",
            "code": int(status),
            "verify_url": "",
            "raw": raw,
        },
        ensure_ascii=False,
    )


def _md5_hex(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _encode_base64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _random_string(length: int) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyz0123456789"
    return "".join(random.choice(alphabet) for _ in range(length))


def _random_mac() -> str:
    mac = [random.randint(0, 255) for _ in range(6)]
    mac[0] &= 0xFE
    mac[0] |= 0x02
    return "".join(f"{b:02X}" for b in mac)
