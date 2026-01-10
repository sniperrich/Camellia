import json
from typing import Any, Dict

from ..config import MGBSDK_BASE
from .http_client import HttpClient


class MgbSdk:
    def __init__(self, game_id: str) -> None:
        self.game_id = game_id
        self.client = HttpClient(base_url=MGBSDK_BASE)

    def generate_sauth(
        self,
        device_id: str,
        user_id: str,
        sdk_uid: str,
        session_id: str,
        timestamp: str,
        channel: str,
        platform: str = "pc",
    ) -> str:
        upper = session_id.upper()
        payload: Dict[str, Any] = {
            "app_channel": channel,
            "client_login_sn": device_id.upper(),
            "deviceid": device_id.upper(),
            "gameid": self.game_id,
            "login_channel": channel,
            "sdkuid": sdk_uid,
            "sessionid": upper,
            "timestamp": timestamp,
            "platform": platform,
            "source_platform": platform,
            "udid": device_id.upper(),
            "userid": user_id,
            "aim_info": "{\"aim\":\"127.0.0.1\",\"tz\":\"+0800\",\"tzid\":\"\",\"country\":\"CN\"}",
            "gas_token": "",
            "ip": "127.0.0.1",
            "realname": "{\"realname_type\":\"0\"}",
            "sdk_version": "1.0.0",
        }
        return json.dumps(payload, ensure_ascii=False)

    def auth_session(self, cookie_json: str) -> None:
        response = self.client.post(f"/{self.game_id}/sdk/uni_sauth", data=cookie_json.encode("utf-8"))
        if response.status >= 400:
            raise RuntimeError(f"mgb sdk error: {response.status}")
        payload = json.loads(response.text())
        code = str(payload.get("code", ""))
        if code != "200":
            status = payload.get("status", "Unknown")
            raise RuntimeError(f"mgb sdk auth failed: {status}")
