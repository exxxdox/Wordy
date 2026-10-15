#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口内的浮动反馈队列。

反馈文字不再占用固定的布局位置：每条反馈显示后自行淡出，鼠标悬停时
保持，移开后继续淡出。多条同时出现时上下并列（新的在下、旧的在上），
各自计时逐个消失，下面的卡片随之上移或落回。队列挂在设置窗口上
（而不是页签页），因此不会被页签透明度动画一起改写。

外观只用一层图形效果（淡化用的不透明度效果）：投影效果嵌在里面时，
卡片在可见状态下改变尺寸会让投影缓存的源图失效，Qt 会整块不绘制，
所以「浮起」的层次改由 paintEvent 手绘的分层圆角矩形和底色渐变实现。
"""

from __future__ import annotations

from PySide6.QtCore import (
    QEasingCurve, QEvent, QObject, QPoint, QPropertyAnimation, QRect, Qt, QTimer,
    Signal,
)
from PySide6.QtGui import QColor, QCursor, QEnterEvent, QPaintEvent, QPainter
from PySide6.QtWidgets import (
    QFrame, QGraphicsOpacityEffect, QHBoxLayout, QLabel, QVBoxLayout, QWidget,
)

from wordy.ui.theme import ELEVATED_BG, SEPARATOR_COLOR, TEXT_PRIMARY

# 停留时长按“读完一条短反馈”取值；淡出比淡入慢，收起时才不会显得突兀。
HOLD_MS = 2600
# 鼠标移开后的重新计时：给用户一个回看缓冲，再继续淡出。
HOVER_HOLD_MS = 1400
FADE_IN_MS = 160
FADE_OUT_MS = 420
# 队列重排（新卡片插入、旧卡片消失）时的位移时长。
SLIDE_MS = 150

EDGE_MARGIN = 16
FOOTER_GAP = 10
CARD_GAP = 8
MIN_WIDTH = 140
# 同时最多并列几条；再多会盖住设置内容，最旧的先让位。
MAX_VISIBLE = 3
TEXT_PX = 12
RADIUS_PX = 10
# 卡片外留白，用来手绘投影；分层叠加出由内向外衰减的暗边。
SHADOW_PAD = 12
SHADOW_STEPS = 5
SHADOW_ALPHA = 18
SHADOW_OFFSET = 3
# 卡片内边距、色条与 1px 边框：宽度按这些常量显式换算，不依赖布局 sizeHint 缓存。
CARD_PAD_LEFT = 10
CARD_PAD_RIGHT = 16
CARD_PAD_V = 8
BAR_WIDTH = 3
BAR_GAP = 10
BORDER_PX = 1
LABEL_CHROME_H = CARD_PAD_LEFT + BAR_WIDTH + BAR_GAP + CARD_PAD_RIGHT + 2 * BORDER_PX
LABEL_CHROME_V = 2 * CARD_PAD_V + 2 * BORDER_PX
# 底色上亮下暗，再把状态色混进去：每条反馈的卡片都带自己颜色的微光。
CARD_TOP_LIGHTEN = 118
CARD_BOTTOM_DARKEN = 108
CARD_TINT_TOP = 0.16
CARD_TINT_BOTTOM = 0.07
BORDER_TINT = 0.5

FOOTER_OBJECT_NAME = "settingsFooter"


def _mix(base: str, accent: str, ratio: float) -> str:
    """把强调色按比例混进底色。"""
    base_color, accent_color = QColor(base), QColor(accent)
    return QColor(
        round(base_color.red() + (accent_color.red() - base_color.red()) * ratio),
        round(base_color.green() + (accent_color.green() - base_color.green()) * ratio),
        round(base_color.blue() + (accent_color.blue() - base_color.blue()) * ratio),
    ).name()


class StatusToast(QWidget):
    """单条反馈卡片。停放位置由 :class:`StatusToastQueue` 统一安排。"""

    # 完全淡出或被收起后发出，队列据此移除并销毁卡片。
    finished = Signal()

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("statusToast")
        self._color = TEXT_PRIMARY
        self._fading_out = False
        self._hovered = False
        self._done = False
        # 计时参数暴露为实例属性，便于测试与后续按需调整节奏。
        self.hold_ms = HOLD_MS
        self.hover_hold_ms = HOVER_HOLD_MS

        outer = QVBoxLayout(self)
        pad = SHADOW_PAD
        outer.setContentsMargins(pad, pad, pad, pad)
        outer.setSpacing(0)

        self._card = QFrame(self)
        self._card.setObjectName("statusToastCard")
        outer.addWidget(self._card)

        card_layout = QHBoxLayout(self._card)
        card_layout.setContentsMargins(CARD_PAD_LEFT, CARD_PAD_V, CARD_PAD_RIGHT, CARD_PAD_V)
        card_layout.setSpacing(BAR_GAP)
        self._bar = QFrame(self._card)
        self._bar.setFixedWidth(BAR_WIDTH)
        card_layout.addWidget(self._bar)
        self._label = QLabel(self._card)
        self._label.setObjectName("statusToastText")
        self._label.setWordWrap(True)
        self._label.setAccessibleName("设置反馈")
        card_layout.addWidget(self._label)

        # 卡片和文字都不拦截鼠标，悬停状态由卡片整体统一判定。
        for passive in (self._card, self._bar, self._label):
            passive.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)

        self._opacity = QGraphicsOpacityEffect(self)
        self._opacity.setOpacity(0.0)
        self.setGraphicsEffect(self._opacity)
        self._fade = QPropertyAnimation(self._opacity, b"opacity", self)
        self._fade.finished.connect(self._on_fade_finished)
        self._slide = QPropertyAnimation(self, b"pos", self)
        self._hold = QTimer(self)
        self._hold.setSingleShot(True)
        self._hold.timeout.connect(self._start_fade_out)
        self.hide()

    # ── 对外接口 ─────────────────────────────────────────────────────

    def show_status(self, text: str, color: str, max_width: int) -> None:
        """载入文本并显示。"""
        self._label.setText(text)
        self._apply_color(color)
        self.fit_width(max_width)
        self.show()
        self.raise_()
        self._fading_out = False
        self._animate(self._opacity.opacity(), 1.0, FADE_IN_MS)
        self._hold.start(self.hold_ms)

    def refresh(self, color: str) -> None:
        """同一文本再次出现：只重置颜色与计时，不重新淡入。"""
        self._apply_color(color)
        self._fading_out = False
        self._animate(self._opacity.opacity(), 1.0, FADE_IN_MS)
        self._hold.start(self.hold_ms)

    def fit_width(self, max_width: int) -> None:
        """按文本与可用宽度定尺寸，不用 adjustSize()。

        控件已可见时布局的 ``sizeHint`` 缓存不随新文本更新，第二条更长的
        反馈会沿用上一条的尺寸导致文字被裁；这里按字体度量显式算出尺寸：
        短文本保持单行紧凑，长文本才在可用宽度处折行。
        """
        natural = self._label.fontMetrics().horizontalAdvance(self._label.text())
        label_width = min(natural, max(MIN_WIDTH, max_width))
        label_height = self._label.heightForWidth(label_width)
        self._label.setFixedSize(label_width, label_height)
        self._card.setFixedSize(label_width + LABEL_CHROME_H, label_height + LABEL_CHROME_V)
        self.resize(self._card.width() + 2 * SHADOW_PAD, self._card.height() + 2 * SHADOW_PAD)

    def glide_to(self, x: int, y: int) -> None:
        """移到目标位置；首次落位直接到位，之后的重排才滑动。"""
        target = QPoint(x, y)
        if self.pos().isNull() or self.pos() == target:
            self.move(target)
            return
        self._slide.stop()
        self._slide.setDuration(SLIDE_MS)
        self._slide.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._slide.setStartValue(self.pos())
        self._slide.setEndValue(target)
        self._slide.start()

    def card_geometry(self) -> QRect:
        """可见卡片在父级坐标系里的位置（不含投影留白）。"""
        return self._card.geometry().translated(self.pos())

    def dismiss(self) -> None:
        """立即收起并通知队列，不做淡出动画。"""
        self._hold.stop()
        self._fade.stop()
        self._slide.stop()
        self._fading_out = False
        self._opacity.setOpacity(0.0)
        self._label.clear()
        self.hide()
        self._finish()

    def text(self) -> str:
        """当前反馈文本。"""
        return self._label.text()

    def color(self) -> str:
        """当前反馈使用的强调色。"""
        return self._color

    # ── 内部实现 ─────────────────────────────────────────────────────

    def _apply_color(self, color: str) -> None:
        self._color = color
        top = _mix(QColor(ELEVATED_BG).lighter(CARD_TOP_LIGHTEN).name(), color, CARD_TINT_TOP)
        bottom = _mix(QColor(ELEVATED_BG).darker(CARD_BOTTOM_DARKEN).name(), color, CARD_TINT_BOTTOM)
        self._card.setStyleSheet(
            f"QFrame#statusToastCard {{"
            f" background: qlineargradient(x1:0, y1:0, x2:0, y2:1,"
            f" stop:0 {top}, stop:1 {bottom});"
            f" border: 1px solid {_mix(SEPARATOR_COLOR, color, BORDER_TINT)};"
            f" border-radius: {RADIUS_PX}px;"
            f"}}"
        )
        # 字号写在 QSS 里：窗口样式表的 QWidget 规则会覆盖 setFont 设的字体。
        self._label.setStyleSheet(
            f"color: {color}; font-size: {TEXT_PX}px; font-weight: 600;"
        )
        self._bar.setStyleSheet(f"background: {color}; border-radius: 1px;")

    def _animate(self, start: float, end: float, duration: int) -> None:
        self._fade.stop()
        self._fade.setDuration(duration)
        self._fade.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._fade.setStartValue(start)
        self._fade.setEndValue(end)
        self._fade.start()

    def _on_fade_finished(self) -> None:
        if self._fading_out:
            self._fading_out = False
            self.hide()
            self._finish()

    def _finish(self) -> None:
        # 队列收到 finished 会销毁卡片，重复触发会落到已销毁对象上。
        if self._done:
            return
        self._done = True
        self.finished.emit()

    def _start_fade_out(self) -> None:
        if not self.isVisible():
            return
        self._fading_out = True
        self._animate(self._opacity.opacity(), 0.0, FADE_OUT_MS)

    def enterEvent(self, event: QEnterEvent) -> None:
        # 悬停期间保持显示：停掉计时器，正在淡出也拉回全不透明。
        if self.isVisible() and self._cursor_inside():
            self._hovered = True
            self._hold.stop()
            self._fading_out = False
            self._animate(self._opacity.opacity(), 1.0, FADE_IN_MS)
        super().enterEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        # 只有真的悬停过才重新计时：显示新卡片时 Qt 会给同层卡片补发一次
        # 没有配对的 Leave，照单全收会把它本来的停留时间压成悬停时长。
        if self._hovered:
            self._hovered = False
            if self.isVisible():
                self._hold.start(self.hover_hold_ms)
        super().leaveEvent(event)

    def _cursor_inside(self) -> bool:
        """真实光标是否落在卡片内。

        部分平台（含离屏）在新卡片显示时会补发一次 Enter，但光标并不在
        卡片上；用它当悬停会白白把停留时间压成悬停时长。
        """
        return self.rect().contains(self.mapFromGlobal(QCursor.pos()))

    def paintEvent(self, _event: QPaintEvent) -> None:
        """在卡片外圈画分层圆角矩形当投影。

        Qt 每个控件只能挂一个图形效果，投影必须手绘，不能再用
        ``QGraphicsDropShadowEffect`` 嵌在淡出效果里。
        """
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(0, 0, 0, SHADOW_ALPHA))
        for step in range(SHADOW_STEPS, 0, -1):
            painter.drawRoundedRect(
                self.rect().adjusted(step, step + SHADOW_OFFSET, -step, -step + SHADOW_OFFSET),
                RADIUS_PX + step,
                RADIUS_PX + step,
            )
        painter.end()


class StatusToastQueue(QObject):
    """设置窗口底部的反馈队列：多条并列，各自计时淡出。"""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self._parent = parent
        self._cards: list[StatusToast] = []
        # 新建卡片继承队列的节奏；测试与后续调优都改这里。
        self.hold_ms = HOLD_MS
        self.hover_hold_ms = HOVER_HOLD_MS
        # 设置窗口可缩放，显示期间跟随父级尺寸重新排版。
        parent.installEventFilter(self)

    # ── 对外接口 ─────────────────────────────────────────────────────

    def push(self, text: str, color: str) -> None:
        """加入一条反馈：新的在下、旧的在上，各自计时逐个消失。"""
        newest = self._cards[-1] if self._cards else None
        if newest is not None and newest.text() == text:
            # 同一条反馈连续出现（例如反复点刷新）只重置计时，不叠重复卡片。
            newest.refresh(color)
            self._relayout()
            return
        while len(self._cards) >= MAX_VISIBLE:
            # 超出上限先收起最旧的一条，避免反馈盖住设置内容。
            self._cards[0].dismiss()
        card = StatusToast(self._parent)
        card.hold_ms = self.hold_ms
        card.hover_hold_ms = self.hover_hold_ms
        card.finished.connect(lambda retired=card: self._retire(retired))
        self._cards.append(card)
        card.show_status(text, color, self._max_width())
        self._relayout()
        card.raise_()

    def clear(self) -> None:
        """立即收起全部反馈。"""
        for card in list(self._cards):
            card.dismiss()

    def cards(self) -> list[StatusToast]:
        """当前并列的卡片，由旧到新。"""
        return list(self._cards)

    def texts(self) -> list[str]:
        """当前并列的反馈文本，由旧到新。"""
        return [card.text() for card in self._cards]

    # ── 内部实现 ─────────────────────────────────────────────────────

    def _retire(self, card: StatusToast) -> None:
        if card not in self._cards:
            return
        self._cards.remove(card)
        card.deleteLater()
        self._relayout()

    def _relayout(self) -> None:
        """按卡片（不含投影留白）从下往上排，新的贴近页脚。"""
        width = self._parent.width()
        max_width = self._max_width()
        bottom = self._anchor_bottom()
        for card in reversed(self._cards):
            card.fit_width(max_width)
            card_height = card.height() - 2 * SHADOW_PAD
            card_top = bottom - card_height
            x = max(EDGE_MARGIN, (width - card.width()) // 2)
            card.glide_to(x, card_top - SHADOW_PAD)
            bottom = card_top - CARD_GAP

    def _max_width(self) -> int:
        return max(
            MIN_WIDTH,
            self._parent.width() - 2 * EDGE_MARGIN - 2 * SHADOW_PAD - LABEL_CHROME_H,
        )

    def _anchor_bottom(self) -> int:
        """以页脚上沿作为停放基线；没有页脚时退回窗口下沿。"""
        footer = self._parent.findChild(QWidget, FOOTER_OBJECT_NAME)
        if footer is None:
            return self._parent.height() - EDGE_MARGIN
        return footer.mapTo(self._parent, QPoint(0, 0)).y() - FOOTER_GAP

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Resize and self._cards:
            self._relayout()
        return super().eventFilter(watched, event)


def show_status_toast(source: QWidget, text: str, color: str) -> bool:
    """在 ``source`` 所属的设置窗口内显示浮动反馈。

    面板被单独实例化（没有设置窗口）时返回 False，调用方据此回退到
    原地的状态文本。
    """
    window = source.window()
    queue = window.findChild(StatusToastQueue) if window is not None else None
    if queue is None:
        return False
    queue.push(text, color)
    return True
