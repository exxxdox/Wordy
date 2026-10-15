#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""玻璃光学线索契约测试。

没有真实背景模糊时，玻璃感来自「亮棱紧贴一条暗过渡」和材质噪声，而不是单纯
提亮。这些断言把这几条光学线索钉住，避免后续调色把它们退化成普通描边。
"""

from __future__ import annotations

import math
import os
from collections.abc import Callable

import pytest

_ = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6.QtGui")

from PySide6.QtCore import QRectF  # noqa: E402
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath  # noqa: E402

from wordy.ui import glass  # noqa: E402

_BASE = QColor(28, 28, 30)
_WHITE = QColor(255, 255, 255)
# 玻璃面板的绘制边界与圆角；用例里统一用这一组，不依赖具体窗口尺寸。
_BOUNDS = QRectF(2, 2, 156, 76)
_RADIUS = 12.0


def _rounded_path(width: int, height: int, radius: float = 12.0) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(QRectF(2, 2, width - 4, height - 4), radius, radius)
    return path


def _render(width: int, height: int, draw: Callable[[QPainter], None]) -> QImage:
    image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(_BASE)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    draw(painter)
    painter.end()
    return image


def _column(image: QImage, x: int, top: int, bottom: int) -> list[int]:
    return [image.pixelColor(x, y).lightness() for y in range(top, bottom)]


# ---------------------------------------------------------------------------
# 噪点
# ---------------------------------------------------------------------------


def test_noise_tile_is_square_and_cached() -> None:
    """噪点块预生成一次并复用，绘制热路径不应重复造像素。"""
    tile = glass.noise_image()

    assert tile.width() == tile.height() == glass.NOISE_TILE_PX
    assert glass.noise_image() is tile, "噪点块必须缓存，不能在每次绘制时重建"


def test_noise_tile_has_both_light_and_dark_grain() -> None:
    """噪点必须亮暗都有，否则整块会朝一个方向偏移底色。"""
    tile_image = glass.noise_image()
    levels = {
        tile_image.pixelColor(x, y).lightness()
        for y in range(0, tile_image.height(), 3)
        for x in range(0, tile_image.width(), 3)
    }

    assert min(levels) < 128, "噪点缺少暗颗粒"
    assert max(levels) > 128, "噪点缺少亮颗粒"


def test_paint_noise_adds_grain_inside_the_path_only() -> None:
    """噪点只在路径内平铺，且确实在底色上叠加了颗粒。"""
    image = _render(120, 60, lambda painter: glass.paint_noise(painter, _rounded_path(120, 60)))
    inside = _column(image, 60, 12, 20)

    assert len(set(inside)) > 1, "路径内必须出现颗粒，而不是一片纯色"
    assert image.pixelColor(60, 1).lightness() == _BASE.lightness(), "路径外不应被噪点影响"


def test_paint_noise_strength_zero_is_a_no_op() -> None:
    """强度为 0 时不绘制，便于按表面关闭噪点。"""
    image = _render(
        120, 60, lambda painter: glass.paint_noise(painter, _rounded_path(120, 60), strength=0.0)
    )
    inside = _column(image, 60, 12, 20)

    assert set(inside) == {_BASE.lightness()}, "强度 0 时不应改变任何像素"


# ---------------------------------------------------------------------------
# 亮棱 + 暗过渡
# ---------------------------------------------------------------------------


def _rim(painter: QPainter, **kwargs: float) -> None:
    glass.paint_glass_rim(painter, _BOUNDS, _RADIUS, _WHITE, **kwargs)  # type: ignore[arg-type]


def _render_rim(**kwargs: float) -> QImage:
    return _render(160, 80, lambda painter: _rim(painter, **kwargs))


def test_glass_rim_creates_a_dark_transition_inside_the_edge() -> None:
    """亮棱内侧必须有暗过渡：只有一条亮线会读成描边，不是玻璃。"""
    image = _render_rim()
    fill = image.pixelColor(80, 40).lightness()
    profile = _column(image, 80, 0, 16)
    peak = max(profile[0:4])
    valley = min(profile[3:8])

    assert peak > fill + 40, f"边缘亮棱不够亮：peak={peak} fill={fill}"
    assert valley < fill - 3, f"亮棱内侧缺少暗过渡：valley={valley} fill={fill}"


def test_glass_rim_brightness_is_even_along_the_whole_outline() -> None:
    """亮棱必须整圈都在，且直边上要满亮度。

    用「画粗笔再裁掉一半」实现时，裁剪边界按像素量化，会把笔宽在曲线上切得
    忽粗忽细——直边的粗细会随位置变化，圆角处还会被切缺。1px 细线在斜角上
    因抗锯齿略暗属正常，但直边必须像素级拉满。
    """
    image = _render_rim()
    fill = image.pixelColor(80, 40).lightness()

    def peak(x: float, y: float) -> int:
        best = 0
        for offset_y in (-1, 0, 1):
            for offset_x in (-1, 0, 1):
                best = max(best, image.pixelColor(round(x) + offset_x, round(y) + offset_y).lightness())
        return best

    straight = [peak(80, 2), peak(80, 78), peak(2, 40), peak(158, 40)]
    corner_x, corner_y = 2 + _RADIUS, 2 + _RADIUS
    corners = [
        peak(corner_x + _RADIUS * math.cos(angle), corner_y + _RADIUS * math.sin(angle))
        for angle in (math.radians(180 + step * 30) for step in range(4))
    ]

    assert min(straight) == 255, f"直边亮棱必须满亮度，不能忽明忽暗：{straight}"
    assert min(corners) > fill + 120, f"圆角处的亮棱被切缺：corners={corners} fill={fill}"


def test_glass_rim_keeps_the_interior_fill_untouched() -> None:
    """边缘处理只作用在贴边几像素，玻璃内部保持调用方绘制的底色。"""
    assert _render_rim().pixelColor(80, 40).lightness() == _BASE.lightness()


def test_glass_rim_scales_with_opacity() -> None:
    """边缘必须跟随窗口不透明度，否则半透明窗口会留下一圈实心亮边。"""
    fill = _BASE.lightness()
    opaque_peak = max(_column(_render_rim(), 80, 0, 4))
    faded_peak = max(_column(_render_rim(opacity=0.25), 80, 0, 4))

    assert faded_peak < opaque_peak, "降低不透明度必须同时压低亮棱"
    assert faded_peak >= fill, "亮棱衰减后仍不应比底色更暗"


def test_glass_rim_restores_painter_state_between_calls() -> None:
    """不透明度必须还原，否则后画的浅色内容会被前一次的透明度连累。"""

    def draw(painter: QPainter) -> None:
        _rim(painter, opacity=0.25)
        painter.setPen(_WHITE)
        painter.setBrush(_WHITE)
        painter.drawRect(QRectF(20, 30, 100, 20))

    image = _render(160, 80, draw)
    assert image.pixelColor(80, 40).lightness() == 255, "后续绘制被上一次的不透明度削弱"


# ---------------------------------------------------------------------------
# 圆形内发光
# ---------------------------------------------------------------------------


def _circle() -> QPainterPath:
    path = QPainterPath()
    path.addEllipse(QRectF(4, 4, 152, 152))
    return path


def test_edge_glow_brightens_a_circle_towards_its_rim() -> None:
    """圆形玻璃的边缘光渗：越靠近外缘越亮，中心保持底色。"""
    path = _circle()
    image = _render(
        160, 160, lambda painter: glass.paint_edge_glow(painter, path, QRectF(4, 4, 152, 152), alpha=70)
    )
    center = image.pixelColor(80, 80).lightness()
    near_rim = image.pixelColor(80, 22).lightness()

    assert near_rim > center, f"外缘必须亮于中心：rim={near_rim} center={center}"
    assert center == _BASE.lightness(), "中心不应被光渗提亮"


def test_edge_glow_ignores_alpha_at_zero() -> None:
    """alpha 为 0 时不绘制，便于按表面关闭光渗。"""
    path = _circle()
    image = _render(
        160,
        160,
        lambda painter: glass.paint_edge_glow(painter, path, QRectF(4, 4, 152, 152), alpha=0),
    )

    assert image.pixelColor(80, 22).lightness() == _BASE.lightness()
