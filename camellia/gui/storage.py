"""Local account storage for the GUI."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import List, Optional


_STORE_PATH = Path.home() / ".camellia" / "accounts.json"


@dataclass
class SavedAccount:
    id: str
    mode: str
    cookie_path: str = ""
    username: str = ""
    password: str = ""
    sauth_json: str = ""
    remark: str = ""
    sub_mode: str = ""
    remember_password: bool = False
    last_used: float = 0.0

    @classmethod
    def new_cookie(cls, path: str) -> "SavedAccount":
        return cls(id=str(uuid.uuid4()), mode="cookie", cookie_path=path, last_used=time.time())

    @classmethod
    def new_account(cls, username: str, password: str, remember: bool, remark: str = "") -> "SavedAccount":
        return cls(
            id=str(uuid.uuid4()),
            mode="account",
            username=username,
            password=password if remember else "",
            remark=remark,
            remember_password=remember,
            last_used=time.time(),
        )

    @classmethod
    def new_netease_email(
        cls, email: str, password: str, remember: bool, remark: str = ""
    ) -> "SavedAccount":
        return cls(
            id=str(uuid.uuid4()),
            mode="netease_email",
            username=email,
            password=password if remember else "",
            remark=remark,
            remember_password=remember,
            last_used=time.time(),
        )

    @classmethod
    def new_netease_phone(
        cls,
        phone: str,
        *,
        login_mode: str = "sms",
        password: str = "",
        remember: bool = False,
        remark: str = "",
    ) -> "SavedAccount":
        sub_mode = "password" if login_mode == "password" else "sms"
        return cls(
            id=str(uuid.uuid4()),
            mode="netease_phone",
            username=phone,
            password=password if remember else "",
            remark=remark,
            sub_mode=sub_mode,
            remember_password=remember,
            last_used=time.time(),
        )

    @classmethod
    def new_sauth(
        cls,
        sauth_json: str,
        *,
        remember: bool = True,
        remark: str = "",
        username: str = "",
    ) -> "SavedAccount":
        return cls(
            id=str(uuid.uuid4()),
            mode="sauth",
            username=username or "SAuth",
            sauth_json=sauth_json if remember else "",
            password=sauth_json if remember else "",
            remark=remark,
            remember_password=remember,
            last_used=time.time(),
        )

    @property
    def key(self) -> str:
        if self.mode == "cookie":
            return self.cookie_path
        return self.username

    @property
    def label(self) -> str:
        # One-line label for list UI; keep it compact but include remark if present.
        if self.mode == "cookie":
            name = Path(self.cookie_path).name if self.cookie_path else "空路径"
            base = f"登录凭据文件：{name}"
            return f"{base} · {self.remark}" if self.remark else base
        if self.mode == "netease_email":
            name = self.username or "未知账号"
            base = f"网易邮箱：{name}"
            return f"{base} · {self.remark}" if self.remark else base
        if self.mode == "netease_phone":
            name = self.username or "未知账号"
            prefix = "网易手机号（密码）" if self.sub_mode == "password" else "网易手机号"
            base = f"{prefix}：{name}"
            return f"{base} · {self.remark}" if self.remark else base
        if self.mode == "sauth":
            name = self.username or "SAuth"
            base = f"SAuth：{name}"
            return f"{base} · {self.remark}" if self.remark else base
        name = self.username or "未知账号"
        base = f"4399账号：{name}"
        return f"{base} · {self.remark}" if self.remark else base


def load_accounts(path: Path | None = None) -> List[SavedAccount]:
    path = path or _STORE_PATH
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    accounts = []
    for item in data.get("accounts", []):
        accounts.append(
            SavedAccount(
                id=item.get("id", str(uuid.uuid4())),
                mode=item.get("mode", ""),
                cookie_path=item.get("cookie_path", ""),
                username=item.get("username", ""),
                password=item.get("password", ""),
                sauth_json=item.get("sauth_json", item.get("password", "")),
                remark=item.get("remark", ""),
                sub_mode=item.get("sub_mode", ""),
                remember_password=bool(item.get("remember_password", False)),
                last_used=float(item.get("last_used", 0.0)),
            )
        )
    return accounts


def save_accounts(accounts: List[SavedAccount], path: Path | None = None) -> None:
    path = path or _STORE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"version": 1, "accounts": [asdict(account) for account in accounts]}
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")
