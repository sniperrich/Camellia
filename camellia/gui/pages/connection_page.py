"""
Connection Page

Manages proxy connections with automatic port allocation.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ..theme import PALETTE
from ..widgets import InfoRow, PortInputWithStatus, LoadingSpinner, MaterialComboBox
from ..utils import get_next_suggested_port

PAGE_MARGIN = 4
PAGE_SPACING = 10


def _format_address(host: str, port: int) -> str:
    if not host or not port:
        return "不可用"
    return f"{host}:{port}"


class ManagedProxy:
    """Placeholder for ManagedProxy - imported from app.py in actual usage."""
    pass


class ConnectionPage(QtWidgets.QWidget):
    """
    Connection management page with automatic port allocation.

    Manages local proxy creation and monitoring with intelligent
    port suggestion based on currently active proxies.
    """

    proxy_start_requested = QtCore.Signal(str, str)
    proxy_stop_requested = QtCore.Signal()
    proxy_close_requested = QtCore.Signal(int)
    session_switch_requested = QtCore.Signal(str)
    proxy_stop_session_requested = QtCore.Signal(str)

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)

        title = QtWidgets.QLabel("连接设置")
        title.setObjectName("Title")
        subtitle = QtWidgets.QLabel("仅使用本地代理进行连接与登录。")
        subtitle.setObjectName("Subtitle")

        layout.addWidget(title)
        layout.addWidget(subtitle)

        self.session_card = QtWidgets.QFrame()
        self.session_card.setProperty("card", "true")
        session_layout = QtWidgets.QVBoxLayout(self.session_card)
        session_layout.setContentsMargins(14, 12, 14, 12)
        session_layout.setSpacing(8)

        session_header = QtWidgets.QHBoxLayout()
        session_title = QtWidgets.QLabel("账号会话")
        session_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.session_summary = QtWidgets.QLabel("已登录 0 / 当前账号代理 0 / 总代理 0")
        self.session_summary.setProperty("muted", "true")
        session_header.addWidget(session_title)
        session_header.addStretch(1)
        session_header.addWidget(self.session_summary)
        session_layout.addLayout(session_header)

        session_row = QtWidgets.QHBoxLayout()
        self.session_combo = MaterialComboBox()
        self.session_combo.setSizeAdjustPolicy(QtWidgets.QComboBox.AdjustToContents)
        self.session_combo.setMinimumWidth(220)
        self.session_combo.currentIndexChanged.connect(self._on_session_selected)
        session_row.addWidget(self.session_combo, 2)

        self.session_switch_button = QtWidgets.QPushButton("切换")
        self.session_switch_button.setProperty("variant", "ghost")
        self.session_switch_button.clicked.connect(self._emit_session_switch)
        session_row.addWidget(self.session_switch_button)
        session_row.addStretch(1)
        session_layout.addLayout(session_row)

        action_row = QtWidgets.QHBoxLayout()
        self.quick_start_button = QtWidgets.QPushButton("一键启动代理")
        self.quick_start_button.setProperty("variant", "primary")
        self.quick_start_button.clicked.connect(self._emit_quick_start)
        action_row.addWidget(self.quick_start_button)

        self.session_close_button = QtWidgets.QPushButton("关闭该账号代理")
        self.session_close_button.setProperty("variant", "danger")
        self.session_close_button.clicked.connect(self._emit_stop_session_proxies)
        action_row.addWidget(self.session_close_button)
        action_row.addStretch(1)
        session_layout.addLayout(action_row)

        self.proxy_filter_check = QtWidgets.QCheckBox("仅显示当前账号代理")
        self.proxy_filter_check.setChecked(False)
        self.proxy_filter_check.toggled.connect(self._on_filter_changed)
        session_layout.addWidget(self.proxy_filter_check)

        self.info_card = QtWidgets.QFrame()
        self.info_card.setProperty("card", "true")
        info_layout = QtWidgets.QVBoxLayout(self.info_card)
        info_layout.setContentsMargins(14, 12, 14, 12)
        info_layout.setSpacing(8)

        self.info_name = InfoRow("服务器", "--")
        self.info_version = InfoRow("版本", "--")
        self.info_remote = InfoRow("远程地址", "--")

        info_layout.addWidget(self.info_name)
        info_layout.addWidget(self.info_version)
        info_layout.addWidget(self.info_remote)

        remote_actions = QtWidgets.QHBoxLayout()
        remote_actions.addStretch(1)
        self.copy_button = QtWidgets.QPushButton("复制远程地址")
        self.copy_button.setProperty("variant", "ghost")
        self.copy_button.clicked.connect(self._copy_remote)
        remote_actions.addWidget(self.copy_button)
        info_layout.addLayout(remote_actions)

        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(12)
        top_row.addWidget(self.session_card, 2)
        top_row.addWidget(self.info_card, 1)

        top_container = QtWidgets.QWidget()
        top_container.setLayout(top_row)

        body = QtWidgets.QHBoxLayout()
        body.setSpacing(18)

        left_widget = QtWidgets.QWidget()
        left = QtWidgets.QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(12)

        proxy_card = QtWidgets.QFrame()
        proxy_card.setProperty("card", "true")
        proxy_layout = QtWidgets.QVBoxLayout(proxy_card)
        proxy_layout.setContentsMargins(14, 12, 14, 12)
        proxy_layout.setSpacing(12)

        proxy_title = QtWidgets.QLabel("本地代理")
        proxy_title.setStyleSheet("font-size: 15px; font-weight: 600;")
        proxy_desc = QtWidgets.QLabel("启动代理后在游戏内连接本地地址即可进入服务器。")
        proxy_desc.setProperty("muted", "true")

        host_row = QtWidgets.QHBoxLayout()
        host_label = QtWidgets.QLabel("监听地址:")
        host_label.setProperty("muted", "true")
        self.local_host = QtWidgets.QLineEdit("127.0.0.1")
        self.local_host.setPlaceholderText("本地监听地址")
        host_row.addWidget(host_label)
        host_row.addWidget(self.local_host, 1)

        port_row = QtWidgets.QHBoxLayout()
        port_label = QtWidgets.QLabel("端口:")
        port_label.setProperty("muted", "true")
        # Use the new PortInputWithStatus widget with automatic port allocation
        self.port_input_widget = PortInputWithStatus(default_port=25570)
        self.port_input_widget.port_changed.connect(self._on_port_changed)
        port_row.addWidget(port_label)
        port_row.addWidget(self.port_input_widget, 1)

        # Add auto-suggest button
        self.auto_port_button = QtWidgets.QPushButton("自动分配端口")
        self.auto_port_button.setProperty("variant", "ghost")
        self.auto_port_button.clicked.connect(self._suggest_next_port)
        port_row.addWidget(self.auto_port_button)

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
        self.proxy_loading_spinner = LoadingSpinner(16)
        self.proxy_loading_spinner.hide()
        self.proxy_status.setWordWrap(True)
        self.proxy_status.setProperty("muted", "true")
        status_row = QtWidgets.QHBoxLayout()
        status_row.setSpacing(8)
        status_row.addWidget(self.proxy_loading_spinner)
        status_row.addWidget(self.proxy_status, 1)

        proxy_layout.addWidget(proxy_title)
        proxy_layout.addWidget(proxy_desc)
        proxy_layout.addLayout(host_row)
        proxy_layout.addLayout(port_row)
        proxy_layout.addLayout(button_row)
        proxy_layout.addLayout(status_row)

        proxy_list_card = QtWidgets.QFrame()
        proxy_list_card.setProperty("card", "true")
        proxy_list_layout = QtWidgets.QVBoxLayout(proxy_list_card)
        proxy_list_layout.setContentsMargins(14, 12, 14, 12)
        proxy_list_layout.setSpacing(10)

        header_row = QtWidgets.QHBoxLayout()
        self.proxy_count_label = QtWidgets.QLabel("运行中代理：0")
        self.proxy_count_label.setProperty("muted", "true")
        self.proxy_close_all = QtWidgets.QPushButton("关闭全部")
        self.proxy_close_all.setProperty("variant", "danger")
        self.proxy_close_all.clicked.connect(self.proxy_stop_requested.emit)
        header_row.addWidget(self.proxy_count_label)
        header_row.addStretch(1)
        header_row.addWidget(self.proxy_close_all)

        self.proxy_scroll = QtWidgets.QScrollArea()
        self.proxy_scroll.setWidgetResizable(True)
        self.proxy_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.proxy_cards_container = QtWidgets.QWidget()
        self.proxy_cards_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.proxy_cards_layout = QtWidgets.QVBoxLayout(self.proxy_cards_container)
        self.proxy_cards_layout.setContentsMargins(0, 0, 0, 0)
        self.proxy_cards_layout.setSpacing(10)
        self.proxy_cards_layout.setAlignment(QtCore.Qt.AlignTop)
        self.proxy_scroll.setWidget(self.proxy_cards_container)

        self.proxy_empty_label = QtWidgets.QLabel("暂无运行中的代理。")
        self.proxy_empty_label.setAlignment(QtCore.Qt.AlignCenter)
        self.proxy_empty_label.setProperty("muted", "true")

        self.proxy_list_status = QtWidgets.QLabel("")
        self.proxy_list_status.setWordWrap(True)
        self.proxy_list_status.setProperty("muted", "true")

        proxy_list_layout.addLayout(header_row)
        proxy_list_layout.addWidget(self.proxy_scroll, 1)
        proxy_list_layout.addWidget(self.proxy_empty_label)
        proxy_list_layout.addWidget(self.proxy_list_status)

        proxy_card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        left.addWidget(proxy_card, 1)

        body.addWidget(left_widget, 2)
        body.addWidget(proxy_list_card, 3)

        body_container = QtWidgets.QWidget()
        body_container.setLayout(body)

        splitter = QtWidgets.QSplitter(QtCore.Qt.Vertical)
        splitter.addWidget(top_container)
        splitter.addWidget(body_container)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)

        layout.addWidget(splitter, 1)

        self._proxies: list = []
        self._sessions: list = []
        self._session_map: dict[str, object] = {}
        self._active_session_id: str | None = None
        self._selected_session_id: str | None = None
        self._active_user_id: str | None = None
        self._proxy_group_state: dict[str, bool] = {}
        self._auto_port_enabled = True
        self._setting_port = False
        self._update_proxy_state()
        self._suggest_next_port(quiet=True)

    def _emit_proxy_start(self) -> None:
        port = str(self.port_input_widget.port_input.value())
        self.proxy_start_requested.emit(self.local_host.text().strip(), port)

    def _suggest_next_port(self, *, quiet: bool = False) -> None:
        """Automatically suggest the next available port based on active proxies."""
        used_ports = [proxy.local_port for proxy in self._proxies if hasattr(proxy, 'local_port')]
        suggested_port = get_next_suggested_port(used_ports, default_port=25570)
        self._setting_port = True
        try:
            self.port_input_widget.port_input.setValue(suggested_port)
        finally:
            self._setting_port = False
        self._auto_port_enabled = True
        if not quiet:
            self.set_proxy_status(f"已自动分配端口 {suggested_port}", error=False)

    def _on_port_changed(self, _port: int) -> None:
        if self._setting_port:
            return
        self._auto_port_enabled = False

    def _copy_remote(self) -> None:
        address = self.info_remote.value.text()
        if address and address not in ("不可用", "--"):
            QtGui.QGuiApplication.clipboard().setText(address)
            self.set_proxy_status("远程地址已复制。", error=False)

    def _update_proxy_state(self) -> None:
        visible = self._get_filtered_proxies() if self.proxy_filter_check.isChecked() else self._proxies
        has_items = bool(visible)
        self.proxy_scroll.setVisible(has_items)
        self.proxy_empty_label.setVisible(not has_items)
        self.proxy_close_all.setEnabled(has_items)
        total = len(self._proxies)
        if self.proxy_filter_check.isChecked():
            self.proxy_count_label.setText(f"运行中代理：{len(visible)} / {total}")
        else:
            self.proxy_count_label.setText(f"运行中代理：{total}")

    def _selected_user_id(self) -> str:
        if not self._selected_session_id:
            return ""
        session = self._session_map.get(self._selected_session_id)
        if not session or not getattr(session, "auth", None):
            return ""
        return str(session.auth.entity_id or "")

    def _get_filtered_proxies(self) -> list:
        proxies = list(self._proxies)
        if self.proxy_filter_check.isChecked():
            user_id = self._selected_user_id()
            if user_id:
                proxies = [p for p in proxies if getattr(p, "user_id", "") == user_id]
        return proxies

    def _update_session_summary(self) -> None:
        total_proxies = len(self._proxies)
        current_count = 0
        user_id = self._selected_user_id()
        if user_id:
            current_count = sum(1 for p in self._proxies if getattr(p, "user_id", "") == user_id)
        self.session_summary.setText(f"已登录 {len(self._sessions)} / 当前账号代理 {current_count} / 总代理 {total_proxies}")

    def _on_session_selected(self) -> None:
        self._selected_session_id = self.selected_session_id()
        self._update_session_summary()
        if self.proxy_filter_check.isChecked():
            self._render_proxy_cards()

    def _on_filter_changed(self) -> None:
        self._render_proxy_cards()

    def _emit_session_switch(self) -> None:
        session_id = self.selected_session_id()
        if session_id:
            self.session_switch_requested.emit(session_id)

    def _emit_stop_session_proxies(self) -> None:
        session_id = self.selected_session_id()
        if session_id:
            self.proxy_stop_session_requested.emit(session_id)

    def _emit_quick_start(self) -> None:
        session_id = self.selected_session_id()
        if session_id:
            self.session_switch_requested.emit(session_id)
            self.proxy_start_requested.emit(
                self.local_host.text().strip(),
                str(self.port_input_widget.port_input.value()),
            )

    def _toggle_group(self, user_id: str) -> None:
        current = self._proxy_group_state.get(user_id, False)
        self._proxy_group_state[user_id] = not current
        self._render_proxy_cards()

    def _render_proxy_cards(self) -> None:
        while self.proxy_cards_layout.count():
            item = self.proxy_cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        proxies = self._get_filtered_proxies()
        visible_count = len(proxies)
        if self.proxy_filter_check.isChecked():
            self.proxy_empty_label.setText("当前账号暂无运行中的代理。" if visible_count == 0 else "暂无运行中的代理。")
        else:
            self.proxy_empty_label.setText("暂无运行中的代理。")

        groups: dict[str, dict[str, object]] = {}
        for proxy in proxies:
            user_id = getattr(proxy, "user_id", "") or "unknown"
            entry = groups.setdefault(user_id, {"name": getattr(proxy, "user_account", "--"), "items": []})
            entry["items"].append(proxy)

        for user_id, data in groups.items():
            group_name = str(data.get("name") or "--")
            items = data.get("items", [])
            collapsed = getattr(self, "_proxy_group_state", {}).get(user_id, False)

            group_frame = QtWidgets.QFrame()
            group_layout = QtWidgets.QVBoxLayout(group_frame)
            group_layout.setContentsMargins(0, 0, 0, 0)
            group_layout.setSpacing(6)

            header_row = QtWidgets.QHBoxLayout()
            toggle = QtWidgets.QToolButton()
            toggle.setArrowType(QtCore.Qt.RightArrow if collapsed else QtCore.Qt.DownArrow)
            toggle.setStyleSheet("border: none;")
            toggle.clicked.connect(lambda checked=False, uid=user_id: self._toggle_group(uid))
            header_label = QtWidgets.QLabel(f"{group_name} · {len(items)}")
            if self._active_user_id and user_id == self._active_user_id:
                header_label.setStyleSheet(f"color: {PALETTE['accent']}; font-weight: 600;")
            else:
                header_label.setStyleSheet("font-weight: 600;")
            header_row.addWidget(toggle)
            header_row.addWidget(header_label)
            header_row.addStretch(1)
            group_layout.addLayout(header_row)

            if not collapsed:
                for proxy in items:
                    card = QtWidgets.QFrame()
                    card.setProperty("card", "true")
                    card_layout = QtWidgets.QVBoxLayout(card)
                    card_layout.setContentsMargins(12, 10, 12, 10)
                    card_layout.setSpacing(6)

                    header = QtWidgets.QHBoxLayout()
                    proxy_id = proxy.id if hasattr(proxy, 'id') else 0
                    title = QtWidgets.QLabel(f"代理 #{proxy_id}")
                    title.setStyleSheet("font-weight: 600;")
                    status = QtWidgets.QLabel(proxy.status if hasattr(proxy, 'status') else "未知状态")
                    status.setProperty("muted", "true")
                    status_text = proxy.status if hasattr(proxy, 'status') else ""
                    if "失败" in status_text:
                        status.setStyleSheet(f"color: {PALETTE['danger']};")
                    elif "启动" in status_text:
                        status.setStyleSheet(f"color: {PALETTE['warning']};")
                    else:
                        status.setStyleSheet(f"color: {PALETTE['accent']};")
                    header.addWidget(title)
                    header.addStretch(1)
                    header.addWidget(status)

                    info_stack = QtWidgets.QVBoxLayout()
                    info_stack.setSpacing(4)
                    nickname = proxy.nickname if hasattr(proxy, 'nickname') else "--"
                    local_addr = proxy.local_address() if hasattr(proxy, 'local_address') else "--"
                    forward_addr = proxy.forward_address() if hasattr(proxy, 'forward_address') else "--"
                    server_name = proxy.server_name if hasattr(proxy, 'server_name') else "--"
                    server_version = proxy.server_version if hasattr(proxy, 'server_version') else "--"

                    info_stack.addWidget(InfoRow("昵称", nickname))
                    info_stack.addWidget(InfoRow("本地地址", local_addr))
                    info_stack.addWidget(InfoRow("转发地址", forward_addr))
                    info_stack.addWidget(InfoRow("服务器", server_name))
                    info_stack.addWidget(InfoRow("版本", server_version))

                    actions = QtWidgets.QHBoxLayout()
                    copy_button = QtWidgets.QPushButton("复制地址")
                    copy_button.setProperty("variant", "ghost")
                    copy_button.clicked.connect(lambda checked=False, addr=local_addr: self._copy_address(addr))
                    close_button = QtWidgets.QPushButton("关闭")
                    close_button.setProperty("variant", "danger")
                    close_button.clicked.connect(lambda checked=False, pid=proxy_id: self.proxy_close_requested.emit(pid))
                    actions.addWidget(copy_button)
                    actions.addWidget(close_button)
                    actions.addStretch(1)

                    card_layout.addLayout(header)
                    card_layout.addLayout(info_stack)
                    card_layout.addLayout(actions)
                    group_layout.addWidget(card)

            self.proxy_cards_layout.addWidget(group_frame)

        self._update_proxy_state()

    def _copy_address(self, address: str) -> None:
        if not address or address == "不可用":
            self.set_proxy_manager_status("本地地址不可用。", error=True)
            return
        QtGui.QGuiApplication.clipboard().setText(address)
        self.set_proxy_manager_status("已复制本地地址。")
        QtCore.QTimer.singleShot(2000, lambda: self.set_proxy_manager_status(""))

    def set_proxies(self, proxies: list) -> None:
        """Set the list of managed proxies."""
        self._proxies = list(proxies)
        self._render_proxy_cards()
        self._update_session_summary()
        if self._auto_port_enabled:
            self._suggest_next_port(quiet=True)

    def set_sessions(self, sessions: list, *, selected_id: str | None = None) -> None:
        self._sessions = list(sessions)
        self._session_map = {s.session_id: s for s in self._sessions if hasattr(s, "session_id")}
        self._active_session_id = selected_id
        self._active_user_id = ""
        if selected_id and selected_id in self._session_map:
            session = self._session_map[selected_id]
            if getattr(session, "auth", None):
                self._active_user_id = str(session.auth.entity_id or "")

        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        if not self._sessions:
            self.session_combo.addItem("暂无已登录账号", "")
            self.session_combo.setEnabled(False)
            self.session_switch_button.setEnabled(False)
            self.session_close_button.setEnabled(False)
        else:
            self.session_combo.setEnabled(True)
            self.session_switch_button.setEnabled(True)
            self.session_close_button.setEnabled(True)
            for session in self._sessions:
                label = session.display_label() if hasattr(session, "display_label") else "未命名账号"
                server = getattr(session, "server", None)
                character = getattr(session, "character_name", None)
                detail_parts = []
                if server is not None:
                    name = getattr(server, "name", "") or ""
                    if name:
                        detail_parts.append(name)
                if character:
                    detail_parts.append(character)
                if detail_parts:
                    label = f"{label} · " + " / ".join(detail_parts)
                self.session_combo.addItem(label, session.session_id)
            if selected_id:
                idx = self.session_combo.findData(selected_id)
                if idx >= 0:
                    self.session_combo.setCurrentIndex(idx)
        self.session_combo.blockSignals(False)
        self._selected_session_id = self.selected_session_id()
        self._update_session_summary()
        self._render_proxy_cards()

    def set_server_info(self, name: str, version: str, remote: str) -> None:
        self.info_name.set_value(name or "--")
        self.info_version.set_value(version or "--")
        self.info_remote.set_value(remote or "--")

    def set_proxy_status(self, text: str, *, error: bool = False) -> None:
        self.proxy_status.setText(text)
        if error:
            self.proxy_status.setStyleSheet(f"color: {PALETTE['danger']};")
            self.proxy_loading_spinner.stop()
        else:
            self.proxy_status.setStyleSheet("")
            self.proxy_status.setProperty("muted", "true")
            # Show spinner for "正在" operations
            if text and "正在" in text:
                self.proxy_loading_spinner.start()
            else:
                self.proxy_loading_spinner.stop()

    def set_proxy_manager_status(self, text: str, *, error: bool = False) -> None:
        self.proxy_list_status.setText(text)
        if error:
            self.proxy_list_status.setStyleSheet(f"color: {PALETTE['danger']};")
        else:
            self.proxy_list_status.setStyleSheet("")
            self.proxy_list_status.setProperty("muted", "true")

    def set_proxy_running(self, running: bool) -> None:
        self.proxy_stop_button.setEnabled(running)

    def set_actions_enabled(self, *, can_join: bool, can_proxy: bool) -> None:
        self.proxy_start_button.setEnabled(can_proxy)

    def selected_session_id(self) -> str | None:
        data = self.session_combo.currentData()
        if not data:
            return None
        return str(data)
