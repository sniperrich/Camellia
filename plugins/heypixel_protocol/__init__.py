from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import random
import struct
import time
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
    UseItemOnEvent
)


REGISTER_CHANNEL = "minecraft:register"
BRAND_CHANNEL = "minecraft:brand"
HEYPIXEL_CHANNEL = "heypixel:s2cevent"
SYNC_SKINS_CHANNEL = "heypixel:sync_skins"
FLOODGATE_FORM_CHANNEL = "floodgate:form"
FLOODGATE_NETEASE_CHANNEL = "floodgate:netease"
# local key derivation (see _do_local_derive)
SAFE_MINIMAL = False  # 设为 True 可仅回应反射排查踢出；默认关闭以启用完整协议
TARGET_GAME_ID = "4661334467366178884"
TARGET_PROTOCOL = 766
# 对齐官方行为：默认主动回发 register 通道列表。
ENABLE_REGISTER_REPLY = True

# V1-V4 枚举数据（来自 EnumC0625x71658c91.java 第 166-169 行，明文硬编码在字节码中）
# 每个版本：version_nums + rules（(segment_idx, pos, length)）
# 对应 C0532.m5392 密钥派生算法：SHA-256(uuid:userId:versionChar) → splice into random UUID
HEYPIXEL_DERIVE_VERSIONS = [
    {"version_nums": ["4", "1", "5", "2"], "rules": [(1, 0, 2), (2, 2, 1), (4, 4, 3)]},  # V1
    {"version_nums": ["c", "f", "0", "d"], "rules": [(1, 1, 1), (2, 1, 2), (4, 2, 3)]},  # V2
    {"version_nums": ["e", "3", "9", "8"], "rules": [(1, 1, 3), (2, 1, 2), (4, 2, 1)]},  # V3
    {"version_nums": ["a", "b", "7", "6"], "rules": [(1, 1, 2), (2, 1, 2), (4, 2, 2)]},  # V4
]

MSG_LA5 = 1
MSG_FSYR = 7  # HeartbeatTokenPacket (C0169): long + String
MSG_OWX2 = 3  # CPS message
MSG_CMESSAGE_BLOCK = 5
MSG_REFLECT = 101

# 消息类型枚举
MSG_TYPE_INFO = 0
MSG_TYPE_BLACK_CLASS = 1
MSG_TYPE_BLACK_MODULE = 2
MSG_TYPE_REFLECT_CHECK = 3

# 对齐官方插件 Cls_009 构造函数中的固定补充通道集合。
# 最终回包会与服务端下发列表合并并去重。
REGISTER_EXTRA_CHANNELS = [
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
    "heypixel:sync_skins",
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

SYNC_SKINS_PAYLOAD = b"ASQwMDAwMDAwMC0wMDAwLTQwMDAtODAwMC0wMDAwMzliYzYyMTM="

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
    register_candidate_hash: str | None = None
    client_register_payload_hash: str | None = None
    last_server_channels: list[str] = field(default_factory=list)
    server_channels_order: list[str] = field(default_factory=list)
    brand_sent: bool = False
    server_channels: set[str] = field(default_factory=set)
    heypixel_detected: bool = False
    profile_uuid: str | None = None
    user_id: int | None = None
    derived_key: str | None = None
    crypto: "HeypixelCrypto | None" = None
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
    sync_skins_acked: bool = False
    heypixel_event_seen: bool = False

    def cleanup(self) -> None:
        self.game_joined = False
        self.registered = False
        self.register_recv_count = 0
        self.register_replied = False
        self.register_payload_hash = None
        self.register_candidate_hash = None
        self.client_register_payload_hash = None
        self.last_server_channels.clear()
        self.server_channels_order.clear()
        self.server_channels.clear()
        self.brand_sent = False
        self.heypixel_detected = False
        self.info_sent = False
        if self.cps_task and not self.cps_task.done():
            self.cps_task.cancel()
        self.cps_task = None
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        self.heartbeat_task = None
        self.click_tracker = None
        self.enc_profile = None
        self.enc_zero = None
        self.last_cps = None
        self.black_class_sent = False
        self.black_module_sent = False
        self.random_uuid = None
        self.sync_skins_acked = False
        self.heypixel_event_seen = False
        self.sync_token = None


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
    logger.info("Heypixel: options register_reply=%s", ENABLE_REGISTER_REPLY)

    async def on_login_success(event: LoginSuccessEvent) -> None:
        session = event.session
        if TARGET_GAME_ID and getattr(session, "game_id", None) not in (None, TARGET_GAME_ID):
            return
        if (
            TARGET_GAME_ID
            and getattr(session, "game_id", None) == TARGET_GAME_ID
            and getattr(session, "protocol_version", None) not in (None, TARGET_PROTOCOL)
        ):
            logger.warning(
                "Heypixel: unsupported target protocol %s (expected %s), skip plugin init",
                getattr(session, "protocol_version", None),
                TARGET_PROTOCOL,
            )
            return
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        _ensure_identity(session, state)
        # 不在这里主动发送 register/brand，等待客户端发送后拦截替换
        if not SAFE_MINIMAL:
            _do_local_derive(session, state, logger)
            _start_heartbeat_task(session, state, logger)
            logger.info("Heypixel: Login success process initiated (v=%s)", getattr(session, "protocol_version", "unknown"))

    async def on_game_join(event: GameJoinEvent) -> None:
        session = event.session
        if TARGET_GAME_ID and getattr(session, "game_id", None) not in (None, TARGET_GAME_ID):
            return
        if (
            TARGET_GAME_ID
            and getattr(session, "game_id", None) == TARGET_GAME_ID
            and getattr(session, "protocol_version", None) not in (None, TARGET_PROTOCOL)
        ):
            return
        if not _is_supported_version(session):
            return
        state = _get_state(session)
        state.game_joined = True
        _ensure_identity(session, state)
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
        # 仅允许对目标服生效，避免影响其他高版本服务器。
        # game_id 在非网易环境可能为 None，这种情况先观测 register 再决定是否启用。
        if TARGET_GAME_ID:
            gid = getattr(session, "game_id", None)
            if gid is not None and gid != TARGET_GAME_ID:
                return
        if not _is_supported_version(session):
            return
        if _is_target_session(session, _get_state(session)) and getattr(session, "protocol_version", None) not in (
            None,
            TARGET_PROTOCOL,
        ):
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

    # 关键：和 Fantnel 一样，在 base_1200 通道注册 PluginMessage 处理器
    # PLAY + CONFIGURATION 都要处理（CONFIGURATION 阶段有 register/brand）
    events.on("base_1200", on_plugin_message, event_type=PluginMessageEvent)
    events.on("base_1200", on_game_join, event_type=GameJoinEvent)
    events.on("base_1200", on_swing_arm, event_type=SwingArmEvent)
    events.on("base_1200", on_use_item, event_type=UseItemEvent)
    events.on("base_1200", on_use_item_on, event_type=UseItemOnEvent)
    events.on("channel_v1206", on_login_success, event_type=LoginSuccessEvent)


def _handle_serverbound(event: PluginMessageEvent, state: HeypixelState, logger) -> None:
    # 非目标服不做任何修改（但仍允许在 clientbound register 中探测到 heypixel 后再启用）。
    if not _is_target_session(event.session, state):
        return
    if event.identifier == REGISTER_CHANNEL:
        payload = event.payload
        if isinstance(payload, memoryview):
            payload = payload.tobytes()
        elif isinstance(payload, bytearray):
            payload = bytes(payload)
        channels = _parse_channels(payload or b"")
        state.client_register_payload_hash = hashlib.sha256(payload or b"").hexdigest()
        state.registered = True
        # 对齐官方插件：拦截客户端原始 register，由插件自行在时机成熟后回发合并结果。
        event.cancelled = True
        logger.info(
            "Heypixel: dropped serverbound minecraft:register (channels=%s len=%s)",
            len(channels),
            len(payload or b""),
        )
        return
    if event.identifier == BRAND_CHANNEL:
        # 替换 brand 为 "forge"
        event.payload = _build_brand_payload()
        state.brand_sent = True
        logger.debug("Heypixel: 已替换 brand 为 forge")
        return


async def _handle_clientbound(event: PluginMessageEvent, state: HeypixelState, logger, write_debug=None) -> None:
    if event.identifier == FLOODGATE_FORM_CHANNEL:
        state.heypixel_detected = True
        if not _is_target_session(event.session, state):
            return
        await _handle_floodgate_form_probe(event, logger, write_debug)
        return

    if event.identifier == FLOODGATE_NETEASE_CHANNEL:
        state.heypixel_detected = True
        logger.info("Heypixel: observed floodgate:netease payload_len=%s", len(event.payload or b""))
        return

    # 部分服会在配置/游玩阶段下发 sync_skins 的探测/触发包；原版 DLL 会读 3 个字节并回包 [第2字节, 第3字节, 0x30]。
    # 见 reference/decomp_HeypixelProtocol_2.3.0/I0agb8QOeTA1DrpjwQ/jqHTfYHgwMMw5io1rf.cs:WeL1Sdg2X
    if event.identifier == SYNC_SKINS_CHANNEL:
        state.heypixel_detected = True
        if not _is_target_session(event.session, state):
            return
        await _handle_sync_skins_probe(event, state, logger, write_debug)
        return

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
            for channel in channels:
                if channel in state.server_channels:
                    continue
                state.server_channels.add(channel)
                state.server_channels_order.append(channel)
            if any(_is_heypixel_channel(ch) for ch in channels):
                state.heypixel_detected = True
                msg = "Detected heypixel channels"
                logger.info(f"Heypixel: {msg}")
                if write_debug:
                    write_debug(msg)
        # 非目标服：只做探测，不主动回包/改 register，避免影响其他服务器的通道协商。
        if not _is_target_session(event.session, state):
            state.register_recv_count += 1
            return
        state.register_recv_count += 1
        if not ENABLE_REGISTER_REPLY:
            msg = f"register reply disabled (count={state.register_recv_count})"
            logger.info("Heypixel: %s", msg)
            if write_debug:
                write_debug(msg)
            return
        # 对齐官方插件：仅在收到第 2 次 clientbound register 后回一次合并通道列表。
        if state.register_recv_count < 2:
            logger.debug(
                "Heypixel: delay register reply until count=2 (current=%s)",
                state.register_recv_count,
            )
            return
        if state.register_replied:
            logger.debug("Heypixel: register reply already sent, skip duplicate")
            return
        await _send_register_reply_if_needed(event.session, state, logger, write_debug=write_debug, force=False)
        return

    if event.identifier != HEYPIXEL_CHANNEL:
        return

    # 收到 heypixel 专用通道包，标记已检测到。
    state.heypixel_detected = True
    state.heypixel_event_seen = True
    if not state.info_sent:
        await _send_info(event.session, state, logger)
    decoded = _decode_event_payload(event.payload)
    if not decoded:
        return
    msg_id, payload = decoded

    # S2C 114: SyncTokenPacket (C0386) — server sends a token string; store for heartbeat
    if msg_id == 114:
        try:
            token, _ = _mp_read_string(payload, 0)
            state.sync_token = token
            logger.debug("Heypixel: 收到 SyncToken: %.20s...", token)
        except Exception as exc:
            logger.debug("Heypixel: SyncToken 解析失败: %s", exc)
        return

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

    req_type = int(req.get("type") or -1)
    if req_type == MSG_TYPE_REFLECT_CHECK:
        await _send_reflect_response(event.session, state, req, logger)
        return
    if req_type == MSG_TYPE_BLACK_CLASS:
        await _send_black_class_response(event.session, state, req, logger)
        return
    if req_type == MSG_TYPE_BLACK_MODULE:
        await _send_black_module_response(event.session, state, req, logger)
        return


def _ensure_identity(session, state: HeypixelState) -> None:
    if not state.profile_uuid:
        state.profile_uuid = _get_profile_uuid(session)
    if state.user_id is None:
        state.user_id = _get_user_id(session)



def _key_material(profile_uuid: str, user_id: str, version_char: str) -> str:
    """C0532.m5393: SHA-256(uuid:userId:versionChar) → lowercase hex string (64 chars).
    Replicates MessageDigest.getInstance("SHA-256").digest(input.getBytes()) formatted with %02x.
    """
    data = f"{profile_uuid}:{user_id}:{version_char}".encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _derive_key_uuid(profile_uuid: str, user_id: int, base_uuid: str) -> str:
    """C0532.m5392: derive the DES password UUID from profile_uuid, userId, and a base (random) UUID.

    Algorithm (from EnumC0625x71658c91 + C0532.m5392):
      1. Split base_uuid by '-' → 5 segments
      2. Randomly pick a version (V1-V4) and a versionChar from its version_nums array
      3. Set segments[0][2] = versionChar  (marks which version was used)
      4. Compute key_material = SHA-256(profile_uuid:userId:versionChar) → 64-char hex
      5. For each rule (seg_idx, pos, length) at rule_idx:
           chunk = key_material[rule_idx * length : (rule_idx + 1) * length]
           segments[seg_idx] = seg[:pos] + chunk + seg[pos+length:]
      6. Return '-'.join(segments)  ← this UUID string is the PBKDF2 password
    """
    version = random.choice(HEYPIXEL_DERIVE_VERSIONS)
    version_char = random.choice(version["version_nums"])

    segs = list(base_uuid.split("-"))
    if len(segs) != 5:
        raise ValueError(f"base_uuid must be a valid UUID, got: {base_uuid!r}")

    # Mark version in segment[0] position 2
    seg0 = list(segs[0])
    seg0[2] = version_char
    segs[0] = "".join(seg0)

    material = _key_material(profile_uuid, str(user_id), version_char)

    for rule_idx, (seg_idx, pos, length) in enumerate(version["rules"]):
        chunk = material[rule_idx * length : (rule_idx + 1) * length]
        seg = segs[seg_idx]
        segs[seg_idx] = seg[:pos] + chunk + seg[pos + length:]

    return "-".join(segs)


def _do_local_derive(session, state: HeypixelState, logger) -> None:
    """Compute the DES key UUID locally using the algorithm from EnumC0625 + C0532.m5392.
    Sets state.derived_key and state.crypto immediately (no async, no network).
    """
    if state.derived_key and state.crypto:
        return
    if SAFE_MINIMAL:
        return
    profile_uuid = state.profile_uuid or _get_profile_uuid(session) or ""
    if not profile_uuid:
        logger.warning("Heypixel: 无法获取 profile_uuid，跳过密钥派生")
        return
    user_id = state.user_id if state.user_id is not None else _get_user_id(session)
    base_uuid = state.random_uuid or str(uuid.uuid4())
    state.random_uuid = base_uuid
    try:
        key = _derive_key_uuid(profile_uuid, user_id, base_uuid)
        state.derived_key = key
        state.crypto = HeypixelCrypto(key)
        _refresh_encrypted_strings(state)
        logger.info(
            "Heypixel: 本地密钥派生完成 (profile=%.8s... user=%s key=%.12s...)",
            profile_uuid, user_id, key,
        )
    except Exception as exc:
        logger.error("Heypixel: 本地密钥派生失败: %s", exc)


def _cancel_task(task: asyncio.Task | None) -> None:
    if task and not task.done():
        task.cancel()


def _cancel_runtime_tasks(state: HeypixelState) -> None:
    _cancel_task(state.cps_task)
    _cancel_task(state.heartbeat_task)
    state.cps_task = None
    state.heartbeat_task = None



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
    return bool(state.profile_uuid and state.derived_key and state.crypto)


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
    if not state.info_sent:
        return
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
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        state.last_cps = (left_cps, right_cps)
    except ProtocolError:
        pass


async def _send_heartbeat(session, state: HeypixelState, logger) -> None:
    if not state.info_sent:
        return
    payload = _build_heartbeat_payload(state)
    payload = _encode_event_payload(MSG_FSYR, payload, state=state, encrypt=False)
    try:
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
    if not state.heypixel_event_seen:
        logger.debug("Heypixel: skip info send, heypixel event not seen yet")
        return
    if not state.derived_key:
        logger.debug("Heypixel: skip info send, derived_key not ready")
        return
    if state.crypto is None:
        logger.warning("Heypixel: 跳过基础信息发送，原因=派生密钥不可用")
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
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        state.info_sent = True
        logger.info("Heypixel: 已发送基础信息")
    except ProtocolError as exc:
        logger.warning("Heypixel: 发送基础信息失败: %s", exc)


async def _send_reflect_response(session, state: HeypixelState, request: dict[str, object], logger) -> None:
    if not state.profile_uuid:
        state.profile_uuid = _get_profile_uuid(session)
    if not state.profile_uuid:
        return
    if not state.derived_key or state.crypto is None:
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
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        logger.info("Heypixel: 已回应反射校验")
    except ProtocolError as exc:
        logger.warning("Heypixel: 反射校验回应失败: %s", exc)


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
    return _build_register_payload_from_list(REGISTER_EXTRA_CHANNELS)


def _build_register_payload_from_list(channels: list[str]) -> bytes:
    clean: list[str] = []
    seen = set()
    for channel in channels:
        if not channel or channel in seen:
            continue
        seen.add(channel)
        clean.append(channel)
    if not clean:
        return b""
    # 对齐官方 Vvcd52DxKV053tPRIwR.w2pDnotFUo：每个 channel 后都写入 '\0'（含末尾）。
    return ("\x00".join(clean) + "\x00").encode("utf-8")


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
    for channel in list(server_channels) + REGISTER_EXTRA_CHANNELS:
        if not channel or channel in seen:
            continue
        seen.add(channel)
        merged.append(channel)
    return merged


def _is_heypixel_channel(channel: str) -> bool:
    if not channel:
        return False
    if channel.startswith("heypixel:") or channel.startswith("heypixelmod:"):
        return True
    if channel in ("heypixelmod", "heypixel"):
        return True
    return False


def _is_target_session(session, state: HeypixelState) -> bool:
    # 优先用网易 game_id 定位；拿不到时用通道探测兜底。
    if TARGET_GAME_ID:
        gid = getattr(session, "game_id", None)
        if gid == TARGET_GAME_ID:
            return True
        if gid is not None and gid != TARGET_GAME_ID:
            return False
    return bool(state.heypixel_detected)


async def _handle_sync_skins_probe(event: PluginMessageEvent, state: HeypixelState, logger, write_debug=None) -> None:
    payload = event.payload
    if isinstance(payload, memoryview):
        payload = payload.tobytes()
    elif isinstance(payload, bytearray):
        payload = bytes(payload)
    if not payload or len(payload) < 2:
        return
    if state.sync_skins_acked:
        return

    # 原版逻辑会先读掉 1 字节，再取后两字节回包。
    if len(payload) >= 3:
        b0 = payload[1]
        b1 = payload[2]
    else:
        # 兼容异常短包，退化到前两字节。
        b0 = payload[0]
        b1 = payload[1]
    reply = bytes([b0, b1, 0x30])
    try:
        await event.session.send_plugin_message(PacketDirection.SERVERBOUND, SYNC_SKINS_CHANNEL, reply)
        state.sync_skins_acked = True
        msg = f"sync_skins probe ack sent ({reply.hex()})"
        logger.info("Heypixel: %s", msg)
        if write_debug:
            write_debug(msg)
    except ProtocolError as exc:
        msg = f"sync_skins probe ack failed: {exc}"
        logger.debug("Heypixel: %s", msg)
        if write_debug:
            write_debug(msg)


async def _handle_floodgate_form_probe(event: PluginMessageEvent, logger, write_debug=None) -> None:
    payload = event.payload
    if isinstance(payload, memoryview):
        payload = payload.tobytes()
    elif isinstance(payload, bytearray):
        payload = bytes(payload)
    if not payload or len(payload) < 3:
        msg = f"floodgate:form payload too short ({len(payload or b'')})"
        logger.debug("Heypixel: %s", msg)
        if write_debug:
            write_debug(msg)
        return

    # 对齐官方逻辑：读取后两个字节，回发 [b1, b2, 0x30]
    b1 = payload[1]
    b2 = payload[2]
    reply = bytes([b1, b2, 0x30])
    try:
        await event.session.send_plugin_message(PacketDirection.SERVERBOUND, FLOODGATE_FORM_CHANNEL, reply)
        msg = f"floodgate:form confirm sent ({reply.hex()})"
        logger.info("Heypixel: %s", msg)
        if write_debug:
            write_debug(msg)
    except ProtocolError as exc:
        msg = f"floodgate:form confirm failed: {exc}"
        logger.debug("Heypixel: %s", msg)
        if write_debug:
            write_debug(msg)


async def _send_register_reply_if_needed(session, state: HeypixelState, logger, *, write_debug=None, force: bool = False) -> None:
    if not ENABLE_REGISTER_REPLY:
        logger.debug("Heypixel: register reply disabled (observe-only)")
        return
    source_channels = state.server_channels_order if state.server_channels_order else state.last_server_channels
    merged = _merge_register_channels(source_channels)
    if not merged:
        return
    payload = _build_register_payload_from_list(merged)
    if not payload:
        return
    payload_hash = hashlib.sha256(payload).hexdigest()
    state.register_candidate_hash = payload_hash
    if not force and payload_hash == state.register_payload_hash:
        return

    preview_hex = payload[:192].hex()
    msg = (
        f"Sending merged register reply (count={state.register_recv_count}, "
        f"channels={len(merged)}, len={len(payload)}, sha={payload_hash[:12]}, "
        f"list={merged}, preview={preview_hex})"
    )
    logger.info("Heypixel: %s", msg)
    if write_debug:
        write_debug(msg)
    try:
        await session.send_plugin_message(PacketDirection.SERVERBOUND, REGISTER_CHANNEL, payload)
        state.register_payload_hash = payload_hash
        state.register_replied = True
        msg = "已回复合并后的通道列表"
        logger.info("Heypixel: %s", msg)
        if write_debug:
            write_debug(msg)
    except ProtocolError as exc:
        msg = f"回复 register 失败: {exc}"
        logger.info("Heypixel: %s", msg)
        if write_debug:
            write_debug(msg)


def _encode_event_payload(msg_id: int, payload: bytes, *, state: HeypixelState | None, encrypt: bool) -> bytes:
    """C2S wire format: VarInt(msgId) + payload (C0209.m3092 + mo1273 pattern).
    No framing byte, no length prefix — the plugin channel message itself is length-bounded.
    """
    body = payload
    if encrypt and state is not None:
        body = _encrypt_payload(state, body)
    return write_varint(int(msg_id)) + body


def _decode_event_payload(payload: bytes) -> tuple[int, bytes] | None:
    """S2C wire format: int32(4B BE, packetId) + payload body (C0521.m5315.readInt()).
    Returns (msg_id, body) or None if too short.
    """
    data = payload.tobytes() if isinstance(payload, memoryview) else payload
    if len(data) < 4:
        return None
    try:
        msg_id = struct.unpack(">i", data[:4])[0]
        return msg_id, data[4:]
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


def _build_heartbeat_payload(state: HeypixelState) -> bytes:
    """C0169 HeartbeatTokenPacket: long(timestamp) + String(syncToken)."""
    return _pack_long(int(time.time() * 1000)) + _mp_string(state.sync_token or "")


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
    """Serialize as 8-byte big-endian signed int64 (C0413.m4483, Java writeLong)."""
    return struct.pack(">q", int(value))


def _pack_byte(value: int) -> bytes:
    return _pack_int(value)


def _pack_int(value: int) -> bytes:
    """Serialize as 4-byte big-endian signed int32 (C0413.m4490 int field, Java writeInt)."""
    return struct.pack(">i", int(value))


def _pack_bool(value: bool) -> bytes:
    """Serialize as 1-byte boolean (C0413, Java writeBoolean)."""
    return b"\x01" if value else b"\x00"


def _mp_string(value: str) -> bytes:
    """Serialize string as VarInt(byteLen) + UTF-8 bytes (C0413.m4490 String field)."""
    data = value.encode("utf-8")
    return write_varint(len(data)) + data


def _mp_read_int(data: bytes, offset: int) -> tuple[int, int]:
    """Read 4-byte big-endian int32 (mirrors C0413 int field deserialization)."""
    end = offset + 4
    if end > len(data):
        raise ValueError("int32 out of range")
    return struct.unpack(">i", data[offset:end])[0], end


def _mp_read_long(data: bytes, offset: int) -> tuple[int, int]:
    """Read 8-byte big-endian int64 (mirrors C0413 long field deserialization)."""
    end = offset + 8
    if end > len(data):
        raise ValueError("int64 out of range")
    return struct.unpack(">q", data[offset:end])[0], end


def _mp_read_string(data: bytes, offset: int) -> tuple[str, int]:
    """Read VarInt(byteLen) + UTF-8 string (mirrors C0413 String field deserialization)."""
    length, offset = read_varint(data, offset)
    end = offset + length
    if end > len(data):
        raise ValueError("string body out of range")
    return data[offset:end].decode("utf-8", errors="replace"), end


def _mp_array(items: list[bytes]) -> bytes:
    """Serialize list as int32(count) + concatenated items (C0413 array serialization)."""
    return struct.pack(">i", len(items)) + b"".join(items)


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
