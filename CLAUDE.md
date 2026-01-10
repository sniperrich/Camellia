# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Camellia.NEL is a Python rewrite of C# NetEase Minecraft launcher (网易我的世界第三方启动器) projects. It provides CLI and GUI interfaces for logging into NetEase's Minecraft servers, managing game profiles, and proxying Minecraft connections with plugin support.

## Commands

```bash
# Install dependencies
python3 -m pip install -r requirements.txt

# Run CLI (interactive)
python3 camellia_cli.py

# Run GUI
python3 camellia_gui.py
```

No test suite or linter is configured.

## Architecture

### Core Package (`camellia/`)

- **api/** - NetEase/WPFLauncher API clients (`WPFLauncherClient` for login, server queries, character management)
- **crypto/** - Cryptographic utilities (AES-ECB, ChaCha20, RSA, Skip32, MD5)
- **mc/** - Minecraft protocol and proxy logic (`MinecraftProxy`, `Yggdrasil` authentication)
- **models/** - API response dataclasses (`AuthOtp`, `NetGameItem`, `GameCharacter`, etc.)
- **gui/** - PySide6 GUI implementation
- **plugins/** - Plugin system (manager, event bus, base classes)

### Plugin System

Plugins are discovered from `plugins/` directory. Each plugin can be:
- A `.py` file with `PLUGIN_META` dict
- A directory with `__init__.py` and optional `plugin.json`

**Plugin metadata format:**
```python
PLUGIN_META = {
    "id": "plugin_id",
    "name": "Plugin Name",
    "description": "Description",
    "author": "Author",
    "version": "1.0.0",
    "dependencies": []  # optional
}
```

**Plugin initialization:** Implement `setup(context)` function or `Plugin` class with `on_initialize()`/`on_load()` hooks.

**Event system:** `PluginEventBus` dispatches events with priority-based ordering. Key events:
- `LoginSuccessEvent`, `GameJoinEvent` - Connection lifecycle
- `PluginMessageEvent` - Plugin channel messages (bidirectional)
- `SwingArmEvent`, `UseItemEvent`, `InteractEvent` - Player actions

### Proxy Flow

1. `MinecraftProxy` listens for client connections
2. Forwards handshake/login packets to remote server
3. On encryption request: performs Yggdrasil join if configured
4. Emits plugin events for packet interception
5. Forwards encrypted play packets bidirectionally

### Reference Materials

`reference/` contains:
- `decomp_*` - Decompiled C# code from original projects
- `plugins_decomp/` - Decompiled plugin DLLs (base108x, base1200, heypixel, omg_protocol)
- `OpenNEL-master`, `fantnel-master` - Open source reference projects

## Coding Conventions

- Python 3.9+, 4-space indentation, type hints throughout
- `snake_case` for functions/variables, `PascalCase` for classes
- Match existing import grouping and formatting (no formatter configured)

## Security Notes

- Cookie login expects a file containing `sauth_json` JSON blob
- Keep credentials out of the repo
- Proxy mode opens a local TCP listener
- GUI stores passwords in plain text only if explicitly enabled by user
