"""
Character Page

Manages game character selection and creation.
"""

from __future__ import annotations

from typing import List

from PySide6 import QtCore, QtWidgets

from ..widgets import LoadingOverlay

from ...models import GameCharacter
from ..theme import PALETTE

PAGE_MARGIN = 4
PAGE_SPACING = 10


class CharacterPage(QtWidgets.QWidget):
    """
    Character management page.

    Allows users to view existing characters, create new ones,
    and select a character to continue with.
    """

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

        # Loading overlay
        self.loading_overlay = LoadingOverlay("正在创建角色...", self)
        self.loading_overlay.hide()

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
