"""
Camellia.NEL GUI Application

Main entry point for the graphical user interface.
"""

from __future__ import annotations

import sys

from PySide6 import QtCore, QtWidgets

from ..plugins import get_plugin_manager
from .theme import build_stylesheet
from .main_window import MainWindow
from .settings import get_settings


def main() -> int:
    """Main entry point for the GUI application."""
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    app = QtWidgets.QApplication(sys.argv)
    
    # Load settings and apply theme
    settings = get_settings()
    stylesheet = build_stylesheet(settings.theme)
    app.setStyleSheet(stylesheet)
    
    # Create main window
    window = MainWindow()
    
    # Load plugins
    get_plugin_manager().load_plugins(extras={"mode": "gui", "app": app, "window": window})
    
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
