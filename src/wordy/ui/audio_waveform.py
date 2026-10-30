"""可拖动双端点的音频区间；仅绘制已提取的峰值，不读取音频文件。"""

from __future__ import annotations

import math
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QKeyEvent, QMouseEvent, QPaintEvent, QPainter, QPen
from PySide6.QtWidgets import QWidget
from wordy.ui.theme import ELEVATED_BG, GREEN_ACCENT, SEPARATOR_COLOR, TEXT_ERROR, TEXT_MUTED, TEXT_PRIMARY, tint


class WaveformSelection(QWidget):
    selectionChanged = Signal(float, float)

    def __init__(self, duration: float, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("音频时长必须大于零")
        self.duration = duration
        self.start, self.end = 0.0, duration
        self._peaks: list[float] = []
        self._playhead: float | None = None
        self._drag: str | None = None
        self._anchor = 0.0
        self._active = "start"
        self.setMinimumSize(400, 200)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setMouseTracking(True)
        self.setAccessibleName("音频截取区间")
        self.setAccessibleDescription("拖动左右手柄或在波形上拖选；方向键调整起点，Shift 加方向键调整终点。")

    def plot_rect(self) -> QRectF:
        return QRectF(18, 18, max(1, self.width() - 36), max(1, self.height() - 52))

    def set_peaks(self, peaks: list[float]) -> None:
        # 峰值数量有上限，长音频不会增加每一帧的绘制工作量。
        self._peaks = [max(0.0, min(1.0, float(value))) if math.isfinite(value) else 0.0
                       for value in peaks[:1600]]
        # 仅按整段峰值归一化显示，保留相对强弱；绘制仍留12px余量，不改变源音频。
        maximum = max(self._peaks, default=0.0)
        if maximum > 0:
            self._peaks = [peak / maximum for peak in self._peaks]
        self.update()

    def set_playhead(self, seconds: float | None) -> None:
        self._playhead = (max(0.0, min(self.duration, seconds))
                          if seconds is not None and math.isfinite(seconds) else None)
        self.update()

    def set_selection(self, start: float, end: float) -> None:
        if not math.isfinite(start) or not math.isfinite(end):
            return
        self.start = max(0.0, min(self.duration, start))
        self.end = max(self.start, min(self.duration, end))
        self.update()

    def _time_at(self, x: float) -> float:
        rect = self.plot_rect()
        return max(0.0, min(self.duration, (x - rect.left()) / rect.width() * self.duration))

    def _x_at(self, time: float) -> float:
        rect = self.plot_rect()
        return rect.left() + time / self.duration * rect.width()

    def _change(self, start: float, end: float) -> None:
        before = self.start, self.end
        self.set_selection(start, end)
        if before != (self.start, self.end):
            self.selectionChanged.emit(self.start, self.end)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton or not self.plot_rect().contains(event.position()):
            return super().mousePressEvent(event)
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        time = self._time_at(event.position().x())
        distances = abs(event.position().x() - self._x_at(self.start)), abs(event.position().x() - self._x_at(self.end))
        if min(distances) <= 12:
            self._drag = "start" if distances[0] <= distances[1] else "end"
            self._active = self._drag
        else:
            self._drag = "range"
            self._anchor = time
        event.accept()

    def _drag_to(self, x: float) -> None:
        time = self._time_at(x)
        gap = min(.001, self.duration)
        if self._drag == "start":
            self._change(min(time, max(0, self.end - gap)), self.end)
        elif self._drag == "end":
            self._change(self.start, max(time, min(self.duration, self.start + gap)))
        elif self._drag == "range":
            start, end = sorted((time, self._anchor))
            if end - start >= gap:
                self._change(start, end)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._drag is not None:
            self._drag_to(event.position().x())
        else:
            near = min(abs(event.position().x() - self._x_at(self.start)),
                       abs(event.position().x() - self._x_at(self.end))) <= 12
            self.setCursor(Qt.CursorShape.SizeHorCursor if near else Qt.CursorShape.CrossCursor)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton and self._drag is not None:
            self._drag_to(event.position().x())
            self._drag = None
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() not in (Qt.Key.Key_Left, Qt.Key.Key_Right):
            return super().keyPressEvent(event)
        self._drag = "end" if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else self._active
        delta = min(.01, self.duration / 100) * (-1 if event.key() == Qt.Key.Key_Left else 1)
        value = self.start if self._drag == "start" else self.end
        self._drag_to(self._x_at(value + delta))
        self._drag = None
        event.accept()

    def paintEvent(self, event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.plot_rect()
        painter.setPen(QPen(QColor(SEPARATOR_COLOR), 1))
        painter.setBrush(QColor(ELEVATED_BG))
        painter.drawRoundedRect(rect, 8, 8)
        left, right = self._x_at(self.start), self._x_at(self.end)
        painter.fillRect(QRectF(left, rect.top(), right - left, rect.height()), tint(GREEN_ACCENT, 35))
        painter.setPen(QPen(QColor(SEPARATOR_COLOR), 1))
        painter.drawLine(QPointF(rect.left(), rect.center().y()), QPointF(rect.right(), rect.center().y()))
        # 以峰值柱保留短促瞬态，不用平滑曲线制造不存在的音频细节。
        count = len(self._peaks)
        for index, peak in enumerate(self._peaks):
            x = rect.left() + (index + .5) / count * rect.width()
            amplitude = peak * (rect.height() / 2 - 12)
            painter.setPen(QPen(QColor(TEXT_PRIMARY) if left <= x <= right else QColor(TEXT_MUTED), 1))
            painter.drawLine(QPointF(x, rect.center().y() - amplitude), QPointF(x, rect.center().y() + amplitude))
        painter.fillRect(QRectF(rect.left(), rect.top(), left - rect.left(), rect.height()), QColor(0, 0, 0, 80))
        painter.fillRect(QRectF(right, rect.top(), rect.right() - right, rect.height()), QColor(0, 0, 0, 80))
        for time in range(6):
            value = self.duration * time / 5
            x = self._x_at(value)
            painter.setPen(QPen(QColor(SEPARATOR_COLOR), 1))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setPen(QColor(TEXT_MUTED))
            painter.drawText(QRectF(max(0, min(self.width() - 70, x - 35)), rect.bottom() + 7, 70, 20), Qt.AlignmentFlag.AlignCenter, f"{value:.2f}s")
        for x in (left, right):
            painter.setPen(QPen(QColor(GREEN_ACCENT), 2))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.setBrush(QColor(GREEN_ACCENT))
            painter.drawRoundedRect(QRectF(x - 5, rect.center().y() - 14, 10, 28), 4, 4)
        if self._playhead is not None:
            # 播放线位于选区和手柄之上，随实际输出块进度移动。
            x = self._x_at(self._playhead)
            painter.setPen(QPen(QColor(TEXT_ERROR), 2))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(tint(GREEN_ACCENT, 150), 1))
            painter.drawRoundedRect(rect.adjusted(-4, -4, 4, 4), 10, 10)
        painter.end()
