from __future__ import annotations

import asyncio
import hashlib
import struct
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path

from camellia.mc.protocol import ProtocolError, read_varint, write_string, write_varint
from camellia.plugins.events import (
    GameJoinEvent,
    LoginSuccessEvent,
    PacketDirection,
    PluginMessageEvent,
    SwingArmEvent,
    UseItemEvent,
    UseItemOnEvent,
)


REGISTER_CHANNEL = "minecraft:register"
BRAND_CHANNEL = "minecraft:brand"
HEYPIXEL_CHANNEL = "heypixel:s2cevent"
SYNC_SKINS_CHANNEL = "heypixel:sync_skins"
FLOODGATE_FORM_CHANNEL = "floodgate:form"
FLOODGATE_NETEASE_CHANNEL = "floodgate:netease"

TARGET_GAME_ID = "4661334467366178884"
TARGET_PROTOCOL = 766
ENABLE_REGISTER_REPLY = True

# Confirmed C2S packets that are safe to emulate with current proxy context.
C2S_HANDSHAKE = 0
C2S_COUNTS = 3
C2S_BLOCK_INTERACTION = 5
C2S_HEARTBEAT = 7

# Confirmed S2C packets we currently consume.
S2C_PLAYER_PROFILE = 101
S2C_REMOTE_EXEC = 107
S2C_SYNC_TOKEN = 114

# Keep the register reply close to the official DLL's extra channel set.
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

# C0267 throttles count reports to ~5572 ms and only sends on change.
CPS_WINDOW_MS = 1000
CPS_SEND_INTERVAL_MS = 5572
HEARTBEAT_INTERVAL_MS = 5000

# We only emit the small-size compact-int cases that are actually needed here.
MAX_SMALL_COMPACT = 0x7F


class ClickTracker:
    def __init__(self) -> None:
        self._left_clicks: deque[int] = deque()
        self._right_clicks: deque[int] = deque()
        self._lock = asyncio.Lock()

    async def record_left(self) -> None:
        async with self._lock:
            self._left_clicks.append(int(time.time() * 1000))

    async def record_right(self) -> None:
        async with self._lock:
            self._right_clicks.append(int(time.time() * 1000))

    async def get_counts(self) -> tuple[int, int]:
        now = int(time.time() * 1000)
        cutoff = now - CPS_WINDOW_MS
        async with self._lock:
            while self._left_clicks and self._left_clicks[0] < cutoff:
                self._left_clicks.popleft()
            while self._right_clicks and self._right_clicks[0] < cutoff:
                self._right_clicks.popleft()
            return len(self._left_clicks), len(self._right_clicks)


@dataclass
class HeypixelState:
    registered: bool = False
    brand_sent: bool = False
    register_recv_count: int = 0
    register_replied: bool = False
    register_payload_hash: str | None = None
    register_candidate_hash: str | None = None
    client_register_payload_hash: str | None = None
    last_server_channels: list[str] = field(default_factory=list)
    server_channels: set[str] = field(default_factory=set)
    server_channels_order: list[str] = field(default_factory=list)

    heypixel_detected: bool = False
    heypixel_event_seen: bool = False
    sync_skins_acked: bool = False
    game_joined: bool = False
    handshake_sent: bool = False
    profile_packet_seen: bool = False

    sync_token: str | None = None
    click_tracker: ClickTracker | None = None
    last_cps: tuple[int, int] | None = None
    cps_task: asyncio.Task | None = None
    heartbeat_task: asyncio.Task | None = None

    def reset_for_login(self) -> None:
        self.registered = False
        self.brand_sent = False
        self.register_recv_count = 0
        self.register_replied = False
        self.register_payload_hash = None
        self.register_candidate_hash = None
        self.client_register_payload_hash = None
        self.last_server_channels.clear()
        self.server_channels.clear()
        self.server_channels_order.clear()

        self.heypixel_detected = False
        self.heypixel_event_seen = False
        self.sync_skins_acked = False
        self.game_joined = False
        self.handshake_sent = False
        self.profile_packet_seen = False
        self.sync_token = None
        self.click_tracker = None
        self.last_cps = None
        self.cancel_runtime_tasks()

    def cancel_runtime_tasks(self) -> None:
        if self.cps_task and not self.cps_task.done():
            self.cps_task.cancel()
        if self.heartbeat_task and not self.heartbeat_task.done():
            self.heartbeat_task.cancel()
        self.cps_task = None
        self.heartbeat_task = None


def setup(context) -> None:
    events = context.events
    logger = context.logger
    debug_path = Path("logs/debug-heypixel.log")
    debug_path.parent.mkdir(parents=True, exist_ok=True)

    def write_debug(message: str) -> None:
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        with debug_path.open("a", encoding="utf-8") as fp:
            fp.write(f"{timestamp} {message}\n")

    logger.info("Heypixel: rewritten protocol plugin loaded")
    write_debug("Heypixel plugin loaded")

    async def on_login_success(event: LoginSuccessEvent) -> None:
        session = event.session
        if not _should_handle_session(session):
            return
        state = _get_state(session)
        state.reset_for_login()

    async def on_game_join(event: GameJoinEvent) -> None:
        session = event.session
        if not _should_handle_session(session):
            return
        state = _get_state(session)
        state.game_joined = True
        if state.click_tracker is None:
            state.click_tracker = ClickTracker()
        await _maybe_activate_runtime(session, state, logger)

    async def on_swing_arm(event: SwingArmEvent) -> None:
        session = event.session
        if not _should_handle_session(session):
            return
        state = _get_state(session)
        if state.click_tracker is None:
            state.click_tracker = ClickTracker()
        await state.click_tracker.record_left()

    async def on_use_item(event: UseItemEvent) -> None:
        session = event.session
        if not _should_handle_session(session):
            return
        state = _get_state(session)
        if state.click_tracker is None:
            state.click_tracker = ClickTracker()
        await state.click_tracker.record_right()

    async def on_use_item_on(event: UseItemOnEvent) -> None:
        session = event.session
        if not _should_handle_session(session):
            return
        if event.cancelled:
            return
        state = _get_state(session)
        if not state.game_joined or not state.heypixel_detected or not event.location:
            return
        await _send_block_message(session, state, event, logger)

    async def on_plugin_message(event: PluginMessageEvent) -> None:
        session = event.session
        if TARGET_GAME_ID:
            gid = getattr(session, "game_id", None)
            if gid is not None and gid != TARGET_GAME_ID:
                return
        if not _is_supported_version(session):
            return
        state_name = getattr(getattr(session, "state", None), "name", None)
        if state_name not in ("PLAY", "CONFIGURATION"):
            return

        state = _get_state(session)
        logger.info("Heypixel: plugin_message dir=%s id=%s", event.direction, event.identifier)
        write_debug(f"Received plugin_message: dir={event.direction} id={event.identifier}")

        if event.direction == PacketDirection.SERVERBOUND:
            _handle_serverbound(event, state, logger)
            return
        if event.direction == PacketDirection.CLIENTBOUND:
            await _handle_clientbound(event, state, logger, write_debug)
            return
        raise ValueError(f"invalid packet direction: {event.direction}")

    events.on("base_1200", on_plugin_message, event_type=PluginMessageEvent)
    events.on("base_1200", on_game_join, event_type=GameJoinEvent)
    events.on("base_1200", on_swing_arm, event_type=SwingArmEvent)
    events.on("base_1200", on_use_item, event_type=UseItemEvent)
    events.on("base_1200", on_use_item_on, event_type=UseItemOnEvent)
    events.on("channel_v1206", on_login_success, event_type=LoginSuccessEvent)


def _get_state(session) -> HeypixelState:
    state = getattr(session, "plugin_data", {}).get("heypixel_protocol")
    if state is None:
        state = HeypixelState()
        session.plugin_data["heypixel_protocol"] = state
    return state


def _should_handle_session(session) -> bool:
    if TARGET_GAME_ID:
        gid = getattr(session, "game_id", None)
        if gid is not None and gid != TARGET_GAME_ID:
            return False
        if gid == TARGET_GAME_ID and getattr(session, "protocol_version", None) not in (None, TARGET_PROTOCOL):
            return False
    return _is_supported_version(session)


def _is_supported_version(session) -> bool:
    version = getattr(session, "protocol_version", 0)
    return version >= 763


def _is_target_session(session, state: HeypixelState) -> bool:
    gid = getattr(session, "game_id", None)
    if TARGET_GAME_ID:
        if gid == TARGET_GAME_ID:
            return True
        if gid is not None and gid != TARGET_GAME_ID:
            return False
    return state.heypixel_detected


def _handle_serverbound(event: PluginMessageEvent, state: HeypixelState, logger) -> None:
    if not _is_target_session(event.session, state):
        return

    if event.identifier == REGISTER_CHANNEL:
        payload = _normalize_bytes(event.payload)
        channels = _parse_channels(payload)
        state.client_register_payload_hash = hashlib.sha256(payload).hexdigest()
        state.registered = True
        event.cancelled = True
        logger.info(
            "Heypixel: dropped client register and will replay merged payload (channels=%s len=%s)",
            len(channels),
            len(payload),
        )
        return

    if event.identifier == BRAND_CHANNEL:
        event.payload = _build_brand_payload()
        state.brand_sent = True
        logger.debug("Heypixel: rewrote brand to forge")


async def _handle_clientbound(event: PluginMessageEvent, state: HeypixelState, logger, write_debug) -> None:
    if event.identifier == FLOODGATE_FORM_CHANNEL:
        state.heypixel_detected = True
        await _maybe_activate_runtime(event.session, state, logger)
        if _is_target_session(event.session, state):
            await _handle_floodgate_form_probe(event, logger, write_debug)
        return

    if event.identifier == FLOODGATE_NETEASE_CHANNEL:
        state.heypixel_detected = True
        await _maybe_activate_runtime(event.session, state, logger)
        logger.info("Heypixel: observed floodgate:netease payload_len=%s", len(_normalize_bytes(event.payload)))
        return

    if event.identifier == SYNC_SKINS_CHANNEL:
        state.heypixel_detected = True
        await _maybe_activate_runtime(event.session, state, logger)
        if _is_target_session(event.session, state):
            await _handle_sync_skins_probe(event, state, logger, write_debug)
        return

    if event.identifier == REGISTER_CHANNEL:
        channels = _parse_channels(_normalize_bytes(event.payload))
        state.register_recv_count += 1
        logger.info("Heypixel: processing REGISTER message (count=%s)", state.register_recv_count)
        write_debug(f"Processing REGISTER message (count={state.register_recv_count})")
        logger.info("Heypixel: parsed channels: %s", channels)
        write_debug(f"Parsed channels: {channels}")

        if channels:
            state.last_server_channels = channels
            for channel in channels:
                if channel in state.server_channels:
                    continue
                state.server_channels.add(channel)
                state.server_channels_order.append(channel)
            if any(_is_heypixel_channel(channel) for channel in channels):
                state.heypixel_detected = True
                write_debug("Detected heypixel channels")

        if not _is_target_session(event.session, state):
            return
        await _maybe_activate_runtime(event.session, state, logger)

        if not ENABLE_REGISTER_REPLY:
            return
        if state.register_recv_count < 2:
            return
        if state.register_replied:
            return
        await _send_register_reply_if_needed(event.session, state, logger, write_debug=write_debug)
        return

    if event.identifier != HEYPIXEL_CHANNEL:
        return

    state.heypixel_detected = True
    state.heypixel_event_seen = True
    await _maybe_activate_runtime(event.session, state, logger)

    decoded = _decode_event_payload(_normalize_bytes(event.payload))
    if decoded is None:
        logger.debug("Heypixel: failed to decode heypixel:s2cevent payload len=%s", len(_normalize_bytes(event.payload)))
        return

    msg_id, payload = decoded
    logger.debug("Heypixel: decoded S2C msg_id=%s payload_len=%s", msg_id, len(payload))

    if msg_id == S2C_SYNC_TOKEN:
        token = _try_read_first_string(payload)
        if token:
            state.sync_token = token
            logger.info("Heypixel: sync token updated len=%s", len(token))
            await _send_heartbeat(event.session, state, logger)
        else:
            logger.debug("Heypixel: failed to parse sync token payload")
        return

    if msg_id == S2C_PLAYER_PROFILE:
        state.profile_packet_seen = True
        logger.info("Heypixel: received S2C 101 profile packet; C0430 upload remains intentionally unimplemented")
        return

    if msg_id == S2C_REMOTE_EXEC:
        logger.warning("Heypixel: received S2C 107 remote-exec trigger; client-side execution is intentionally ignored")
        return

    logger.debug("Heypixel: ignoring S2C msg_id=%s", msg_id)


async def _maybe_activate_runtime(session, state: HeypixelState, logger) -> None:
    if not state.heypixel_detected or not state.game_joined:
        return
    if state.click_tracker is None:
        state.click_tracker = ClickTracker()
    if not state.handshake_sent:
        await _send_handshake(session, state, logger)
    _start_cps_task(session, state, logger)
    _start_heartbeat_task(session, state, logger)


def _start_cps_task(session, state: HeypixelState, logger) -> None:
    if state.cps_task and not state.cps_task.done():
        return

    async def cps_loop() -> None:
        while session.is_active and state.game_joined and state.heypixel_detected:
            try:
                await _send_cps(session, state, logger)
                await asyncio.sleep(CPS_SEND_INTERVAL_MS / 1000.0)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug("Heypixel: CPS task error: %s", exc)

    state.cps_task = session.create_task(cps_loop())


def _start_heartbeat_task(session, state: HeypixelState, logger) -> None:
    if state.heartbeat_task and not state.heartbeat_task.done():
        return

    async def heartbeat_loop() -> None:
        while session.is_active and state.game_joined and state.heypixel_detected:
            try:
                await asyncio.sleep(HEARTBEAT_INTERVAL_MS / 1000.0)
                await _send_heartbeat(session, state, logger)
            except asyncio.CancelledError:
                break
            except Exception as exc:  # pylint: disable=broad-except
                logger.debug("Heypixel: heartbeat task error: %s", exc)

    state.heartbeat_task = session.create_task(heartbeat_loop())


async def _send_handshake(session, state: HeypixelState, logger) -> None:
    if state.handshake_sent:
        return
    try:
        payload = _encode_c2s_message(C2S_HANDSHAKE, b"")
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        state.handshake_sent = True
        logger.info("Heypixel: sent C2S handshake")
    except (ProtocolError, ValueError) as exc:
        logger.debug("Heypixel: handshake send failed: %s", exc)


async def _send_cps(session, state: HeypixelState, logger) -> None:
    if state.click_tracker is None:
        return
    left_cps, right_cps = await state.click_tracker.get_counts()
    if state.last_cps == (left_cps, right_cps):
        return
    try:
        payload = _encode_c2s_message(
            C2S_COUNTS,
            _pack_int(left_cps) + _pack_int(right_cps),
        )
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
        state.last_cps = (left_cps, right_cps)
    except (ProtocolError, ValueError) as exc:
        logger.debug("Heypixel: count send failed: %s", exc)


async def _send_heartbeat(session, state: HeypixelState, logger) -> None:
    if not state.sync_token:
        return
    body = _pack_long(int(time.time() * 1000)) + _mp_string(state.sync_token)
    try:
        payload = _encode_c2s_message(C2S_HEARTBEAT, body)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
    except (ProtocolError, ValueError) as exc:
        logger.debug("Heypixel: heartbeat send failed: %s", exc)


async def _send_block_message(session, state: HeypixelState, event: UseItemOnEvent, logger) -> None:
    if not event.location:
        return

    block_x, block_y, block_z = event.location
    player_pos = getattr(session, "player_pos", None) or (block_x + 0.5, block_y + 0.5, block_z + 0.5)
    player_rot = getattr(session, "player_rot", None) or (0.0, 0.0)
    player_yaw, player_pitch = player_rot

    hit_x = float(block_x) + float(event.cursor_x)
    hit_y = float(block_y) + float(event.cursor_y)
    hit_z = float(block_z) + float(event.cursor_z)

    body = b"".join(
        [
            _pack_double(float(player_pos[0])),
            _pack_double(float(player_pos[1])),
            _pack_double(float(player_pos[2])),
            _pack_int(int(event.face)),
            _pack_int(1),  # BlockHitResult.Type.BLOCK
            _pack_double(hit_x),
            _pack_double(hit_y),
            _pack_double(hit_z),
            _pack_double(float(block_x)),
            _pack_double(float(block_y)),
            _pack_double(float(block_z)),
            _pack_bool(bool(event.inside_block)),
            _pack_float(float(player_pitch)),
            _pack_float(float(player_yaw)),
            _pack_bool(int(event.hand) == 0),
        ]
    )

    try:
        payload = _encode_c2s_message(C2S_BLOCK_INTERACTION, body)
        await session.send_plugin_message(PacketDirection.SERVERBOUND, HEYPIXEL_CHANNEL, payload)
    except (ProtocolError, ValueError) as exc:
        logger.debug("Heypixel: block interaction send failed: %s", exc)


async def _handle_sync_skins_probe(event: PluginMessageEvent, state: HeypixelState, logger, write_debug) -> None:
    payload = _normalize_bytes(event.payload)
    if len(payload) < 2 or state.sync_skins_acked:
        return

    if len(payload) >= 3:
        b0 = payload[1]
        b1 = payload[2]
    else:
        b0 = payload[0]
        b1 = payload[1]
    reply = bytes([b0, b1, 0x30])

    try:
        await event.session.send_plugin_message(PacketDirection.SERVERBOUND, SYNC_SKINS_CHANNEL, reply)
        state.sync_skins_acked = True
        logger.info("Heypixel: sync_skins probe ack sent (%s)", reply.hex())
        write_debug(f"sync_skins probe ack sent ({reply.hex()})")
    except ProtocolError as exc:
        logger.debug("Heypixel: sync_skins probe ack failed: %s", exc)


async def _handle_floodgate_form_probe(event: PluginMessageEvent, logger, write_debug) -> None:
    payload = _normalize_bytes(event.payload)
    if len(payload) < 3:
        logger.debug("Heypixel: floodgate:form payload too short (%s)", len(payload))
        return

    reply = bytes([payload[1], payload[2], 0x30])
    try:
        await event.session.send_plugin_message(PacketDirection.SERVERBOUND, FLOODGATE_FORM_CHANNEL, reply)
        logger.info("Heypixel: floodgate:form confirm sent (%s)", reply.hex())
        write_debug(f"floodgate:form confirm sent ({reply.hex()})")
    except ProtocolError as exc:
        logger.debug("Heypixel: floodgate:form confirm failed: %s", exc)


async def _send_register_reply_if_needed(session, state: HeypixelState, logger, *, write_debug=None) -> None:
    source_channels = state.server_channels_order or state.last_server_channels
    merged = _merge_register_channels(source_channels)
    payload = _build_register_payload_from_list(merged)
    if not payload:
        return

    payload_hash = hashlib.sha256(payload).hexdigest()
    state.register_candidate_hash = payload_hash
    if payload_hash == state.register_payload_hash:
        return

    logger.info(
        "Heypixel: sending merged register reply (count=%s, channels=%s, len=%s, sha=%s)",
        state.register_recv_count,
        len(merged),
        len(payload),
        payload_hash[:12],
    )
    if write_debug:
        write_debug(
            f"Sending merged register reply (count={state.register_recv_count}, channels={len(merged)}, "
            f"len={len(payload)}, sha={payload_hash[:12]}, list={merged}, preview={payload[:192].hex()})"
        )

    try:
        await session.send_plugin_message(PacketDirection.SERVERBOUND, REGISTER_CHANNEL, payload)
        state.register_payload_hash = payload_hash
        state.register_replied = True
        logger.info("Heypixel: merged register reply sent")
        if write_debug:
            write_debug("已回复合并后的通道列表")
    except ProtocolError as exc:
        logger.info("Heypixel: register reply failed: %s", exc)


def _decode_event_payload(payload: bytes) -> tuple[int, bytes] | None:
    if len(payload) < 4:
        return None
    msg_id = struct.unpack(">i", payload[:4])[0]
    rest = payload[4:]
    return msg_id, _unwrap_small_length_prefix(rest)


def _unwrap_small_length_prefix(payload: bytes) -> bytes:
    if not payload:
        return b""
    length, size = _read_small_compact_uint(payload, 0)
    if length is None:
        return payload
    remaining = len(payload) - size
    if length in (remaining, remaining + 1):
        return payload[size:]
    return payload


def _try_read_first_string(payload: bytes) -> str | None:
    for candidate in (payload, _unwrap_small_length_prefix(payload)):
        try:
            return _mp_read_string(candidate, 0)[0]
        except Exception:  # pylint: disable=broad-except
            continue
    return None


def _encode_c2s_message(msg_id: int, body: bytes) -> bytes:
    payload = _pack_long(int(time.time() * 1000)) + body
    header = _encode_small_compact_uint(msg_id)
    # Original C0188.m2892 writes payload length + 1 before the raw serialized body.
    length = _encode_small_compact_uint(len(payload) + 1)
    return header + length + payload


def _encode_small_compact_uint(value: int) -> bytes:
    if value < 0 or value > MAX_SMALL_COMPACT:
        raise ValueError(f"small compact int out of range: {value}")
    return bytes([value])


def _read_small_compact_uint(data: bytes, offset: int) -> tuple[int | None, int]:
    if offset >= len(data):
        return None, 0
    value = data[offset]
    if value > MAX_SMALL_COMPACT:
        return None, 0
    return value, 1


def _build_brand_payload() -> bytes:
    return write_string("forge")


def _build_register_payload_from_list(channels: list[str]) -> bytes:
    ordered: list[str] = []
    seen: set[str] = set()
    for channel in channels:
        if not channel or channel in seen:
            continue
        seen.add(channel)
        ordered.append(channel)
    if not ordered:
        return b""
    return ("\x00".join(ordered) + "\x00").encode("utf-8")


def _merge_register_channels(server_channels: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for channel in list(server_channels) + REGISTER_EXTRA_CHANNELS:
        if not channel or channel in seen:
            continue
        seen.add(channel)
        merged.append(channel)
    return merged


def _parse_channels(payload: bytes) -> list[str]:
    if not payload:
        return []
    try:
        raw = payload.decode("utf-8", errors="replace")
    except UnicodeDecodeError:
        return []
    return [item for item in raw.split("\x00") if item]


def _is_heypixel_channel(channel: str) -> bool:
    if not channel:
        return False
    return channel.startswith("heypixel:") or channel.startswith("heypixelmod:") or channel in {"heypixel", "heypixelmod"}


def _normalize_bytes(payload: bytes | bytearray | memoryview | None) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, memoryview):
        return payload.tobytes()
    if isinstance(payload, bytearray):
        return bytes(payload)
    return payload


def _pack_bool(value: bool) -> bytes:
    return b"\x01" if value else b"\x00"


def _pack_int(value: int) -> bytes:
    return struct.pack(">i", int(value))


def _pack_long(value: int) -> bytes:
    return struct.pack(">q", int(value))


def _pack_float(value: float) -> bytes:
    return struct.pack(">f", float(value))


def _pack_double(value: float) -> bytes:
    return struct.pack(">d", float(value))


def _mp_string(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return write_varint(len(encoded)) + encoded


def _mp_read_string(data: bytes, offset: int) -> tuple[str, int]:
    length, size = read_varint(data, offset)
    start = offset + size
    end = start + length
    if end > len(data):
        raise ValueError("string body out of range")
    return data[start:end].decode("utf-8", errors="replace"), end
