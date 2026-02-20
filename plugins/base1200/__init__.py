from __future__ import annotations

import os
from pathlib import Path

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
    logger = context.logger

    # 创建调试日志文件
    debug_log = Path("logs/debug-base1200.log")
    debug_log.parent.mkdir(exist_ok=True)

    def write_debug(msg: str) -> None:
        with open(debug_log, "a") as f:
            from datetime import datetime
            f.write(f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} {msg}\n")
            f.flush()

    write_debug("Base1200 plugin loaded")

    async def forward(event) -> None:
        session = getattr(event, "session", None)
        proto = getattr(session, "protocol_version", None)
        # Base1200 仅用于 1.20+（避免影响 1.8.9 / base108x 的流程）
        if proto is not None and proto < 763:
            return
        if isinstance(event, PluginMessageEvent):
            state = getattr(session, "state", None)
            state_name = getattr(state, "name", None)
            if state_name not in ("PLAY", "CONFIGURATION"):
                return
            msg = f"Forwarding plugin_message: dir={event.direction} id={event.identifier}"
            logger.info(f"Base1200: {msg}")
            write_debug(msg)
        await events.emit("base_1200", event)

    events.on("plugin_message", forward, event_type=PluginMessageEvent)
    events.on("swing_arm", forward, event_type=SwingArmEvent)
    events.on("use_item", forward, event_type=UseItemEvent)
    events.on("use_item_on", forward, event_type=UseItemOnEvent)
    events.on("game_join", forward, event_type=GameJoinEvent)
    events.on("interact", forward, event_type=InteractEvent)
    events.on("set_entity_metadata", forward, event_type=SetEntityMetadataEvent)
