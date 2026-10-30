"""设置预览与运行轮盘共享的玻璃盘体。"""

from __future__ import annotations

import math
from PySide6.QtCore import QEasingCurve, QPointF, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPen, QRadialGradient
from PySide6.QtWidgets import QWidget
from wordy.ui import glass
from wordy.ui.theme import ACCENT_SECONDARY, ELEVATED_BG, GREEN_ACCENT, TEXT_MUTED, TEXT_PRIMARY, WINDOW_BG, tint


class WheelHover(QVariantAnimation):
    """高亮从当前画面续接，快速扫过时不积压动画。"""

    def __init__(self, widget: QWidget) -> None:
        super().__init__(widget)
        self.values = [0.0] * 8
        self._start = self.values.copy()
        self._target: int | None = None
        self.setDuration(90)
        self.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.setStartValue(0.0)
        self.setEndValue(1.0)
        self.valueChanged.connect(self._advance)
        self.valueChanged.connect(lambda _value: widget.update())

    def reset(self) -> None:
        self.stop()
        self.values = [0.0] * 8
        self._start = self.values.copy()
        self._target = None

    def select(self, index: int | None) -> None:
        if index == self._target:
            return
        self.stop()
        self._start = self.values.copy()
        self._target = index
        self.setCurrentTime(0)
        self.start()

    def _advance(self, value: float) -> None:
        self.values = [start + (float(i == self._target) - start) * value
                       for i, start in enumerate(self._start)]


def _paint_wheel_shadow(painter: QPainter, disc: QPainterPath) -> None:
    """连续衰减的外侧投影，不让黑影透过半透明盘体。"""
    bounds = disc.boundingRect().adjusted(-18, -18, 18, 18).translated(0, 3)
    shadow = QPainterPath()
    shadow.addEllipse(bounds)
    gradient = QRadialGradient(bounds.center(), bounds.width() / 2)
    gradient.setColorAt(0.0, QColor(0, 0, 0, 60))
    gradient.setColorAt(0.90, QColor(0, 0, 0, 60))
    gradient.setColorAt(0.93, QColor(0, 0, 0, 45))
    gradient.setColorAt(0.96, QColor(0, 0, 0, 16))
    gradient.setColorAt(1.0, QColor(0, 0, 0, 0))
    painter.save()
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(gradient)
    # 挖掉盘体而非叠黑实心圆；外沿在窗口余量内降到全透明，避免硬圈与裁切。
    painter.drawPath(shadow.subtracted(disc))
    painter.restore()


def paint_wheel(painter: QPainter, labels: list[str], selected_index: int | None,
                *, editing: bool = False, highlights: list[float] | None = None,
                style: str = "classic") -> None:
    """预览与运行时统一分派；旧配置和未知样式保持经典外观。"""
    if style == "game":
        _paint_game_wheel(painter, labels, selected_index, editing=editing, highlights=highlights)
    elif style in ("glass", "petals"):
        _paint_modern_wheel(painter, labels, selected_index, editing=editing,
                            highlights=highlights, petals=style == "petals")
    else:
        _paint_classic_wheel(painter, labels, selected_index, editing=editing, highlights=highlights)


def _paint_game_wheel(painter: QPainter, labels: list[str], selected_index: int | None,
                      *, editing: bool, highlights: list[float] | None) -> None:
    """竞技轮盘：半透明扇面、外围大字与随选择方向偏移的金属圆钮。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    center = QPointF(270, 270)
    outer = QRectF(14, 14, 512, 512)
    # 扇面内沿紧跟圆环外沿（62px），否则中间会露出一圈透明空隙。
    inner = QRectF(208, 208, 124, 124)
    strengths = [
        (highlights[index] if highlights is not None else float(index == selected_index))
        if label or editing else 0.0
        for index, label in enumerate(labels)
    ]
    # 边界不画描边、外沿渐隐，让游戏背景自然穿过扇区而非被实心圆盘遮住。
    painter.setPen(Qt.PenStyle.NoPen)
    for index, strength in enumerate(strengths):
        angle = 112.5 - index * 45
        path = QPainterPath()
        path.arcMoveTo(outer, angle)
        path.arcTo(outer, angle, -45)
        end = math.radians(angle - 45)
        path.lineTo(center + QPointF(62 * math.cos(end), -62 * math.sin(end)))
        path.arcTo(inner, angle - 45, 45)
        path.closeSubpath()
        # 黑白相间：偶数槽亮、奇数槽暗，低透明度让游戏画面继续透出。
        dark = index % 2 != 0
        base_tone = 12 if dark else 222
        tone = round(base_tone + (235 - base_tone) * strength)
        base_alpha = 78 if dark else 12
        inner_alpha = round(base_alpha + (144 - base_alpha) * strength)
        face = QRadialGradient(center, 256)
        face.setColorAt(0.0, QColor(tone, tone, tone, inner_alpha))
        face.setColorAt(0.35, QColor(tone, tone, tone, inner_alpha))
        face.setColorAt(0.82, QColor(tone, tone, tone, round(inner_alpha * 0.6)))
        face.setColorAt(1.0, QColor(tone, tone, tone, 0))
        painter.setBrush(face)
        painter.drawPath(path)

    # 左右长名称向窗口内侧排版，给中心圆钮和窗口边缘各留余量。
    text_rects = (
        QRectF(150, 44, 240, 56), QRectF(306, 122, 228, 56),
        QRectF(368, 242, 166, 56), QRectF(306, 362, 228, 56),
        QRectF(150, 440, 240, 56), QRectF(6, 362, 228, 56),
        QRectF(6, 242, 166, 56), QRectF(6, 122, 228, 56),
    )
    for index, label in enumerate(labels):
        strength = strengths[index]
        font = QFont("Microsoft YaHei UI")
        # 竞技轮盘的名称靠近边缘，预览连同文字等比缩放，避免比实际轮盘更早省略。
        font.setPixelSize(round(22 + 4 * strength))
        painter.setFont(font)
        rect = text_rects[index]
        text = painter.fontMetrics().elidedText(label or "—", Qt.TextElideMode.ElideRight,
                                               round(rect.width() - 4))
        # 游戏画面亮暗变化大：已有名称始终用实白，薄黑字缘和下移阴影共同保住对比。
        color = QColor(255, 255, 255, 255 if label else round(90 + 165 * strength))
        painter.setPen(QColor(0, 0, 0, 220))
        for dx, dy in ((-1, 0), (1, 0), (0, -1), (0, 2)):
            painter.drawText(rect.translated(dx, dy), Qt.AlignmentFlag.AlignCenter, text)
        glass.paint_lifted_text(painter, rect, Qt.AlignmentFlag.AlignCenter, text, color,
                                opacity=painter.opacity())

    # 选择方向偏移先算出来：金属圆钮与圆环高光共用同一个方向。
    offset = QPointF()
    for index, strength in enumerate(strengths):
        radians = math.radians(90 - index * 45)
        offset += QPointF(math.cos(radians), -math.sin(radians)) * (28 * strength)
    # 指向角只属于当前有效选择；不沿动画残影画旧方向，也不常驻多角底座。
    if selected_index is not None and (labels[selected_index] or editing):
        painter.save()
        painter.translate(center)
        painter.rotate(selected_index * 45)
        painter.setOpacity(painter.opacity() * strengths[selected_index])
        pointer = QPainterPath(QPointF(0, -96))
        pointer.lineTo(-46, -54)
        pointer.lineTo(46, -54)
        pointer.closeSubpath()
        metal = QLinearGradient(QPointF(-20, -96), QPointF(20, -54))
        metal.setColorAt(0, QColor(65, 73, 78, 250))
        metal.setColorAt(1, QColor(20, 27, 32, 250))
        painter.setBrush(metal)
        painter.setPen(QPen(QColor(0, 0, 0, 85), 2))
        painter.drawPath(pointer)
        painter.restore()
    # 圆环按整盘比例缩小到 62px，整体轮盘尺寸不变，中心留出更多游戏画面。
    ring_rect = QRectF(208, 208, 124, 124)
    ring = QLinearGradient(QPointF(270, 208), QPointF(270, 332))
    ring.setColorAt(0, QColor(220, 225, 225))
    ring.setColorAt(0.5, QColor(115, 126, 131))
    ring.setColorAt(1, QColor(177, 187, 190))
    painter.setBrush(ring)
    painter.setPen(QPen(QColor(225, 232, 232), 1.2))
    painter.drawEllipse(ring_rect)

    # 高光随选择方向沿圆环移动，hover 到某一项时环上直接亮出对应位置。
    reach = math.hypot(offset.x(), offset.y())
    if reach > 0.5:
        unit = offset / reach
        spot = center + unit * 58
        glow = QRadialGradient(spot, 34)
        glow.setColorAt(0.0, QColor(255, 255, 255, round(180 * min(1.0, reach / 28))))
        glow.setColorAt(1.0, QColor(255, 255, 255, 0))
        clip = QPainterPath()
        clip.addEllipse(ring_rect)
        painter.save()
        # 只让高光落在圆环带上，不越过内圈糊到中心碗里。
        painter.setClipPath(clip, Qt.ClipOperation.IntersectClip)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(glow)
        painter.drawEllipse(spot, 34, 34)
        painter.restore()
    bowl = QRadialGradient(QPointF(258, 245), 88)
    bowl.setColorAt(0, QColor(65, 75, 83))
    bowl.setColorAt(1, QColor(19, 27, 33))
    painter.setBrush(bowl)
    painter.setPen(QPen(QColor(180, 190, 192), 1.5))
    painter.drawEllipse(QRectF(219, 219, 102, 102))
    knob = center + offset
    cap = QRadialGradient(knob + QPointF(-6, -8), 23)
    cap.setColorAt(0, QColor(207, 216, 219))
    cap.setColorAt(0.36, QColor(112, 126, 137))
    cap.setColorAt(0.65, QColor(55, 67, 76))
    cap.setColorAt(1, QColor(17, 25, 32))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(0, 0, 0, 90))
    painter.drawEllipse(knob + QPointF(1, 3), 18, 18)
    painter.setBrush(cap)
    painter.drawEllipse(knob, 17, 17)
    painter.restore()


def _modern_sector(index: int, petals: bool) -> QPainterPath:
    """圆角与缝隙只改变轮廓，八向命中仍由原有轮盘交互负责。"""
    center = QPointF(270, 270)
    start, end = 112.5 - index * 45, 67.5 - index * 45
    outer_radius, inner_radius = (242, 88) if petals else (246, 80)
    outer = QRectF(270 - outer_radius, 270 - outer_radius, outer_radius * 2, outer_radius * 2)
    inner = QRectF(270 - inner_radius, 270 - inner_radius, inner_radius * 2, inner_radius * 2)

    def point(radius: float, angle: float) -> QPointF:
        radians = math.radians(angle)
        return center + QPointF(radius * math.cos(radians), -radius * math.sin(radians))

    path = QPainterPath()
    if not petals:
        path.arcMoveTo(outer, start)
        path.arcTo(outer, start, -45)
        path.lineTo(point(inner_radius, end))
        path.arcTo(inner, end, 45)
    else:
        # 径向两端分别留近似 3px 缝隙，再用二次曲线收圆四角。
        outer_gap, inner_gap = 0.7, 1.9
        outer_corner, inner_corner = 1.9, 5.2
        path.arcMoveTo(outer, start - outer_gap - outer_corner)
        path.arcTo(outer, start - outer_gap - outer_corner, -45 + 2 * (outer_gap + outer_corner))
        path.quadTo(point(outer_radius, end + outer_gap), point(outer_radius - 8, end + outer_gap))
        path.lineTo(point(inner_radius + 8, end + inner_gap))
        path.quadTo(point(inner_radius, end + inner_gap), point(inner_radius, end + inner_gap + inner_corner))
        path.arcTo(inner, end + inner_gap + inner_corner, 45 - 2 * (inner_gap + inner_corner))
        path.quadTo(point(inner_radius, start - inner_gap), point(inner_radius + 8, start - inner_gap))
        path.lineTo(point(outer_radius - 8, start - outer_gap))
        path.quadTo(point(outer_radius, start - outer_gap), point(outer_radius, start - outer_gap - outer_corner))
    path.closeSubpath()
    return path


def _paint_modern_wheel(painter: QPainter, labels: list[str], selected_index: int | None,
                        *, editing: bool, highlights: list[float] | None, petals: bool) -> None:
    """轻薄整盘与独立花瓣共用字级、中心反馈和高亮动画。"""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    center = QPointF(270, 270)
    disc = QPainterPath()
    disc.addEllipse(QRectF(24, 24, 492, 492))
    _paint_wheel_shadow(painter, disc)
    accent = QColor(GREEN_ACCENT)
    if not petals:
        # 轻薄玻璃靠微弱外缘建立整体轮廓，不沿用经典样式的厚外环。
        base = QLinearGradient(QPointF(100, 24), QPointF(430, 516))
        base.setColorAt(0, QColor(46, 48, 53, 230))
        base.setColorAt(1, QColor(28, 29, 33, 235))
        painter.setPen(QPen(QColor(255, 255, 255, 65), 1))
        painter.setBrush(base)
        painter.drawPath(disc)

    for index, label in enumerate(labels):
        selected = index == selected_index and (bool(label) or editing)
        strength = highlights[index] if highlights is not None else float(selected)
        path = _modern_sector(index, petals)
        normal = QColor(39, 40, 44)
        # 整盘保留克制的蓝色透染；花瓣用更清晰的独立蓝面交代选中。
        mix = strength * (1.0 if petals else 0.38)
        color = QColor(*(round(a + (b - a) * mix) for a, b in zip(
            (normal.red(), normal.green(), normal.blue()),
            (accent.red(), accent.green(), accent.blue()))))
        # 左上柔光穿过整盘；花瓣选中时保留原蓝，避免提亮成粉蓝色。
        face = QRadialGradient(QPointF(170, 140), 460)
        top = color.lighter(round(155 - (45 if petals else 0) * strength))
        bottom = color.darker(120)
        alpha = 240 if label or editing else 185
        top.setAlpha(alpha)
        bottom.setAlpha(alpha)
        face.setColorAt(0, top)
        face.setColorAt(1, bottom)
        edge = QColor(255, 255, 255, 45 if label else 22)
        if strength > 0:
            edge = QColor(80, 170, 255, round(45 + 130 * strength))
        painter.setPen(QPen(edge, 1.3 if petals else 0.8))
        painter.setBrush(face)
        painter.drawPath(path)
        radians = math.radians(90 - index * 45)
        text_center = center + QPointF(164 * math.cos(radians), -164 * math.sin(radians))
        font = QFont("Segoe UI")
        font.setPixelSize(17 if editing else 13)
        painter.setFont(font)
        glass.paint_lifted_text(painter, QRectF(text_center.x() - 54, text_center.y() - 28, 108, 20),
                                Qt.AlignmentFlag.AlignCenter, f"0{index + 1}",
                                QColor(TEXT_PRIMARY) if selected else QColor(TEXT_MUTED))
        font = QFont("Microsoft YaHei UI")
        font.setPixelSize(25 if editing else 19)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        text = painter.fontMetrics().elidedText(label or "—", Qt.TextElideMode.ElideRight, 124)
        glass.paint_lifted_text(painter, QRectF(text_center.x() - 64, text_center.y() - 4, 128, 34),
                                Qt.AlignmentFlag.AlignCenter, text,
                                QColor(TEXT_PRIMARY) if label or selected else QColor(TEXT_MUTED))

    # 方向弧跟随同一份动画强度，不在换槽时突然跳亮。
    painter.setBrush(Qt.BrushStyle.NoBrush)
    for index in range(len(labels)):
        strength = highlights[index] if highlights is not None else float(
            index == selected_index and (bool(labels[index]) or editing))
        if strength > 0:
            painter.setPen(QPen(tint(GREEN_ACCENT, round(255 * strength)), 4,
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawArc(QRectF(27, 27, 486, 486),
                            int((90 - index * 45 - 14) * 16), 28 * 16)

    hub = QRadialGradient(QPointF(248, 240), 110)
    hub.setColorAt(0, QColor(42, 43, 47, 250))
    hub.setColorAt(1, QColor(24, 25, 29, 250))
    painter.setBrush(hub)
    painter.setPen(QPen(QColor(255, 255, 255, 65), 1.2))
    painter.drawEllipse(QRectF(194, 194, 152, 152))
    title = labels[selected_index] if selected_index is not None and labels[selected_index] else ""
    font = QFont("Microsoft YaHei UI")
    font.setPixelSize(25 if editing else 19)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    split = 0
    while split < len(title) and metrics.horizontalAdvance(title[:split + 1]) <= 116:
        split += 1
    lines = [title] if split == len(title) else [title[:split], metrics.elidedText(
        title[split:], Qt.TextElideMode.ElideRight, 116)]
    for row, text in enumerate(lines):
        glass.paint_lifted_text(painter, QRectF(210, 230 + row * 24 if len(lines) > 1 else 243, 120, 26),
                                Qt.AlignmentFlag.AlignCenter, text, QColor(TEXT_PRIMARY))
    font.setPixelSize(17 if editing else 13)
    font.setWeight(QFont.Weight.Normal)
    painter.setFont(font)
    glass.paint_lifted_text(painter, QRectF(205, 286, 130, 23), Qt.AlignmentFlag.AlignCenter,
                            "点击编辑" if editing else "松开播放" if title else "移回取消", QColor(TEXT_MUTED))
    painter.restore()


def _paint_classic_wheel(painter: QPainter, labels: list[str], selected_index: int | None,
                          *, editing: bool = False, highlights: list[float] | None = None) -> None:
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
    _paint_wheel_shadow(painter, disc)
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
    # 底缘只交代厚度，避免与外侧投影叠成一条厚黑带。
    rim_light.setColorAt(1.0, QColor(0, 0, 0, 65))
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
        strength = highlights[index] if highlights is not None else float(selected)
        angle = 112.5 - index * 45
        path = QPainterPath()
        path.arcMoveTo(outer, angle - 1)
        path.arcTo(outer, angle - 1, -43)
        end = math.radians(angle - 44)
        path.lineTo(center + QPointF(80 * math.cos(end), -80 * math.sin(end)))
        path.arcTo(inner, angle - 44, 43)
        path.closeSubpath()
        if strength > 0:
            # 双层光晕表达选中，不额外改变扇区几何。
            for width, alpha in ((14, 22), (8, 40)):
                painter.setPen(QPen(tint(GREEN_ACCENT, round(alpha * strength)), width))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        normal = QColor(ELEVATED_BG)
        base = QColor(*(round(a + (b - a) * strength) for a, b in zip(
            (normal.red(), normal.green(), normal.blue()),
            (accent.red(), accent.green(), accent.blue()))))
        # 扇区不再是纯色片：主光在左上，中心提亮、外缘压暗，才有起鼓的曲面感。
        face = QRadialGradient(center + QPointF(-30, -40), 260)
        lit = base.lighter(122)
        lit.setAlpha(round((225 if label else 100) * (1 - strength) + 235 * strength))
        shade = base.darker(135)
        shade.setAlpha(round((200 if label else 90) * (1 - strength) + 175 * strength))
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
            # 编号不跟随蓝色选中底色，悬停时仍保持明暗对比。
            QColor(TEXT_PRIMARY) if selected or label else QColor(TEXT_MUTED),
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

    # 选中方向保留粗弧，移除无操作含义的外圈刻度。
    if selected_index is not None and (labels[selected_index] or editing):
        painter.setPen(QPen(accent, 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.drawArc(QRectF(31, 31, 478, 478),
                        int((90 - selected_index * 45 - 14) * 16), 28 * 16)
    # 外缘上亮下暗：固定光源在主光位，圆盘才有厚度而不是一圈平描边。
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(tint(TEXT_PRIMARY, 70), 2))
    painter.drawArc(QRectF(26, 26, 488, 488), 45 * 16, 90 * 16)
    painter.setPen(QPen(QColor(0, 0, 0, 55), 1))
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
    title = labels[selected_index] if selected_index is not None and has_selection else ""
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
