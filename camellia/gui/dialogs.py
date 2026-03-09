from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .widgets import CamelliaLogo


class LoginErrorDialog(QtWidgets.QDialog):
    def __init__(
        self,
        *,
        window_title: str = "登录失败",
        title: str,
        reason: str,
        code: int | None = None,
        verify_url: str | None = None,
        details: str | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(window_title)
        self.setModal(True)
        self.setMinimumWidth(420)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 16)
        layout.setSpacing(12)

        container = QtWidgets.QFrame()
        container.setProperty("card", "true")
        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setContentsMargins(16, 14, 16, 14)
        container_layout.setSpacing(12)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setSpacing(10)
        header_icon = CamelliaLogo(size=28)
        header_row.addWidget(header_icon, alignment=QtCore.Qt.AlignLeft)
        header = QtWidgets.QLabel(title)
        header.setStyleSheet("font-size: 16px; font-weight: 600;")
        header_row.addWidget(header)
        header_row.addStretch(1)
        container_layout.addLayout(header_row)

        card = QtWidgets.QFrame()
        card.setProperty("card", "true")
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(14, 12, 14, 12)
        card_layout.setSpacing(8)

        reason_label = QtWidgets.QLabel(reason or "未知错误")
        reason_label.setWordWrap(True)
        reason_label.setProperty("error", "true")
        card_layout.addWidget(reason_label)

        if code is not None:
            code_label = QtWidgets.QLabel(f"错误码：{code}")
            code_label.setProperty("muted", "true")
            card_layout.addWidget(code_label)

        if verify_url:
            url_label = QtWidgets.QLabel("安全验证链接：")
            url_label.setProperty("muted", "true")
            card_layout.addWidget(url_label)

            url_text = QtWidgets.QPlainTextEdit(verify_url)
            url_text.setReadOnly(True)
            url_text.setMinimumHeight(60)
            url_text.setStyleSheet("border-radius: 10px;")
            card_layout.addWidget(url_text)

            url_buttons = QtWidgets.QHBoxLayout()
            url_buttons.setSpacing(8)

            open_btn = QtWidgets.QPushButton("打开链接")
            open_btn.setProperty("variant", "ghost")
            open_btn.clicked.connect(lambda: QtGui.QDesktopServices.openUrl(QtCore.QUrl(verify_url)))
            url_buttons.addWidget(open_btn)

            copy_btn = QtWidgets.QPushButton("复制链接")
            copy_btn.setProperty("variant", "ghost")
            copy_btn.clicked.connect(lambda: QtGui.QGuiApplication.clipboard().setText(verify_url))
            url_buttons.addWidget(copy_btn)
            url_buttons.addStretch(1)
            card_layout.addLayout(url_buttons)

        if details:
            details = details.strip()
        if details:
            toggle = QtWidgets.QToolButton()
            toggle.setText("查看详情")
            toggle.setCheckable(True)
            toggle.setChecked(False)
            toggle.setProperty("variant", "ghost")
            toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextOnly)
            card_layout.addWidget(toggle, alignment=QtCore.Qt.AlignLeft)

            details_box = QtWidgets.QPlainTextEdit(details)
            details_box.setReadOnly(True)
            details_box.setVisible(False)
            details_box.setMinimumHeight(120)
            details_box.setStyleSheet("border-radius: 10px;")
            card_layout.addWidget(details_box)

            def _set_visible(checked: bool) -> None:
                details_box.setVisible(bool(checked))
                toggle.setText("收起详情" if checked else "查看详情")

            toggle.toggled.connect(_set_visible)

        container_layout.addWidget(card)

        buttons = QtWidgets.QHBoxLayout()
        buttons.addStretch(1)
        ok_button = QtWidgets.QPushButton("知道了")
        ok_button.setProperty("variant", "primary")
        ok_button.clicked.connect(self.accept)
        buttons.addWidget(ok_button)
        container_layout.addLayout(buttons)

        layout.addWidget(container)
