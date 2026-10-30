"""不抢游戏焦点的八向音频轮盘：按住、指向、松开播放。"""

from __future__ import annotations

import ctypes
import math
import sys
from collections.abc import Callable

from PySide6.QtCore import QEasingCurve, QPoint, QPointF, QRectF, Qt, QTimer, QVariantAnimation
from PySide6.QtGui import (
    QColor, QCursor, QGuiApplication, QKeyEvent, QMouseEvent,
    QPaintEvent, QPainter, QPen,
)
from PySide6.QtWidgets import QWidget

from wordy.ui.wheel_paint import WheelHover, paint_wheel
from wordy.ui.theme import (
    GREEN_ACCENT, TEXT_PRIMARY, WINDOW_BG, tint,
)
from wordy.ui.window import clamp_window_position


def _key_down(key: int) -> bool:
    if not sys.platform.startswith("win"):
        return False
    return bool(ctypes.windll.user32.GetAsyncKeyState(key) & 0x8000)


class ChatWheel(QWidget):
    def __init__(self, on_select: Callable[[int], None]) -> None:
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
                         | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        # 游戏继续接收输入；通过全局轮询检测按键释放，而非激活窗口捕获键盘。
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setMouseTracking(True)
        self.setFixedSize(540, 540)
        self.setWindowTitle("Wordy · 音频轮盘")
        self._on_select = on_select
        self._labels = [""] * 8
        self._style = "classic"
        self._selected: int | None = None
        self._key_code: int | None = None
        self._origin: QPoint | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._poll)
        self._closing = False
        self._opacity = 0.0
        self._hover_animation = WheelHover(self)
        self._fade = QVariantAnimation(self)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.valueChanged.connect(self._set_opacity)
        self._fade.finished.connect(self._fade_finished)

    def open_wheel(self, labels: list[str], key_code: int | None = None,
                   *, style: str = "classic") -> None:
        if self.is_active():
            return
        was_closing = self._closing
        self._fade.stop()
        self._closing = False
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        if not was_closing:
            self._opacity = 0.0
        self._hover_animation.reset()
        self._labels = [(label.strip() if label else "") for label in (labels + [""] * 8)[:8]]
        # 每轮唤出固定外观快照，样式切换不干扰正在进行的选择与松键播放。
        self._style = style
        self._selected = None
        self._key_code = key_code
        position = QCursor.pos()
        self._origin = QPoint(position)
        screen = QGuiApplication.screenAt(position) or QGuiApplication.primaryScreen()
        x, y = clamp_window_position(screen, self.width(), self.height(),
                                     position.x() - 270, position.y() - 270)
        self.move(x, y)
        # 只定位窗口，不移动游戏光标；show 前的窗口映射在首开/跨屏时也可能尚未稳定。
        self.show()
        self._animate_opacity(1.0, 120)
        self._timer.start()
        self.update()

    def is_active(self) -> bool:
        return self.isVisible() and not self._closing

    def _set_opacity(self, value: float) -> None:
        self._opacity = value
        self.update()

    def _animate_opacity(self, target: float, duration: int) -> None:
        start = self._opacity
        self._fade.stop()
        self._fade.setCurrentTime(0)
        self._fade.setDuration(duration)
        self._fade.setStartValue(start)
        self._fade.setEndValue(target)
        self._fade.start()

    def _fade_finished(self) -> None:
        # 同一动画由重开操作停止并改向，旧淡出不能收起新一轮选择。
        if self._closing:
            self.cancel()

    def dismiss(self) -> None:
        if not self.is_active():
            return
        self._timer.stop()
        self._origin = None
        self._key_code = None
        self._closing = True
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        if self._opacity <= 0.0:
            self.cancel()
        else:
            self._animate_opacity(0.0, 90)

    def cancel(self) -> None:
        # 控制器停机或重绑热键必须立即关闭，不等待视觉动画。
        self._fade.stop()
        self._hover_animation.reset()
        self._timer.stop()
        self.hide()
        self._closing = False
        self._opacity = 0.0
        self._selected = None
        # 结束选择也不改光标，保留用户移动后的实际位置。
        self._origin = None

    def _slot_at(self, point: QPoint) -> int | None:
        dx, dy = point.x() - 270, point.y() - 270
        if math.hypot(dx, dy) < 76:
            return None
        # 正上方为 0，顺时针每 45 度一个槽；远处拖动仍按方向选择。
        angle = math.degrees(math.atan2(dx, -dy)) % 360
        return int((angle + 22.5) % 360 // 45)

    def _commit(self) -> None:
        if not self.is_active():
            return
        selected = self._selected
        self.dismiss()
        if selected is not None and self._labels[selected]:
            self._on_select(selected)

    def _slot_at_global(self, point: QPoint) -> int | None:
        if self._origin is None:
            return None
        # 方向以唤出时的 Qt 全局逻辑坐标为锚点，窗口边缘钳位不会造成静止误选。
        return self._slot_at(point - self._origin + QPoint(270, 270))

    def _poll(self) -> None:
        if not self.is_active():
            self._timer.stop()
            return
        if _key_down(0x1B) or _key_down(0x02):
            self.dismiss()
            return
        self._selected = self._slot_at_global(QCursor.pos())
        if self._key_code is not None and not _key_down(self._key_code):
            self._commit()
            return
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not self.is_active():
            return
        self._selected = self._slot_at_global(event.globalPosition().toPoint())
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if not self.is_active():
            return
        if event.button() == Qt.MouseButton.RightButton:
            self.dismiss()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._selected = self._slot_at(event.position().toPoint())
            self._commit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.dismiss()

    def closeEvent(self, event) -> None:
        self.cancel()
        super().closeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setOpacity(self._opacity)
        self._hover_animation.select(
            self._selected if self._selected is not None and self._labels[self._selected] else None)
        paint_wheel(painter, self._labels, self._selected,
                    highlights=self._hover_animation.values, style=self._style)
        center = QPointF(270, 270)
        accent = QColor(GREEN_ACCENT)
        # 钳位只移动盘体：标出真实取消区，避免把视觉中心误认为手势起点。
        if self._origin is not None:
            anchor = QPointF(self.mapFromGlobal(self._origin))
            if (anchor - center).manhattanLength() > 2:
                painter.setPen(QPen(tint(TEXT_PRIMARY, 160), 1.5, Qt.PenStyle.DashLine))
                painter.drawLine(center, anchor)
                painter.setBrush(tint(WINDOW_BG, 190))
                painter.drawEllipse(anchor, 12, 12)
                painter.setPen(QPen(accent, 2))
                painter.drawEllipse(anchor, 3, 3)
        # 完整操作说明集中在设置页，轮盘底部不再重复显示提示。
        painter.end()
