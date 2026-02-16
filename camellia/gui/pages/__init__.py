"""
GUI Pages Module

This module contains individual page classes for the Camellia.NEL GUI.
Each page is responsible for a specific section of the application.
"""

from .login_page import LoginPage
from .servers_page import ServersPage
from .characters_page import CharacterPage
from .connection_page import ConnectionPage
from .skins_page import SkinPage
from .plugins_page import PluginsPage
from .settings_page import SettingsPage

__all__ = [
    "LoginPage",
    "ServersPage",
    "CharacterPage",
    "ConnectionPage",
    "SkinPage",
    "PluginsPage",
    "SettingsPage",
]
