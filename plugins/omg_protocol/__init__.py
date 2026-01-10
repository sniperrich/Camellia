from __future__ import annotations

import asyncio
import base64
import hashlib
import random
import time
from dataclasses import dataclass, field

from Crypto.Cipher import DES
from Crypto.Util.Padding import pad

from camellia.mc.protocol import ProtocolError, read_varint, write_varint
from camellia.plugins.events import (
    AnimationEvent,
    LoginSuccessEvent,
    PacketDirection,
    PluginMessageEvent,
)


GAME_ID = "4662907052731024438"
BRAND_CHANNEL = "MC|Brand"
REGISTER_CHANNEL = "REGISTER"
BRAND_PAYLOAD = base64.b64decode("CWZtbCxmb3JnZQ==")
REGISTER_PAYLOAD = base64.b64decode(
    "Rk1MfEhTAEZNTABGTUx8TVAARk1MAGFudGltb2QAZ2VybXBsdWdpbgBGT1JHRQ=="
)
OMG_CURR_CPS = "omg|curr_cps"
OMG_CPS_INF = "omg|cps_inf"
OMG_INF_REQ = "omg|inf_req"
OMG_INF = "omg|inf"
GERM_PLUGIN = "germplugin"
GERM_MOD = "germmod"

DES_KEY_STAGE1 = "1qaz2wsx3edc4ds6g4f4g65a7ujm8ik,9ol.0p;/"
DES_KEY_STAGE2 = "!QAZ@WSX#EDC$RFV%TGB^YHN&UJM*IK<(OL>)P:?"

CPU_SIGNATURES = [
    "BFEBFBFF000B06F2|Intel(R) Core(TM) i7-14700K @ 3.40GHz|Intel64 Family 6 Model 186 Stepping 2",
    "BFEBFBFF000B06F3|Intel(R) Core(TM) i7-14700KF @ 3.40GHz|Intel64 Family 6 Model 186 Stepping 3",
    "BFEBFBFF000B06F5|Intel(R) Core(TM) i9-14900K @ 3.20GHz|Intel64 Family 6 Model 186 Stepping 5",
    "BFEBFBFF000B06F6|Intel(R) Core(TM) i9-14900KF @ 3.20GHz|Intel64 Family 6 Model 186 Stepping 6",
    "BFEBFBFF000B06F1|Intel(R) Core(TM) i5-14600K @ 3.50GHz|Intel64 Family 6 Model 186 Stepping 1",
    "BFEBFBFF000B06F0|Intel(R) Core(TM) i3-14100F @ 3.50GHz|Intel64 Family 6 Model 186 Stepping 0",
    "BFEBFBFF000B06D4|Intel(R) Core(TM) i7-14700HX @ 2.10GHz|Intel64 Family 6 Model 186 Stepping 4",
    "BFEBFBFF000A06A5|Intel(R) Core(TM) i9-13900K @ 3.00GHz|Intel64 Family 6 Model 170 Stepping 5",
    "BFEBFBFF000A06A3|Intel(R) Core(TM) i7-13700K @ 3.40GHz|Intel64 Family 6 Model 170 Stepping 3",
    "BFEBFBFF000A06A4|Intel(R) Core(TM) i7-13700KF @ 3.40GHz|Intel64 Family 6 Model 170 Stepping 4",
    "BFEBFBFF000A06A2|Intel(R) Core(TM) i5-13600K @ 3.50GHz|Intel64 Family 6 Model 170 Stepping 2",
    "BFEBFBFF000A06A1|Intel(R) Core(TM) i5-13600KF @ 3.50GHz|Intel64 Family 6 Model 170 Stepping 1",
    "BFEBFBFF000A06A0|Intel(R) Core(TM) i3-13100 @ 3.40GHz|Intel64 Family 6 Model 170 Stepping 0",
    "BFEBFBFF000A06C8|Intel(R) Core(TM) i7-13700H @ 2.40GHz|Intel64 Family 6 Model 170 Stepping 8",
    "BFEBFBFF000806C1|Intel(R) Core(TM) i9-12900K @ 3.20GHz|Intel64 Family 6 Model 140 Stepping 1",
    "BFEBFBFF000806C2|Intel(R) Core(TM) i9-12900KF @ 3.20GHz|Intel64 Family 6 Model 140 Stepping 2",
    "BFEBFBFF000806C0|Intel(R) Core(TM) i3-12100 @ 3.30GHz|Intel64 Family 6 Model 140 Stepping 0",
    "BFEBFBFF00090672|12th Gen Intel(R) Core(TM) i7-12700K|Intel64 Family 6 Model 151 Stepping 2",
    "BFEBFBFF000806E9|Intel(R) Core(TM) i7-12800H @ 2.40GHz|Intel64 Family 6 Model 141 Stepping 9",
    "BFEBFBFF000806E8|Intel(R) Core(TM) i9-12900HK @ 2.50GHz|Intel64 Family 6 Model 141 Stepping 8",
    "BFEBFBFF000A0671|Intel(R) Core(TM) i5-11400 @ 2.60GHz|Intel64 Family 6 Model 167 Stepping 1",
    "BFEBFBFF000A0671|Intel(R) Core(TM) i5-11400F @ 2.60GHz|Intel64 Family 6 Model 167 Stepping 1",
    "BFEBFBFF000806D1|Intel(R) Core(TM) i5-11400H @ 2.70GHz|Intel64 Family 6 Model 141 Stepping 1",
    "BFEBFBFF000A0654|Intel(R) Core(TM) i7-10700K @ 3.80GHz|Intel64 Family 6 Model 165 Stepping 5",
    "BFEBFBFF000A0655|Intel(R) Core(TM) i7-10700F @ 2.90GHz|Intel64 Family 6 Model 165 Stepping 5",
    "BFEBFBFF000A0652|Intel(R) Core(TM) i7-10750H @ 2.60GHz|Intel64 Family 6 Model 165 Stepping 2",
    "BFEBFBFF000906F0|Intel(R) Core(TM) i3-10100 @ 3.60GHz|Intel64 Family 6 Model 158 Stepping 16",
    "BFEBFBFF000906EC|Intel(R) Core(TM) i7-9700K @ 3.60GHz|Intel64 Family 6 Model 158 Stepping 12",
    "BFEBFBFF000906ED|Intel(R) Core(TM) i5-9600K @ 3.70GHz|Intel64 Family 6 Model 158 Stepping 13",
    "BFEBFBFF000906B0|Intel(R) Core(TM) i3-N305 @ 3.80GHz|Intel64 Family 6 Model 154 Stepping 0",
    "BFEBFBFF000906B1|Intel(R) Core(TM) i3-N100 @ 3.40GHz|Intel64 Family 6 Model 154 Stepping 0",
    "BFEBFBFF000A06B2|Intel(R) Processor N100 @ 3.40GHz|Intel64 Family 6 Model 170 Stepping 0",
    "BFEBFBFF000A06B3|Intel(R) Processor N200 @ 3.70GHz|Intel64 Family 6 Model 170 Stepping 0",
]


@dataclass
class CpsCounter:
    primary: list[int] = field(default_factory=list)
    secondary: list[int] = field(default_factory=list)

    def add_primary(self) -> None:
        self._add(self.primary)

    def add_secondary(self) -> None:
        self._add(self.secondary)

    def cleanup(self) -> None:
        now_ms = _now_ms()
        cutoff = now_ms - 1000
        self.primary[:] = [ts for ts in self.primary if ts >= cutoff]
        self.secondary[:] = [ts for ts in self.secondary if ts >= cutoff]

    def primary_count(self) -> int:
        return len(self.primary)

    def secondary_count(self) -> int:
        return len(self.secondary)

    def _add(self, target: list[int]) -> None:
        now_ms = _now_ms()
        cutoff = now_ms - 1000
        target.append(now_ms)
        target[:] = [ts for ts in target if ts >= cutoff]


@dataclass
class OmgState:
    fingerprint: str
    token_bytes: bytes
    cps_counter: CpsCounter = field(default_factory=CpsCounter)
    last_cps: int = 0
    last_secondary: int = 0
    loop_task: asyncio.Task | None = None


def setup(context) -> None:
    events = context.events
    logger = context.logger

    async def on_login_success(event: LoginSuccessEvent) -> None:
        session = event.session
        if str(getattr(session, "game_id", "")) != GAME_ID:
            return
        state = _get_state(session)
        if state.loop_task and not state.loop_task.done():
            return
        state.loop_task = session.create_task(_cps_loop(session, state, logger))

    async def on_animation(event: AnimationEvent) -> None:
        session = event.session
        if str(getattr(session, "game_id", "")) != GAME_ID:
            return
        state = _get_state(session)
        state.cps_counter.add_primary()

    async def on_plugin_message(event: PluginMessageEvent) -> None:
        session = event.session
        if str(getattr(session, "game_id", "")) != GAME_ID:
            return
        if event.direction == PacketDirection.SERVERBOUND:
            _handle_serverbound(event, logger)
        elif event.direction == PacketDirection.CLIENTBOUND:
            await _handle_clientbound(event, logger)
        else:
            raise ValueError("Invalid packet direction")

    events.on("base_108x", on_plugin_message, event_type=PluginMessageEvent)
    events.on("base_108x", on_animation, event_type=AnimationEvent)
    events.on("channel_v1122", on_login_success, event_type=LoginSuccessEvent)


def _handle_serverbound(event: PluginMessageEvent, logger) -> None:
    identifier = event.identifier
    if identifier == BRAND_CHANNEL:
        event.payload = BRAND_PAYLOAD
        logger.info("Replacing brand channel data.")
    elif identifier == REGISTER_CHANNEL:
        event.cancelled = True
        logger.info("Cancelling register channel.")


async def _handle_clientbound(event: PluginMessageEvent, logger) -> None:
    session = event.session
    state = _get_state(session)
    identifier = event.identifier

    if identifier == GERM_PLUGIN:
        event.cancelled = True
        code = _read_int(event.payload)
        if code == 37:
            payload = _build_germmod_payload(state.fingerprint)
            await session.send_plugin_message(PacketDirection.SERVERBOUND, GERM_MOD, payload)
            logger.info("Sent mod channel data, fingerprint: %s", state.fingerprint)
        return

    if identifier == OMG_CPS_INF:
        return

    if identifier == OMG_INF_REQ:
        event.cancelled = True
        if not _read_bool(event.payload):
            return
        payload = b" " + state.token_bytes
        await session.send_plugin_message(PacketDirection.SERVERBOUND, OMG_INF, payload)
        return

    if identifier == BRAND_CHANNEL:
        brand = _read_mc_string(event.payload)
        if "<- BurritoSpigot" in brand:
            await session.send_plugin_message(PacketDirection.SERVERBOUND, REGISTER_CHANNEL, REGISTER_PAYLOAD)


async def _cps_loop(session, state: OmgState, logger) -> None:
    rng = random.Random()
    while getattr(session, "is_active", True):
        cps = state.cps_counter.primary_count()
        if cps != state.last_cps:
            state.last_cps = cps
            payload = cps.to_bytes(4, "big", signed=True) + b"\x01"
            try:
                await session.send_plugin_message(PacketDirection.SERVERBOUND, OMG_CURR_CPS, payload)
            except Exception as exc:  # pylint: disable=broad-except
                logger.warning("Error sending message: %s", exc)
        secondary = state.cps_counter.secondary_count()
        if state.last_secondary != secondary:
            state.last_secondary = secondary
        else:
            state.cps_counter.cleanup()
        jitter = rng.randint(0, 9)
        delay_ms = 50 - jitter if rng.random() < 0.5 else 50 + jitter
        await asyncio.sleep(max(0, delay_ms) / 1000.0)


def _get_state(session) -> OmgState:
    state = getattr(session, "plugin_data", {}).get("omg_protocol")
    if state is not None:
        return state
    rng = random.Random()
    fingerprint = _build_fingerprint(rng)
    token_bytes = _build_token_bytes(rng)
    state = OmgState(fingerprint=fingerprint, token_bytes=token_bytes)
    session.plugin_data["omg_protocol"] = state
    return state


def _build_token_bytes(rng: random.Random) -> bytes:
    seed = _random_hex(rng, 16)
    return hashlib.md5(seed.encode("utf-8")).hexdigest().encode("utf-8")


def _build_fingerprint(rng: random.Random) -> str:
    mac_list = _build_mac_list(rng)
    cpu_id = _random_cpu_id(rng)
    device_id = _random_device_id(rng)
    combined = _za470vjeq(mac_list) + _za470vjeq(cpu_id) + _za470vjeq(device_id)
    combined = combined.replace("\r\n", "").upper()
    return base64.b64encode(combined.encode("utf-8")).decode("ascii")


def _build_mac_list(rng: random.Random) -> str:
    return "[" + _random_mac(rng, 6) + "]"


def _random_cpu_id(rng: random.Random) -> str:
    choice = rng.choice(CPU_SIGNATURES)
    return choice.split("|", 1)[0]


def _random_device_id(rng: random.Random) -> str:
    return (
        _random_hex(rng, 2)
        + "_"
        + _random_hex(rng, 2)
        + "_"
        + _random_hex(rng, 2)
        + "_"
        + _random_hex(rng, 2)
        + "."
    ).upper()


def _random_hex(rng: random.Random, size: int) -> str:
    data = bytes(rng.randrange(0, 256) for _ in range(size))
    return data.hex().upper()


def _random_mac(rng: random.Random, size: int) -> str:
    data = bytearray(rng.randrange(0, 256) for _ in range(size))
    data[0] &= 0xFE
    return ":".join(f"{b:02x}" for b in data)


def _za470vjeq(value: str) -> str:
    stage1 = _des_encrypt(DES_KEY_STAGE1, value.encode("utf-8"))
    step1 = _base64url(stage1)
    stage2 = _des_encrypt(DES_KEY_STAGE2, step1.encode("utf-8"))
    return base64.b32encode(stage2).decode("ascii").replace("=", "")


def _des_encrypt(key_text: str, data: bytes) -> bytes:
    key = hashlib.sha1(key_text.encode("utf-8")).digest()[:8]
    cipher = DES.new(key, DES.MODE_ECB)
    return cipher.encrypt(pad(data, 8))


def _base64url(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii").replace("+", "-").replace("/", "_").rstrip("=")


def _read_bool(payload: bytes) -> bool:
    return bool(payload and payload[0])


def _read_int(payload: bytes) -> int | None:
    if len(payload) < 4:
        return None
    return int.from_bytes(payload[:4], "big", signed=True)


def _read_mc_string(payload: bytes) -> str:
    try:
        length, size = read_varint(payload, 0)
    except ProtocolError:
        return payload.decode("utf-8", errors="ignore")
    if length < 0:
        return ""
    end = min(size + length, len(payload))
    raw = payload[size:end]
    return raw.decode("utf-8", errors="ignore")


def _build_germmod_payload(fingerprint: str) -> bytes:
    return (
        (16).to_bytes(4, "big", signed=True)
        + _write_mc_string("zs.mcohmygod.com")
        + _write_mc_string("GermMC")
        + _write_mc_string("zs.mcohmygod.com")
        + _write_mc_string(fingerprint)
    )


def _write_mc_string(value: str) -> bytes:
    raw = value.encode("utf-8")
    return write_varint(len(raw)) + raw


def _now_ms() -> int:
    return int(time.time() * 1000)
