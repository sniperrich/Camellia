from __future__ import annotations

HARDCODED_CRC_SALT = "E77652A5A6FE19810998B02347F2D805"


def resolve_crc_salt_with_fallback(
    *,
    primary_crc_salt: str | None,
    primary_game_version: str | None,
    fetch_json: object | None = None,
) -> tuple[str, str]:
    _ = primary_crc_salt, primary_game_version, fetch_json
    return HARDCODED_CRC_SALT, "hardcoded"
