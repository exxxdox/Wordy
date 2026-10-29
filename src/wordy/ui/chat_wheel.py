"""不抢游戏焦点的八向音频轮盘：按住、指向、松开播放。"""

from __future__ import annotations

import ctypes
import math
import sys
from collections.abc import Callable

from PySide6.QtCore import QElapsedTimer, QPoint, QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor, QCursor, QFont, QGuiApplication, QKeyEvent, QLinearGradient, QMouseEvent,
    QPaintEvent, QPainter, QPainterPath, QPen, QRadialGradient,
)
from PySide6.QtWidgets import QWidget

from wordy.ui import glass
from wordy.ui.theme import (
    ACCENT_SECONDARY, ELEVATED_BG, GREEN_ACCENT, TEXT_MUTED, TEXT_PRIMARY, WINDOW_BG, tint,
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
        accent = QColor(GREEN_ACCENT)
        secondary = QColor(ACCENT_SECONDARY)
        # 玻璃底盘半透明，但扇区本身要给足不透明度：没有背景模糊时，
        # 过透的材质会让槽位文字在游戏画面上读不清。
        disc = QPainterPath()
        disc.addEllipse(QRectF(24, 24, 492, 492))
        # 盘体先落投影：窗口四周 24px 余量正好容纳，越贴近盘边越实，
        # 并整体下移一点交代高度；游戏画面亮暗不定，影要比贴桌面的输入框更明显。
        painter.setPen(Qt.PenStyle.NoPen)
        for spread in range(18, -1, -3):
            painter.setBrush(QColor(0, 0, 0, 15))
            painter.drawEllipse(QRectF(24 - spread, 29 - spread, 492 + spread * 2, 492 + spread * 2))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint(WINDOW_BG, 165))
        painter.drawPath(disc)
        glass.paint_noise(painter, disc)
        # 圆形玻璃靠外缘向内的光渗表达厚度，比一圈均匀描边更像一整块玻璃。
        glass.paint_edge_glow(painter, disc, QRectF(24, 24, 492, 492), alpha=56)
        # 外圈是一圈有厚度的边：顶部受光、底部背光，轮盘才像一块实体圆盘。
        rim = QPainterPath()
        rim.addEllipse(QRectF(24, 24, 492, 492))
        rim.addEllipse(QRectF(38, 38, 464, 464))
        rim.setFillRule(Qt.FillRule.OddEvenFill)
        rim_light = QLinearGradient(QPointF(270, 24), QPointF(270, 516))
        rim_light.setColorAt(0.0, QColor(255, 255, 255, 64))
        rim_light.setColorAt(0.5, QColor(255, 255, 255, 20))
        rim_light.setColorAt(1.0, QColor(0, 0, 0, 150))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(rim_light)
        painter.drawPath(rim)

        # 整盘共享同一束左上主光：每个扇区画完底面再叠同一层光，圆盘才像一个平面。
        sheen = QRadialGradient(QPointF(170, 140), 380)
        sheen.setColorAt(0.0, QColor(255, 255, 255, 26))
        sheen.setColorAt(0.55, QColor(255, 255, 255, 6))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 0))

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
                # 双层光晕表达选中，不额外改变扇区几何。
                for width, alpha in ((14, 22), (8, 40)):
                    painter.setPen(QPen(tint(GREEN_ACCENT, alpha), width))
                    painter.setBrush(Qt.BrushStyle.NoBrush)
                    painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            base = QColor(GREEN_ACCENT) if selected else QColor(ELEVATED_BG)
            # 扇区不再是纯色片：主光在左上，中心提亮、外缘压暗，才有起鼓的曲面感。
            face = QRadialGradient(center + QPointF(-30, -40), 260)
            lit = base.lighter(122)
            lit.setAlpha(235 if selected else 225)
            shade = base.darker(135)
            shade.setAlpha(175 if selected else 200)
            face.setColorAt(0.0, lit)
            face.setColorAt(1.0, shade)
            painter.setBrush(face)
            painter.drawPath(path)
            # 主光压在扇区漆面上，但必须在文字之前，否则字会被一起照亮变糊。
            painter.save()
            painter.setClipPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(sheen)
            painter.drawPath(path)
            painter.restore()
            # 扇区分隔只用居中的 1px 细线：8 个扇区各自向内偏移会在交界处叠出
            # 深浅不一的缝，粗笔裁内缩在弧线上也会参差。
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent if selected else tint(TEXT_PRIMARY, 45), 1.0))
            painter.drawPath(path)
            radians = math.radians(90 - index * 45)
            text_center = center + QPointF(158 * math.cos(radians), -158 * math.sin(radians))
            painter.setFont(QFont("Segoe UI", 9))
            glass.paint_lifted_text(
                painter,
                QRectF(text_center.x() - 54, text_center.y() - 30, 108, 20),
                Qt.AlignmentFlag.AlignCenter,
                f"0{index + 1}",
                accent if selected else secondary,
            )
            painter.setFont(QFont("Microsoft YaHei UI", 10, QFont.Weight.DemiBold))
            text = painter.fontMetrics().elidedText(label or "未设置", Qt.TextElideMode.ElideRight, 116)
            glass.paint_lifted_text(
                painter,
                QRectF(text_center.x() - 62, text_center.y() - 6, 124, 34),
                Qt.AlignmentFlag.AlignCenter,
                text,
                QColor(TEXT_PRIMARY) if label else QColor(TEXT_MUTED),
            )

        # 外圈刻度：细白线为主，每八格用次级强调色标出方位。
        for tick in range(64):
            angle = math.radians(tick * 360 / 64)
            major = tick % 8 == 0
            painter.setPen(QPen(secondary if major else tint(TEXT_PRIMARY, 50), 1.5 if major else 1))
            painter.drawLine(center + QPointF(241 * math.cos(angle), 241 * math.sin(angle)),
                             center + QPointF((248 if major else 245) * math.cos(angle), (248 if major else 245) * math.sin(angle)))
        # 外缘上亮下暗：固定光源在主光位，圆盘才有厚度而不是一圈平描边。
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(tint(TEXT_PRIMARY, 70), 2))
        painter.drawArc(QRectF(26, 26, 488, 488), 45 * 16, 90 * 16)
        painter.setPen(QPen(QColor(0, 0, 0, 110), 2))
        painter.drawArc(QRectF(26, 26, 488, 488), 225 * 16, 90 * 16)
        # 中心盘是叠在扇区上的第二层玻璃：先压一圈投影，再用渐变起鼓。
        painter.setPen(Qt.PenStyle.NoPen)
        for step, alpha in ((7, 14), (4, 26), (1, 38)):
            painter.setBrush(QColor(0, 0, 0, alpha))
            painter.drawEllipse(QRectF(198 - step, 199 - step, 144 + step * 2, 144 + step * 2))
        hub = QRadialGradient(QPointF(262, 248), 96)
        hub_inner = QColor(WINDOW_BG).lighter(185)
        hub_inner.setAlpha(250)
        hub_outer = QColor(WINDOW_BG)
        hub_outer.setAlpha(238)
        hub.setColorAt(0.0, hub_inner)
        hub.setColorAt(1.0, hub_outer)
        # 中心盘比扇区更实一档，作为玻璃叠层的最上层。
        painter.setBrush(hub)
        painter.setPen(QPen(tint(GREEN_ACCENT, 90), 1))
        painter.drawEllipse(QRectF(198, 198, 144, 144))
        # 中心盘自身的倒角：左上亮、右下暗，才像一块嵌进去的凸起玻璃。
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(tint(TEXT_PRIMARY, 90), 2))
        painter.drawArc(QRectF(200, 200, 140, 140), 50 * 16, 80 * 16)
        painter.setPen(QPen(QColor(0, 0, 0, 120), 2))
        painter.drawArc(QRectF(200, 200, 140, 140), 230 * 16, 80 * 16)
        # 入场扫描弧只播一次，静止后不再有常驻动画干扰选择。
        painter.setPen(QPen(accent, 2))
        phase = min(self._clock.elapsed(), 300) / 40 if self._clock.isValid() else 0
        painter.drawArc(QRectF(205, 205, 130, 130), int(phase * 16), 52 * 16)
        painter.setPen(QPen(secondary, 2))
        painter.drawArc(QRectF(205, 205, 130, 130), int((phase + 180) * 16), 52 * 16)
        painter.setFont(QFont("Segoe UI", 10, QFont.Weight.DemiBold))
        glass.paint_lifted_text(
            painter, QRectF(207, 236, 126, 23), Qt.AlignmentFlag.AlignCenter, "WORDY", accent
        )
        painter.setFont(QFont("Microsoft YaHei UI", 9))
        has_selection = self._selected is not None and bool(self._labels[self._selected])
        glass.paint_lifted_text(
            painter,
            QRectF(207, 263, 126, 23),
            Qt.AlignmentFlag.AlignCenter,
            "松开播放" if has_selection else "移回取消",
            QColor(TEXT_MUTED),
        )
        # 完整操作说明集中在设置页，轮盘底部不再重复显示提示。
        painter.end()
