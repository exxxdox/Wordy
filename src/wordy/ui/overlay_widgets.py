#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Overlay Qt widgets: signals bridge and transparent capsule input box."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QEvent, QObject, QPointF, QSize, Qt, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import (
    QColor, QIcon, QKeyEvent, QLinearGradient, QMouseEvent, QPainter, QPainterPath,
    QPaintEvent, QPen, QPixmap, QPolygonF,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QStyle, QWidget

from wordy.ui.theme import (
    ACCENT_HOVER, ACCENT_SECONDARY, CONFIG_BUTTON_IDLE, GREEN_ACCENT,
    INPUT_BACKGROUND, INPUT_BORDER, MONO_FONT, UI_FONT, TEXT_PRIMARY,
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
SETTINGS_BUTTON_ENTRY_RIGHT_PADDING = 144
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
        self._focus_glow = 0.0
        # 焦点变化只播放一次短动画，不用常驻计时器驱动装饰性闪烁。
        self._focus_animation = QVariantAnimation(self)
        self._focus_animation.setDuration(160)
        self._focus_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._focus_animation.valueChanged.connect(self._set_focus_glow)
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
        self.entry.setPlaceholderText(owner._cfg.overlay_placeholder)
        self.entry.setAccessibleName("朗读文本")
        self.entry.setToolTip("Enter 朗读 · Esc 收起")
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
                color: {TEXT_PRIMARY};
                selection-background-color: {CONFIG_BUTTON_HOVER_TEXT_COLOR};
                selection-color: {INPUT_BACKGROUND_COLOR};
                border: none;
                font-family: {UI_FONT};
                font-size: 24px;
                padding: 0;
            }}
        ''')

        # 显式入口让鼠标用户也能朗读；不抢输入焦点，避免点击时触发失焦收起。
        self.submit_button = QPushButton(self)
        self.submit_button.setObjectName("overlaySubmit")
        self.submit_button.setGeometry(owner.width - 106, 11, 36, owner.height - 22)
        self.submit_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.submit_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.submit_button.setAccessibleName("朗读")
        self.submit_button.setToolTip("朗读并保留输入框")
        self.submit_button.setEnabled(False)
        self.set_submit_hover(False)
        self.submit_button.installEventFilter(signals)
        self.submit_button.setIconSize(QSize(16, 16))
        self.submit_button.clicked.connect(owner._on_read)
        self.entry.textChanged.connect(lambda text: self.submit_button.setEnabled(bool(text.strip())))
        self.submit_button.setStyleSheet(f'''
            QPushButton {{ background: transparent; color: {INPUT_TEXT_COLOR};
                border: none; border-radius: 9px;
                font-size: 22px; padding: 0; }}
            QPushButton:disabled {{ color: {CONFIG_BUTTON_TEXT_COLOR}; background: transparent; }}
        ''')

        # Material Symbols SVG 设置图标（透明背景）
        icon_size = 24
        settings_hit_size = 36  # 扩大命中区域，图标大小不变。
        settings_x = owner.width - SETTINGS_BUTTON_CENTER_X_OFFSET - settings_hit_size // 2
        settings_y = (owner.height - settings_hit_size) // 2
        self.settings_button = QLabel(self)
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.settings_button.setCursor(Qt.CursorShape.PointingHandCursor)
        # 鼠标点击不能先抢走输入焦点，否则 Qt 会在 press 回调前触发失焦收起。
        # TabFocus 保留键盘导航；鼠标点击继续由已有事件过滤器处理。
        self.settings_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.settings_button.setAccessibleName("设置")
        self.settings_button.setToolTip("打开设置")
        self.settings_button.setGeometry(settings_x, settings_y, settings_hit_size, settings_hit_size)
        self.settings_button.setPixmap(self._make_settings_icon(icon_size, CONFIG_BUTTON_TEXT_COLOR))
        self.settings_button.setStyleSheet("background: transparent;")
        self.settings_button.installEventFilter(signals)
        self._settings_icon_size = icon_size
        self.set_settings_hover(False)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        w, h = self.width(), self.height()

        # 使用连续色面与局部光晕表达层次，不绘制环绕输入栏的硬边框。
        bg_color = QColor(INPUT_BACKGROUND_COLOR)
        bg_color.setAlphaF(self._overlay_opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        path = QPainterPath()
        path.addRoundedRect(1, 1, w - 2, h - 2, WINDOW_RADIUS, WINDOW_RADIUS)
        painter.drawPath(path)

        painter.save()
        painter.setClipPath(path)
        dot_color = QColor(INPUT_TEXT_COLOR)
        dot_color.setAlphaF(0.05 * self._overlay_opacity)
        painter.setPen(QPen(dot_color, 1.0))
        for x in range(12, w - 12, 12):
            painter.drawPoint(x, h - 8)
        # 焦点光融入底部色面，随既有短动画过渡，不添加常态描边。
        wash = QLinearGradient(0, 0, 0, h)
        wash.setColorAt(0.0, QColor(Qt.GlobalColor.transparent))
        light = QColor(ACCENT_SECONDARY)
        light.setAlphaF((0.04 + 0.08 * self._focus_glow) * self._overlay_opacity)
        wash.setColorAt(1.0, light)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(wash)
        painter.drawPath(path)
        painter.restore()

    def animate_focus(self, focused: bool) -> None:
        self._focus_animation.stop()
        # 使用 Qt 原生动效偏好，关闭系统动画时直接切换焦点光晕。
        if not self.style().styleHint(QStyle.StyleHint.SH_Widget_Animate):
            self._set_focus_glow(1.0 if focused else 0.0)
            return
        self._focus_animation.setStartValue(self._focus_glow)
        self._focus_animation.setEndValue(1.0 if focused else 0.0)
        self._focus_animation.start()

    def _set_focus_glow(self, value: Any) -> None:
        self._focus_glow = float(value)
        self.update()

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
        # 悬停与键盘聚焦只改变图标颜色，背景持续融入输入栏。
        color = CONFIG_BUTTON_HOVER_TEXT_COLOR if hovered else CONFIG_BUTTON_TEXT_COLOR
        self.settings_button.setPixmap(
            self._make_settings_icon(self._settings_icon_size, color)
        )

    def set_submit_hover(self, hovered: bool) -> None:
        # QSS 的 color 不会给 QIcon 着色，直接重绘几何图标；禁用态仍由 Qt 生成。
        play_icon = QPixmap(16, 16)
        play_icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(play_icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT_HOVER if hovered else INPUT_TEXT_COLOR))
        painter.drawPolygon(QPolygonF([QPointF(4, 2), QPointF(14, 8), QPointF(4, 14)]))
        painter.end()
        self.submit_button.setIcon(QIcon(play_icon))
