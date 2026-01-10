from __future__ import annotations

from camellia.plugins.events import (
    GameJoinEvent,
    InteractEvent,
    PluginMessageEvent,
    SetEntityMetadataEvent,
    SwingArmEvent,
    UseItemEvent,
    UseItemOnEvent,
)


def setup(context) -> None:
    events = context.events

    async def forward(event) -> None:
        await events.emit("base_1200", event)

    events.on("plugin_message", forward, event_type=PluginMessageEvent)
    events.on("swing_arm", forward, event_type=SwingArmEvent)
    events.on("use_item", forward, event_type=UseItemEvent)
    events.on("use_item_on", forward, event_type=UseItemOnEvent)
    events.on("game_join", forward, event_type=GameJoinEvent)
    events.on("interact", forward, event_type=InteractEvent)
    events.on("set_entity_metadata", forward, event_type=SetEntityMetadataEvent)
