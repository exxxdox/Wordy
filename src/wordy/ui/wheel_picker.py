"""可点击的轮盘预览：设置页与后台录制目标选择共用同一套盘体。

只负责显示与命中；槽位内容、选中状态由持有者通过按钮文本与 ``set_selected``
维护，保证两处轮盘视觉不分叉。
"""

from __future__ import annotations

import math
from collections.abc import Callable

from PySide6.QtCore import QEvent, QObject, QPoint, Qt
from PySide6.QtGui import QMouseEvent, QPaintEvent, QPainter
from PySide6.QtWidgets import QPushButton, QWidget

from wordy.ui.wheel_paint import (
    WHEEL_CANVAS_HEIGHT, WHEEL_CANVAS_WIDTH, WheelHover, paint_wheel,
)


class WheelPicker(QWidget):
    """用真实方位呈现八个可键盘聚焦的轮盘槽。"""

    def __init__(self, on_select: Callable[[int], None], size: int = 400) -> None:
        super().__init__()
        self._on_select = on_select
        self._hovered_index: int | None = None
        self._hover_animation = WheelHover(self)
        self.wheel_style = "classic"
        # 预览按画布等比缩进：画布比盘体宽，按画布宽缩放让左右余量也完整显示。
        self._size = size
        self._scale = size / WHEEL_CANVAS_WIDTH
        self._top = (size - WHEEL_CANVAS_HEIGHT * self._scale) / 2
        self.setFixedSize(size, size)
        self.setMouseTracking(True)
        self.buttons: list[QPushButton] = []
        for index in range(8):
            button = QPushButton(self)
            button.setObjectName("wheelSlot")
            button.setAccessibleName(f"轮盘槽 {index + 1}")
            button.setCheckable(True)
            # 鼠标统一由轮盘处理，避免透明按钮的 hover/pressed 状态覆盖扇区选中色。
            button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            button.setFixedSize(88, 44)
            # 焦点按钮跟随放大后的槽位中心，确保键盘导航滚动到正确位置。
            angle = math.radians(90 - index * 45)
            radius = 158 * self._scale
            button.move(round(size / 2 + radius * math.cos(angle) - 44),
                        round(size / 2 - radius * math.sin(angle) - 22))
            button.clicked.connect(lambda _checked=False, i=index: on_select(i))
            button.installEventFilter(self)
            self.buttons.append(button)
        self.setStyleSheet("""
            QPushButton#wheelSlot {
                background: transparent;
                color: transparent;
                border: none;
            }
            QPushButton#wheelSlot:disabled {
                background: transparent;
                color: transparent;
            }
        """)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in self.buttons and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.update()
        return super().eventFilter(watched, event)

    def _slot_at(self, point: QPoint) -> int | None:
        dx, dy = point.x() - self._size / 2, point.y() - self._size / 2
        distance = math.hypot(dx, dy)
        # 命中范围跟随共享盘体的 80/232 半径及缩放比例。
        if distance < 80 * self._scale or distance > 232 * self._scale:
            return None
        angle = math.degrees(math.atan2(dx, -dy)) % 360
        return int((angle + 22.5) % 360 // 45)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._slot_at(event.position().toPoint())
            if index is not None and self.buttons[index].isEnabled():
                self._on_select(index)
                self.update()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        hovered_index = self._slot_at(event.position().toPoint())
        if hovered_index != self._hovered_index:
            self._hovered_index = hovered_index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered_index = None
        self.update()
        super().leaveEvent(event)

    def set_selected(self, index: int | None) -> None:
        for position, button in enumerate(self.buttons):
            button.setChecked(position == index)
        self.update()

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.translate(0, self._top)
        painter.scale(self._scale, self._scale)
        labels = [button.text().split("\n", 1)[-1] for button in self.buttons]
        labels = ["" if label == "未设置" else label for label in labels]
        focused = next((i for i, button in enumerate(self.buttons) if button.hasFocus()), None)
        checked = next((i for i, button in enumerate(self.buttons) if button.isChecked()), None)
        selected = focused if focused is not None else self._hovered_index
        selected = selected if selected is not None else checked
        self._hover_animation.select(selected)
        paint_wheel(painter, labels, selected, editing=True, highlights=self._hover_animation.values,
                    style=self.wheel_style)
        painter.end()
