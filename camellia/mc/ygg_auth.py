from __future__ import annotations

from typing import Any

from .yggdrasil import GameProfile, YggdrasilData


def build_runtime_ygg_data(*, client_game_version: str, crc_salt: str) -> YggdrasilData:
    return YggdrasilData(
        launcher_version=(client_game_version or "").strip(),
        channel="netease",
        crc_salt=(crc_salt or "").strip(),
    )


def build_fantnel_profile_payload(profile: GameProfile) -> dict[str, Any]:
    return {
        "gameId": str(profile.game_id),
        "gameVersion": str(profile.game_version),
        "bootstrapMd5": str(profile.bootstrap_md5),
        "datFileMd5": str(profile.dat_file_md5),
        "mods": profile.mods.to_dict(),
        "profile": {
            "user": {
                "userId": str(profile.user.user_id),
                "token": str(profile.user.user_token),
            },
        },
    }


def is_backend_authenticated_success(payload: dict[str, Any] | None) -> bool:
    if not isinstance(payload, dict):
        return False
    return int(payload.get("code", 0) or 0) == 1
