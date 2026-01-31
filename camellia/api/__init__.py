"""API clients for NetEase/WPFLauncher services."""

from .http_client import HttpClient, HttpResponse, load_cookie_jar
from .mgb_sdk import MgbSdk
from .n4399 import LoginError, login_with_password
from .netease import (
    NeteaseLoginError,
    login_with_netease_email,
    login_with_netease_phone,
    send_netease_sms,
)
from .wpf_launcher import ApiError, ModFetchError, WPFLauncherClient
from .x19 import X19Api, get_latest_version, get_patch_versions

__all__ = [
    "ApiError",
    "HttpClient",
    "HttpResponse",
    "LoginError",
    "MgbSdk",
    "ModFetchError",
    "NeteaseLoginError",
    "WPFLauncherClient",
    "X19Api",
    "get_latest_version",
    "get_patch_versions",
    "load_cookie_jar",
    "login_with_password",
    "login_with_netease_email",
    "login_with_netease_phone",
    "send_netease_sms",
]
