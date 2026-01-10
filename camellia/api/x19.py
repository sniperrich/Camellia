import json
from typing import Dict

from ..config import DEFAULT_API_USER_AGENT, X19_API_GATEWAY, X19_PATCH_LIST_URL
from ..crypto.http_crypto import compute_dynamic_token
from .http_client import HttpClient


def _parse_patchlist(text: str) -> Dict[str, object]:
    text = text.strip()
    if not text:
        raise RuntimeError("empty patch list")

    last_comma = text.rfind(",")
    trimmed = text[:last_comma] if last_comma != -1 else text
    json_text = "{" + trimmed + "}"
    try:
        return json.loads(json_text)
    except json.JSONDecodeError:
        last_newline = trimmed.rfind("\n")
        if last_newline != -1:
            json_text = "{" + trimmed[last_newline + 1 :] + "}"
            return json.loads(json_text)
        raise


def get_patch_versions() -> Dict[str, object]:
    client = HttpClient()
    response = client.get(X19_PATCH_LIST_URL)
    return _parse_patchlist(response.text())


def get_latest_version() -> str:
    versions = get_patch_versions()
    if not versions:
        raise RuntimeError("patch list is empty")
    return list(versions.keys())[-1]


class X19Api:
    def __init__(self) -> None:
        self.client = HttpClient(
            base_url=X19_API_GATEWAY,
            default_headers={"User-Agent": DEFAULT_API_USER_AGENT},
        )

    def post(self, path: str, body: str, user_id: str, user_token: str) -> str:
        headers = compute_dynamic_token(path, body, user_id, user_token)
        response = self.client.post(path, data=body.encode("utf-8"), headers=headers)
        if response.status >= 400:
            raise RuntimeError(f"x19 api error {response.status}: {response.text()}")
        return response.text()
