#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Overlay Qt widgets: signals bridge and transparent capsule input box."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, Qt, Signal
from PySide6.QtGui import (
    QColor, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen, QPixmap,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QLineEdit, QWidget

from wordy.ui.theme import (
    ACCENT_HOVER, CONFIG_BUTTON_IDLE, GREEN_ACCENT, INPUT_BACKGROUND, INPUT_BORDER, MONO_FONT,
)

# Material Symbols 设置图标 SVG 路径
_ICONS_DIR = Path(__file__).resolve().parent / "icons"
_SETTINGS_SVG_PATH = _ICONS_DIR / "settings.svg"
# 缓存已加载的 SVG 模板文本
_settings_svg_template: str | None = None

if TYPE_CHECKING:
    from wordy.ui.overlay import InputOverlay

# 控件尺寸/颜色常量
INPUT_BACKGROUND_COLOR = INPUT_BACKGROUND
INPUT_BORDER_COLOR = INPUT_BORDER
INPUT_TEXT_COLOR = GREEN_ACCENT
CONFIG_BUTTON_TEXT_COLOR = CONFIG_BUTTON_IDLE
CONFIG_BUTTON_HOVER_TEXT_COLOR = ACCENT_HOVER
SETTINGS_BUTTON_CENTER_X_OFFSET = 34
SETTINGS_BUTTON_ENTRY_RIGHT_PADDING = 92
# 终端风格：左侧 prompt + 缩进
PROMPT_LEFT = 10
ENTRY_LEFT_PADDING = 32
ENTRY_VERTICAL_PADDING = 9
WINDOW_RADIUS = 14


class _OverlaySignals(QObject):
    """将后台线程事件安全转发到 Qt 主线程。"""

    hotkey_triggered = Signal()
    record_finished = Signal(object, object)
    # 请求序号和服务商随结果返回，GUI 线程可拒绝延迟到达的旧结果。
    voices_loaded = Signal(int, str, object)
    voices_error = Signal(int, str, object)

    def __init__(self, owner: "InputOverlay") -> None:
        super().__init__()
        self._owner = owner

    def eventFilter(self, watched: QObject, event: Any) -> bool:
        return self._owner._event_filter(watched, event)


class _OverlayWidget(QWidget):
    """无边框透明胶囊输入框 —— 终端风格。"""

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

        # 终端风格 `>` 提示符
        prompt_font_size = 22
        self.prompt_label = QLabel(">", self)
        self.prompt_label.setObjectName("overlayPrompt")
        self.prompt_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        prompt_y = (owner.height - prompt_font_size - 4) // 2
        self.prompt_label.setGeometry(PROMPT_LEFT, prompt_y, 18, prompt_font_size + 4)
        self.prompt_label.setStyleSheet(f'''
            QLabel#overlayPrompt {{
                color: {CONFIG_BUTTON_TEXT_COLOR};
                background: transparent;
                font-family: {MONO_FONT};
                font-size: {prompt_font_size}px;
                font-weight: bold;
            }}
        ''')

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
                font-family: {MONO_FONT};
                font-size: 24px;
                padding: 0;
            }}
        ''')

        # Material Symbols SVG 设置图标（透明背景）
        icon_size = 24
        settings_hit_size = icon_size + 4  # 28px 点击区域
        settings_x = owner.width - SETTINGS_BUTTON_CENTER_X_OFFSET - settings_hit_size // 2
        settings_y = (owner.height - settings_hit_size) // 2
        self.settings_button = QLabel(self)
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.settings_button.setGeometry(settings_x, settings_y, settings_hit_size, settings_hit_size)
        self.settings_button.setPixmap(self._make_settings_icon(icon_size, CONFIG_BUTTON_TEXT_COLOR))
        self.settings_button.installEventFilter(signals)
        self._settings_icon_size = icon_size
        self.set_settings_hover(False)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # 1. 点阵背景纹理 —— 终端屏幕质感
        dot_color = QColor(INPUT_TEXT_COLOR)
        dot_color.setAlphaF(0.025 * self._overlay_opacity)
        dot_pen = QPen(dot_color, 1.0)
        dot_pen.setDashPattern([1, 5])
        painter.setPen(dot_pen)
        for y in range(2, h - 2, 6):
            painter.drawLine(2, y, w - 2, y)

        # 2. 胶囊背景
        bg_color = QColor(INPUT_BACKGROUND_COLOR)
        bg_color.setAlphaF(self._overlay_opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        path = QPainterPath()
        path.addRoundedRect(1, 1, w - 2, h - 2, WINDOW_RADIUS, WINDOW_RADIUS)
        painter.drawPath(path)

        # 3. 内发光边框 —— 绿色微光
        glow_outer = QColor(INPUT_TEXT_COLOR)
        glow_outer.setAlphaF(0.12 * self._overlay_opacity)
        glow_pen = QPen(glow_outer, 2.0)
        painter.setPen(glow_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(2, 2, w - 4, h - 4, WINDOW_RADIUS - 1, WINDOW_RADIUS - 1)

        # 5. 外边框
        border_color = QColor(INPUT_BORDER_COLOR)
        border_color.setAlphaF(self._overlay_opacity)
        painter.setPen(QPen(border_color, 1.0))
        painter.drawRoundedRect(1, 1, w - 2, h - 2, WINDOW_RADIUS, WINDOW_RADIUS)

    @staticmethod
    def _make_settings_icon(size: int, color: str) -> QPixmap:
        """用 Material Symbols SVG 渲染设置齿轮图标，替换颜色后光栅化。"""
        global _settings_svg_template
        if _settings_svg_template is None:
            _settings_svg_template = _SETTINGS_SVG_PATH.read_text(encoding="utf-8")

        # Material Symbols 默认 fill="#e3e3e3"，替换为目标色
        svg_data = _settings_svg_template.replace("#e3e3e3", color)

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        renderer = QSvgRenderer(svg_data.encode("utf-8"))
        p = QPainter(pixmap)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(p)
        p.end()
        return pixmap

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
        self.settings_button.setPixmap(
            self._make_settings_icon(self._settings_icon_size, color)
        )
