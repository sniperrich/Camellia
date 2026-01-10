from __future__ import annotations

import zlib
from dataclasses import dataclass
from typing import Optional, Tuple


class ProtocolError(ValueError):
    pass


class IncompleteVarInt(ProtocolError):
    pass


def read_varint(data: bytes, offset: int = 0) -> Tuple[int, int]:
    num = 0
    num_read = 0
    while True:
        if offset + num_read >= len(data):
            raise IncompleteVarInt("not enough bytes for varint")
        byte = data[offset + num_read]
        num |= (byte & 0x7F) << (7 * num_read)
        num_read += 1
        if num_read > 5:
            raise ProtocolError("varint too big")
        if (byte & 0x80) == 0:
            break
    return num, num_read


def write_varint(value: int) -> bytes:
    if value < 0:
        raise ProtocolError("varint cannot be negative")
    out = bytearray()
    while (value & -128) != 0:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0xFF)
    return bytes(out)


def read_bool(data: bytes, offset: int = 0) -> Tuple[bool, int]:
    if offset >= len(data):
        raise ProtocolError("bool out of range")
    return data[offset] != 0, 1


def read_ushort(data: bytes, offset: int = 0) -> Tuple[int, int]:
    end = offset + 2
    if end > len(data):
        raise ProtocolError("ushort out of range")
    return int.from_bytes(data[offset:end], "big"), 2


def write_ushort(value: int) -> bytes:
    if value < 0 or value > 0xFFFF:
        raise ProtocolError("ushort out of range")
    return int(value).to_bytes(2, "big")


def read_bytes(data: bytes, offset: int, length: int) -> Tuple[bytes, int]:
    end = offset + length
    if end > len(data):
        raise ProtocolError("bytes out of range")
    return data[offset:end], length


def read_string(data: bytes, offset: int = 0, max_length: int = 32767) -> Tuple[str, int]:
    length, size = read_varint(data, offset)
    if length < 0:
        raise ProtocolError("string length negative")
    if length > max_length * 4:
        raise ProtocolError("string length too big")
    start = offset + size
    end = start + length
    if end > len(data):
        raise ProtocolError("string out of range")
    value = data[start:end].decode("utf-8")
    if len(value) > max_length:
        raise ProtocolError("string exceeds max length")
    return value, size + length


def write_string(value: str, max_length: int = 32767) -> bytes:
    if len(value) > max_length:
        raise ProtocolError("string too long")
    raw = value.encode("utf-8")
    return write_varint(len(raw)) + raw


def read_byte_array(data: bytes, offset: int = 0) -> Tuple[bytes, int]:
    length, size = read_varint(data, offset)
    if length < 0:
        raise ProtocolError("byte array length negative")
    payload, consumed = read_bytes(data, offset + size, length)
    return payload, size + consumed


def write_byte_array(value: bytes) -> bytes:
    return write_varint(len(value)) + value


def decompress_packet(payload: bytes, threshold: int) -> bytes:
    data_length, size = read_varint(payload, 0)
    if data_length == 0:
        return payload[size:]
    if data_length < threshold:
        raise ProtocolError("compressed packet below threshold")
    decompressed = zlib.decompress(payload[size:])
    if len(decompressed) != data_length:
        raise ProtocolError("decompressed length mismatch")
    return decompressed


def compress_packet(payload: bytes, threshold: int) -> bytes:
    if len(payload) < threshold:
        return write_varint(0) + payload
    compressed = zlib.compress(payload)
    return write_varint(len(payload)) + compressed


@dataclass
class PacketFrame:
    payload: bytes


class PacketFramer:
    def __init__(self, max_varint_len: int = 3) -> None:
        self._buffer = bytearray()
        self._max_varint_len = max_varint_len

    def feed(self, data: bytes) -> Tuple[PacketFrame, ...]:
        if not data:
            return ()
        self._buffer.extend(data)
        frames = []
        while True:
            result = self._try_read_length()
            if result is None:
                break
            length, size = result
            if len(self._buffer) < size + length:
                break
            start = size
            end = size + length
            frames.append(PacketFrame(payload=bytes(self._buffer[start:end])))
            del self._buffer[:end]
        return tuple(frames)

    def _try_read_length(self) -> Optional[Tuple[int, int]]:
        num = 0
        num_read = 0
        while True:
            if num_read >= len(self._buffer):
                return None
            byte = self._buffer[num_read]
            num |= (byte & 0x7F) << (7 * num_read)
            num_read += 1
            if num_read > self._max_varint_len:
                raise ProtocolError("packet length varint too long")
            if (byte & 0x80) == 0:
                break
        return num, num_read


def wrap_packet(payload: bytes) -> bytes:
    length = len(payload)
    if length > 0x1FFFFF:
        raise ProtocolError("packet too large for 21-bit length")
    return write_varint(length) + payload
