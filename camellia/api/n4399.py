import json
import time
import urllib.parse
from http.cookiejar import CookieJar
from typing import Dict

from .http_client import HttpClient, load_cookie_jar
from .mgb_sdk import MgbSdk


class LoginError(RuntimeError):
    pass


_DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)


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
    params = {
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
    return params


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
    if response.status >= 400:
        raise LoginError(f"check login failed: http {response.status}")
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
    if response.status >= 400:
        raise LoginError(f"sdk info failed: http {response.status}")
    text = response.text().strip()
    if text.startswith("(") and text.endswith(")"):
        text = text[1:-1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LoginError("sdk info returned invalid json") from exc
    sdk_login_data = data.get("data", {}).get("sdk_login_data", "")
    params = urllib.parse.parse_qs(sdk_login_data, keep_blank_values=True)
    return {k: v[0] for k, v in params.items() if v}


def login_with_password(username: str, password: str) -> str:
    jar = load_cookie_jar()
    client = HttpClient(
        cookie_jar=jar,
        default_headers={
            "User-Agent": _DEFAULT_BROWSER_UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        },
    )
    response = client.post_form(
        "https://ptlogin.4399.com/ptlogin/login.do?v=1",
        _build_login_params(username, password),
    )
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

    import uuid

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
