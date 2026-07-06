"""Pure helpers for saved-account login fallback rules."""

from __future__ import annotations

from .storage import SavedAccount


def build_saved_phone_login_error(account: SavedAccount, previous_error: str = "") -> str:
    del account  # The rule is mode-wide; the record is accepted for future extension.
    base = "手机号账号的旧密码模式已废弃。请手动短信验证码登录。"
    if previous_error:
        return f"{previous_error}；{base}"
    return base
