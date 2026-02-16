from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import ssl
import struct
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

from camellia.mc.protocol import ProtocolError, read_string
from camellia.plugins.events import PacketDirection, PluginMessageEvent

TARGET_GAME_ID = "4634308743419409922"
REGISTER_CHANNEL = "REGISTER"
BRAND_CHANNEL = "MC|Brand"
UVIEW_CHANNEL = "UView"
CUSTOM_SKIN_LOADER_CHANNEL = "CustomSkinLoader"

PACKET_CLOSE_GUI = "PacketCloseGui"
PACKET_RESOURCE_VERSION = "PacketResourceVersion"
PACKET_SECRET_KEY_REQUEST = "PacketSecretKeyRequest"
PACKET_SECRET_KEY_RESPONSE = "PacketSecretKeyResponse"
SECRET_KEY_TYPE_RES = "res"

BRAND_MARKER = "<- dSpigot"
UPLOAD_URL = "https://upload.server.domcer.com:25566/uploadJpg?key=fea2c199-0341-0fe8-c1f6-8ea2fd831b3b&type="
UPLOAD_BOUNDARY = "MyBoundary"
REGISTER_HEX = (
    "464D4C7C485300464D4C00464D4C7C4D5000464D4C00616E74696D6F6400"
    "437573746F6D536B696E4C6F6164657200555669657700464F524745"
)
REGISTER_PAYLOAD = bytes.fromhex(REGISTER_HEX)
CHECK_LIST_JSON = (
    "[\"419C05FE9BE71F792B2D76CFC9B67F1ED0FEC7F6\\u0000soundsystem-20120107.jar\","
    "\"070FA623BD47BE6ED9AF84BDD03C8172B188CD8B\\u00004640208077513769652@3@0.jar\","
    "\"99BF196CF2CA7CD630805D1C458ADBE0A99A0006\\u0000config-1.2.1.jar\","
    "\"320A2DFD18513A5F41B4E75729DF684488CBD925\\u0000twitch-6.5.jar\","
    "\"5EB80E4A779F012F4291B46FE0A36ABC357A03BD\\u0000scala-continuations-plugin_2.11.1-1.0.2.jar\","
    "\"EC319FF0AB355F25565B2107A211DB26D2494186\\u0000launchwrapper-1.12.jar\","
    "\"9CE04E34240F674BC72680F8B843B1457383161A\\u0000commons-codec-1.9.jar\","
    "\"0E6647973DF4049A51AFCAE0B1191E887C63B58F\\u0000log4j-api-2.15.1.jar\","
    "\"000FF86C9E10B8EA4DBF8164623D3B6580BA029E\\u00004618827437296985101@3@0.jar\","
    "\"803FF252FEDBD395BAFFD43B37341DC4A150A554\\u0000jna-3.4.0.jar\","
    "\"0BFC353F1258A6FE4DB6A4C546E7F4E49D06BA41\\u0000scala-xml_2.11-1.0.2.jar\","
    "\"A698750C16740FD5B3871425F4CB3BBAA87F529D\\u0000commons-compress-1.8.1.jar\","
    "\"3413ED8D96751B3E9E1A9703FC3D8FD3CD720652\\u00004618419806295460477@3@0.jar\","
    "\"90A3822C38EC8C996E84C16A3477EF632CBC87A3\\u0000commons-lang3-3.3.2.jar\","
    "\"31FBBFF1DDBF98F3AA7377C94D33B0447C646B6E\\u0000httpcore-4.3.2.jar\","
    "\"281740B6EBC1C1DAFD65E6F561A54F6EE2423FB5\\u00004663899996118045598@3@0.jar\","
    "\"79846BA34CBD89E2422D74D53752F993DCC2CCAF\\u0000vecmath-1.5.2.jar\","
    "\"D362D58A28F5373B141B9E426E8E160638BFAFCD\\u00004672816195709755108@2@8.jar\","
    "\"0294104AAF1781D6A56A07D561E792C5D0C95F45\\u0000netty-all-4.0.23.Final.jar\","
    "\"81F88CBA916FF220414C9AA3F66BDD9CB07CD327\\u00004672816195671426289@2@8.jar\","
    "\"07190AAD48991720A1FE4B3A3DAF2D35486318EF\\u00004672816195680992971@2@8.jar\","
    "\"7AFE0626116F76479E8FE0A26E8F6D5C11E36695\\u0000scala-swing_2.11-1.0.1.jar\","
    "\"A94B1541B04EB2C231E893F0369A2BD7A88890EB\\u0000scala-continuations-library_2.11-1.0.2.jar\","
    "\"4C72F2E9EAD3F73F63A0D574F13DAF3F4B0338B8\\u00004672816195699798094@2@8.jar\","
    "\"521616DC7487B42BEF0E803BD2FA3FAF668101D7\\u0000lzma-0.0.1.jar\","
    "\"6952FD5954AE64AB03503B3810810A1011F05249\\u00004663899996085643111@3@0.jar\","
    "\"427057882E76D5711AF6BF8008AA2848EFAA4679\\u00004672816195721344264@2@8.jar\","
    "\"D51A7C040A721D13EFDFBD34F8B257B2DF882AD0\\u0000lwjgl_util-2.9.4-nightly-20150209.jar\","
    "\"4333508B8DD8EE72AA4E39AFA713B3A74579B773\\u0000asm-all-5.0.3.jar\","
    "\"CC6A644757AF0CEACCBB4BCBB1B76ACD69084AF1\\u00004626894634154779079@3@0.jar\","
    "\"ADB5FFC55F44B506782CD876BE21FBA6E7303047\\u0000scala-parser-combinators_2.11-1.0.1.jar\","
    "\"4B75825A06139752BD800D9E29C5FD55B8B1B1E4\\u0000netty-1.6.jar\","
    "\"2D9530D0A25DAFFAFFDA7C35037B046B627BB171\\u0000jline-2.13.jar\","
    "\"73E80D0794C39665AEC3F62EEE88CA91676674EF\\u0000librarylwjglopenal-20100824.jar\","
    "\"39C7796B469A600F72380316F6B1F11DB6C2C7C4\\u0000jinput-2.0.5.jar\","
    "\"42CCAF4761F0DFDFA805C9E340D99A755907E2DD\\u0000trove4j-3.0.3.jar\","
    "\"076689F7AC17DB3F03E95BB2EF161CBBF959F6FA\\u0000forge-1.8.9-11.15.1.1722.jar\","
    "\"B94D6EA6A821CE5BFE2C1048710A8DD93857884C\\u0000akka-actor_2.11-2.3.3.jar\","
    "\"A60A5E993C98C864010053CB901B7EAB25306568\\u0000gson-2.2.4.jar\","
    "\"B46E2EC31CDC1F02923F8C0374671D6D5884CD3E\\u00004618424574399199550@3@0.jar\","
    "\"F5A1492D1AA29054455685AC959172852076AC22\\u0000javassist-1.12.jar\","
    "\"697517568C68E78AE0B4544145AF031C81082DFE\\u0000lwjgl-2.9.4-nightly-20150209.jar\","
    "\"56220BEADCC3FC57A343780AAEB7E34C3D691587\\u00004620273813222949778@3@0.jar\","
    "\"6C0AEFAE854C2CA19A75A283CEA224B212B17297\\u0000scala-library-2.11.1.jar\","
    "\"8F363B35FD4DF1389F3CD9408C5BE2B23B61E9A0\\u00004672816195690538733@2@8.jar\","
    "\"AC84D139DD6D223B551DC6F0F23D76D12631207B\\u00004620702952524438419@3@0.jar\","
    "\"307990DF74CDF9BE6AFBF35B0457C83A9F2D875F\\u0000.minecraft\","
    "\"A7087FD6B59AB33A1152031DFB2547D2FFD3C67A\\u00001.8.9.jar\","
    "\"9C6C59B742D8E038A15F64C1AA273A893A658424\\u0000realms-1.7.59.jar\","
    "\"9DDF7B048A8D701BE231C0F4F95FD986198FD2D8\\u0000oshi-core-1.1.jar\","
    "\"306816FB57CF94F108A43C95731B08934DCAE15C\\u0000jopt-simple-4.6.jar\","
    "\"E12FE1FDA814BD348C1579329C86943D2CD3C6A6\\u0000jutils-1.0.0.jar\","
    "\"4526510DFD5009954301DF204AEF2EA50232A1FE\\u0000scala-actors-migration_2.11-1.1.0.jar\","
    "\"80ABDC65EAEA2BE3B74B8B527D267342673F2480\\u00004620273813196076442@3@0.jar\","
    "\"7A81F23C01A16797AA844B2CA3ECBCD8FBAE45C3\\u00004624103992226684617@3@0.jar\","
    "\"5C5E304366F75F9EAA2E8CCA546A1FB6109348B3\\u0000libraryjavasound-20101123.jar\","
    "\"FA0353471AAB70D5189F953F3984092C374059DB\\u00004620273813159696403@3@0.jar\","
    "\"47D99359C55845FD91A9FD37D5EEE1AE8BB8BF7D\\u0000scala-compiler-2.11.1.jar\","
    "\"63D216A9311CCA6BE337C1E458E587F99D382B84\\u0000icu4j-core-mojang-51.2.jar\","
    "\"4BAFF23209F18E76AE5EE5E27D8F17FAA9BC91BB\\u0000scala-reflect-2.11.1.jar\","
    "\"F6F66E966C70A83FFBDB6F17A0919EAF7C8ACA7F\\u0000commons-logging-1.1.3.jar\","
    "\"C73B5636FAF089D9F00E8732A829577DE25237EE\\u0000codecjorbis-20101023.jar\","
    "\"B1B6EA3B7E4AA4F492509A4952029CD8E48019AD\\u0000commons-io-2.4.jar\","
    "\"87E2987FFA166D192B322B11FDCEE4861B40AD23\\u00004672816195730710794@2@8.jar\","
    "\"9C6EF172E8DE35FD8D4D8783E4821E57CDEF7445\\u0000guava-17.0.jar\","
    "\"18F4247FF4572A074444572CEE34647C43E7C9C7\\u0000httpclient-4.3.3.jar\","
    "\"56D28800D86CB73D5A44E6353915E9CC046B6BFC\\u0000authlib-1.5.21.jar\","
    "\"FBB83DC03700A6F97E4F7382261831205C1A46B4\\u0000log4j-core-2.15.1.jar\","
    "\"12F031CFE88FEF5C1DD36C563C0A3A69BD7261DA\\u0000codecwav-20101023.jar\"]"
)
_UPLOAD_HEADERS = {
    "Content-Type": f"multipart/form-data; boundary={UPLOAD_BOUNDARY}",
    "Connection": "keep-alive",
    "Charset": "UTF-8",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "User-Agent": "Java/1.8.0_60",
}

_FALLBACK_JPEG = base64.b64decode(
    b"/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAP//////////////////////////////////////////////////////////////////////////////////////"
    b"2wBDAf//////////////////////////////////////////////////////////////////////////////////////"
    b"wAARCAABAAEDASIAAhEBAxEB/8QAFwAAAwEAAAAAAAAAAAAAAAAAAAQFBv/EABQBAQAAAAAAAAAAAAAAAAAAAAD/"
    b"2gAMAwEAAhADEAAAAf8A/8QAFBABAAAAAAAAAAAAAAAAAAAAAP/aAAgBAQABPwA//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/"
    b"aAAgBAgEBPwA//8QAFBEBAAAAAAAAAAAAAAAAAAAAAP/aAAgBAwEBPwA//9k="
)


@dataclass
class DomcerState:
    sent_close_gui: bool = False
    register_sent: bool = False
    last_guid: str | None = None
    last_resource_version: int | None = None
    awaiting_secret: bool = False
    uploading: bool = False


def setup(context) -> None:
    events = context.events
    logger = context.logger

    async def on_plugin_message(event: PluginMessageEvent) -> None:
        session = event.session
        if getattr(session, "protocol_version", None) != 47:
            return
        game_id = getattr(session, "game_id", None)
        if TARGET_GAME_ID and game_id not in (None, TARGET_GAME_ID):
            return

        if event.identifier == REGISTER_CHANNEL:
            await _handle_register(event, logger)
            return

        if event.identifier == BRAND_CHANNEL and event.direction == PacketDirection.CLIENTBOUND:
            await _handle_brand(event, logger)
            return

        if event.identifier in (UVIEW_CHANNEL, CUSTOM_SKIN_LOADER_CHANNEL):
            if event.direction == PacketDirection.CLIENTBOUND:
                await _handle_clientbound(event, logger)
                event.cancelled = True
            return

    events.on("base_108x", on_plugin_message, event_type=PluginMessageEvent)


def _get_state(session) -> DomcerState:
    state = getattr(session, "_domcer_state", None)
    if state is None:
        state = DomcerState()
        setattr(session, "_domcer_state", state)
    return state


async def _handle_register(event: PluginMessageEvent, logger) -> None:
    state = _get_state(event.session)
    if event.direction == PacketDirection.SERVERBOUND:
        event.payload = REGISTER_PAYLOAD
        return
    if not state.register_sent:
        await event.session.send_plugin_message(PacketDirection.SERVERBOUND, REGISTER_CHANNEL, REGISTER_PAYLOAD)
        state.register_sent = True
        logger.info("DoMCer: sent REGISTER list to server")
    if not state.sent_close_gui:
        await _send_close_gui(event.session, logger)


async def _handle_brand(event: PluginMessageEvent, logger) -> None:
    payload = _payload_bytes(event.payload)
    if not payload:
        return
    try:
        brand, _ = read_string(payload, 0, 32767)
    except ProtocolError:
        brand = payload.decode("ascii", "ignore")
    if BRAND_MARKER not in brand:
        return
    guid = str(uuid.uuid4())
    _get_state(event.session).last_guid = guid
    await event.session.send_plugin_message(
        PacketDirection.SERVERBOUND,
        CUSTOM_SKIN_LOADER_CHANNEL,
        guid.encode("ascii"),
    )
    logger.info("DoMCer: brand matched, sent CustomSkinLoader guid=%s", guid)


async def _handle_clientbound(event: PluginMessageEvent, logger) -> None:
    if event.identifier == CUSTOM_SKIN_LOADER_CHANNEL:
        await _handle_custom_skin_loader(event, logger)
        return
    if event.identifier != UVIEW_CHANNEL:
        return
    payload = _payload_bytes(event.payload)
    name, offset = _read_uview_string(payload, 0)
    if not name:
        logger.debug("DoMCer: empty UView payload")
        return
    state = _get_state(event.session)
    if name == PACKET_RESOURCE_VERSION:
        version, _ = _read_uview_int(payload, offset)
        state.last_resource_version = version
        await _send_secret_key_request(event.session, logger)
        logger.info("DoMCer: resource version=%s, requested secret key", version)
        return
    if name == PACKET_SECRET_KEY_RESPONSE:
        response, _ = _read_uview_string(payload, offset)
        state.awaiting_secret = False
        logger.info("DoMCer: secret key response=%s", response)
        return
    logger.debug("DoMCer: UView packet=%s len=%s", name, len(payload))


async def _handle_custom_skin_loader(event: PluginMessageEvent, logger) -> None:
    payload = _payload_bytes(event.payload)
    if not payload:
        return
    text = payload.decode("ascii", "ignore")
    guid = _extract_guid(text)
    if not guid:
        logger.debug("DoMCer: CustomSkinLoader payload=%r", text[:120])
        return
    state = _get_state(event.session)
    if state.uploading:
        logger.debug("DoMCer: upload already in progress guid=%s", guid)
        return
    state.last_guid = guid
    state.uploading = True
    logger.info("DoMCer: CustomSkinLoader guid=%s, uploading screenshot", guid)
    try:
        data = await asyncio.to_thread(_upload_screenshot, guid, logger)
    finally:
        state.uploading = False
    if not data:
        logger.warning("DoMCer: upload failed guid=%s", guid)
        return
    reply = f"{guid}:{data}".encode("utf-8")
    await event.session.send_plugin_message(PacketDirection.SERVERBOUND, CUSTOM_SKIN_LOADER_CHANNEL, reply)
    logger.info("DoMCer: CustomSkinLoader reply sent guid=%s", guid)


async def _send_close_gui(session, logger) -> None:
    state = _get_state(session)
    payload = _uview_pack_strings(PACKET_CLOSE_GUI)
    await session.send_plugin_message(PacketDirection.SERVERBOUND, UVIEW_CHANNEL, payload)
    state.sent_close_gui = True
    logger.info("DoMCer: sent PacketCloseGui")


async def _send_secret_key_request(session, logger) -> None:
    state = _get_state(session)
    payload = _uview_pack_strings(PACKET_SECRET_KEY_REQUEST, SECRET_KEY_TYPE_RES)
    await session.send_plugin_message(PacketDirection.SERVERBOUND, UVIEW_CHANNEL, payload)
    state.awaiting_secret = True
    logger.info("DoMCer: sent PacketSecretKeyRequest type=%s", SECRET_KEY_TYPE_RES)


def _uview_pack_strings(*values: str | None) -> bytes:
    out = bytearray()
    for value in values:
        out += _uview_write_string(value)
    return bytes(out)


def _uview_write_string(value: str | None) -> bytes:
    if value is None:
        return struct.pack(">i", -1)
    raw = value.encode("utf-8")
    return struct.pack(">i", len(raw)) + raw


def _read_uview_int(data: bytes, offset: int) -> tuple[int, int]:
    if offset + 4 > len(data):
        return 0, offset
    return struct.unpack(">i", data[offset : offset + 4])[0], offset + 4


def _read_uview_string(data: bytes, offset: int) -> tuple[str | None, int]:
    length, offset = _read_uview_int(data, offset)
    if length < 0:
        return None, offset
    if length == 0:
        return "", offset
    end = offset + length
    if end > len(data):
        return None, len(data)
    return data[offset:end].decode("utf-8", "replace"), end


def _payload_bytes(payload: bytes | bytearray | memoryview | None) -> bytes:
    if payload is None:
        return b""
    if isinstance(payload, memoryview):
        return payload.tobytes()
    if isinstance(payload, bytearray):
        return bytes(payload)
    return payload


def _extract_guid(text: str) -> str | None:
    candidate = text.strip()
    if not candidate:
        return None
    if ":" in candidate:
        candidate = candidate.split(":", 1)[0]
    try:
        return str(uuid.UUID(candidate))
    except (ValueError, AttributeError):
        return None


def _build_multipart(guid: str, image_bytes: bytes, content_type: str, file_ext: str) -> bytes:
    boundary = UPLOAD_BOUNDARY
    boundary_bytes = boundary.encode("utf-8")
    filename = f"{guid}{file_ext}"
    parts: list[bytes] = []
    # check part (server expects multipart file part named "check")
    parts.append(b"--" + boundary_bytes + b"\r\n")
    parts.append(
        f'Content-Disposition: form-data; name="check"; filename="{filename}"\r\n'.encode("utf-8")
    )
    parts.append(b"Content-Type: text/plain; charset=UTF-8\r\n\r\n")
    parts.append(CHECK_LIST_JSON.encode("utf-8"))
    parts.append(b"\r\n")
    # file part
    parts.append(b"--" + boundary_bytes + b"\r\n")
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'.encode("utf-8")
    )
    parts.append(f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"))
    parts.append(image_bytes)
    parts.append(b"\r\n")
    parts.append(b"--" + boundary_bytes + b"--\r\n")
    return b"".join(parts)


def _load_screenshot_image(logger) -> tuple[bytes, str, str]:
    base_dir = os.path.dirname(__file__)
    jpg_path = os.path.join(base_dir, "playing.jpg")
    png_path = os.path.join(base_dir, "playing.png")
    if os.path.exists(jpg_path):
        with open(jpg_path, "rb") as handle:
            logger.info("DoMCer: using playing.jpg")
            return handle.read(), "image/jpeg", ".jpg"
    if os.path.exists(png_path):
        with open(png_path, "rb") as handle:
            png_bytes = handle.read()
        try:
            from PIL import Image  # optional
        except Exception as exc:
            logger.info("DoMCer: PIL not available, uploading PNG (%s)", exc)
            return png_bytes, "image/png", ".png"
        try:
            image = Image.open(io.BytesIO(png_bytes)).convert("RGB")
            buffer = io.BytesIO()
            image.save(buffer, format="JPEG", quality=90)
            logger.info("DoMCer: converted playing.png to JPEG")
            return buffer.getvalue(), "image/jpeg", ".jpg"
        except Exception as exc:
            logger.warning("DoMCer: PNG->JPEG failed (%s)", exc)
            return png_bytes, "image/png", ".png"
    logger.warning("DoMCer: no playing.jpg/playing.png, using fallback JPEG")
    return _FALLBACK_JPEG, "image/jpeg", ".jpg"


def _upload_screenshot(guid: str, logger) -> str | None:
    image_bytes, content_type, file_ext = _load_screenshot_image(logger)
    filename = f"{guid}{file_ext}"
    check_bytes = CHECK_LIST_JSON.encode("utf-8")
    try:
        import requests  # type: ignore
    except Exception:
        requests = None

    if requests is not None:
        headers = {k: v for k, v in _UPLOAD_HEADERS.items() if k.lower() != "content-type"}
        files = {
            "check": (filename, check_bytes, "text/plain; charset=UTF-8"),
            "file": (filename, image_bytes, content_type),
        }
        try:
            resp = requests.post(UPLOAD_URL, files=files, headers=headers, timeout=15)
            if resp.status_code >= 400:
                logger.warning("DoMCer: upload failed HTTP %s body=%s", resp.status_code, resp.text[:200])
                return None
            raw = resp.text
        except Exception as exc:
            logger.warning("DoMCer: upload failed %s", exc)
            return None
    else:
        body = _build_multipart(guid, image_bytes, content_type, file_ext)
        req = urllib.request.Request(UPLOAD_URL, data=body, headers=_UPLOAD_HEADERS, method="POST")
        ctx = None
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:
                raw = ""
            logger.warning("DoMCer: upload failed HTTP %s body=%s", exc.code, raw[:200])
            return None
        except Exception as exc:
            logger.warning("DoMCer: upload failed %s", exc)
            try:
                ctx = ssl._create_unverified_context()
                with urllib.request.urlopen(req, timeout=15, context=ctx) as resp:
                    raw = resp.read().decode("utf-8", "replace")
            except Exception as exc2:
                logger.warning("DoMCer: upload failed (no verify) %s", exc2)
                return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("DoMCer: upload response not json: %s", raw[:200])
        return None
    if isinstance(data, dict):
        value = data.get("data")
        if isinstance(value, str) and value:
            return value
    logger.warning("DoMCer: upload response missing data")
    return None
