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
DEFAULT_SDK_VERSION = "4.2.0"
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
            default_headers={"User-Agent": DEFAULT_API_USER_AGENT},
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
            raise NeteaseLoginError(f"email login failed: {data}")
        return json.loads(data)

    def send_sms_code(self, phone_number: str) -> bool:
        device = self.initialize_device()
        params = self._build_base_params()
        params.update(
            {
                "device_id": device.device_id,
                "mobile": phone_number,
            }
        )
        resp = self._client.post_form("/mpay/api/users/login/mobile/get_sms", params)
        return resp.status < 400

    def verify_sms_code(self, phone_number: str, code: str) -> dict[str, Any] | None:
        device = self.initialize_device()
        params = self._build_base_params()
        params.update(
            {
                "device_id": device.device_id,
                "mobile": phone_number,
                "smscode": code,
                "up_content": "",
            }
        )
        resp = self._client.post_form("/mpay/api/users/login/mobile/verify_sms", params)
        if resp.status >= 400:
            return None
        return json.loads(resp.text())

    def complete_sms_login(self, phone_number: str, ticket: str) -> dict[str, Any] | None:
        device = self.initialize_device()
        params = self._build_base_params()
        params.update(
            {
                "device_id": device.device_id,
                "opt_fields": "nickname,avatar,realname_status,mobile_bind_status,mask_related_mobile,related_login_status",
                "ticket": ticket,
            }
        )
        encoded = _encode_base64(phone_number)
        resp = self._client.post_form(f"/mpay/api/users/login/mobile/finish?un={encoded}", params)
        if resp.status >= 400:
            return None
        return json.loads(resp.text())

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
            "cv": "c4.2.0",
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
            raise NeteaseLoginError(f"device register failed: {resp.text()}")
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


def build_sauth_json(user_id: str, token: str, device_id: str) -> str:
    payload = {
        "gameid": "x19",
        "login_channel": "netease",
        "app_channel": "netease",
        "platform": "pc",
        "sdkuid": user_id,
        "sessionid": token,
        "sdk_version": DEFAULT_SDK_VERSION,
        "udid": uuid.uuid4().hex.upper(),
        "deviceid": device_id,
        "aim_info": DEFAULT_AIM_INFO,
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
    if not user_id or not token:
        raise NeteaseLoginError("网易邮箱登录失败")
    return build_sauth_json(user_id, token, client.device.device_id if client.device else "")


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
    if not ticket_wrapper:
        raise NeteaseLoginError("验证码验证失败")
    ticket = ticket_wrapper.get("ticket")
    if not ticket:
        raise NeteaseLoginError("验证码无效或已过期")
    user_wrapper = client.complete_sms_login(phone_number, ticket)
    if not user_wrapper:
        raise NeteaseLoginError("手机号登录失败")
    user = user_wrapper.get("user") or {}
    user_id = user.get("id")
    token = user.get("token")
    if not user_id or not token:
        raise NeteaseLoginError("手机号登录失败")
    return build_sauth_json(user_id, token, client.device.device_id if client.device else "")


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
