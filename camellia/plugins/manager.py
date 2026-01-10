from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

from .events import PluginEventBus, get_event_bus


PLUGIN_META_VAR = "PLUGIN_META"
PLUGIN_JSON_NAME = "plugin.json"
DISABLE_SUFFIX = ".disable"


@dataclass
class PluginMeta:
    plugin_id: str
    name: str
    description: str
    author: str
    version: str
    dependencies: list[str] = field(default_factory=list)

    @classmethod
    def from_mapping(cls, data: dict[str, Any], source: str) -> "PluginMeta":
        plugin_id = str(data.get("id") or data.get("plugin_id") or "").strip()
        name = str(data.get("name") or "").strip()
        description = str(data.get("description") or data.get("desc") or data.get("summary") or "").strip()
        author = str(data.get("author") or data.get("publisher") or "").strip()
        version = str(data.get("version") or "").strip()
        dependencies = _normalize_dependencies(data.get("dependencies"))
        missing = [key for key, value in (("id", plugin_id), ("name", name), ("version", version)) if not value]
        if missing:
            raise ValueError(f"{source} 缺少插件字段: {', '.join(missing)}")
        return cls(
            plugin_id=plugin_id,
            name=name,
            description=description,
            author=author,
            version=version,
            dependencies=dependencies,
        )


@dataclass
class PluginSpec:
    path: Path
    entry: Path
    enabled: bool
    meta: PluginMeta


@dataclass
class PluginState:
    meta: PluginMeta
    path: Path
    enabled: bool
    error: str | None = None


@dataclass
class LoadedPlugin:
    spec: PluginSpec
    module_name: str
    module: ModuleType
    instance: Any | None = None


@dataclass
class PluginContext:
    manager: "PluginManager"
    data_dir: Path
    logger: logging.Logger
    extras: dict[str, Any] = field(default_factory=dict)
    events: PluginEventBus = field(default_factory=get_event_bus)

    def plugin_data_dir(self, plugin_id: str) -> Path:
        path = self.data_dir / plugin_id
        path.mkdir(parents=True, exist_ok=True)
        return path


class PluginManager:
    def __init__(
        self,
        plugins_dir: Path | None = None,
        data_dir: Path | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.plugins_dir = plugins_dir or (Path.cwd() / "plugins")
        self.data_dir = data_dir or (Path.home() / ".camellia" / "plugins")
        self.logger = logger or logging.getLogger("camellia.plugins")
        self.plugins_dir.mkdir(parents=True, exist_ok=True)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._loaded_plugins: dict[str, LoadedPlugin] = {}
        self._states: dict[str, PluginState] = {}
        self._loaded_paths: list[str] = []

    def load_plugins(self, *, extras: dict[str, Any] | None = None) -> list[PluginState]:
        self.unload_plugins()
        get_event_bus().reset()
        specs = self._discover_plugins()
        self._states = {}
        for spec in specs:
            if spec.meta.plugin_id in self._states:
                self.logger.warning("插件 ID 冲突: %s (%s)", spec.meta.plugin_id, spec.path)
                continue
            self._states[spec.meta.plugin_id] = PluginState(
                meta=spec.meta,
                path=spec.path,
                enabled=spec.enabled,
            )

        enabled_specs = [spec for spec in specs if spec.enabled]
        self._loaded_paths = sorted(str(spec.path) for spec in enabled_specs)
        if not enabled_specs:
            return list(self._states.values())

        context = PluginContext(self, self.data_dir, self.logger, extras or {})
        self._load_in_dependency_order(enabled_specs, context)
        return list(self._states.values())

    def unload_plugins(self) -> None:
        for plugin in list(self._loaded_plugins.values()):
            instance = plugin.instance
            if instance and hasattr(instance, "on_unload"):
                try:
                    instance.on_unload()
                except Exception as exc:  # pylint: disable=broad-except
                    self.logger.warning("插件卸载失败: %s (%s)", plugin.spec.meta.name, exc)
            if plugin.module_name in sys.modules:
                del sys.modules[plugin.module_name]
        self._loaded_plugins.clear()

    def reload_if_changed(self, *, extras: dict[str, Any] | None = None) -> bool:
        specs = self._discover_plugins()
        current = sorted(str(spec.path) for spec in specs if spec.enabled)
        if current == self._loaded_paths:
            return False
        self.load_plugins(extras=extras)
        return True

    def get_plugin_states(self) -> list[PluginState]:
        return list(self._states.values())

    def disable_plugin(self, plugin_id: str) -> bool:
        state = self._states.get(plugin_id)
        if not state or not state.enabled:
            return False
        targets = [plugin_id]
        targets.extend(self._collect_dependents(plugin_id))
        changed = False
        for target_id in targets:
            target_state = self._states.get(target_id)
            if not target_state or not target_state.enabled:
                continue
            source = target_state.path
            target = Path(str(source) + DISABLE_SUFFIX)
            source.rename(target)
            target_state.path = target
            target_state.enabled = False
            changed = True
        return changed

    def enable_plugin(self, plugin_id: str) -> bool:
        state = self._states.get(plugin_id)
        if not state or state.enabled:
            return False
        source = state.path
        if not source.name.endswith(DISABLE_SUFFIX):
            return False
        target = Path(str(source)[: -len(DISABLE_SUFFIX)])
        source.rename(target)
        state.path = target
        state.enabled = True
        return True

    def _discover_plugins(self) -> list[PluginSpec]:
        specs: list[PluginSpec] = []
        for entry in sorted(self.plugins_dir.iterdir(), key=lambda item: item.name.lower()):
            try:
                spec = self._build_spec(entry)
            except Exception as exc:  # pylint: disable=broad-except
                self.logger.warning("解析插件失败: %s (%s)", entry, exc)
                continue
            if spec:
                specs.append(spec)
        return specs

    def _build_spec(self, entry: Path) -> PluginSpec | None:
        enabled = not entry.name.endswith(DISABLE_SUFFIX)
        if entry.is_file():
            if not (entry.name.endswith(".py") or entry.name.endswith(".py" + DISABLE_SUFFIX)):
                return None
            meta = _read_meta_from_python(entry)
            return PluginSpec(path=entry, entry=entry, enabled=enabled, meta=meta)
        if entry.is_dir():
            meta, entry_file = _read_meta_from_dir(entry)
            return PluginSpec(path=entry, entry=entry / entry_file, enabled=enabled, meta=meta)
        return None

    def _load_in_dependency_order(self, specs: list[PluginSpec], context: PluginContext) -> None:
        remaining = {spec.meta.plugin_id: spec for spec in specs}
        loaded: set[str] = set()
        while remaining:
            progress = False
            for plugin_id, spec in list(remaining.items()):
                if not self._dependencies_satisfied(spec, loaded):
                    continue
                self._load_single(spec, context)
                loaded.add(plugin_id)
                remaining.pop(plugin_id, None)
                progress = True
            if progress:
                continue
            for spec in remaining.values():
                missing = [dep for dep in spec.meta.dependencies if dep not in loaded]
                self._mark_error(spec.meta.plugin_id, f"缺少依赖: {', '.join(missing)}")
            break

    def _dependencies_satisfied(self, spec: PluginSpec, loaded: set[str]) -> bool:
        return all(dep in loaded for dep in spec.meta.dependencies)

    def _collect_dependents(self, plugin_id: str) -> list[str]:
        dependents: list[str] = []
        seen = {plugin_id}
        queue = [plugin_id]
        while queue:
            current = queue.pop(0)
            for state in self._states.values():
                if state.meta.plugin_id in seen:
                    continue
                if current in state.meta.dependencies:
                    dependents.append(state.meta.plugin_id)
                    seen.add(state.meta.plugin_id)
                    queue.append(state.meta.plugin_id)
        return dependents

    def _load_single(self, spec: PluginSpec, context: PluginContext) -> None:
        if not spec.entry.exists():
            self._mark_error(spec.meta.plugin_id, "插件入口文件不存在")
            return
        module_name = _module_name_for(spec.meta, spec.path)
        try:
            module = _load_module(spec.entry, module_name)
            instance = _initialize_module(module, context)
        except Exception as exc:  # pylint: disable=broad-except
            self._mark_error(spec.meta.plugin_id, f"加载失败: {exc}")
            return
        self._loaded_plugins[spec.meta.plugin_id] = LoadedPlugin(
            spec=spec,
            module_name=module_name,
            module=module,
            instance=instance,
        )

    def _mark_error(self, plugin_id: str, message: str) -> None:
        state = self._states.get(plugin_id)
        if state:
            state.error = message
        self.logger.warning("插件 %s: %s", plugin_id, message)


_DEFAULT_MANAGER: PluginManager | None = None


def get_plugin_manager() -> PluginManager:
    global _DEFAULT_MANAGER  # pylint: disable=global-statement
    if _DEFAULT_MANAGER is None:
        _DEFAULT_MANAGER = PluginManager()
    return _DEFAULT_MANAGER


def _normalize_dependencies(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _read_meta_from_python(path: Path) -> PluginMeta:
    content = path.read_text(encoding="utf-8")
    tree = ast.parse(content, filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == PLUGIN_META_VAR:
                    raw = ast.literal_eval(node.value)
                    if not isinstance(raw, dict):
                        raise ValueError(f"{path} 的 {PLUGIN_META_VAR} 必须是字典")
                    return PluginMeta.from_mapping(raw, str(path))
    raise ValueError(f"{path} 未找到 {PLUGIN_META_VAR}")


def _read_meta_from_dir(path: Path) -> tuple[PluginMeta, str]:
    config = path / PLUGIN_JSON_NAME
    if config.exists():
        data = json.loads(config.read_text(encoding="utf-8"))
        meta_data = data.get("meta") or data
        entry = str(data.get("entry") or "__init__.py")
        return PluginMeta.from_mapping(meta_data, str(config)), entry
    entry = path / "__init__.py"
    if entry.exists():
        return _read_meta_from_python(entry), "__init__.py"
    raise ValueError(f"{path} 缺少 {PLUGIN_JSON_NAME} 或 __init__.py")


def _module_name_for(meta: PluginMeta, path: Path) -> str:
    digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:8]
    safe = "".join(ch if ch.isalnum() else "_" for ch in meta.plugin_id.lower())
    return f"camellia_plugin_{safe}_{digest}"


def _load_module(entry: Path, module_name: str) -> ModuleType:
    is_package = entry.name == "__init__.py"
    locations = [str(entry.parent)] if is_package else None
    spec = importlib.util.spec_from_file_location(module_name, entry, submodule_search_locations=locations)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载模块: {entry}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _initialize_module(module: ModuleType, context: PluginContext) -> Any | None:
    setup = getattr(module, "setup", None)
    if callable(setup):
        setup(context)
        return None
    instance = None
    if hasattr(module, "plugin"):
        instance = getattr(module, "plugin")
    elif hasattr(module, "Plugin"):
        plugin_cls = getattr(module, "Plugin")
        if isinstance(plugin_cls, type):
            instance = plugin_cls()
    elif hasattr(module, "get_plugin"):
        creator = getattr(module, "get_plugin")
        if callable(creator):
            instance = creator()
    if instance is None:
        return None
    if hasattr(instance, "on_initialize"):
        instance.on_initialize(context)
    elif hasattr(instance, "on_load"):
        instance.on_load(context)
    return instance
