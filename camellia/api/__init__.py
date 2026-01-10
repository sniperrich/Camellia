"""API clients for NetEase/WPFLauncher services."""

from .http_client import HttpClient, HttpResponse, load_cookie_jar
from .mgb_sdk import MgbSdk
from .n4399 import LoginError, login_with_password
from .wpf_launcher import ApiError, ModFetchError, WPFLauncherClient
from .x19 import X19Api, get_latest_version, get_patch_versions

__all__ = [
    "ApiError",
    "HttpClient",
    "HttpResponse",
    "LoginError",
    "MgbSdk",
    "ModFetchError",
    "WPFLauncherClient",
    "X19Api",
    "get_latest_version",
    "get_patch_versions",
    "load_cookie_jar",
    "login_with_password",
]
