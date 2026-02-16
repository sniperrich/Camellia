"""
Settings Management

Handles application settings persistence and retrieval with Material 3 theme support.
"""

import json
import os
import logging
from pathlib import Path
from typing import Any, Dict, Optional
from PySide6.QtCore import QObject, Signal

_LOGGER = logging.getLogger("camellia.settings")

class Settings(QObject):
    """
    Application settings manager with JSON persistence.

    Settings are stored in ~/.camellia/settings.json
    """

    # Signals
    theme_changed = Signal(str)  # Emitted when theme changes
    language_changed = Signal(str)  # Emitted when language changes
    setting_changed = Signal(str, object)  # Emitted when any setting changes

    # Default settings (Material 3 compliant)
    DEFAULTS = {
        # Appearance
        'theme': 'light',  # 'light', 'dark', 'system'
        'accent_color': '#6750A4',  # Material 3 primary color (purple)
        'language': 'zh_CN',  # 'zh_CN', 'en_US'
        'animations_enabled': True,
        'font_size': 'medium',  # 'small', 'medium', 'large'

        # Network
        'default_proxy_port': 25570,
        'auto_increment_port': True,
        'connection_timeout': 30,
        'max_retries': 3,

        # Storage
        'auto_login': False,
        'remember_password': True,
        'cache_enabled': True,
        'cache_max_size_mb': 500,

        # Advanced
        'debug_mode': False,
        'auto_load_plugins': True,
        'show_console': False,
        'check_updates': True,

        # Auth gateway
        'auth_base_url': 'https://api.taylorswift.fit',
        'auth_access_token': '',
        'auth_refresh_token': '',
        'auth_user': '',
        'auth_saved_user': '',
        'auth_saved_password': '',
        'auth_device_id': '',
        'auth_auto_login': False,
        'auth_remember_password': False,

        # Recent server
        'last_server_id': '',
        'last_server_name': '',
        'last_server_version': '',
        'last_server_address': '',
        'last_server_time': 0,
    }

    def __init__(self):
        super().__init__()
        self._settings: Dict[str, Any] = {}
        self._settings_path = self._get_settings_path()
        self.load()

    def _get_settings_path(self) -> Path:
        """Get the path to the settings file."""
        config_dir = Path.home() / '.camellia'
        config_dir.mkdir(exist_ok=True)
        return config_dir / 'settings.json'

    def load(self):
        """Load settings from disk, merging with defaults."""
        try:
            if self._settings_path.exists():
                with open(self._settings_path, 'r', encoding='utf-8') as f:
                    loaded_settings = json.load(f)
                # Merge with defaults (defaults for missing keys)
                self._settings = {**self.DEFAULTS, **loaded_settings}
            else:
                # Use defaults
                self._settings = self.DEFAULTS.copy()
                self.save()  # Create settings file with defaults
        except Exception as e:
            _LOGGER.warning("Error loading settings: %s", e)
            self._settings = self.DEFAULTS.copy()

    def save(self):
        """Save settings to disk."""
        try:
            with open(self._settings_path, 'w', encoding='utf-8') as f:
                json.dump(self._settings, f, indent=2, ensure_ascii=False)
        except Exception as e:
            _LOGGER.warning("Error saving settings: %s", e)

    def get(self, key: str, default: Any = None) -> Any:
        """
        Get a setting value.

        Args:
            key: Setting key
            default: Default value if key not found

        Returns:
            Setting value or default
        """
        return self._settings.get(key, default)

    def set(self, key: str, value: Any, save: bool = True):
        """
        Set a setting value.

        Args:
            key: Setting key
            value: Setting value
            save: Whether to save to disk immediately (default: True)
        """
        old_value = self._settings.get(key)
        self._settings[key] = value

        # Emit specific signals
        if key == 'theme' and old_value != value:
            self.theme_changed.emit(value)
        elif key == 'language' and old_value != value:
            self.language_changed.emit(value)

        # Emit general signal
        self.setting_changed.emit(key, value)

        if save:
            self.save()

    def reset(self, key: Optional[str] = None):
        """
        Reset settings to defaults.

        Args:
            key: Specific key to reset, or None to reset all
        """
        if key:
            if key in self.DEFAULTS:
                self.set(key, self.DEFAULTS[key])
        else:
            self._settings = self.DEFAULTS.copy()
            self.save()
            # Emit signals for major changes
            self.theme_changed.emit(self._settings['theme'])
            self.language_changed.emit(self._settings['language'])

    def get_all(self) -> Dict[str, Any]:
        """Get all settings as a dictionary."""
        return self._settings.copy()

    # Convenience properties for commonly used settings
    @property
    def theme(self) -> str:
        """Get current theme."""
        return self.get('theme', 'light')

    @theme.setter
    def theme(self, value: str):
        """Set current theme."""
        self.set('theme', value)

    @property
    def accent_color(self) -> str:
        """Get accent color."""
        return self.get('accent_color', '#6750A4')

    @accent_color.setter
    def accent_color(self, value: str):
        """Set accent color."""
        self.set('accent_color', value)

    @property
    def default_proxy_port(self) -> int:
        """Get default proxy port."""
        return self.get('default_proxy_port', 25570)

    @default_proxy_port.setter
    def default_proxy_port(self, value: int):
        """Set default proxy port."""
        self.set('default_proxy_port', value)

    @property
    def auto_increment_port(self) -> bool:
        """Get auto increment port setting."""
        return self.get('auto_increment_port', True)

    @auto_increment_port.setter
    def auto_increment_port(self, value: bool):
        """Set auto increment port setting."""
        self.set('auto_increment_port', value)

    @property
    def animations_enabled(self) -> bool:
        """Get animations enabled setting."""
        return self.get('animations_enabled', True)

    @animations_enabled.setter
    def animations_enabled(self, value: bool):
        """Set animations enabled setting."""
        self.set('animations_enabled', value)


# Global settings instance
_settings_instance: Optional[Settings] = None


def get_settings() -> Settings:
    """Get the global settings instance."""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance
