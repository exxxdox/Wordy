#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口专用 Qt 控件。

- ``_SettingsDialog``：带标题栏拖拽的自定义 QDialog
- ``NoWheelComboBox``：忽略滚轮的 QComboBox
- ``NoWheelSlider``：忽略滚轮的 QSlider
- ``CheckmarkCheckBox``：自绘勾选标记的 QCheckBox
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QColor, QCloseEvent, QKeyEvent, QMouseEvent, QPainter, QPainterPath, QPaintEvent, QPen
from PySide6.QtWidgets import QApplication, QCheckBox, QComboBox, QDialog, QSlider, QWidget

if TYPE_CHECKING:
    from easy_tts.ui.settings import SettingsWindow

from easy_tts.ui.theme import GREEN_ACCENT, SEPARATOR_COLOR, SURFACE_BG, TEXT_PRIMARY

INPUT_TEXT_COLOR = GREEN_ACCENT


class _SettingsDialog(QDialog):
    """将 Qt 原生关闭事件转发给 SettingsWindow，支持标题栏拖拽移动。"""

    def __init__(self, owner: "SettingsWindow", parent: QWidget | None) -> None:
        super().__init__(parent)
        self._owner = owner
        self._drag_active = False
        self._drag_position = QPoint()
        self._application_event_filter_installed = False
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
            self._application_event_filter_installed = True

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # type: ignore[override]
        if self._should_swallow_recording_key_event(watched, event):
            event.accept()
            return True

        object_name = getattr(watched, "objectName", None)
        set_cursor = getattr(watched, "setCursor", None)
        if not callable(object_name) or object_name() != "dialogTitle" or not isinstance(event, QMouseEvent):
            return False

        if event.type() == QEvent.Type.MouseButtonPress:
            if event.button() != Qt.MouseButton.LeftButton:
                return False
            global_pos = event.globalPosition().toPoint()
            self._drag_active = True
            self._drag_position = global_pos - self.frameGeometry().topLeft()
            if callable(set_cursor):
                set_cursor(Qt.CursorShape.ClosedHandCursor)
            return True

        if event.type() == QEvent.Type.MouseMove:
            if not self._drag_active or not event.buttons() & Qt.MouseButton.LeftButton:
                return False
            global_pos = event.globalPosition().toPoint()
            self.move(global_pos - self._drag_position)
            return True

        if event.type() == QEvent.Type.MouseButtonRelease:
            if not self._drag_active or event.button() != Qt.MouseButton.LeftButton:
                return False
            self._drag_active = False
            if callable(set_cursor):
                set_cursor(Qt.CursorShape.OpenHandCursor)
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
        if self._application_event_filter_installed:
            app = QApplication.instance()
            if app is not None:
                app.removeEventFilter(self)
            self._application_event_filter_installed = False
        self._owner._handle_dialog_close()
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


class NoWheelComboBox(QComboBox):
    """忽略折叠状态下的鼠标滚轮，避免误切换选项并让滚动传递给设置页。"""

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
        border_color = accent_color if self.isChecked() else QColor(SEPARATOR_COLOR)
        background_color = accent_color if self.isChecked() else QColor(SURFACE_BG)

        painter.setPen(QPen(border_color, 1))
        painter.setBrush(background_color)
        painter.drawRoundedRect(indicator_rect.adjusted(0, 0, -1, -1), 3, 3)

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
