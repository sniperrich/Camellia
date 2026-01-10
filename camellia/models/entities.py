from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class LoginOtp:
    aid: int
    otp_token: str
    lock_time: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "LoginOtp":
        return cls(
            aid=int(data.get("aid", 0)),
            otp_token=data.get("otp_token", ""),
            lock_time=int(data.get("lock_time", 0)),
        )


@dataclass
class AuthOtp:
    entity_id: str
    token: str
    account: str
    login_channel: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any], login_channel: str) -> "AuthOtp":
        return cls(
            entity_id=data.get("entity_id", ""),
            token=data.get("token", ""),
            account=data.get("account", ""),
            login_channel=login_channel,
        )


@dataclass
class NetGameItem:
    entity_id: str
    name: str
    brief_summary: str
    online_count: str
    title_image_url: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetGameItem":
        return cls(
            entity_id=data.get("entity_id", ""),
            name=data.get("name", ""),
            brief_summary=data.get("brief_summary", ""),
            online_count=str(data.get("online_count", "")),
            title_image_url=data.get("title_image_url", ""),
        )


@dataclass
class McVersion:
    name: str
    mcversion_id: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "McVersion":
        return cls(
            name=data.get("name", ""),
            mcversion_id=int(data.get("mcversionid", 0)),
        )


@dataclass
class NetGameDetail:
    mc_versions: List[McVersion]
    server_address: str
    server_port: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetGameDetail":
        mc_versions = [McVersion.from_dict(item) for item in data.get("mc_version_list", [])]
        return cls(
            mc_versions=mc_versions,
            server_address=data.get("server_address", ""),
            server_port=int(data.get("server_port", 0)),
        )


@dataclass
class NetGameServerAddress:
    host: str
    port: int

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NetGameServerAddress":
        return cls(
            host=data.get("ip", ""),
            port=int(data.get("port", 0)),
        )


@dataclass
class GameCharacter:
    name: str
    game_id: str
    user_id: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameCharacter":
        return cls(
            name=data.get("name", ""),
            game_id=data.get("game_id", ""),
            user_id=data.get("user_id", ""),
        )


@dataclass
class GameSkin:
    entity_id: str
    name: str
    brief_summary: str
    title_image_url: str
    like_num: int
    download_num: int
    developer_name: str

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GameSkin":
        return cls(
            entity_id=str(data.get("entity_id", "")),
            name=str(data.get("name", "")),
            brief_summary=str(data.get("brief_summary", "")),
            title_image_url=str(data.get("title_image_url", "")),
            like_num=int(data.get("like_num", 0) or 0),
            download_num=int(data.get("download_num", 0) or 0),
            developer_name=str(data.get("developer_name", "")),
        )


@dataclass
class FantnelInfo:
    crc_salt: Optional[str]
    game_version: Optional[str]

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FantnelInfo":
        return cls(
            crc_salt=data.get("crcSalt"),
            game_version=data.get("gameVersion"),
        )
