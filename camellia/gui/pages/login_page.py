"""
Login Page

Handles user authentication via multiple methods with Material 3 design.
"""

from __future__ import annotations

from typing import Optional

from PySide6 import QtCore, QtWidgets

from ..theme import PALETTE
from ..storage import SavedAccount
from ..widgets import CamelliaLogo, LoadingOverlay

PAGE_MARGIN = 10
PAGE_SPACING = 10


class SessionState:
    """Placeholder for SessionState - imported from app.py in actual usage."""
    pass


class LoginPage(QtWidgets.QWidget):
    """
    Login page for user authentication with Material 3 design.

    Supports multiple login methods and manages saved accounts and active sessions.
    """

    login_clicked = QtCore.Signal()
    send_sms_clicked = QtCore.Signal()
    saved_login_clicked = QtCore.Signal()
    session_switch_clicked = QtCore.Signal()
    session_logout_clicked = QtCore.Signal()

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN, PAGE_MARGIN)
        layout.setSpacing(PAGE_SPACING)

        # Header section with Camellia branding
        header_row = QtWidgets.QHBoxLayout()
        header_row.setSpacing(10)

        logo = CamelliaLogo(size=28)
        header_row.addWidget(logo, alignment=QtCore.Qt.AlignLeft)

        header = QtWidgets.QVBoxLayout()
        header.setSpacing(4)
        title = QtWidgets.QLabel("登录")
        title.setObjectName("Title")
        subtitle = QtWidgets.QLabel("选择登录方式，开始你的 Minecraft 之旅")
        subtitle.setObjectName("Subtitle")
        header.addWidget(title)
        header.addWidget(subtitle)
        header_row.addLayout(header)
        header_row.addStretch(1)

        layout.addLayout(header_row)

        # Loading overlay
        self.loading_overlay = LoadingOverlay("正在登录...", self)
        self.loading_overlay.hide()

        # Main content area with cards
        content_layout = QtWidgets.QHBoxLayout()
        content_layout.setSpacing(10)
        content_layout.setAlignment(QtCore.Qt.AlignTop)

        left_container = QtWidgets.QWidget()
        left_layout = QtWidgets.QVBoxLayout(left_container)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(10)

        login_card = self._create_login_card()
        # Allow the login card to grow with the selected login form (phone form is taller).
        login_card.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Expanding)
        left_layout.addWidget(login_card, 1)

        insight_card = self._create_insight_card()
        insight_card.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)
        left_layout.addWidget(insight_card)

        left_layout.addStretch(1)

        accounts_card = self._create_accounts_card()
        accounts_card.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Maximum)

        content_layout.addWidget(left_container, 2)
        content_layout.addWidget(accounts_card, 2)

        layout.addLayout(content_layout, 1)

        self._update_saved_actions()
        self._update_session_actions()

    def _create_login_card(self) -> QtWidgets.QFrame:
        """Create the main login form card."""
        card = QtWidgets.QFrame()
        card.setProperty("card", "true")
        
        layout = QtWidgets.QVBoxLayout(card)
        # Slightly larger padding to reduce crowding between sections.
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        # Card title
        card_title = QtWidgets.QLabel("账号登录")
        card_title.setStyleSheet("font-size: 14px; font-weight: 600;")
        layout.addWidget(card_title)

        # Login method tabs (Material 3 style)
        tab_container = QtWidgets.QWidget()
        tab_layout = QtWidgets.QHBoxLayout(tab_container)
        tab_layout.setContentsMargins(0, 0, 0, 0)
        tab_layout.setSpacing(8)
        
        self.account_tabs: list[QtWidgets.QPushButton] = []
        for label in ("4399 账号", "网易邮箱", "网易手机号", "SAuth"):
            button = QtWidgets.QPushButton(label)
            button.setCheckable(True)
            button.setProperty("variant", "seg")
            self.account_tabs.append(button)
            tab_layout.addWidget(button)
        
        tab_layout.addStretch()
        layout.addWidget(tab_container)

        self.account_group = QtWidgets.QButtonGroup(self)
        self.account_group.setExclusive(True)
        for idx, button in enumerate(self.account_tabs):
            self.account_group.addButton(button, idx)
        self.account_tabs[0].setChecked(True)
        self.account_group.buttonClicked.connect(
            lambda button: self._on_account_type_changed(self.account_group.id(button))
        )

        # Form stack
        self.account_stack = QtWidgets.QStackedWidget()
        self.account_stack.addWidget(self._build_4399_form())
        self.account_stack.addWidget(self._build_netease_email_form())
        self.account_stack.addWidget(self._build_netease_phone_form())
        self.account_stack.addWidget(self._build_sauth_form())
        layout.addWidget(self.account_stack)

        # Remark (optional) for saved accounts
        self.remark_input = QtWidgets.QLineEdit()
        self.remark_input.setPlaceholderText("备注（可选，例如：小号 / 朋友 / 地区）")
        self.remark_input.setMinimumHeight(34)
        layout.addWidget(self.remark_input)

        # Options row
        options = QtWidgets.QHBoxLayout()
        options.setSpacing(12)
        options.addStretch()

        self.save_button = QtWidgets.QPushButton("保存账号")
        self.save_button.setProperty("variant", "ghost")
        options.addWidget(self.save_button)

        layout.addLayout(options)

        # Login button (prominent)
        self.login_button = QtWidgets.QPushButton("登录")
        self.login_button.setProperty("variant", "primary")
        self.login_button.setMinimumHeight(30)
        self.login_button.clicked.connect(self.login_clicked.emit)
        layout.addWidget(self.login_button)

        # Status message
        self.status_label = QtWidgets.QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setProperty("muted", "true")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(self.status_label)

        return card

    def _create_accounts_card(self) -> QtWidgets.QFrame:
        """Create the saved accounts and sessions card."""
        card = QtWidgets.QFrame()
        card.setProperty("card", "true")
        
        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        # Saved accounts section
        saved_title = QtWidgets.QLabel("已保存账号")
        saved_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(saved_title)

        self.saved_list = QtWidgets.QListWidget()
        self.saved_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.saved_list.itemDoubleClicked.connect(lambda *_: self.saved_login_clicked.emit())
        self.saved_list.itemSelectionChanged.connect(self._update_saved_actions)
        layout.addWidget(self.saved_list, 1)

        hint = QtWidgets.QLabel("双击快速登录")
        hint.setProperty("muted", "true")
        hint.setStyleSheet("font-size: 11px;")
        layout.addWidget(hint)

        # Saved account actions
        saved_actions = QtWidgets.QHBoxLayout()
        saved_actions.setSpacing(8)
        
        self.saved_login_button = QtWidgets.QPushButton("登录")
        self.saved_login_button.setProperty("variant", "primary")
        self.saved_login_button.clicked.connect(self.saved_login_clicked.emit)
        saved_actions.addWidget(self.saved_login_button)
        
        self.remove_saved_button = QtWidgets.QPushButton("删除")
        self.remove_saved_button.setProperty("variant", "ghost")
        saved_actions.addWidget(self.remove_saved_button)
        
        layout.addLayout(saved_actions)

        # Divider
        divider = QtWidgets.QFrame()
        divider.setFrameShape(QtWidgets.QFrame.HLine)
        divider.setStyleSheet(f"background: {PALETTE['border']}; max-height: 1px;")
        layout.addWidget(divider)

        # Active sessions section
        session_title = QtWidgets.QLabel("已登录账号")
        session_title.setStyleSheet("font-size: 13px; font-weight: 600;")
        layout.addWidget(session_title)

        self.session_list = QtWidgets.QListWidget()
        self.session_list.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.session_list.itemSelectionChanged.connect(self._update_session_actions)
        self.session_list.itemDoubleClicked.connect(lambda *_: self.session_switch_clicked.emit())
        layout.addWidget(self.session_list, 1)

        # Session actions
        session_actions = QtWidgets.QHBoxLayout()
        session_actions.setSpacing(8)
        
        self.session_switch_button = QtWidgets.QPushButton("切换")
        self.session_switch_button.setProperty("variant", "primary")
        self.session_switch_button.clicked.connect(self.session_switch_clicked.emit)
        session_actions.addWidget(self.session_switch_button)
        
        self.session_logout_button = QtWidgets.QPushButton("退出")
        self.session_logout_button.setProperty("variant", "ghost")
        self.session_logout_button.clicked.connect(self.session_logout_clicked.emit)
        session_actions.addWidget(self.session_logout_button)
        
        layout.addLayout(session_actions)

        return card

    def _create_insight_card(self) -> QtWidgets.QFrame:
        """Create a compact insight card to balance the layout."""
        card = QtWidgets.QFrame()
        card.setProperty("card", "true")

        layout = QtWidgets.QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(6)

        header_row = QtWidgets.QHBoxLayout()
        logo = CamelliaLogo(size=18)
        title = QtWidgets.QLabel("Camellia 小贴士")
        title.setStyleSheet("font-size: 13px; font-weight: 600;")
        badge = QtWidgets.QLabel("Tips")
        badge.setProperty("status", "info")
        badge.setStyleSheet("font-size: 11px;")
        header_row.addWidget(logo)
        header_row.addWidget(title)
        header_row.addStretch(1)
        header_row.addWidget(badge)
        layout.addLayout(header_row)

        tips = [
            "Camellia官方卡网:ayuncc.cfd",
            "启动本地代理后，用本地地址进入服务器。",
            "支持多账号并行登录，可在右侧会话列表切换。",
            "代理默认启动端口在25570，可自行调整。",
            "官方QQ群:572461756"

        ]
        for line in tips:
            label = QtWidgets.QLabel(f"• {line}")
            label.setWordWrap(True)
            label.setProperty("muted", "true")
            label.setStyleSheet("font-size: 12px;")
            layout.addWidget(label)

        return card

    def _build_4399_form(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        self.account_user = QtWidgets.QLineEdit()
        self.account_user.setPlaceholderText("4399 用户名")
        self.account_user.setMinimumHeight(38)
        layout.addWidget(self.account_user)

        self.account_pass = QtWidgets.QLineEdit()
        self.account_pass.setPlaceholderText("密码")
        self.account_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        self.account_pass.setMinimumHeight(38)
        self.account_pass.returnPressed.connect(self.login_clicked.emit)
        layout.addWidget(self.account_pass)

        layout.addStretch()
        return widget

    def _build_netease_email_form(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        self.netease_email = QtWidgets.QLineEdit()
        self.netease_email.setPlaceholderText("网易邮箱")
        self.netease_email.setMinimumHeight(38)
        layout.addWidget(self.netease_email)

        self.netease_pass = QtWidgets.QLineEdit()
        self.netease_pass.setPlaceholderText("密码")
        self.netease_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        self.netease_pass.setMinimumHeight(38)
        self.netease_pass.returnPressed.connect(self.login_clicked.emit)
        layout.addWidget(self.netease_pass)

        layout.addStretch()
        return widget

    def _build_netease_phone_form(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(16)

        # Phone login method switch: SMS code or password (phone@163.com via email login).
        self.netease_phone = QtWidgets.QLineEdit()
        self.netease_phone.setPlaceholderText("手机号")
        self.netease_phone.setMinimumHeight(38)
        phone_tabs_container = QtWidgets.QWidget()
        phone_tabs_layout = QtWidgets.QHBoxLayout(phone_tabs_container)
        phone_tabs_layout.setContentsMargins(0, 0, 0, 0)
        phone_tabs_layout.setSpacing(8)

        self.netease_phone_tabs: list[QtWidgets.QPushButton] = []
        for label in ("短信验证码", "密码登录"):
            button = QtWidgets.QPushButton(label)
            button.setCheckable(True)
            button.setProperty("variant", "seg")
            self.netease_phone_tabs.append(button)
            phone_tabs_layout.addWidget(button)
        phone_tabs_layout.addStretch(1)

        self.netease_phone_group = QtWidgets.QButtonGroup(self)
        self.netease_phone_group.setExclusive(True)
        for idx, button in enumerate(self.netease_phone_tabs):
            self.netease_phone_group.addButton(button, idx)
        self.netease_phone_tabs[0].setChecked(True)

        layout.addWidget(phone_tabs_container)
        layout.addSpacing(8)
        layout.addWidget(self.netease_phone)
        layout.addSpacing(8)

        self.netease_phone_stack = QtWidgets.QStackedWidget()

        # SMS code page
        sms_page = QtWidgets.QWidget()
        sms_layout = QtWidgets.QHBoxLayout(sms_page)
        sms_layout.setContentsMargins(0, 0, 0, 0)
        sms_layout.setSpacing(8)

        self.netease_code = QtWidgets.QLineEdit()
        self.netease_code.setPlaceholderText("验证码")
        self.netease_code.setMinimumHeight(38)
        self.netease_code.returnPressed.connect(self.login_clicked.emit)
        sms_layout.addWidget(self.netease_code, 2)

        self.sms_send_button = QtWidgets.QPushButton("发送验证码")
        self.sms_send_button.setProperty("variant", "ghost")
        self.sms_send_button.setMinimumHeight(38)
        self.sms_send_button.clicked.connect(self.send_sms_clicked.emit)
        sms_layout.addWidget(self.sms_send_button, 1)

        self.netease_phone_stack.addWidget(sms_page)

        # Password page
        pwd_page = QtWidgets.QWidget()
        pwd_layout = QtWidgets.QVBoxLayout(pwd_page)
        pwd_layout.setContentsMargins(0, 0, 0, 0)
        pwd_layout.setSpacing(8)

        self.netease_phone_pass = QtWidgets.QLineEdit()
        self.netease_phone_pass.setPlaceholderText("密码（将使用 手机号@163.com 登录）")
        self.netease_phone_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        self.netease_phone_pass.setMinimumHeight(38)
        self.netease_phone_pass.returnPressed.connect(self.login_clicked.emit)
        pwd_layout.addWidget(self.netease_phone_pass)

        self.netease_phone_stack.addWidget(pwd_page)

        layout.addWidget(self.netease_phone_stack)

        self.netease_phone_group.buttonClicked.connect(
            lambda button: self.netease_phone_stack.setCurrentIndex(self.netease_phone_group.id(button))
        )
        layout.addStretch()
        return widget

    def _build_sauth_form(self) -> QtWidgets.QWidget:
        widget = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(10)

        self.sauth_input = QtWidgets.QPlainTextEdit()
        self.sauth_input.setPlaceholderText("粘贴 sauth_json（完整 JSON 文本）")
        self.sauth_input.setMinimumHeight(120)
        layout.addWidget(self.sauth_input)

        hint = QtWidgets.QLabel("将直接使用 sauth_json 调用登录流程。")
        hint.setProperty("muted", "true")
        hint.setStyleSheet("font-size: 11px;")
        layout.addWidget(hint)

        layout.addStretch()
        return widget

    def _on_account_type_changed(self, index: int) -> None:
        self.account_stack.setCurrentIndex(index)

    def _update_saved_actions(self) -> None:
        has_selection = bool(self.saved_list.selectedItems())
        self.saved_login_button.setEnabled(has_selection)
        self.remove_saved_button.setEnabled(has_selection)

    def _update_session_actions(self) -> None:
        has_selection = bool(self.session_list.selectedItems())
        self.session_switch_button.setEnabled(has_selection)
        self.session_logout_button.setEnabled(has_selection)

    def login_mode(self) -> str:
        index = self.account_stack.currentIndex()
        return ["account", "netease_email", "netease_phone", "sauth"][index]

    def select_account_type(self, index: int) -> None:
        if 0 <= index < len(self.account_tabs):
            self.account_tabs[index].setChecked(True)
            self.account_stack.setCurrentIndex(index)

    def set_status(self, message: str, *, error: bool = False) -> None:
        self.status_label.setText(message)
        if error:
            self.status_label.setProperty("error", "true")
            self.status_label.setProperty("muted", "false")
        else:
            self.status_label.setProperty("error", None)
            self.status_label.setProperty("muted", "true")
        self.status_label.style().unpolish(self.status_label)
        self.status_label.style().polish(self.status_label)

    def clear_status(self) -> None:
        self.status_label.setText("")

    def set_busy(self, busy: bool) -> None:
        self.login_button.setEnabled(not busy)
        for button in self.account_tabs:
            button.setEnabled(not busy)
        self.saved_list.setEnabled(not busy)
        self.session_list.setEnabled(not busy)
        if busy:
            self.saved_login_button.setEnabled(False)
            self.remove_saved_button.setEnabled(False)
            self.session_switch_button.setEnabled(False)
            self.session_logout_button.setEnabled(False)
            self.loading_overlay.show_loading("正在验证...")
        else:
            self.loading_overlay.hide_loading()
            self._update_saved_actions()
            self._update_session_actions()
        self.save_button.setEnabled(not busy)
        self.account_user.setEnabled(not busy)
        self.account_pass.setEnabled(not busy)
        self.netease_email.setEnabled(not busy)
        self.netease_pass.setEnabled(not busy)
        self.netease_phone.setEnabled(not busy)
        self.netease_code.setEnabled(not busy)
        self.sms_send_button.setEnabled(not busy)
        if hasattr(self, "sauth_input"):
            self.sauth_input.setEnabled(not busy)
        if hasattr(self, "netease_phone_pass"):
            self.netease_phone_pass.setEnabled(not busy)
        if hasattr(self, "netease_phone_tabs"):
            for button in self.netease_phone_tabs:
                button.setEnabled(not busy)
        self.remark_input.setEnabled(not busy)

    def netease_phone_login_mode(self) -> str:
        """Return current phone login mode: 'sms' or 'password'."""
        if hasattr(self, "netease_phone_stack") and self.netease_phone_stack.currentIndex() == 1:
            return "password"
        return "sms"

    def set_netease_phone_login_mode(self, mode: str) -> None:
        """Set phone login mode: 'sms' or 'password'."""
        if not hasattr(self, "netease_phone_stack"):
            return
        idx = 1 if mode == "password" else 0
        self.netease_phone_stack.setCurrentIndex(idx)
        if hasattr(self, "netease_phone_tabs") and 0 <= idx < len(self.netease_phone_tabs):
            self.netease_phone_tabs[idx].setChecked(True)

    def set_saved_accounts(self, accounts: list[SavedAccount], *, selected_id: str | None = None) -> None:
        self.saved_list.clear()
        for account in accounts:
            label = account.label
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, account.id)
            self.saved_list.addItem(item)
            if selected_id and account.id == selected_id:
                item.setSelected(True)
        self._update_saved_actions()

    def set_sessions(self, sessions: list, *, selected_id: str | None = None) -> None:
        self.session_list.clear()
        for session in sessions:
            label = session.display_label()
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
            item = QtWidgets.QListWidgetItem(label)
            item.setData(QtCore.Qt.UserRole, session.session_id)
            self.session_list.addItem(item)
            if selected_id and session.session_id == selected_id:
                item.setSelected(True)
        self._update_session_actions()

    def selected_saved_id(self) -> str | None:
        items = self.saved_list.selectedItems()
        if not items:
            return None
        return items[0].data(QtCore.Qt.UserRole)

    def selected_session_id(self) -> str | None:
        items = self.session_list.selectedItems()
        if not items:
            return None
        return items[0].data(QtCore.Qt.UserRole)
