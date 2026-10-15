#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口浮动反馈队列：自动淡出、悬停保持、多条并列逐个消失。"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

_ = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ = pytest.importorskip("PySide6")

from PySide6.QtCore import QEvent, QPointF
from PySide6.QtGui import QCursor, QEnterEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFrame, QLabel, QWidget

from wordy.ui.status_toast import (
    EDGE_MARGIN, MAX_VISIBLE, SLIDE_MS, StatusToast, StatusToastQueue,
    show_status_toast,
)
from wordy.ui.theme import GREEN_ACCENT, TEXT_ERROR

pytestmark = pytest.mark.usefixtures("qapp")

# 用远小于生产值的停留时间驱动真实计时器，避免测试等待数秒。
# 长停留要远大于 WAIT_MS：qWait 只保证“至少等这么久”，余量小会变成偶发失败。
HOLD_MS = 40
LONG_HOLD_MS = 3000
WAIT_MS = 700


@pytest.fixture
def host() -> Iterator[QWidget]:
    """子控件只有在父级可见时才算可见，测试宿主必须先 show。"""
    widget = QWidget()
    widget.resize(420, 300)
    widget.show()
    QTest.qWait(10)
    yield widget
    widget.close()


@pytest.fixture
def queue(host: QWidget) -> StatusToastQueue:
    queue = StatusToastQueue(host)
    queue.hold_ms = HOLD_MS
    queue.hover_hold_ms = HOLD_MS
    return queue


def _enter(card: StatusToast) -> None:
    # 卡片会核对真实光标位置，先把光标放到卡片上再发 Enter。
    QCursor.setPos(card.mapToGlobal(card.rect().center()))
    point = QPointF(5, 5)
    QApplication.sendEvent(card, QEnterEvent(point, point, point))


def _leave(card: StatusToast) -> None:
    QApplication.sendEvent(card, QEvent(QEvent.Type.Leave))


# ── 单条反馈 ─────────────────────────────────────────────────────────


def test_push_shows_card_with_text(queue: StatusToastQueue) -> None:
    queue.push("设置已应用", GREEN_ACCENT)

    cards = queue.cards()
    assert len(cards) == 1
    assert cards[0].text() == "设置已应用"
    assert cards[0].isVisible() is True


def test_error_status_keeps_error_color(queue: StatusToastQueue) -> None:
    queue.push("保存失败：磁盘已满", TEXT_ERROR)

    assert queue.cards()[0].color() == TEXT_ERROR


def test_card_fades_out_after_hold(queue: StatusToastQueue) -> None:
    queue.push("已保存", GREEN_ACCENT)

    QTest.qWait(WAIT_MS)

    assert queue.texts() == []


def test_hover_holds_card_then_leave_resumes_fade(queue: StatusToastQueue) -> None:
    queue.push("已保存", GREEN_ACCENT)
    card = queue.cards()[0]

    _enter(card)
    QTest.qWait(WAIT_MS)
    assert card.isVisible() is True, "悬停期间不能淡出"

    _leave(card)
    QTest.qWait(WAIT_MS)
    assert queue.texts() == []


def test_repeated_same_text_resets_hold_instead_of_stacking(queue: StatusToastQueue) -> None:
    queue.hold_ms = WAIT_MS
    queue.push("正在加载音色列表...", GREEN_ACCENT)
    QTest.qWait(HOLD_MS * 4)
    queue.push("正在加载音色列表...", GREEN_ACCENT)

    assert queue.texts() == ["正在加载音色列表..."]


def test_long_status_wraps_and_stays_inside_host(queue: StatusToastQueue, host: QWidget) -> None:
    queue.push("很长的反馈文本" * 8, GREEN_ACCENT)

    card = queue.cards()[0].card_geometry()
    assert card.height() > 30, "超宽文本必须折行"
    assert card.width() <= host.width() - 2 * EDGE_MARGIN
    assert card.right() <= host.width()
    assert card.x() >= EDGE_MARGIN


def test_card_reserves_room_for_the_painted_shadow(queue: StatusToastQueue) -> None:
    """投影是手绘的，胶囊必须比卡片大一圈留出绘制空间。

    Qt 每个控件只能挂一个图形效果，淡出占用了它，投影不能再用
    QGraphicsDropShadowEffect 嵌一层（嵌套会让可见卡片改尺寸后整块不绘制）。
    """
    queue.push("已保存", GREEN_ACCENT)

    toast = queue.cards()[0]
    assert toast.width() > toast.card_geometry().width()
    assert toast.height() > toast.card_geometry().height()


def test_card_colors_follow_the_status_color(queue: StatusToastQueue) -> None:
    """底色与描边由状态色混出，不同状态不能长成同一张卡片。"""
    queue.push("加载失败：网络不可达", TEXT_ERROR)
    error_card = _card_frame(queue.cards()[0])
    error_label = queue.cards()[0].findChild(QLabel, "statusToastText")

    queue.clear()
    queue.push("已保存", GREEN_ACCENT)
    ok_card = _card_frame(queue.cards()[0])

    assert error_label is not None and TEXT_ERROR in error_label.styleSheet()
    assert error_card.styleSheet() != ok_card.styleSheet(), "底色与描边必须随状态色变化"


def _card_frame(card: StatusToast) -> QFrame:
    frame = card.findChild(QFrame, "statusToastCard")
    assert frame is not None, "卡片必须存在 statusToastCard 内框"
    return frame


# ── 多条并列 ─────────────────────────────────────────────────────────


def test_two_cards_stack_vertically_without_overlap(queue: StatusToastQueue) -> None:
    """卡片之间不能重叠；对比的是可见卡片，不含投影留白。"""
    queue.push("正在加载音色列表...", GREEN_ACCENT)
    queue.push("已加载 12 个音色", GREEN_ACCENT)
    QTest.qWait(SLIDE_MS + 40)

    older, newer = queue.cards()
    assert older.card_geometry().bottom() < newer.card_geometry().y()


def test_each_card_disappears_on_its_own(queue: StatusToastQueue) -> None:
    """各自计时：停留短的那条先走，另一条继续显示。"""
    queue.hold_ms = HOLD_MS
    queue.push("第一条", GREEN_ACCENT)
    queue.hold_ms = LONG_HOLD_MS
    queue.push("第二条", GREEN_ACCENT)

    QTest.qWait(WAIT_MS)

    assert queue.texts() == ["第二条"], "短停留的先消失"


def test_cards_drop_back_to_anchor_when_lower_card_expires(queue: StatusToastQueue) -> None:
    queue.hold_ms = LONG_HOLD_MS
    queue.push("第一条", GREEN_ACCENT)
    queue.hold_ms = HOLD_MS
    queue.push("第二条", GREEN_ACCENT)
    QTest.qWait(SLIDE_MS + 40)
    stacked_y = queue.cards()[0].card_geometry().y()

    QTest.qWait(WAIT_MS)

    assert queue.texts() == ["第一条"]
    assert queue.cards()[0].card_geometry().y() > stacked_y, "下方卡片消失后，上方卡片落回锚点"


def test_new_cards_appear_above_the_footer(queue: StatusToastQueue, host: QWidget) -> None:
    queue.push("第一条", GREEN_ACCENT)
    queue.push("第二条", GREEN_ACCENT)

    for card in queue.cards():
        assert card.geometry().bottom() <= host.height()


def test_stacked_cards_drop_oldest_beyond_cap(queue: StatusToastQueue) -> None:
    for index in range(MAX_VISIBLE + 2):
        queue.push(f"反馈 {index}", GREEN_ACCENT)

    texts = queue.texts()
    assert len(texts) == MAX_VISIBLE
    assert texts[-1] == f"反馈 {MAX_VISIBLE + 1}"


def test_clear_removes_every_card(queue: StatusToastQueue) -> None:
    queue.push("第一条", GREEN_ACCENT)
    queue.push("第二条", GREEN_ACCENT)

    queue.clear()

    assert queue.texts() == []
    assert queue.cards() == []


def test_cards_follow_host_resize(queue: StatusToastQueue, host: QWidget) -> None:
    queue.push("很长的反馈文本" * 8, GREEN_ACCENT)
    wide = queue.cards()[0].width()

    host.resize(240, 300)
    QTest.qWait(30)

    card = queue.cards()[0]
    assert card.width() < wide, "宿主变窄后必须重新折行"
    assert card.geometry().right() <= host.width()


def test_card_has_no_second_graphics_effect(queue: StatusToastQueue) -> None:
    """卡片不能再挂投影等第二层图形效果。

    嵌套效果（外层淡出 + 内层投影）会让可见卡片改尺寸后整块不绘制，
    浮起观感由渐变底色和描边提供。
    """
    queue.push("已保存", GREEN_ACCENT)
    card = queue.cards()[0]
    inner = card.findChild(QFrame, "statusToastCard")

    assert inner is not None
    assert inner.graphicsEffect() is None


# ── 宿主查找 ─────────────────────────────────────────────────────────


def test_helper_falls_back_without_toast_host(host: QWidget) -> None:
    """面板被单独实例化（没有设置窗口）时回退，让调用方保留原地文本。"""
    assert show_status_toast(host, "已保存", GREEN_ACCENT) is False


def test_helper_routes_to_host_window_queue(host: QWidget) -> None:
    queue = StatusToastQueue(host)

    assert show_status_toast(host, "已保存", GREEN_ACCENT) is True
    assert queue.texts() == ["已保存"]
