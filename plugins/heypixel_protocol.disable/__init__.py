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
SAFE_MINIMAL = False  # 设为 True 可仅回应反射排查踢出；默认关闭以启用完整协议
TARGET_GAME_ID = "4661334467366178884"

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
    "bungeequeue:queue",
    "legacy:redisbungee",
    "gameteam:redisteam",
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

# Naven/decoded 常见路径与默认伪造配置
DEFAULT_GAME_PATH = "E:\\MCLDownload\\Game\\.minecraft"
DEFAULT_JRE_PATH = "E:\\MCLDownload\\ext\\jre-v64-220420\\jdk17"
DEFAULT_MOD_LIST = [
    "minecraft",
    "saturn",
    "entityculling",
    "mixinextras",
    "netease_official",
    "fastload",
    "geckolib",
    "waveycapes",
    "ferritecore",
    "embeddium_extra",
    "heypixelmod",
    "cloth_config",
    "forge",
    "embeddium",
    "rubidium",
    "oculus",
]

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
    register_recv_count: int = 0
    register_replied: bool = False
    register_payload_hash: str | None = None
    last_server_channels: list[str] = field(default_factory=list)
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
    random_uuid: str | None = None

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
        self.random_uuid = None


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

    # 创建调试日志文件
    from pathlib import Path
    debug_log = Path("logs/debug-heypixel.log")
    debug_log.parent.mkdir(exist_ok=True)

    def write_debug(msg: str) -> None:
        with open(debug_log, "a") as f:
            from datetime import datetime
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} {msg}\n")
            f.flush()

    write_debug("Heypixel plugin loaded")

    async def on_login_success(event: LoginSuccessEvent) -> None:
        session = event.session
        if TARGET_GAME_ID and getattr(session, "game_id", None) not in (None, TARGET_GAME_ID):
            return
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        _ensure_identity(session, state)
        # 不在这里主动发送 register/brand，等待客户端发送后拦截替换
        if not SAFE_MINIMAL:
            _start_derive_key(session, state, logger)
            _start_heartbeat_task(session, state, logger)
            logger.info("Heypixel: Login success process initiated (v=%s)", getattr(session, "protocol_version", "unknown"))

    async def on_game_join(event: GameJoinEvent) -> None:
        session = event.session
        if TARGET_GAME_ID and getattr(session, "game_id", None) not in (None, TARGET_GAME_ID):
            return
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        state.game_joined = True
        if not SAFE_MINIMAL:
            if state.click_tracker is None:
                state.click_tracker = ClickTracker()
            await _send_sync_skins(session, logger)
            await _send_info(session, state, logger)
            _start_cps_task(session, state, logger)
            _start_heartbeat_task(session, state, logger)
            logger.info("Heypixel: Game join process completed")

    async def on_swing_arm(event: SwingArmEvent) -> None:
        session = event.session
        if TARGET_GAME_ID and getattr(session, "game_id", None) not in (None, TARGET_GAME_ID):
            return
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        if not SAFE_MINIMAL and state.click_tracker:
            await state.click_tracker.record_left()

    async def on_use_item(event: UseItemEvent) -> None:
        session = event.session
        if TARGET_GAME_ID and getattr(session, "game_id", None) not in (None, TARGET_GAME_ID):
            return
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        if not SAFE_MINIMAL and state.click_tracker:
            await state.click_tracker.record_right()

    async def on_use_item_on(event: UseItemOnEvent) -> None:
        session = event.session
        if TARGET_GAME_ID and getattr(session, "game_id", None) not in (None, TARGET_GAME_ID):
            return
        if not _is_supported_version(session):
            return
        if event.cancelled:
            return
        state = _get_state(session)
        if SAFE_MINIMAL:
            return
        if not state.game_joined or not state.heypixel_detected:
            return
        await _send_block_message(session, state, event, logger)

    async def on_plugin_message(event: PluginMessageEvent) -> None:
        session = event.session
        if not _is_supported_version(session):
            return
        state_name = getattr(getattr(session, "state", None), "name", None)
        if state_name not in ("PLAY", "CONFIGURATION"):
            return
        state = _get_state(session)
        msg = f"Received plugin_message: dir={event.direction} id={event.identifier}"
        logger.info(f"Heypixel: {msg}")
        write_debug(msg)
        if event.direction == PacketDirection.SERVERBOUND:
            _handle_serverbound(event, state, logger)
        elif event.direction == PacketDirection.CLIENTBOUND:
            await _handle_clientbound(event, state, logger, write_debug)
        else:
            raise ValueError("Invalid packet direction")

    async def on_set_entity_metadata(event: SetEntityMetadataEvent) -> None:
        session = event.session
        if not _is_supported_version(session):
            return
        # 暂时只记录日志
        pass

    # 关键：和 Fantnel 一样，在 base_1200 通道注册 PluginMessage 处理器
    # PLAY + CONFIGURATION 都要处理（CONFIGURATION 阶段有 register/brand）
    events.on("base_1200", on_plugin_message, event_type=PluginMessageEvent)
    events.on("base_1200", on_game_join, event_type=GameJoinEvent)
    events.on("base_1200", on_swing_arm, event_type=SwingArmEvent)
    events.on("base_1200", on_use_item, event_type=UseItemEvent)
    events.on("base_1200", on_use_item_on, event_type=UseItemOnEvent)
    events.on("base_1200", on_set_entity_metadata, event_type=SetEntityMetadataEvent)
    events.on("channel_v1206", on_login_success, event_type=LoginSuccessEvent)


def _handle_serverbound(event: PluginMessageEvent, state: HeypixelState, logger) -> None:
    if event.identifier == BRAND_CHANNEL:
        # 替换 brand 为 "forge"
        event.payload = _build_brand_payload()
        state.brand_sent = True
        logger.debug("Heypixel: 已替换 brand 为 forge")
        return

    if event.identifier == REGISTER_CHANNEL:
        merged = _merge_register_channels(state.last_server_channels)
        if merged:
            payload = _build_register_payload_from_list(merged)
            state.register_payload_hash = hashlib.sha256(payload).hexdigest()
            event.payload = payload
            state.register_replied = True
            logger.debug("Heypixel: 已替换 client register 为合并列表")


async def _handle_clientbound(event: PluginMessageEvent, state: HeypixelState, logger, write_debug=None) -> None:
    if event.identifier == REGISTER_CHANNEL:
        msg = f"Processing REGISTER message (count={state.register_recv_count + 1})"
        logger.info(f"Heypixel: {msg}")
        if write_debug:
            write_debug(msg)
        channels = _parse_channels(event.payload)
        msg = f"Parsed channels: {channels}"
        logger.info(f"Heypixel: {msg}")
        if write_debug:
            write_debug(msg)
        if channels:
            state.last_server_channels = channels
            state.server_channels.update(channels)
            if any(ch.startswith("heypixel:") for ch in channels):
                state.heypixel_detected = True
                msg = "Detected heypixel channels"
                logger.info(f"Heypixel: {msg}")
                if write_debug:
                    write_debug(msg)
        state.register_recv_count += 1
        merged_source = channels or state.last_server_channels
        merged = _merge_register_channels(merged_source)
        if merged:
            payload = _build_register_payload_from_list(merged)
            payload_hash = hashlib.sha256(payload).hexdigest()
            if payload_hash != state.register_payload_hash:
                msg = "Sending merged register reply"
                logger.info(f"Heypixel: {msg}")
                if write_debug:
                    write_debug(msg)
                try:
                    await event.session.send_plugin_message(PacketDirection.SERVERBOUND, REGISTER_CHANNEL, payload)
                    state.register_payload_hash = payload_hash
                    state.register_replied = True
                    msg = "已回复合并后的通道列表"
                    logger.info(f"Heypixel: {msg}")
                    if write_debug:
                        write_debug(msg)
                except ProtocolError as exc:
                    msg = f"回复 register 失败: {exc}"
                    logger.info(f"Heypixel: {msg}")
                    if write_debug:
                        write_debug(msg)
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
    if SAFE_MINIMAL:
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
    if SAFE_MINIMAL:
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
    if SAFE_MINIMAL:
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
    payload = _encode_event_payload(MSG_OWX2, payload, state=state, encrypt=False)
    try:
        _log_heypixel_payload("send", payload, state, note=f"cps={left_cps}/{right_cps}", logger=logger)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        state.last_cps = (left_cps, right_cps)
    except ProtocolError:
        pass


async def _send_heartbeat(session, state: HeypixelState, logger) -> None:
    payload = _build_heartbeat_payload()
    payload = _encode_event_payload(MSG_FSYR, payload, state=state, encrypt=False)
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
    if SAFE_MINIMAL:
        return

    now_ms = int(time.time() * 1000)
    payload = _build_la5_payload(
        profile_uuid=state.profile_uuid,
        msg_type=MSG_TYPE_INFO,
        text=state.profile_uuid,
        timestamp=now_ms,
        extra="",
        state=state,
        session=session,
    )
    payload = _encode_event_payload(MSG_LA5, payload, state=state, encrypt=True)
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
        session=session,
    )
    payload = _encode_event_payload(MSG_LA5, payload, state=state, encrypt=True)
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
        session=session,
    )
    payload = _encode_event_payload(MSG_LA5, payload, state=state, encrypt=True)
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
        session=session,
    )
    payload = _encode_event_payload(MSG_LA5, payload, state=state, encrypt=True)
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
    payload = _encode_event_payload(MSG_CMESSAGE_BLOCK, payload, state=state, encrypt=False)
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
    return _build_register_payload_from_list(REGISTER_CHANNELS)


def _build_register_payload_from_list(channels: list[str]) -> bytes:
    return "\x00".join(channels).encode("utf-8")


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


def _merge_register_channels(server_channels: list[str]) -> list[str]:
    merged: list[str] = []
    seen = set()
    for channel in list(server_channels) + REGISTER_CHANNELS:
        if not channel or channel in seen:
            continue
        seen.add(channel)
        merged.append(channel)
    return merged


def _encode_event_payload(msg_id: int, payload: bytes, *, state: HeypixelState | None, encrypt: bool) -> bytes:
    """Encode with VarInt msg_id + VarInt(len); body含msgpack时间戳，可选DES."""
    timestamp = _pack_long(int(time.time() * 1000))
    body = timestamp + payload
    if encrypt and state is not None:
        body = _encrypt_payload(state, body)
    # Fantnel/Naven: length = payload_len + 1
    header = write_varint(msg_id) + write_varint(len(body) + 1)
    return header + body


def _decode_event_payload(payload: bytes) -> tuple[int, bytes] | None:
    if not payload:
        return None
    data = payload.tobytes() if isinstance(payload, memoryview) else payload
    # primary: 0xFA + int32 msg_id + VarInt(len) (server->client)
    try:
        offset = 0
        if data[offset] == 0xFA:
            offset += 1
            if offset + 4 > len(data):
                return None
            msg_id = struct.unpack(">i", data[offset : offset + 4])[0]
            offset += 4
            declared_len, size = read_varint(data, offset)
            offset += size
            if declared_len < 0 or offset + declared_len > len(data):
                return None
            return msg_id, data[offset : offset + declared_len]
    except Exception:
        return None
    # fallback: VarInt msg_id + VarInt(len) (client->server echoes or variants)
    try:
        offset = 0
        msg_id, size = read_varint(data, offset)
        offset += size
        declared_len, size = read_varint(data, offset)
        offset += size
        if declared_len < 0 or offset + declared_len > len(data):
            return None
        return msg_id, data[offset : offset + declared_len]
    except Exception:
        return None


def _build_la5_payload(
    *,
    profile_uuid: str,
    msg_type: int,
    text: str,
    timestamp: int,
    extra: str,
    state: HeypixelState,
    session=None,
) -> bytes:
    parts = [
        _mp_string(profile_uuid),
        _pack_byte(msg_type),
        _mp_string(text),
        _pack_long(timestamp),
    ]
    if msg_type == MSG_TYPE_INFO:
        parts.append(_build_info_payload(session, state))
    elif msg_type == MSG_TYPE_BLACK_CLASS:
        parts.append(_build_black_class_payload(state))
    elif msg_type == MSG_TYPE_BLACK_MODULE:
        parts.append(_build_black_module_payload(state))
    elif msg_type == MSG_TYPE_REFLECT_CHECK and extra:
        parts.append(_mp_string(extra))
    return b"".join(parts)


def _build_cps_payload(left_cps: int, right_cps: int) -> bytes:
    # decoded: 只发送左键 CPS，右键固定为 0
    return _pack_int(left_cps) + _pack_int(0)


def _build_heartbeat_payload() -> bytes:
    now_ms = int(time.time() * 1000)
    return _pack_long(now_ms)


def _build_info_payload(session, state: HeypixelState) -> bytes:
    if session is None:
        return _build_info_payload_min(state)
    return _build_info_payload_full(session, state)


def _build_info_payload_min(state: HeypixelState) -> bytes:
    """最小 fallback（避免 session 不可用时崩溃）。"""
    marker = state.enc_zero or "0"
    parts = [
        _pack_int(state.random_id),
        _pack_int(1),
        _pack_int(1),
        _mp_string(marker),
    ]
    return b"".join(parts)


def _build_black_class_payload(state: HeypixelState) -> bytes:
    # decoded ImkX: random_id, 1, 1, enc_zero
    marker = state.enc_zero or "0"
    parts = [
        _pack_int(state.random_id),
        _pack_int(1),
        _pack_int(1),
        _mp_string(marker),
    ]
    return b"".join(parts)


def _build_black_module_payload(state: HeypixelState) -> bytes:
    # decoded yTyX: module_count, 1, 1, enc_profile:enc_zero
    enc_profile = state.enc_profile or (state.profile_uuid or "")
    enc_zero = state.enc_zero or "0"
    marker = f"{enc_profile}:{enc_zero}"
    parts = [
        _pack_int(len(FAKE_MODULE_LIST)),
        _pack_int(1),
        _pack_int(1),
        _mp_string(marker),
    ]
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
    # decoded: string -> long -> int -> (optional string)
    try:
        text, offset = _mp_read_string(payload, offset)
        timestamp, offset = _mp_read_int(payload, offset)
        msg_type, offset = _mp_read_int(payload, offset)
        extra = ""
        if offset < len(payload):
            extra, offset = _mp_read_string(payload, offset)
        return {"text": text, "timestamp": timestamp, "type": msg_type, "extra": extra}
    except Exception:
        # fallback: legacy/prepend timestamp
        offset = 0
        _, offset = _mp_read_int(payload, offset)
        text, offset = _mp_read_string(payload, offset)
        timestamp, offset = _mp_read_int(payload, offset)
        msg_type, offset = _mp_read_int(payload, offset)
        extra = ""
        if offset < len(payload):
            extra, offset = _mp_read_string(payload, offset)
        return {"text": text, "timestamp": timestamp, "type": msg_type, "extra": extra}


def _build_info_payload_full(session, state: HeypixelState) -> bytes:
    rng = _get_stable_rng(session, state)
    profile_uuid = state.profile_uuid or _get_profile_uuid(session) or str(uuid.uuid4())
    if state.random_uuid is None:
        state.random_uuid = str(uuid.UUID(int=rng.getrandbits(128)))

    mod_list = _get_mod_names(session)
    mods = _mp_array([_mp_string(name) for name in mod_list])

    game_path = DEFAULT_GAME_PATH
    jre_path = DEFAULT_JRE_PATH

    cpu_info = _mp_array(
        [
            _mp_string(_fake_cpuid(rng)),
            _mp_string(_fake_cpu_name(rng)),
            _mp_string(_fake_cpuidf(rng)),
        ]
    )
    baseboard_info = _mp_array(
        [
            _mp_string(_fake_baseboard_vendor(rng)),
            _mp_string(_fake_baseboard_model(rng)),
            _mp_string(_fake_baseboard_serial(rng)),
            _mp_string("1.0"),
            _mp_string(str(uuid.UUID(int=rng.getrandbits(128))).upper()),
        ]
    )
    network_info = _mp_array([_build_network_entry(rng)])
    disk_info = _mp_array([_build_disk_entry(rng)])
    netease_emails = _mp_array([])

    # game_session / extra info placeholder
    game_session = _mp_array(
        [
            _mp_string(state.random_uuid),
            _mp_string(profile_uuid),
            _pack_long(int(time.time() * 1000)),
            _pack_long(0),
        ]
    )

    parts = [
        mods,
        _mp_string(game_path),
        _mp_string(jre_path),
        cpu_info,
        baseboard_info,
        network_info,
        disk_info,
        netease_emails,
        game_session,
    ]
    return b"".join(parts)


def _pack_float(value: float) -> bytes:
    return b"\xCA" + struct.pack(">f", float(value))


def _pack_long(value: int) -> bytes:
    v = int(value)
    if -32 <= v <= 127:
        return bytes([v & 0xFF])
    if -128 <= v <= 127:
        return b"\xD0" + struct.pack(">b", v)
    if -32768 <= v <= 32767:
        return b"\xD1" + struct.pack(">h", v)
    if -2147483648 <= v <= 2147483647:
        return b"\xD2" + struct.pack(">i", v)
    if 0 <= v <= 0xFFFFFFFF:
        return b"\xCE" + struct.pack(">I", v)
    return b"\xD3" + struct.pack(">q", v)


def _pack_byte(value: int) -> bytes:
    return _pack_int(value)


def _pack_int(value: int) -> bytes:
    v = int(value)
    if -32 <= v <= 127:
        return bytes([v & 0xFF])
    if -128 <= v <= 127:
        return b"\xD0" + struct.pack(">b", v)
    if -32768 <= v <= 32767:
        return b"\xD1" + struct.pack(">h", v)
    if 0 <= v <= 255:
        return b"\xCC" + struct.pack(">B", v)
    if 256 <= v <= 65535:
        return b"\xCD" + struct.pack(">H", v)
    if -2147483648 <= v <= 2147483647:
        return b"\xD2" + struct.pack(">i", v)
    if 0 <= v <= 0xFFFFFFFF:
        return b"\xCE" + struct.pack(">I", v)
    return b"\xD3" + struct.pack(">q", v)


def _pack_bool(value: bool) -> bytes:
    return b"\xC3" if value else b"\xC2"


def _mp_string(value: str) -> bytes:
    data = value.encode("utf-8")
    length = len(data)
    if length <= 31:
        return bytes([0xA0 | length]) + data
    if length <= 0xFF:
        return b"\xD9" + bytes([length]) + data
    if length <= 0xFFFF:
        return b"\xDA" + struct.pack(">H", length) + data
    return b"\xDB" + struct.pack(">I", length) + data


def _mp_read_int(data: bytes, offset: int) -> tuple[int, int]:
    if offset >= len(data):
        raise ValueError("int out of range")
    b = data[offset]
    offset += 1
    # positive fixint
    if b <= 0x7F:
        return b, offset
    # negative fixint
    if b >= 0xE0:
        return b - 0x100, offset
    if b == 0xD0:
        if offset >= len(data):
            raise ValueError("int8 out of range")
        return struct.unpack(">b", data[offset:offset + 1])[0], offset + 1
    if b == 0xD1:
        end = offset + 2
        if end > len(data):
            raise ValueError("int16 out of range")
        return struct.unpack(">h", data[offset:end])[0], end
    if b == 0xD2:
        end = offset + 4
        if end > len(data):
            raise ValueError("int32 out of range")
        return struct.unpack(">i", data[offset:end])[0], end
    if b == 0xD3:
        end = offset + 8
        if end > len(data):
            raise ValueError("int64 out of range")
        return struct.unpack(">q", data[offset:end])[0], end
    if b == 0xCC:
        if offset >= len(data):
            raise ValueError("uint8 out of range")
        return data[offset], offset + 1
    if b == 0xCD:
        end = offset + 2
        if end > len(data):
            raise ValueError("uint16 out of range")
        return struct.unpack(">H", data[offset:end])[0], end
    if b == 0xCE:
        end = offset + 4
        if end > len(data):
            raise ValueError("uint32 out of range")
        return struct.unpack(">I", data[offset:end])[0], end
    if b == 0xCF:
        end = offset + 8
        if end > len(data):
            raise ValueError("uint64 out of range")
        return struct.unpack(">Q", data[offset:end])[0], end
    raise ValueError(f"unsupported int prefix: {hex(b)}")


def _mp_read_string(data: bytes, offset: int) -> tuple[str, int]:
    if offset >= len(data):
        raise ValueError("string out of range")
    b = data[offset]
    offset += 1
    if 0xA0 <= b <= 0xBF:
        length = b & 0x1F
    elif b == 0xD9:
        if offset >= len(data):
            raise ValueError("str8 length missing")
        length = data[offset]
        offset += 1
    elif b == 0xDA:
        end = offset + 2
        if end > len(data):
            raise ValueError("str16 length missing")
        length = struct.unpack(">H", data[offset:end])[0]
        offset = end
    elif b == 0xDB:
        end = offset + 4
        if end > len(data):
            raise ValueError("str32 length missing")
        length = struct.unpack(">I", data[offset:end])[0]
        offset = end
    else:
        raise ValueError(f"unsupported string prefix: {hex(b)}")
    end = offset + length
    if end > len(data):
        raise ValueError("string body out of range")
    return data[offset:end].decode("utf-8", errors="replace"), end


def _mp_array(items: list[bytes]) -> bytes:
    length = len(items)
    if length <= 0x0F:
        prefix = bytes([0x90 | length])
    elif length <= 0xFFFF:
        prefix = b"\xDC" + struct.pack(">H", length)
    else:
        prefix = b"\xDD" + struct.pack(">I", length)
    return prefix + b"".join(items)


def _get_stable_rng(session, state: HeypixelState) -> random.Random:
    seed = state.profile_uuid or _get_name(session) or "player"
    return random.Random(hash(seed) & 0xFFFFFFFF)


def _get_mod_names(session) -> list[str]:
    cfg = getattr(session, "config", None)
    profile = getattr(cfg, "ygg_profile", None) if cfg else None
    mods: list[str] = []
    if profile and getattr(profile, "mods", None):
        for mod in profile.mods.mods:
            name = getattr(mod, "id", "") or getattr(mod, "name", "")
            if name and name not in mods:
                mods.append(name)
    return mods or list(DEFAULT_MOD_LIST)


def _fake_cpuid(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789ABCDEF") for _ in range(16))


def _fake_cpu_name(rng: random.Random) -> str:
    candidates = [
        "Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz",
        "Intel(R) Core(TM) i5-10400F CPU @ 2.90GHz",
        "AMD Ryzen 5 5600X 6-Core Processor",
    ]
    return rng.choice(candidates)


def _fake_cpuidf(rng: random.Random) -> str:
    return rng.choice(["GenuineIntel", "AuthenticAMD"])


def _fake_baseboard_vendor(rng: random.Random) -> str:
    return rng.choice(["ASUSTeK COMPUTER INC.", "Gigabyte Technology Co., Ltd.", "MSI"])


def _fake_baseboard_model(rng: random.Random) -> str:
    return rng.choice(["PRIME Z390-A", "B550M DS3H", "MAG B460M MORTAR"])


def _fake_baseboard_serial(rng: random.Random) -> str:
    return "".join(rng.choice("0123456789ABCDEF") for _ in range(12))


def _build_network_entry(rng: random.Random) -> bytes:
    mac = ":".join(f"{rng.randint(0,255):02x}" for _ in range(6))
    ip = f"192.168.{rng.randint(0,255)}.{rng.randint(2,254)}"
    return _mp_array(
        [
            _mp_string("wlan0"),
            _mp_string(mac),
            _mp_string("Intel"),
            _mp_array([]),
            _mp_string(f"[{ip}]"),
        ]
    )


def _build_disk_entry(rng: random.Random) -> bytes:
    serial = "".join(rng.choice("0123456789ABCDEF") for _ in range(16))
    name = rng.choice(["NVMe SSD", "Samsung SSD", "KINGSTON SSD"])
    model = rng.choice(["Samsung SSD 970 EVO", "KINGSTON SA400", "WD Blue SN550"])
    return _mp_array([_mp_string(serial), _mp_string(name), _mp_string(model)])
