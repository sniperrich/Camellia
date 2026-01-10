"""Custom widgets for the Qt GUI."""

from __future__ import annotations

from PySide6 import QtCore, QtGui, QtWidgets

from .theme import PALETTE
from ..plugins import PluginState


class Backdrop(QtWidgets.QWidget):
    def paintEvent(self, event: QtGui.QPaintEvent) -> None:
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)

        gradient = QtGui.QLinearGradient(0, 0, self.width(), self.height())
        gradient.setColorAt(0.0, QtGui.QColor(PALETTE["bg"]))
        gradient.setColorAt(1.0, QtGui.QColor(PALETTE["bg_alt"]))
        painter.fillRect(self.rect(), gradient)

        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QtGui.QColor(255, 255, 255, 90))
        painter.drawEllipse(QtCore.QPoint(int(self.width() * 0.15), int(self.height() * 0.2)), 140, 140)

        painter.setBrush(QtGui.QColor(24, 106, 92, 60))
        painter.drawEllipse(QtCore.QPoint(int(self.width() * 0.85), int(self.height() * 0.3)), 180, 180)

        painter.setBrush(QtGui.QColor(18, 82, 72, 45))
        painter.drawEllipse(QtCore.QPoint(int(self.width() * 0.75), int(self.height() * 0.8)), 220, 220)


class NavButton(QtWidgets.QPushButton):
    def __init__(self, text: str, parent: QtWidgets.QWidget | None = None) -> None:
        super().__init__(text, parent)
        self.setObjectName("NavButton")
        self.setCheckable(True)
        self.setCursor(QtCore.Qt.PointingHandCursor)


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
        layout.setContentsMargins(16, 14, 16, 14)
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
        layout.setContentsMargins(16, 14, 16, 14)
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
        layout.setContentsMargins(16, 14, 16, 14)
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
