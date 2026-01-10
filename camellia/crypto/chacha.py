from __future__ import annotations

from typing import List


_SIGMA = [0x61707865, 0x3320646E, 0x79622D32, 0x6B206574]


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) & 0xFFFFFFFF) | (value >> (32 - shift))


def _quarter_round(state: List[int], a: int, b: int, c: int, d: int) -> None:
    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl(state[d], 16)

    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl(state[b], 12)

    state[a] = (state[a] + state[b]) & 0xFFFFFFFF
    state[d] ^= state[a]
    state[d] = _rotl(state[d], 8)

    state[c] = (state[c] + state[d]) & 0xFFFFFFFF
    state[b] ^= state[c]
    state[b] = _rotl(state[b], 7)


def _chacha_block(key: bytes, nonce: bytes, counter: int, rounds: int) -> bytes:
    if len(key) != 32:
        raise ValueError("ChaCha key must be 32 bytes")
    if len(nonce) != 12:
        raise ValueError("ChaCha nonce must be 12 bytes")

    key_words = [int.from_bytes(key[i:i + 4], "little") for i in range(0, 32, 4)]
    nonce_words = [int.from_bytes(nonce[i:i + 4], "little") for i in range(0, 12, 4)]

    state = _SIGMA + key_words + [counter & 0xFFFFFFFF] + nonce_words
    working = state.copy()

    for _ in range(rounds // 2):
        _quarter_round(working, 0, 4, 8, 12)
        _quarter_round(working, 1, 5, 9, 13)
        _quarter_round(working, 2, 6, 10, 14)
        _quarter_round(working, 3, 7, 11, 15)

        _quarter_round(working, 0, 5, 10, 15)
        _quarter_round(working, 1, 6, 11, 12)
        _quarter_round(working, 2, 7, 8, 13)
        _quarter_round(working, 3, 4, 9, 14)

    output = [(working[i] + state[i]) & 0xFFFFFFFF for i in range(16)]
    return b"".join(word.to_bytes(4, "little") for word in output)


class ChaChaPacker:
    def __init__(self, key: bytes, nonce: bytes, rounds: int = 8, counter_start: int = 0) -> None:
        self.key = key
        self.nonce = nonce
        self.rounds = rounds
        self.counter = counter_start & 0xFFFFFFFF

    def _keystream(self, length: int) -> bytes:
        out = bytearray()
        while len(out) < length:
            block = _chacha_block(self.key, self.nonce, self.counter, self.rounds)
            self.counter = (self.counter + 1) & 0xFFFFFFFF
            out.extend(block)
        return bytes(out[:length])

    def process_bytes(self, data: bytearray, offset: int, length: int) -> None:
        stream = self._keystream(length)
        for i in range(length):
            data[offset + i] ^= stream[i]
