#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口专用 Qt 控件。

- ``_SettingsDialog``：处理原生关闭与快捷键录制事件的 QDialog
- ``NoWheelComboBox``：忽略滚轮的 QComboBox
- ``NoWheelSlider``：忽略滚轮的 QSlider
- ``CheckmarkCheckBox``：自绘勾选标记的 QCheckBox
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer
from PySide6.QtGui import (
    QBrush, QColor, QCloseEvent, QKeyEvent, QLinearGradient, QMouseEvent, QPainter,
    QPainterPath, QPaintEvent, QPen, QShowEvent,
)
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFontComboBox, QHBoxLayout, QLabel,
    QPushButton, QSlider, QWidget,
)

if TYPE_CHECKING:
    from wordy.ui.settings import SettingsWindow

from wordy.ui import glass
from wordy.ui.theme import (
    BUTTON_GHOST_BORDER, ELEVATED_BG, GREEN_ACCENT, RADIUS_WINDOW, SURFACE_BG,
    TEXT_ERROR, TEXT_PRIMARY, WINDOW_BG, tint,
)
from wordy.ui.window import set_window_cloaked

# 窗口四周的透明余量：容投影、当缩放热区，并让子控件不贴边——
# 热区必须落在没有子控件的区域，鼠标按下才会直达窗口自己。
WINDOW_EDGE_PAD = 12
# 缩放热区比余量窄，避免和余量最外圈的投影抢像素。
RESIZE_BORDER = 6
TITLE_BAR_HEIGHT = 44
# 窗口若因故一直不绘制（离屏/最小化启动），兜底解除遮蔽，不能永久藏起来。
FIRST_FRAME_GUARD_MS = 250

# 玻璃本体：顶亮底暗的极浅渐变，替代原先 QDialog QSS 里的 qlineargradient。
GLASS_BODY_TOP_ALPHA = 236
GLASS_BODY_BOTTOM_ALPHA = 246
# 分层投影：环境影铺得开而淡，接触影贴着玻璃交代贴面。
# 补上 WS_THICKFRAME 之后 DWM 自己也会画影子，这层只补贴面感，别压太重。
SHADOW_STEPS = 5
SHADOW_ALPHA = 8
SHADOW_OFFSET = 4
CONTACT_SHADOW_STEPS = 2
CONTACT_SHADOW_ALPHA = 18
CONTACT_SHADOW_OFFSET = 2

INPUT_TEXT_COLOR = GREEN_ACCENT


class _SettingsDialog(QDialog):
    """将原生关闭事件转发给 SettingsWindow，并保护快捷键录制。"""

    def __init__(self, owner: "SettingsWindow", parent: QWidget | None) -> None:
        super().__init__(parent)
        self._owner = owner
        self._application_event_filter_installed = False
        # 自绘标题栏由装配方创建后回填；在此之前为 None，尺寸派生要先判空。
        self.title_bar: _TitleBar | None = None
        self._first_frame_painted = False
        self._cloak_released = False
        self._cloak_guard: QTimer | None = None
        # 边缘缩放靠鼠标移动事件切换光标，窗口必须常开 tracking。
        self.setMouseTracking(True)
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._application_event_filter_installed = True

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # type: ignore[override]
        if self._should_swallow_recording_key_event(watched, event):
            event.accept()
            return True

        return False

    def _should_swallow_recording_key_event(self, watched: object, event: QEvent) -> bool:
        if self._owner.record_button.isEnabled():
            return False
        if event.type() not in (
            QEvent.Type.KeyPress,
            QEvent.Type.KeyRelease,
            QEvent.Type.ShortcutOverride,
        ):
            return False
        if not isinstance(event, QKeyEvent) or not isinstance(watched, QWidget):
            return False
        return watched is self or self.isAncestorOf(watched)

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._owner._handle_dialog_close():
            event.ignore()
            return
        if self._application_event_filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._application_event_filter_installed = False
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._owner.record_button.isEnabled():
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            event.accept()
            self._owner.close()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if not self._owner.record_button.isEnabled():
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ----- 无边框玻璃窗口：自绘底、投影、缩放热区、首帧遮蔽 -----

    def chrome_collapsed(self) -> bool:
        """最大化/全屏时不留圆角与余量，玻璃直接铺满工作区。"""
        return self.isMaximized() or self.isFullScreen()

    def edge_pad(self) -> int:
        """窗口四周透明余量的当前宽度；最大化时归零。"""
        return 0 if self.chrome_collapsed() else WINDOW_EDGE_PAD

    def corner_radius(self) -> float:
        """玻璃本体圆角；最大化时退化成直角。"""
        return 0.0 if self.chrome_collapsed() else float(RADIUS_WINDOW)

    def resize_edges_at(self, pos: QPoint) -> Qt.Edge | None:
        """命中窗口边缘时返回要缩放的边，角上返回两条边的组合。

        只认余量圈内的点：那一圈没有子控件，事件一定到得了窗口自己，
        不需要额外的 eventFilter 或子控件转发。
        """
        if self.chrome_collapsed():
            return None
        horizontal = (
            Qt.Edge.LeftEdge if pos.x() <= RESIZE_BORDER
            else Qt.Edge.RightEdge if pos.x() >= self.width() - RESIZE_BORDER
            else None
        )
        vertical = (
            Qt.Edge.TopEdge if pos.y() <= RESIZE_BORDER
            else Qt.Edge.BottomEdge if pos.y() >= self.height() - RESIZE_BORDER
            else None
        )
        if horizontal is None:
            return vertical
        if vertical is None:
            return horizontal
        return horizontal | vertical

    def start_system_move(self) -> bool:
        """把拖动交给 Windows 原生移动循环，贴边分屏由系统处理。"""
        handle = self.windowHandle()
        if handle is None:
            return False
        handle.startSystemMove()
        return True

    def start_system_resize(self, edges: Qt.Edge) -> bool:
        """把缩放交给 Windows 原生缩放循环。"""
        handle = self.windowHandle()
        if handle is None:
            return False
        handle.startSystemResize(edges)
        return True

    def toggle_maximized(self) -> None:
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def release_cloak(self) -> None:
        """首帧已提交，解除 DWM 遮蔽。幂等。"""
        if self._cloak_released:
            return
        self._cloak_released = True
        set_window_cloaked(self, False)

    def paintEvent(self, _event: QPaintEvent) -> None:
        self._paint_glass()
        # paintEvent 返回时这一帧已提交给平台插件；放到下一轮事件循环再解除遮蔽，
        # DWM 第一次合成拿到的就是画好的玻璃，而不是一张还没画的空窗口。
        if not self._first_frame_painted:
            self._first_frame_painted = True
            QTimer.singleShot(0, self.release_cloak)

    def _paint_glass(self) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        pad = self.edge_pad()
        if pad:
            # 环境影铺得开而淡，接触影贴着玻璃交代贴面，两层叠出浮起的厚度。
            painter.setBrush(QColor(0, 0, 0, SHADOW_ALPHA))
            for step in range(SHADOW_STEPS, CONTACT_SHADOW_STEPS, -1):
                painter.drawRoundedRect(
                    self.rect().adjusted(step, step + SHADOW_OFFSET, -step, -step + SHADOW_OFFSET),
                    RADIUS_WINDOW + step, RADIUS_WINDOW + step,
                )
            painter.setBrush(QColor(0, 0, 0, CONTACT_SHADOW_ALPHA))
            for step in range(CONTACT_SHADOW_STEPS, 0, -1):
                painter.drawRoundedRect(
                    self.rect().adjusted(step, step + CONTACT_SHADOW_OFFSET, -step, -step + CONTACT_SHADOW_OFFSET),
                    RADIUS_WINDOW + step, RADIUS_WINDOW + step,
                )

        radius = self.corner_radius()
        body = QRectF(self.rect()).adjusted(pad, pad, -pad, -pad)
        path = QPainterPath()
        path.addRoundedRect(body, radius, radius)

        # 玻璃本体：顶亮底暗的极浅渐变，透出背后但仍压得住内容。
        body_gradient = QLinearGradient(body.topLeft(), body.bottomLeft())
        body_gradient.setColorAt(0.0, tint(SURFACE_BG, GLASS_BODY_TOP_ALPHA))
        body_gradient.setColorAt(1.0, tint(WINDOW_BG, GLASS_BODY_BOTTOM_ALPHA))
        painter.setBrush(body_gradient)
        painter.drawPath(path)

        # 白色光泽：自上而下衰减，给平面一个受光方向。
        painter.save()
        painter.setClipPath(path)
        sheen = QLinearGradient(body.topLeft(), body.bottomLeft())
        sheen.setColorAt(0.0, QColor(255, 255, 255, 26))
        sheen.setColorAt(1.0, QColor(255, 255, 255, 8))
        painter.setBrush(sheen)
        painter.drawPath(path)
        painter.restore()

        glass.paint_noise(painter, path)

        # 上缘最亮的受光棱 + 内侧暗过渡：亮棱压着暗过渡才读成玻璃。
        edge = QLinearGradient(body.topLeft(), body.bottomLeft())
        edge.setColorAt(0.0, QColor(255, 255, 255, 255))
        edge.setColorAt(0.35, QColor(255, 255, 255, 118))
        edge.setColorAt(1.0, QColor(255, 255, 255, 88))
        glass.paint_glass_rim(painter, body, radius, QBrush(edge))
        painter.end()

    def showEvent(self, event: QShowEvent) -> None:
        super().showEvent(event)
        # 兜底：窗口若始终不绘制（离屏、最小化启动），也要解除遮蔽，不能永久藏起来。
        if not self._cloak_released and self._cloak_guard is None:
            guard = QTimer(self)
            guard.setSingleShot(True)
            guard.setInterval(FIRST_FRAME_GUARD_MS)
            guard.timeout.connect(self.release_cloak)
            guard.start()
            self._cloak_guard = guard

    def changeEvent(self, event: QEvent) -> None:
        if event.type() == QEvent.Type.WindowStateChange:
            # 余量与圆角都是派生值，最大化时三处（绘制/热区/布局）自动一致。
            pad = self.edge_pad()
            layout = self.layout()
            if layout is not None:
                layout.setContentsMargins(pad, pad, pad, pad)
            if self.title_bar is not None:
                self.title_bar.set_maximized(self.chrome_collapsed())
            self.update()
        super().changeEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        # 余量圈里没有子控件，能走到这里的一定是窗口边缘。
        if event.button() == Qt.MouseButton.LeftButton:
            edges = self.resize_edges_at(event.position().toPoint())
            if edges is not None:
                self.start_system_resize(edges)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        # 只在边缘切光标；这里不调 update()，鼠标划过不该触发重绘。
        edges = self.resize_edges_at(event.position().toPoint())
        self.setCursor(_cursor_for_edges(edges) if edges is not None else Qt.CursorShape.ArrowCursor)
        super().mouseMoveEvent(event)


def _cursor_for_edges(edges: Qt.Edge) -> Qt.CursorShape:
    """按命中的边给出对应的缩放光标。"""
    left = bool(edges & Qt.Edge.LeftEdge)
    right = bool(edges & Qt.Edge.RightEdge)
    top = bool(edges & Qt.Edge.TopEdge)
    bottom = bool(edges & Qt.Edge.BottomEdge)
    if (left and top) or (right and bottom):
        return Qt.CursorShape.SizeFDiagCursor
    if (right and top) or (left and bottom):
        return Qt.CursorShape.SizeBDiagCursor
    if left or right:
        return Qt.CursorShape.SizeHorCursor
    return Qt.CursorShape.SizeVerCursor


class _WindowButton(QPushButton):
    """窗口控制按钮：底色和悬停态交给 QSS，字形用矢量线画。

    字形不依赖图标字体或 Unicode 字形，和本文件 CheckmarkCheckBox 的自绘惯例一致。
    """

    SIZE = 28
    KIND_MINIMIZE = "minimize"
    KIND_MAXIMIZE = "maximize"
    KIND_CLOSE = "close"
    GLYPH_PEN_WIDTH = 1.4

    def __init__(self, kind: str, parent: QWidget) -> None:
        super().__init__(parent)
        self._kind = kind
        self._maximized = False
        self.setFixedSize(self.SIZE, self.SIZE)
        self.setCursor(Qt.CursorShape.ArrowCursor)

    def set_maximized(self, maximized: bool) -> None:
        if self._maximized != maximized:
            self._maximized = maximized
            self.update()

    def paintEvent(self, event: QPaintEvent) -> None:
        # 先让 QSS 画出底色/圆角/悬停态，再补画字形。
        super().paintEvent(event)
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        active = self.isDown() or self.underMouse()
        color = QColor("#ffffff") if active and self._kind == self.KIND_CLOSE else QColor(TEXT_PRIMARY)
        painter.setPen(QPen(
            color, self.GLYPH_PEN_WIDTH, Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
        ))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        center = QPointF(self.width() / 2, self.height() / 2)
        if self._kind == self.KIND_MINIMIZE:
            painter.drawLine(
                QPointF(center.x() - 4.5, center.y() + 4), QPointF(center.x() + 4.5, center.y() + 4),
            )
        elif self._kind == self.KIND_MAXIMIZE:
            self._paint_maximize_glyph(painter, center)
        else:
            painter.drawLine(
                QPointF(center.x() - 4, center.y() - 4), QPointF(center.x() + 4, center.y() + 4),
            )
            painter.drawLine(
                QPointF(center.x() + 4, center.y() - 4), QPointF(center.x() - 4, center.y() + 4),
            )
        painter.end()

    def _paint_maximize_glyph(self, painter: QPainter, center: QPointF) -> None:
        if not self._maximized:
            painter.drawRect(QRectF(center.x() - 4.5, center.y() - 4.5, 9, 9))
            return
        # 还原态：一实一小两个叠角方块，与系统图标语义一致。
        painter.drawRect(QRectF(center.x() - 1.5, center.y() - 4.5, 6, 6))
        painter.drawLine(QPointF(center.x() - 4.5, center.y() - 1.5), QPointF(center.x() - 4.5, center.y() + 4.5))
        painter.drawLine(QPointF(center.x() - 4.5, center.y() + 4.5), QPointF(center.x() - 1.5, center.y() + 4.5))
        painter.drawLine(QPointF(center.x() - 1.5, center.y() - 4.5), QPointF(center.x() + 4.5, center.y() - 4.5))
        painter.drawLine(QPointF(center.x() + 4.5, center.y() - 4.5), QPointF(center.x() + 4.5, center.y() + 1.5))


class _TitleBar(QWidget):
    """自绘标题栏：标题 + 三个窗口按钮，空白处按下交给系统拖动。"""

    # 与 QTabBar 的左内边距对齐，标题和页签落在同一条竖线上。
    PADDING_LEFT = 20

    def __init__(self, dialog: "_SettingsDialog") -> None:
        super().__init__(dialog)
        self._dialog = dialog
        self.setObjectName("settingsTitleBar")
        self.setFixedHeight(TITLE_BAR_HEIGHT)
        # 不继承窗口在边缘设的缩放光标。
        self.setCursor(Qt.CursorShape.ArrowCursor)
        row = QHBoxLayout(self)
        row.setContentsMargins(self.PADDING_LEFT, 0, 12, 0)
        row.setSpacing(6)
        self.title_label = QLabel(dialog.windowTitle() or "设置", self)
        self.title_label.setObjectName("dialogTitle")
        self.title_label.setAccessibleName("设置")
        # 标题不参与命中，按在文字上也要落回标题栏本体去拖动窗口。
        self.title_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        row.addWidget(self.title_label)
        row.addStretch(1)
        self.minimize_button = self._add_button(_WindowButton.KIND_MINIMIZE, "dialogMinimizeButton", "最小化", row)
        self.maximize_button = self._add_button(_WindowButton.KIND_MAXIMIZE, "dialogMaximizeButton", "最大化", row)
        self.close_button = self._add_button(_WindowButton.KIND_CLOSE, "dialogCloseButton", "关闭", row)
        self.minimize_button.clicked.connect(dialog.showMinimized)
        self.maximize_button.clicked.connect(dialog.toggle_maximized)
        # 关闭仍走既有 closeEvent，保存与 on_close 的单次派发不受影响。
        self.close_button.clicked.connect(dialog.close)

    def _add_button(self, kind: str, name: str, tip: str, row: QHBoxLayout) -> _WindowButton:
        button = _WindowButton(kind, self)
        button.setObjectName(name)
        button.setToolTip(tip)
        button.setAccessibleName(tip)
        row.addWidget(button)
        return button

    def set_maximized(self, maximized: bool) -> None:
        self.maximize_button.set_maximized(maximized)
        self.maximize_button.setToolTip("还原" if maximized else "最大化")

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dialog.start_system_move()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._dialog.toggle_maximized()
            event.accept()
            return
        super().mouseDoubleClickEvent(event)


class NoWheelComboBox(QComboBox):
    """忽略折叠状态下的鼠标滚轮，避免误切换选项并让滚动传递给设置页。"""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()


class NoWheelFontComboBox(QFontComboBox):
    """同上，但列出已安装字体族供界面字体选择。"""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()


class NoWheelSlider(QSlider):
    """忽略鼠标滚轮，避免滚动设置页时误调整数值。"""

    def wheelEvent(self, event) -> None:  # type: ignore[override]
        event.ignore()


class CheckmarkCheckBox(QCheckBox):
    """用代码绘制勾选标记的复选框，保留 QCheckBox 行为。"""

    INDICATOR_SIZE: int = 14
    LABEL_GAP: int = 8

    def sizeHint(self) -> QSize:
        size = super().sizeHint()
        font_height = self.fontMetrics().height()
        text_width = self.fontMetrics().horizontalAdvance(self.text())
        return QSize(
            max(size.width(), self.INDICATOR_SIZE + self.LABEL_GAP + text_width),
            max(size.height(), self.INDICATOR_SIZE, font_height),
        )

    def paintEvent(self, event: QPaintEvent) -> None:
        event.accept()
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        indicator_y = (self.height() - self.INDICATOR_SIZE) // 2
        indicator_rect = QRect(0, indicator_y, self.INDICATOR_SIZE, self.INDICATOR_SIZE)
        accent_color = QColor(INPUT_TEXT_COLOR)
        # 未勾选态是玻璃空槽：底色比窗口表面亮一档，描边用可见的浅灰。
        border_color = accent_color if self.isChecked() else QColor(BUTTON_GHOST_BORDER)
        background_color = accent_color if self.isChecked() else QColor(ELEVATED_BG)

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(background_color)
        painter.drawRoundedRect(indicator_rect.adjusted(0, 0, -1, -1), 4, 4)

        if self.isChecked():
            check_path = QPainterPath()
            check_path.moveTo(QPointF(3.2, indicator_y + 7.3))
            check_path.lineTo(QPointF(5.7, indicator_y + 9.8))
            check_path.lineTo(QPointF(10.9, indicator_y + 4.1))
            painter.setPen(QPen(QColor("#ffffff"), 2.2, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(check_path)

        text_rect = self.rect().adjusted(self.INDICATOR_SIZE + self.LABEL_GAP, 0, 0, 0)
        painter.setPen(QPen(QColor(TEXT_PRIMARY)))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.text())
