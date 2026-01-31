"""Custom widgets for the Qt GUI."""

from __future__ import annotations

import math

from PySide6 import QtCore, QtGui, QtWidgets

from .theme import PALETTE
from ..plugins import PluginState


def _draw_camellia(
    painter: QtGui.QPainter,
    center: QtCore.QPointF,
    size: float,
    petal_color: QtGui.QColor,
    core_color: QtGui.QColor,
) -> None:
    radius = size * 0.28
    petal_radius = size * 0.22
    painter.setPen(QtCore.Qt.NoPen)
    for idx in range(5):
        angle = math.radians(idx * 72)
        offset = QtCore.QPointF(
            radius * math.cos(angle),
            radius * math.sin(angle),
        )
        painter.setBrush(petal_color)
        painter.drawEllipse(center + offset, petal_radius, petal_radius)
    painter.setBrush(core_color)
    painter.drawEllipse(center, petal_radius * 0.9, petal_radius * 0.9)


class Backdrop(QtWidgets.QWidget):
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        gradient = QtGui.QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QtGui.QColor(PALETTE["bg"]))
        gradient.setColorAt(1.0, QtGui.QColor(PALETTE["bg_alt"]))
        painter.fillRect(self.rect(), gradient)

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(255, 255, 255, 110))
        painter.drawEllipse(QtCore.QPoint(int(self.width() * 0.12), int(self.height() * 0.18)), 180, 180)

        painter.setBrush(QtGui.QColor(47, 139, 124, 55))
        painter.drawEllipse(QtCore.QPoint(int(self.width() * 0.85), int(self.height() * 0.25)), 220, 220)

        painter.setBrush(QtGui.QColor(34, 104, 93, 45))
        painter.drawEllipse(QtCore.QPoint(int(self.width() * 0.78), int(self.height() * 0.82)), 260, 260)

        # Subtle camellia motifs
        accent = QtGui.QColor(PALETTE["accent"])
        accent_light = QtGui.QColor(PALETTE.get("accent_light", PALETTE["accent"]))
        accent.setAlpha(60)
        accent_light.setAlpha(70)
        _draw_camellia(painter, QtCore.QPointF(self.width() * 0.18, self.height() * 0.78), 72, accent_light, accent)
        _draw_camellia(painter, QtCore.QPointF(self.width() * 0.82, self.height() * 0.12), 56, accent_light, accent)
        _draw_camellia(painter, QtCore.QPointF(self.width() * 0.48, self.height() * 0.92), 64, accent_light, accent)


class CamelliaLogo(QtWidgets.QWidget):
    def __init__(self, size: int = 44, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self._size = size
        self.setFixedSize(size, size)

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        center = QtCore.QPointF(self.width() / 2, self.height() / 2)

        base = QtGui.QColor(PALETTE["accent"])
        core = QtGui.QColor(PALETTE["accent_dark"])
        core.setAlpha(170)
        _draw_camellia(painter, center, float(self._size), base, core)


def make_nav_icon(kind: str, size: int = 18, active: bool = False) -> QtGui.QIcon:
    pixmap = QtGui.QPixmap(size, size)
    pixmap.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(pixmap)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

    if active:
        color = QtGui.QColor(PALETTE["accent"])
    else:
        color = QtGui.QColor(PALETTE.get("text_secondary", PALETTE["muted"]))
    accent = QtGui.QColor(PALETTE["accent"])
    pen = QtGui.QPen(color, 1.6, QtCore.Qt.SolidLine, QtCore.Qt.RoundCap, QtCore.Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(QtCore.Qt.NoBrush)

    rect = QtCore.QRectF(2, 2, size - 4, size - 4)

    if kind == "login":
        painter.drawRoundedRect(rect, 3, 3)
        painter.drawLine(rect.left() + rect.width() * 0.55, rect.top() + 3, rect.left() + rect.width() * 0.55, rect.bottom() - 3)
        painter.setBrush(color)
        painter.drawEllipse(QtCore.QPointF(rect.left() + 5, rect.center().y()), 1.2, 1.2)
    elif kind == "servers":
        for offset in (4, 8, 12):
            painter.drawLine(4, offset, size - 4, offset)
    elif kind == "characters":
        painter.drawEllipse(QtCore.QPointF(size / 2, 6.5), 3.2, 3.2)
        painter.drawRoundedRect(QtCore.QRectF(4, 10, size - 8, 5), 2, 2)
    elif kind == "connection":
        painter.drawEllipse(QtCore.QPointF(6.5, 9), 3.5, 3.5)
        painter.drawEllipse(QtCore.QPointF(size - 6.5, 9), 3.5, 3.5)
        painter.drawLine(9, 9, size - 9, 9)
    elif kind == "skins":
        path = QtGui.QPainterPath()
        path.moveTo(size / 2, 3)
        path.cubicTo(3, 8, 5, size - 4, size / 2, size - 3)
        path.cubicTo(size - 5, size - 4, size - 3, 8, size / 2, 3)
        painter.drawPath(path)
    elif kind == "plugins":
        painter.drawRoundedRect(QtCore.QRectF(4, 5, 8, 8), 2, 2)
        painter.drawLine(8, 3, 8, 5)
        painter.drawLine(6, 3, 6, 5)
    elif kind == "settings":
        painter.drawEllipse(QtCore.QPointF(size / 2, size / 2), 5.5, 5.5)
        painter.drawEllipse(QtCore.QPointF(size / 2, size / 2), 1.6, 1.6)
    else:
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(accent)
        painter.drawEllipse(QtCore.QPointF(size / 2, size / 2), 4.5, 4.5)

    painter.end()
    return QtGui.QIcon(pixmap)


class NavButton(QtWidgets.QPushButton):
    def __init__(
        self,
        text: str,
        icon: QtGui.QIcon | None = None,
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(text, parent)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)
        if icon is not None:
            self.setIcon(icon)
            self.setIconSize(QtCore.QSize(18, 18))


class InfoRow(QtWidgets.QFrame):
    def __init__(self, label: str, value: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.label = QtWidgets.QLabel(label)
        self.label.setProperty("muted", "true")
        self.value = QtWidgets.QLabel(value)
        self.value.setWordWrap(True)

        layout.addWidget(self.label)
        layout.addStretch(1)
        layout.addWidget(self.value)

    def set_value(self, value: str) -> None:
        self.value.setText(value)


class ServerCard(QtWidgets.QFrame):
    selected = QtCore.Signal(object)

    def __init__(self, server: object, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.server = server
        self.setProperty("card", "true")
        self.setProperty("selected", "false")
        self.setCursor(QtCore.Qt.PointingHandCursor)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(8)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(getattr(server, "name", "未知服务器"))
        title.setStyleSheet("font-weight: 600; font-size: 15px;")
        title.setWordWrap(True)
        header.addWidget(title)
        header.addStretch(1)

        badge = QtWidgets.QLabel(f"编号 {getattr(server, 'entity_id', '')}")
        badge.setProperty("muted", "true")
        badge.setStyleSheet("font-size: 11px;")
        header.addWidget(badge)

        summary = QtWidgets.QLabel(getattr(server, "brief_summary", "") or "暂无简介。")
        summary.setWordWrap(True)
        summary.setProperty("muted", "true")

        footer = QtWidgets.QHBoxLayout()
        online = getattr(server, "online_count", "")
        online_label = QtWidgets.QLabel(f"在线人数：{online}" if online else "在线人数：--")
        online_label.setProperty("muted", "true")
        footer.addWidget(online_label)
        footer.addStretch(1)

        select_button = QtWidgets.QPushButton("选择")
        select_button.setProperty("variant", "primary")
        select_button.clicked.connect(self._emit_selected)
        footer.addWidget(select_button)

        layout.addLayout(header)
        layout.addWidget(summary)
        layout.addLayout(footer)

    def mousePressEvent(self, event: QtGui.QMouseEvent) -> None:
        self._emit_selected()
        super().mousePressEvent(event)

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", "true" if selected else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def _emit_selected(self) -> None:
        self.selected.emit(self.server)


class SkinCard(QtWidgets.QFrame):
    apply_requested = QtCore.Signal(object)

    def __init__(self, skin: object, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.skin = skin
        self.setProperty("card", "true")

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(14)

        self.image_label = QtWidgets.QLabel("加载中")
        self.image_label.setAlignment(QtCore.Qt.AlignCenter)
        self.image_label.setFixedSize(120, 120)
        self.image_label.setStyleSheet(
            f"background: {PALETTE['panel_alt']}; border-radius: 12px; color: {PALETTE['muted']};"
        )
        layout.addWidget(self.image_label)

        info_layout = QtWidgets.QVBoxLayout()
        info_layout.setSpacing(6)

        title = QtWidgets.QLabel(getattr(skin, "name", "未知皮肤"))
        title.setStyleSheet("font-weight: 600; font-size: 15px;")

        summary = QtWidgets.QLabel(getattr(skin, "brief_summary", "") or "暂无简介。")
        summary.setWordWrap(True)
        summary.setProperty("muted", "true")

        meta_row = QtWidgets.QHBoxLayout()
        likes = getattr(skin, "like_num", 0)
        downloads = getattr(skin, "download_num", 0)
        like_label = QtWidgets.QLabel(f"点赞：{likes}" if likes else "点赞：--")
        like_label.setProperty("muted", "true")
        meta_row.addWidget(like_label)
        if downloads:
            download_label = QtWidgets.QLabel(f"下载：{downloads}")
            download_label.setProperty("muted", "true")
            meta_row.addWidget(download_label)
        meta_row.addStretch(1)

        actions = QtWidgets.QHBoxLayout()
        apply_button = QtWidgets.QPushButton("应用皮肤")
        apply_button.setProperty("variant", "primary")
        apply_button.clicked.connect(self._emit_apply)
        actions.addWidget(apply_button)
        actions.addStretch(1)

        info_layout.addWidget(title)
        info_layout.addWidget(summary)
        info_layout.addLayout(meta_row)
        info_layout.addLayout(actions)

        layout.addLayout(info_layout, 1)

    def set_image(self, pixmap: QtGui.QPixmap) -> None:
        if pixmap.isNull():
            return
        scaled = pixmap.scaled(
            self.image_label.size(),
            QtCore.Qt.KeepAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
        self.image_label.setText("")
        self.image_label.setPixmap(scaled)

    def _emit_apply(self) -> None:
        self.apply_requested.emit(self.skin)


class PluginCard(QtWidgets.QFrame):
    toggle_requested = QtCore.Signal(str, bool)

    def __init__(self, state: PluginState, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.state = state
        self.setProperty("card", "true")

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(state.meta.name or "未知插件")
        title.setStyleSheet("font-weight: 600; font-size: 15px;")
        header.addWidget(title)

        version = QtWidgets.QLabel(state.meta.version or "--")
        version.setProperty("muted", "true")
        version.setStyleSheet("font-size: 12px;")
        header.addWidget(version)
        header.addStretch(1)

        status_text = "已启用" if state.enabled else "已禁用"
        self.status_label = QtWidgets.QLabel(status_text)
        if state.enabled:
            self.status_label.setStyleSheet(f"color: {PALETTE['accent']}; font-weight: 600;")
        else:
            self.status_label.setProperty("muted", "true")
        header.addWidget(self.status_label)

        layout.addLayout(header)

        desc = QtWidgets.QLabel(state.meta.description or "暂无描述。")
        desc.setWordWrap(True)
        desc.setProperty("muted", "true")
        layout.addWidget(desc)

        meta_row = QtWidgets.QHBoxLayout()
        author = state.meta.author or "未知作者"
        meta_row.addWidget(QtWidgets.QLabel(f"作者：{author}"))
        meta_row.addStretch(1)
        layout.addLayout(meta_row)

        dep_row = QtWidgets.QHBoxLayout()
        deps = state.meta.dependencies or []
        dep_text = "依赖：无" if not deps else "依赖：" + ", ".join(deps)
        dep_label = QtWidgets.QLabel(dep_text)
        dep_label.setProperty("muted", "true")
        dep_row.addWidget(dep_label)
        dep_row.addStretch(1)
        layout.addLayout(dep_row)

        id_label = QtWidgets.QLabel(f"插件ID：{state.meta.plugin_id}")
        id_label.setProperty("muted", "true")
        layout.addWidget(id_label)

        path_label = QtWidgets.QLabel(f"路径：{state.path.name}")
        path_label.setProperty("muted", "true")
        layout.addWidget(path_label)

        if state.error:
            error_label = QtWidgets.QLabel(f"加载错误：{state.error}")
            error_label.setStyleSheet(f"color: {PALETTE['danger']};")
            error_label.setWordWrap(True)
            layout.addWidget(error_label)

        action_row = QtWidgets.QHBoxLayout()
        self.toggle_button = QtWidgets.QPushButton("禁用" if state.enabled else "启用")
        self.toggle_button.setProperty("variant", "danger" if state.enabled else "primary")
        self.toggle_button.clicked.connect(self._emit_toggle)
        action_row.addWidget(self.toggle_button)
        action_row.addStretch(1)
        layout.addLayout(action_row)

    def _emit_toggle(self) -> None:
        self.toggle_requested.emit(self.state.meta.plugin_id, self.state.enabled)


# ============================================================================
# Material 3 Components
# ============================================================================


class LoadingSpinner(QtWidgets.QWidget):
    """
    Material 3 style circular loading spinner.

    A smooth rotating circular progress indicator.
    """

    def __init__(self, size: int = 32, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedSize(size, size)
        self._angle = 0
        self._segment_count = 12
        self._timer = QtCore.QTimer(self)
        self._timer.timeout.connect(self._rotate)
        self._timer.setInterval(16)  # ~60 FPS

    def start(self) -> None:
        """Start the spinner animation."""
        self._timer.start()
        self.show()

    def stop(self) -> None:
        """Stop the spinner animation."""
        self._timer.stop()
        self.hide()

    def _rotate(self) -> None:
        """Rotate the spinner."""
        self._angle = (self._angle + 6) % 360
        self.update()

    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        center = QtCore.QPointF(self.width() / 2, self.height() / 2)
        outer = min(self.width(), self.height()) / 2 - 2
        inner = outer * 0.45

        painter.translate(center)
        painter.rotate(self._angle)

        for idx in range(self._segment_count):
            strength = (idx + 1) / self._segment_count
            alpha = int(40 + 200 * (strength ** 1.6))
            color = QtGui.QColor(PALETTE["accent"])
            color.setAlpha(alpha)
            pen = QtGui.QPen(color, max(2.0, self.width() * 0.08))
            pen.setCapStyle(QtCore.Qt.RoundCap)
            painter.setPen(pen)
            painter.drawLine(0, -outer, 0, -inner)
            painter.rotate(360 / self._segment_count)


class LoadingOverlay(QtWidgets.QWidget):
    """
    Material 3 style full-page loading overlay.

    Displays a semi-transparent backdrop with a centered spinner and message.
    """

    def __init__(self, message: str = "加载中...", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.setStyleSheet(f"""
            LoadingOverlay {{
                background: rgba(8, 10, 12, 0.45);
            }}
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setAlignment(QtCore.Qt.AlignCenter)

        # Container for spinner and message
        container = QtWidgets.QFrame()
        container.setObjectName("OverlayCard")
        container.setStyleSheet(f"""
            QFrame#OverlayCard {{
                background: {PALETTE['panel']};
                border-radius: 18px;
                border: 1px solid {PALETTE['border']};
                padding: 20px 24px;
            }}
        """)
        shadow = QtWidgets.QGraphicsDropShadowEffect(container)
        shadow.setBlurRadius(30)
        shadow.setOffset(0, 10)
        shadow.setColor(QtGui.QColor(0, 0, 0, 90))
        container.setGraphicsEffect(shadow)

        container_layout = QtWidgets.QVBoxLayout(container)
        container_layout.setSpacing(12)
        container_layout.setAlignment(QtCore.Qt.AlignCenter)

        # Brand + spinner
        brand_row = QtWidgets.QHBoxLayout()
        brand_row.setAlignment(QtCore.Qt.AlignCenter)
        brand_icon = CamelliaLogo(size=20)
        brand_label = QtWidgets.QLabel("Camellia")
        brand_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        brand_row.addWidget(brand_icon)
        brand_row.addWidget(brand_label)
        container_layout.addLayout(brand_row)

        self.spinner = LoadingSpinner(30)
        spinner_container = QtWidgets.QHBoxLayout()
        spinner_container.addStretch()
        spinner_container.addWidget(self.spinner)
        spinner_container.addStretch()
        container_layout.addLayout(spinner_container)

        # Message
        self.message_label = QtWidgets.QLabel(message)
        self.message_label.setAlignment(QtCore.Qt.AlignCenter)
        self.message_label.setStyleSheet("font-size: 13px; font-weight: 600;")
        container_layout.addWidget(self.message_label)

        self.sub_label = QtWidgets.QLabel("请稍候")
        self.sub_label.setAlignment(QtCore.Qt.AlignCenter)
        self.sub_label.setProperty("muted", "true")
        self.sub_label.setStyleSheet("font-size: 12px;")
        container_layout.addWidget(self.sub_label)

        layout.addWidget(container)

        self.hide()

    def show_loading(self, message: str = "加载中...") -> None:
        """Show the overlay with a message."""
        self.message_label.setText(message)
        self.spinner.start()
        self.show()
        self.raise_()

    def hide_loading(self) -> None:
        """Hide the overlay."""
        self.spinner.stop()
        self.hide()

    def resizeEvent(self, event: QtCore.QEvent) -> None:
        """Ensure overlay covers the entire parent."""
        if self.parent():
            self.setGeometry(self.parent().rect())
        super().resizeEvent(event)


class InlineLoadingIndicator(QtWidgets.QWidget):
    """
    Material 3 style inline loading indicator.

    A small spinner with optional text, suitable for inline use.
    """

    def __init__(self, text: str = "", parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.spinner = LoadingSpinner(16)
        layout.addWidget(self.spinner)

        self.text_label = QtWidgets.QLabel(text)
        self.text_label.setProperty("muted", "true")
        layout.addWidget(self.text_label)

        layout.addStretch()

    def start(self, text: str = "") -> None:
        """Start the indicator."""
        if text:
            self.text_label.setText(text)
        self.spinner.start()
        self.show()

    def stop(self) -> None:
        """Stop the indicator."""
        self.spinner.stop()
        self.hide()


class StatusBadge(QtWidgets.QLabel):
    """
    Material 3 style status badge.

    Displays status with color-coded background and icon.
    """

    # Status types
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"
    INFO = "info"

    def __init__(self, text: str = "", status: str = INFO, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self._status = status
        self.setAlignment(QtCore.Qt.AlignCenter)
        self._update_style()

    def set_status(self, status: str, text: str = "") -> None:
        """Update the badge status and text."""
        self._status = status
        if text:
            self.setText(text)
        self._update_style()

    def _update_style(self) -> None:
        """Update the badge styling based on status."""
        colors = {
            self.SUCCESS: ("#1B5E20", "#C8E6C9"),  # Dark green, light green
            self.WARNING: ("#E65100", "#FFE0B2"),  # Dark orange, light orange
            self.ERROR: ("#B71C1C", "#FFCDD2"),    # Dark red, light red
            self.INFO: ("#01579B", "#B3E5FC"),     # Dark blue, light blue
        }

        text_color, bg_color = colors.get(self._status, colors[self.INFO])

        self.setStyleSheet(f"""
            QLabel {{
                background: {bg_color};
                color: {text_color};
                border-radius: 12px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: 600;
            }}
        """)


class EmptyState(QtWidgets.QWidget):
    """
    Material 3 style empty state placeholder.

    Displays a friendly message when there's no content to show.
    """

    def __init__(
        self,
        title: str = "暂无内容",
        message: str = "",
        icon_text: str = "📭",
        parent: QtWidgets.QWidget | None = None,
    ) -> None:
        super().__init__(parent)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setAlignment(QtCore.Qt.AlignCenter)
        layout.setSpacing(16)

        # Icon
        icon_label = QtWidgets.QLabel(icon_text)
        icon_label.setAlignment(QtCore.Qt.AlignCenter)
        icon_label.setStyleSheet("font-size: 64px;")
        layout.addWidget(icon_label)

        # Title
        title_label = QtWidgets.QLabel(title)
        title_label.setAlignment(QtCore.Qt.AlignCenter)
        title_label.setStyleSheet("font-size: 18px; font-weight: 600;")
        layout.addWidget(title_label)

        # Message
        if message:
            message_label = QtWidgets.QLabel(message)
            message_label.setAlignment(QtCore.Qt.AlignCenter)
            message_label.setWordWrap(True)
            message_label.setProperty("muted", "true")
            layout.addWidget(message_label)


class PortInputWithStatus(QtWidgets.QWidget):
    """
    Material 3 style port input with availability status indicator.

    Shows a green checkmark if port is available, red X if not.
    """

    port_changed = QtCore.Signal(int)

    def __init__(self, default_port: int = 6445, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(parent)

        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        # Port input
        self.port_input = QtWidgets.QSpinBox()
        self.port_input.setRange(1024, 65535)
        self.port_input.setValue(default_port)
        self.port_input.valueChanged.connect(self._on_port_changed)
        layout.addWidget(self.port_input)

        # Status indicator
        self.status_label = QtWidgets.QLabel("✓")
        self.status_label.setFixedSize(20, 20)
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        self.status_label.setStyleSheet(f"""
            QLabel {{
                color: {PALETTE['accent']};
                font-size: 16px;
                font-weight: bold;
            }}
        """)
        layout.addWidget(self.status_label)

        # Check initial port
        self._check_port_availability()

    def _on_port_changed(self, port: int) -> None:
        """Handle port value change."""
        self._check_port_availability()
        self.port_changed.emit(port)

    def _check_port_availability(self) -> None:
        """Check if the current port is available."""
        from .utils import is_port_available

        port = self.port_input.value()
        available = is_port_available(port)

        if available:
            self.status_label.setText("✓")
            self.status_label.setStyleSheet(f"""
                QLabel {{
                    color: #2E7D32;
                    font-size: 16px;
                    font-weight: bold;
                }}
            """)
        else:
            self.status_label.setText("✗")
            self.status_label.setStyleSheet(f"""
                QLabel {{
                    color: #C62828;
                    font-size: 16px;
                    font-weight: bold;
                }}
            """)

    def value(self) -> int:
        """Get the current port value."""
        return self.port_input.value()

    def setValue(self, port: int) -> None:
        """Set the port value."""
        self.port_input.setValue(port)
