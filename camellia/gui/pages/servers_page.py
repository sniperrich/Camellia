"""
Servers Page

Displays available game servers with search, pagination, and detail view.
"""

from __future__ import annotations

from typing import List, Optional

from PySide6 import QtCore, QtGui, QtWidgets

from ...models import NetGameItem, NetGameDetail, NetGameServerAddress
from ...utils import fuzzy_search_games
from ..widgets import InfoRow, ServerCard, InlineLoadingIndicator

PAGE_MARGIN = 4
PAGE_SPACING = 10


def _format_address(host: str, port: int) -> str:
    if not host or not port:
        return "不可用"
    return f"{host}:{port}"


def _format_timestamp(ts: float | int | None) -> str:
    if not ts:
        return "--"
    try:
        return QtCore.QDateTime.fromSecsSinceEpoch(int(ts)).toString("yyyy-MM-dd HH:mm")
    except (TypeError, ValueError):
        return "--"


class ServersPage(QtWidgets.QWidget):
    """
    Server browsing page with search and pagination.

    Displays a list of available game servers with details and allows
    server selection for gameplay.
    """

    server_selected = QtCore.Signal(object)
    load_more_requested = QtCore.Signal()
    continue_requested = QtCore.Signal()
    recent_server_requested = QtCore.Signal(str)

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

        left_widget = QtWidgets.QWidget()
        left_widget.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        left = QtWidgets.QVBoxLayout(left_widget)
        left.setContentsMargins(0, 0, 0, 0)
        left.setSpacing(10)
        self.search_input = QtWidgets.QLineEdit()
        self.search_input.setPlaceholderText("按名称或简介搜索，滚动到底自动加载更多")
        self.search_input.setClearButtonEnabled(True)
        self.search_input.textChanged.connect(lambda _=None: self._apply_filter())

        self.scroll_area = QtWidgets.QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.scroll_area.verticalScrollBar().valueChanged.connect(lambda value: self._handle_scroll(value))
        self.scroll_area.setMinimumWidth(520)
        self.cards_container = QtWidgets.QWidget()
        self.cards_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.setAlignment(QtCore.Qt.AlignTop)
        self.scroll_area.setWidget(self.cards_container)

        self.status_label = QtWidgets.QLabel("")
        self.loading_indicator = InlineLoadingIndicator("正在加载服务器...")
        self.loading_indicator.hide()
        self.status_label.setProperty("muted", "true")
        self.status_label.setWordWrap(True)

        left.addWidget(self.search_input)
        left.addWidget(self.scroll_area, 1)
        left.addWidget(self.status_label)

        right_widget = QtWidgets.QWidget()
        right_widget.setMinimumWidth(280)
        right_widget.setMaximumWidth(360)
        right = QtWidgets.QVBoxLayout(right_widget)
        right.setContentsMargins(0, 0, 0, 0)
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
        self.detail_summary.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
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

        self.recent_card = QtWidgets.QFrame()
        self.recent_card.setProperty("card", "true")
        recent_layout = QtWidgets.QVBoxLayout(self.recent_card)
        recent_layout.setContentsMargins(16, 14, 16, 14)
        recent_layout.setSpacing(8)

        self.recent_title = QtWidgets.QLabel("上次游玩")
        self.recent_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        self.recent_name = QtWidgets.QLabel("暂无记录")
        self.recent_name.setWordWrap(True)
        self.recent_name.setProperty("muted", "true")

        self.recent_version = InfoRow("版本", "--")
        self.recent_address = InfoRow("地址", "--")
        self.recent_time = InfoRow("时间", "--")
        self.recent_action = QtWidgets.QPushButton("选择该服务器")
        self.recent_action.setProperty("variant", "ghost")
        self.recent_action.clicked.connect(self._emit_recent_selected)

        recent_layout.addWidget(self.recent_title)
        recent_layout.addWidget(self.recent_name)
        recent_layout.addWidget(self.recent_version)
        recent_layout.addWidget(self.recent_address)
        recent_layout.addWidget(self.recent_time)
        recent_layout.addWidget(self.recent_action, alignment=QtCore.Qt.AlignLeft)

        right.addWidget(self.recent_card)
        right.addStretch(1)

        body.addWidget(left_widget, 4)
        body.addWidget(right_widget, 2)

        layout.addLayout(body, 1)

        self._servers: List[NetGameItem] = []
        self._cards: List[ServerCard] = []
        self._selected_server: Optional[NetGameItem] = None
        self._is_loading = False
        self._no_more = False
        self._load_threshold = 120
        # 保持对动画和特效的强引用，避免被 GC 导致渲染问题
        self._animations: List[QtCore.QPropertyAnimation] = []
        self._effects: List[QtWidgets.QGraphicsOpacityEffect] = []
        self._recent_server: Optional[dict] = None

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

    def set_recent_server(self, info: Optional[dict]) -> None:
        self._recent_server = info
        if not info:
            self.recent_name.setText("暂无记录")
            self.recent_name.setProperty("muted", "true")
            self.recent_version.set_value("--")
            self.recent_address.set_value("--")
            self.recent_time.set_value("--")
            self.recent_action.setEnabled(False)
            return
        name = (info.get("name") or "").strip()
        version = (info.get("version") or "").strip()
        address = (info.get("address") or "").strip()
        last_time = info.get("time")
        self.recent_name.setText(name or "未知服务器")
        self.recent_name.setProperty("muted", "false")
        self.recent_version.set_value(version or "--")
        self.recent_address.set_value(address or "--")
        self.recent_time.set_value(_format_timestamp(last_time))
        self.recent_action.setEnabled(bool(info.get("id")))

    def find_server_by_id(self, server_id: str) -> Optional[NetGameItem]:
        for server in self._servers:
            if server.entity_id == server_id:
                return server
        return None

    def _emit_recent_selected(self) -> None:
        if not self._recent_server:
            return
        server_id = (self._recent_server.get("id") or "").strip()
        if server_id:
            self.recent_server_requested.emit(server_id)

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
            servers = fuzzy_search_games(query, servers)

        # 清理旧的动画和特效引用
        self._animations.clear()
        self._effects.clear()

        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setGraphicsEffect(None)
                widget.setParent(None)
                widget.deleteLater()
        self._cards.clear()

        for idx, server in enumerate(servers):
            card = ServerCard(server)
            card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            card.selected.connect(self._on_card_selected)
            self._cards.append(card)
            self.cards_layout.addWidget(card)
            QtCore.QTimer.singleShot(idx * 30, lambda c=card: self._fade_in(c))

        self.cards_container.updateGeometry()
        self.cards_layout.update()

        if self._selected_server:
            for card in self._cards:
                card.set_selected(card.server == self._selected_server)

        QtCore.QTimer.singleShot(100, self._maybe_trigger_autoload)

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
            QtCore.QTimer.singleShot(idx * 30, lambda c=card: self._fade_in(c))

        self.cards_container.updateGeometry()
        self.cards_layout.update()

        QtCore.QTimer.singleShot(100, self._maybe_trigger_autoload)

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

    def _fade_in(self, widget: QtWidgets.QWidget) -> None:
        if not widget or not widget.parent():
            return
        effect = QtWidgets.QGraphicsOpacityEffect(widget)
        widget.setGraphicsEffect(effect)
        effect.setOpacity(0.0)
        self._effects.append(effect)

        anim = QtCore.QPropertyAnimation(effect, b"opacity", self)
        anim.setDuration(220)
        anim.setStartValue(0.0)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QtCore.QEasingCurve.OutCubic)
        self._animations.append(anim)

        def cleanup() -> None:
            try:
                if widget and widget.parent():
                    widget.setGraphicsEffect(None)
                if effect in self._effects:
                    self._effects.remove(effect)
                if anim in self._animations:
                    self._animations.remove(anim)
            except RuntimeError:
                pass

        anim.finished.connect(cleanup)
        anim.start()
