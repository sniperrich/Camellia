from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import struct
import threading
import time
import urllib.parse
import urllib.request
import uuid
from collections import deque
from dataclasses import dataclass, field

from Crypto.Cipher import DES
from Crypto.Util.Padding import pad, unpad

from camellia.mc.protocol import ProtocolError, read_string, read_varint, write_string, write_varint
from camellia.plugins.events import (
    GameJoinEvent,
    LoginSuccessEvent,
    PacketDirection,
    PluginMessageEvent,
    SwingArmEvent,
    UseItemEvent,
    UseItemOnEvent,
    SetEntityMetadataEvent,
)


REGISTER_CHANNEL = "minecraft:register"
BRAND_CHANNEL = "minecraft:brand"
HEYPIXEL_CHANNEL = "heypixel:s2cevent"
SYNC_SKINS_CHANNEL = "heypixel:sync_skins"
DERIVE_KEY_URL = "https://service.codexus.today/third-party/heypixel/derive-key"

MSG_LA5 = 1
MSG_FSYR = 2  # Heartbeat message
MSG_OWX2 = 3  # CPS message
MSG_CMESSAGE_BLOCK = 5
MSG_REFLECT = 101

# 消息类型枚举
MSG_TYPE_INFO = 0
MSG_TYPE_BLACK_CLASS = 1
MSG_TYPE_BLACK_MODULE = 2
MSG_TYPE_REFLECT_CHECK = 3

REGISTER_CHANNELS = [
    "worldedit:cui",
    "fml:loginwrapper",
    "forge:tier_sorting",
    "storemod:buy",
    "floodgate:custom",
    "floodgate:packet",
    "heypixel:s2cevent",
    "report:areport",
    "plugin:guild",
    "fml:play",
    "floodgate:netease",
    "floodgate:transfer",
    "fml:handshake",
    "heypixel:onlinestats",
    "forge:split",
    "floodgate:form",
    "geckolib:main",
    "floodgate:skin",
    "minecraft:register",
    "minecraft:brand",
    "minecraft:netregistry",
]

SYNC_SKINS_PAYLOAD = base64.b64decode("ASQwMDAwMDAwMC0wMDAwLTQwMDAtODAwMC0wMDAwMzliYzYyMTM=")

# 伪造的 DLL 模块列表
FAKE_MODULE_LIST = [
    "C:\\WINDOWS\\SYSTEM32\\ntdll.dll:b7d3143f2d98d9b5eb79d2c8339192ab",
    # ... 省略部分 dll 避免过长 ...
    "C:\\WINDOWS\\System32\\wintypes.dll:1b9dfb387d7e163c9091781474e884e5",
]

CPS_WINDOW_MS = 1000
CPS_SEND_INTERVAL_MS = 50
CPS_JITTER_MS = 9
HEARTBEAT_INTERVAL_MS = 5000
LOG_HEYPIXEL_TRAFFIC = True
LOG_HEYPIXEL_MAX_BYTES = 96

_traffic_log_handle = None
_traffic_log_path = None
_traffic_log_lock = threading.Lock()


def _ensure_traffic_log(logger=None):
    global _traffic_log_handle, _traffic_log_path
    if not LOG_HEYPIXEL_TRAFFIC:
        return None
    if _traffic_log_handle is not None:
        return _traffic_log_handle
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "logs"))
    try:
        os.makedirs(base_dir, exist_ok=True)
        session_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}-{random.randint(0, 0xFFFF):04x}"
        _traffic_log_path = os.path.join(base_dir, f"heypixel-traffic-{session_id}.log")
        _traffic_log_handle = open(_traffic_log_path, "a", encoding="utf-8", buffering=1)
        _traffic_log_handle.write(f"# heypixel_traffic {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        if logger is not None:
            logger.info("Heypixel: traffic log: %s", _traffic_log_path)
    except OSError:
        _traffic_log_handle = None
        _traffic_log_path = None
    return _traffic_log_handle


def _log_heypixel_payload(direction: str, payload: bytes, state: HeypixelState | None = None, note: str = "", logger=None) -> None:
    handle = _ensure_traffic_log(logger)
    if handle is None:
        return
    data = payload.tobytes() if isinstance(payload, memoryview) else payload
    if isinstance(data, bytearray):
        data = bytes(data)
    msg_id = None
    inner_len = None
    decoded = None
    if data and data[0] == 0xFA:
        decoded = _decode_event_payload(data)
        if decoded:
            msg_id = decoded[0]
            inner_len = len(decoded[1])
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    line = f"{ts} {direction} msg={msg_id if msg_id is not None else '?'} len={len(data)}"
    if inner_len is not None:
        line += f" inner={inner_len}"
    if note:
        line += f" note={note}"
    if decoded and msg_id == MSG_LA5 and state and state.crypto:
        try:
            decrypted = _decrypt_payload(state, decoded[1])
            line += f" dec_len={len(decrypted)}"
        except Exception:
            line += " dec_err=1"
    hex_preview = data[:LOG_HEYPIXEL_MAX_BYTES].hex()
    line += f" hex={hex_preview}"
    if len(data) > LOG_HEYPIXEL_MAX_BYTES:
        line += "..."
    with _traffic_log_lock:
        handle.write(line + "\n")


class ClickTracker:
    def __init__(self) -> None:
        self._left_clicks: deque[int] = deque()
        self._right_clicks: deque[int] = deque()
        self._lock = asyncio.Lock()

    async def record_left(self) -> None:
        now = int(time.time() * 1000)
        async with self._lock:
            self._left_clicks.append(now)

    async def record_right(self) -> None:
        now = int(time.time() * 1000)
        async with self._lock:
            self._right_clicks.append(now)

    def _cleanup(self, clicks: deque[int], now: int) -> None:
        cutoff = now - CPS_WINDOW_MS
        while clicks and clicks[0] < cutoff:
            clicks.popleft()

    async def get_cps(self) -> tuple[int, int]:
        now = int(time.time() * 1000)
        async with self._lock:
            self._cleanup(self._left_clicks, now)
            self._cleanup(self._right_clicks, now)
            return len(self._left_clicks), len(self._right_clicks)


@dataclass
class HeypixelState:
    registered: bool = False
    brand_sent: bool = False
    server_channels: set[str] = field(default_factory=set)
    heypixel_detected: bool = False
    profile_uuid: str | None = None
    user_id: int | None = None
    derived_key: str | None = None
    crypto: "HeypixelCrypto | None" = None
    derive_task: asyncio.Task | None = None
    info_sent: bool = False
    click_tracker: ClickTracker | None = None
    cps_task: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None
    game_joined: bool = False
    random_id: int = field(default_factory=lambda: random.randint(77772, 81079))
    enc_profile: str | None = None
    enc_zero: str | None = None
    last_cps: tuple[int, int] | None = None
    black_class_sent: bool = False
    black_module_sent: bool = False

    def cleanup(self) -> None:
        self.game_joined = False
        if self.cps_task and not self.cps_task.done():
            self.cps_task.cancel()
        self.cps_task = None
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        self.heartbeat_task = None
        if self.derive_task and not self.derive_task.done():
            self.derive_task.cancel()
        self.derive_task = None
        self.click_tracker = None
        self.enc_profile = None
        self.enc_zero = None
        self.last_cps = None
        self.black_class_sent = False
        self.black_module_sent = False


class HeypixelCrypto:
    def __init__(self, key: str) -> None:
        self._key = key

    def encrypt(self, data: bytes) -> bytes:
        if not self._key:
            return data
        salt = os.urandom(8)
        key_bytes, iv = _derive_key_iv(self._key, salt)
        # DES CBC
        cipher = DES.new(key_bytes, DES.MODE_CBC, iv)
        return salt + cipher.encrypt(pad(data, 8))

    def decrypt(self, data: bytes) -> bytes:
        if not self._key:
            return data
        if len(data) <= 8:
            raise ValueError("payload too short")
        salt = data[:8]
        key_bytes, iv = _derive_key_iv(self._key, salt)
        cipher = DES.new(key_bytes, DES.MODE_CBC, iv)
        return unpad(cipher.decrypt(data[8:]), 8)


def setup(context) -> None:
    events = context.events
    logger = context.logger

    async def on_login_success(event: LoginSuccessEvent) -> None:
        session = event.session
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        _ensure_identity(session, state)
        await _ensure_registered(session, logger)
        _start_derive_key(session, state, logger)
        _start_heartbeat_task(session, state, logger)
        logger.info("Heypixel: Login success process initiated (v=%s)", getattr(session, "protocol_version", "unknown"))

    async def on_game_join(event: GameJoinEvent) -> None:
        session = event.session
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        state.game_joined = True
        if state.click_tracker is None:
            state.click_tracker = ClickTracker()
        await _send_sync_skins(session, logger)
        await _send_info(session, state, logger)
        _start_cps_task(session, state, logger)
        _start_heartbeat_task(session, state, logger)
        logger.info("Heypixel: Game join process completed")

    async def on_swing_arm(event: SwingArmEvent) -> None:
        session = event.session
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        if state.click_tracker:
            await state.click_tracker.record_left()

    async def on_use_item(event: UseItemEvent) -> None:
        session = event.session
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        if state.click_tracker:
            await state.click_tracker.record_right()

    async def on_use_item_on(event: UseItemOnEvent) -> None:
        session = event.session
        if not _is_supported_version(session):
            return
        if event.cancelled:
            return
        state = _get_state(session)
        if not state.game_joined or not state.heypixel_detected:
            return
        await _send_block_message(session, state, event, logger)

    async def on_plugin_message(event: PluginMessageEvent) -> None:
        session = event.session
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        if event.direction == PacketDirection.SERVERBOUND:
            _handle_serverbound(event, state, logger)
        elif event.direction == PacketDirection.CLIENTBOUND:
            await _handle_clientbound(event, state, logger)
        else:
            raise ValueError("Invalid packet direction")

    async def on_set_entity_metadata(event: SetEntityMetadataEvent) -> None:
        session = event.session
        if not _is_supported_version(session):
            return
        # 暂时只记录日志
        pass

    events.on("plugin_message", on_plugin_message, event_type=PluginMessageEvent)
    events.on("game_join", on_game_join, event_type=GameJoinEvent)
    events.on("swing_arm", on_swing_arm, event_type=SwingArmEvent)
    events.on("use_item", on_use_item, event_type=UseItemEvent)
    events.on("use_item_on", on_use_item_on, event_type=UseItemOnEvent)
    events.on("channel_v1206", on_login_success, event_type=LoginSuccessEvent)
    events.on("set_entity_metadata", on_set_entity_metadata, event_type=SetEntityMetadataEvent)


def _handle_serverbound(event: PluginMessageEvent, state: HeypixelState, logger) -> None:
    if event.identifier == BRAND_CHANNEL:
        event.payload = _build_brand_payload()
        state.brand_sent = True
    elif event.identifier == REGISTER_CHANNEL:
        event.payload = _build_register_payload()
        state.registered = True


async def _handle_clientbound(event: PluginMessageEvent, state: HeypixelState, logger) -> None:
    if event.identifier == REGISTER_CHANNEL:
        channels = _parse_channels(event.payload)
        if channels:
            state.server_channels.update(channels)
            if any(ch.startswith("heypixel:") for ch in channels):
                state.heypixel_detected = True
        return

    if event.identifier != HEYPIXEL_CHANNEL:
        return

    _log_heypixel_payload("recv", event.payload, state, logger=logger)
    decoded = _decode_event_payload(event.payload)
    if not decoded:
        return
    msg_id, payload = decoded
    if msg_id != MSG_REFLECT:
        return

    raw = payload
    try:
        data = _decrypt_payload(state, payload)
    except Exception:
        data = payload

    try:
        req = _parse_reflect_request(data)
    except Exception:
        if data is not raw:
            try:
                req = _parse_reflect_request(raw)
            except Exception as exc:
                logger.warning("Heypixel: 解析反射请求失败: %s", exc)
                return
        else:
            return

    if req.get("type") != MSG_TYPE_REFLECT_CHECK:
        return

    await _send_reflect_response(event.session, state, req, logger)


def _ensure_identity(session, state: HeypixelState) -> None:
    if not state.profile_uuid:
        state.profile_uuid = _get_profile_uuid(session)
    if state.user_id is None:
        state.user_id = _get_user_id(session)


def _start_derive_key(session, state: HeypixelState, logger) -> None:
    if state.derived_key or state.derive_task:
        return
    if not state.profile_uuid:
        return

    async def task() -> None:
        try:
            key = await _fetch_derived_key(state.profile_uuid, state.user_id or 0, _get_name(session))
            if key:
                state.derived_key = key
                state.crypto = HeypixelCrypto(key)
                _refresh_encrypted_strings(state)
                logger.info("Heypixel: 派生密钥获取成功 (DES)")
            else:
                logger.warning("Heypixel: 派生密钥为空")
        except Exception as exc:
            logger.warning("Heypixel: 派生密钥失败: %s", exc)
        await _send_info(session, state, logger)
        await _maybe_send_black_checks(session, state, logger)
    
    state.derive_task = session.create_task(task())


def _refresh_encrypted_strings(state: HeypixelState) -> None:
    if state.crypto is None:
        return
    profile = state.profile_uuid or ""
    state.enc_profile = _encrypt_string(state, profile)
    state.enc_zero = _encrypt_string(state, "0")
    state.black_class_sent = False
    state.black_module_sent = False


def _encrypt_string(state: HeypixelState, text: str) -> str:
    if state.crypto is None:
        return text
    try:
        raw = state.crypto.encrypt(text.encode("utf-8"))
        return base64.b64encode(raw).decode("ascii")
    except Exception:
        return text


def _can_send_secure(state: HeypixelState, session) -> bool:
    if not state.profile_uuid:
        state.profile_uuid = _get_profile_uuid(session)
    return bool(state.profile_uuid and state.crypto)


async def _maybe_send_black_checks(session, state: HeypixelState, logger) -> None:
    if not _can_send_secure(state, session):
        return
    if not state.info_sent:
        return
    if not state.black_class_sent:
        await _send_black_class_response(session, state, {}, logger)
    if not state.black_module_sent:
        await _send_black_module_response(session, state, {}, logger)


def _start_heartbeat_task(session, state: HeypixelState, logger) -> None:
    if state.heartbeat_task:
        return

    async def heartbeat_loop() -> None:
        while session.is_active:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_MS / 1000.0)
                if not session.is_active:
                    break
                await _send_heartbeat(session, state, logger)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Heypixel: 心跳包发送异常: %s", exc)

    state.heartbeat_task = session.create_task(heartbeat_loop())


def _start_cps_task(session, state: HeypixelState, logger) -> None:
    if state.cps_task:
        return

    async def cps_loop() -> None:
        while session.is_active and state.game_joined:
            try:
                await _send_cps(session, state, logger)
                jitter = random.randint(0, CPS_JITTER_MS)
                if random.random() < 0.5:
                    jitter = -jitter
                delay_ms = max(1, CPS_SEND_INTERVAL_MS + jitter)
                await asyncio.sleep(delay_ms / 1000.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.debug("Heypixel: CPS 发送异常: %s", exc)

    state.cps_task = session.create_task(cps_loop())


async def _send_cps(session, state: HeypixelState, logger) -> None:
    if not state.click_tracker:
        return
    left_cps, right_cps = await state.click_tracker.get_cps()
    if state.last_cps is None:
        if left_cps == 0 and right_cps == 0:
            state.last_cps = (left_cps, right_cps)
            return
    elif state.last_cps == (left_cps, right_cps):
        return
    payload = _build_cps_payload(left_cps, right_cps)
    payload = _encode_event_payload(MSG_OWX2, payload)
    try:
        _log_heypixel_payload("send", payload, state, note=f"cps={left_cps}/{right_cps}", logger=logger)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        state.last_cps = (left_cps, right_cps)
    except ProtocolError:
        pass


async def _send_heartbeat(session, state: HeypixelState, logger) -> None:
    payload = _build_heartbeat_payload()
    payload = _encode_event_payload(MSG_FSYR, payload)
    try:
        _log_heypixel_payload("send", payload, state, note="heartbeat", logger=logger)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        logger.debug("Heypixel: 已发送心跳包")
    except ProtocolError as exc:
        logger.debug("Heypixel: 心跳包发送失败: %s", exc)


async def _send_sync_skins(session, logger) -> None:
    try:
        await session.send_plugin_message(PacketDirection.SERVERBOUND, SYNC_SKINS_CHANNEL, SYNC_SKINS_PAYLOAD)
        logger.info("Heypixel: 已发送皮肤同步消息")
    except ProtocolError as exc:
        logger.warning("Heypixel: 发送皮肤同步失败: %s", exc)


async def _send_info(session, state: HeypixelState, logger) -> None:
    if state.info_sent:
        return
    if not state.profile_uuid:
        return
    if state.crypto is None:
        return
    if state.enc_profile is None or state.enc_zero is None:
        _refresh_encrypted_strings(state)

    now_ms = int(time.time() * 1000)
    payload = _build_la5_payload(
        profile_uuid=state.profile_uuid,
        msg_type=MSG_TYPE_INFO,
        text=state.profile_uuid,
        timestamp=now_ms,
        extra="",
        state=state,
    )
    payload = _encrypt_payload(state, payload)
    payload = _encode_event_payload(MSG_LA5, payload)
    try:
        _log_heypixel_payload("send", payload, state, note="info", logger=logger)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        state.info_sent = True
        await _maybe_send_black_checks(session, state, logger)
        logger.info("Heypixel: 已发送基础信息")
    except ProtocolError as exc:
        logger.warning("Heypixel: 发送基础信息失败: %s", exc)


async def _send_reflect_response(session, state: HeypixelState, request: dict[str, object], logger) -> None:
    if not state.profile_uuid:
        state.profile_uuid = _get_profile_uuid(session)
    if not state.profile_uuid:
        return
    if state.crypto is None:
        return
    now_ms = int(time.time() * 1000)
    payload = _build_la5_payload(
        profile_uuid=state.profile_uuid,
        msg_type=MSG_TYPE_REFLECT_CHECK,
        text=str(request.get("text") or ""),
        timestamp=int(request.get("timestamp") or now_ms),
        extra=str(request.get("extra") or ""),
        state=state,
    )
    payload = _encrypt_payload(state, payload)
    payload = _encode_event_payload(MSG_LA5, payload)
    try:
        _log_heypixel_payload("send", payload, state, note="reflect", logger=logger)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        logger.info("Heypixel: 已回应反射校验")
    except ProtocolError as exc:
        logger.warning("Heypixel: 反射校验回应失败: %s", exc)
    await _maybe_send_black_checks(session, state, logger)


async def _send_black_class_response(session, state: HeypixelState, request: dict[str, object], logger) -> None:
    if not _can_send_secure(state, session):
        return
    payload = _build_la5_payload(
        profile_uuid=state.profile_uuid,
        msg_type=MSG_TYPE_BLACK_CLASS,
        text=state.profile_uuid,
        timestamp=int(time.time() * 1000),
        extra="",
        state=state,
    )
    payload = _encrypt_payload(state, payload)
    payload = _encode_event_payload(MSG_LA5, payload)
    try:
        _log_heypixel_payload("send", payload, state, note="black_class", logger=logger)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        state.black_class_sent = True
        logger.info("Heypixel: 已回应 BlackClass 检查")
    except ProtocolError as exc:
        logger.warning("Heypixel: BlackClass 回应失败: %s", exc)


async def _send_black_module_response(session, state: HeypixelState, request: dict[str, object], logger) -> None:
    if not _can_send_secure(state, session):
        return
    payload = _build_la5_payload(
        profile_uuid=state.profile_uuid,
        msg_type=MSG_TYPE_BLACK_MODULE,
        text=state.profile_uuid,
        timestamp=int(time.time() * 1000),
        extra="",
        state=state,
    )
    payload = _encrypt_payload(state, payload)
    payload = _encode_event_payload(MSG_LA5, payload)
    try:
        _log_heypixel_payload("send", payload, state, note="black_module", logger=logger)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        state.black_module_sent = True
        logger.info("Heypixel: 已回应 BlackModule 检查")
    except ProtocolError as exc:
        logger.warning("Heypixel: BlackModule 回应失败: %s", exc)


async def _send_block_message(session, state: HeypixelState, event: UseItemOnEvent, logger) -> None:
    if not event.location:
        return
    block_x, block_y, block_z = event.location
    player_pos = getattr(session, "player_pos", None)
    if player_pos and len(player_pos) == 3:
        player_x, player_y, player_z = player_pos
    else:
        player_x = block_x + 0.5
        player_y = block_y + 0.5
        player_z = block_z + 0.5
    player_rot = getattr(session, "player_rot", None)
    if player_rot and len(player_rot) == 2:
        head_yaw, head_pitch = player_rot
    else:
        head_yaw = 0.0
        head_pitch = 0.0

    location_x = float(block_x) + float(event.cursor_x)
    location_y = float(block_y) + float(event.cursor_y)
    location_z = float(block_z) + float(event.cursor_z)

    payload = _build_block_message_payload(
        player_x=float(player_x),
        player_y=float(player_y),
        player_z=float(player_z),
        direction=int(event.face),
        block_type=1,
        location_x=location_x,
        location_y=location_y,
        location_z=location_z,
        block_x=float(block_x),
        block_y=float(block_y),
        block_z=float(block_z),
        inside=bool(event.inside_block),
        head_yaw=float(head_yaw),
        head_pitch=float(head_pitch),
        is_hand=False,
    )
    payload = _encode_event_payload(MSG_CMESSAGE_BLOCK, payload)
    try:
        _log_heypixel_payload("send", payload, state, note="block", logger=logger)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
    except ProtocolError:
        pass


async def _ensure_registered(session, logger) -> None:
    state = _get_state(session)
    try:
        if not state.registered:
            payload = _build_register_payload()
            await session.send_plugin_message(PacketDirection.SERVERBOUND, REGISTER_CHANNEL, payload)
            state.registered = True
        if not state.brand_sent:
            payload = _build_brand_payload()
            await session.send_plugin_message(PacketDirection.SERVERBOUND, BRAND_CHANNEL, payload)
            state.brand_sent = True
    except ProtocolError:
        pass


def _get_state(session) -> HeypixelState:
    state = getattr(session, "plugin_data", {}).get("heypixel_protocol")
    if state is None:
        state = HeypixelState()
        session.plugin_data["heypixel_protocol"] = state
    return state


def _is_supported_version(session) -> bool:
    v = getattr(session, "protocol_version", 0)
    return v >= 763


def _get_profile_uuid(session) -> str | None:
    value = getattr(session, "player_uuid", None)
    if value:
        return value
    nickname = _get_name(session)
    if not nickname:
        return None
    offline = uuid.uuid3(uuid.NAMESPACE_DNS, f"OfflinePlayer:{nickname}")
    return str(offline)


def _get_user_id(session) -> int:
    profile = getattr(session, "config", None)
    ygg = getattr(profile, "ygg_profile", None)
    if ygg and getattr(ygg, "user", None):
        return int(ygg.user.user_id)
    return 0


def _get_name(session) -> str:
    cfg = getattr(session, "config", None)
    nickname = getattr(cfg, "nickname", "") if cfg else ""
    return nickname or "player"


async def _fetch_derived_key(profile_uuid: str, user_id: int, name: str) -> str:
    query = urllib.parse.urlencode({"profile": profile_uuid, "user": user_id, "name": name})
    url = f"{DERIVE_KEY_URL}?{query}"

    def _request() -> str:
        with urllib.request.urlopen(url, timeout=8) as response:  # nosec
            raw = response.read().decode("utf-8", errors="replace")
        return raw

    raw = await asyncio.to_thread(_request)
    raw = raw.strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            payload = json.loads(raw)
            for key in ("data", "key", "value"):
                if key in payload:
                    return str(payload[key]).strip()
        except json.JSONDecodeError:
            pass
    return raw.strip("\"\n\r\t ")


def _derive_key_iv(password: str, salt: bytes) -> tuple[bytes, bytes]:
    derived = hashlib.pbkdf2_hmac("sha1", password.encode("utf-8"), salt, 1000, dklen=16)
    return derived[:8], derived[8:]


def _encrypt_payload(state: HeypixelState, payload: bytes) -> bytes:
    if state.crypto is None and state.derived_key:
        state.crypto = HeypixelCrypto(state.derived_key)
    if state.crypto is None:
        return payload
    try:
        return state.crypto.encrypt(payload)
    except Exception:
        return payload


def _decrypt_payload(state: HeypixelState, payload: bytes) -> bytes:
    if state.crypto is None and state.derived_key:
        state.crypto = HeypixelCrypto(state.derived_key)
    if state.crypto is None:
        return payload
    return state.crypto.decrypt(payload)


def _build_register_payload() -> bytes:
    unique = []
    seen = set()
    for channel in REGISTER_CHANNELS:
        if channel in seen:
            continue
        seen.add(channel)
        unique.append(channel)
    return "\x00".join(unique).encode("utf-8")


def _build_brand_payload() -> bytes:
    return write_string("forge")


def _parse_channels(payload: bytes) -> list[str]:
    if not payload:
        return []
    if isinstance(payload, memoryview):
        payload = payload.tobytes()
    elif isinstance(payload, bytearray):
        payload = bytes(payload)
    try:
        raw = payload.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return []
    return [item for item in raw.split("\x00") if item]


def _encode_event_payload(msg_id: int, payload: bytes) -> bytes:
    return b"\xFA" + write_varint(msg_id) + write_varint(len(payload)) + payload


def _decode_event_payload(payload: bytes) -> tuple[int, bytes] | None:
    if not payload:
        return None
    data = payload.tobytes() if isinstance(payload, memoryview) else payload
    if not data or data[0] != 0xFA:
        return None
    try:
        offset = 1
        msg_id, size = read_varint(data, offset)
        offset += size
        length, size = read_varint(data, offset)
        offset += size
        if length < 0 or offset + length > len(data):
            return None
        return msg_id, data[offset : offset + length]
    except (ProtocolError, ValueError):
        return None


def _build_la5_payload(
    *,
    profile_uuid: str,
    msg_type: int,
    text: str,
    timestamp: int,
    extra: str,
    state: HeypixelState,
) -> bytes:
    parts = [
        write_string(profile_uuid),
        _pack_byte(msg_type),
        write_string(text),
        _pack_long(timestamp),
    ]
    if msg_type == MSG_TYPE_INFO:
        parts.append(_build_info_payload(state))
    elif msg_type == MSG_TYPE_BLACK_CLASS:
        parts.append(_build_black_class_payload(state))
    elif msg_type == MSG_TYPE_BLACK_MODULE:
        parts.append(_build_black_module_payload(state))
    elif msg_type == MSG_TYPE_REFLECT_CHECK and extra:
        parts.append(write_string(extra))
    return b"".join(parts)


def _build_cps_payload(left_cps: int, right_cps: int) -> bytes:
    return _pack_int(left_cps) + _pack_int(right_cps)


def _build_heartbeat_payload() -> bytes:
    now_ms = int(time.time() * 1000)
    return _pack_long(now_ms)


def _build_info_payload(state: HeypixelState) -> bytes:
    marker = state.enc_zero or "0"
    parts = [
        _pack_int(state.random_id),
        _pack_int(1),
        write_varint(1),
        write_string(marker),
    ]
    return b"".join(parts)


def _build_black_class_payload(state: HeypixelState) -> bytes:
    enc_profile = state.enc_profile or (state.profile_uuid or "")
    enc_zero = state.enc_zero or "0"
    marker = f"{enc_profile}:{enc_zero}"
    parts = [
        _pack_int(len(FAKE_MODULE_LIST)),
        write_varint(1),
        write_string(marker),
    ]
    return b"".join(parts)


def _build_black_module_payload(state: HeypixelState) -> bytes:
    parts = [write_string("") for _ in range(7)]
    return b"".join(parts)


def _build_block_message_payload(
    *,
    player_x: float,
    player_y: float,
    player_z: float,
    direction: int,
    block_type: int,
    location_x: float,
    location_y: float,
    location_z: float,
    block_x: float,
    block_y: float,
    block_z: float,
    inside: bool,
    head_yaw: float,
    head_pitch: float,
    is_hand: bool,
) -> bytes:
    parts = [
        _pack_float(player_x),
        _pack_float(player_y),
        _pack_float(player_z),
        _pack_int(direction),
        _pack_int(block_type),
        _pack_float(location_x),
        _pack_float(location_y),
        _pack_float(location_z),
        _pack_float(block_x),
        _pack_float(block_y),
        _pack_float(block_z),
        _pack_bool(inside),
        _pack_float(head_yaw),
        _pack_float(head_pitch),
        _pack_bool(is_hand),
    ]
    return b"".join(parts)


def _parse_reflect_request(payload: bytes) -> dict[str, object]:
    offset = 0
    text, size = read_string(payload, offset, 32767)
    offset += size
    timestamp, size = _read_long(payload, offset)
    offset += size
    msg_type, size = _read_byte(payload, offset)
    offset += size
    extra = ""
    if offset < len(payload):
        extra, size = read_string(payload, offset, 32767)
        offset += size
    return {"text": text, "timestamp": timestamp, "type": msg_type, "extra": extra}


def _read_long(data: bytes, offset: int) -> tuple[int, int]:
    end = offset + 8
    if end > len(data):
        raise ValueError("long out of range")
    return struct.unpack(">q", data[offset:end])[0], 8


def _read_byte(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("byte out of range")
    return data[offset], 1


def _pack_float(value: float) -> bytes:
    return struct.pack(">f", float(value))


def _pack_long(value: int) -> bytes:
    return struct.pack(">q", int(value))


def _pack_byte(value: int) -> bytes:
    return bytes([int(value) & 0xFF])


def _pack_int(value: int) -> bytes:
    return struct.pack(">i", int(value))


def _pack_bool(value: bool) -> bytes:
    return b"\x01" if value else b"\x00"
