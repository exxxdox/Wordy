from __future__ import annotations

import pytest
from PySide6.QtCore import QEvent, QPoint, QPointF, Qt
from PySide6.QtGui import QMouseEvent

from wordy.ui import chat_wheel, wheel_paint
from wordy.ui.chat_wheel import ChatWheel


@pytest.mark.parametrize("point, expected", [
    (QPoint(270, 110), 0), (QPoint(390, 150), 1),
    (QPoint(430, 270), 2), (QPoint(390, 390), 3),
    (QPoint(270, 430), 4), (QPoint(150, 390), 5),
    (QPoint(110, 270), 6), (QPoint(150, 150), 7),
    (QPoint(270, 270), None), (QPoint(290, 270), None),
])
def test_direction_and_center_deadzone(qapp, point, expected):
    wheel = ChatWheel(lambda _: None)
    assert wheel._slot_at(point) == expected


def test_release_plays_selected_slot_and_hides(qapp, monkeypatch):
    selected: list[int] = []
    origin = QPoint(600, 600)
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: origin)
    wheel = ChatWheel(selected.append)
    wheel.open_wheel(["Audio"] * 8, 0x56)
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: origin + QPoint(160, 0))
    monkeypatch.setattr(chat_wheel, "_key_down", lambda _: False)
    wheel._poll()
    assert selected == [2]
    assert not wheel.isVisible()
    assert not wheel._timer.isActive()


@pytest.mark.parametrize("labels, point", [([""] * 8, QPoint(430, 270)), (["Audio"] * 8, QPoint(270, 270))])
def test_empty_slot_and_center_cancel(qapp, monkeypatch, labels, point):
    selected: list[int] = []
    origin = QPoint(600, 600)
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: origin)
    wheel = ChatWheel(selected.append)
    wheel.open_wheel(labels, 0x56)
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: origin + point - QPoint(270, 270))
    monkeypatch.setattr(chat_wheel, "_key_down", lambda _: False)
    wheel._poll()
    assert selected == []
    assert not wheel.isVisible()


@pytest.mark.parametrize("cancel_key", [0x1B, 0x02])
def test_global_escape_and_right_button_cancel(qapp, monkeypatch, cancel_key):
    selected: list[int] = []
    wheel = ChatWheel(selected.append)
    wheel.open_wheel(["Audio"] * 8, 0x56)
    monkeypatch.setattr(chat_wheel, "_key_down", lambda key: key == cancel_key)
    wheel._poll()
    assert selected == []
    assert not wheel.isVisible()


def test_wheel_preserves_game_focus_and_normalizes_slots(qapp):
    wheel = ChatWheel(lambda _: None)
    wheel.open_wheel(["Only slot"])
    assert len(wheel._labels) == 8
    assert wheel.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert wheel.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus
    wheel.cancel()


def test_open_and_cancel_never_move_cursor(qapp, monkeypatch):
    moved: list[QPoint] = []
    origin = QPoint(30, 40)
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: origin)
    monkeypatch.setattr(chat_wheel.QCursor, "setPos", moved.append)
    wheel = ChatWheel(lambda _: None)
    wheel.open_wheel(["Audio"] * 8)
    wheel.cancel()
    wheel.cancel()
    assert moved == []


def test_stationary_cursor_at_screen_edge_never_selects(qapp, monkeypatch):
    selected: list[int] = []
    origin = QPoint(30, 40)
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: origin)
    monkeypatch.setattr(chat_wheel.QCursor, "setPos", lambda _point: None)
    monkeypatch.setattr(chat_wheel, "clamp_window_position", lambda *_args: (0, 0))
    monkeypatch.setattr(chat_wheel, "_key_down", lambda _: False)
    wheel = ChatWheel(selected.append)
    wheel.open_wheel(["Audio"] * 8, 0x56)
    wheel._poll()
    assert selected == []
    assert not wheel.isVisible()


@pytest.mark.parametrize("origin", [QPoint(30, 40), QPoint(-1800, 900), QPoint(3500, -200)])
def test_direction_uses_invocation_position_with_clamped_window(qapp, monkeypatch, origin):
    selected: list[int] = []
    cursor = QPoint(origin)
    moved: list[QPoint] = []
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: cursor)
    monkeypatch.setattr(chat_wheel.QCursor, "setPos", moved.append)
    monkeypatch.setattr(chat_wheel, "clamp_window_position", lambda *_args: (0, 0))
    monkeypatch.setattr(chat_wheel, "_key_down", lambda _: False)
    wheel = ChatWheel(selected.append)
    wheel.open_wheel(["Audio"] * 8, 0x56)
    cursor = origin + QPoint(160, 0)
    wheel._poll()
    assert selected == [2]
    assert moved == []


def test_mouse_move_uses_global_anchor_but_click_uses_visible_sector(qapp, monkeypatch):
    selected: list[int] = []
    origin = QPoint(30, 40)
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: origin)
    monkeypatch.setattr(chat_wheel, "clamp_window_position", lambda *_args: (0, 0))
    wheel = ChatWheel(selected.append)
    wheel.open_wheel(["Audio"] * 8)
    # 局部位置朝下、相对唤出点全局位置朝右，证明移动未误用窗口映射。
    local = QPointF(270, 430)
    global_position = QPointF(origin + QPoint(160, 0))
    move = QMouseEvent(QEvent.Type.MouseMove, local, global_position,
                      Qt.MouseButton.NoButton, Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier)
    wheel.mouseMoveEvent(move)
    assert wheel._selected == 2
    click = QMouseEvent(QEvent.Type.MouseButtonPress, local, global_position,
                       Qt.MouseButton.LeftButton, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    wheel.mousePressEvent(click)
    assert selected == [4]
    assert not wheel.isVisible()


@pytest.mark.parametrize("origin", [QPoint(20, 20), QPoint(520, 20), QPoint(20, 520), QPoint(520, 520)])
def test_corner_render_keeps_anchor_and_selected_title(qapp, monkeypatch, origin):
    monkeypatch.setattr(chat_wheel.QCursor, "pos", lambda: origin)
    monkeypatch.setattr(chat_wheel, "clamp_window_position", lambda *_args: (0, 0))
    texts = []
    original = wheel_paint.glass.paint_lifted_text

    def capture(painter, rect, flags, text, color):
        texts.append(text)
        original(painter, rect, flags, text, color)

    monkeypatch.setattr(wheel_paint.glass, "paint_lifted_text", capture)
    wheel = ChatWheel(lambda _: None)
    title = "这是一段需要在中心显示的长音频名称用于验证省略"
    wheel.open_wheel([title])
    wheel._selected = 0
    image = wheel.grab()
    assert not image.isNull()
    assert "松开播放" in texts
    assert any(text.startswith("这是一段") and text != title for text in texts)
    assert "—" in texts
    assert wheel._slot_at_global(origin) is None
    assert wheel._slot_at_global(origin + QPoint(160, 0)) == 2
    wheel.cancel()
