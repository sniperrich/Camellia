"""Python plugin loader for Camellia."""

from .events import (
    AnimationEvent,
    LoginSuccessEvent,
    PacketDirection,
    PluginEventBus,
    PluginMessageEvent,
    get_event_bus,
)
from .manager import (
    PluginContext,
    PluginManager,
    PluginMeta,
    PluginState,
    get_plugin_manager,
)

__all__ = [
    "PluginContext",
    "PluginEventBus",
    "PluginManager",
    "PluginMeta",
    "PluginState",
    "AnimationEvent",
    "LoginSuccessEvent",
    "PacketDirection",
    "PluginMessageEvent",
    "get_event_bus",
    "get_plugin_manager",
]
