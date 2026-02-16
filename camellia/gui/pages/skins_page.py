"""
Skin Page

Manages game skin browsing, searching, and application.
"""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from ...models import GameSkin
from ..theme import PALETTE
from ..widgets import SkinCard, InlineLoadingIndicator

PAGE_MARGIN = 4
PAGE_SPACING = 10


class SkinPage(QtWidgets.QWidget):
    """
    Skin management page.

    Allows users to browse available skins, search by name,
    and apply skins to their character.
    """

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
        self.scroll_area.verticalScrollBar().valueChanged.connect(lambda value: self._handle_scroll(value))
        self.cards_container = QtWidgets.QWidget()
        self.cards_container.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Preferred)
        self.cards_layout = QtWidgets.QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(12)
        self.cards_layout.setAlignment(QtCore.Qt.AlignTop)
        self.scroll_area.setWidget(self.cards_container)
        layout.addWidget(self.scroll_area, 1)

        self.status_label = QtWidgets.QLabel("")
        self.loading_indicator = InlineLoadingIndicator("正在加载皮肤...")
        self.loading_indicator.hide()
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", "true")
        layout.addWidget(self.status_label)
        layout.addWidget(self.loading_indicator)

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
