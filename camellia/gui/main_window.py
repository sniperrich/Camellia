"""
Main Window

Main application window for Camellia.NEL GUI with Material 3 design.
"""

from __future__ import annotations

import json
import hashlib
import logging
import os
import time
import traceback
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..api import (
    WPFLauncherClient,
    login_with_password,
    login_with_netease_email,
    login_with_netease_phone,
    send_netease_sms,
)
from ..api.auth_backend import AuthBackend
from ..crypto.http_crypto import load_cookie_json
from ..mc import GameProfile, ModList, ProxyConfig, StandardYggdrasil, UserProfile, YggdrasilData, get_md5_pair
from ..models import AuthOtp, GameCharacter, GameSkin, NetGameDetail, NetGameItem, NetGameServerAddress
from ..plugins import PluginState, get_plugin_manager
from ..version import __version__
from .theme import build_stylesheet
from .widgets import Backdrop, CamelliaLogo, NavButton, SkinCard, make_nav_icon, apply_drop_shadow
from .dialogs import LoginErrorDialog
from .auth_gate import AuthGateDialog
from .auth_bypass import get_auth_bypass_status
from .storage import SavedAccount, load_accounts, save_accounts
from .workers import ProxyThread, Worker
from .pages import LoginPage, ServersPage, CharacterPage, ConnectionPage, SkinPage, PluginsPage, SettingsPage
from .settings import get_settings


def _format_address(host: str, port: int) -> str:
    if not host or not port:
        return "不可用"
    return f"{host}:{port}"


@dataclass
class SessionState:
    session_id: str = ""
    client: Optional[WPFLauncherClient] = None
    auth: Optional[AuthOtp] = None
    display_name: str = ""
    remark: str = ""
    server: Optional[NetGameItem] = None
    server_detail: Optional[NetGameDetail] = None
    server_address: Optional[NetGameServerAddress] = None
    character_name: Optional[str] = None
    game_started: bool = False

    def server_version(self) -> str:
        if self.server_detail and self.server_detail.mc_versions:
            return self.server_detail.mc_versions[0].name
        return ""

    def remote_address(self) -> Tuple[str, int]:
        host = ""
        port = 0
        if self.server_address:
            host = self.server_address.host or ""
            port = self.server_address.port or 0
        if self.server_detail:
            if not host:
                host = self.server_detail.server_address
            if not port:
                port = self.server_detail.server_port
        return host, port

    def display_label(self) -> str:
        if not self.auth:
            return "未登录"
        channel = self.auth.login_channel or "未知渠道"
        account = self.display_name or self.auth.account or self.auth.entity_id or "未知账号"
        base = f"{channel} / {account}"
        if self.remark:
            return f"{base} · {self.remark}"
        return base


@dataclass
class ManagedProxy:
    id: int
    user_id: str
    user_account: str
    user_token: str
    server_id: str
    server_name: str
    server_version: str
    local_host: str
    local_port: int
    forward_host: str
    forward_port: int
    nickname: str
    status: str
    started_at: float
    thread: ProxyThread

    def local_address(self) -> str:
        return _format_address(self.local_host, self.local_port)

    def forward_address(self) -> str:
        return _format_address(self.forward_host, self.forward_port)


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"Camellia NEL 启动器 v{__version__}")
        self.resize(1080, 640)
        self.setMinimumSize(1080, 640)
        self._logger = logging.getLogger("camellia.gui")

        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self._workers: list[Worker] = []
        self._saved_accounts: list[SavedAccount] = []
        self._sessions: dict[str, SessionState] = {}
        self._active_session_id: str | None = None
        self.session = SessionState()
        self.plugin_manager = get_plugin_manager()
        self.settings = get_settings()
        self._managed_proxies: list[ManagedProxy] = []
        self._next_proxy_id = 1
        self._server_offset = 0
        self._server_page_size = 15
        self._server_detail_request_id = 0
        self._skin_offset = 0
        self._skin_page_size = 20
        self._skin_query = ""
        self._skin_request_id = 0
        self._skin_image_cache: dict[str, QtGui.QPixmap] = {}
        self._skin_image_pending: dict[str, list[SkinCard]] = {}

        # Reuse auth backend session (cookies/headers) to reduce sporadic WAF 403.
        self._auth_backend: AuthBackend | None = None
        self._auth_backend_base_url: str = ""

        self._build_ui()

    def _get_auth_backend(self, base_url: str) -> AuthBackend:
        base_url = (base_url or "").strip().rstrip("/")
        if not base_url:
            base_url = "https://api.taylorswift.fit"
        if self._auth_backend is None or self._auth_backend_base_url != base_url:
            self._auth_backend = AuthBackend(base_url)
            self._auth_backend_base_url = base_url
        return self._auth_backend

    def _build_ui(self) -> None:
        backdrop = Backdrop()
        central_layout = QtWidgets.QHBoxLayout(backdrop)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)

        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(12, 14, 12, 14)
        sidebar_layout.setSpacing(10)
        sidebar.setFixedWidth(200)

        brand_row = QtWidgets.QHBoxLayout()
        logo = CamelliaLogo(size=34)
        title = QtWidgets.QLabel("Camellia NEL")
        title.setObjectName("AppTitle")
        brand_row.addWidget(logo)
        brand_row.addWidget(title)
        brand_row.addStretch(1)

        sidebar_layout.addLayout(brand_row)

        # Keep version visible without truncating the brand title.
        subtitle_row = QtWidgets.QHBoxLayout()
        subtitle = QtWidgets.QLabel("Camellia Engine")
        subtitle.setProperty("muted", "true")
        subtitle_row.addWidget(subtitle)
        subtitle_row.addStretch(1)
        version_badge = QtWidgets.QLabel(f"v{__version__}")
        version_badge.setProperty("status", "info")
        # Keep badge styled via theme rules (rounded corners, background), only tweak font-size.
        version_badge.setStyleSheet("font-size: 11px;")
        version_badge.setAlignment(QtCore.Qt.AlignCenter)
        version_badge.style().unpolish(version_badge)
        version_badge.style().polish(version_badge)
        subtitle_row.addWidget(version_badge)
        sidebar_layout.addLayout(subtitle_row)

        self.nav_buttons = {
            "login": NavButton("登录", make_nav_icon("login")),
            "servers": NavButton("服务器", make_nav_icon("servers")),
            "characters": NavButton("角色", make_nav_icon("characters")),
            "connection": NavButton("连接", make_nav_icon("connection")),
            "skins": NavButton("皮肤", make_nav_icon("skins")),
            "plugins": NavButton("插件", make_nav_icon("plugins")),
            "settings": NavButton("设置", make_nav_icon("settings")),
        }

        for key, button in self.nav_buttons.items():
            sidebar_layout.addWidget(button)
            button.clicked.connect(lambda checked=False, name=key: self.switch_page(name))

        sidebar_layout.addStretch(1)

        self.status_pill = QtWidgets.QFrame()
        self.status_pill.setProperty("card", "true")
        pill_layout = QtWidgets.QVBoxLayout(self.status_pill)
        pill_layout.setContentsMargins(10, 8, 10, 8)
        pill_layout.setSpacing(6)
        
        # 状态标题
        pill_title = QtWidgets.QLabel("当前状态")
        pill_title.setStyleSheet("font-weight: 600; font-size: 13px; color: #6750A4;")
        pill_layout.addWidget(pill_title)
        
        # 状态内容
        self.status_label = QtWidgets.QLabel("未登录")
        self.status_label.setWordWrap(True)
        self.status_label.setStyleSheet("font-size: 13px;")
        self.status_label.setProperty("muted", "true")
        pill_layout.addWidget(self.status_label)

        sidebar_layout.addWidget(self.status_pill)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.setObjectName("ContentStack")
        self.login_page = LoginPage()
        self.servers_page = ServersPage()
        self.characters_page = CharacterPage()
        self.connection_page = ConnectionPage()
        self.skins_page = SkinPage()
        self.plugins_page = PluginsPage()
        self.settings_page = SettingsPage()
        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.servers_page)
        self.stack.addWidget(self.characters_page)
        self.stack.addWidget(self.connection_page)
        self.stack.addWidget(self.skins_page)
        self.stack.addWidget(self.plugins_page)
        self.stack.addWidget(self.settings_page)

        central_layout.addWidget(sidebar)
        central_layout.addWidget(self.stack, 1)

        self.setCentralWidget(backdrop)
        self._apply_card_shadows()

        self.login_page.login_clicked.connect(lambda: self._handle_login())
        self.login_page.send_sms_clicked.connect(lambda: self._handle_send_sms())
        self.login_page.saved_login_clicked.connect(lambda: self._handle_saved_login())
        self.login_page.session_switch_clicked.connect(lambda: self._switch_session())
        self.login_page.session_logout_clicked.connect(lambda: self._logout_session())
        self.login_page.save_button.clicked.connect(lambda: self._save_current_account())
        self.login_page.remove_saved_button.clicked.connect(lambda: self._remove_saved_account())
        self.login_page.saved_list.itemSelectionChanged.connect(lambda: self._load_saved_account())
        self.servers_page.load_more_requested.connect(lambda: self._load_more_servers())
        self.servers_page.server_selected.connect(lambda server: self._handle_server_selected(server))
        self.servers_page.recent_server_requested.connect(lambda server_id: self._select_recent_server(server_id))
        self.servers_page.continue_requested.connect(lambda: self.switch_page("characters"))

        self.characters_page.refresh_requested.connect(lambda: self._load_characters())
        self.characters_page.create_requested.connect(lambda name: self._create_character(name))
        self.characters_page.continue_requested.connect(lambda name: self._start_game(name))

        self.skins_page.load_more_requested.connect(lambda: self._load_more_skins())
        self.skins_page.search_requested.connect(lambda text: self._search_skins(text))
        self.skins_page.apply_requested.connect(lambda skin: self._apply_skin(skin))
        self.skins_page.image_requested.connect(lambda skin, card: self._load_skin_image(skin, card))

        self.plugins_page.set_plugin_path(str(self.plugin_manager.plugins_dir))
        self.plugins_page.refresh_requested.connect(lambda: self._refresh_plugins(force=True))
        self.plugins_page.toggle_requested.connect(lambda plugin_id, enabled: self._toggle_plugin(plugin_id, enabled))
        self.plugins_page.open_dir_requested.connect(lambda: self._open_plugin_dir())

        self.connection_page.proxy_start_requested.connect(lambda host, port: self._start_proxy(host, port))
        self.connection_page.proxy_stop_requested.connect(lambda: self._stop_all_proxies())
        self.connection_page.proxy_close_requested.connect(lambda proxy_id: self._stop_proxy_by_id(proxy_id))
        self.connection_page.session_switch_requested.connect(lambda sid: self._set_active_session(sid))
        self.connection_page.proxy_stop_session_requested.connect(lambda sid: self._stop_proxies_for_session(sid))

        self.settings_page.theme_changed.connect(lambda theme: self._apply_theme(theme))
        self.settings_page.settings_saved.connect(lambda: self._on_settings_saved())

        self._set_active_session(None)
        self._refresh_nav_icons("login")
        self.switch_page("login")
        self._load_saved_accounts()

    def _dump_sauth(self, mode: str, sauth_json: str) -> None:
        """Optionally dump sauth_json for debugging purposes.

        sauth_json includes sensitive credentials (sessionid/token). By default we only log a
        masked preview; enable full dumping via `debug_mode` or `NEL_DUMP_SAUTH=1`.
        """
        if not sauth_json:
            return

        sha = hashlib.sha256(sauth_json.encode("utf-8", errors="replace")).hexdigest()
        allow_full = bool(self.settings.get("debug_mode", False)) or os.getenv("NEL_DUMP_SAUTH", "").lower() in {
            "1",
            "true",
            "yes",
        }

        # Always emit a stable fingerprint to help correlate server-side logs.
        self._logger.info("sauth fingerprint sha256=%s len=%s mode=%s", sha[:16], len(sauth_json), mode)

        try:
            obj = json.loads(sauth_json)
        except Exception:  # pylint: disable=broad-except
            obj = None

        if isinstance(obj, dict):
            masked = dict(obj)
            inner = masked.get("sauth_json")
            if isinstance(inner, str):
                try:
                    inner_obj = json.loads(inner)
                    if isinstance(inner_obj, dict):
                        masked["sauth_json"] = {
                            k: (
                                v[:3] + "***" + v[-3:]
                                if isinstance(v, str) and k in {"sessionid", "gas_token", "userToken", "token"} and v
                                else v
                            )
                            for k, v in inner_obj.items()
                        }
                except Exception:  # pylint: disable=broad-except
                    pass
            for key in ("sessionid", "gas_token", "userToken", "token"):
                if key in masked and isinstance(masked[key], str) and masked[key]:
                    masked[key] = masked[key][:3] + "***" + masked[key][-3:]
            # Masked preview is safe enough to show at INFO for debugging.
            self._logger.info(
                "sauth preview(masked, not reusable)=%s",
                json.dumps(masked, ensure_ascii=False),
            )

        if not allow_full:
            return

        try:
            dump_dir = Path.home() / ".camellia" / "dumps"
            dump_dir.mkdir(parents=True, exist_ok=True)
            ts = time.strftime("%Y%m%d-%H%M%S")
            path = dump_dir / f"sauth-{ts}-{mode}-{sha[:8]}.json"
            path.write_text(sauth_json, encoding="utf-8")
            self._logger.warning("sauth dumped to %s", str(path))
        except Exception as exc:  # pylint: disable=broad-except
            self._logger.warning("sauth dump failed: %s", exc)

    def switch_page(self, name: str) -> None:
        if name not in self.nav_buttons:
            return
        self.stack.setCurrentIndex(list(self.nav_buttons.keys()).index(name))
        for key, button in self.nav_buttons.items():
            button.setChecked(key == name)
        self._refresh_nav_icons(name)
        self._animate_page(self.stack.currentWidget())
        if name == "servers":
            if not self.session.client:
                self.servers_page.reset_state()
                self.servers_page.set_status("请先登录以加载服务器列表。")
                return
            self._refresh_recent_server_card()
            self._ensure_servers_loaded()
        elif name == "characters":
            if not self.session.client:
                self.characters_page.set_characters([])
                self.characters_page.set_status("请先登录。", error=True)
                return
            if not self.session.server:
                self.characters_page.set_characters([])
                self.characters_page.set_status("请先选择服务器。", error=True)
                return
            self._load_characters()
        elif name == "connection":
            self._refresh_connection_info()
            self._refresh_proxy_manager()
            if not self.session.client:
                self.connection_page.set_proxy_status("请先登录。", error=True)
                self.connection_page.set_actions_enabled(can_join=False, can_proxy=False)
                return
            can_join = self.session.server is not None
            can_proxy = bool(self.session.server and self.session.character_name)
            if not self.session.server:
                self.connection_page.set_proxy_status("请先选择服务器和角色。", error=True)
            elif not self.session.character_name:
                self.connection_page.set_proxy_status("请先选择角色。", error=True)
            else:
                self.connection_page.set_proxy_status("")
            self.connection_page.set_actions_enabled(can_join=can_join, can_proxy=can_proxy)
        elif name == "skins":
            if not self.session.client:
                self.skins_page.reset_state()
                self.skins_page.set_status("请先登录以查看皮肤。", error=True)
                return
            self._ensure_skins_loaded()
        elif name == "plugins":
            self._refresh_plugins()
        elif name == "settings":
            pass  # Settings page is always ready

    def _refresh_nav_icons(self, active: str | None) -> None:
        for key, button in self.nav_buttons.items():
            button.setIcon(make_nav_icon(key, active=(key == active)))

    def _apply_card_shadows(self) -> None:
        for frame in self.findChildren(QtWidgets.QFrame):
            if frame.property("card") == "true":
                apply_drop_shadow(frame, color=(0, 0, 0, 30), blur_radius=32, offset=(0, 12))

    def _animate_page(self, widget: QtWidgets.QWidget) -> None:
        # Disabled to avoid conflicts with LoadingOverlay
        pass

    def _set_nav_enabled(
        self,
        *,
        servers: bool,
        characters: bool,
        connection: bool,
        skins: bool,
        plugins: bool,
    ) -> None:
        self.nav_buttons["servers"].setEnabled(servers)
        self.nav_buttons["characters"].setEnabled(characters)
        self.nav_buttons["connection"].setEnabled(connection)
        self.nav_buttons["skins"].setEnabled(skins)
        self.nav_buttons["plugins"].setEnabled(plugins)
        self.nav_buttons["settings"].setEnabled(True)  # Settings always enabled

    def _run_task(self, func: callable, on_success: callable, on_error: callable) -> None:
        worker = Worker(func)
        self._workers.append(worker)

        def _cleanup() -> None:
            if worker in self._workers:
                self._workers.remove(worker)
            worker._callback_proxy = None
            worker._on_finished = None
            worker._on_error = None

        def _handle_finished(result: object) -> None:
            def _invoke() -> None:
                try:
                    on_success(result)
                except Exception as exc:  # pylint: disable=broad-except
                    self._logger.exception("Task on_success exception")
                    on_error(str(exc) or "登录失败。")
                finally:
                    _cleanup()
            QtCore.QTimer.singleShot(0, self, _invoke)

        def _handle_error(message: str) -> None:
            def _invoke() -> None:
                try:
                    on_error(message)
                finally:
                    _cleanup()
            QtCore.QTimer.singleShot(0, self, _invoke)

        # Keep references to avoid GC in Nuitka builds.
        worker._callback_proxy = self
        worker._on_finished = _handle_finished
        worker._on_error = _handle_error
        worker.signals.finished.connect(_handle_finished)
        worker.signals.error.connect(_handle_error)
        self.thread_pool.start(worker)

    def _login_status(self, message: str, *, error: bool = False) -> None:
        self.login_page.set_status(message, error=error)

    def _proxy_status(self, message: str, *, error: bool = False) -> None:
        self.connection_page.set_proxy_status(message, error=error)

    def _store_auth_tokens(self, username: str, access_token: str, refresh_token: str) -> None:
        self._logger.info(
            "Auth store tokens user=%s access_len=%s refresh_len=%s",
            username,
            len(access_token),
            len(refresh_token),
        )
        if username:
            self.settings.set("auth_user", username)
        if access_token:
            self.settings.set("auth_access_token", access_token)
        if refresh_token:
            self.settings.set("auth_refresh_token", refresh_token)

    def _open_auth_gate(self, status_cb: callable | None, proceed: callable) -> None:
        self._logger.info("Auth gate open")
        gate = AuthGateDialog(self)
        if gate.exec() == QtWidgets.QDialog.Accepted:
            self._logger.info("Auth gate accepted")
            if status_cb:
                status_cb("")
            proceed()
        else:
            self._logger.info("Auth gate rejected")
            if status_cb:
                status_cb("授权未完成，已取消。", error=True)

    def _ensure_auth_then(self, reason: str, proceed: callable, status_cb: callable | None = None) -> None:
        bypass_enabled, bypass_source = get_auth_bypass_status()
        if bypass_enabled:
            self._logger.warning("Auth bypass enabled for action=%s via %s", reason, bypass_source)
            if status_cb:
                status_cb("本地免授权模式已启用。")
            proceed()
            return

        access = self.settings.get("auth_access_token", "")
        refresh = self.settings.get("auth_refresh_token", "")
        base_url = self.settings.get("auth_base_url", "https://api.taylorswift.fit")
        device_id = self.settings.get("auth_device_id", "")
        self._logger.info("Auth ensure reason=%s access=%s refresh=%s", reason, bool(access), bool(refresh))

        if not access and not refresh:
            if status_cb:
                status_cb(f"需要授权才能{reason}。", error=True)
            self._open_auth_gate(status_cb, proceed)
            return

        if status_cb:
            status_cb("正在验证授权…")

        def task() -> tuple[str, dict]:
            backend = self._get_auth_backend(base_url)
            verify_resp: dict = {}
            if access:
                self._logger.info("Auth verify access token")
                verify_resp = backend.verify(access)
                if verify_resp.get("success"):
                    return "ok", verify_resp
            if refresh:
                self._logger.info("Auth refresh token")
                refresh_resp = backend.refresh(refresh, device_id)
                if refresh_resp.get("success"):
                    new_access = refresh_resp.get("access_token", "")
                    self._logger.info("Auth refresh ok access_len=%s", len(new_access))
                    verify_new = backend.verify(new_access) if new_access else {}
                    if verify_new.get("success"):
                        return "refresh_ok", {
                            "refresh": refresh_resp,
                            "verify": verify_new,
                        }
                return "refresh_fail", refresh_resp
            return "fail", verify_resp

        def on_success(result: tuple[str, dict]) -> None:
            kind, payload = result
            self._logger.info("Auth ensure result=%s", kind)
            if kind == "ok":
                if status_cb:
                    status_cb("")
                proceed()
                return
            if kind == "refresh_ok":
                refresh_resp = payload.get("refresh", {})
                verify_resp = payload.get("verify", {})
                self._store_auth_tokens(
                    verify_resp.get("user", "") if isinstance(verify_resp, dict) else "",
                    refresh_resp.get("access_token", ""),
                    refresh_resp.get("refresh_token", ""),
                )
                if status_cb:
                    status_cb("")
                proceed()
                return
            if status_cb:
                status_cb("授权已失效，请重新登录。", error=True)
            self._open_auth_gate(status_cb, proceed)

        def on_error(message: str) -> None:
            self._logger.warning("Auth ensure error=%s", message)
            if status_cb:
                status_cb(message or "授权验证失败，请重新登录。", error=True)
            self._open_auth_gate(status_cb, proceed)

        self._run_task(task, on_success, on_error)

    def _refresh_proxy_manager(self) -> None:
        self.connection_page.set_proxies(self._managed_proxies)
        self.connection_page.set_proxy_running(bool(self._managed_proxies))

    def _refresh_plugins(self, *, force: bool = False) -> None:
        extras = {"mode": "gui", "window": self, "app": QtWidgets.QApplication.instance()}
        if force:
            self.plugins_page.set_status("正在刷新插件...")
        def task() -> list[PluginState]:
            if force:
                return self.plugin_manager.load_plugins(extras=extras)
            self.plugin_manager.reload_if_changed(extras=extras)
            return self.plugin_manager.get_plugin_states()

        def on_success(states: list[PluginState]) -> None:
            self.plugins_page.set_status("")
            self.plugins_page.set_plugins(states)

        def on_error(message: str) -> None:
            self.plugins_page.set_status(message or "插件刷新失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _toggle_plugin(self, plugin_id: str, enabled: bool) -> None:
        action = "禁用" if enabled else "启用"
        self.plugins_page.set_status(f"正在{action}插件...")
        extras = {"mode": "gui", "window": self, "app": QtWidgets.QApplication.instance()}

        def task() -> tuple[bool, list[PluginState], str]:
            if enabled:
                changed = self.plugin_manager.disable_plugin(plugin_id)
            else:
                changed = self.plugin_manager.enable_plugin(plugin_id)
            if not changed:
                return False, self.plugin_manager.get_plugin_states(), "未找到插件或状态未变化。"
            states = self.plugin_manager.load_plugins(extras=extras)
            return True, states, ""

        def on_success(result: tuple[bool, list[PluginState], str]) -> None:
            ok, states, message = result
            self.plugins_page.set_plugins(states)
            if ok:
                self.plugins_page.set_status(f"{action}成功。")
            else:
                self.plugins_page.set_status(message or f"{action}失败。", error=True)

        def on_error(message: str) -> None:
            self.plugins_page.set_status(message or f"{action}失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _open_plugin_dir(self) -> None:
        path = str(self.plugin_manager.plugins_dir)
        ok = QtGui.QDesktopServices.openUrl(QtCore.QUrl.fromLocalFile(path))
        if not ok:
            self.plugins_page.set_status("无法打开插件目录。", error=True)

    def _remove_proxy(self, proxy: ManagedProxy) -> None:
        if proxy in self._managed_proxies:
            self._managed_proxies.remove(proxy)
        self._refresh_proxy_manager()

    def _stop_proxy_thread(self, proxy: ManagedProxy) -> None:
        proxy.status = "正在停止"
        self._refresh_proxy_manager()
        if proxy.thread.isRunning():
            proxy.thread.stop()
        else:
            self._remove_proxy(proxy)

    def _stop_proxy_by_id(self, proxy_id: int) -> None:
        for proxy in list(self._managed_proxies):
            if proxy.id == proxy_id:
                self.connection_page.set_proxy_manager_status(f"正在停止代理 #{proxy_id}...")
                self._stop_proxy_thread(proxy)
                return
        self.connection_page.set_proxy_manager_status("未找到要关闭的代理。", error=True)

    def _stop_all_proxies(self) -> None:
        if not self._managed_proxies:
            self.connection_page.set_proxy_status("代理未运行。")
            self.connection_page.set_proxy_manager_status("当前没有运行的代理。")
            return
        self.connection_page.set_proxy_status("正在停止全部代理...")
        self.connection_page.set_proxy_manager_status("正在停止全部代理...")
        for proxy in list(self._managed_proxies):
            self._stop_proxy_thread(proxy)

    def _stop_proxies_for_session(self, session_id: str) -> None:
        session = self._sessions.get(session_id)
        if not session or not session.auth:
            self.connection_page.set_proxy_manager_status("请选择有效账号。", error=True)
            return
        user_id = session.auth.entity_id
        stopped = False
        for proxy in list(self._managed_proxies):
            if proxy.user_id == user_id:
                stopped = True
                self._stop_proxy_thread(proxy)
        if stopped:
            label = session.display_label()
            self.connection_page.set_proxy_manager_status(f"已请求关闭 {label} 的代理。")
        else:
            self.connection_page.set_proxy_manager_status("该账号暂无运行中的代理。")

    def _sync_running_proxy_tokens(self, user_id: str, user_token: str) -> None:
        """
        If a user re-logs in, we may get a new token. Existing proxies should not be forced
        to restart; we hot-update their config so new client connections can authenticate.
        """
        user_id = (user_id or "").strip()
        user_token = (user_token or "").strip()
        if not user_id or not user_token:
            return
        for proxy in list(self._managed_proxies):
            if proxy.user_id != user_id:
                continue
            if proxy.user_token == user_token:
                continue
            proxy.user_token = user_token
            try:
                proxy.thread.update_user_token(user_token)
            except Exception as exc:  # pylint: disable=broad-except
                self._logger.debug("Proxy token hot-update failed id=%s: %s", proxy.id, exc)

    def _cleanup_duplicate_proxies(self, user_id: str, server_id: str, nickname: str, user_token: str) -> None:
        # Do not stop all proxies just because the token changed (re-login).
        # Instead, hot-update tokens for the same user so existing proxies keep working.
        self._sync_running_proxy_tokens(user_id, user_token)
        for proxy in list(self._managed_proxies):
            same_user = proxy.user_id == user_id
            same_server = proxy.server_id == server_id
            same_nickname = proxy.nickname == nickname
            if same_user and same_server and same_nickname:
                self._stop_proxy_thread(proxy)

    def _find_proxy_by_thread(self, thread: QtCore.QThread) -> ManagedProxy | None:
        for proxy in self._managed_proxies:
            if proxy.thread is thread:
                return proxy
        return None

    def _on_proxy_started(self, address: str, thread: ProxyThread | None = None) -> None:
        thread = thread or self.sender()
        if not isinstance(thread, ProxyThread):
            return
        proxy = self._find_proxy_by_thread(thread)
        if not proxy:
            return
        self._logger.info("Proxy started id=%s address=%s", proxy.id, address)
        proxy.status = "运行中"
        self.connection_page.set_proxy_status(
            f"代理已启动：{address} -> {proxy.forward_host}:{proxy.forward_port}"
        )
        self._refresh_proxy_manager()

    def _on_proxy_error(self, message: str, thread: ProxyThread | None = None) -> None:
        thread = thread or self.sender()
        if not isinstance(thread, ProxyThread):
            return
        proxy = self._find_proxy_by_thread(thread)
        if not proxy:
            return
        self._logger.warning("Proxy error id=%s message=%s", proxy.id, message)
        proxy.status = "启动失败"
        self.connection_page.set_proxy_status(message or "代理启动失败。", error=True)
        self.connection_page.set_proxy_manager_status(f"代理 #{proxy.id} 启动失败。", error=True)
        self._refresh_proxy_manager()

    def _on_proxy_stopped(self, thread: ProxyThread | None = None) -> None:
        thread = thread or self.sender()
        if not isinstance(thread, ProxyThread):
            return
        proxy = self._find_proxy_by_thread(thread)
        if not proxy:
            return
        self._logger.info("Proxy stopped id=%s", proxy.id)
        self._remove_proxy(proxy)
        if not self._managed_proxies:
            self.connection_page.set_proxy_status("代理已停止。")
        self.connection_page.set_proxy_manager_status(f"代理 #{proxy.id} 已停止。")

    def _load_saved_accounts(self, selected_id: str | None = None) -> None:
        accounts = [acc for acc in load_accounts() if acc.mode != "cookie"]
        self._saved_accounts = sorted(accounts, key=lambda acc: acc.last_used, reverse=True)
        self.login_page.set_saved_accounts(self._saved_accounts, selected_id=selected_id)
        self._refresh_sessions()

    def _refresh_sessions(self) -> None:
        sessions = list(self._sessions.values())
        self.login_page.set_sessions(sessions, selected_id=self._active_session_id)
        self.connection_page.set_sessions(sessions, selected_id=self._active_session_id)

    def _set_active_session(self, session_id: str | None) -> None:
        if not session_id or session_id not in self._sessions:
            self._active_session_id = None
            self.session = SessionState()
            self.status_label.setText("未登录")
            self._set_nav_enabled(servers=False, characters=False, connection=False, skins=False, plugins=True)
            self.servers_page.reset_state()
            self.characters_page.set_characters([])
            self.skins_page.reset_state()
            self._refresh_connection_info()
            self._refresh_sessions()
            return

        self._active_session_id = session_id
        self.session = self._sessions[session_id]
        self.status_label.setText(f"已登录：{self.session.display_label()}")
        # Re-login may create a new session/token for the same user id. Keep existing proxies alive.
        if self.session.auth:
            self._sync_running_proxy_tokens(self.session.auth.entity_id, self.session.auth.token)
        self._set_nav_enabled(servers=True, characters=True, connection=True, skins=True, plugins=True)
        # 清理当前页面缓存，避免混淆不同账号的数据
        self._server_offset = 0
        self._server_detail_request_id = 0
        self.servers_page.reset_state()
        self._skin_offset = 0
        self._skin_query = ""
        self._skin_request_id = 0
        self._skin_image_pending.clear()
        self.skins_page.reset_state()
        self._refresh_connection_info()
        self._refresh_proxy_manager()
        self._refresh_sessions()

    def _switch_session(self) -> None:
        session_id = self.login_page.selected_session_id()
        if not session_id:
            self.login_page.set_status("请选择一个已登录账号。", error=True)
            return
        self._set_active_session(session_id)
        self.login_page.set_status("已切换账号。")

    def _logout_session(self) -> None:
        session_id = self.login_page.selected_session_id()
        if not session_id:
            self.login_page.set_status("请选择一个已登录账号。", error=True)
            return
        if session_id in self._sessions:
            del self._sessions[session_id]
        if self._active_session_id == session_id:
            next_id = next(iter(self._sessions.keys()), None)
            self._set_active_session(next_id)
        else:
            self._refresh_sessions()
        self.login_page.set_status("已退出账号。")

    def _current_saved_account(self) -> SavedAccount | None:
        selected_id = self.login_page.selected_saved_id()
        if not selected_id:
            return None
        for account in self._saved_accounts:
            if account.id == selected_id:
                return account
        return None

    @staticmethod
    def _parse_sauth_text(sauth_text: str) -> tuple[dict, str, str]:
        text = (sauth_text or "").strip()
        if not text:
            raise ValueError("请输入 sauth_json")
        try:
            normalized = load_cookie_json(text)
        except ValueError as exc:
            raise ValueError(f"sauth_json 格式错误：{exc}") from exc
        normalized = (normalized or "").strip()
        try:
            payload = json.loads(normalized)
        except json.JSONDecodeError as exc:
            raise ValueError(f"sauth_json 不是有效 JSON：{exc.msg}") from exc
        if not isinstance(payload, dict):
            raise ValueError("sauth_json 必须是 JSON 对象")
        session_id = str(payload.get("sessionid") or payload.get("sessionId") or "").strip()
        if not session_id:
            raise ValueError("sauth_json 缺少 sessionid")
        if "*" in session_id:
            raise ValueError("sauth_json 的 sessionid 看起来被打码了，请粘贴原始未打码内容")
        payload["sessionid"] = session_id
        inner_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        wrapped_json = json.dumps({"sauth_json": inner_json}, ensure_ascii=False, separators=(",", ":"))
        return payload, inner_json, wrapped_json

    @staticmethod
    def _build_sauth_account_name(payload: dict, raw: str) -> str:
        sdkuid = str(payload.get("sdkuid") or "").strip()
        if sdkuid:
            return f"sdkuid:{sdkuid}"
        digest = hashlib.sha1((raw or "").encode("utf-8")).hexdigest()[:10]
        return f"sauth:{digest}"

    def _load_saved_account(self) -> None:
        account = self._current_saved_account()
        if not account:
            return
        if account.mode == "account":
            self.login_page.select_account_type(0)
            self.login_page.account_user.setText(account.username)
            self.login_page.account_pass.setText(account.password)
        elif account.mode == "netease_email":
            self.login_page.select_account_type(1)
            self.login_page.netease_email.setText(account.username)
            self.login_page.netease_pass.setText(account.password)
        elif account.mode == "netease_phone":
            self.login_page.select_account_type(2)
            self.login_page.netease_phone.setText(account.username)
            phone_sub_mode = getattr(account, "sub_mode", "") or ("password" if getattr(account, "password", "") else "sms")
            if phone_sub_mode == "password":
                self.login_page.set_netease_phone_login_mode("password")
                self.login_page.netease_phone_pass.setText(account.password)
                self.login_page.netease_code.setText("")
            else:
                self.login_page.set_netease_phone_login_mode("sms")
                self.login_page.netease_phone_pass.setText("")
                self.login_page.netease_code.setText("")
        elif account.mode == "sauth":
            self.login_page.select_account_type(3)
            text = (getattr(account, "sauth_json", "") or account.password or "").strip()
            if hasattr(self.login_page, "sauth_input"):
                self.login_page.sauth_input.setPlainText(text)
        # Keep remark in sync for all modes.
        self.login_page.remark_input.setText(getattr(account, "remark", "") or "")

    def _handle_saved_login(self) -> None:
        account = self._current_saved_account()
        if not account:
            self.login_page.set_status("请选择一个已保存账号。", error=True)
            return
        self._load_saved_account()
        def proceed() -> None:
            self._handle_saved_login_impl(account)

        self._ensure_auth_then(
            reason="登录已保存账号",
            proceed=proceed,
            status_cb=self._login_status,
        )

    def _update_saved_account_sauth(self, account_id: str, wrapped_sauth: str) -> None:
        if not account_id or not wrapped_sauth:
            return
        for account in self._saved_accounts:
            if account.id != account_id:
                continue
            account.sauth_json = wrapped_sauth
            account.last_used = time.time()
            save_accounts(self._saved_accounts)
            self._load_saved_accounts(selected_id=account.id)
            return

    def _handle_saved_login_impl(self, account: SavedAccount) -> None:
        self.login_page.clear_status()
        self.login_page.set_busy(True)

        mode = account.mode
        display_name = account.username or "已保存账号"
        remark = account.remark
        account_id = account.id

        def task() -> tuple[WPFLauncherClient, AuthOtp, str, str]:
            errors: list[str] = []
            raw_saved_sauth = (getattr(account, "sauth_json", "") or "").strip()
            if raw_saved_sauth:
                try:
                    _, _, wrapped_sauth = self._parse_sauth_text(raw_saved_sauth)
                    client = WPFLauncherClient()
                    self._dump_sauth(f"{mode}:saved_sauth", wrapped_sauth)
                    auth = client.login_with_cookie(wrapped_sauth)
                    return client, auth, wrapped_sauth, "sauth"
                except Exception as exc:  # pylint: disable=broad-except
                    errors.append(f"SAuth 登录失败：{exc}")

            if mode == "account":
                username = (account.username or "").strip()
                password = (account.password or "").strip()
                if not username or not password:
                    base = "已保存账号缺少用户名或密码，无法回退账号密码登录。"
                    if errors:
                        base = f"{errors[-1]}；{base}"
                    raise ValueError(base)
                client = WPFLauncherClient()
                sauth_json = login_with_password(username, password)
                _, _, wrapped_sauth = self._parse_sauth_text(sauth_json)
                self._dump_sauth(f"{mode}:fallback_password", wrapped_sauth)
                auth = client.login_with_cookie(wrapped_sauth)
                return client, auth, wrapped_sauth, "fallback_password"

            if mode == "netease_email":
                email = (account.username or "").strip()
                password = (account.password or "").strip()
                if not email or not password:
                    base = "已保存账号缺少邮箱或密码，无法回退邮箱密码登录。"
                    if errors:
                        base = f"{errors[-1]}；{base}"
                    raise ValueError(base)
                client = WPFLauncherClient()
                sauth_json = login_with_netease_email(email, password)
                _, _, wrapped_sauth = self._parse_sauth_text(sauth_json)
                self._dump_sauth(f"{mode}:fallback_password", wrapped_sauth)
                auth = client.login_with_cookie(wrapped_sauth)
                return client, auth, wrapped_sauth, "fallback_password"

            if mode == "netease_phone":
                phone = (account.username or "").strip()
                sub_mode = (getattr(account, "sub_mode", "") or "").strip().lower()
                password = (account.password or "").strip()
                if phone and sub_mode == "password" and password:
                    client = WPFLauncherClient()
                    email = f"{phone}@163.com"
                    sauth_json = login_with_netease_email(email, password)
                    _, _, wrapped_sauth = self._parse_sauth_text(sauth_json)
                    self._dump_sauth(f"{mode}:fallback_password", wrapped_sauth)
                    auth = client.login_with_cookie(wrapped_sauth)
                    return client, auth, wrapped_sauth, "fallback_password"
                base = "手机号账号未保存密码，且 SAuth 已失效，无法自动回退。请手动验证码登录。"
                if errors:
                    base = f"{errors[-1]}；{base}"
                raise ValueError(base)

            if errors:
                raise ValueError(errors[-1])
            raise ValueError("已保存账号缺少可用登录信息。")

        def on_success(result: tuple[WPFLauncherClient, AuthOtp, str, str]) -> None:
            client, auth, wrapped_sauth, source = result
            session_id = uuid.uuid4().hex
            self._sessions[session_id] = SessionState(
                session_id=session_id,
                client=client,
                auth=auth,
                display_name=display_name or auth.account or auth.entity_id,
                remark=remark,
            )
            self._set_active_session(session_id)
            self._logger.debug("saved login source=%s user_token=%s", source, auth.token[:4] + "..." if auth.token else "")
            self._server_offset = 0
            self.servers_page.reset_state()
            self._skin_offset = 0
            self._skin_query = ""
            self._skin_request_id = 0
            self._skin_image_pending.clear()
            self.skins_page.reset_state()
            self.skins_page.search_input.clear()
            self._update_saved_account_sauth(account_id, wrapped_sauth)
            self.login_page.set_busy(False)
            if source == "sauth":
                self.login_page.set_status("登录成功（已使用保存的 SAuth）。")
            else:
                self.login_page.set_status("登录成功（SAuth 失效，已自动回退账号密码）。")
            self.switch_page("servers")

        def on_error(message: str) -> None:
            self.login_page.set_busy(False)
            if self._show_login_error(message):
                self.login_page.set_status("登录失败，请查看详细提示。", error=True)
            else:
                self.login_page.set_status(message or "登录失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _save_current_account(self) -> None:
        mode = self.login_page.login_mode()
        remember = self._should_remember_password()
        remark = self.login_page.remark_input.text().strip()
        if mode == "account":
            username = self.login_page.account_user.text().strip()
            if not username:
                self.login_page.set_status("请输入用户名。", error=True)
                return
            password = self.login_page.account_pass.text().strip()
            account = SavedAccount.new_account(username, password, remember, remark=remark)
        elif mode == "netease_email":
            email = self.login_page.netease_email.text().strip()
            if not email:
                self.login_page.set_status("请输入网易邮箱。", error=True)
                return
            password = self.login_page.netease_pass.text().strip()
            account = SavedAccount.new_netease_email(email, password, remember, remark=remark)
        elif mode == "netease_phone":
            phone = self.login_page.netease_phone.text().strip()
            if not phone:
                self.login_page.set_status("请输入手机号。", error=True)
                return
            phone_mode = self.login_page.netease_phone_login_mode()
            if phone_mode == "password":
                password = self.login_page.netease_phone_pass.text().strip()
                if not password:
                    self.login_page.set_status("请输入密码。", error=True)
                    return
                account = SavedAccount.new_netease_phone(
                    phone, login_mode="password", password=password, remember=remember, remark=remark
                )
            else:
                account = SavedAccount.new_netease_phone(phone, login_mode="sms", remark=remark)
        else:
            raw_sauth = self.login_page.sauth_input.toPlainText().strip() if hasattr(self.login_page, "sauth_input") else ""
            try:
                payload, inner_sauth, wrapped_sauth = self._parse_sauth_text(raw_sauth)
            except ValueError as exc:
                self.login_page.set_status(str(exc), error=True)
                return
            username = self._build_sauth_account_name(payload, inner_sauth)
            account = SavedAccount.new_sauth(wrapped_sauth, remember=True, remark=remark, username=username)

        for idx, existing in enumerate(self._saved_accounts):
            if existing.mode == account.mode and existing.key == account.key:
                account.id = existing.id
                if not getattr(account, "sauth_json", "") and getattr(existing, "sauth_json", ""):
                    account.sauth_json = existing.sauth_json
                self._saved_accounts[idx] = account
                break
        else:
            self._saved_accounts.append(account)
        save_accounts(self._saved_accounts)
        self.login_page.set_status("已保存账号。")
        self._load_saved_accounts(selected_id=account.id)

    def _remove_saved_account(self) -> None:
        account = self._current_saved_account()
        if not account:
            return
        self._saved_accounts = [item for item in self._saved_accounts if item.id != account.id]
        save_accounts(self._saved_accounts)
        self._load_saved_accounts()
        self.login_page.set_status("已删除账号。")

    def _auto_save_account(self, mode: str, wrapped_sauth: str = "") -> None:
        remember = self._should_remember_password()
        remark = self.login_page.remark_input.text().strip()
        if mode == "account":
            username = self.login_page.account_user.text().strip()
            if not username:
                return
            password = self.login_page.account_pass.text().strip() if remember else ""
            existing = next((item for item in self._saved_accounts if item.mode == "account" and item.key == username), None)
            if existing:
                existing.username = username
                existing.last_used = time.time()
                if remark:
                    existing.remark = remark
                if remember:
                    existing.password = password
                    existing.remember_password = True
                else:
                    existing.password = ""
                    existing.remember_password = False
                if wrapped_sauth:
                    existing.sauth_json = wrapped_sauth
                save_accounts(self._saved_accounts)
                self._load_saved_accounts(selected_id=existing.id)
                return
            account = SavedAccount.new_account(username, password, remember, remark=remark)
            if wrapped_sauth:
                account.sauth_json = wrapped_sauth
        elif mode == "netease_email":
            email = self.login_page.netease_email.text().strip()
            if not email:
                return
            password = self.login_page.netease_pass.text().strip() if remember else ""
            existing = next((item for item in self._saved_accounts if item.mode == "netease_email" and item.key == email), None)
            if existing:
                existing.username = email
                existing.last_used = time.time()
                if remark:
                    existing.remark = remark
                if remember:
                    existing.password = password
                    existing.remember_password = True
                else:
                    existing.password = ""
                    existing.remember_password = False
                if wrapped_sauth:
                    existing.sauth_json = wrapped_sauth
                save_accounts(self._saved_accounts)
                self._load_saved_accounts(selected_id=existing.id)
                return
            account = SavedAccount.new_netease_email(email, password, remember, remark=remark)
            if wrapped_sauth:
                account.sauth_json = wrapped_sauth
        elif mode == "netease_phone":
            phone = self.login_page.netease_phone.text().strip()
            if not phone:
                return
            phone_mode = self.login_page.netease_phone_login_mode()
            password = self.login_page.netease_phone_pass.text().strip() if (remember and phone_mode == "password") else ""
            existing = next((item for item in self._saved_accounts if item.mode == "netease_phone" and item.key == phone), None)
            if existing:
                existing.username = phone
                existing.last_used = time.time()
                if remark:
                    existing.remark = remark
                if phone_mode == "password":
                    existing.sub_mode = "password"
                    if remember and password:
                        existing.password = password
                        existing.remember_password = True
                    else:
                        existing.password = ""
                        existing.remember_password = False
                else:
                    existing.sub_mode = "sms"
                if wrapped_sauth:
                    existing.sauth_json = wrapped_sauth
                save_accounts(self._saved_accounts)
                self._load_saved_accounts(selected_id=existing.id)
                return
            if phone_mode == "password":
                account = SavedAccount.new_netease_phone(
                    phone, login_mode="password", password=password, remember=remember, remark=remark
                )
            else:
                account = SavedAccount.new_netease_phone(phone, login_mode="sms", remark=remark)
            if wrapped_sauth:
                account.sauth_json = wrapped_sauth
        else:
            raw_sauth = self.login_page.sauth_input.toPlainText().strip() if hasattr(self.login_page, "sauth_input") else ""
            if not raw_sauth:
                return
            if not remember:
                return
            try:
                payload, inner_sauth, wrapped_sauth = self._parse_sauth_text(raw_sauth)
            except ValueError:
                return
            username = self._build_sauth_account_name(payload, inner_sauth)
            sauth_payload = wrapped_sauth if remember else ""
            existing = next((item for item in self._saved_accounts if item.mode == "sauth" and item.key == username), None)
            if existing:
                existing.username = username
                existing.last_used = time.time()
                if remark:
                    existing.remark = remark
                existing.sauth_json = sauth_payload
                existing.password = sauth_payload
                existing.remember_password = bool(remember and sauth_payload)
                save_accounts(self._saved_accounts)
                self._load_saved_accounts(selected_id=existing.id)
                return
            account = SavedAccount.new_sauth(sauth_payload, remember=bool(remember and sauth_payload), remark=remark, username=username)

        self._saved_accounts.append(account)
        save_accounts(self._saved_accounts)
        self._load_saved_accounts(selected_id=account.id)

    def _should_remember_password(self) -> bool:
        return bool(self.settings.get("remember_password", True))

    def _handle_login(self) -> None:
        def proceed() -> None:
            self._handle_login_impl()

        self._ensure_auth_then(
            reason="登录账号",
            proceed=proceed,
            status_cb=self._login_status,
        )

    def _handle_login_impl(self) -> None:
        self.login_page.clear_status()
        self.login_page.set_busy(True)

        mode = self.login_page.login_mode()
        display_name = ""
        remark = self.login_page.remark_input.text().strip()
        if mode == "account":
            username = self.login_page.account_user.text().strip()
            password = self.login_page.account_pass.text().strip()
            display_name = username

            def task() -> tuple[WPFLauncherClient, AuthOtp, str]:
                if not username or not password:
                    raise ValueError("请输入用户名和密码")
                client = WPFLauncherClient()
                sauth_json = login_with_password(username, password)
                _, _, wrapped_sauth = self._parse_sauth_text(sauth_json)
                self._dump_sauth(mode, wrapped_sauth)
                auth = client.login_with_cookie(wrapped_sauth)
                return client, auth, wrapped_sauth

        elif mode == "netease_email":
            email = self.login_page.netease_email.text().strip()
            password = self.login_page.netease_pass.text().strip()
            display_name = email

            def task() -> tuple[WPFLauncherClient, AuthOtp, str]:
                if not email or not password:
                    raise ValueError("请输入邮箱和密码")
                client = WPFLauncherClient()
                sauth_json = login_with_netease_email(email, password)
                _, _, wrapped_sauth = self._parse_sauth_text(sauth_json)
                self._dump_sauth(mode, wrapped_sauth)
                auth = client.login_with_cookie(wrapped_sauth)
                return client, auth, wrapped_sauth

        elif mode == "netease_phone":
            phone = self.login_page.netease_phone.text().strip()
            display_name = phone
            phone_mode = self.login_page.netease_phone_login_mode()

            def task() -> tuple[WPFLauncherClient, AuthOtp, str]:
                if not phone:
                    raise ValueError("请输入手机号")
                client = WPFLauncherClient()
                if phone_mode == "password":
                    password = self.login_page.netease_phone_pass.text().strip()
                    if not password:
                        raise ValueError("请输入密码")
                    email = f"{phone}@163.com"
                    sauth_json = login_with_netease_email(email, password)
                    _, _, wrapped_sauth = self._parse_sauth_text(sauth_json)
                    self._dump_sauth(f"{mode}:{phone_mode}", wrapped_sauth)
                    auth = client.login_with_cookie(wrapped_sauth)
                    return client, auth, wrapped_sauth
                code = self.login_page.netease_code.text().strip()
                if not code:
                    raise ValueError("请输入验证码")
                sauth_json = login_with_netease_phone(phone, code)
                _, _, wrapped_sauth = self._parse_sauth_text(sauth_json)
                self._dump_sauth(f"{mode}:{phone_mode}", wrapped_sauth)
                auth = client.login_with_cookie(wrapped_sauth)
                return client, auth, wrapped_sauth
        else:
            raw_sauth = self.login_page.sauth_input.toPlainText().strip() if hasattr(self.login_page, "sauth_input") else ""
            display_name = ""

            def task() -> tuple[WPFLauncherClient, AuthOtp, str]:
                _, _, wrapped_sauth = self._parse_sauth_text(raw_sauth)
                client = WPFLauncherClient()
                self._dump_sauth(mode, wrapped_sauth)
                auth = client.login_with_cookie(wrapped_sauth)
                return client, auth, wrapped_sauth

        def on_success(result: tuple[WPFLauncherClient, AuthOtp, str]) -> None:
            client, auth, wrapped_sauth = result
            session_id = uuid.uuid4().hex
            self._sessions[session_id] = SessionState(
                session_id=session_id,
                client=client,
                auth=auth,
                display_name=display_name or auth.account or auth.entity_id,
                remark=remark,
            )
            self._set_active_session(session_id)
            token_preview = auth.token[:4] + "..." if auth.token else ""
            self._logger.debug("user_token=%s", token_preview)
            self._server_offset = 0
            self.servers_page.reset_state()
            self._skin_offset = 0
            self._skin_query = ""
            self._skin_request_id = 0
            self._skin_image_pending.clear()
            self.skins_page.reset_state()
            self.skins_page.search_input.clear()
            self.login_page.set_busy(False)
            self.login_page.set_status("登录成功。")
            self._auto_save_account(mode, wrapped_sauth=wrapped_sauth)
            self.switch_page("servers")

        def on_error(message: str) -> None:
            self.login_page.set_busy(False)
            if self._show_login_error(message):
                self.login_page.set_status("登录失败，请查看详细提示。", error=True)
            else:
                self.login_page.set_status(message or "登录失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _handle_send_sms(self) -> None:
        def proceed() -> None:
            phone = self.login_page.netease_phone.text().strip()
            if not phone:
                self.login_page.set_status("请输入手机号。", error=True)
                return
            self.login_page.set_busy(True)

            def task() -> bool:
                return send_netease_sms(phone)

            def on_success(ok: bool) -> None:
                self.login_page.set_busy(False)
                if ok:
                    self.login_page.set_status("验证码已发送，请查收短信。")
                else:
                    self.login_page.set_status("验证码发送失败。", error=True)

            def on_error(message: str) -> None:
                self.login_page.set_busy(False)
                if self._show_login_error(message, title="发送验证码失败"):
                    self.login_page.set_status("验证码发送失败，请查看详细提示。", error=True)
                else:
                    self.login_page.set_status(message or "验证码发送失败。", error=True)

            self._run_task(task, on_success, on_error)

        self._ensure_auth_then(
            reason="发送验证码",
            proceed=proceed,
            status_cb=self._login_status,
        )

    def _show_login_error(self, message: str | None, *, title: str = "网易账号登录失败") -> bool:
        if not message:
            return False
        normalized = message.lower()
        net_map = [
            ("connection reset by peer", "网络连接被服务器断开，可能是网络波动或临时风控，请稍后重试。"),
            ("winerror 10054", "网络连接被对端强制断开（10054）。常见原因是系统代理/VPN/拦截导致 SSL 或连接被重置，请先关闭系统代理/VPN 后重试。"),
            ("errno 10054", "网络连接被对端强制断开（10054）。常见原因是系统代理/VPN/拦截导致 SSL 或连接被重置，请先关闭系统代理/VPN 后重试。"),
            ("winerror 10049", "当前网络地址不可用（10049）。常见原因是系统代理/VPN 配置指向了不可用地址或网络环境异常，请先关闭系统代理/VPN 后重试。"),
            ("errno 10049", "当前网络地址不可用（10049）。常见原因是系统代理/VPN 配置指向了不可用地址或网络环境异常，请先关闭系统代理/VPN 后重试。"),
            ("timed out", "网络请求超时，请检查网络或稍后重试。"),
            ("name or service not known", "域名解析失败，请检查网络或 DNS 设置。"),
            ("temporary failure in name resolution", "DNS 解析失败，请检查网络或稍后重试。"),
            ("connection refused", "目标服务器拒绝连接，请稍后重试。"),
            ("ssl: certificate_verify_failed", "SSL 证书校验失败，请检查系统时间或网络环境。"),
            ("http error 400", "请求被拒绝（400）。可能是账号需要安全验证或参数被服务器拒绝。"),
            ("http error 403", "请求被拒绝（403）。可能触发风控或权限不足。"),
            ("http error 429", "请求过于频繁（429），请稍后再试。"),
            ("http error 500", "服务器内部错误（500），请稍后重试。"),
            ("http error 502", "服务器网关错误（502），请稍后重试。"),
            ("http error 503", "服务器暂时不可用（503），请稍后重试。"),
            ("http error 504", "服务器网关超时（504），请稍后重试。"),
        ]
        for key, friendly in net_map:
            if key in normalized:
                dialog = LoginErrorDialog(
                    title=title,
                    reason=friendly,
                    parent=self,
                )
                dialog.exec()
                return True
        start = message.find("{")
        end = message.rfind("}")
        if start == -1 or end <= start:
            return False
        payload_text = message[start:end + 1]
        try:
            payload = json.loads(payload_text)
        except json.JSONDecodeError:
            return False

        reason = payload.get("reason") or payload.get("message") or ""
        verify_url = payload.get("verify_url") or payload.get("verifyUrl") or ""
        code = payload.get("code")
        details = payload.get("raw") if isinstance(payload, dict) else None
        if isinstance(details, str) and not details.strip():
            details = None
        if not reason and not verify_url and code is None:
            return False

        dialog_title = title
        final_reason = reason or "登录失败，请稍后再试。"
        # Provide extra guidance for MPay security verification flows.
        if (code == 1351 or verify_url) and "安全验证" not in final_reason:
            final_reason = final_reason.rstrip("。") + "。"
        if code == 1351 or verify_url:
            final_reason += "\n请在浏览器打开/复制下方链接完成安全验证后，再回到 Camellia 重新登录。"

        dialog = LoginErrorDialog(
            title=dialog_title,
            reason=final_reason,
            code=code if isinstance(code, int) else None,
            verify_url=verify_url or None,
            details=details if isinstance(details, str) else None,
            parent=self,
        )
        dialog.exec()
        return True

        self._run_task(task, on_success, on_error)

    def _ensure_servers_loaded(self) -> None:
        if self.servers_page.has_no_more() or self.servers_page.is_loading():
            return
        if not self.servers_page._servers:
            self._load_more_servers()

    def _load_more_servers(self) -> None:
        if not self.session.client:
            return
        if self.servers_page.is_loading() or self.servers_page.has_no_more():
            return
        self.servers_page.set_loading(True)
        self.servers_page.set_status("正在加载服务器...")

        def task() -> List[NetGameItem]:
            return self.session.client.get_available_servers(self._server_offset, self._server_page_size)

        def on_success(servers: List[NetGameItem]) -> None:
            self.servers_page.set_loading(False)
            if not servers:
                self.servers_page.set_no_more(True)
                if self._server_offset == 0:
                    self.servers_page.set_status("暂无可用服务器。")
                else:
                    self.servers_page.set_status("已加载全部服务器。")
                return
            self._server_offset += self._server_page_size
            self.servers_page.set_servers(servers, append=True)
            self.servers_page.set_status("")

        def on_error(message: str) -> None:
            self.servers_page.set_loading(False)
            self.servers_page.set_status(message or "获取服务器失败。")

        self._run_task(task, on_success, on_error)

    def _ensure_skins_loaded(self) -> None:
        if not self.session.client:
            return
        if self.skins_page.is_loading() or self.skins_page.has_no_more():
            return
        if not self.skins_page._skins:
            self._load_more_skins()

    def _load_more_skins(self) -> None:
        if not self.session.client:
            return
        if self.skins_page.is_loading() or self.skins_page.has_no_more():
            return
        query = self._skin_query
        offset = self._skin_offset
        self.skins_page.set_loading(True)
        self.skins_page.set_status("正在搜索皮肤..." if query else "正在加载皮肤...")
        self._skin_request_id += 1
        request_id = self._skin_request_id

        def task() -> List[GameSkin]:
            if query:
                return self.session.client.search_free_skins(query, offset, self._skin_page_size)
            return self.session.client.get_free_skins(offset, self._skin_page_size)

        def on_success(skins: List[GameSkin]) -> None:
            if request_id != self._skin_request_id:
                return
            self.skins_page.set_loading(False)
            if not skins:
                self.skins_page.set_no_more(True)
                if query:
                    self.skins_page.set_status("未找到匹配的皮肤。")
                elif offset == 0:
                    self.skins_page.set_status("暂无可用皮肤。")
                else:
                    self.skins_page.set_status("已加载全部皮肤。")
                return
            self._skin_offset += self._skin_page_size
            self.skins_page.set_skins(skins, append=True)
            if len(skins) < self._skin_page_size:
                self.skins_page.set_no_more(True)
                self.skins_page.set_status("已加载全部皮肤。")
            else:
                self.skins_page.set_status("")

        def on_error(message: str) -> None:
            if request_id != self._skin_request_id:
                return
            self.skins_page.set_loading(False)
            self.skins_page.set_status(message or "获取皮肤失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _search_skins(self, keyword: str) -> None:
        if not self.session.client:
            return
        self._skin_query = keyword.strip()
        self._skin_offset = 0
        self.skins_page.reset_state()
        self._load_more_skins()

    def _apply_skin(self, skin: object) -> None:
        if not self.session.client:
            return
        skin_id = getattr(skin, "entity_id", "")
        if not skin_id:
            self.skins_page.set_status("皮肤信息不完整。", error=True)
            return
        skin_name = getattr(skin, "name", "") or skin_id
        self.skins_page.set_status(f"正在应用皮肤：{skin_name}...")

        def task() -> None:
            self.session.client.set_skin(skin_id)

        def on_success(_: object) -> None:
            self.skins_page.set_status(f"已应用皮肤：{skin_name}。")

        def on_error(message: str) -> None:
            self.skins_page.set_status(message or "应用皮肤失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _load_skin_image(self, card: SkinCard, url: str) -> None:
        if not url:
            return
        cached = self._skin_image_cache.get(url)
        if cached:
            card.set_image(cached)
            return
        pending = self._skin_image_pending.setdefault(url, [])
        pending.append(card)
        if len(pending) > 1:
            return

        def task() -> bytes:
            return self._fetch_image_bytes(url)

        def on_success(data: bytes) -> None:
            pending_cards = self._skin_image_pending.pop(url, [])
            if not pending_cards:
                return
            pixmap = QtGui.QPixmap()
            pixmap.loadFromData(data)
            if pixmap.isNull():
                for item in pending_cards:
                    item.image_label.setText("加载失败")
                return
            self._skin_image_cache[url] = pixmap
            for item in pending_cards:
                item.set_image(pixmap)

        def on_error(_: str) -> None:
            pending_cards = self._skin_image_pending.pop(url, [])
            for item in pending_cards:
                item.image_label.setText("加载失败")

        self._run_task(task, on_success, on_error)

    def _fetch_image_bytes(self, url: str) -> bytes:
        request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.read()

    def _handle_server_selected(self, server: NetGameItem) -> None:
        if not self.session.client:
            return
        self._server_detail_request_id += 1
        request_id = self._server_detail_request_id
        selected_id = server.entity_id
        self.session.server = server
        self.session.server_detail = None
        self.session.server_address = None
        self.session.character_name = None
        self.session.game_started = False
        self.servers_page.set_selected_server(server)
        self.servers_page.set_status("正在加载服务器详情...")

        def task() -> tuple[NetGameDetail, NetGameServerAddress]:
            detail = self.session.client.get_server_detail(server.entity_id)
            address = self.session.client.get_server_address(server.entity_id)
            return detail, address

        def on_success(result: tuple[NetGameDetail, NetGameServerAddress]) -> None:
            if request_id != self._server_detail_request_id:
                return
            if not self.session.server or self.session.server.entity_id != selected_id:
                return
            detail, address = result
            self.session.server_detail = detail
            self.session.server_address = address
            self.servers_page.set_server_details(detail, address)
            self.servers_page.set_status("")
            self._set_nav_enabled(servers=True, characters=True, connection=True, skins=True, plugins=True)

        def on_error(message: str) -> None:
            if request_id != self._server_detail_request_id:
                return
            self.servers_page.set_status(message or "获取服务器详情失败。")
            self.servers_page.set_server_details(None, None)

        self._run_task(task, on_success, on_error)

    def _load_characters(self) -> None:
        if not self.session.client or not self.session.server:
            return
        self.characters_page.set_status("正在加载角色...")

        game_id = self.session.server.entity_id

        def task() -> List[GameCharacter]:
            return self.session.client.get_characters(game_id)

        def on_success(characters: List[GameCharacter]) -> None:
            self.characters_page.set_status("")
            self.characters_page.set_characters(characters)

        def on_error(message: str) -> None:
            self.characters_page.set_status(message or "获取角色失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _create_character(self, name: str) -> None:
        if not self.session.client or not self.session.server:
            return

        game_id = self.session.server.entity_id
        self.characters_page.set_status("正在创建角色...")

        def task() -> None:
            self.session.client.create_character(game_id, name)

        def on_success(_: object) -> None:
            self.characters_page.set_status("角色已创建。")
            self.characters_page.new_name.clear()
            self._load_characters()

        def on_error(message: str) -> None:
            self.characters_page.set_status(message or "创建角色失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _start_game(self, character_name: str) -> None:
        if not self.session.client or not self.session.server:
            return
        game_id = self.session.server.entity_id
        self.characters_page.set_status("正在进入游戏...")

        def task() -> None:
            self.session.client.game_start(game_id)

        def on_success(_: object) -> None:
            self.session.character_name = character_name
            self.session.game_started = True
            # Store last played server info
            if self.session.server:
                version = self.session.server_version() or "--"
                host, port = self.session.remote_address()
                address = _format_address(host, port) if host and port else "--"
                self.settings.set("last_server_id", self.session.server.entity_id, save=False)
                self.settings.set("last_server_name", self.session.server.name or "服务器", save=False)
                self.settings.set("last_server_version", version, save=False)
                self.settings.set("last_server_address", address, save=False)
                self.settings.set("last_server_time", int(time.time()), save=True)
            self.characters_page.set_status("进入游戏成功。")
            self._set_nav_enabled(servers=True, characters=True, connection=True, skins=True, plugins=True)
            self.switch_page("connection")

        def on_error(message: str) -> None:
            self.characters_page.set_status(message or "进入游戏失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _refresh_connection_info(self) -> None:
        server = self.session.server
        if not server:
            self.connection_page.set_server_info("--", "--", "--")
            self.connection_page.set_actions_enabled(can_join=False, can_proxy=False)
            return
        version = self.session.server_version()
        host, port = self.session.remote_address()
        remote = _format_address(host, port)
        self.connection_page.set_server_info(server.name, version, remote)
        self.connection_page.set_actions_enabled(
            can_join=True,
            can_proxy=bool(self.session.character_name),
        )

    def _refresh_recent_server_card(self) -> None:
        last_id = self.settings.get("last_server_id", "")
        if not last_id:
            self.servers_page.set_recent_server(None)
            return
        info = {
            "id": last_id,
            "name": self.settings.get("last_server_name", ""),
            "version": self.settings.get("last_server_version", ""),
            "address": self.settings.get("last_server_address", ""),
            "time": self.settings.get("last_server_time", 0),
        }
        self.servers_page.set_recent_server(info)

    def _select_recent_server(self, server_id: str) -> None:
        if not server_id or not self.session.client:
            return
        server = self.servers_page.find_server_by_id(server_id)
        if server is None:
            server = NetGameItem(
                entity_id=server_id,
                name=self.settings.get("last_server_name", "服务器"),
                brief_summary="",
                online_count="--",
                title_image_url="",
            )
        self._handle_server_selected(server)

    def _build_ygg_profile(self, include_mods: bool) -> tuple[GameProfile, YggdrasilData]:
        if not self.session.client or not self.session.server or not self.session.auth:
            raise RuntimeError("缺少会话数据")

        version = self.session.server_version()
        if not version:
            raise RuntimeError("服务器版本不可用")

        t0 = time.perf_counter()
        self._logger.info(
            "ProxyPhase ygg profile start game_id=%s version=%s include_mods=%s",
            self.session.server.entity_id,
            version,
            include_mods,
        )

        t1 = time.perf_counter()
        info = self.session.client.fetch_fantnel_info()
        self._logger.info("ProxyPhase ygg fetch fantnel info %.1fms", (time.perf_counter() - t1) * 1000)
        if not info.crc_salt:
            raise RuntimeError("CRC 盐值不可用")

        t2 = time.perf_counter()
        pair = get_md5_pair(version)
        self._logger.info("ProxyPhase ygg md5 pair %.1fms", (time.perf_counter() - t2) * 1000)
        mods = ModList([])
        if include_mods:
            try:
                t3 = time.perf_counter()
                mods = self.session.client.get_mod_list(self.session.server.entity_id, version, include_assets=True)
                self._logger.info(
                    "ProxyPhase ygg mod list %s %.1fms",
                    len(mods.mods),
                    (time.perf_counter() - t3) * 1000,
                )
            except Exception:  # pylint: disable=broad-except
                mods = ModList([])
                self._logger.warning("ProxyPhase ygg mod list failed, fallback empty")

        profile = GameProfile(
            game_id=self.session.server.entity_id,
            game_version=version,
            bootstrap_md5=pair.bootstrap_md5,
            dat_file_md5=pair.dat_file_md5,
            mods=mods,
            user=UserProfile(user_id=int(self.session.auth.entity_id), user_token=self.session.auth.token),
        )
        ygg_data = YggdrasilData(
            launcher_version=self.session.client.game_version,
            channel="netease",
            crc_salt=info.crc_salt,
        )
        self._logger.info("ProxyPhase ygg profile ready %.1fms", (time.perf_counter() - t0) * 1000)
        return profile, ygg_data

    def _join_yggdrasil(self, server_id: str) -> None:
        if not server_id:
            self.connection_page.set_proxy_status("请输入服务器ID以加入。", error=True)
            return

        self.connection_page.set_proxy_status("正在加入 Yggdrasil...", error=False)

        def task() -> tuple[bool, str]:
            profile, ygg_data = self._build_ygg_profile(include_mods=False)
            ygg = StandardYggdrasil.with_random_server(ygg_data)
            ok, err = ygg.join_server(profile, server_id)
            return ok, err or ""

        def on_success(result: tuple[bool, str]) -> None:
            ok, err = result
            if ok:
                self.connection_page.set_proxy_status("Yggdrasil 加入成功。")
            else:
                self.connection_page.set_proxy_status(f"加入失败：{err}", error=True)

        def on_error(message: str) -> None:
            self.connection_page.set_proxy_status(message or "加入失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _start_proxy(self, local_host: str, local_port_raw: str) -> None:
        def proceed() -> None:
            self._start_proxy_impl(local_host, local_port_raw)

        self._ensure_auth_then(
            reason="启动代理",
            proceed=proceed,
            status_cb=self._proxy_status,
        )

    def _start_proxy_impl(self, local_host: str, local_port_raw: str) -> None:
        if not self.session.server or not self.session.character_name:
            self.connection_page.set_proxy_status("请先选择服务器和角色。", error=True)
            return

        host, port = self.session.remote_address()
        if not host or not port:
            self.connection_page.set_proxy_status("服务器地址不可用。", error=True)
            return

        try:
            local_port = int(local_port_raw)
        except ValueError:
            self.connection_page.set_proxy_status("本地端口无效。", error=True)
            return

        self._logger.info("Proxy start requested host=%s port=%s", local_host, local_port_raw)
        self.connection_page.set_proxy_status("正在准备代理配置...")
        self.connection_page.proxy_start_button.setEnabled(False)

        def task() -> tuple[Optional[GameProfile], Optional[YggdrasilData], str]:
            warning = ""
            profile = None
            ygg_data = None
            try:
                profile, ygg_data = self._build_ygg_profile(include_mods=True)
            except Exception as exc:  # pylint: disable=broad-except
                warning = str(exc)
            return profile, ygg_data, warning

        def on_success(result: tuple[Optional[GameProfile], Optional[YggdrasilData], str]) -> None:
            profile, ygg_data, warning = result
            if warning:
                self.connection_page.set_proxy_status(f"Yggdrasil 已跳过：{warning}")
            config = ProxyConfig(
                listen_host=local_host or "127.0.0.1",
                listen_port=local_port,
                forward_host=host,
                forward_port=port,
                nickname=self.session.character_name,
                game_id=self.session.server.entity_id,
                ygg_profile=profile,
                ygg_data=ygg_data,
            )
            self._logger.info(
                "Proxy config ready listen=%s:%s forward=%s:%s",
                config.listen_host,
                config.listen_port,
                config.forward_host,
                config.forward_port,
            )
            self._launch_proxy_thread(config)
            self.connection_page.proxy_start_button.setEnabled(True)

        def on_error(message: str) -> None:
            self._logger.warning("Proxy config error: %s", message)
            self.connection_page.set_proxy_status(message or "准备代理失败。", error=True)
            self.connection_page.proxy_start_button.setEnabled(True)

        self._run_task(task, on_success, on_error)

    def _launch_proxy_thread(self, config: ProxyConfig) -> None:
        if not self.session.auth or not self.session.server:
            self.connection_page.set_proxy_status("缺少会话数据。", error=True)
            self.connection_page.proxy_start_button.setEnabled(True)
            return

        user_id = self.session.auth.entity_id
        user_account = self.session.display_name or self.session.auth.account or self.session.auth.entity_id
        if getattr(self.session, "remark", ""):
            user_account = f"{user_account} · {self.session.remark}"
        user_token = self.session.auth.token
        server_id = self.session.server.entity_id
        server_name = self.session.server.name or "服务器"
        server_version = self.session.server_version() or "--"
        nickname = config.nickname or ""

        self._cleanup_duplicate_proxies(user_id, server_id, nickname, user_token)

        for proxy in self._managed_proxies:
            if proxy.status == "正在停止":
                continue
            if proxy.local_host == config.listen_host and proxy.local_port == config.listen_port:
                self.connection_page.set_proxy_status("本地监听地址已被占用。", error=True)
                self.connection_page.proxy_start_button.setEnabled(True)
                return

        thread = ProxyThread(config)
        proxy = ManagedProxy(
            id=self._next_proxy_id,
            user_id=user_id,
            user_account=user_account,
            user_token=user_token,
            server_id=server_id,
            server_name=server_name,
            server_version=server_version,
            local_host=config.listen_host,
            local_port=config.listen_port,
            forward_host=config.forward_host,
            forward_port=config.forward_port,
            nickname=nickname,
            status="启动中",
            started_at=time.time(),
            thread=thread,
        )
        self._next_proxy_id += 1
        self._managed_proxies.append(proxy)
        self._refresh_proxy_manager()
        self.connection_page.set_proxy_status("正在启动代理...")
        self._logger.info(
            "Proxy thread launch id=%s local=%s forward=%s",
            proxy.id,
            proxy.local_address(),
            proxy.forward_address(),
        )

        thread.started_proxy.connect(lambda address, t=thread: self._on_proxy_started(address, t))
        thread.error.connect(lambda message, t=thread: self._on_proxy_error(message, t))
        thread.stopped_proxy.connect(lambda t=thread: self._on_proxy_stopped(t))
        thread.start()

    def _apply_theme(self, theme: str) -> None:
        """Apply theme to the application."""
        stylesheet = build_stylesheet(theme)
        QtWidgets.QApplication.instance().setStyleSheet(stylesheet)

    def _on_settings_saved(self) -> None:
        """Handle settings saved event."""
        # Reload settings and apply theme
        theme = self.settings.theme
        self._apply_theme(theme)

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        for proxy in list(self._managed_proxies):
            if proxy.thread.isRunning():
                proxy.thread.stop()
        for proxy in list(self._managed_proxies):
            if proxy.thread.isRunning():
                proxy.thread.wait(2000)
        super().closeEvent(event)
