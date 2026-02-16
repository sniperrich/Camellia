"""
Camellia.NEL GUI Application

Main entry point for the graphical user interface.
"""

from __future__ import annotations

import sys
import logging

from PySide6 import QtCore, QtWidgets

from ..plugins import get_plugin_manager
from .theme import build_stylesheet
from .main_window import MainWindow
from .auth_gate import AuthGateDialog
from .settings import get_settings


def main() -> int:
    """Main entry point for the GUI application."""
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_EnableHighDpiScaling, True)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseStyleSheetPropagationInWidgetStyles, True)
    app = QtWidgets.QApplication(sys.argv)
    if sys.platform.startswith("win"):
        app.setStyle("Fusion")
    
    # Load settings and apply theme
    settings = get_settings()
    log_level = logging.DEBUG if settings.get("debug_mode", False) else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    stylesheet = build_stylesheet(settings.theme)
    app.setStyleSheet(stylesheet)
    
    # Create main window (hidden until gate passes)
    window = MainWindow()

    gate = AuthGateDialog(window)
    if gate.exec() != QtWidgets.QDialog.Accepted:
        return 0

    # Load plugins
    get_plugin_manager().load_plugins(extras={"mode": "gui", "app": app, "window": window})

    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
