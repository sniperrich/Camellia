"""Theme helpers for the Qt GUI."""

PALETTE = {
    "bg": "#efe7dc",
    "bg_alt": "#dbe6e1",
    "panel": "#ffffff",
    "panel_alt": "#f3f7f5",
    "accent": "#1f7a6b",
    "accent_dark": "#145c50",
    "text": "#1c2b29",
    "muted": "#5b6b67",
    "border": "#d6ddd8",
    "danger": "#b5473d",
    "warning": "#d39c2f",
}


def build_stylesheet() -> str:
    colors = PALETTE
    return f"""
    * {{
        color: {colors["text"]};
        font-family: "Source Sans 3", "Noto Sans", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
        font-size: 13px;
    }}

    QMainWindow, QWidget {{
        background: transparent;
    }}

    #Sidebar {{
        background: rgba(255, 255, 255, 0.88);
        border-right: 1px solid {colors["border"]};
        border-top-left-radius: 16px;
        border-bottom-left-radius: 16px;
    }}

    #ContentStack {{
        background: rgba(255, 255, 255, 0.78);
        border: 1px solid {colors["border"]};
        border-left: none;
        border-top-right-radius: 16px;
        border-bottom-right-radius: 16px;
    }}

    #AppTitle {{
        font-size: 18px;
        font-weight: 700;
        letter-spacing: 0.5px;
    }}

    #Title {{
        font-size: 22px;
        font-weight: 700;
    }}

    #Subtitle {{
        color: {colors["muted"]};
    }}

    QLineEdit {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 8px;
        padding: 8px 10px;
    }}

    QLineEdit:focus {{
        border: 1px solid {colors["accent"]};
    }}

    QComboBox {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 8px;
        padding: 6px 8px;
    }}

    QAbstractItemView {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 8px;
        selection-background-color: {colors["accent"]};
        selection-color: white;
        outline: 0;
    }}

    QComboBox QAbstractItemView {{
        padding: 4px;
    }}

    QListWidget {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 10px;
        padding: 6px;
    }}

    QListWidget::item {{
        padding: 6px 8px;
        border-radius: 6px;
    }}

    QListWidget::item:selected {{
        background: {colors["accent"]};
        color: white;
    }}

    QTextEdit {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 8px;
        padding: 8px 10px;
    }}

    QScrollArea {{
        background: transparent;
        border: none;
    }}

    QScrollBar:vertical {{
        background: transparent;
        width: 10px;
        margin: 4px 0;
    }}

    QScrollBar::handle:vertical {{
        background: rgba(28, 43, 41, 0.2);
        border-radius: 5px;
        min-height: 40px;
    }}

    QScrollBar::add-line:vertical,
    QScrollBar::sub-line:vertical {{
        height: 0px;
    }}

    QPushButton {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 10px;
        padding: 8px 14px;
    }}

    QPushButton:hover {{
        border: 1px solid {colors["accent"]};
    }}

    QPushButton[variant="primary"] {{
        background: {colors["accent"]};
        color: white;
        border: none;
        font-weight: 600;
    }}

    QPushButton[variant="primary"]:hover {{
        background: {colors["accent_dark"]};
    }}

    QPushButton[variant="ghost"] {{
        background: transparent;
        border: 1px solid {colors["border"]};
    }}

    QPushButton[variant="danger"] {{
        background: {colors["danger"]};
        color: white;
        border: none;
        font-weight: 600;
    }}

    QPushButton[variant="danger"]:hover {{
        background: #983a32;
    }}

    QPushButton#NavButton {{
        text-align: left;
        padding: 10px 14px;
        border-radius: 10px;
        border: none;
        background: transparent;
    }}

    QPushButton#NavButton:checked {{
        background: {colors["accent"]};
        color: white;
        font-weight: 600;
    }}

    QPushButton[variant="seg"] {{
        border-radius: 12px;
        border: 1px solid {colors["border"]};
        padding: 6px 12px;
        background: {colors["panel"]};
    }}

    QPushButton[variant="seg"]:checked {{
        background: {colors["accent"]};
        color: white;
        border: none;
        font-weight: 600;
    }}

    QFrame[card="true"] {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 16px;
    }}

    QFrame[card="true"][selected="true"] {{
        border: 2px solid {colors["accent"]};
    }}

    QLabel[muted="true"] {{
        color: {colors["muted"]};
    }}
    """
