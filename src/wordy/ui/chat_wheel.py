"""不抢游戏焦点的八向音频轮盘：按住、指向、松开播放。"""

from __future__ import annotations

import ctypes
import math
import sys
from collections.abc import Callable

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QCursor, QFont, QGuiApplication, QKeyEvent, QMouseEvent, QPaintEvent, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

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
        self._selected: int | None = None
        self._key_code: int | None = None
        self._origin: QPoint | None = None
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._poll)
        self._clock = QElapsedTimer()

    def open_wheel(self, labels: list[str], key_code: int | None = None) -> None:
        if self.isVisible():
            return
        self._labels = [(label.strip() if label else "") for label in (labels + [""] * 8)[:8]]
        self._selected = None
        self._key_code = key_code
        position = QCursor.pos()
        self._origin = QPoint(position)
        screen = QGuiApplication.screenAt(position) or QGuiApplication.primaryScreen()
        x, y = clamp_window_position(screen, self.width(), self.height(),
                                     position.x() - 270, position.y() - 270)
        self.move(x, y)
        # 只定位窗口，不移动游戏光标；show 前的窗口映射在首开/跨屏时也可能尚未稳定。
        self._clock.start()
        self.show()
        self._timer.start()
        self.update()

    def cancel(self) -> None:
        self._timer.stop()
        self.hide()
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
        selected = self._selected
        self.cancel()
        if selected is not None and self._labels[selected]:
            self._on_select(selected)

    def _slot_at_global(self, point: QPoint) -> int | None:
        if self._origin is None:
            return None
        # 方向以唤出时的 Qt 全局逻辑坐标为锚点，窗口边缘钳位不会造成静止误选。
        return self._slot_at(point - self._origin + QPoint(270, 270))

    def _poll(self) -> None:
        if not self.isVisible():
            self._timer.stop()
            return
        if _key_down(0x1B) or _key_down(0x02):
            self.cancel()
            return
        self._selected = self._slot_at_global(QCursor.pos())
        if self._key_code is not None and not _key_down(self._key_code):
            self._commit()
            return
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        self._selected = self._slot_at_global(event.globalPosition().toPoint())
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            self.cancel()
        elif event.button() == Qt.MouseButton.LeftButton:
            self._selected = self._slot_at(event.position().toPoint())
            self._commit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.cancel()

    def closeEvent(self, event) -> None:
        self.cancel()
        super().closeEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(270, 270)
        outer = QRectF(38, 38, 464, 464)
        inner = QRectF(190, 190, 160, 160)
        cyan = QColor("#55d9f2")
        violet = QColor("#8d9fff")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(4, 12, 24, 150))
        painter.drawEllipse(QRectF(24, 24, 492, 492))

        for index, label in enumerate(self._labels):
            selected = index == self._selected and bool(label)
            angle = 112.5 - index * 45
            path = QPainterPath()
            path.arcMoveTo(outer, angle - 1)
            path.arcTo(outer, angle - 1, -43)
            end = math.radians(angle - 44)
            path.lineTo(center + QPointF(80 * math.cos(end), -80 * math.sin(end)))
            path.arcTo(inner, angle - 44, 43)
            path.closeSubpath()
            if selected:
                for width, alpha in ((14, 18), (8, 32)):
                    painter.setPen(QPen(QColor(85, 217, 242, alpha), width))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(path)
            painter.setPen(QPen(cyan if selected else QColor(65, 112, 153, 170), 1.5 if selected else 0.8))
            painter.setBrush(QColor(26, 88, 116, 235) if selected else QColor(9, 27, 46, 228))
            painter.drawPath(path)
            radians = math.radians(90 - index * 45)
            text_center = center + QPointF(158 * math.cos(radians), -158 * math.sin(radians))
            painter.setFont(QFont("Segoe UI", 9))
            painter.setPen(cyan if selected else violet)
            painter.drawText(QRectF(text_center.x() - 54, text_center.y() - 30, 108, 20), Qt.AlignmentFlag.AlignCenter, f"0{index + 1}")
            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
            painter.setPen(QColor("#f0fbff") if label else QColor("#6b839b"))
            text = painter.fontMetrics().elidedText(label or "未设置", Qt.TextElideMode.ElideRight, 116)
            painter.drawText(QRectF(text_center.x() - 62, text_center.y() - 6, 124, 34), Qt.AlignmentFlag.AlignCenter, text)

        # 外圈刻度与短暂入场扫描保持科技感，静止后避免持续动画干扰选择。
        for tick in range(64):
            angle = math.radians(tick * 360 / 64)
            major = tick % 8 == 0
            painter.setPen(QPen(violet if major else QColor(63, 107, 141, 155), 1.5 if major else 1))
            painter.drawLine(center + QPointF(241 * math.cos(angle), 241 * math.sin(angle)),
                             center + QPointF((248 if major else 245) * math.cos(angle), (248 if major else 245) * math.sin(angle)))
        painter.setBrush(QColor(7, 19, 33, 246))
        painter.setPen(QPen(QColor(85, 217, 242, 125), 1))
        painter.drawEllipse(QRectF(198, 198, 144, 144))
        painter.setPen(QPen(cyan, 2))
        phase = min(self._clock.elapsed(), 300) / 40 if self._clock.isValid() else 0
        painter.drawArc(QRectF(205, 205, 130, 130), int(phase * 16), 52 * 16)
        painter.setPen(QPen(violet, 2))
        painter.drawArc(QRectF(205, 205, 130, 130), int((phase + 180) * 16), 52 * 16)
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        painter.setPen(cyan)
        painter.drawText(QRectF(207, 236, 126, 23), Qt.AlignmentFlag.AlignCenter, "WORDY")
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        painter.setPen(QColor("#9bb3cb"))
        has_selection = self._selected is not None and bool(self._labels[self._selected])
        painter.drawText(QRectF(207, 263, 126, 23), Qt.AlignmentFlag.AlignCenter, "松开播放" if has_selection else "移回取消")
        # 完整操作说明集中在设置页，轮盘底部不再重复显示提示。
        painter.end()
