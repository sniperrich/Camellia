from __future__ import annotations

import os
from pathlib import Path

_BYPASS_FILE_NAME = ".camellia_auth_bypass.local"
_TRUE_VALUES = {"1", "true", "on", "yes", "y", "bypass", "skip", "disable", "disabled"}
_FORCE_BYPASS_ENABLED = True


def _resolve_bypass_file() -> Path:
    custom = os.getenv("CAMELLIA_AUTH_BYPASS_FILE", "").strip()
    if custom:
        return Path(custom).expanduser()
    return Path.cwd() / _BYPASS_FILE_NAME


def _read_switch_value(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""
    for raw in content.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        return line.lower()
    return ""


def get_auth_bypass_status() -> tuple[bool, str]:
    if _FORCE_BYPASS_ENABLED:
        return True, "code:default_enabled"

    env_value = os.getenv("CAMELLIA_AUTH_BYPASS", "").strip().lower()
    if env_value in _TRUE_VALUES:
        return True, "env:CAMELLIA_AUTH_BYPASS"

    flag_path = _resolve_bypass_file()
    if not flag_path.exists():
        return False, ""

    # Empty file means enabled; otherwise parse first non-comment line.
    value = _read_switch_value(flag_path)
    if value == "" or value in _TRUE_VALUES:
        return True, f"file:{flag_path}"
    return False, ""
