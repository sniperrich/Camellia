from __future__ import annotations

from camellia.plugins.events import (
    ChatMessageEvent,
    GameJoinEvent,
    PlayerPositionEvent,
    PluginMessageEvent,
)


V1122 = 340


def setup(context) -> None:
    events = context.events

    async def forward(event) -> None:
        session = getattr(event, "session", None)
        proto = getattr(session, "protocol_version", None)
        if proto is not None and int(proto) != V1122:
            return
        await events.emit("base_1122", event)

    events.on("plugin_message", forward, event_type=PluginMessageEvent)
    events.on("chat_message", forward, event_type=ChatMessageEvent)
    events.on("player_position", forward, event_type=PlayerPositionEvent)
    events.on("game_join", forward, event_type=GameJoinEvent)
