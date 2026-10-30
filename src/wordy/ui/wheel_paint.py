"""设置预览与运行轮盘共享的玻璃盘体。"""

from __future__ import annotations

import math
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from wordy.ui import glass
from wordy.ui.theme import ACCENT_SECONDARY, ELEVATED_BG, GREEN_ACCENT, TEXT_MUTED, TEXT_PRIMARY, WINDOW_BG, tint


def paint_wheel(painter: QPainter, labels: list[str], selected_index: int | None,
                *, editing: bool = False) -> None:
    """统一盘体样式；缩略图仅提高字级并使用编辑提示。"""
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

    for index, label in enumerate(labels):
        selected = index == selected_index and (bool(label) or editing)
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
        lit.setAlpha(235 if selected else 225 if label else 100)
        shade = base.darker(135)
        shade.setAlpha(175 if selected else 200 if label else 90)
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
        painter.setPen(QPen(accent if selected else tint(TEXT_PRIMARY, 45 if label else 18), 1.0))
        painter.drawPath(path)
        radians = math.radians(90 - index * 45)
        text_center = center + QPointF(158 * math.cos(radians), -158 * math.sin(radians))
        painter.setFont(QFont("Segoe UI", 9 if not editing else 13))
        glass.paint_lifted_text(
            painter,
            QRectF(text_center.x() - 54, text_center.y() - 30, 108, 20),
            Qt.AlignmentFlag.AlignCenter,
            f"0{index + 1}",
            accent if selected else secondary if label else QColor(TEXT_MUTED),
        )
        painter.setFont(QFont("Microsoft YaHei UI", 10 if not editing else 14, QFont.Weight.DemiBold))
        text = painter.fontMetrics().elidedText(label or "—", Qt.TextElideMode.ElideRight, 116)
        glass.paint_lifted_text(
            painter,
            QRectF(text_center.x() - 62, text_center.y() - 6, 124, 34),
            Qt.AlignmentFlag.AlignCenter,
            text,
            QColor(TEXT_PRIMARY) if label else QColor(TEXT_MUTED),
        )

    # 只保留八个方位；选中方向用粗弧补充颜色反馈。
    for tick in range(8):
        angle = math.radians(tick * 45)
        painter.setPen(QPen(secondary, 1.5))
        painter.drawLine(center + QPointF(241 * math.cos(angle), 241 * math.sin(angle)),
                         center + QPointF(248 * math.cos(angle), 248 * math.sin(angle)))
    if selected_index is not None and (labels[selected_index] or editing):
        painter.setPen(QPen(accent, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(31, 31, 478, 478),
                        int((90 - selected_index * 45 - 14) * 16), 28 * 16)
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
    has_selection = selected_index is not None and bool(labels[selected_index])
    painter.setFont(QFont("Microsoft YaHei UI", 10 if not editing else 14, QFont.Weight.DemiBold))
    # 中心补足扇区省略的名称，最多两行，超长第二行仍保留省略号。
    title = labels[selected_index] if selected_index is not None and has_selection else "WORDY"
    metrics = painter.fontMetrics()
    split = 0
    while split < len(title) and metrics.horizontalAdvance(title[:split + 1]) <= 112:
        split += 1
    lines = [title] if split == len(title) else [title[:split], metrics.elidedText(
        title[split:], Qt.TextElideMode.ElideRight, 112)]
    for row, text in enumerate(lines):
        glass.paint_lifted_text(
            painter, QRectF(214, 232 + row * 22 if len(lines) > 1 else 243, 112, 22),
            Qt.AlignmentFlag.AlignCenter, text, accent if has_selection else secondary,
        )
    painter.setFont(QFont("Microsoft YaHei UI", 9 if not editing else 13))
    glass.paint_lifted_text(
        painter, QRectF(207, 286, 126, 23), Qt.AlignmentFlag.AlignCenter,
        "点击编辑" if editing else "松开播放" if has_selection else "移回取消", QColor(TEXT_MUTED),
    )
