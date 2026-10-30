"""轮盘投影在运行尺寸和设置预览尺寸下都应平滑，且不压暗盘体。"""

import math

import pytest
from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPainterPath

from wordy.ui import wheel_paint


@pytest.mark.parametrize("size", [540, 400])
def test_wheel_shadow_fades_smoothly_outside_disc(size):
    image = QImage(size, size, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.scale(size / 540, size / 540)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    disc = QPainterPath()
    disc.addEllipse(QRectF(24, 24, 492, 492))
    wheel_paint._paint_wheel_shadow(painter, disc)
    painter.end()

    assert image.pixelColor(size // 2, size // 2).alpha() == 0
    # 从完全落在盘体外的像素开始，排除缩略图边界的抗锯齿覆盖率。
    edge = math.ceil(516 * size / 540)
    alphas = [image.pixelColor(x, size // 2).alpha() for x in range(edge, size)]
    assert 0 < max(alphas) < 60
    assert all(a >= b for a, b in zip(alphas, alphas[1:]))
    # 每像素连续衰减，不能回到每隔数像素跳一档的同心圆。
    assert max(a - b for a, b in zip(alphas, alphas[1:])) <= 8
    assert all(image.pixelColor(x, size - 1).alpha() == 0 for x in range(size))


def test_wheel_shadow_does_not_darken_translucent_disc(qapp, monkeypatch):
    def render():
        image = QImage(540, 540, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        wheel_paint.paint_wheel(painter, [""] * 8, None)
        painter.end()
        return image

    with_shadow = render()
    monkeypatch.setattr(wheel_paint, "_paint_wheel_shadow", lambda painter, disc: None)
    without_shadow = render()
    # 空槽较透明，最容易暴露盘底投影；外影开关不得改变盘内像素。
    for x, y in ((270, 50), (270, 110), (110, 270), (430, 270), (270, 490)):
        assert with_shadow.pixelColor(x, y) == without_shadow.pixelColor(x, y)
