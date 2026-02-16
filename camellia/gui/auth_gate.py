from __future__ import annotations

import time
import uuid
from PySide6 import QtCore, QtWidgets

from ..api.auth_backend import AuthBackend
from .widgets import CamelliaLogo
from .theme import PALETTE
from .settings import get_settings
from .workers import Worker


class AuthGateDialog(QtWidgets.QDialog):
    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Camellia 访问验证")
        self.setModal(True)
        self.setMinimumSize(520, 420)
        self.setStyleSheet(f"QDialog {{ background: {PALETTE['panel']}; }}")

        self.settings = get_settings()
        self.backend = AuthBackend(self.settings.get("auth_base_url", "https://api.taylorswift.fit"))
        self.thread_pool = QtCore.QThreadPool.globalInstance()
        self.device_id = self.settings.get("auth_device_id", "")
        if not self.device_id:
            self.device_id = uuid.uuid4().hex
            self.settings.set("auth_device_id", self.device_id)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        card = QtWidgets.QFrame()
        card.setProperty("card", "true")
        card.setProperty("auth_card", "true")
        card.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(26, 22, 26, 20)
        card_layout.setSpacing(14)

        header_row = QtWidgets.QHBoxLayout()
        header_row.setSpacing(10)
        header_row.addWidget(CamelliaLogo(size=30), alignment=QtCore.Qt.AlignLeft)
        header_text = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("访问验证")
        title.setStyleSheet("font-size: 16px; font-weight: 600;")
        subtitle = QtWidgets.QLabel("登录 / 注册 / 激活后继续使用")
        subtitle.setObjectName("Subtitle")
        header_text.addWidget(title)
        header_text.addWidget(subtitle)
        header_row.addLayout(header_text)
        header_row.addStretch(1)
        card_layout.addLayout(header_row)

        # Tabs
        tab_row = QtWidgets.QHBoxLayout()
        tab_row.setSpacing(8)
        self.tab_buttons: list[QtWidgets.QPushButton] = []
        self.tab_group = QtWidgets.QButtonGroup(self)
        for idx, label in enumerate(("登录", "注册", "激活")):
            btn = QtWidgets.QPushButton(label)
            btn.setCheckable(True)
            btn.setProperty("variant", "seg")
            btn.setMinimumHeight(28)
            self.tab_group.addButton(btn, idx)
            self.tab_buttons.append(btn)
            tab_row.addWidget(btn)
        self.tab_buttons[0].setChecked(True)
        tab_row.addStretch(1)
        card_layout.addLayout(tab_row)

        self.stack = QtWidgets.QStackedWidget()
        card_layout.addWidget(self.stack, 1)

        self.login_panel = self._build_login_panel()
        self.register_panel = self._build_register_panel()
        self.activate_panel = self._build_activate_panel()
        self.stack.addWidget(self.login_panel)
        self.stack.addWidget(self.register_panel)
        self.stack.addWidget(self.activate_panel)

        footer = QtWidgets.QHBoxLayout()
        footer.addStretch(1)
        self.primary_button = QtWidgets.QPushButton("进入")
        self.primary_button.setProperty("variant", "primary")
        footer.addWidget(self.primary_button)
        card_layout.addLayout(footer)

        layout.addWidget(card)

        self.primary_button.clicked.connect(self._handle_primary)
        self.tab_group.buttonClicked.connect(self._switch_tab)

        # Default status label (shared)
        self.status = QtWidgets.QLabel("")
        self.status.setProperty("error", "false")
        card_layout.addWidget(self.status)

        self._inputs: list[QtWidgets.QWidget] = [
            self.user_input,
            self.pass_input,
            self.remember_auth,
            self.reg_user,
            self.reg_pass,
            self.reg_pass2,
            self.reg_contact,
            self.act_user,
            self.act_key,
        ]

        self._load_saved_auth_credentials()

        if self.settings.get("auth_auto_login", False):
            self._try_auto_login()

    def _build_login_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.user_input = QtWidgets.QLineEdit()
        self.user_input.setPlaceholderText("账号")
        layout.addWidget(self.user_input)

        self.pass_input = QtWidgets.QLineEdit()
        self.pass_input.setPlaceholderText("密码")
        self.pass_input.setEchoMode(QtWidgets.QLineEdit.Password)
        layout.addWidget(self.pass_input)

        self.remember_auth = QtWidgets.QCheckBox("记住账号密码（明文存储）")
        self.remember_auth.setChecked(bool(self.settings.get("auth_remember_password", False)))
        layout.addWidget(self.remember_auth)

        return panel

    def _build_register_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.reg_user = QtWidgets.QLineEdit()
        self.reg_user.setPlaceholderText("账号")
        layout.addWidget(self.reg_user)

        self.reg_pass = QtWidgets.QLineEdit()
        self.reg_pass.setPlaceholderText("密码")
        self.reg_pass.setEchoMode(QtWidgets.QLineEdit.Password)
        layout.addWidget(self.reg_pass)

        self.reg_pass2 = QtWidgets.QLineEdit()
        self.reg_pass2.setPlaceholderText("确认密码")
        self.reg_pass2.setEchoMode(QtWidgets.QLineEdit.Password)
        layout.addWidget(self.reg_pass2)

        self.reg_contact = QtWidgets.QLineEdit()
        self.reg_contact.setPlaceholderText("邮箱/手机号（可选）")
        layout.addWidget(self.reg_contact)

        # hint = QtWidgets.QLabel("注册功能待接入后端，当前仅展示界面。")
        # hint.setProperty("muted", "true")
        # layout.addWidget(hint)
        return panel

    def _build_activate_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.act_user = QtWidgets.QLineEdit()
        self.act_user.setPlaceholderText("账号")
        layout.addWidget(self.act_user)

        self.act_key = QtWidgets.QLineEdit()
        self.act_key.setPlaceholderText("卡密 / 激活码")
        layout.addWidget(self.act_key)

        hint = QtWidgets.QLabel("激活成功后即可登录。")
        hint.setProperty("muted", "true")
        layout.addWidget(hint)
        return panel

    @QtCore.Slot()
    def _switch_tab(self) -> None:
        index = self.tab_group.checkedId()
        if index < 0:
            index = 0
        self.stack.setCurrentIndex(index)
        if index == 0:
            self.primary_button.setText("进入")
        elif index == 1:
            self.primary_button.setText("注册")
        else:
            self.primary_button.setText("激活")
        self.status.setText("")

    @QtCore.Slot()
    def _handle_primary(self) -> None:
        index = self.stack.currentIndex()
        if index == 0:
            username = self.user_input.text().strip()
            password = self.pass_input.text()
            if not username or not password:
                self._set_status("请输入账号与密码。", error=True)
                return
            self._set_busy(True, "正在登录…")
            self._run_task(
                lambda: self.backend.login(username, password, self.device_id),
                self._on_login,
            )
            return

        if index == 1:
            username = self.reg_user.text().strip()
            password = self.reg_pass.text()
            password2 = self.reg_pass2.text()
            if not username or not password:
                self._set_status("请输入账号与密码。", error=True)
                return
            if password != password2:
                self._set_status("两次输入密码不一致。", error=True)
                return
            self._set_busy(True, "正在注册…")
            self._run_task(
                lambda: self.backend.register(username, password),
                self._on_register,
            )
            return

        if index == 2:
            code = self.act_key.text().strip()
            username = self.act_user.text().strip()
            device = self.device_id
            if not username:
                self._set_status("请输入账号。", error=True)
                return
            if not code:
                self._set_status("请输入卡密。", error=True)
                return
            self._set_busy(True, "正在激活…")
            self._run_task(
                lambda: self.backend.activate(username, code, device),
                self._on_activate,
            )
            return

    def _set_busy(self, busy: bool, message: str = "") -> None:
        self.primary_button.setDisabled(busy)
        for widget in self._inputs:
            widget.setDisabled(busy)
        if message:
            self._set_status(message, error=False)

    def _set_status(self, message: str, error: bool = False) -> None:
        self.status.setText(message)
        self.status.setProperty("error", "true" if error else "false")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _run_task(self, func, callback) -> None:
        worker = Worker(func)
        worker.signals.finished.connect(callback)
        worker.signals.error.connect(lambda msg: self._on_error(msg))
        self.thread_pool.start(worker)

    def _on_error(self, message: str) -> None:
        self._set_busy(False)
        self._set_status(message or "请求失败。", error=True)
        if self.stack.currentIndex() == 0:
            QtWidgets.QMessageBox.warning(
                self,
                "登录失败",
                message or "请求失败，请稍后重试。",
            )

    def _format_error(self, resp: dict) -> str:
        error = resp.get("error", "")
        message = resp.get("message", "")
        raw = resp.get("raw", "")
        raw_lower = raw.lower() if isinstance(raw, str) else ""
        if "error code: 1010" in raw_lower:
            return "Cloudflare 已拦截请求（1010），请在 CF 中放行 /auth/* 或关闭相关防护。"
        if "error code: 1020" in raw_lower:
            return "Cloudflare 已拦截请求（1020），请检查防火墙规则并放行 /auth/*。"
        if "error code:" in raw_lower and error in {"http_403", "http_503"}:
            return "Cloudflare 已拦截请求，请在 CF 中放行 /auth/*。"
        mapping = {
            "not_activated": "账号未激活。",
            "activation_expired": "激活已过期，请重新激活。",
            "activation_disabled": "当前不允许激活。",
            "register_disabled": "当前不允许注册。",
            "user_exists": "账号已存在。",
            "weak_password": "密码强度不足。",
            "invalid_credentials": "账号或密码错误。",
            "device_limit": "设备数量超过限制。",
            "locked": "账户已锁定，请稍后再试。",
            "invalid_code": "卡密无效。",
            "code_expired": "卡密已过期。",
            "code_used": "卡密已使用完。",
            "code_bound_user": "卡密已绑定其他账号。",
            "code_bound_device": "卡密已绑定其他设备。",
            "missing_code": "请输入卡密。",
            "network_error": "网络错误，请稍后再试。",
        }
        return message or mapping.get(error, f"请求失败：{error or 'unknown_error'}")

    def _store_tokens(self, username: str, access: str, refresh: str) -> None:
        self.settings.set("auth_user", username)
        if access:
            self.settings.set("auth_access_token", access)
        if refresh:
            self.settings.set("auth_refresh_token", refresh)

    def _load_saved_auth_credentials(self) -> None:
        if not self.settings.get("auth_remember_password", False):
            self.remember_auth.setChecked(False)
            return
        saved_user = self.settings.get("auth_saved_user", "") or self.settings.get("auth_user", "")
        saved_pass = self.settings.get("auth_saved_password", "")
        if saved_user:
            self.user_input.setText(saved_user)
        if saved_pass:
            self.pass_input.setText(saved_pass)
        self.remember_auth.setChecked(True)

    def _format_remaining(self, activated_until: int | str | None) -> str:
        if activated_until in (0, "0"):
            return "永久"
        if not activated_until:
            return "未知"
        try:
            deadline = int(activated_until)
        except (TypeError, ValueError):
            return "未知"
        remaining = deadline - int(time.time())
        if remaining <= 0:
            return "已过期"
        days, rem = divmod(remaining, 86400)
        hours, rem = divmod(rem, 3600)
        minutes, _ = divmod(rem, 60)
        if days > 0:
            return f"{days}天{hours}小时{minutes}分钟"
        if hours > 0:
            return f"{hours}小时{minutes}分钟"
        return f"{minutes}分钟"

    def _on_register(self, resp: dict) -> None:
        self._set_busy(False)
        if resp.get("success"):
            self.tab_buttons[2].setChecked(True)
            self._switch_tab()
            self._set_status(resp.get("message", "注册成功，请激活后登录。"), error=False)
        else:
            self._set_status(self._format_error(resp), error=True)

    def _on_activate(self, resp: dict) -> None:
        self._set_busy(False)
        if resp.get("success"):
            self.tab_buttons[0].setChecked(True)
            self._switch_tab()
            self._set_status(resp.get("message", "激活成功，请登录。"), error=False)
        else:
            self._set_status(self._format_error(resp), error=True)

    def _on_login(self, resp: dict) -> None:
        self._set_busy(False)
        if resp.get("success"):
            username = resp.get("user", "") or self.user_input.text().strip()
            self._store_tokens(username, resp.get("access_token", ""), resp.get("refresh_token", ""))
            if self.remember_auth.isChecked():
                self.settings.set("auth_remember_password", True)
                self.settings.set("auth_saved_user", username)
                self.settings.set("auth_saved_password", self.pass_input.text())
            else:
                self.settings.set("auth_remember_password", False)
                self.settings.set("auth_saved_password", "")
            remaining = self._format_remaining(resp.get("activated_until"))
            QtWidgets.QMessageBox.information(
                self,
                "登录成功",
                f"授权登录成功。\n剩余时长：{remaining}",
            )
            self.accept()
        else:
            reason = self._format_error(resp)
            self._set_status(reason, error=True)
            QtWidgets.QMessageBox.warning(
                self,
                "登录失败",
                reason,
            )

    def _try_auto_login(self) -> None:
        access = self.settings.get("auth_access_token", "")
        refresh = self.settings.get("auth_refresh_token", "")
        if not access and not refresh:
            return
        self._set_busy(True, "正在验证授权…")
        if access:
            self._run_task(lambda: self.backend.verify(access), self._on_verify)
        else:
            self._run_task(lambda: self.backend.refresh(refresh, self.device_id), self._on_refresh)

    def _on_verify(self, resp: dict) -> None:
        if resp.get("success"):
            self._set_busy(False)
            self.accept()
            return
        refresh = self.settings.get("auth_refresh_token", "")
        if refresh:
            self._run_task(lambda: self.backend.refresh(refresh, self.device_id), self._on_refresh)
        else:
            self._set_busy(False)
            self._set_status(self._format_error(resp), error=True)

    def _on_refresh(self, resp: dict) -> None:
        if resp.get("success"):
            self._store_tokens(self.settings.get("auth_user", ""), resp.get("access_token", ""), resp.get("refresh_token", ""))
            self._run_task(lambda: self.backend.verify(resp.get("access_token", "")), self._on_verify)
            return
        self._set_busy(False)
        self._set_status(self._format_error(resp), error=True)
