#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import importlib
from typing import Protocol

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QAction,
    QBrush,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPalette,
    QPen,
    QPixmap,
)
from PySide6.QtWidgets import (
    QApplication,
    QMenu,
    QPlainTextEdit,
    QSystemTrayIcon,
    QVBoxLayout,
    QWidget,
)

from qt_lifecycle import safe_qt_call



class _OverlayLike(Protocol):
    def _open_settings(self) -> None: ...
    def stop(self) -> None: ...


class _LogStreamLike(Protocol):
    def write(self, record: str, /) -> None: ...
    def attach(self, view: object, /) -> None: ...


def _create_tts_tray_icon() -> QIcon:
    """Create a small, high-contrast text-to-speech tray icon."""
    size = 64
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    background = QPainterPath()
    background.addEllipse(QRectF(2, 2, 60, 60))
    painter.fillPath(background, QBrush(QColor("#0f5fd7")))
    painter.setPen(QPen(QColor("#9bd7ff"), 2))
    painter.drawPath(background)

    bubble = QPainterPath()
    bubble.addRoundedRect(QRectF(10, 13, 30, 30), 7, 7)
    bubble.moveTo(21, 43)
    bubble.lineTo(17, 51)
    bubble.lineTo(28, 43)
    bubble.closeSubpath()
    painter.fillPath(bubble, QBrush(QColor("#ffffff")))

    painter.setPen(QPen(QColor("#0f5fd7"), 3, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
    painter.drawLine(QPointF(17, 22), QPointF(33, 22))
    painter.drawLine(QPointF(20, 30), QPointF(30, 30))

    speaker = QPainterPath()
    speaker.moveTo(30, 36)
    speaker.lineTo(36, 36)
    speaker.lineTo(44, 29)
    speaker.lineTo(44, 51)
    speaker.lineTo(36, 44)
    speaker.lineTo(30, 44)
    speaker.closeSubpath()
    painter.fillPath(speaker, QBrush(QColor("#ffffff")))

    wave_pen = QPen(QColor("#7dd3fc"), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap)
    painter.setPen(wave_pen)
    near_wave = QPainterPath()
    near_wave.moveTo(47, 34)
    near_wave.cubicTo(51, 37, 51, 43, 47, 46)
    painter.drawPath(near_wave)
    far_wave = QPainterPath()
    far_wave.moveTo(52, 29)
    far_wave.cubicTo(59, 35, 59, 45, 52, 51)
    painter.drawPath(far_wave)

    painter.end()
    return QIcon(pixmap)


class _LogWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("日志窗口")
        self.resize(800, 500)

        self._text_edit: QPlainTextEdit = QPlainTextEdit(self)
        self._text_edit.setReadOnly(True)
        self._text_edit.setMaximumBlockCount(5000)

        palette = self._text_edit.palette()
        palette.setColor(QPalette.ColorRole.Base, QColor(Qt.GlobalColor.black))
        palette.setColor(QPalette.ColorRole.Text, QColor(Qt.GlobalColor.white))
        self._text_edit.setPalette(palette)
        self._text_edit.setStyleSheet(
            "QPlainTextEdit { background-color: #000000; color: #ffffff; }"
        )

        mono = QFont("Consolas")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        mono.setFixedPitch(True)
        self._text_edit.setFont(mono)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._text_edit)
        self.setLayout(layout)

    @property
    def text_edit(self) -> QPlainTextEdit:
        return self._text_edit

    def append_log(self, message: str) -> None:
        scrollbar = self._text_edit.verticalScrollBar()
        at_bottom = scrollbar.value() >= scrollbar.maximum() - 4
        self._text_edit.appendPlainText(message)
        if at_bottom:
            scrollbar.setValue(scrollbar.maximum())


class TrayApp:
    """System tray controller with settings + log window actions."""

    def __init__(
        self,
        overlay: _OverlayLike,
        log_stream: _LogStreamLike | None = None,
    ) -> None:
        self._overlay: _OverlayLike = overlay
        self._log_stream: _LogStreamLike | None = log_stream
        self.log_window: _LogWindow | None = None
        self._log_attached: bool = False

        self.available: bool = False
        self.tray_icon: QSystemTrayIcon | None = None
        self.menu: QMenu | None = None
        self.settings_action: QAction | None = None
        self.log_action: QAction | None = None
        self.quit_action: QAction | None = None
        self._disposed: bool = False

        overlay_prepare = getattr(overlay, "prepare_ui", None)
        if callable(overlay_prepare) and getattr(overlay, "root", None) is None:
            overlay_prepare()

        app = QApplication.instance()
        if app is None:
            return

        # Only proceed when the overlay looks like a real prepared UI owner.
        # Real InputOverlay exposes `qt_app` (matching `app`) and `root` after
        # prepare_ui(). Stubs in tests have neither, and touching Qt tray APIs
        # against an unprepared environment can segfault.
        overlay_qt_app = getattr(overlay, "qt_app", None)
        overlay_root = getattr(overlay, "root", None)
        if overlay_qt_app is not app and overlay_root is None:
            return

        self.available = bool(QSystemTrayIcon.isSystemTrayAvailable())
        if not self.available:
            return

        menu = QMenu()
        self.menu = menu

        settings_action = QAction("打开设置", menu)
        settings_action.triggered.connect(self._on_open_settings)
        menu.addAction(settings_action)
        self.settings_action = settings_action

        log_action = QAction("打开日志窗口", menu)
        log_action.triggered.connect(self._on_open_log_window)
        menu.addAction(log_action)
        self.log_action = log_action

        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self._on_quit)
        menu.addAction(quit_action)
        self.quit_action = quit_action

        tray_icon = QSystemTrayIcon(_create_tts_tray_icon())
        tray_icon.setToolTip("Wav Trans")
        tray_icon.setContextMenu(menu)
        tray_icon.show()
        self.tray_icon = tray_icon

    @property
    def log_stream(self) -> _LogStreamLike:
        stream = self._log_stream
        if stream is None:
            module = importlib.import_module("log_stream")
            shared = module.current_log_stream()
            stream = shared if shared is not None else module.LogStream()
            self._log_stream = stream
        return stream

    def _on_open_settings(self) -> None:
        self._overlay._open_settings()

    def _on_open_log_window(self) -> None:
        if self.log_window is None:
            self.log_window = _LogWindow()
        if not self._log_attached:
            self.log_stream.attach(self.log_window)
            self._log_attached = True
        self.log_window.show()
        self.log_window.raise_()
        self.log_window.activateWindow()

    def _on_quit(self) -> None:
        # Defer overlay.stop() out of the QAction slot so the Qt event loop
        # can finish dispatching this menu action before teardown begins.
        # Running stop() inline from inside a QAction signal handler causes
        # reentrant teardown crashes on Windows (exit code -1073740791).
        QTimer.singleShot(0, self._overlay.stop)

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True

        tray_icon = self.tray_icon
        menu = self.menu
        settings_action = self.settings_action
        log_action = self.log_action
        quit_action = self.quit_action
        log_window = self.log_window

        # Defensively disconnect action signals before any deleteLater()
        # so queued slot invocations cannot land on torn-down Python state.
        for action, handler in (
            (settings_action, self._on_open_settings),
            (log_action, self._on_open_log_window),
            (quit_action, self._on_quit),
        ):
            if action is None:
                continue
            safe_qt_call(lambda a=action, h=handler: a.triggered.disconnect(h))

        if tray_icon is not None:
            try:
                tray_icon.hide()
            except RuntimeError:
                pass
            safe_qt_call(lambda: tray_icon.setContextMenu(None))
            try:
                tray_icon.deleteLater()
            except RuntimeError:
                pass

        if log_window is not None:
            if self._log_attached:
                broadcaster = getattr(self.log_stream, "broadcaster", None)
                signal = (
                    getattr(broadcaster, "message_emitted", None)
                    if broadcaster is not None
                    else None
                )
                if signal is not None:
                    safe_qt_call(lambda: signal.disconnect(log_window.append_log))
            try:
                log_window.close()
            except RuntimeError:
                pass
            try:
                log_window.deleteLater()
            except RuntimeError:
                pass

        if menu is not None:
            try:
                menu.deleteLater()
            except RuntimeError:
                pass

        for action in (settings_action, log_action, quit_action):
            if action is None:
                continue
            try:
                action.deleteLater()
            except RuntimeError:
                pass

        self.tray_icon = None
        self.menu = None
        self.settings_action = None
        self.log_action = None
        self.quit_action = None
        self.log_window = None
        self._log_attached = False


class TrayController:
    """Lifecycle wrapper around :class:`TrayApp`.

    Accepts the underlying tray app plus an optional overlay reference so
    higher-level wiring can delegate disposal without owning Qt internals.
    ``dispose()`` is idempotent and forwards exactly once to the tray app.
    """

    def __init__(self, tray_app: TrayApp, overlay: object | None = None) -> None:
        self._tray_app: TrayApp = tray_app
        self._overlay: object | None = overlay
        self._disposed: bool = False

    @property
    def tray_app(self) -> TrayApp:
        return self._tray_app

    @property
    def overlay(self) -> object | None:
        return self._overlay

    def dispose(self) -> None:
        if self._disposed:
            return
        self._disposed = True
        self._tray_app.dispose()


__all__ = ["TrayApp", "TrayController"]
