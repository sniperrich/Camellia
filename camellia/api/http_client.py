import gzip
import json
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar
from typing import Any, Dict, Optional


class HttpResponse:
    def __init__(self, status: int, headers: Dict[str, str], body: bytes, url: str):
        self.status = status
        self.headers = headers
        self.body = body
        self.url = url

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text())


class HttpClient:
    def __init__(
        self,
        base_url: str = "",
        default_headers: Optional[Dict[str, str]] = None,
        timeout: int = 30,
        cookie_jar: Optional[CookieJar] = None,
        ignore_system_proxy: bool = False,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.default_headers = default_headers or {}
        self.timeout = timeout
        self.cookie_jar = cookie_jar
        # By default urllib honors environment/registry proxies. That breaks in some
        # Windows setups (e.g. stale VPN/system proxy) and can surface as WinSock
        # errors 10049/10054 or SSL handshake failures. We allow opting out.
        proxy_handler = urllib.request.ProxyHandler({}) if ignore_system_proxy else urllib.request.ProxyHandler()
        handlers = [proxy_handler]
        if cookie_jar is not None:
            handlers.append(urllib.request.HTTPCookieProcessor(cookie_jar))
        self._opener = urllib.request.build_opener(*handlers)

    def _build_url(self, path: str, params: Optional[Dict[str, str]] = None) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            url = path
        else:
            url = f"{self.base_url}/{path.lstrip('/')}" if self.base_url else path
        if params:
            query = urllib.parse.urlencode(params)
            url = f"{url}{'&' if '?' in url else '?'}{query}"
        return url

    def _merge_headers(self, headers: Optional[Dict[str, str]]) -> Dict[str, str]:
        merged = dict(self.default_headers)
        if headers:
            merged.update(headers)
        return merged

    def _read_response(self, resp: Any) -> HttpResponse:
        body = resp.read()
        encoding = getattr(resp, "headers", {}).get("Content-Encoding", "")
        if isinstance(encoding, str) and encoding.lower() == "gzip":
            body = gzip.decompress(body)
        headers = {k: v for k, v in getattr(resp, "headers", {}).items()}
        status = getattr(resp, "status", None) or getattr(resp, "code", 0) or 0
        url = resp.geturl() if hasattr(resp, "geturl") else ""
        return HttpResponse(int(status), headers, body, url)

    def get(self, path: str, params: Optional[Dict[str, str]] = None,
            headers: Optional[Dict[str, str]] = None) -> HttpResponse:
        url = self._build_url(path, params)
        request = urllib.request.Request(url, method="GET")
        for k, v in self._merge_headers(headers).items():
            request.add_header(k, v)
        try:
            resp = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            resp = exc
        return self._read_response(resp)

    def post(
        self,
        path: str,
        data: Optional[bytes] = None,
        headers: Optional[Dict[str, str]] = None,
        content_type: str = "application/json",
    ) -> HttpResponse:
        url = self._build_url(path)
        request = urllib.request.Request(url, data=data or b"", method="POST")
        merged = self._merge_headers(headers)
        if "Content-Type" not in merged and content_type:
            merged["Content-Type"] = content_type
        for k, v in merged.items():
            request.add_header(k, v)
        try:
            resp = self._opener.open(request, timeout=self.timeout)
        except urllib.error.HTTPError as exc:
            resp = exc
        return self._read_response(resp)

    def post_json(self, path: str, payload: Any,
                  headers: Optional[Dict[str, str]] = None) -> HttpResponse:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        return self.post(path, data=data, headers=headers, content_type="application/json")

    def post_form(self, path: str, form: Dict[str, str],
                  headers: Optional[Dict[str, str]] = None) -> HttpResponse:
        data = urllib.parse.urlencode(form).encode("utf-8")
        return self.post(path, data=data, headers=headers, content_type="application/x-www-form-urlencoded")


def load_cookie_jar() -> CookieJar:
    return CookieJar()
