#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Overlay Qt widgets: signals bridge and transparent capsule input box."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, QPointF, QRect, QRectF, QSize, Qt, Signal, QVariantAnimation, QEasingCurve
from PySide6.QtGui import (
    QBrush, QColor, QIcon, QKeyEvent, QLinearGradient, QMouseEvent, QPainter,
    QPainterPath, QPaintEvent, QPixmap, QPolygonF,
)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import QLabel, QLineEdit, QPushButton, QStyle, QWidget

from wordy.ui import glass
from wordy.ui.theme import (
    ACCENT_HOVER, ACCENT_SECONDARY, CONFIG_BUTTON_IDLE, GREEN_ACCENT,
    INPUT_BACKGROUND, RADIUS_WINDOW, TEXT_PRIMARY, ui_font_stack,
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
INPUT_TEXT_COLOR = GREEN_ACCENT
CONFIG_BUTTON_TEXT_COLOR = CONFIG_BUTTON_IDLE
CONFIG_BUTTON_HOVER_TEXT_COLOR = ACCENT_HOVER
SETTINGS_BUTTON_CENTER_X_OFFSET = 34
SETTINGS_BUTTON_ENTRY_RIGHT_PADDING = 144
# 左侧提示符 + 缩进
PROMPT_LEFT = 10
ENTRY_LEFT_PADDING = 32
ENTRY_VERTICAL_PADDING = 9
WINDOW_RADIUS = RADIUS_WINDOW
# 胶囊四周留出的一圈余量，专门用来画浮起投影。窗口只比胶囊大这么多：
# 余量不够时投影会被窗口边界裁成硬边，看着像描边而不是浮起来。
# 投影很短（见 glass.SHADOW_STEPS），留白跟着收窄——这圈是点不到的死区。
SHADOW_PAD = 8


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

    PROMPT_FONT_SIZE = 20
    ENTRY_FONT_SIZE = 24
    SUBMIT_FONT_SIZE = 22

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
        # 窗口比胶囊大一圈：多出来的余量是投影的画布，不是内容区。
        self.setFixedSize(owner.width + SHADOW_PAD * 2, owner.height + SHADOW_PAD * 2)
        self.setMouseTracking(True)

        # 字体族由 _apply_font_styles 统一注入——设置页换字体时要能当场重刷。
        self._font_stack = ui_font_stack(owner._cfg.ui_font)
        # 左侧提示符：用系统 UI 字体读作玻璃面板上的一个符号，而不是终端光标。
        self.prompt_label = QLabel(">", self)
        self.prompt_label.setObjectName("overlayPrompt")
        self.prompt_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        prompt_y = (owner.height - self.PROMPT_FONT_SIZE - 4) // 2
        self.prompt_label.setGeometry(
            PROMPT_LEFT + SHADOW_PAD, prompt_y + SHADOW_PAD, 18, self.PROMPT_FONT_SIZE + 4
        )

        self.entry = QLineEdit(self)
        self.entry.setObjectName("overlayEntry")
        self.entry.setFrame(False)
        self.entry.setPlaceholderText(owner._cfg.overlay_placeholder)
        self.entry.setAccessibleName("朗读文本")
        self.entry.setToolTip("Enter 朗读 · Esc 收起")
        self.entry.setGeometry(
            ENTRY_LEFT_PADDING + SHADOW_PAD, ENTRY_VERTICAL_PADDING + SHADOW_PAD,
            owner.width - SETTINGS_BUTTON_ENTRY_RIGHT_PADDING,
            owner.height - ENTRY_VERTICAL_PADDING * 2,
        )
        self.entry.returnPressed.connect(owner._on_return)
        self.entry.installEventFilter(signals)

        # 显式入口让鼠标用户也能朗读；不抢输入焦点，避免点击时触发失焦收起。
        self.submit_button = QPushButton(self)
        self.submit_button.setObjectName("overlaySubmit")
        self.submit_button.setGeometry(
            owner.width - 106 + SHADOW_PAD, 11 + SHADOW_PAD, 36, owner.height - 22
        )
        self.submit_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.submit_button.setCursor(Qt.CursorShape.ArrowCursor)
        self.submit_button.setAccessibleName("朗读")
        self.submit_button.setToolTip("朗读并保留输入框")
        self.submit_button.setEnabled(False)
        self.set_submit_hover(False)
        self.submit_button.installEventFilter(signals)
        self.submit_button.setIconSize(QSize(20, 20))
        self.submit_button.clicked.connect(owner._on_read)
        self.entry.textChanged.connect(self._on_submit_text_changed)

        self._apply_font_styles()

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
        self.settings_button.setGeometry(
            settings_x + SHADOW_PAD, settings_y + SHADOW_PAD, settings_hit_size, settings_hit_size
        )
        self.settings_button.setPixmap(self._make_settings_icon(icon_size, CONFIG_BUTTON_TEXT_COLOR))
        self.settings_button.setStyleSheet("background: transparent;")
        self.settings_button.installEventFilter(signals)
        self._settings_icon_size = icon_size
        self.set_settings_hover(False)

    def content_rect(self) -> QRect:
        """胶囊本体在窗口内的矩形；四周余量只是投影的画布，不是可交互区域。"""
        return self.rect().adjusted(SHADOW_PAD, SHADOW_PAD, -SHADOW_PAD, -SHADOW_PAD)

    def contains_global_point(self, x: int, y: int) -> bool:
        """全局屏幕坐标是否落在胶囊本体上（投影留白不计入）。"""
        rect = self.content_rect()
        return rect.translated(self.mapToGlobal(rect.topLeft())).contains(x, y)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        opacity = self._overlay_opacity
        content = self.content_rect()
        bounds = QRectF(content).adjusted(1, 1, -1, -1)
        # 渐变都按胶囊本体取值：窗口高出来的那圈余量不属于面板。
        top, bottom = content.top(), content.bottom()

        # 先画投影：胶囊浮在桌面上，暗边只出现在它的外侧，内侧由下面的玻璃底盖住。
        glass.paint_drop_shadow(painter, QRectF(content), WINDOW_RADIUS, opacity=opacity)
        path = QPainterPath()
        path.addRoundedRect(bounds, WINDOW_RADIUS, WINDOW_RADIUS)

        # 玻璃底：本体色打底，再叠一层自上而下衰减的白色光泽。
        bg_color = QColor(INPUT_BACKGROUND_COLOR)
        bg_color.setAlphaF(opacity)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(bg_color)
        painter.drawPath(path)

        painter.save()
        painter.setClipPath(path)
        sheen = QLinearGradient(0, top, 0, bottom)
        sheen.setColorAt(0.0, QColor(255, 255, 255, round(26 * opacity)))
        sheen.setColorAt(1.0, QColor(255, 255, 255, round(8 * opacity)))
        painter.setBrush(sheen)
        painter.drawPath(path)

        # 焦点时底部浮起强调色微光，沿用既有短动画，不做常驻闪烁。
        if self._focus_glow > 0.0:
            glow = QLinearGradient(0, top, 0, bottom)
            glow.setColorAt(0.0, QColor(Qt.GlobalColor.transparent))
            light = QColor(ACCENT_SECONDARY)
            light.setAlphaF(0.10 * self._focus_glow * opacity)
            glow.setColorAt(1.0, light)
            painter.setBrush(glow)
            painter.drawPath(path)
        painter.restore()

        # 噪点：打破纯色填充，玻璃才有材质而不只是一块半透明色。
        glass.paint_noise(painter, path)

        # 玻璃边缘：外侧光晕 + 内侧暗过渡 + 上缘最亮的受光棱。
        # 亮棱用相对亮度（alpha 不再乘窗口不透明度），整体透明度由 rim 统一施加。
        edge = QLinearGradient(0, top, 0, bottom)
        edge.setColorAt(0.0, QColor(255, 255, 255, 255))
        edge.setColorAt(0.35, QColor(255, 255, 255, 118))
        edge.setColorAt(1.0, QColor(255, 255, 255, 88))
        glass.paint_glass_rim(painter, bounds, WINDOW_RADIUS, QBrush(edge), opacity=opacity)
        painter.end()

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

    def _apply_font_styles(self) -> None:
        """把当前字体栈写进三处带文字的样式表。

        抽成方法是因为这三条规则里只有 font-family 会随设置变，其余是静态外观；
        换字体时整段重设最省事，也保证三处永远用同一个栈。
        """
        self.prompt_label.setStyleSheet(f'''
            QLabel#overlayPrompt {{
                color: {CONFIG_BUTTON_TEXT_COLOR};
                background: transparent;
                font-family: {self._font_stack};
                font-size: {self.PROMPT_FONT_SIZE}px;
                font-weight: 500;
            }}
        ''')
        self.entry.setStyleSheet(f'''
            QLineEdit#overlayEntry {{
                background: transparent;
                color: {TEXT_PRIMARY};
                selection-background-color: {CONFIG_BUTTON_HOVER_TEXT_COLOR};
                selection-color: {INPUT_BACKGROUND_COLOR};
                border: none;
                font-family: {self._font_stack};
                font-size: {self.ENTRY_FONT_SIZE}px;
                padding: 0;
            }}
        ''')
        # 提交按钮原先没写族名，一直飘在应用默认字体上，跟旁边的输入框不是一套。
        self.submit_button.setStyleSheet(f'''
            QPushButton {{ background: transparent; color: {INPUT_TEXT_COLOR};
                border: none; border-radius: 9px;
                font-family: {self._font_stack};
                font-size: {self.SUBMIT_FONT_SIZE}px; padding: 0; }}
            QPushButton:disabled {{ color: {CONFIG_BUTTON_TEXT_COLOR}; background: transparent; }}
        ''')

    def set_ui_font(self, font_stack: str) -> None:
        """设置页改字体后当场重刷，不必等下次启动。"""
        self._font_stack = font_stack
        self._apply_font_styles()

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
            if not self.content_rect().contains(event.position().toPoint()):
                # 投影留白是视觉装饰：按在上面的算外部点击（收起交给外部点击监视器），
                # 否则拖那圈看不见的区域也能把窗口拖走。
                event.ignore()
                return
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

    def _on_submit_text_changed(self, text: str) -> None:
        # 清空或只剩空格时同步重置图标与光标，避免保留先前的悬停提示。
        enabled = bool(text.strip())
        self.submit_button.setEnabled(enabled)
        self.submit_button.setCursor(Qt.CursorShape.PointingHandCursor if enabled else Qt.CursorShape.ArrowCursor)
        self.set_submit_hover(self.submit_button.underMouse())

    def set_submit_hover(self, hovered: bool) -> None:
        # 禁用按钮也会收到进入事件，不能仅依赖 Qt 的 disabled 绘制颜色。
        hovered = hovered and self.submit_button.isEnabled()
        # QSS 的 color 不会给 QIcon 着色，直接重绘几何图标；禁用态仍由 Qt 生成。
        # 放大图标画布和箭头轮廓，按钮命中区域保持不变。
        play_icon = QPixmap(20, 20)
        play_icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(play_icon)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(ACCENT_HOVER if hovered else CONFIG_BUTTON_TEXT_COLOR))
        painter.drawPolygon(QPolygonF([QPointF(5, 2), QPointF(18, 10), QPointF(5, 18)]))
        painter.end()
        self.submit_button.setIcon(QIcon(play_icon))
