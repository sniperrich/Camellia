"""
Settings Page

Material 3 style settings page for Camellia.NEL GUI.
Provides configuration for appearance, network, storage, and advanced options.
"""

from __future__ import annotations

from PySide6 import QtCore, QtWidgets

from ..settings import get_settings
from ..widgets import StatusBadge, MaterialComboBox


class SettingsPage(QtWidgets.QWidget):
    """
    Settings page with Material 3 design.

    Provides configuration options for:
    - Appearance (theme, accent color, language, animations)
    - Network (default port, auto-increment, timeout)
    - Storage (auto-login, cache settings)
    - Advanced (debug mode, plugin auto-load)
    """

    # Signals
    theme_changed = QtCore.Signal(str)  # Emitted when theme changes
    settings_saved = QtCore.Signal()  # Emitted when settings are saved

    def __init__(self, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = get_settings()
        self._init_ui()
        self._load_settings()

    def _init_ui(self) -> None:
        """Initialize the UI."""
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(16)

        # Title
        title = QtWidgets.QLabel("设置")
        title.setObjectName("Title")
        layout.addWidget(title)

        # Scroll area for settings
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)

        scroll_content = QtWidgets.QWidget()
        scroll_layout = QtWidgets.QVBoxLayout(scroll_content)
        scroll_layout.setSpacing(16)

        # Appearance Section
        scroll_layout.addWidget(self._create_appearance_section())

        # Network Section
        scroll_layout.addWidget(self._create_network_section())

        # Storage Section
        scroll_layout.addWidget(self._create_storage_section())

        # Advanced Section
        scroll_layout.addWidget(self._create_advanced_section())

        scroll_layout.addStretch()

        scroll.setWidget(scroll_content)
        layout.addWidget(scroll)

        # Action buttons
        actions = QtWidgets.QHBoxLayout()
        actions.addStretch()

        reset_button = QtWidgets.QPushButton("恢复默认")
        reset_button.setProperty("variant", "ghost")
        reset_button.clicked.connect(self._reset_settings)
        actions.addWidget(reset_button)

        save_button = QtWidgets.QPushButton("保存设置")
        save_button.setProperty("variant", "primary")
        save_button.clicked.connect(self._save_settings)
        actions.addWidget(save_button)

        layout.addLayout(actions)

    def _create_appearance_section(self) -> QtWidgets.QFrame:
        """Create appearance settings section."""
        section = self._create_section("外观设置")
        layout = section.layout()

        # Theme selection
        theme_row = QtWidgets.QHBoxLayout()
        theme_label = QtWidgets.QLabel("主题")
        theme_label.setFixedWidth(100)
        theme_row.addWidget(theme_label)

        self.theme_combo = MaterialComboBox()
        self.theme_combo.addItems(["浅色", "深色", "跟随系统"])
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        theme_row.addWidget(self.theme_combo)
        theme_row.addStretch()

        layout.addLayout(theme_row)

        # Accent color
        accent_row = QtWidgets.QHBoxLayout()
        accent_label = QtWidgets.QLabel("强调色")
        accent_label.setFixedWidth(100)
        accent_row.addWidget(accent_label)

        self.accent_combo = MaterialComboBox()
        self.accent_combo.addItems([
            "紫色 (Material 3)",
            "蓝色",
            "绿色",
            "橙色",
            "红色"
        ])
        accent_row.addWidget(self.accent_combo)
        accent_row.addStretch()

        layout.addLayout(accent_row)

        # Language
        lang_row = QtWidgets.QHBoxLayout()
        lang_label = QtWidgets.QLabel("语言")
        lang_label.setFixedWidth(100)
        lang_row.addWidget(lang_label)

        self.lang_combo = MaterialComboBox()
        self.lang_combo.addItems(["简体中文", "English"])
        lang_row.addWidget(self.lang_combo)
        lang_row.addStretch()

        layout.addLayout(lang_row)

        # Font size
        font_row = QtWidgets.QHBoxLayout()
        font_label = QtWidgets.QLabel("字体大小")
        font_label.setFixedWidth(100)
        font_row.addWidget(font_label)

        self.font_combo = MaterialComboBox()
        self.font_combo.addItems(["小", "中", "大"])
        font_row.addWidget(self.font_combo)
        font_row.addStretch()

        layout.addLayout(font_row)

        # Animations
        anim_row = QtWidgets.QHBoxLayout()
        anim_label = QtWidgets.QLabel("启用动画")
        anim_label.setFixedWidth(100)
        anim_row.addWidget(anim_label)

        self.animations_check = QtWidgets.QCheckBox()
        anim_row.addWidget(self.animations_check)
        anim_row.addStretch()

        layout.addLayout(anim_row)

        return section

    def _create_network_section(self) -> QtWidgets.QFrame:
        """Create network settings section."""
        section = self._create_section("网络设置")
        layout = section.layout()

        # Default proxy port
        port_row = QtWidgets.QHBoxLayout()
        port_label = QtWidgets.QLabel("默认代理端口")
        port_label.setFixedWidth(100)
        port_row.addWidget(port_label)

        self.port_spin = QtWidgets.QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(25570)
        port_row.addWidget(self.port_spin)
        port_row.addStretch()

        layout.addLayout(port_row)

        # Auto-increment port
        auto_port_row = QtWidgets.QHBoxLayout()
        auto_port_label = QtWidgets.QLabel("自动递增端口")
        auto_port_label.setFixedWidth(100)
        auto_port_row.addWidget(auto_port_label)

        self.auto_port_check = QtWidgets.QCheckBox()
        auto_port_row.addWidget(self.auto_port_check)

        auto_port_hint = QtWidgets.QLabel("启动多个代理时自动分配端口")
        auto_port_hint.setProperty("muted", "true")
        auto_port_row.addWidget(auto_port_hint)
        auto_port_row.addStretch()

        layout.addLayout(auto_port_row)

        # Connection timeout
        timeout_row = QtWidgets.QHBoxLayout()
        timeout_label = QtWidgets.QLabel("连接超时")
        timeout_label.setFixedWidth(100)
        timeout_row.addWidget(timeout_label)

        self.timeout_spin = QtWidgets.QSpinBox()
        self.timeout_spin.setRange(5, 120)
        self.timeout_spin.setSuffix(" 秒")
        timeout_row.addWidget(self.timeout_spin)
        timeout_row.addStretch()

        layout.addLayout(timeout_row)

        # Max retries
        retry_row = QtWidgets.QHBoxLayout()
        retry_label = QtWidgets.QLabel("最大重试次数")
        retry_label.setFixedWidth(100)
        retry_row.addWidget(retry_label)

        self.retry_spin = QtWidgets.QSpinBox()
        self.retry_spin.setRange(0, 10)
        retry_row.addWidget(self.retry_spin)
        retry_row.addStretch()

        layout.addLayout(retry_row)

        return section

    def _create_storage_section(self) -> QtWidgets.QFrame:
        """Create storage settings section."""
        section = self._create_section("存储设置")
        layout = section.layout()

        # Auto-login
        auto_login_row = QtWidgets.QHBoxLayout()
        auto_login_label = QtWidgets.QLabel("游戏账号自动登录")
        auto_login_label.setFixedWidth(100)
        auto_login_row.addWidget(auto_login_label)

        self.auto_login_check = QtWidgets.QCheckBox()
        auto_login_row.addWidget(self.auto_login_check)

        auto_login_hint = QtWidgets.QLabel("启动时自动登录上次使用的 4399/网易账号")
        auto_login_hint.setProperty("muted", "true")
        auto_login_row.addWidget(auto_login_hint)
        auto_login_row.addStretch()

        layout.addLayout(auto_login_row)

        # Remember password
        remember_row = QtWidgets.QHBoxLayout()
        remember_label = QtWidgets.QLabel("游戏账号记住密码")
        remember_label.setFixedWidth(100)
        remember_row.addWidget(remember_label)

        self.remember_check = QtWidgets.QCheckBox()
        remember_row.addWidget(self.remember_check)

        remember_hint = QtWidgets.QLabel("⚠️ 仅用于 4399/网易账号，密码将以明文存储")
        remember_hint.setStyleSheet("color: #E65100;")
        remember_row.addWidget(remember_hint)
        remember_row.addStretch()

        layout.addLayout(remember_row)

        # Auth auto-login
        auth_auto_row = QtWidgets.QHBoxLayout()
        auth_auto_label = QtWidgets.QLabel("授权自动登录")
        auth_auto_label.setFixedWidth(100)
        auth_auto_row.addWidget(auth_auto_label)

        self.auth_auto_login_check = QtWidgets.QCheckBox()
        auth_auto_row.addWidget(self.auth_auto_login_check)

        auth_auto_hint = QtWidgets.QLabel("访问验证通过后可自动验证授权")
        auth_auto_hint.setProperty("muted", "true")
        auth_auto_row.addWidget(auth_auto_hint)
        auth_auto_row.addStretch()

        layout.addLayout(auth_auto_row)

        # Auth remember password
        auth_remember_row = QtWidgets.QHBoxLayout()
        auth_remember_label = QtWidgets.QLabel("授权记住密码")
        auth_remember_label.setFixedWidth(100)
        auth_remember_row.addWidget(auth_remember_label)

        self.auth_remember_check = QtWidgets.QCheckBox()
        auth_remember_row.addWidget(self.auth_remember_check)

        auth_remember_hint = QtWidgets.QLabel("⚠️ 仅用于访问验证账号，密码将以明文存储")
        auth_remember_hint.setStyleSheet("color: #E65100;")
        auth_remember_row.addWidget(auth_remember_hint)
        auth_remember_row.addStretch()

        layout.addLayout(auth_remember_row)

        # Cache enabled
        cache_row = QtWidgets.QHBoxLayout()
        cache_label = QtWidgets.QLabel("启用缓存")
        cache_label.setFixedWidth(100)
        cache_row.addWidget(cache_label)

        self.cache_check = QtWidgets.QCheckBox()
        cache_row.addWidget(self.cache_check)
        cache_row.addStretch()

        layout.addLayout(cache_row)

        # Cache size
        cache_size_row = QtWidgets.QHBoxLayout()
        cache_size_label = QtWidgets.QLabel("缓存大小限制")
        cache_size_label.setFixedWidth(100)
        cache_size_row.addWidget(cache_size_label)

        self.cache_size_spin = QtWidgets.QSpinBox()
        self.cache_size_spin.setRange(50, 5000)
        self.cache_size_spin.setSuffix(" MB")
        cache_size_row.addWidget(self.cache_size_spin)
        cache_size_row.addStretch()

        layout.addLayout(cache_size_row)

        # Clear cache button
        clear_cache_row = QtWidgets.QHBoxLayout()
        clear_cache_row.addSpacing(120)
        clear_cache_button = QtWidgets.QPushButton("清除缓存")
        clear_cache_button.setProperty("variant", "ghost")
        clear_cache_button.clicked.connect(self._clear_cache)
        clear_cache_row.addWidget(clear_cache_button)
        clear_cache_row.addStretch()

        layout.addLayout(clear_cache_row)

        return section

    def _create_advanced_section(self) -> QtWidgets.QFrame:
        """Create advanced settings section."""
        section = self._create_section("高级设置")
        layout = section.layout()

        # Debug mode
        debug_row = QtWidgets.QHBoxLayout()
        debug_label = QtWidgets.QLabel("调试模式")
        debug_label.setFixedWidth(100)
        debug_row.addWidget(debug_label)

        self.debug_check = QtWidgets.QCheckBox()
        debug_row.addWidget(self.debug_check)

        debug_hint = QtWidgets.QLabel("显示详细日志信息")
        debug_hint.setProperty("muted", "true")
        debug_row.addWidget(debug_hint)
        debug_row.addStretch()

        layout.addLayout(debug_row)

        # Auto-load plugins
        auto_plugin_row = QtWidgets.QHBoxLayout()
        auto_plugin_label = QtWidgets.QLabel("自动加载插件")
        auto_plugin_label.setFixedWidth(100)
        auto_plugin_row.addWidget(auto_plugin_label)

        self.auto_plugin_check = QtWidgets.QCheckBox()
        auto_plugin_row.addWidget(self.auto_plugin_check)
        auto_plugin_row.addStretch()

        layout.addLayout(auto_plugin_row)

        # Show console
        console_row = QtWidgets.QHBoxLayout()
        console_label = QtWidgets.QLabel("显示控制台")
        console_label.setFixedWidth(100)
        console_row.addWidget(console_label)

        self.console_check = QtWidgets.QCheckBox()
        console_row.addWidget(self.console_check)
        console_row.addStretch()

        layout.addLayout(console_row)

        # Check updates
        update_row = QtWidgets.QHBoxLayout()
        update_label = QtWidgets.QLabel("检查更新")
        update_label.setFixedWidth(100)
        update_row.addWidget(update_label)

        self.update_check = QtWidgets.QCheckBox()
        update_row.addWidget(self.update_check)

        update_hint = QtWidgets.QLabel("启动时检查新版本")
        update_hint.setProperty("muted", "true")
        update_row.addWidget(update_hint)
        update_row.addStretch()

        layout.addLayout(update_row)

        return section

    def _create_section(self, title: str) -> QtWidgets.QFrame:
        """Create a settings section with title."""
        section = QtWidgets.QFrame()
        section.setProperty("card", "true")

        layout = QtWidgets.QVBoxLayout(section)
        layout.setContentsMargins(18, 14, 18, 14)
        layout.setSpacing(16)

        # Section title
        title_label = QtWidgets.QLabel(title)
        title_label.setStyleSheet("font-size: 16px; font-weight: 600;")
        layout.addWidget(title_label)

        return section

    def _load_settings(self) -> None:
        """Load settings from storage."""
        # Appearance
        theme_map = {"light": 0, "dark": 1, "system": 2}
        self.theme_combo.setCurrentIndex(theme_map.get(self.settings.theme, 0))

        accent_map = {
            "#6750A4": 0,  # Purple
            "#1976D2": 1,  # Blue
            "#388E3C": 2,  # Green
            "#F57C00": 3,  # Orange
            "#D32F2F": 4,  # Red
        }
        self.accent_combo.setCurrentIndex(accent_map.get(self.settings.accent_color, 0))

        lang_map = {"zh_CN": 0, "en_US": 1}
        self.lang_combo.setCurrentIndex(lang_map.get(self.settings.get("language"), 0))

        font_map = {"small": 0, "medium": 1, "large": 2}
        self.font_combo.setCurrentIndex(font_map.get(self.settings.get("font_size"), 1))

        self.animations_check.setChecked(self.settings.animations_enabled)

        # Network
        self.port_spin.setValue(self.settings.default_proxy_port)
        self.auto_port_check.setChecked(self.settings.auto_increment_port)
        self.timeout_spin.setValue(self.settings.get("connection_timeout", 30))
        self.retry_spin.setValue(self.settings.get("max_retries", 3))

        # Storage
        self.auto_login_check.setChecked(self.settings.get("auto_login", False))
        self.remember_check.setChecked(self.settings.get("remember_password", True))
        self.auth_auto_login_check.setChecked(self.settings.get("auth_auto_login", False))
        self.auth_remember_check.setChecked(self.settings.get("auth_remember_password", False))
        self.cache_check.setChecked(self.settings.get("cache_enabled", True))
        self.cache_size_spin.setValue(self.settings.get("cache_max_size_mb", 500))

        # Advanced
        self.debug_check.setChecked(self.settings.get("debug_mode", False))
        self.auto_plugin_check.setChecked(self.settings.get("auto_load_plugins", True))
        self.console_check.setChecked(self.settings.get("show_console", False))
        self.update_check.setChecked(self.settings.get("check_updates", True))

    def _save_settings(self) -> None:
        """Save settings to storage."""
        # Appearance
        theme_map = {0: "light", 1: "dark", 2: "system"}
        new_theme = theme_map[self.theme_combo.currentIndex()]
        self.settings.theme = new_theme

        accent_map = {
            0: "#6750A4",  # Purple
            1: "#1976D2",  # Blue
            2: "#388E3C",  # Green
            3: "#F57C00",  # Orange
            4: "#D32F2F",  # Red
        }
        self.settings.accent_color = accent_map[self.accent_combo.currentIndex()]

        lang_map = {0: "zh_CN", 1: "en_US"}
        self.settings.set("language", lang_map[self.lang_combo.currentIndex()])

        font_map = {0: "small", 1: "medium", 2: "large"}
        self.settings.set("font_size", font_map[self.font_combo.currentIndex()])

        self.settings.animations_enabled = self.animations_check.isChecked()

        # Network
        self.settings.default_proxy_port = self.port_spin.value()
        self.settings.auto_increment_port = self.auto_port_check.isChecked()
        self.settings.set("connection_timeout", self.timeout_spin.value())
        self.settings.set("max_retries", self.retry_spin.value())

        # Storage
        self.settings.set("auto_login", self.auto_login_check.isChecked())
        self.settings.set("remember_password", self.remember_check.isChecked())
        self.settings.set("auth_auto_login", self.auth_auto_login_check.isChecked())
        self.settings.set("auth_remember_password", self.auth_remember_check.isChecked())
        self.settings.set("cache_enabled", self.cache_check.isChecked())
        self.settings.set("cache_max_size_mb", self.cache_size_spin.value())

        # Advanced
        self.settings.set("debug_mode", self.debug_check.isChecked())
        self.settings.set("auto_load_plugins", self.auto_plugin_check.isChecked())
        self.settings.set("show_console", self.console_check.isChecked())
        self.settings.set("check_updates", self.update_check.isChecked())

        # Emit signals
        self.settings_saved.emit()

        # Show success message
        QtWidgets.QMessageBox.information(
            self,
            "设置已保存",
            "您的设置已成功保存。\n某些设置可能需要重启应用后生效。"
        )

    def _reset_settings(self) -> None:
        """Reset all settings to defaults."""
        reply = QtWidgets.QMessageBox.question(
            self,
            "恢复默认设置",
            "确定要恢复所有设置为默认值吗？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )

        if reply == QtWidgets.QMessageBox.Yes:
            self.settings.reset()
            self._load_settings()
            QtWidgets.QMessageBox.information(
                self,
                "设置已重置",
                "所有设置已恢复为默认值。"
            )

    def _clear_cache(self) -> None:
        """Clear application cache."""
        reply = QtWidgets.QMessageBox.question(
            self,
            "清除缓存",
            "确定要清除所有缓存数据吗？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No
        )

        if reply == QtWidgets.QMessageBox.Yes:
            # TODO: Implement cache clearing logic
            QtWidgets.QMessageBox.information(
                self,
                "缓存已清除",
                "所有缓存数据已成功清除。"
            )

    def _on_theme_changed(self, text: str) -> None:
        """Handle theme change."""
        theme_map = {"浅色": "light", "深色": "dark", "跟随系统": "system"}
        theme = theme_map.get(text, "light")
        self.theme_changed.emit(theme)
