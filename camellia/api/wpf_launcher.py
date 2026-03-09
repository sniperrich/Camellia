import hashlib
import time
import json
import logging
import os
import subprocess
import tempfile
from typing import Any, Dict, List, Optional
from urllib.request import urlopen
from pathlib import Path

from ..config import (
    DEFAULT_API_USER_AGENT,
    FANTNEL_INFO_URL,
    X19_API_GATEWAY,
    X19_CORE,
    X19_MCL,
)
from ..crypto.http_crypto import compute_dynamic_token, http_decrypt, http_encrypt, load_cookie_json
from .http_client import HttpClient
from ..models.entities import (
    AuthOtp,
    FantnelInfo,
    GameCharacter,
    GameSkin,
    LoginOtp,
    NetGameDetail,
    NetGameItem,
    NetGameServerAddress,
)
from .mgb_sdk import MgbSdk
from .x19 import get_latest_version
from ..mc.yggdrasil import Mod, ModList


class ApiError(RuntimeError):
    pass


class ModFetchError(RuntimeError):
    pass


_GAME_VERSION_IDS = {
    "1.6.4": 1006004,
    "1.7.2": 1007002,
    "1.7.10": 1007010,
    "1.8": 1008000,
    "1.8.8": 1008008,
    "1.8.9": 1008009,
    "1.9.4": 1009004,
    "1.10.2": 1010002,
    "1.11.2": 1011002,
    "1.12": 1012000,
    "1.12.2": 1012002,
    "1.13.2": 1013002,
    "1.14.3": 1014003,
    "1.15": 1015000,
    "1.16": 1016000,
    "1.18": 1018000,
    "1.19.2": 1019002,
    "1.20": 1020000,
    "1.20.6": 1020006,
    "1.21": 1021000,
}


def _json_dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False)


def _generate_hex_string(length: int) -> str:
    import os

    return os.urandom(length).hex().upper()


def _generate_random_mac() -> str:
    import os

    mac = bytearray(os.urandom(6))
    mac[0] &= 0xFE
    mac[0] |= 0x02
    return "".join(f"{b:02X}" for b in mac)


class WPFLauncherClient:
    def __init__(self) -> None:
        self._logger = logging.getLogger("camellia.modinfo")
        self.game_version = get_latest_version()
        self.core = HttpClient(
            base_url=X19_CORE,
            default_headers={"User-Agent": f"WPFLauncher/{self.game_version}"},
        )
        self.api = HttpClient(
            base_url=X19_API_GATEWAY,
            default_headers={"User-Agent": DEFAULT_API_USER_AGENT},
        )
        self.mcl = HttpClient(
            base_url=X19_MCL,
            default_headers={"User-Agent": DEFAULT_API_USER_AGENT},
        )
        self.nirvana = HttpClient(
            default_headers={"User-Agent": DEFAULT_API_USER_AGENT},
        )
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
        try:
            cookie = json.loads(sauth_json)
        except json.JSONDecodeError as exc:
            raise ApiError(f"invalid sauth_json: {exc.msg}") from exc
        if not isinstance(cookie, dict):
            raise ApiError("invalid sauth_json: expected JSON object")
        session_id = str(cookie.get("sessionid") or cookie.get("sessionId") or "").strip()
        if "*" in session_id:
            raise ApiError("invalid sauth_json: sessionid appears masked")
        login_channel = cookie.get("login_channel", "netease")
        if login_channel != "netease":
            MgbSdk("x19").auth_session(sauth_json)

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
        response = self.core.post(
            "/authentication-otp",
            data=encrypted,
            content_type="application/octet-stream",
        )
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
        payload = {
            "offset": 0,
            "length": 10,
            "user_id": self.user_id,
            "game_id": game_id,
            "game_type": "2",
        }
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
            data = self._api_post("/game-character/create", payload)
            if data.get("code") not in (None, 0):
                raise ApiError(data.get("message", "create character failed")) from exc
        if data.get("code") not in (None, 0):
            raise ApiError(data.get("message", "create character failed"))

    def fetch_fantnel_info(self) -> FantnelInfo:
        response = self.nirvana.get(FANTNEL_INFO_URL)
        if response.status >= 400:
            raise ApiError(f"fantnel info error {response.status}: {response.text()}")
        try:
            payload = json.loads(response.text())
        except json.JSONDecodeError as exc:
            raise ApiError(f"fantnel info parse error: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ApiError("fantnel info invalid payload")
        info = FantnelInfo.from_dict(payload)
        if not info.crc_salt or not info.game_version:
            raise ApiError("fantnel info missing crc_salt/game_version")
        return info

    def get_free_skins(self, offset: int, length: int = 20) -> List[GameSkin]:
        payload = {
            "is_has": True,
            "item_type": 2,
            "length": length,
            "master_type_id": 10,
            "offset": offset,
            "price_type": 3,
            "secondary_type_id": 31,
        }
        data = self._api_post("/item/query/available", payload)
        base = [GameSkin.from_dict(item) for item in data.get("entities", [])]
        if not base:
            return []
        try:
            details = self._get_skin_details([skin.entity_id for skin in base])
        except Exception:  # pylint: disable=broad-except
            return base
        detail_map = {skin.entity_id: skin for skin in details}
        return [detail_map.get(skin.entity_id, skin) for skin in base]

    def search_free_skins(self, keyword: str, offset: int = 0, length: int = 20) -> List[GameSkin]:
        payload = {
            "is_has": True,
            "is_sync": 0,
            "item_type": 2,
            "keyword": keyword,
            "length": length,
            "master_type_id": 10,
            "offset": offset,
            "price_type": 3,
            "secondary_type_id": "31",
            "sort_type": 1,
            "year": 0,
        }
        data = self._api_post("/item/query/search-by-keyword", payload)
        return [GameSkin.from_dict(item) for item in data.get("entities", [])]

    def set_skin(self, skin_id: str) -> None:
        settings = []
        for game_type in (9, 8, 2, 10, 7):
            settings.append(
                {
                    "client_type": "java",
                    "game_type": game_type,
                    "skin_id": skin_id,
                    "skin_mode": 0,
                    "skin_type": 31,
                }
            )
        payload = {"skin_settings": settings}
        self._api_post("/user-game-skin-multi", payload)

    def _get_skin_details(self, entity_ids: List[str]) -> List[GameSkin]:
        if not entity_ids:
            return []
        payload = {
            "channel_id": 11,
            "entity_ids": entity_ids,
            "is_has": True,
            "with_price": True,
            "with_title_image": True,
        }
        data = self._api_post("/item/query/search-by-ids", payload)
        return [GameSkin.from_dict(item) for item in data.get("entities", [])]

    def get_mod_list(self, game_id: str, version_name: str, include_assets: bool = True) -> ModList:
        mods: Dict[str, Mod] = {}
        core_count = 0
        asset_count = 0
        version_id = _GAME_VERSION_IDS.get(version_name)
        if version_id is None:
            self._logger.warning("Unknown game version for core mods: %s", version_name)
        else:
            core = self._api_post(
                "/game-auth-item-list/query/search-by-game",
                {"mc_version_id": version_id, "game_type": 2},
            )
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
                        mods[mod_path] = Mod(
                            modPath=mod_path,
                            name="",
                            id=mod_path,
                            iid=item_id,
                            md5=str(jar_md5).upper(),
                            version="",
                        )
            core_count = len(mods)

        if include_assets:
            try:
                asset_mods = self._get_server_asset_mods(game_id)
                asset_count = len(asset_mods)
                mods.update(asset_mods)
            except ModFetchError as exc:
                self._logger.warning("Server asset mods skipped: %s", exc)

        self._logger.info(
            "Mod list built: game_id=%s version=%s core=%s assets=%s total=%s",
            game_id,
            version_name,
            core_count,
            asset_count,
            len(mods),
        )
        return ModList(list(mods.values()))

    def _get_server_asset_mods(self, game_id: str) -> Dict[str, Mod]:
        response = self._mcl_post(
            "/user-item-download-v2",
            {"item_id": game_id, "length": 0, "offset": 0},
        )
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
            md5 = _file_md5(path).upper()
            iid = filename.split("@", 1)[0]
            mods[filename] = Mod(
                modPath=filename,
                name="",
                id=filename,
                iid=iid,
                md5=md5,
                version="",
            )
        return mods


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
        md5 = str(item.get("md5", "")).upper()
        iid = str(item.get("iid", ""))
        if not mod_path or not md5:
            continue
        mods[mod_path] = Mod(
            modPath=mod_path,
            name=str(item.get("name", "")),
            id=str(item.get("id", mod_path)),
            iid=iid,
            md5=md5,
            version=str(item.get("version", "")),
        )
    return mods or None


def _save_mod_cache(res_url: str, mods: Dict[str, Mod]) -> None:
    try:
        _MOD_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        return
    cache_path = _mod_cache_path(res_url)
    payload = {
        "res_url": res_url,
        "fetched_at": int(time.time()),
        "mods": [mod.to_dict() for mod in mods.values()],
    }
    try:
        cache_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    except OSError:
        return


def _download_file(url: str, dest: str) -> None:
    try:
        with urlopen(url, timeout=20) as resp, open(dest, "wb") as handle:
            while True:
                chunk = resp.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
    except Exception as exc:  # pylint: disable=broad-except
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
