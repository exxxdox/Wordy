"""三种外观共用选择语义，经典样式保持原有像素。"""

import math

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from wordy.ui import wheel_paint


def _render(style, selected=None, editing=False, labels=None, highlights=None):
    image = QImage(540, 540, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    wheel_paint.paint_wheel(painter, ["你好"] * 8 if labels is None else labels, selected,
                            style=style, editing=editing, highlights=highlights)
    painter.end()
    return image


def test_classic_style_and_unknown_style_keep_existing_pixels(qapp):
    expected = QImage(540, 540, QImage.Format.Format_ARGB32_Premultiplied)
    expected.fill(Qt.GlobalColor.transparent)
    painter = QPainter(expected)
    wheel_paint._paint_classic_wheel(painter, ["你好"] * 8, 0)
    painter.end()
    assert _render("classic", 0) == expected
    assert _render("unknown", 0) == expected


@pytest.mark.parametrize("style", ["classic", "glass", "petals"])
@pytest.mark.parametrize("editing", [False, True])
def test_all_styles_keep_numbers_and_selection_feedback(qapp, monkeypatch, style, editing):
    texts = []
    monkeypatch.setattr(wheel_paint.glass, "paint_lifted_text",
                        lambda painter, rect, flags, text, color: texts.append(text))
    _render(style, 0, editing)
    assert all(f"0{index}" in texts for index in range(1, 9))
    assert "点击编辑" in texts if editing else "松开播放" in texts


def test_petals_have_clear_gaps_while_glass_remains_a_disc(qapp):
    petals = _render("petals")
    glass = _render("glass")
    # 八条方向边界在花瓣之间露出背景，而不是在盘面画八条浅色线。
    for index in range(8):
        angle = math.radians(112.5 - index * 45)
        x, y = round(270 + 170 * math.cos(angle)), round(270 - 170 * math.sin(angle))
        assert petals.pixelColor(x, y).alpha() == 0
        assert glass.pixelColor(x, y).alpha() > 100


@pytest.mark.parametrize("style", ["glass", "petals"])
def test_new_styles_do_not_highlight_empty_runtime_slots(qapp, style):
    assert _render(style, 0, labels=[""] * 8) == _render(style, None, labels=[""] * 8)


@pytest.mark.parametrize("style", ["glass", "petals"])
def test_new_styles_blend_intermediate_hover_frames(qapp, style):
    blues = [_render(style, 0, highlights=[strength] + [0] * 7).pixelColor(270, 155).blue()
             for strength in (0, 0.5, 1)]
    assert blues[0] < blues[1] < blues[2]
