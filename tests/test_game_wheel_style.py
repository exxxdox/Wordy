"""竞技轮盘保留透明游戏背景和原有的八向选择语义。"""

import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from wordy.ui import wheel_paint


def _render(labels, selected, *, highlights=None, opacity=1.0, editing=False):
    image = QImage(540, 540, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setOpacity(opacity)
    wheel_paint.paint_wheel(painter, labels, selected, style="game", editing=editing,
                            highlights=highlights)
    painter.end()
    return image


def test_game_wheel_is_translucent_and_highlights_direction(qapp):
    labels = ["谢谢", "别激动", "是不是亮瞎了", "我后悔了", "能缓一缓了", "这游戏太难玩", "我的锅", "很好"]
    idle = _render(labels, None)
    selected = _render(labels, 2)
    # 扇区本身保持透明；选中是浅色扇面，不是蓝色实心圆盘。
    sample = (410, 285)
    assert 0 < selected.pixelColor(*sample).alpha() < 180
    assert selected.pixelColor(*sample).red() > idle.pixelColor(*sample).red() + 80
    assert selected.pixelColor(0, 0).alpha() == 0


@pytest.mark.parametrize("editing", [False, True])
def test_game_wheel_empty_selection_and_fade(qapp, editing):
    empty = [""] * 8
    if not editing:
        assert _render(empty, 0) == _render(empty, None)
    full = _render(["Audio"] * 8, 2, editing=editing)
    half = _render(["Audio"] * 8, 2, opacity=0.5, editing=editing)
    assert half.pixelColor(270, 270).alpha() < full.pixelColor(270, 270).alpha()


def test_game_wheel_long_names_stay_inside_window(qapp):
    image = _render(["这是一个很长的音频名称用于检查文字省略"] * 8, 2)
    # 窗口最外侧留透明余量，长名称不能画到窗口边界被硬切。
    assert all(image.pixelColor(0, y).alpha() == 0 for y in range(540))
    assert all(image.pixelColor(539, y).alpha() == 0 for y in range(540))


def test_game_wheel_side_names_clear_metal_base_and_match_preview(qapp, monkeypatch):
    calls = []

    def capture(painter, rect, flags, text, color, **options):
        calls.append((rect, text))

    monkeypatch.setattr(wheel_paint.glass, "paint_lifted_text", capture)
    for editing in (False, True):
        calls.clear()
        _render(["是不是亮瞎了"] * 8, 2, editing=editing)
        assert calls[2][0].left() > 366
        assert calls[6][0].right() < 174
        assert calls[2][1] == "是不是亮瞎了"


@pytest.mark.parametrize("selected", [None, *range(8)])
def test_game_wheel_has_only_one_selected_pointer(qapp, selected):
    image = _render(["Audio"] * 8, selected)
    for index in range(8):
        angle = math.radians(90 - index * 45)
        x, y = round(270 + 90 * math.cos(angle)), round(270 - 90 * math.sin(angle))
        alpha = image.pixelColor(x, y).alpha()
        if index == selected:
            assert alpha > 200
        else:
            # 圆盘以外只保留透明扇面，不能残留固定方形/菱形底座的角。
            assert alpha < 180


def test_game_wheel_pointer_does_not_linger_in_previous_direction(qapp):
    image = _render(["Audio"] * 8, 2, highlights=[0.5, 0, 0.5, 0, 0, 0, 0, 0])
    assert image.pixelColor(270, 180).alpha() < 180
    empty = _render([""] * 8, 2, highlights=[0, 0, 1, 0, 0, 0, 0, 0])
    assert empty.pixelColor(360, 270).alpha() < 180


@pytest.mark.parametrize("selected", [None, 0])
def test_game_wheel_names_are_white_with_dark_shadow(qapp, selected):
    image = _render(["清晰白字"] * 8, selected)
    colors = [image.pixelColor(x, y) for x in range(150, 390) for y in range(44, 100)]
    # 未选中的名称也要是实白；近字缘须有足够实的黑影，才能从亮背景中分离。
    assert sum(c.red() > 248 and c.green() > 248 and c.blue() > 248 and c.alpha() > 248
               for c in colors) > 50
    assert sum(c.red() < 10 and c.green() < 10 and c.blue() < 10 and c.alpha() > 200
               for c in colors) > 20
