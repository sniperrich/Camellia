from __future__ import annotations

from camellia.plugins.events import AnimationEvent, PluginMessageEvent


def setup(context) -> None:
    events = context.events

    async def forward(event) -> None:
        await events.emit("base_108x", event)

    events.on("plugin_message", forward, event_type=PluginMessageEvent)
    events.on("animation", forward, event_type=AnimationEvent)
