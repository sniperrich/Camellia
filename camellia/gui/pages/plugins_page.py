"""
Plugins Page

Manages plugin discovery, loading, and state management.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ...plugins import PluginState
from ..theme import PALETTE
from ..widgets import PluginCard

PAGE_MARGIN = 4
PAGE_SPACING = 10


class PluginsPage(QtWidgets.QWidget):
    """
    Plugin management page.

    Displays available plugins, allows enabling/disabling them,
    and provides access to the plugins directory.
    """

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
