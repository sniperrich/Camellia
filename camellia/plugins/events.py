from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable


class PacketDirection(str, Enum):
    SERVERBOUND = "serverbound"
    CLIENTBOUND = "clientbound"


@dataclass
class BaseEvent:
    session: Any
    cancelled: bool = False


@dataclass
class PluginMessageEvent(BaseEvent):
    direction: PacketDirection = PacketDirection.SERVERBOUND
    identifier: str = ""
    payload: bytes = b""


@dataclass
class ChatMessageEvent(BaseEvent):
    message: str = ""


@dataclass
class PlayerPositionEvent(BaseEvent):
    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    pitch: float = 0.0
    on_ground: bool = False


@dataclass
class AnimationEvent(BaseEvent):
    pass


@dataclass
class LoginSuccessEvent(BaseEvent):
    pass


@dataclass
class SwingArmEvent(BaseEvent):
    hand: int = 0


@dataclass
class UseItemEvent(BaseEvent):
    hand: int = 0
    sequence: int = 0


@dataclass
class UseItemOnEvent(BaseEvent):
    hand: int = 0
    location: tuple[int, int, int] | None = None
    face: int = 0
    cursor_x: float = 0.0
    cursor_y: float = 0.0
    cursor_z: float = 0.0
    inside_block: bool = False
    sequence: int = 0


@dataclass
class GameJoinEvent(BaseEvent):
    player_id: int | None = None
    payload: bytes = b""


@dataclass
class InteractEvent(BaseEvent):
    entity_id: int = 0
    type: int = 0
    target_x: float | None = None
    target_y: float | None = None
    target_z: float | None = None
    hand: int | None = None
    sneaking: bool = False


@dataclass
class SetEntityMetadataEvent(BaseEvent):
    entity_id: int = 0
    raw_data: bytes = b""


Handler = Callable[[Any], Any]


@dataclass(frozen=True)
class _HandlerEntry:
    handler: Handler
    event_type: type | None
    priority: int


class PluginEventBus:
    def __init__(self) -> None:
        self._handlers: dict[str, list[_HandlerEntry]] = {}
        self._logger = logging.getLogger("camellia.plugins.events")

    def on(self, name: str, handler: Handler, *, event_type: type | None = None, priority: int = 0) -> None:
        entries = self._handlers.setdefault(name, [])
        entries.append(_HandlerEntry(handler=handler, event_type=event_type, priority=priority))
        entries.sort(key=lambda entry: entry.priority, reverse=True)

    def reset(self) -> None:
        self._handlers.clear()

    async def emit(self, name: str, event: Any) -> None:
        entries = list(self._handlers.get(name, []))
        if not entries:
            return
        for entry in entries:
            if entry.event_type is not None and not isinstance(event, entry.event_type):
                continue
            try:
                result = entry.handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as exc:  # pylint: disable=broad-except
                self._logger.warning("Event handler failed: %s (%s)", name, exc)
            if getattr(event, "cancelled", False):
                break


_DEFAULT_BUS: PluginEventBus | None = None


def get_event_bus() -> PluginEventBus:
    global _DEFAULT_BUS  # pylint: disable=global-statement
    if _DEFAULT_BUS is None:
        _DEFAULT_BUS = PluginEventBus()
    return _DEFAULT_BUS
