from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import struct
import uuid
from datetime import datetime
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, Optional

from Crypto.Cipher import AES, PKCS1_v1_5
from Crypto.PublicKey import RSA

from .protocol import (
    PacketFramer,
    ProtocolError,
    compress_packet,
    decompress_packet,
    read_bool,
    read_byte_array,
    read_bytes,
    read_string,
    read_ushort,
    read_varint,
    wrap_packet,
    write_byte_array,
    write_string,
    write_ushort,
    write_varint,
)
from .yggdrasil import GameProfile, StandardYggdrasil, UserProfile, YggdrasilData
from ..plugins.events import (
    AnimationEvent,
    ChatMessageEvent,
    GameJoinEvent,
    InteractEvent,
    LoginSuccessEvent,
    PacketDirection,
    PlayerPositionEvent,
    PluginMessageEvent,
    SetEntityMetadataEvent,
    SwingArmEvent,
    UseItemEvent,
    UseItemOnEvent,
    get_event_bus,
)

# 对高版本(尤其 1.20.6)兼容性优先：默认不向客户端注入自定义欢迎聊天包。
ENABLE_WELCOME_PACKET = False
HEYPIXEL_GAME_ID = "4661334467366178884"
# 临时兼容补丁默认关闭：直接丢 0x58/0x60 会影响大厅同步，优先保留原始包透传。
ENABLE_HEYPIXEL_SAFE_DROP = False
HEYPIXEL_SAFE_DROP_IDS = {0x58, 0x60}


class ConnectionState(IntEnum):
    HANDSHAKE = 0
    STATUS = 1
    LOGIN = 2
    PLAY = 3
    CONFIGURATION = 4


class ProtocolVersion(IntEnum):
    V1076 = 5
    V108X = 47
    V1122 = 340
    V1165 = 754
    V1180 = 757
    V1200 = 763
    V1206 = 766
    V1210 = 767


@dataclass
class ProxyConfig:
    listen_host: str
    listen_port: int
    forward_host: str
    forward_port: int
    nickname: str
    game_id: Optional[str] = None
    ygg_profile: Optional[GameProfile] = None
    ygg_data: Optional[YggdrasilData] = None


class _Cfb8Cipher:
    def __init__(self, key: bytes) -> None:
        self._encryptor = AES.new(key, AES.MODE_CFB, iv=key, segment_size=8)
        self._decryptor = AES.new(key, AES.MODE_CFB, iv=key, segment_size=8)

    def encrypt(self, data: bytes) -> bytes:
        return self._encryptor.encrypt(data)

    def decrypt(self, data: bytes) -> bytes:
        return self._decryptor.decrypt(data)


class PacketLogger:
    def __init__(self, logger: logging.Logger) -> None:
        self._logger = logger
        self._handle: Optional[Any] = None
        self._path: Optional[str] = None
        session_id = f"{datetime.now():%Y%m%d-%H%M%S}-{os.getpid()}-{id(self):x}"
        try:
            base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir, os.pardir, "logs"))
            os.makedirs(base_dir, exist_ok=True)
            self._path = os.path.join(base_dir, f"packets-{session_id}.log")
            self._handle = open(self._path, "a", encoding="utf-8", buffering=1)
            self._handle.write(f"# packet_log {datetime.now().isoformat(timespec='seconds')}\n")
        except OSError as exc:
            self._logger.warning("Packet log disabled: %s", exc)
            self._handle = None
            self._path = None

    @property
    def path(self) -> Optional[str]:
        return self._path

    def log(
        self,
        direction: str,
        state: ConnectionState,
        proto: Optional[int],
        packet_id: int,
        raw_len: int,
        payload_len: int,
    ) -> None:
        if self._handle is None:
            return
        timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
        proto_label = str(proto) if proto is not None else "-"
        line = (
            f"{timestamp} {direction} state={state.name} proto={proto_label} "
            f"id=0x{packet_id:02X} raw={raw_len} len={payload_len}"
        )
        try:
            self._handle.write(line + "\n")
        except OSError:
            self._handle = None

    def close(self) -> None:
        if self._handle is None:
            return
        try:
            self._handle.close()
        finally:
            self._handle = None


class MinecraftProxy:
    def __init__(self, config: ProxyConfig) -> None:
        self.config = config
        self._logger = logging.getLogger("camellia.proxy")
        self._server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> asyncio.AbstractServer:
        self._server = await asyncio.start_server(self._handle_client, self.config.listen_host, self.config.listen_port)
        addrs = ", ".join(str(sock.getsockname()) for sock in self._server.sockets or [])
        self._logger.info("Proxy listening on %s -> %s:%s", addrs, self.config.forward_host, self.config.forward_port)
        self._logger.info("[ProxyPhase] listening %s -> %s:%s", addrs, self.config.forward_host, self.config.forward_port)
        return self._server

    async def serve(self) -> None:
        server = await self.start()
        async with server:
            await server.serve_forever()

    async def stop(self) -> None:
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()

    async def _handle_client(self, client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
        peer = client_writer.get_extra_info("peername")
        self._logger.info("Client connected: %s", peer)
        self._logger.info("[ProxyPhase] client connected %s", peer)
        try:
            self._logger.info("[ProxyPhase] connecting server %s:%s", self.config.forward_host, self.config.forward_port)
            server_reader, server_writer = await asyncio.open_connection(self.config.forward_host, self.config.forward_port)
        except OSError as exc:
            self._logger.error("Failed to connect to server: %s", exc)
            self._logger.warning("[ProxyPhase] connect failed: %s", exc)
            client_writer.close()
            await client_writer.wait_closed()
            return
        self._logger.info("[ProxyPhase] server connected %s:%s", self.config.forward_host, self.config.forward_port)

        session = _ProxySession(self.config, client_reader, client_writer, server_reader, server_writer, self._logger)
        await session.run()


class _ProxySession:
    def __init__(
        self,
        config: ProxyConfig,
        client_reader: asyncio.StreamReader,
        client_writer: asyncio.StreamWriter,
        server_reader: asyncio.StreamReader,
        server_writer: asyncio.StreamWriter,
        logger: logging.Logger,
    ) -> None:
        self.config = config
        self.client_reader = client_reader
        self.client_writer = client_writer
        self.server_reader = server_reader
        self.server_writer = server_writer
        self.logger = logger

        self.state = ConnectionState.HANDSHAKE
        self.protocol_version: Optional[int] = None
        self.game_id = config.game_id

        self._packet_logger = PacketLogger(self.logger)
        if self._packet_logger.path:
            self.logger.info("Packet log: %s", self._packet_logger.path)

        self.server_compression_threshold: Optional[int] = None
        self.client_compression_threshold: Optional[int] = None
        self._cipher: Optional[_Cfb8Cipher] = None

        self._client_framer = PacketFramer()
        self._server_framer = PacketFramer()
        self._events = get_event_bus()
        self._tasks: set[asyncio.Task] = set()
        self._closed = False
        self.plugin_data: dict[str, Any] = {}
        self._pending_client_packets: list[tuple[bytes, bool]] = []
        self._welcome_pending = False
        self._welcome_sent = False
        self._play_seen = False
        self._name_maps: dict[str, dict[str, str]] = {
            "objective": {},
            "team": {},
            "player": {},
        }
        self.player_uuid: str | None = None
        self.player_pos: tuple[float, float, float] | None = None
        self.player_rot: tuple[float, float] | None = None
        self.player_on_ground: bool | None = None
        self._session_id = f"{self.config.nickname}-{id(self):x}"

    def _phase(self, message: str) -> None:
        self.logger.info("[ProxyPhase] %s %s", self._session_id, message)

    async def run(self) -> None:
        self._phase(
            f"session start listen={self.config.listen_host}:{self.config.listen_port} "
            f"forward={self.config.forward_host}:{self.config.forward_port}"
        )
        client_task = asyncio.create_task(self._client_loop(), name="proxy_client_loop")
        server_task = asyncio.create_task(self._server_loop(), name="proxy_server_loop")
        done, pending = await asyncio.wait({client_task, server_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        for task in done:
            loop_name = "client" if task is client_task else "server"
            exc = task.exception()
            if exc:
                self.logger.warning("[ProxyPhase] %s loop ended with error: %s", loop_name, exc)
            else:
                self.logger.info("[ProxyPhase] %s loop ended", loop_name)
        await self._close()
        self._phase("session closed")

    async def _close(self) -> None:
        self._closed = True
        if self._tasks:
            for task in list(self._tasks):
                task.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._packet_logger.close()
        for writer in (self.client_writer, self.server_writer):
            if not writer.is_closing():
                writer.close()
                await writer.wait_closed()

    async def _client_loop(self) -> None:
        while True:
            data = await self.client_reader.read(4096)
            if not data:
                self._phase("client stream closed")
                break
            for frame in self._client_framer.feed(data):
                raw_payload = frame.payload
                raw_len = len(raw_payload)
                payload = raw_payload
                if self.client_compression_threshold is not None:
                    try:
                        payload = decompress_packet(payload, self.client_compression_threshold)
                    except ProtocolError as exc:
                        self.logger.warning("Client packet decompress failed: %s", exc)
                        continue
                try:
                    packet_id, size = read_varint(payload, 0)
                except ProtocolError as exc:
                    self.logger.warning("Client packet parse failed: %s", exc)
                    continue
                body = payload[size:]
                self._packet_logger.log(
                    "C->S",
                    self.state,
                    self.protocol_version,
                    packet_id,
                    raw_len,
                    len(payload),
                )
                new_payload = await self._handle_client_packet(packet_id, payload, body)
                if new_payload is None:
                    continue
                if new_payload is payload:
                    await self._send_frame_to_server_raw(raw_payload)
                else:
                    self.logger.debug(
                        "[ProxyPhase] rewrite C->S id=0x%02X raw=%s->%s payload=%s->%s",
                        packet_id,
                        raw_len,
                        len(new_payload),
                        len(payload),
                        len(new_payload),
                    )
                    await self._send_to_server(new_payload)

    async def _server_loop(self) -> None:
        while True:
            data = await self.server_reader.read(4096)
            if not data:
                self._phase("server stream closed")
                break
            if self._cipher is not None:
                data = self._cipher.decrypt(data)
            for frame in self._server_framer.feed(data):
                raw_payload = frame.payload
                raw_len = len(raw_payload)
                payload = raw_payload
                if self.server_compression_threshold is not None:
                    try:
                        payload = decompress_packet(payload, self.server_compression_threshold)
                    except ProtocolError as exc:
                        self.logger.warning("Server packet decompress failed: %s", exc)
                        continue
                try:
                    packet_id, size = read_varint(payload, 0)
                except ProtocolError as exc:
                    self.logger.warning("Server packet parse failed: %s", exc)
                    continue
                body = payload[size:]
                state_before = self.state
                self._packet_logger.log(
                    "S->C",
                    self.state,
                    self.protocol_version,
                    packet_id,
                    raw_len,
                    len(payload),
                )
                new_payload = await self._handle_server_packet(packet_id, payload, body)
                if new_payload is None:
                    continue
                if new_payload is payload:
                    await self._send_frame_to_client_raw(raw_payload)
                else:
                    self.logger.debug(
                        "[ProxyPhase] rewrite S->C id=0x%02X raw=%s->%s payload=%s->%s",
                        packet_id,
                        raw_len,
                        len(new_payload),
                        len(payload),
                        len(new_payload),
                    )
                    await self._send_to_client(new_payload)
                if state_before == ConnectionState.PLAY:
                    self._mark_play_packet(packet_id)
                await self._flush_pending_client_packets()

    async def _send_to_server(self, payload: bytes, *, force_uncompressed: bool = False, force_unencrypted: bool = False) -> None:
        data = payload
        if self.server_compression_threshold is not None and not force_uncompressed:
            data = compress_packet(payload, self.server_compression_threshold)
        data = wrap_packet(data)
        if self._cipher is not None and not force_unencrypted:
            data = self._cipher.encrypt(data)
        self.server_writer.write(data)
        await self.server_writer.drain()

    async def _send_frame_to_server_raw(self, frame_payload: bytes, *, force_unencrypted: bool = False) -> None:
        """Forward a pre-framed packet payload as-is (already compressed/uncompressed for this direction)."""
        data = wrap_packet(frame_payload)
        if self._cipher is not None and not force_unencrypted:
            data = self._cipher.encrypt(data)
        self.server_writer.write(data)
        await self.server_writer.drain()

    async def _send_to_client(self, payload: bytes, *, force_uncompressed: bool = False) -> None:
        data = payload
        if self.client_compression_threshold is not None and not force_uncompressed:
            data = compress_packet(payload, self.client_compression_threshold)
        data = wrap_packet(data)
        self.client_writer.write(data)
        await self.client_writer.drain()

    async def _send_frame_to_client_raw(self, frame_payload: bytes) -> None:
        """Forward a pre-framed packet payload as-is (already compressed/uncompressed for this direction)."""
        data = wrap_packet(frame_payload)
        self.client_writer.write(data)
        await self.client_writer.drain()

    def _queue_client_packet(self, payload: bytes, *, force_uncompressed: bool = False) -> None:
        if payload:
            self._pending_client_packets.append((payload, force_uncompressed))

    async def _flush_pending_client_packets(self) -> None:
        if not self._pending_client_packets:
            return
        queued = self._pending_client_packets
        self._pending_client_packets = []
        for payload, force_uncompressed in queued:
            await self._send_to_client(payload, force_uncompressed=force_uncompressed)

    def create_task(self, coro: asyncio.Future) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    @property
    def is_active(self) -> bool:
        return not self._closed and not self.client_writer.is_closing() and not self.server_writer.is_closing()

    async def send_plugin_message(self, direction: PacketDirection, identifier: str, payload: bytes) -> None:
        packet_id = self._get_plugin_message_id(direction)
        if packet_id is None:
            self.logger.warning("Unsupported protocol for plugin message: %s", self.protocol_version)
            return
        if isinstance(payload, memoryview):
            payload = payload.tobytes()
        elif isinstance(payload, bytearray):
            payload = bytes(payload)
        body = write_string(identifier, 32767) + (payload or b"")
        packet = write_varint(packet_id) + body
        if direction == PacketDirection.SERVERBOUND:
            await self._send_to_server(packet)
        else:
            await self._send_to_client(packet)

    async def _emit_login_success(self) -> None:
        event = LoginSuccessEvent(session=self)
        await self._events.emit("login_success", event)
        await self._events.emit("channel_v1122", event)
        if self._is_proto(ProtocolVersion.V1206):
            await self._events.emit("channel_v1206", event)
        self._welcome_pending = ENABLE_WELCOME_PACKET

    def _mark_play_packet(self, packet_id: int) -> None:
        if not ENABLE_WELCOME_PACKET:
            return
        if self._play_seen:
            return
        if packet_id == self._get_play_disconnect_id():
            return
        self._play_seen = True
        if not self._welcome_pending or self._welcome_sent:
            return
        packet = self._build_welcome_packet()
        if packet is None:
            return
        self._queue_client_packet(packet)
        self._welcome_sent = True

    def _build_welcome_packet(self) -> Optional[bytes]:
        nickname = self.config.nickname or "玩家"
        message_text = f"欢迎{nickname}，祝你游戏愉快！Ft.Camellia"
        try:
            if self.protocol_version == int(ProtocolVersion.V108X):
                payload = {"text": message_text}
                message = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
                return write_varint(0x02) + write_string(message, 32767) + b"\x00"
            if self.protocol_version == int(ProtocolVersion.V1206):
                text_bytes = message_text.encode("utf-8")
                if len(text_bytes) > 32767:
                    raise ProtocolError("welcome message too long")
                body = b"\x08" + len(text_bytes).to_bytes(2, "big") + text_bytes + b"\x00"
                return write_varint(0x6C) + body
        except ProtocolError as exc:
            self.logger.debug("Welcome chat build failed: %s", exc)
        return None

    def _get_v120x_id(self, id_v1200: int, id_v1206: int) -> Optional[int]:
        if self.protocol_version == int(ProtocolVersion.V1200):
            return id_v1200
        if self.protocol_version == int(ProtocolVersion.V1206):
            return id_v1206
        return None

    @staticmethod
    def _read_double(data: bytes, offset: int) -> tuple[float, int]:
        end = offset + 8
        if end > len(data):
            raise ProtocolError("double out of range")
        return struct.unpack_from(">d", data, offset)[0], 8

    @staticmethod
    def _read_float(data: bytes, offset: int) -> tuple[float, int]:
        end = offset + 4
        if end > len(data):
            raise ProtocolError("float out of range")
        return struct.unpack_from(">f", data, offset)[0], 4

    @staticmethod
    def _read_long(data: bytes, offset: int) -> tuple[int, int]:
        end = offset + 8
        if end > len(data):
            raise ProtocolError("long out of range")
        return int.from_bytes(data[offset:end], "big", signed=False), 8

    @classmethod
    def _read_position(cls, data: bytes, offset: int) -> tuple[tuple[int, int, int], int]:
        value, size = cls._read_long(data, offset)
        x = value >> 38
        y = value & 0xFFF
        z = (value >> 12) & 0x3FFFFFF
        if x >= 1 << 25:
            x -= 1 << 26
        if y >= 1 << 11:
            y -= 1 << 12
        if z >= 1 << 25:
            z -= 1 << 26
        return (x, y, z), size

    @staticmethod
    def _write_position(pos: tuple[int, int, int]) -> bytes:
        x, y, z = pos
        value = ((x & 0x3FFFFFF) << 38) | ((z & 0x3FFFFFF) << 12) | (y & 0xFFF)
        return int(value).to_bytes(8, "big", signed=False)

    def _update_player_state(self, packet_id: int, body: bytes) -> bool:
        pos_id = self._get_v120x_id(0x14, 0x1A)
        pos_rot_id = self._get_v120x_id(0x15, 0x1B)
        rot_id = self._get_v120x_id(0x16, 0x1C)
        on_ground_id = self._get_v120x_id(0x17, 0x1D)
        try:
            if pos_id is not None and packet_id == pos_id:
                offset = 0
                x, size = self._read_double(body, offset)
                offset += size
                y, size = self._read_double(body, offset)
                offset += size
                z, size = self._read_double(body, offset)
                offset += size
                on_ground, _ = read_bool(body, offset)
                self.player_pos = (x, y, z)
                self.player_on_ground = on_ground
                return True
            if pos_rot_id is not None and packet_id == pos_rot_id:
                offset = 0
                x, size = self._read_double(body, offset)
                offset += size
                y, size = self._read_double(body, offset)
                offset += size
                z, size = self._read_double(body, offset)
                offset += size
                yaw, size = self._read_float(body, offset)
                offset += size
                pitch, size = self._read_float(body, offset)
                offset += size
                on_ground, _ = read_bool(body, offset)
                self.player_pos = (x, y, z)
                self.player_rot = (yaw, pitch)
                self.player_on_ground = on_ground
                return True
            if rot_id is not None and packet_id == rot_id:
                offset = 0
                yaw, size = self._read_float(body, offset)
                offset += size
                pitch, size = self._read_float(body, offset)
                offset += size
                on_ground, _ = read_bool(body, offset)
                self.player_rot = (yaw, pitch)
                self.player_on_ground = on_ground
                return True
            if on_ground_id is not None and packet_id == on_ground_id:
                on_ground, _ = read_bool(body, 0)
                self.player_on_ground = on_ground
                return True
        except ProtocolError as exc:
            self.logger.debug("Player state parse failed: %s", exc)
        return False

    async def _handle_plugin_message(
        self,
        direction: PacketDirection,
        packet_id: int,
        payload: bytes,
        body: bytes,
    ) -> Optional[bytes]:
        try:
            identifier, size = read_string(body, 0, 32767)
        except ProtocolError as exc:
            preview = body[:96].hex()
            self.logger.warning(
                "Plugin message parse failed: dir=%s state=%s proto=%s packet_id=0x%02X body_len=%s err=%s preview=%s",
                direction.name,
                self.state.name,
                self.protocol_version,
                packet_id,
                len(body),
                exc,
                preview,
            )
            return payload
        data = body[size:]
        event = PluginMessageEvent(
            session=self,
            direction=direction,
            identifier=identifier,
            payload=data,
        )
        await self._events.emit("plugin_message", event)
        if event.cancelled:
            self.logger.info(
                "Plugin message cancelled: dir=%s state=%s proto=%s id=%s payload_len=%s",
                direction.name,
                self.state.name,
                self.protocol_version,
                identifier,
                len(data),
            )
            return None
        new_identifier = event.identifier
        new_payload = event.payload
        if isinstance(new_payload, memoryview):
            new_payload = new_payload.tobytes()
        elif isinstance(new_payload, bytearray):
            new_payload = bytes(new_payload)
        if new_identifier != identifier or new_payload != data:
            return write_varint(packet_id) + write_string(new_identifier, 32767) + (new_payload or b"")
        return payload

    def _get_plugin_message_id(self, direction: PacketDirection) -> Optional[int]:
        if self.protocol_version == int(ProtocolVersion.V108X):
            return 0x17 if direction == PacketDirection.SERVERBOUND else 0x3F
        if self.protocol_version == int(ProtocolVersion.V1122):
            return 0x09 if direction == PacketDirection.SERVERBOUND else 0x18
        if self.protocol_version == int(ProtocolVersion.V1200):
            return 0x0D if direction == PacketDirection.SERVERBOUND else 0x17
        if self.protocol_version == int(ProtocolVersion.V1206):
            if self.state == ConnectionState.CONFIGURATION:
                return 0x02 if direction == PacketDirection.SERVERBOUND else 0x01
            return 0x12 if direction == PacketDirection.SERVERBOUND else 0x19
        return None

    async def _handle_client_packet(self, packet_id: int, payload: bytes, body: bytes) -> Optional[bytes]:
        if self.state == ConnectionState.HANDSHAKE and packet_id == 0:
            self._phase("client handshake")
            return self._rewrite_handshake(body)
        if self.state == ConnectionState.LOGIN and packet_id == 0:
            self._phase("client login start")
            return self._rewrite_login_start(body)
        if self.state == ConnectionState.LOGIN and packet_id == 3 and self._is_proto(ProtocolVersion.V1206):
            self.state = ConnectionState.CONFIGURATION
            self.logger.info("Client login acknowledged, entering configuration")
            self._phase("client login ack -> configuration")
            return payload
        if self.state == ConnectionState.CONFIGURATION and self._is_proto(ProtocolVersion.V1206):
            config_plugin_id = self._get_plugin_message_id(PacketDirection.SERVERBOUND)
            if config_plugin_id is not None and packet_id == config_plugin_id:
                return await self._handle_plugin_message(PacketDirection.SERVERBOUND, packet_id, payload, body)
        if self.state == ConnectionState.CONFIGURATION and packet_id == 3:
            self.state = ConnectionState.PLAY
            self.logger.info("Client finished configuration, entering play")
            self._phase("client finished configuration -> play")
            await self._emit_login_success()
            return payload
        if self.state == ConnectionState.PLAY:
            if self.protocol_version == int(ProtocolVersion.V108X):
                if packet_id == 0x17:
                    return await self._handle_plugin_message(PacketDirection.SERVERBOUND, packet_id, payload, body)
                if packet_id == 0x0A:
                    event = AnimationEvent(session=self)
                    await self._events.emit("animation", event)
                    if event.cancelled:
                        return None
                if packet_id == 0x02:
                    try:
                        offset = 0
                        entity_id, size = read_varint(body, offset)
                        offset += size
                        action_type, _ = read_varint(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Use entity parse failed: %s", exc)
                        return payload
                    event = InteractEvent(
                        session=self,
                        entity_id=entity_id,
                        type=action_type,
                        target_x=None,
                        target_y=None,
                        target_z=None,
                        hand=None,
                        sneaking=False,
                    )
                    await self._events.emit("interact", event)
                    if event.cancelled:
                        return None
            elif self.protocol_version == int(ProtocolVersion.V1122):
                play_plugin_id = self._get_plugin_message_id(PacketDirection.SERVERBOUND)
                if play_plugin_id is not None and packet_id == play_plugin_id:
                    return await self._handle_plugin_message(PacketDirection.SERVERBOUND, packet_id, payload, body)
                if packet_id == 0x02:
                    try:
                        message, size = read_string(body, 0, 256)
                    except ProtocolError as exc:
                        self.logger.debug("Chat message parse failed: %s", exc)
                        return payload
                    event = ChatMessageEvent(session=self, message=message)
                    await self._events.emit("chat_message", event)
                    if event.cancelled:
                        return None
                    if event.message != message:
                        try:
                            return write_varint(packet_id) + write_string(event.message, 256)
                        except ProtocolError as exc:
                            self.logger.debug("Chat message build failed: %s", exc)
                if packet_id == 0x0E:
                    try:
                        offset = 0
                        x, size = self._read_double(body, offset)
                        offset += size
                        y, size = self._read_double(body, offset)
                        offset += size
                        z, size = self._read_double(body, offset)
                        offset += size
                        yaw, size = self._read_float(body, offset)
                        offset += size
                        pitch, size = self._read_float(body, offset)
                        offset += size
                        on_ground, _ = read_bool(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Player position parse failed: %s", exc)
                        return payload
                    event = PlayerPositionEvent(
                        session=self,
                        x=x,
                        y=y,
                        z=z,
                        yaw=yaw,
                        pitch=pitch,
                        on_ground=on_ground,
                    )
                    await self._events.emit("player_position", event)
                    if event.cancelled:
                        return None
                    if (
                        event.x != x
                        or event.y != y
                        or event.z != z
                        or event.yaw != yaw
                        or event.pitch != pitch
                        or event.on_ground != on_ground
                    ):
                        new_body = struct.pack(
                            ">dddff?",
                            float(event.x),
                            float(event.y),
                            float(event.z),
                            float(event.yaw),
                            float(event.pitch),
                            bool(event.on_ground),
                        )
                        return write_varint(packet_id) + new_body
            elif self._is_proto(ProtocolVersion.V1200):
                play_plugin_id = self._get_plugin_message_id(PacketDirection.SERVERBOUND)
                if play_plugin_id is not None and packet_id == play_plugin_id:
                    return await self._handle_plugin_message(PacketDirection.SERVERBOUND, packet_id, payload, body)
                if self._update_player_state(packet_id, body):
                    return payload
                swing_id = self._get_v120x_id(0x2F, 0x36)
                if swing_id is not None and packet_id == swing_id:
                    try:
                        hand, _ = read_varint(body, 0)
                    except ProtocolError as exc:
                        self.logger.debug("Swing arm parse failed: %s", exc)
                        return payload
                    event = SwingArmEvent(session=self, hand=hand)
                    await self._events.emit("swing_arm", event)
                    if event.cancelled:
                        return None
                use_item_on_id = self._get_v120x_id(0x31, 0x38)
                if use_item_on_id is not None and packet_id == use_item_on_id:
                    try:
                        offset = 0
                        hand, size = read_varint(body, offset)
                        offset += size
                        location, size = self._read_position(body, offset)
                        offset += size
                        face, size = read_varint(body, offset)
                        offset += size
                        cursor_x, size = self._read_float(body, offset)
                        offset += size
                        cursor_y, size = self._read_float(body, offset)
                        offset += size
                        cursor_z, size = self._read_float(body, offset)
                        offset += size
                        inside, size = read_bool(body, offset)
                        offset += size
                        sequence, _ = read_varint(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Use item on parse failed: %s", exc)
                        return payload
                    event = UseItemOnEvent(
                        session=self,
                        hand=hand,
                        location=location,
                        face=face,
                        cursor_x=cursor_x,
                        cursor_y=cursor_y,
                        cursor_z=cursor_z,
                        inside_block=inside,
                        sequence=sequence,
                    )
                    await self._events.emit("use_item_on", event)
                    if event.cancelled:
                        return None
                use_item_id = self._get_v120x_id(0x32, 0x39)
                if use_item_id is not None and packet_id == use_item_id:
                    try:
                        offset = 0
                        hand, size = read_varint(body, offset)
                        offset += size
                        sequence, _ = read_varint(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Use item parse failed: %s", exc)
                        return payload
                    event = UseItemEvent(session=self, hand=hand, sequence=sequence)
                    await self._events.emit("use_item", event)
                    if event.cancelled:
                        return None
                if self.protocol_version == int(ProtocolVersion.V1200) and packet_id == 0x10:
                    try:
                        offset = 0
                        entity_id, size = read_varint(body, offset)
                        offset += size
                        action_type, size = read_varint(body, offset)
                        offset += size
                        target_x = target_y = target_z = None
                        if action_type == 2:
                            target_x, size = self._read_float(body, offset)
                            offset += size
                            target_y, size = self._read_float(body, offset)
                            offset += size
                            target_z, size = self._read_float(body, offset)
                            offset += size
                        hand = None
                        if action_type in (0, 2):
                            hand, size = read_varint(body, offset)
                            offset += size
                        sneaking, _ = read_bool(body, offset)
                    except ProtocolError as exc:
                        self.logger.debug("Interact parse failed: %s", exc)
                        return payload
                    event = InteractEvent(
                        session=self,
                        entity_id=entity_id,
                        type=action_type,
                        target_x=target_x,
                        target_y=target_y,
                        target_z=target_z,
                        hand=hand,
                        sneaking=sneaking,
                    )
                    await self._events.emit("interact", event)
                    if event.cancelled:
                        return None
        return payload

    async def _handle_server_packet(self, packet_id: int, payload: bytes, body: bytes) -> Optional[bytes]:
        if self.state == ConnectionState.LOGIN and packet_id == 1:
            self._phase("server encryption request")
            await self._handle_encryption_request(body)
            return None
        if self.state == ConnectionState.LOGIN and packet_id == 0:
            self._phase("server login disconnect")
            self._handle_disconnect(body)
            return payload
        if self.state == ConnectionState.LOGIN and packet_id == 3 and self.protocol_version != ProtocolVersion.V1076:
            self._phase("server set compression")
            self._handle_set_compression(body)
            await self._send_to_client(payload, force_uncompressed=True)
            self.client_compression_threshold = self.server_compression_threshold
            return None
        if self.state == ConnectionState.LOGIN and packet_id == 2:
            self._phase("server login success")
            await self._handle_login_success()
            return payload
        if self.state == ConnectionState.CONFIGURATION and self._is_proto(ProtocolVersion.V1206):
            config_plugin_id = self._get_plugin_message_id(PacketDirection.CLIENTBOUND)
            if config_plugin_id is not None and packet_id == config_plugin_id:
                return await self._handle_plugin_message(PacketDirection.CLIENTBOUND, packet_id, payload, body)
        if self.state == ConnectionState.PLAY and packet_id == self._get_play_disconnect_id():
            self._phase("server play disconnect")
            self._handle_disconnect(body)
            return payload
        if self.state == ConnectionState.PLAY:
            if (
                ENABLE_HEYPIXEL_SAFE_DROP
                and self.protocol_version == int(ProtocolVersion.V1206)
                and str(getattr(self, "game_id", "") or "") == HEYPIXEL_GAME_ID
                and packet_id in HEYPIXEL_SAFE_DROP_IDS
            ):
                self.logger.warning(
                    "Heuristic drop packet for heypixel safety: id=0x%02X raw=%s len=%s",
                    packet_id,
                    len(payload),
                    len(body),
                )
                return None
            if self.protocol_version == int(ProtocolVersion.V108X):
                if packet_id == 0x26 and len(payload) > 0x200000:
                    try:
                        split_packets = self._split_chunk_bulk_v108x(packet_id, body)
                    except ProtocolError as exc:
                        self.logger.warning(
                            "Chunk bulk split failed (payload=%s body=%s): %s",
                            len(payload),
                            len(body),
                            exc,
                        )
                        self.logger.warning("Dropping oversized chunk bulk to avoid client disconnect")
                        return None
                    else:
                        self.logger.warning(
                            "Chunk bulk too large (%s bytes), split into %s packets",
                            len(payload),
                            len(split_packets),
                        )
                        for packet in split_packets:
                            await self._send_to_client(packet)
                        return None
                if len(payload) > 0x200000:
                    self.logger.warning(
                        "Oversized v108x packet id=0x%02X payload=%s; dropping to avoid client disconnect",
                        packet_id,
                        len(payload),
                    )
                    return None
                if packet_id == 0x3F:
                    return await self._handle_plugin_message(PacketDirection.CLIENTBOUND, packet_id, payload, body)
                sanitized = self._sanitize_clientbound_play_v108x(packet_id, body)
                if sanitized is not None:
                    return write_varint(packet_id) + sanitized
            elif self.protocol_version == int(ProtocolVersion.V1122):
                play_plugin_id = self._get_plugin_message_id(PacketDirection.CLIENTBOUND)
                if play_plugin_id is not None and packet_id == play_plugin_id:
                    return await self._handle_plugin_message(PacketDirection.CLIENTBOUND, packet_id, payload, body)
                if packet_id == 0x23:
                    try:
                        if len(body) < 4:
                            raise ProtocolError("join game packet too short")
                        player_id = int.from_bytes(body[0:4], "big", signed=True)
                    except ProtocolError as exc:
                        self.logger.debug("Game join parse failed: %s", exc)
                        return payload
                    event = GameJoinEvent(session=self, player_id=player_id, payload=body[4:])
                    await self._events.emit("game_join", event)
                    if event.cancelled:
                        return None
            elif self._is_proto(ProtocolVersion.V1200):
                play_plugin_id = self._get_plugin_message_id(PacketDirection.CLIENTBOUND)
                if play_plugin_id is not None and packet_id == play_plugin_id:
                    return await self._handle_plugin_message(PacketDirection.CLIENTBOUND, packet_id, payload, body)
                game_join_id = self._get_v120x_id(0x28, 0x2B)
                if game_join_id is not None and packet_id == game_join_id:
                    try:
                        player_id, size = read_varint(body, 0)
                    except ProtocolError as exc:
                        self.logger.debug("Game join parse failed: %s", exc)
                        return payload
                    event = GameJoinEvent(session=self, player_id=player_id, payload=body[size:])
                    await self._events.emit("game_join", event)
                    if event.cancelled:
                        return None
                if self.protocol_version == int(ProtocolVersion.V1206) and packet_id == 0x58:
                    try:
                        entity_id, size = read_varint(body, 0)
                    except ProtocolError as exc:
                        self.logger.debug("Entity metadata parse failed: %s", exc)
                        return payload
                    event = SetEntityMetadataEvent(session=self, entity_id=entity_id, raw_data=body[size:])
                    await self._events.emit("set_entity_metadata", event)
                    if event.cancelled:
                        return None
        if self.state == ConnectionState.PLAY and packet_id == 105 and self._is_proto(ProtocolVersion.V1206):
            self.state = ConnectionState.CONFIGURATION
            self.logger.info("Server started configuration")
            self._phase("server started configuration")
            return payload
        return payload

    def _rewrite_handshake(self, body: bytes) -> bytes:
        offset = 0
        protocol, size = read_varint(body, offset)
        offset += size
        server_addr, size = read_string(body, offset, 255)
        offset += size
        _server_port, size = read_ushort(body, offset)
        offset += size
        next_state, size = read_varint(body, offset)
        offset += size

        self.protocol_version = protocol
        try:
            self.state = ConnectionState(next_state)
        except ValueError:
            self.state = ConnectionState.HANDSHAKE
        self._phase(f"handshake proto={protocol} next_state={self.state.name}")

        new_addr = _with_fml_marker(protocol, self.config.forward_host)
        self.logger.debug("Handshake addr %s -> %s", _safe_b64(server_addr), _safe_b64(new_addr))
        new_body = (
            write_varint(protocol)
            + write_string(new_addr)
            + write_ushort(self.config.forward_port)
            + write_varint(next_state)
        )
        return write_varint(0) + new_body

    def _rewrite_login_start(self, body: bytes) -> bytes:
        offset = 0
        proto = self.protocol_version or ProtocolVersion.V108X
        nickname = self.config.nickname
        if proto in (ProtocolVersion.V1206, ProtocolVersion.V1210):
            _profile, size = read_string(body, offset, 16)
            offset += size
            uuid_bytes, size = read_bytes(body, offset, 16)
            offset += size
            try:
                self.player_uuid = str(uuid.UUID(bytes=uuid_bytes))
            except ValueError:
                self.player_uuid = None
            new_body = write_string(nickname, 16) + uuid_bytes
        elif proto == ProtocolVersion.V1200:
            _profile, size = read_string(body, offset, 16)
            offset += size
            has_uuid, size = read_bool(body, offset)
            offset += size
            uuid_bytes = b""
            if has_uuid:
                uuid_bytes, size = read_bytes(body, offset, 16)
                offset += size
                try:
                    self.player_uuid = str(uuid.UUID(bytes=uuid_bytes))
                except ValueError:
                    self.player_uuid = None
            new_body = write_string(nickname, 16) + (b"\x01" if has_uuid else b"\x00") + uuid_bytes
        elif proto == ProtocolVersion.V1180:
            _profile, size = read_string(body, offset, 16)
            offset += size
            new_body = write_string(nickname)
        else:
            _profile, size = read_string(body, offset, 16)
            offset += size
            new_body = write_string(nickname, 16)
        self._phase(f"rewrite login start proto={int(proto)} nickname={nickname}")
        return write_varint(0) + new_body

    async def _handle_encryption_request(self, body: bytes) -> None:
        if self.protocol_version is None:
            self.logger.warning("Encryption request received before handshake")
            return
        server_id, public_key, verify_token, _should_auth = _parse_encryption_request(body, self.protocol_version)
        self._phase(f"encryption request server_id_len={len(server_id)} should_auth={_should_auth}")
        secret_key = os.urandom(16)
        server_hash = _compute_server_hash(server_id, secret_key, public_key)

        if self.config.ygg_profile and self.config.ygg_data:
            self._phase(f"yggdrasil join server_hash={server_hash[:12]}...")
            await self._join_yggdrasil(server_hash)
        else:
            self.logger.warning("Yggdrasil profile missing; skipping join")
            self._phase("yggdrasil join skipped (missing profile)")

        response_body = _build_encryption_response(self.protocol_version, secret_key, public_key, verify_token)
        response_payload = write_varint(1) + response_body
        await self._send_to_server(response_payload, force_uncompressed=True, force_unencrypted=True)
        self._cipher = _Cfb8Cipher(secret_key)
        self.logger.info("Encryption enabled for server connection")
        self._phase("encryption enabled")

    def _handle_set_compression(self, body: bytes) -> None:
        threshold, _ = read_varint(body, 0)
        self.server_compression_threshold = threshold
        self.logger.info("Compression enabled, threshold=%s", threshold)
        self._phase(f"compression enabled threshold={threshold}")

    async def _handle_login_success(self) -> None:
        if self._is_proto(ProtocolVersion.V1206):
            self.logger.info("Login success (configuration flow)")
            self._phase("login success (configuration flow)")
            return
        self.state = ConnectionState.PLAY
        self.logger.info("Login success, entering play")
        self._phase("login success -> play")
        await self._emit_login_success()

    def _get_play_disconnect_id(self) -> Optional[int]:
        if self.protocol_version is None:
            return None
        if self.protocol_version <= int(ProtocolVersion.V108X):
            return 0x40
        return 0x1A

    def _handle_disconnect(self, body: bytes) -> None:
        try:
            reason, _ = read_string(body, 0, 32767)
        except ProtocolError:
            reason = "<parse failed>"
        self.logger.warning("Server disconnect: %s", reason)
        self._phase(f"disconnect reason={reason}")

    def _is_proto(self, proto: ProtocolVersion) -> bool:
        if self.protocol_version is None:
            return False
        return self.protocol_version >= int(proto)

    def _sanitize_clientbound_play_v108x(self, packet_id: int, body: bytes) -> Optional[bytes]:
        try:
            if packet_id == 0x38:
                return self._sanitize_player_list(body)
            if packet_id == 0x3B:
                return self._sanitize_objective(body)
            if packet_id == 0x3C:
                return self._sanitize_score_update(body)
            if packet_id == 0x3D:
                return self._sanitize_display_score(body)
            if packet_id == 0x3E:
                return self._sanitize_team(body)
        except ProtocolError as exc:
            self.logger.debug("Sanitize packet failed: %s", exc)
        return None

    def _sanitize_player_list(self, body: bytes) -> Optional[bytes]:
        offset = 0
        action, size = read_varint(body, offset)
        offset += size
        count, size = read_varint(body, offset)
        offset += size
        out = bytearray()
        out += write_varint(action)
        out += write_varint(count)
        changed = False
        for _ in range(count):
            uuid_bytes, size = read_bytes(body, offset, 16)
            offset += size
            out += uuid_bytes
            if action == 0:
                name, size = read_string(body, offset, 32767)
                offset += size
                mapped = self._map_name("player", name, 16)
                if mapped != name:
                    changed = True
                out += write_string(mapped, 16)
                prop_count, size = read_varint(body, offset)
                offset += size
                out += write_varint(prop_count)
                for _ in range(prop_count):
                    prop_name, size = read_string(body, offset, 32767)
                    offset += size
                    prop_value, size = read_string(body, offset, 32767)
                    offset += size
                    out += write_string(prop_name)
                    out += write_string(prop_value)
                    has_sig, size = read_bool(body, offset)
                    offset += size
                    out += b"\x01" if has_sig else b"\x00"
                    if has_sig:
                        signature, size = read_string(body, offset, 32767)
                        offset += size
                        out += write_string(signature)
                gamemode, size = read_varint(body, offset)
                offset += size
                out += write_varint(gamemode)
                ping, size = read_varint(body, offset)
                offset += size
                out += write_varint(ping)
                has_display, size = read_bool(body, offset)
                offset += size
                out += b"\x01" if has_display else b"\x00"
                if has_display:
                    display, size = read_string(body, offset, 32767)
                    offset += size
                    out += write_string(display)
            elif action == 1:
                gamemode, size = read_varint(body, offset)
                offset += size
                out += write_varint(gamemode)
            elif action == 2:
                ping, size = read_varint(body, offset)
                offset += size
                out += write_varint(ping)
            elif action == 3:
                has_display, size = read_bool(body, offset)
                offset += size
                out += b"\x01" if has_display else b"\x00"
                if has_display:
                    display, size = read_string(body, offset, 32767)
                    offset += size
                    out += write_string(display)
            elif action == 4:
                continue
        if not changed:
            return None
        return bytes(out)

    def _sanitize_objective(self, body: bytes) -> Optional[bytes]:
        offset = 0
        name, size = read_string(body, offset, 32767)
        offset += size
        if offset >= len(body):
            return None
        mode = body[offset]
        offset += 1
        mapped = self._map_name("objective", name, 16)
        out = bytearray()
        out += write_string(mapped, 16)
        out.append(mode)
        changed = mapped != name
        if mode in (0, 2):
            display_raw, size = read_string(body, offset, 32767)
            offset += size
            display = self._truncate_string(display_raw, 32)
            out += write_string(display, 32)
            type_raw, size = read_string(body, offset, 32767)
            offset += size
            type_name = self._truncate_string(type_raw, 16)
            out += write_string(type_name, 16)
            if display != display_raw or type_name != type_raw:
                changed = True
        return bytes(out) if changed else None

    def _sanitize_score_update(self, body: bytes) -> Optional[bytes]:
        offset = 0
        score_name, size = read_string(body, offset, 32767)
        offset += size
        if offset >= len(body):
            return None
        action = body[offset]
        offset += 1
        objective, size = read_string(body, offset, 32767)
        offset += size
        mapped_objective = self._map_name("objective", objective, 16)
        out = bytearray()
        out += write_string(score_name)
        out.append(action)
        out += write_string(mapped_objective, 16)
        changed = mapped_objective != objective
        if action != 1:
            value, size = read_varint(body, offset)
            offset += size
            out += write_varint(value)
        return bytes(out) if changed else None

    def _sanitize_display_score(self, body: bytes) -> Optional[bytes]:
        if not body:
            return None
        position = body[0]
        offset = 1
        name, size = read_string(body, offset, 32767)
        offset += size
        mapped = self._map_name("objective", name, 16)
        if mapped == name:
            return None
        out = bytearray()
        out.append(position)
        out += write_string(mapped, 16)
        return bytes(out)

    def _sanitize_team(self, body: bytes) -> Optional[bytes]:
        offset = 0
        name, size = read_string(body, offset, 32767)
        offset += size
        if offset >= len(body):
            return None
        mode = body[offset]
        offset += 1
        mapped_name = self._map_name("team", name, 16)
        out = bytearray()
        out += write_string(mapped_name, 16)
        out.append(mode)
        changed = mapped_name != name
        if mode in (0, 2):
            display_raw, size = read_string(body, offset, 32767)
            offset += size
            display = self._truncate_string(display_raw, 32)
            out += write_string(display, 32)
            prefix_raw, size = read_string(body, offset, 32767)
            offset += size
            prefix = self._truncate_string(prefix_raw, 16)
            out += write_string(prefix, 16)
            suffix_raw, size = read_string(body, offset, 32767)
            offset += size
            suffix = self._truncate_string(suffix_raw, 16)
            out += write_string(suffix, 16)
            if prefix != prefix_raw or suffix != suffix_raw or display != display_raw:
                changed = True
            if offset < len(body):
                out.append(body[offset])
                offset += 1
            visibility_raw, size = read_string(body, offset, 32767)
            offset += size
            visibility = self._truncate_string(visibility_raw, 32)
            out += write_string(visibility, 32)
            if visibility != visibility_raw:
                changed = True
            if offset < len(body):
                out.append(body[offset])
                offset += 1
        if mode in (0, 3, 4):
            count, size = read_varint(body, offset)
            offset += size
            out += write_varint(count)
            for _ in range(count):
                player, size = read_string(body, offset, 32767)
                offset += size
                mapped_player = self._map_name("player", player, 16)
                if mapped_player != player:
                    changed = True
                out += write_string(mapped_player, 16)
        return bytes(out) if changed else None

    def _split_chunk_bulk_v108x(self, packet_id: int, body: bytes) -> list[bytes]:
        offset = 0
        skylight, size = read_bool(body, offset)
        offset += size
        count, size = read_varint(body, offset)
        offset += size
        metas: list[tuple[int, int, int, int]] = []
        for _ in range(count):
            if offset + 12 > len(body):
                raise ProtocolError("chunk bulk meta out of range")
            chunk_x = int.from_bytes(body[offset : offset + 4], "big", signed=True)
            offset += 4
            chunk_z = int.from_bytes(body[offset : offset + 4], "big", signed=True)
            offset += 4
            primary = int.from_bytes(body[offset : offset + 2], "big")
            offset += 2
            add = int.from_bytes(body[offset : offset + 2], "big")
            offset += 2
            metas.append((chunk_x, chunk_z, primary, add))

        data_blob = body[offset:]
        if len(data_blob) >= 4:
            possible_len = int.from_bytes(data_blob[:4], "big", signed=False)
            if possible_len == len(data_blob) - 4:
                data_blob = data_blob[4:]
        data_offset = 0
        chunks: list[tuple[int, int, int, int, bytes]] = []
        # 1.8.9 chunk section layout:
        # blocks: 4096 * 2 bytes (12-bit packed) = 8192
        # block light: 2048
        # sky light: 2048 (if skylight)
        section_size = 8192 + 2048 + (2048 if skylight else 0)
        for chunk_x, chunk_z, primary, add in metas:
            section_count = primary.bit_count()
            add_count = add.bit_count()
            chunk_size = section_count * section_size + add_count * 2048 + 256
            end = data_offset + chunk_size
            if end > len(data_blob):
                raise ProtocolError("chunk bulk data out of range")
            chunks.append((chunk_x, chunk_z, primary, add, data_blob[data_offset:end]))
            data_offset = end
        if data_offset != len(data_blob):
            raise ProtocolError("chunk bulk trailing data")

        packets: list[bytes] = []
        for chunk_x, chunk_z, primary, add, chunk_data in chunks:
            body_out = bytearray()
            body_out.append(1 if skylight else 0)
            body_out += write_varint(1)
            body_out += int(chunk_x).to_bytes(4, "big", signed=True)
            body_out += int(chunk_z).to_bytes(4, "big", signed=True)
            body_out += int(primary).to_bytes(2, "big")
            body_out += int(add).to_bytes(2, "big")
            body_out += chunk_data
            packets.append(write_varint(packet_id) + bytes(body_out))
        return packets

    def _map_name(self, category: str, value: str, max_len: int) -> str:
        if len(value) <= max_len:
            return value
        cache = self._name_maps.setdefault(category, {})
        if value in cache:
            return cache[value]
        prefix = category[:1].lower() if category else "x"
        suffix_len = max_len - len(prefix)
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()
        mapped = prefix + digest[:suffix_len]
        cache[value] = mapped
        return mapped

    @staticmethod
    def _truncate_string(value: str, max_len: int) -> str:
        if len(value) <= max_len:
            return value
        return value[:max_len]

    async def _join_yggdrasil(self, server_hash: str) -> None:
        profile = self.config.ygg_profile
        data = self.config.ygg_data
        if profile is None or data is None:
            return
        try:
            servers = StandardYggdrasil.auth_servers()
        except Exception as exc:  # pylint: disable=broad-except
            self.logger.warning("Yggdrasil auth server list failed: %s", exc)
            return
        import random

        random.shuffle(servers)
        strategies = self._build_auth_strategies(profile)
        wire_variants = [
            ("crc-be-ctr0", 0, False),
            ("crc-le-ctr0", 0, True),
            ("crc-be-ctr1", 1, False),
            ("crc-le-ctr1", 1, True),
        ]
        failures: list[str] = []
        successes: list[str] = []

        def _summarize(entries: list[str], limit: int = 6) -> str:
            if not entries:
                return "none"
            shown = entries[:limit]
            extra = len(entries) - limit
            return "; ".join(shown) + (f" (+{extra} more)" if extra > 0 else "")

        for strategy_name, strategy_profile in strategies:
            for host, port in servers:
                for wire_name, counter_start, crc_le in wire_variants:
                    ygg = StandardYggdrasil(data, host, port)
                    ok, err = await asyncio.to_thread(
                        ygg.join_server,
                        strategy_profile,
                        server_hash,
                        False,
                        counter_start=counter_start,
                        crc_little_endian=crc_le,
                    )
                    if ok:
                        successes.append(f"{host}:{port} {strategy_name}/{wire_name}")
                        self.logger.info(
                            "Yggdrasil join success (%s/%s) via %s:%s",
                            strategy_name,
                            wire_name,
                            host,
                            port,
                        )
                        if failures:
                            self.logger.info(
                                "Yggdrasil attempts before success (%d): %s",
                                len(failures),
                                _summarize(failures),
                            )
                        self.logger.info("Yggdrasil auth success list: %s", _summarize(successes))
                        return
                    failures.append(f"{host}:{port} {strategy_name}/{wire_name}")
                    self.logger.debug(
                        "Yggdrasil join failed (%s/%s) via %s:%s: %s",
                        strategy_name,
                        wire_name,
                        host,
                        port,
                        err,
                    )
        self.logger.warning("Yggdrasil join failed across all strategies")
        if failures:
            self.logger.warning("Yggdrasil auth failed list (%d): %s", len(failures), _summarize(failures))

    @staticmethod
    def _build_auth_strategies(profile: GameProfile) -> list[tuple[str, GameProfile]]:
        strategies: list[tuple[str, GameProfile]] = [("skip32+xor", profile)]
        user = profile.user
        if user.use_skip32:
            strategies.append((
                "plain+xor",
                GameProfile(
                    game_id=profile.game_id,
                    game_version=profile.game_version,
                    bootstrap_md5=profile.bootstrap_md5,
                    dat_file_md5=profile.dat_file_md5,
                    mods=profile.mods,
                    user=UserProfile(
                        user_id=user.user_id,
                        user_token=user.user_token,
                        use_skip32=False,
                        use_xor=user.use_xor,
                        use_hex_token=user.use_hex_token,
                    ),
                ),
            ))
        if _looks_like_hex_32(user.user_token):
            strategies.append((
                "skip32+hex",
                GameProfile(
                    game_id=profile.game_id,
                    game_version=profile.game_version,
                    bootstrap_md5=profile.bootstrap_md5,
                    dat_file_md5=profile.dat_file_md5,
                    mods=profile.mods,
                    user=UserProfile(
                        user_id=user.user_id,
                        user_token=user.user_token,
                        use_skip32=user.use_skip32,
                        use_xor=False,
                        use_hex_token=True,
                    ),
                ),
            ))
            strategies.append((
                "plain+hex",
                GameProfile(
                    game_id=profile.game_id,
                    game_version=profile.game_version,
                    bootstrap_md5=profile.bootstrap_md5,
                    dat_file_md5=profile.dat_file_md5,
                    mods=profile.mods,
                    user=UserProfile(
                        user_id=user.user_id,
                        user_token=user.user_token,
                        use_skip32=False,
                        use_xor=False,
                        use_hex_token=True,
                    ),
                ),
            ))
        return strategies


def _parse_encryption_request(
    body: bytes, protocol: int
) -> tuple[str, bytes, bytes, Optional[bool]]:
    offset = 0
    server_id, size = read_string(body, offset, 20)
    offset += size
    if protocol == ProtocolVersion.V1076:
        key_len, size = read_ushort(body, offset)
        offset += size
        public_key, size = read_bytes(body, offset, key_len)
        offset += size
        token_len, size = read_ushort(body, offset)
        offset += size
        verify_token, size = read_bytes(body, offset, token_len)
        offset += size
        return server_id, public_key, verify_token, None
    public_key, size = read_byte_array(body, offset)
    offset += size
    verify_token, size = read_byte_array(body, offset)
    offset += size
    should_auth = None
    if protocol in (ProtocolVersion.V1206, ProtocolVersion.V1210):
        should_auth, size = read_bool(body, offset)
        offset += size
    return server_id, public_key, verify_token, should_auth


def _build_encryption_response(protocol: int, secret_key: bytes, public_key: bytes, verify_token: bytes) -> bytes:
    rsa_key = RSA.import_key(public_key)
    cipher = PKCS1_v1_5.new(rsa_key)
    secret_enc = cipher.encrypt(secret_key)
    token_enc = cipher.encrypt(verify_token)
    if protocol == ProtocolVersion.V1076:
        return write_ushort(len(secret_enc)) + secret_enc + write_ushort(len(token_enc)) + token_enc
    return write_byte_array(secret_enc) + write_byte_array(token_enc)


def _compute_server_hash(server_id: str, secret_key: bytes, public_key: bytes) -> str:
    raw = server_id.encode("iso-8859-1") + secret_key + public_key
    digest = hashlib.sha1(raw).digest()
    num = int.from_bytes(digest, byteorder="big", signed=True)
    if num < 0:
        value = format(-num, "x").lstrip("0")
        return f"-{value}" if value else "-0"
    value = format(num, "x").lstrip("0")
    return value or "0"


def _with_fml_marker(protocol: int, address: str) -> str:
    if protocol > ProtocolVersion.V1180:
        if protocol <= ProtocolVersion.V1206:
            return f"{address}\x00FML3\x00"
        return f"{address}\x00FORGE"
    if protocol <= ProtocolVersion.V1122:
        return f"{address}\x00FML\x00"
    return f"{address}\x00FML2\x00"


def _safe_b64(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _looks_like_hex_32(value: str) -> bool:
    if len(value) != 32:
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in value)
