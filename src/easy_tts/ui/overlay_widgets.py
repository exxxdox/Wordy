#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Overlay Qt widgets: signals bridge and transparent capsule input box."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPaintEvent
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from easy_tts.ui.theme import ACCENT_HOVER, CONFIG_BUTTON_IDLE, GREEN_ACCENT, INPUT_BACKGROUND, INPUT_BORDER

if TYPE_CHECKING:
    from easy_tts.ui.overlay import InputOverlay

# 控件尺寸/颜色常量
INPUT_BACKGROUND_COLOR = INPUT_BACKGROUND
INPUT_BORDER_COLOR = INPUT_BORDER
INPUT_TEXT_COLOR = GREEN_ACCENT
CONFIG_BUTTON_TEXT_COLOR = CONFIG_BUTTON_IDLE
CONFIG_BUTTON_HOVER_TEXT_COLOR = ACCENT_HOVER
SETTINGS_BUTTON_FONT_SIZE = 18
SETTINGS_BUTTON_CENTER_X_OFFSET = 34
SETTINGS_BUTTON_ENTRY_RIGHT_PADDING = 92
ENTRY_LEFT_PADDING = 20
ENTRY_VERTICAL_PADDING = 9
WINDOW_RADIUS = 18


class _OverlaySignals(QObject):
    """将后台线程事件安全转发到 Qt 主线程。"""

    hotkey_triggered = Signal()
    record_finished = Signal(object, object)
    voices_loaded = Signal(object)
    voices_error = Signal(object)

    def __init__(self, owner: "InputOverlay") -> None:
        super().__init__()
        self._owner = owner

    def eventFilter(self, watched: QObject, event: Any) -> bool:
        return self._owner._event_filter(watched, event)


class _OverlayWidget(QWidget):
    """无边框透明胶囊输入框。"""

    def __init__(self, owner: "InputOverlay", signals: _OverlaySignals) -> None:
        super().__init__()
        self._owner = owner
        self._overlay_opacity: float = 1.0
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(owner.width, owner.height)
        self.setMouseTracking(True)

        self.entry = QLineEdit(self)
        self.entry.setObjectName("overlayEntry")
        self.entry.setFrame(False)
        self.entry.setGeometry(
            ENTRY_LEFT_PADDING, ENTRY_VERTICAL_PADDING,
            owner.width - SETTINGS_BUTTON_ENTRY_RIGHT_PADDING,
            owner.height - ENTRY_VERTICAL_PADDING * 2,
        )
        self.entry.returnPressed.connect(owner._on_return)
        self.entry.installEventFilter(signals)
        self.entry.setStyleSheet(f'''
            QLineEdit#overlayEntry {{
                background: transparent;
                color: {INPUT_TEXT_COLOR};
                selection-background-color: {CONFIG_BUTTON_HOVER_TEXT_COLOR};
                selection-color: {INPUT_BACKGROUND_COLOR};
                border: none;
                font-family: "Segoe UI";
                font-size: 24px;
                padding: 0;
            }}
        ''')

        self.settings_button = QLabel("⚙", self)
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        settings_hit_size = SETTINGS_BUTTON_FONT_SIZE + 6
        settings_x = owner.width - SETTINGS_BUTTON_CENTER_X_OFFSET - settings_hit_size // 2
        settings_y = (owner.height - settings_hit_size) // 2
        self.settings_button.setGeometry(settings_x, settings_y, settings_hit_size, settings_hit_size)
        self.settings_button.installEventFilter(signals)
        self.set_settings_hover(False)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        border_color = QColor(INPUT_BORDER_COLOR)
        background_color = QColor(INPUT_BACKGROUND_COLOR)
        border_color.setAlphaF(self._overlay_opacity)
        background_color.setAlphaF(self._overlay_opacity)
        painter.setPen(border_color)
        painter.setBrush(background_color)
        path = QPainterPath()
        path.addRoundedRect(1, 1, self.width() - 2, self.height() - 2, WINDOW_RADIUS, WINDOW_RADIUS)
        painter.drawPath(path)

    def set_overlay_opacity(self, opacity: float) -> None:
        self._overlay_opacity = opacity
        self.update()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self._owner.hide()
            event.accept()
            return
        super().keyPressEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._owner._on_overlay_mouse_press(event)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._owner._on_overlay_mouse_move(event)
        if self._owner._is_dragging_window:
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._owner._on_overlay_mouse_release()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event: Any) -> None:
        self._owner._on_focus_out()
        super().focusOutEvent(event)

    def set_settings_hover(self, hovered: bool) -> None:
        color = CONFIG_BUTTON_HOVER_TEXT_COLOR if hovered else CONFIG_BUTTON_TEXT_COLOR
        self.settings_button.setStyleSheet(f'''
            QLabel#settingsButton {{
                color: {color};
                background: transparent;
                font-family: "Segoe UI";
                font-size: {SETTINGS_BUTTON_FONT_SIZE}px;
            }}
        ''')
