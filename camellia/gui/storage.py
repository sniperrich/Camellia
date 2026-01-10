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
    remember_password: bool = False
    last_used: float = 0.0

    @classmethod
    def new_cookie(cls, path: str) -> "SavedAccount":
        return cls(id=str(uuid.uuid4()), mode="cookie", cookie_path=path, last_used=time.time())

    @classmethod
    def new_account(cls, username: str, password: str, remember: bool) -> "SavedAccount":
        return cls(
            id=str(uuid.uuid4()),
            mode="account",
            username=username,
            password=password if remember else "",
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
        if self.mode == "cookie":
            name = Path(self.cookie_path).name if self.cookie_path else "空路径"
            return f"登录凭据文件：{name}"
        name = self.username or "未知账号"
        return f"4399账号：{name}"


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
