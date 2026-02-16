"""Theme helpers for the Qt GUI with Material 3 support."""

import base64

# Material 3 Light Theme Palette
LIGHT_PALETTE = {
    "bg": "#eef1f0",
    "bg_alt": "#dde6e2",
    "panel": "#ffffff",
    "panel_alt": "#f4f7f6",
    "accent": "#6750A4",  # Material 3 primary purple
    "accent_dark": "#4F378B",
    "accent_light": "#EADDFF",
    "text": "#1C1B1F",
    "text_secondary": "#49454F",
    "muted": "#5e6f6b",
    "border": "#c4cdc9",
    "border_strong": "#9aa5a0",
    "danger": "#BA1A1A",
    "warning": "#7D5700",
    "success": "#2E7D32",
    "surface": "#FEF7FF",
    "surface_variant": "#E7E0EC",
    "glass": "rgba(255, 255, 255, 0.72)",
    "glass_alt": "rgba(255, 255, 255, 0.62)",
    "glass_border": "rgba(255, 255, 255, 0.45)",
    "glass_hover": "rgba(255, 255, 255, 0.82)",
}

# Material 3 Dark Theme Palette
DARK_PALETTE = {
    "bg": "#1C1B1F",
    "bg_alt": "#2B2930",
    "panel": "#2B2930",
    "panel_alt": "#36343B",
    "accent": "#D0BCFF",  # Material 3 primary purple (dark)
    "accent_dark": "#B69DF8",
    "accent_light": "#4F378B",
    "text": "#E6E1E5",
    "text_secondary": "#CAC4D0",
    "muted": "#938F99",
    "border": "#49454F",
    "border_strong": "#635e6a",
    "danger": "#FFB4AB",
    "warning": "#FFB951",
    "success": "#81C784",
    "surface": "#1C1B1F",
    "surface_variant": "#49454F",
    "glass": "rgba(42, 39, 47, 0.68)",
    "glass_alt": "rgba(42, 39, 47, 0.58)",
    "glass_border": "rgba(255, 255, 255, 0.12)",
    "glass_hover": "rgba(58, 54, 64, 0.75)",
}

# Default palette (for backward compatibility)
PALETTE = LIGHT_PALETTE


def get_palette(theme: str = "light") -> dict:
    """
    Get color palette for the specified theme.

    Args:
        theme: Theme name ('light', 'dark', or 'system')

    Returns:
        Dictionary of color values
    """
    if theme == "dark":
        return DARK_PALETTE
    elif theme == "system":
        # TODO: Detect system theme preference
        # For now, default to light
        return LIGHT_PALETTE
    else:
        return LIGHT_PALETTE


def build_stylesheet(theme: str = "light") -> str:
    """
    Build Material 3 stylesheet for the specified theme.

    Args:
        theme: Theme name ('light', 'dark', or 'system')

    Returns:
        Complete stylesheet string
    """
    colors = get_palette(theme)
    arrow_svg = (
        "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 24 24' "
        f"fill='{colors['text_secondary']}'>"
        "<path d='M7 10l5 5 5-5z'/>"
        "</svg>"
    )
    arrow_b64 = base64.b64encode(arrow_svg.encode("utf-8")).decode("ascii")
    arrow_uri = f"data:image/svg+xml;base64,{arrow_b64}"
    return f"""
    * {{
        color: {colors["text"]};
        font-family: "Manrope", "Noto Sans", "Noto Sans CJK SC", "PingFang SC", "Microsoft YaHei", sans-serif;
        font-size: 14px;
    }}

    QMainWindow, QWidget {{
        background: transparent;
    }}

    #Sidebar {{
        background: {colors["glass"]};
        border-right: 1px solid {colors["border"]};
    }}

    #ContentStack {{
        background: transparent;
        border-left: none;
    }}

    #AppTitle {{
        font-size: 17px;
        font-weight: 700;
        letter-spacing: 0.2px;
    }}

    #Title {{
        font-size: 22px;
        font-weight: 700;
        letter-spacing: -0.3px;
    }}

    #Subtitle {{
        color: {colors["text_secondary"]};
        font-size: 13px;
    }}

    QLineEdit {{
        background: {colors["surface_variant"]};
        border: none;
        border-bottom: 2px solid {colors["border"]};
        border-radius: 4px 4px 0 0;
        padding: 6px 10px;
        font-size: 14px;
        min-height: 30px;
    }}

    QLineEdit:focus {{
        border-bottom: 2px solid {colors["accent"]};
        background: {colors["surface_variant"]};
    }}
    
    QLineEdit:hover {{
        background: {colors["panel_alt"]};
    }}

    QComboBox {{
        background: {colors["surface_variant"]};
        border: 1px solid {colors["border"]};
        border-radius: 12px;
        padding: 8px 12px;
        min-width: 100px;
        min-height: 25px;
    }}

    QComboBox:hover {{
        border: 1px solid {colors["accent_dark"]};
        background: {colors["panel_alt"]};
    }}

    QComboBox:focus {{
        border: 2px solid {colors["accent"]};
        background: {colors["panel_alt"]};
    }}

    QComboBox::drop-down {{
        border-left: 1px solid {colors["border"]};
        width: 30px;
        border-top-right-radius: 12px;
        border-bottom-right-radius: 12px;
        background: {colors["panel_alt"]};
    }}

    QComboBox::drop-down:hover {{
        background: {colors["surface_variant"]};
    }}

    QComboBox::down-arrow {{
        width: 12px;
        height: 12px;
        image: url("{arrow_uri}");
    }}

    QAbstractItemView {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 12px;
        selection-background-color: {colors["accent_light"]};
        selection-color: {colors["text"]};
        outline: 0;
        padding: 4px;
    }}

    QComboBox QAbstractItemView {{
        padding: 6px;
    }}


    QListWidget {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 12px;
        padding: 4px;
    }}

    QListWidget::item {{
        padding: 4px 6px;
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

    QCheckBox {{
        spacing: 8px;
    }}

    QCheckBox::indicator {{
        width: 16px;
        height: 16px;
        border: 1px solid {colors["border_strong"]};
        border-radius: 4px;
        background-color: {colors["panel"]};
    }}

    QCheckBox::indicator:hover {{
        border: 1px solid {colors["accent"]};
    }}

    QCheckBox::indicator:checked {{
        background-color: {colors["accent"]};
        border: 1px solid {colors["accent"]};
        image: url(:/qt-project.org/styles/commonstyle/images/checkbox_checked.png);
    }}

    QCheckBox::indicator:checked:disabled {{
        background-color: {colors["border"]};
        border: 1px solid {colors["border"]};
        image: url(:/qt-project.org/styles/commonstyle/images/checkbox_checked_disabled.png);
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
        background-color: {colors["panel"]};
        border: 1px solid {colors["border_strong"]};
        border-style: solid;
        border-image: none;
        border-radius: 12px;
        padding: 5px 10px;
        font-weight: 500;
        font-size: 14px;
        min-height: 28px;
    }}

    QPushButton:focus {{
        outline: none;
    }}

    QPushButton:hover {{
        border: 1px solid {colors["accent"]};
        background: {colors["panel_alt"]};
    }}
    
    QPushButton:pressed {{
        background: {colors["border"]};
    }}

    QPushButton[variant="primary"] {{
        background-color: {colors["accent"]};
        color: white;
        border: none;
        border-image: none;
        border-radius: 12px;
        padding: 6px 12px;
        font-weight: 600;
        font-size: 14px;
        min-height: 30px;
    }}

    QPushButton[variant="primary"]:hover {{
        background: {colors["accent_dark"]};
    }}
    
    QPushButton[variant="primary"]:pressed {{
        background: {colors["accent_light"]};
    }}

    QPushButton[variant="ghost"] {{
        background-color: transparent;
        border: 1px solid {colors["border_strong"]};
        border-style: solid;
        border-image: none;
        border-radius: 12px;
        padding: 5px 10px;
        font-weight: 500;
        font-size: 14px;
        min-height: 28px;
    }}
    
    QPushButton[variant="ghost"]:hover {{
        background: {colors["panel_alt"]};
        border: 1px solid {colors["accent"]};
    }}
    
    QPushButton[variant="ghost"]:pressed {{
        background: {colors["border"]};
    }}

    QPushButton[variant="danger"] {{
        background-color: {colors["danger"]};
        color: white;
        border: none;
        font-weight: 600;
    }}

    QPushButton[variant="danger"]:hover {{
        background: #983a32;
    }}

    QPushButton#NavButton {{
        text-align: left;
        padding: 6px 10px;
        border-radius: 10px;
        border: none;
        background: transparent;
        font-size: 13px;
        font-weight: 500;
    }}
    
    QPushButton#NavButton:hover {{
        background: {colors["panel_alt"]};
    }}

    QPushButton#NavButton:checked {{
        background: {colors["accent_light"]};
        color: {colors["accent"]};
        font-weight: 600;
    }}
    
    QPushButton#NavButton:checked:hover {{
        background: {colors["accent_light"]};
    }}

    QPushButton[variant="seg"] {{
        border-radius: 12px;
        border: 1px solid {colors["border"]};
        padding: 4px 10px;
        background: transparent;
        font-weight: 500;
        font-size: 12px;
        min-height: 26px;
    }}
    
    QPushButton[variant="seg"]:hover {{
        background: {colors["panel_alt"]};
    }}

    QPushButton[variant="seg"]:checked {{
        background: {colors["accent_light"]};
        color: {colors["accent"]};
        border: 1px solid {colors["accent"]};
        font-weight: 600;
    }}
    
    QPushButton[variant="seg"]:checked:hover {{
        background: {colors["accent_light"]};
    }}

    QFrame[card="true"] {{
        background: {colors["glass"]};
        border: 1px solid {colors["glass_border"]};
        border-radius: 14px;
    }}

    QFrame[auth_card="true"] {{
        border-radius: 0px;
        border: none;
        background: {colors["panel"]};
    }}

    QFrame[card="true"]:hover {{
        background: {colors["glass_hover"]};
        border: 1px solid {colors["accent"]};
    }}

    QFrame[card="true"][selected="true"] {{
        border: 2px solid {colors["accent"]};
        background: {colors["glass_alt"]};
    }}

    QLabel[muted="true"] {{
        color: {colors["muted"]};
    }}

    QLabel[error="true"] {{
        color: {colors["danger"]};
    }}

    QSpinBox {{
        background: {colors["panel"]};
        border: 1px solid {colors["border"]};
        border-radius: 12px;
        padding: 7px 10px;
    }}

    QSpinBox:focus {{
        border: 1px solid {colors["accent"]};
    }}

    QSpinBox::up-button, QSpinBox::down-button {{
        background: transparent;
        border: none;
        width: 16px;
    }}

    QSpinBox::up-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-bottom: 6px solid {colors["text"]};
        width: 0;
        height: 0;
    }}

    QSpinBox::down-arrow {{
        image: none;
        border-left: 4px solid transparent;
        border-right: 4px solid transparent;
        border-top: 6px solid {colors["text"]};
        width: 0;
        height: 0;
    }}

    /* Material 3 Elevation Shadows */
    QFrame[elevation="1"] {{
        background: {colors["panel"]};
        border: none;
        border-radius: 12px;
    }}

    QFrame[elevation="2"] {{
        background: {colors["panel"]};
        border: none;
        border-radius: 16px;
    }}

    QFrame[elevation="3"] {{
        background: {colors["panel"]};
        border: none;
        border-radius: 20px;
    }}

    /* Status badges */
    QLabel[status="success"] {{
        background: {colors["success"]};
        color: white;
        border-radius: 12px;
        padding: 4px 12px;
        font-weight: 600;
    }}

    QLabel[status="warning"] {{
        background: {colors["warning"]};
        color: white;
        border-radius: 12px;
        padding: 4px 12px;
        font-weight: 600;
    }}

    QLabel[status="error"] {{
        background: {colors["danger"]};
        color: white;
        border-radius: 12px;
        padding: 4px 12px;
        font-weight: 600;
    }}

    QLabel[status="info"] {{
        background: {colors["accent"]};
        color: white;
        border-radius: 12px;
        padding: 4px 12px;
        font-weight: 600;
    }}
    """
