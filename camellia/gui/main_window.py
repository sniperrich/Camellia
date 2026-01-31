"""
Main Window

Main application window for Camellia.NEL GUI with Material 3 design.
"""

from __future__ import annotations

import time
import urllib.request
import uuid
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..api import (
    WPFLauncherClient,
    login_with_password,
    login_with_netease_email,
    login_with_netease_phone,
    send_netease_sms,
)
from ..mc import GameProfile, ModList, ProxyConfig, StandardYggdrasil, UserProfile, YggdrasilData, get_md5_pair
from ..models import AuthOtp, GameCharacter, GameSkin, NetGameDetail, NetGameItem, NetGameServerAddress
from ..plugins import PluginState, get_plugin_manager
from .theme import build_stylesheet
from .widgets import Backdrop, CamelliaLogo, NavButton, SkinCard, make_nav_icon
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
        return f"{channel} / {self.auth.entity_id}"


@dataclass
class ManagedProxy:
    id: int
    user_id: str
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
        self.setWindowTitle("Camellia NEL 启动器")
        self.resize(1080, 640)
        self.setMinimumSize(1080, 640)

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

        self._build_ui()

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

        subtitle = QtWidgets.QLabel("Camellia Engine")
        subtitle.setProperty("muted", "true")

        sidebar_layout.addLayout(brand_row)
        sidebar_layout.addWidget(subtitle)

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
        self.servers_page.continue_requested.connect(lambda: self.switch_page("characters"))

        self.characters_page.refresh_requested.connect(lambda: self._load_characters())
        self.characters_page.create_requested.connect(lambda: self._create_character())
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

        self.settings_page.theme_changed.connect(lambda theme: self._apply_theme(theme))
        self.settings_page.settings_saved.connect(lambda: self._on_settings_saved())

        self._set_active_session(None)
        self._refresh_nav_icons("login")
        self.switch_page("login")
        self._load_saved_accounts()

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

        class _CallbackProxy(QtCore.QObject):
            @QtCore.Slot(object)
            def handle_finished(self, result: object) -> None:
                try:
                    on_success(result)
                finally:
                    _cleanup()

            @QtCore.Slot(str)
            def handle_error(self, message: str) -> None:
                try:
                    on_error(message)
                finally:
                    _cleanup()

        proxy = _CallbackProxy(self)
        worker._callback_proxy = proxy
        worker.signals.finished.connect(proxy.handle_finished)
        worker.signals.error.connect(proxy.handle_error)
        self.thread_pool.start(worker)

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

    def _cleanup_duplicate_proxies(self, user_id: str, server_id: str, nickname: str, user_token: str) -> None:
        for proxy in list(self._managed_proxies):
            same_user = proxy.user_id == user_id
            same_server = proxy.server_id == server_id
            same_nickname = proxy.nickname == nickname
            token_changed = same_user and proxy.user_token != user_token
            if (same_user and same_server and same_nickname) or token_changed:
                self._stop_proxy_thread(proxy)

    def _find_proxy_by_thread(self, thread: QtCore.QThread) -> ManagedProxy | None:
        for proxy in self._managed_proxies:
            if proxy.thread is thread:
                return proxy
        return None

    @QtCore.Slot(str)
    def _on_proxy_started(self, address: str) -> None:
        thread = self.sender()
        if not isinstance(thread, ProxyThread):
            return
        proxy = self._find_proxy_by_thread(thread)
        if not proxy:
            return
        proxy.status = "运行中"
        self.connection_page.set_proxy_status(
            f"代理已启动：{address} -> {proxy.forward_host}:{proxy.forward_port}"
        )
        self._refresh_proxy_manager()

    @QtCore.Slot(str)
    def _on_proxy_error(self, message: str) -> None:
        thread = self.sender()
        if not isinstance(thread, ProxyThread):
            return
        proxy = self._find_proxy_by_thread(thread)
        if not proxy:
            return
        proxy.status = "启动失败"
        self.connection_page.set_proxy_status(message or "代理启动失败。", error=True)
        self.connection_page.set_proxy_manager_status(f"代理 #{proxy.id} 启动失败。", error=True)
        self._refresh_proxy_manager()

    @QtCore.Slot()
    def _on_proxy_stopped(self) -> None:
        thread = self.sender()
        if not isinstance(thread, ProxyThread):
            return
        proxy = self._find_proxy_by_thread(thread)
        if not proxy:
            return
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
            self.login_page.netease_code.setText("")

    def _handle_saved_login(self) -> None:
        account = self._current_saved_account()
        if not account:
            self.login_page.set_status("请选择一个已保存账号。", error=True)
            return
        self._load_saved_account()
        self._handle_login()

    def _save_current_account(self) -> None:
        mode = self.login_page.login_mode()
        remember = self._should_remember_password()
        if mode == "account":
            username = self.login_page.account_user.text().strip()
            if not username:
                self.login_page.set_status("请输入用户名。", error=True)
                return
            password = self.login_page.account_pass.text().strip()
            account = SavedAccount.new_account(username, password, remember)
        elif mode == "netease_email":
            email = self.login_page.netease_email.text().strip()
            if not email:
                self.login_page.set_status("请输入网易邮箱。", error=True)
                return
            password = self.login_page.netease_pass.text().strip()
            account = SavedAccount.new_netease_email(email, password, remember)
        else:
            phone = self.login_page.netease_phone.text().strip()
            if not phone:
                self.login_page.set_status("请输入手机号。", error=True)
                return
            account = SavedAccount.new_netease_phone(phone)

        for idx, existing in enumerate(self._saved_accounts):
            if existing.mode == account.mode and existing.key == account.key:
                account.id = existing.id
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

    def _auto_save_account(self, mode: str) -> None:
        remember = self._should_remember_password()
        if mode == "account":
            username = self.login_page.account_user.text().strip()
            if not username:
                return
            password = self.login_page.account_pass.text().strip() if remember else ""
            existing = next((item for item in self._saved_accounts if item.mode == "account" and item.key == username), None)
            if existing:
                existing.username = username
                existing.last_used = time.time()
                if remember:
                    existing.password = password
                    existing.remember_password = True
                else:
                    existing.password = ""
                    existing.remember_password = False
                save_accounts(self._saved_accounts)
                self._load_saved_accounts(selected_id=existing.id)
                return
            account = SavedAccount.new_account(username, password, remember)
        elif mode == "netease_email":
            email = self.login_page.netease_email.text().strip()
            if not email:
                return
            password = self.login_page.netease_pass.text().strip() if remember else ""
            existing = next((item for item in self._saved_accounts if item.mode == "netease_email" and item.key == email), None)
            if existing:
                existing.username = email
                existing.last_used = time.time()
                if remember:
                    existing.password = password
                    existing.remember_password = True
                else:
                    existing.password = ""
                    existing.remember_password = False
                save_accounts(self._saved_accounts)
                self._load_saved_accounts(selected_id=existing.id)
                return
            account = SavedAccount.new_netease_email(email, password, remember)
        else:
            phone = self.login_page.netease_phone.text().strip()
            if not phone:
                return
            existing = next((item for item in self._saved_accounts if item.mode == "netease_phone" and item.key == phone), None)
            if existing:
                existing.username = phone
                existing.last_used = time.time()
                save_accounts(self._saved_accounts)
                self._load_saved_accounts(selected_id=existing.id)
                return
            account = SavedAccount.new_netease_phone(phone)

        self._saved_accounts.append(account)
        save_accounts(self._saved_accounts)
        self._load_saved_accounts(selected_id=account.id)

    def _should_remember_password(self) -> bool:
        return bool(self.settings.get("remember_password", True))

    def _handle_login(self) -> None:
        self.login_page.clear_status()
        self.login_page.set_busy(True)

        mode = self.login_page.login_mode()
        if mode == "account":
            username = self.login_page.account_user.text().strip()
            password = self.login_page.account_pass.text().strip()

            def task() -> tuple[WPFLauncherClient, AuthOtp]:
                if not username or not password:
                    raise ValueError("请输入用户名和密码")
                client = WPFLauncherClient()
                sauth_json = login_with_password(username, password)
                auth = client.login_with_cookie(sauth_json)
                return client, auth

        elif mode == "netease_email":
            email = self.login_page.netease_email.text().strip()
            password = self.login_page.netease_pass.text().strip()

            def task() -> tuple[WPFLauncherClient, AuthOtp]:
                if not email or not password:
                    raise ValueError("请输入邮箱和密码")
                client = WPFLauncherClient()
                sauth_json = login_with_netease_email(email, password)
                auth = client.login_with_cookie(sauth_json)
                return client, auth

        else:
            phone = self.login_page.netease_phone.text().strip()
            code = self.login_page.netease_code.text().strip()

            def task() -> tuple[WPFLauncherClient, AuthOtp]:
                if not phone or not code:
                    raise ValueError("请输入手机号和验证码")
                client = WPFLauncherClient()
                sauth_json = login_with_netease_phone(phone, code)
                auth = client.login_with_cookie(sauth_json)
                return client, auth

        def on_success(result: tuple[WPFLauncherClient, AuthOtp]) -> None:
            client, auth = result
            session_id = uuid.uuid4().hex
            self._sessions[session_id] = SessionState(
                session_id=session_id,
                client=client,
                auth=auth,
            )
            self._set_active_session(session_id)
            print(f"user_token={auth.token}")
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
            self._auto_save_account(mode)
            self.switch_page("servers")

        def on_error(message: str) -> None:
            self.login_page.set_busy(False)
            self.login_page.set_status(message or "登录失败。", error=True)

        self._run_task(task, on_success, on_error)

    def _handle_send_sms(self) -> None:
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
            self.login_page.set_status(message or "验证码发送失败。", error=True)

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

    def _build_ygg_profile(self, include_mods: bool) -> tuple[GameProfile, YggdrasilData]:
        if not self.session.client or not self.session.server or not self.session.auth:
            raise RuntimeError("缺少会话数据")

        version = self.session.server_version()
        if not version:
            raise RuntimeError("服务器版本不可用")

        info = self.session.client.fetch_fantnel_info()
        if not info.crc_salt:
            raise RuntimeError("CRC 盐值不可用")

        pair = get_md5_pair(version)
        mods = ModList([])
        if include_mods:
            try:
                mods = self.session.client.get_mod_list(self.session.server.entity_id, version, include_assets=True)
            except Exception:  # pylint: disable=broad-except
                mods = ModList([])

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
            self._launch_proxy_thread(config)
            self.connection_page.proxy_start_button.setEnabled(True)

        def on_error(message: str) -> None:
            self.connection_page.set_proxy_status(message or "准备代理失败。", error=True)
            self.connection_page.proxy_start_button.setEnabled(True)

        self._run_task(task, on_success, on_error)

    def _launch_proxy_thread(self, config: ProxyConfig) -> None:
        if not self.session.auth or not self.session.server:
            self.connection_page.set_proxy_status("缺少会话数据。", error=True)
            self.connection_page.proxy_start_button.setEnabled(True)
            return

        user_id = self.session.auth.entity_id
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

        thread.started_proxy.connect(self._on_proxy_started)
        thread.error.connect(self._on_proxy_error)
        thread.stopped_proxy.connect(self._on_proxy_stopped)
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
