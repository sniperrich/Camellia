from __future__ import annotations

import sys
import time
import urllib.request
from dataclasses import dataclass
from typing import List, Optional, Tuple

from PySide6 import QtCore, QtGui, QtWidgets

from ..api import WPFLauncherClient, login_with_password
from ..mc import GameProfile, ModList, ProxyConfig, StandardYggdrasil, UserProfile, YggdrasilData, get_md5_pair
from ..models import AuthOtp, GameCharacter, GameSkin, NetGameDetail, NetGameItem, NetGameServerAddress
from ..plugins import PluginState, get_plugin_manager
from .theme import PALETTE, build_stylesheet
from .widgets import Backdrop, InfoRow, NavButton, PluginCard, ServerCard, SkinCard
from .storage import SavedAccount, load_accounts, save_accounts
from .workers import ProxyThread, Worker


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _extract_cookie(raw_text: str) -> str:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    for line in lines:
        if line.startswith("{") and "sauth_json" in line:
            return line
    for idx, line in enumerate(lines):
        if line.lower().startswith("cookies"):
            for next_line in lines[idx + 1 :]:
                if next_line.startswith("{") and "sauth_json" in next_line:
                    return next_line
    start = raw_text.find("{")
    end = raw_text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw_text[start : end + 1].strip()
    raise ValueError("未在登录凭据文件中找到 sauth_json")


def _format_address(host: str, port: int) -> str:
    if not host or not port:
        return "不可用"
    return f"{host}:{port}"


PAGE_MARGIN = 8
PAGE_SPACING = 16


@dataclass
class SessionState:
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


class LoginPage(QtWidgets.QWidget):
    login_clicked = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)

        title = QtWidgets.QLabel("登录")
        title.setObjectName("Title")
        subtitle = QtWidgets.QLabel("I wish u well.")
        subtitle.setObjectName("Subtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        card = QtWidgets.QFrame()
        card.setProperty("card", "true")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(18, 18, 18, 18)
        card_layout.setSpacing(14)

        saved_row = QtWidgets.QHBoxLayout()
        self.saved_combo = QtWidgets.QComboBox()
        self.saved_combo.setMinimumWidth(220)
        self.saved_combo.setPlaceholderText("历史账号")
        self.load_saved_button = QtWidgets.QPushButton("载入")
        self.load_saved_button.setProperty("variant", "ghost")
        self.remove_saved_button = QtWidgets.QPushButton("删除")
        self.remove_saved_button.setProperty("variant", "ghost")

        saved_row.addWidget(self.saved_combo, 1)
        saved_row.addWidget(self.load_saved_button)
        saved_row.addWidget(self.remove_saved_button)
        card_layout.addLayout(saved_row)

        mode_layout = QtWidgets.QHBoxLayout()
        self.cookie_radio = QtWidgets.QRadioButton("登录凭据文件")
        self.account_radio = QtWidgets.QRadioButton("4399 账号")
        self.cookie_radio.setChecked(True)

        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_group.addButton(self.cookie_radio)
        self.mode_group.addButton(self.account_radio)
        self.mode_group.buttonToggled.connect(self._on_mode_changed)

        mode_layout.addWidget(self.cookie_radio)
        mode_layout.addWidget(self.account_radio)
        mode_layout.addStretch(1)
        card_layout.addLayout(mode_layout)

        self.stack = QtWidgets.QStackedWidget()
        self.stack.addWidget(self._build_cookie_form())
        self.stack.addWidget(self._build_account_form())
        card_layout.addWidget(self.stack)

        save_row = QtWidgets.QHBoxLayout()
        self.save_button = QtWidgets.QPushButton("保存当前账号")
        self.save_button.setProperty("variant", "ghost")
        self.remember_checkbox = QtWidgets.QCheckBox("记住密码（明文存储）")
        self.remember_checkbox.setChecked(False)
        self.remember_checkbox.setVisible(False)
        save_row.addWidget(self.save_button)
        save_row.addStretch(1)
        save_row.addWidget(self.remember_checkbox)
        card_layout.addLayout(save_row)

        self.login_button = QtWidgets.QPushButton("登录")
        self.login_button.setProperty("variant", "primary")
        self.login_button.clicked.connect(self.login_clicked.emit)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", "true")

        card_layout.addWidget(self.login_button)
        card_layout.addWidget(self.status_label)

        layout.addWidget(card)
        layout.addStretch(1)

    def _build_cookie_form(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        path_row = QtWidgets.QHBoxLayout()
        self.cookie_path = QtWidgets.QLineEdit()
        self.cookie_path.setPlaceholderText("登录凭据文件路径（默认：test_sauth）")
        self.browse_button = QtWidgets.QPushButton("浏览")
        self.browse_button.setProperty("variant", "ghost")
        self.browse_button.clicked.connect(self._browse_cookie_file)

        path_row.addWidget(self.cookie_path, 1)
        path_row.addWidget(self.browse_button)

        hint = QtWidgets.QLabel("登录凭据文件需要包含 sauth_json 的 JSON 数据。")
        hint.setProperty("muted", "true")

        layout.addLayout(path_row)
        layout.addWidget(hint)
        return widget

    def _build_account_form(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        self.account_user = QtWidgets.QLineEdit()
        self.account_user.setPlaceholderText("4399 用户名")
        self.account_pass = QtWidgets.QLineEdit()
        self.account_pass.setPlaceholderText("4399 密码")
        self.account_pass.setEchoMode(QtWidgets.QLineEdit.Password)

        layout.addWidget(self.account_user)
        layout.addWidget(self.account_pass)
        return widget

    def _browse_cookie_file(self) -> None:
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择登录凭据文件", "", "所有文件 (*)")
        if path:
            self.cookie_path.setText(path)

    def _on_mode_changed(self, button: QtWidgets.QAbstractButton, checked: bool) -> None:
        if not checked:
            return
        self.stack.setCurrentIndex(0 if button == self.cookie_radio else 1)
        account_mode = button == self.account_radio
        self.remember_checkbox.setVisible(account_mode)
        if not account_mode:
            self.remember_checkbox.setChecked(False)

    def login_mode(self) -> str:
        return "cookie" if self.cookie_radio.isChecked() else "account"

    def set_busy(self, busy: bool) -> None:
        self.login_button.setEnabled(not busy)
        self.cookie_radio.setEnabled(not busy)
        self.account_radio.setEnabled(not busy)
        self.saved_combo.setEnabled(not busy)
        self.load_saved_button.setEnabled(not busy)
        self.remove_saved_button.setEnabled(not busy)
        self.save_button.setEnabled(not busy)
        self.remember_checkbox.setEnabled(not busy)
        self.cookie_path.setEnabled(not busy)
        self.browse_button.setEnabled(not busy)
        self.account_user.setEnabled(not busy)
        self.account_pass.setEnabled(not busy)
        if busy:
            self.set_status("处理中...", error=False)

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        if error:
            self.status_label.setStyleSheet(f"color: {PALETTE['danger']};")
        else:
            self.status_label.setStyleSheet("")
            self.status_label.setProperty("muted", "true")

    def clear_status(self) -> None:
        self.status_label.setText("")
        self.status_label.setStyleSheet("")

    def set_saved_accounts(self, accounts: list[SavedAccount], selected_id: str | None = None) -> None:
        self.saved_combo.blockSignals(True)
        self.saved_combo.clear()
        self.saved_combo.addItem("选择历史账号", None)
        for account in accounts:
            self.saved_combo.addItem(account.label, account.id)
        if selected_id:
            index = self.saved_combo.findData(selected_id)
            if index >= 0:
                self.saved_combo.setCurrentIndex(index)
        self.saved_combo.blockSignals(False)

    def selected_saved_id(self) -> str | None:
        data = self.saved_combo.currentData()
        return data if isinstance(data, str) else None


class ServersPage(QtWidgets.QWidget):
    server_selected = QtCore.Signal(object)
    load_more_requested = QtCore.Signal()
    continue_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)

        title = QtWidgets.QLabel("服务器列表")
        title.setObjectName("Title")
        subtitle = QtWidgets.QLabel("选择服务器并查看详情后继续。")
        subtitle.setObjectName("Subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(18)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("按名称或简介搜索，滚动到底自动加载更多")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(self._apply_filter)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._handle_scroll)
        self.scroll_area.setMinimumWidth(520)
        self.cards_container = QtWidgets.QWidget()
        self.cards_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.setAlignment(QtCore.Qt.AlignTop)
        self.scroll_area.setWidget(self.cards_container)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setProperty("muted", "true")
        self.status_label.setWordWrap(True)

        left.addWidget(self.search_input)
        left.addWidget(self.scroll_area, 1)
        left.addWidget(self.status_label)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(12)
        self.detail_card = QtWidgets.QFrame()
        self.detail_card.setProperty("card", "true")
        detail_layout = QtWidgets.QVBoxLayout(self.detail_card)
        detail_layout.setContentsMargins(18, 18, 18, 18)
        detail_layout.setSpacing(10)

        self.detail_title = QtWidgets.QLabel("未选择服务器")
        self.detail_title.setStyleSheet("font-size: 16px; font-weight: 600;")
        self.detail_summary = QtWidgets.QLabel("请选择服务器以加载详情。")
        self.detail_summary.setWordWrap(True)
        self.detail_summary.setProperty("muted", "true")

        self.info_id = InfoRow("服务器ID", "--")
        self.info_version = InfoRow("版本", "--")
        self.info_address = InfoRow("远程地址", "--")
        self.info_online = InfoRow("在线人数", "--")

        detail_layout.addWidget(self.detail_title)
        detail_layout.addWidget(self.detail_summary)
        detail_layout.addWidget(self.info_id)
        detail_layout.addWidget(self.info_version)
        detail_layout.addWidget(self.info_address)
        detail_layout.addWidget(self.info_online)

        self.continue_button = QtWidgets.QPushButton("继续")
        self.continue_button.setProperty("variant", "primary")
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self.continue_requested.emit)

        right.addWidget(self.detail_card)
        right.addWidget(self.continue_button)
        right.addStretch(1)

        body.addLayout(left, 4)
        body.addLayout(right, 2)

        layout.addLayout(body, 1)

        self._servers: List[NetGameItem] = []
        self._cards: List[ServerCard] = []
        self._selected_server: Optional[NetGameItem] = None
        self._is_loading = False
        self._no_more = False
        self._load_threshold = 120

    def set_servers(self, servers: List[NetGameItem], *, append: bool) -> None:
        if append:
            self._servers.extend(servers)
            if not self.search_input.text().strip():
                self._append_cards(servers)
                return
        else:
            self._servers = list(servers)
        self._render_cards()

    def set_loading(self, loading: bool) -> None:
        self._is_loading = loading

    def set_status(self, text: str) -> None:
        self.status_label.setText(text)

    def set_no_more(self, no_more: bool) -> None:
        self._no_more = no_more

    def is_loading(self) -> bool:
        return self._is_loading

    def has_no_more(self) -> bool:
        return self._no_more

    def reset_state(self) -> None:
        self._servers = []
        self._selected_server = None
        self._no_more = False
        self._is_loading = False
        self.search_input.clear()
        self.set_status("")
        self._render_cards()
        self.set_selected_server(None)

    def set_selected_server(self, server: NetGameItem | None) -> None:
        self._selected_server = server
        if not server:
            self.detail_title.setText("未选择服务器")
            self.detail_summary.setText("请选择服务器以加载详情。")
            self.info_id.set_value("--")
            self.info_version.set_value("--")
            self.info_address.set_value("--")
            self.info_online.set_value("--")
            self.continue_button.setEnabled(False)
            return

        self.detail_title.setText(server.name or "服务器")
        self.detail_summary.setText(server.brief_summary or "暂无简介。")
        self.info_id.set_value(server.entity_id)
        self.info_online.set_value(server.online_count or "--")
        self.info_version.set_value("加载中...")
        self.info_address.set_value("加载中...")

    def set_server_details(self, detail: NetGameDetail | None, address: NetGameServerAddress | None) -> None:
        if detail is None and address is None:
            self.info_version.set_value("--")
            self.info_address.set_value("--")
            self.continue_button.setEnabled(False)
            return
        version = "--"
        if detail and detail.mc_versions:
            version = detail.mc_versions[0].name
        host = ""
        port = 0
        if address:
            host = address.host
            port = address.port
        if detail:
            if not host:
                host = detail.server_address
            if not port:
                port = detail.server_port
        self.info_version.set_value(version or "--")
        self.info_address.set_value(_format_address(host, port))
        self.continue_button.setEnabled(True)

    def _handle_scroll(self, value: int) -> None:
        if self._no_more or self._is_loading:
            return
        bar = self.scroll_area.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if value >= bar.maximum() - self._load_threshold:
            self.load_more_requested.emit()

    def _maybe_trigger_autoload(self) -> None:
        if self.search_input.text().strip():
            return
        if self._no_more or self._is_loading:
            return
        bar = self.scroll_area.verticalScrollBar()
        if bar.maximum() <= 0 and self._servers:
            self.load_more_requested.emit()

    def _apply_filter(self, *_: object) -> None:
        self._render_cards()

    def _render_cards(self) -> None:
        query = self.search_input.text().strip().lower()
        servers = self._servers
        if query:
            servers = [
                server
                for server in servers
                if query in (server.name or "").lower()
                or query in (server.brief_summary or "").lower()
            ]

        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._cards = []

        for idx, server in enumerate(servers):
            card = ServerCard(server)
            card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            card.selected.connect(self._on_card_selected)
            self._cards.append(card)
            self.cards_layout.addWidget(card)
            self._fade_in(card, delay=idx * 30)

        if self._selected_server:
            for card in self._cards:
                card.set_selected(card.server == self._selected_server)

        self._maybe_trigger_autoload()

    def _append_cards(self, servers: List[NetGameItem]) -> None:
        if not servers:
            return
        for idx, server in enumerate(servers):
            card = ServerCard(server)
            card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            card.selected.connect(self._on_card_selected)
            if self._selected_server and card.server == self._selected_server:
                card.set_selected(True)
            self._cards.append(card)
            self.cards_layout.addWidget(card)
            self._fade_in(card, delay=idx * 30)
        self._maybe_trigger_autoload()

    def _on_card_selected(self, server: NetGameItem) -> None:
        self._selected_server = server
        for card in self._cards:
            card.set_selected(card.server == server)
        self.server_selected.emit(server)

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if self._servers or self._is_loading or self._no_more:
            return
        QtCore.QTimer.singleShot(0, self.load_more_requested.emit)

    def _fade_in(self, widget: QtWidgets.QWidget, delay: int = 0) -> None:
        effect = QtWidgets.QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        anim = QtCore.QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        QtCore.QTimer.singleShot(delay, anim.start)


class CharacterPage(QtWidgets.QWidget):
    refresh_requested = QtCore.Signal()
    create_requested = QtCore.Signal(str)
    continue_requested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)

        title = QtWidgets.QLabel("角色选择")
        title.setObjectName("Title")
        subtitle = QtWidgets.QLabel("选择已有角色或创建新角色。")
        subtitle.setObjectName("Subtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(18)

        left = QtWidgets.QVBoxLayout()
        left.setSpacing(10)
        self.character_list = QtWidgets.QListWidget()
        self.character_list.itemSelectionChanged.connect(self._sync_selection)

        self.refresh_button = QtWidgets.QPushButton("刷新")
        self.refresh_button.setProperty("variant", "ghost")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)

        left.addWidget(self.character_list, 1)
        left.addWidget(self.refresh_button)

        right = QtWidgets.QVBoxLayout()
        right.setSpacing(12)
        create_card = QtWidgets.QFrame()
        create_card.setProperty("card", "true")
        create_layout = QtWidgets.QVBoxLayout(create_card)
        create_layout.setContentsMargins(18, 18, 18, 18)
        create_layout.setSpacing(10)

        create_title = QtWidgets.QLabel("创建新角色")
        create_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.new_name = QtWidgets.QLineEdit()
        self.new_name.setPlaceholderText("角色名")
        self.create_button = QtWidgets.QPushButton("创建")
        self.create_button.setProperty("variant", "primary")
        self.create_button.clicked.connect(self._emit_create)

        create_layout.addWidget(create_title)
        create_layout.addWidget(self.new_name)
        create_layout.addWidget(self.create_button)

        selection_card = QtWidgets.QFrame()
        selection_card.setProperty("card", "true")
        selection_layout = QtWidgets.QVBoxLayout(selection_card)
        selection_layout.setContentsMargins(18, 18, 18, 18)
        selection_layout.setSpacing(10)

        selection_title = QtWidgets.QLabel("已选择角色")
        selection_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        self.selected_label = QtWidgets.QLabel("--")
        self.selected_label.setProperty("muted", "true")

        self.continue_button = QtWidgets.QPushButton("继续到连接设置")
        self.continue_button.setProperty("variant", "primary")
        self.continue_button.setEnabled(False)
        self.continue_button.clicked.connect(self._emit_continue)

        selection_layout.addWidget(selection_title)
        selection_layout.addWidget(self.selected_label)
        selection_layout.addWidget(self.continue_button)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", "true")

        right.addWidget(create_card)
        right.addWidget(selection_card)
        right.addWidget(self.status_label)
        right.addStretch(1)

        body.addLayout(left, 2)
        body.addLayout(right, 2)

        layout.addLayout(body, 1)

    def set_characters(self, characters: List[GameCharacter]) -> None:
        self.character_list.clear()
        for character in characters:
            self.character_list.addItem(character.name)
        if characters:
            self.character_list.setCurrentRow(0)
        else:
            self.selected_label.setText("--")
            self.continue_button.setEnabled(False)

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        if error:
            self.status_label.setStyleSheet(f"color: {PALETTE['danger']};")
        else:
            self.status_label.setStyleSheet("")
            self.status_label.setProperty("muted", "true")

    def _sync_selection(self) -> None:
        items = self.character_list.selectedItems()
        if not items:
            self.selected_label.setText("--")
            self.continue_button.setEnabled(False)
            return
        self.selected_label.setText(items[0].text())
        self.continue_button.setEnabled(True)

    def _emit_create(self) -> None:
        name = self.new_name.text().strip()
        if name:
            self.create_requested.emit(name)

    def _emit_continue(self) -> None:
        items = self.character_list.selectedItems()
        if items:
            self.continue_requested.emit(items[0].text())


class SkinPage(QtWidgets.QWidget):
    load_more_requested = QtCore.Signal()
    search_requested = QtCore.Signal(str)
    apply_requested = QtCore.Signal(object)
    image_requested = QtCore.Signal(object, str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)

        title = QtWidgets.QLabel("皮肤管理")
        title.setObjectName("Title")
        subtitle = QtWidgets.QLabel("浏览并应用免费皮肤。")
        subtitle.setObjectName("Subtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        search_row = QtWidgets.QHBoxLayout()
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("搜索皮肤名称")
        self.search_input.returnPressed.connect(self._emit_search)
        self.search_button = QtWidgets.QPushButton("搜索")
        self.search_button.setProperty("variant", "ghost")
        self.search_button.clicked.connect(self._emit_search)
        self.clear_button = QtWidgets.QPushButton("清除")
        self.clear_button.setProperty("variant", "ghost")
        self.clear_button.clicked.connect(self._clear_search)
        search_row.addWidget(self.search_input, 1)
        search_row.addWidget(self.search_button)
        search_row.addWidget(self.clear_button)
        layout.addLayout(search_row)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().valueChanged.connect(self._handle_scroll)
        self.cards_container = QtWidgets.QWidget()
        self.cards_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.setAlignment(QtCore.Qt.AlignTop)
        self.scroll_area.setWidget(self.cards_container)
        layout.addWidget(self.scroll_area, 1)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", "true")
        layout.addWidget(self.status_label)

        self._skins: list[GameSkin] = []
        self._cards: list[SkinCard] = []
        self._is_loading = False
        self._no_more = False
        self._load_threshold = 160

    def set_skins(self, skins: list[GameSkin], *, append: bool) -> None:
        if append:
            self._skins.extend(skins)
            self._append_cards(skins)
            return
        self._skins = list(skins)
        self._render_cards()

    def set_loading(self, loading: bool) -> None:
        self._is_loading = loading

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        if error:
            self.status_label.setStyleSheet(f"color: {PALETTE['danger']};")
        else:
            self.status_label.setStyleSheet("")
            self.status_label.setProperty("muted", "true")

    def set_no_more(self, no_more: bool) -> None:
        self._no_more = no_more

    def is_loading(self) -> bool:
        return self._is_loading

    def has_no_more(self) -> bool:
        return self._no_more

    def reset_state(self) -> None:
        self._skins = []
        self._no_more = False
        self._is_loading = False
        self.set_status("")
        self._render_cards()

    def showEvent(self, event: QtGui.QShowEvent) -> None:
        super().showEvent(event)
        if self._skins or self._is_loading or self._no_more:
            return
        QtCore.QTimer.singleShot(0, self.load_more_requested.emit)

    def _handle_scroll(self, value: int) -> None:
        if self._no_more or self._is_loading:
            return
        bar = self.scroll_area.verticalScrollBar()
        if bar.maximum() <= 0:
            return
        if value >= bar.maximum() - self._load_threshold:
            self.load_more_requested.emit()

    def _render_cards(self) -> None:
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        self._cards = []
        self._append_cards(self._skins)

    def _append_cards(self, skins: list[GameSkin]) -> None:
        for idx, skin in enumerate(skins):
            card = SkinCard(skin)
            card.apply_requested.connect(self._emit_apply)
            self._cards.append(card)
            self.cards_layout.addWidget(card)
            if skin.title_image_url:
                self.image_requested.emit(card, skin.title_image_url)
            self._fade_in(card, delay=idx * 30)

    def _emit_search(self) -> None:
        self.search_requested.emit(self.search_input.text().strip())

    def _clear_search(self) -> None:
        if not self.search_input.text().strip():
            return
        self.search_input.clear()
        self.search_requested.emit("")

    def _emit_apply(self, skin: object) -> None:
        self.apply_requested.emit(skin)

    def _fade_in(self, widget: QtWidgets.QWidget, delay: int = 0) -> None:
        effect = QtWidgets.QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        anim = QtCore.QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        QtCore.QTimer.singleShot(delay, anim.start)


class PluginsPage(QtWidgets.QWidget):
    refresh_requested = QtCore.Signal()
    toggle_requested = QtCore.Signal(str, bool)
    open_dir_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)

        title = QtWidgets.QLabel("插件管理")
        title.setObjectName("Title")
        subtitle = QtWidgets.QLabel("加载本地 Python 插件并管理启用状态。")
        subtitle.setObjectName("Subtitle")
        layout.addWidget(title)
        layout.addWidget(subtitle)

        header_row = QtWidgets.QHBoxLayout()
        self.count_label = QtWidgets.QLabel("插件数量：0")
        self.count_label.setProperty("muted", "true")
        self.refresh_button = QtWidgets.QPushButton("刷新")
        self.refresh_button.setProperty("variant", "ghost")
        self.refresh_button.clicked.connect(self.refresh_requested.emit)
        self.open_dir_button = QtWidgets.QPushButton("打开插件目录")
        self.open_dir_button.setProperty("variant", "ghost")
        self.open_dir_button.clicked.connect(self.open_dir_requested.emit)
        header_row.addWidget(self.count_label)
        header_row.addStretch(1)
        header_row.addWidget(self.refresh_button)
        header_row.addWidget(self.open_dir_button)
        layout.addLayout(header_row)

        self.path_label = QtWidgets.QLabel("")
        self.path_label.setProperty("muted", "true")
        layout.addWidget(self.path_label)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.cards_container = QtWidgets.QWidget()
        self.cards_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.setAlignment(QtCore.Qt.AlignTop)
        self.scroll_area.setWidget(self.cards_container)

        self.empty_label = QtWidgets.QLabel("未检测到插件。请将 Python 插件放入 plugins/ 目录。")
        self.empty_label.setProperty("muted", "true")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", "true")

        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.status_label)

        self._plugins: list[PluginState] = []
        self._update_empty_state()

    def set_plugin_path(self, path: str) -> None:
        if path:
            self.path_label.setText(f"插件目录：{path}")

    def set_plugins(self, plugins: list[PluginState]) -> None:
        self._plugins = list(plugins)
        self._render_cards()

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        if error:
            self.status_label.setStyleSheet(f"color: {PALETTE['danger']};")
        else:
            self.status_label.setStyleSheet("")
            self.status_label.setProperty("muted", "true")

    def _update_empty_state(self) -> None:
        has_items = bool(self._plugins)
        self.scroll_area.setVisible(has_items)
        self.empty_label.setVisible(not has_items)

    def _render_cards(self) -> None:
        self.count_label.setText(f"插件数量：{len(self._plugins)}")
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()
        for plugin in self._plugins:
            card = PluginCard(plugin)
            card.toggle_requested.connect(self.toggle_requested.emit)
            self.cards_layout.addWidget(card)
        self._update_empty_state()


class ConnectionPage(QtWidgets.QWidget):
    join_requested = QtCore.Signal(str)
    proxy_start_requested = QtCore.Signal(str, str)
    proxy_stop_requested = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)

        title = QtWidgets.QLabel("连接设置")
        title.setObjectName("Title")
        subtitle = QtWidgets.QLabel("选择直连或启动本地代理。")
        subtitle.setObjectName("Subtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.info_card = QtWidgets.QFrame()
        self.info_card.setProperty("card", "true")
        info_layout = QtWidgets.QVBoxLayout(self.info_card)
        info_layout.setContentsMargins(18, 18, 18, 18)
        info_layout.setSpacing(8)

        self.info_name = InfoRow("服务器", "--")
        self.info_version = InfoRow("版本", "--")
        self.info_remote = InfoRow("远程地址", "--")

        info_layout.addWidget(self.info_name)
        info_layout.addWidget(self.info_version)
        info_layout.addWidget(self.info_remote)

        layout.addWidget(self.info_card)

        mode_row = QtWidgets.QHBoxLayout()
        self.direct_button = QtWidgets.QPushButton("直连")
        self.direct_button.setCheckable(True)
        self.direct_button.setProperty("variant", "seg")
        self.proxy_button = QtWidgets.QPushButton("本地代理")
        self.proxy_button.setCheckable(True)
        self.proxy_button.setProperty("variant", "seg")

        self.mode_group = QtWidgets.QButtonGroup(self)
        self.mode_group.setExclusive(True)
        self.mode_group.addButton(self.direct_button)
        self.mode_group.addButton(self.proxy_button)
        self.direct_button.setChecked(True)
        self.mode_group.buttonClicked.connect(self._switch_mode)

        mode_row.addWidget(self.direct_button)
        mode_row.addWidget(self.proxy_button)
        mode_row.addStretch(1)

        layout.addLayout(mode_row)

        self.mode_stack = QtWidgets.QStackedWidget()
        self.mode_stack.addWidget(self._build_direct_panel())
        self.mode_stack.addWidget(self._build_proxy_panel())
        layout.addWidget(self.mode_stack, 1)

    def _build_direct_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setProperty("card", "true")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        self.server_id_input = QtWidgets.QLineEdit()
        self.server_id_input.setPlaceholderText("可选：输入服务器ID加入 Yggdrasil")

        button_row = QtWidgets.QHBoxLayout()
        self.join_button = QtWidgets.QPushButton("加入 Yggdrasil")
        self.join_button.setProperty("variant", "primary")
        self.join_button.clicked.connect(self._emit_join)
        self.copy_button = QtWidgets.QPushButton("复制远程地址")
        self.copy_button.setProperty("variant", "ghost")
        self.copy_button.clicked.connect(self._copy_remote)
        button_row.addWidget(self.join_button)
        button_row.addWidget(self.copy_button)
        button_row.addStretch(1)

        self.direct_status = QtWidgets.QLabel("")
        self.direct_status.setWordWrap(True)
        self.direct_status.setProperty("muted", "true")

        layout.addWidget(self.server_id_input)
        layout.addLayout(button_row)
        layout.addWidget(self.direct_status)

        return panel

    def _build_proxy_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QFrame()
        panel.setProperty("card", "true")
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        host_row = QtWidgets.QHBoxLayout()
        self.local_host = QtWidgets.QLineEdit("127.0.0.1")
        self.local_host.setPlaceholderText("本地监听地址")
        self.local_port = QtWidgets.QLineEdit("6445")
        self.local_port.setPlaceholderText("端口")
        host_row.addWidget(self.local_host, 2)
        host_row.addWidget(self.local_port, 1)

        button_row = QtWidgets.QHBoxLayout()
        self.proxy_start_button = QtWidgets.QPushButton("启动代理")
        self.proxy_start_button.setProperty("variant", "primary")
        self.proxy_start_button.clicked.connect(self._emit_proxy_start)
        self.proxy_stop_button = QtWidgets.QPushButton("停止全部代理")
        self.proxy_stop_button.setProperty("variant", "danger")
        self.proxy_stop_button.setEnabled(False)
        self.proxy_stop_button.clicked.connect(self.proxy_stop_requested.emit)
        button_row.addWidget(self.proxy_start_button)
        button_row.addWidget(self.proxy_stop_button)
        button_row.addStretch(1)

        self.proxy_status = QtWidgets.QLabel("")
        self.proxy_status.setWordWrap(True)
        self.proxy_status.setProperty("muted", "true")

        layout.addLayout(host_row)
        layout.addLayout(button_row)
        layout.addWidget(self.proxy_status)
        return panel

    def _switch_mode(self, *_: object) -> None:
        index = 0 if self.direct_button.isChecked() else 1
        self.mode_stack.setCurrentIndex(index)

    def _emit_join(self) -> None:
        server_id = self.server_id_input.text().strip()
        self.join_requested.emit(server_id)

    def _emit_proxy_start(self) -> None:
        self.proxy_start_requested.emit(self.local_host.text().strip(), self.local_port.text().strip())

    def _copy_remote(self) -> None:
        address = self.info_remote.value.text()
        if address and address not in ("不可用", "--"):
            QtGui.QGuiApplication.clipboard().setText(address)
            self.set_direct_status("远程地址已复制。", error=False)

    def set_server_info(self, name: str, version: str, remote: str) -> None:
        self.info_name.set_value(name or "--")
        self.info_version.set_value(version or "--")
        self.info_remote.set_value(remote or "--")

    def set_direct_status(self, text: str, *, error: bool = False) -> None:
        self.direct_status.setText(text)
        if error:
            self.direct_status.setStyleSheet(f"color: {PALETTE['danger']};")
        else:
            self.direct_status.setStyleSheet("")
            self.direct_status.setProperty("muted", "true")

    def set_proxy_status(self, text: str, *, error: bool = False) -> None:
        self.proxy_status.setText(text)
        if error:
            self.proxy_status.setStyleSheet(f"color: {PALETTE['danger']};")
        else:
            self.proxy_status.setStyleSheet("")
            self.proxy_status.setProperty("muted", "true")

    def set_proxy_running(self, running: bool) -> None:
        self.proxy_stop_button.setEnabled(running)

    def set_actions_enabled(self, *, can_join: bool, can_proxy: bool) -> None:
        self.join_button.setEnabled(can_join)
        self.proxy_start_button.setEnabled(can_proxy)


class ProxyManagerPage(QtWidgets.QWidget):
    close_all_requested = QtCore.Signal()
    close_one_requested = QtCore.Signal(int)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)

        title = QtWidgets.QLabel("代理管理")
        title.setObjectName("Title")
        subtitle = QtWidgets.QLabel("管理已启动的本地代理。")
        subtitle.setObjectName("Subtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        header_row = QtWidgets.QHBoxLayout()
        self.count_label = QtWidgets.QLabel("当前运行的代理数量：0")
        self.count_label.setProperty("muted", "true")
        self.close_all_button = QtWidgets.QPushButton("关闭全部代理")
        self.close_all_button.setProperty("variant", "danger")
        self.close_all_button.clicked.connect(self.close_all_requested.emit)
        header_row.addWidget(self.count_label)
        header_row.addStretch(1)
        header_row.addWidget(self.close_all_button)
        layout.addLayout(header_row)

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.cards_container = QtWidgets.QWidget()
        self.cards_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.setAlignment(QtCore.Qt.AlignTop)
        self.scroll_area.setWidget(self.cards_container)

        self.empty_label = QtWidgets.QLabel("当前没有运行的代理服务器。")
        self.empty_label.setProperty("muted", "true")
        self.empty_label.setAlignment(QtCore.Qt.AlignCenter)

        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", "true")

        layout.addWidget(self.scroll_area, 1)
        layout.addWidget(self.empty_label)
        layout.addWidget(self.status_label)

        self._proxies: list[ManagedProxy] = []
        self._update_empty_state()

    def set_proxies(self, proxies: list[ManagedProxy]) -> None:
        self._proxies = list(proxies)
        self._render_cards()

    def set_status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        if error:
            self.status_label.setStyleSheet(f"color: {PALETTE['danger']};")
        else:
            self.status_label.setStyleSheet("")
            self.status_label.setProperty("muted", "true")

    def _update_empty_state(self) -> None:
        has_items = bool(self._proxies)
        self.scroll_area.setVisible(has_items)
        self.empty_label.setVisible(not has_items)
        self.close_all_button.setEnabled(has_items)

    def _render_cards(self) -> None:
        self.count_label.setText(f"当前运行的代理数量：{len(self._proxies)}")
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        for proxy in self._proxies:
            card = QtWidgets.QFrame()
            card.setProperty("card", "true")
            card_layout = QtWidgets.QVBoxLayout(card)
            card_layout.setContentsMargins(16, 14, 16, 14)
            card_layout.setSpacing(8)

            header = QtWidgets.QHBoxLayout()
            title = QtWidgets.QLabel(f"代理 #{proxy.id}")
            title.setStyleSheet("font-weight: 600; font-size: 15px;")
            status = QtWidgets.QLabel(proxy.status or "未知状态")
            if "失败" in proxy.status:
                status.setStyleSheet(f"color: {PALETTE['danger']};")
            elif "启动" in proxy.status:
                status.setStyleSheet(f"color: {PALETTE['warning']};")
            else:
                status.setStyleSheet(f"color: {PALETTE['accent']};")
            status.setProperty("muted", "true")
            header.addWidget(title)
            header.addStretch(1)
            header.addWidget(status)

            info_stack = QtWidgets.QVBoxLayout()
            info_stack.setSpacing(6)
            info_stack.addWidget(InfoRow("昵称", proxy.nickname or "--"))
            info_stack.addWidget(InfoRow("本地地址", proxy.local_address()))
            info_stack.addWidget(InfoRow("转发地址", proxy.forward_address()))
            info_stack.addWidget(InfoRow("服务器", proxy.server_name or "--"))
            info_stack.addWidget(InfoRow("版本", proxy.server_version or "--"))

            actions = QtWidgets.QHBoxLayout()
            copy_button = QtWidgets.QPushButton("复制本地地址")
            copy_button.setProperty("variant", "ghost")
            copy_button.clicked.connect(lambda checked=False, addr=proxy.local_address(): self._copy_address(addr))
            close_button = QtWidgets.QPushButton("关闭")
            close_button.setProperty("variant", "danger")
            close_button.clicked.connect(lambda checked=False, proxy_id=proxy.id: self.close_one_requested.emit(proxy_id))
            actions.addWidget(copy_button)
            actions.addWidget(close_button)
            actions.addStretch(1)

            card_layout.addLayout(header)
            card_layout.addLayout(info_stack)
            card_layout.addLayout(actions)
            self.cards_layout.addWidget(card)

        self._update_empty_state()

    def _copy_address(self, address: str) -> None:
        if not address or address == "不可用":
            self.set_status("本地地址不可用。", error=True)
            return
        QtGui.QGuiApplication.clipboard().setText(address)
        self.set_status("已复制本地地址。")
        QtCore.QTimer.singleShot(2000, lambda: self.set_status(""))


class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Camellia NEL 启动器")
        self.resize(1180, 760)

        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self._workers: list[Worker] = []
        self._saved_accounts: list[SavedAccount] = []
        self.session = SessionState()
        self.plugin_manager = get_plugin_manager()
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
        central_layout.setContentsMargins(18, 18, 18, 18)
        central_layout.setSpacing(0)

        sidebar = QtWidgets.QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar_layout = QtWidgets.QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(16, 18, 16, 18)
        sidebar_layout.setSpacing(10)

        title = QtWidgets.QLabel("Camellia NEL")
        title.setObjectName("AppTitle")
        subtitle = QtWidgets.QLabel("Miss u~")
        subtitle.setProperty("muted", "true")

        sidebar_layout.addWidget(title)
        sidebar_layout.addWidget(subtitle)

        self.nav_buttons = {
            "login": NavButton("登录"),
            "servers": NavButton("服务器"),
            "characters": NavButton("角色"),
            "connection": NavButton("连接"),
            "skins": NavButton("皮肤"),
            "plugins": NavButton("插件管理"),
            "proxies": NavButton("代理管理"),
        }

        for key, button in self.nav_buttons.items():
            sidebar_layout.addWidget(button)
            button.clicked.connect(lambda checked=False, name=key: self.switch_page(name))

        sidebar_layout.addStretch(1)

        self.status_pill = QtWidgets.QFrame()
        self.status_pill.setProperty("card", "true")
        pill_layout = QtWidgets.QVBoxLayout(self.status_pill)
        pill_layout.setContentsMargins(12, 12, 12, 12)
        pill_layout.setSpacing(4)
        pill_title = QtWidgets.QLabel("状态")
        pill_title.setStyleSheet("font-weight: 600;")
        self.status_label = QtWidgets.QLabel("未登录")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", "true")
        pill_layout.addWidget(pill_title)
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
        self.proxy_manager_page = ProxyManagerPage()

        self.stack.addWidget(self.login_page)
        self.stack.addWidget(self.servers_page)
        self.stack.addWidget(self.characters_page)
        self.stack.addWidget(self.connection_page)
        self.stack.addWidget(self.skins_page)
        self.stack.addWidget(self.plugins_page)
        self.stack.addWidget(self.proxy_manager_page)

        central_layout.addWidget(sidebar, 1)
        central_layout.addWidget(self.stack, 4)

        self.setCentralWidget(backdrop)

        self.login_page.login_clicked.connect(self._handle_login)
        self.login_page.save_button.clicked.connect(self._save_current_account)
        self.login_page.load_saved_button.clicked.connect(self._load_saved_account)
        self.login_page.remove_saved_button.clicked.connect(self._remove_saved_account)
        self.login_page.saved_combo.currentIndexChanged.connect(self._load_saved_account)
        self.servers_page.load_more_requested.connect(self._load_more_servers)
        self.servers_page.server_selected.connect(self._handle_server_selected)
        self.servers_page.continue_requested.connect(lambda: self.switch_page("characters"))

        self.characters_page.refresh_requested.connect(self._load_characters)
        self.characters_page.create_requested.connect(self._create_character)
        self.characters_page.continue_requested.connect(self._start_game)

        self.skins_page.load_more_requested.connect(self._load_more_skins)
        self.skins_page.search_requested.connect(self._search_skins)
        self.skins_page.apply_requested.connect(self._apply_skin)
        self.skins_page.image_requested.connect(self._load_skin_image)

        self.plugins_page.set_plugin_path(str(self.plugin_manager.plugins_dir))
        self.plugins_page.refresh_requested.connect(lambda: self._refresh_plugins(force=True))
        self.plugins_page.toggle_requested.connect(self._toggle_plugin)
        self.plugins_page.open_dir_requested.connect(self._open_plugin_dir)

        self.connection_page.join_requested.connect(self._join_yggdrasil)
        self.connection_page.proxy_start_requested.connect(self._start_proxy)
        self.connection_page.proxy_stop_requested.connect(self._stop_all_proxies)
        self.proxy_manager_page.close_all_requested.connect(self._stop_all_proxies)
        self.proxy_manager_page.close_one_requested.connect(self._stop_proxy_by_id)

        self._set_nav_enabled(servers=True, characters=True, connection=True, skins=True, plugins=True, proxies=True)
        self.switch_page("login")
        self._load_saved_accounts()

    def switch_page(self, name: str) -> None:
        if name not in self.nav_buttons:
            return
        self.stack.setCurrentIndex(list(self.nav_buttons.keys()).index(name))
        for key, button in self.nav_buttons.items():
            button.setChecked(key == name)
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
            if not self.session.client:
                self.connection_page.set_direct_status("请先登录。", error=True)
                self.connection_page.set_proxy_status("请先登录。", error=True)
                self.connection_page.set_actions_enabled(can_join=False, can_proxy=False)
                return
            can_join = self.session.server is not None
            can_proxy = bool(self.session.server and self.session.character_name)
            if not self.session.server:
                self.connection_page.set_direct_status("请先选择服务器。", error=True)
                self.connection_page.set_proxy_status("请先选择服务器和角色。", error=True)
            elif not self.session.character_name:
                self.connection_page.set_direct_status("")
                self.connection_page.set_proxy_status("请先选择角色。", error=True)
            else:
                self.connection_page.set_direct_status("")
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
        elif name == "proxies":
            if not self.session.client:
                self.proxy_manager_page.set_status("请先登录。", error=True)
            self._refresh_proxy_manager()

    def _animate_page(self, widget: QtWidgets.QWidget) -> None:
        effect = QtWidgets.QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        anim = QtCore.QPropertyAnimation(effect, b"opacity", widget)
        anim.setDuration(260)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        anim.start(QtCore.QAbstractAnimation.DeleteWhenStopped)

    def _set_nav_enabled(
        self,
        *,
        servers: bool,
        characters: bool,
        connection: bool,
        skins: bool,
        plugins: bool,
        proxies: bool,
    ) -> None:
        self.nav_buttons["servers"].setEnabled(servers)
        self.nav_buttons["characters"].setEnabled(characters)
        self.nav_buttons["connection"].setEnabled(connection)
        self.nav_buttons["skins"].setEnabled(skins)
        self.nav_buttons["plugins"].setEnabled(plugins)
        self.nav_buttons["proxies"].setEnabled(proxies)

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
        self.proxy_manager_page.set_proxies(self._managed_proxies)
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
                self.proxy_manager_page.set_status(f"正在停止代理 #{proxy_id}...")
                self._stop_proxy_thread(proxy)
                return
        self.proxy_manager_page.set_status("未找到要关闭的代理。", error=True)

    def _stop_all_proxies(self) -> None:
        if not self._managed_proxies:
            self.connection_page.set_proxy_status("代理未运行。")
            self.proxy_manager_page.set_status("当前没有运行的代理。")
            return
        self.connection_page.set_proxy_status("正在停止全部代理...")
        self.proxy_manager_page.set_status("正在停止全部代理...")
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
        self.proxy_manager_page.set_status(f"代理 #{proxy.id} 启动失败。", error=True)
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
        self.proxy_manager_page.set_status(f"代理 #{proxy.id} 已停止。")

    def _load_saved_accounts(self, selected_id: str | None = None) -> None:
        self._saved_accounts = sorted(load_accounts(), key=lambda acc: acc.last_used, reverse=True)
        self.login_page.set_saved_accounts(self._saved_accounts, selected_id=selected_id)

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
        if account.mode == "cookie":
            self.login_page.cookie_radio.setChecked(True)
            self.login_page.cookie_path.setText(account.cookie_path)
        elif account.mode == "account":
            self.login_page.account_radio.setChecked(True)
            self.login_page.account_user.setText(account.username)
            self.login_page.account_pass.setText(account.password)
            self.login_page.remember_checkbox.setChecked(account.remember_password)
        account.last_used = time.time()
        save_accounts(self._saved_accounts)
        self._load_saved_accounts(selected_id=account.id)

    def _save_current_account(self) -> None:
        mode = self.login_page.login_mode()
        if mode == "cookie":
            path = self.login_page.cookie_path.text().strip() or "test_sauth"
            if not path:
                self.login_page.set_status("请填写登录凭据文件路径。", error=True)
                return
            account = SavedAccount.new_cookie(path)
        else:
            username = self.login_page.account_user.text().strip()
            if not username:
                self.login_page.set_status("请输入用户名。", error=True)
                return
            password = self.login_page.account_pass.text().strip()
            remember = self.login_page.remember_checkbox.isChecked()
            account = SavedAccount.new_account(username, password, remember)

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
        if mode == "cookie":
            path = self.login_page.cookie_path.text().strip() or "test_sauth"
            if not path:
                return
            existing = next((item for item in self._saved_accounts if item.mode == "cookie" and item.key == path), None)
            if existing:
                existing.cookie_path = path
                existing.last_used = time.time()
                save_accounts(self._saved_accounts)
                self._load_saved_accounts(selected_id=existing.id)
                return
            account = SavedAccount.new_cookie(path)
        else:
            username = self.login_page.account_user.text().strip()
            if not username:
                return
            remember = self.login_page.remember_checkbox.isChecked()
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

        self._saved_accounts.append(account)
        save_accounts(self._saved_accounts)
        self._load_saved_accounts(selected_id=account.id)

    def _handle_login(self) -> None:
        self.login_page.clear_status()
        self.login_page.set_busy(True)

        mode = self.login_page.login_mode()
        if mode == "cookie":
            path = self.login_page.cookie_path.text().strip() or "test_sauth"

            def task() -> tuple[WPFLauncherClient, AuthOtp]:
                client = WPFLauncherClient()
                raw = _read_text(path)
                cookie = _extract_cookie(raw)
                auth = client.login_with_cookie(cookie)
                return client, auth

        else:
            username = self.login_page.account_user.text().strip()
            password = self.login_page.account_pass.text().strip()

            def task() -> tuple[WPFLauncherClient, AuthOtp]:
                if not username or not password:
                    raise ValueError("请输入用户名和密码")
                client = WPFLauncherClient()
                sauth_json = login_with_password(username, password)
                auth = client.login_with_cookie(sauth_json)
                return client, auth

        def on_success(result: tuple[WPFLauncherClient, AuthOtp]) -> None:
            client, auth = result
            self.session = SessionState(client=client, auth=auth)
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
            self.status_label.setText(f"已登录：{auth.entity_id} ({auth.login_channel})")
            self._auto_save_account(mode)
            self._set_nav_enabled(servers=True, characters=True, connection=True, skins=True, plugins=True, proxies=True)
            self.switch_page("servers")

        def on_error(message: str) -> None:
            self.login_page.set_busy(False)
            self.login_page.set_status(message or "登录失败。", error=True)

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
            self._set_nav_enabled(servers=True, characters=True, connection=True, skins=True, plugins=True, proxies=True)

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
            self._set_nav_enabled(servers=True, characters=True, connection=True, skins=True, plugins=True, proxies=True)
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
            self.connection_page.set_direct_status("请输入服务器ID以加入。", error=True)
            return

        self.connection_page.set_direct_status("正在加入 Yggdrasil...", error=False)

        def task() -> tuple[bool, str]:
            profile, ygg_data = self._build_ygg_profile(include_mods=False)
            ygg = StandardYggdrasil.with_random_server(ygg_data)
            ok, err = ygg.join_server(profile, server_id)
            return ok, err or ""

        def on_success(result: tuple[bool, str]) -> None:
            ok, err = result
            if ok:
                self.connection_page.set_direct_status("Yggdrasil 加入成功。")
            else:
                self.connection_page.set_direct_status(f"加入失败：{err}", error=True)

        def on_error(message: str) -> None:
            self.connection_page.set_direct_status(message or "加入失败。", error=True)

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

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        for proxy in list(self._managed_proxies):
            if proxy.thread.isRunning():
                proxy.thread.stop()
        for proxy in list(self._managed_proxies):
            if proxy.thread.isRunning():
                proxy.thread.wait(2000)
        super().closeEvent(event)


def main() -> int:
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    app = QtWidgets.QApplication(sys.argv)
    app.setStyleSheet(build_stylesheet())
    window = MainWindow()
    get_plugin_manager().load_plugins(extras={"mode": "gui", "app": app, "window": window})
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
