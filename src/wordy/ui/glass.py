#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Apple 式玻璃的光学线索：细噪点、亮棱 + 暗过渡、圆形边缘光渗。

没有真实背景模糊时，玻璃感来自「亮棱紧贴一条暗过渡」和材质噪声，而不是单纯
提亮面板。这三件事在悬浮输入框、音频轮盘、反馈卡片之间重复，集中在这里。

每层都绘同一条路径、由粗到细叠加，不做任何路径运算，因此可以放在每帧重绘的
热路径上。
"""

from __future__ import annotations

import random

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import (
    QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QRadialGradient,
)

# 噪点块：64×64 足够打散色带，又能在首次绘制时立刻生成完。
NOISE_TILE_PX = 64
# 固定种子：噪点是材质，不该每次启动长成另一个样子。
_NOISE_SEED = 0x5F3A2C
_NOISE_ALPHA_RANGE = (48, 255)
# 噪点整体强度：只够打破纯色，不该被看出来是颗粒。
NOISE_STRENGTH = 0.035

# 亮棱内侧的暗过渡：给边缘做出玻璃的厚度。
SHADE_ALPHA = 72
SHADE_WIDTH = 3.0
# 玻璃上文字的投影：够把内容从玻璃上分离，又不至于读成描边字。
TEXT_SHADOW_ALPHA = 150

_noise_image: QImage | None = None


def noise_image() -> QImage:
    """预生成并缓存噪点块；绘制热路径只做一次平铺。

    用 QImage 而不是 QPixmap：QPixmap 要求先有 QGuiApplication，而玻璃绘制本身
    只依赖光栅缓冲，做成纯 QImage 后离屏渲染和单元测试都不必拉起 Qt 应用。
    """
    global _noise_image
    if _noise_image is None:
        rng = random.Random(_NOISE_SEED)
        low, high = _NOISE_ALPHA_RANGE
        image = QImage(NOISE_TILE_PX, NOISE_TILE_PX, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor(0, 0, 0, 0))
        # 亮暗各半：整块的平均亮度不变，只增加颗粒，不会把底色整体提亮或压暗。
        for y in range(NOISE_TILE_PX):
            for x in range(NOISE_TILE_PX):
                level = 255 if rng.random() < 0.5 else 0
                image.setPixelColor(x, y, QColor(level, level, level, rng.randint(low, high)))
        _noise_image = image
    return _noise_image


def paint_noise(painter: QPainter, path: QPainterPath, strength: float = NOISE_STRENGTH) -> None:
    """在路径内平铺噪点，给玻璃加一层极细的材质散射。"""
    if strength <= 0.0:
        return
    painter.save()
    painter.setClipPath(path)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(noise_image()))
    painter.setOpacity(strength)
    painter.drawPath(path)
    painter.restore()


def _rounded(rect: QRectF, radius: float) -> QPainterPath:
    path = QPainterPath()
    path.addRoundedRect(rect, max(radius, 0.0), max(radius, 0.0))
    return path


def _inset_path(bounds: QRectF, radius: float | None, inset: float) -> QPainterPath:
    """按 inset 内缩（负数外扩）边界。

    解析地收缩矩形与圆角，而不是「画粗笔再裁掉一半」：裁剪边界是按像素量化的，
    在曲线上会把笔宽切得忽粗忽细，圆角处最明显。
    """
    shrunk = bounds.adjusted(inset, inset, -inset, -inset)
    if radius is None:
        path = QPainterPath()
        path.addEllipse(shrunk)
        return path
    return _rounded(shrunk, radius - inset)


def paint_glass_rim(
    painter: QPainter,
    bounds: QRectF,
    radius: float,
    rim: QBrush | QColor,
    *,
    opacity: float = 1.0,
    shade_alpha: int = SHADE_ALPHA,
    shade_width: float = SHADE_WIDTH,
) -> None:
    """画圆角矩形玻璃面板的边缘：内侧暗过渡 + 贴边的亮棱。

    亮棱必须压着一条暗过渡才读成玻璃；只有一条亮线会退化成普通描边。

    两层都画在解析内缩的路径上、完全落在边界以内，所以沿整条边粗细一致：
    既不做裁剪（裁剪边界按像素量化，会把笔宽在曲线上切得忽粗忽细），也不向
    外画发光（面板贴着窗口时，直边外侧没有余量、圆角外侧有余量，同一圈光会
    一处被裁一处露出，看着就是多出来的一道弧）。
    """
    if opacity <= 0.0:
        return
    painter.save()
    painter.setBrush(Qt.BrushStyle.NoBrush)

    if shade_alpha > 0:
        half = shade_width / 2.0
        painter.setPen(QPen(QColor(0, 0, 0, round(shade_alpha * opacity)), shade_width))
        painter.drawPath(_inset_path(bounds, radius, half))

    # 亮棱内缩半个像素，1px 笔正好落满一列像素，直边不会忽亮忽暗。
    painter.setPen(QPen(rim if isinstance(rim, QBrush) else QBrush(rim), 1.0))
    painter.setOpacity(opacity)
    painter.drawPath(_inset_path(bounds, radius, 0.5))
    painter.restore()


def paint_edge_glow(
    painter: QPainter,
    path: QPainterPath,
    bounds: QRectF,
    *,
    alpha: int,
    opacity: float = 1.0,
) -> None:
    """圆形玻璃的边缘光渗：越靠近外缘越亮，中心保持透明。

    只适合接近圆形的面板；长条形用径向渐变会让两端过曝，那种形状靠
    :func:`paint_glass_rim` 的亮棱表达边缘。
    """
    if alpha <= 0 or opacity <= 0.0:
        return
    painter.save()
    painter.setClipPath(path)
    gradient = QRadialGradient(bounds.center(), max(bounds.width(), bounds.height()) / 2.0)
    gradient.setColorAt(0.0, QColor(255, 255, 255, 0))
    gradient.setColorAt(0.72, QColor(255, 255, 255, 0))
    gradient.setColorAt(1.0, QColor(255, 255, 255, round(alpha * opacity)))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    painter.drawPath(path)
    painter.restore()


def paint_lifted_text(
    painter: QPainter,
    rect: QRectF,
    alignment: Qt.AlignmentFlag,
    text: str,
    color: QColor,
    *,
    opacity: float = 1.0,
) -> None:
    """文字先画一层下移 1px 的暗影，内容就从玻璃上浮起来，而不是印进玻璃里。

    调用方负责先设好字体；投影跟随同一套字体度量，所以两遍文字完全重合。
    """
    painter.save()
    painter.setPen(QColor(0, 0, 0, round(TEXT_SHADOW_ALPHA * opacity)))
    painter.drawText(rect.translated(0, 1), alignment, text)
    painter.setPen(color)
    painter.drawText(rect, alignment, text)
    painter.restore()
