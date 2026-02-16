from __future__ import annotations

import json
import time
from typing import Any, Dict, Optional

from .http_client import HttpClient, HttpResponse, load_cookie_jar


class AuthBackend:
    def __init__(self, base_url: str, timeout: int = 20) -> None:
        # Keep cookies like a normal HttpClient session.
        # This improves compatibility with Cloudflare/WAF that may rely on cookies.
        self._cookie_jar = load_cookie_jar()
        self.client = HttpClient(
            base_url=base_url,
            timeout=timeout,
            cookie_jar=self._cookie_jar,
            # Auth backend should not be affected by OS/system proxies. Users often have
            # stale VPN/proxy settings that cause WinSock 10049/10054 or SSL errors.
            ignore_system_proxy=True,
            default_headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # Match the reference behavior (Fantnel/OpenSDK): always read the response body,
        # even when status is 4xx/5xx, so callers can surface real backend/WAF reasons.
        def do_request() -> "HttpResponse":
            if method.upper() == "GET":
                return self.client.get(path)
            return self.client.post_json(path, payload or {})

        try:
            resp = do_request()
        except Exception as exc:  # pylint: disable=broad-except
            return {"success": False, "error": "network_error", "message": str(exc)}

        raw_text = resp.text()
        # Cloudflare may intermittently block the first request but set a cookie (e.g. __cf_bm).
        # A single retry with the same cookie jar often stabilizes the flow.
        lowered = raw_text.lower()
        if resp.status in (403, 503) and ("error code: 1010" in lowered or "error code: 1020" in lowered):
            try:
                time.sleep(0.2)
                resp = do_request()
                raw_text = resp.text()
            except Exception:
                # Keep the original response.
                pass
        parsed: dict[str, Any] | None = None
        try:
            data = json.loads(raw_text) if raw_text else None
            if isinstance(data, dict):
                parsed = data
        except json.JSONDecodeError:
            parsed = None

        if resp.status >= 400:
            if parsed is not None and parsed:
                parsed.setdefault("success", False)
                parsed.setdefault("error", f"http_{resp.status}")
                parsed.setdefault("raw", raw_text)
                return parsed
            return {
                "success": False,
                "error": f"http_{resp.status}",
                "raw": raw_text,
            }

        if parsed is not None:
            return parsed
        # Successful status but not JSON: still return raw for debugging.
        return {
            "success": False,
            "error": "invalid_response",
            "raw": raw_text,
            "ts": int(time.time()),
        }

    def health(self) -> Dict[str, Any]:
        return self._request("GET", "/auth/health")

    def register(self, username: str, password: str) -> Dict[str, Any]:
        return self._request("POST", "/auth/register", {"username": username, "password": password})

    def activate(self, username: str, card_code: str, device_id: str = "") -> Dict[str, Any]:
        return self._request(
            "POST",
            "/auth/activate",
            {"username": username, "card_code": card_code, "device_id": device_id},
        )

    def login(self, username: str, password: str, device_id: str = "") -> Dict[str, Any]:
        return self._request(
            "POST",
            "/auth/login",
            {"username": username, "password": password, "device_id": device_id},
        )

    def verify(self, access_token: str) -> Dict[str, Any]:
        return self._request("POST", "/auth/verify", {"access_token": access_token})

    def refresh(self, refresh_token: str, device_id: str = "") -> Dict[str, Any]:
        return self._request(
            "POST",
            "/auth/refresh",
            {"refresh_token": refresh_token, "device_id": device_id},
        )
