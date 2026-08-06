#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""RED contract tests for TrayApp and LogStream public hooks.

These tests intentionally fail because tray.TrayApp and
log_stream.LogStream do not exist yet, and because the existing
InputOverlay public hook _open_settings is not yet wired to a tray menu.
"""

from __future__ import annotations

import importlib
import logging
import os
import sys
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest.mock import MagicMock

import pytest

_ = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ = pytest.importorskip("PySide6")

from PySide6 import QtCore, QtGui, QtWidgets


_CONFIG: dict[str, Any] = {
    "hotkey": "f6",
    "name": "F6",
    "voice_id": "voice-current",
    "voice_name": "Current Voice",
    "volume": 1.0,
    "overlay_opacity": 1.0,
    "tts_backend": "cartesia-bytes",
    "fixed_center": True,
    "window_position": None,
}


def _install_native_hotkey_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("wordy.hotkey")

    class NativeHotkeyListener:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.started = False

    setattr(module, "NativeHotkeyListener", NativeHotkeyListener)
    from wordy.hotkey import display_hotkey, iter_hotkey_parts, normalize_key_part, split_hotkey
    setattr(module, "display_hotkey", display_hotkey)
    setattr(module, "iter_hotkey_parts", iter_hotkey_parts)
    setattr(module, "normalize_key_part", normalize_key_part)
    setattr(module, "split_hotkey", split_hotkey)
    monkeypatch.setitem(sys.modules, "wordy.hotkey", module)


def _import_input_overlay(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(sys, "platform", "win32")
    _install_native_hotkey_stub(monkeypatch)

    # 用 AppSettings 替代已删除的 load_initial_config / save_app_config
    from wordy.config import AppSettings
    _test_settings = AppSettings()
    _test_settings.hotkey = _CONFIG["hotkey"]
    _test_settings.name = _CONFIG["name"]
    _test_settings.voice_id = _CONFIG["voice_id"]
    _test_settings.voice_name = _CONFIG["voice_name"]
    _test_settings.volume = _CONFIG["volume"]
    _test_settings.overlay_opacity = _CONFIG["overlay_opacity"]
    _test_settings.tts_backend = _CONFIG["tts_backend"]
    _test_settings.fixed_center = _CONFIG["fixed_center"]
    monkeypatch.setattr(AppSettings, "load", lambda **kw: _test_settings)
    monkeypatch.setattr(AppSettings, "update", lambda self, **kw: Path("/tmp/wavtrans-test-config.json"))
    monkeypatch.setattr("wordy.config.get_active_config_file", lambda: Path("/tmp/wavtrans-test-config.json"))

    original_module = sys.modules.pop("wordy.ui.overlay", None)
    module = importlib.import_module("wordy.ui.overlay")

    def skip_voice_loading(_self: object, show_status: bool = True) -> None:
        _ = show_status

    monkeypatch.setattr(module.InputOverlay, "_start_load_voices", skip_voice_loading)
    if original_module is not None:
        monkeypatch.setitem(
            sys.modules, "input_overlay_original_for_contract", original_module
        )
    return module


def _import_tray_app(monkeypatch: pytest.MonkeyPatch):
    """Import tray, ensuring overlay deps are stubbed first.

    Returns the imported module or fails the test with a clear RED message.
    """
    _import_input_overlay(monkeypatch)
    sys.modules.pop("wordy.ui.tray", None)
    try:
        return importlib.import_module("wordy.ui.tray")
    except ModuleNotFoundError as exc:
        pytest.fail(
            "tray module is missing. Create wordy/ui/tray.py exposing a TrayApp "
            f"class that wires QSystemTrayIcon, a QMenu with the two Chinese "
            f"actions, and delegates settings to overlay._open_settings. "
            f"Underlying error: {exc!r}"
        )


def _import_log_stream(monkeypatch: pytest.MonkeyPatch):
    _import_input_overlay(monkeypatch)
    sys.modules.pop("wordy.log", None)
    try:
        return importlib.import_module("wordy.log")
    except ModuleNotFoundError as exc:
        pytest.fail(
            "log_stream module is missing. Create wordy/log.py exposing a "
            f"LogStream class with .write(record)/.attach(view) and a "
            f"LogWindow with a black read-only QPlainTextEdit (maxBlockCount=5000). "
            f"Underlying error: {exc!r}"
        )
# Helpers used by all tray contract tests.

def _make_tray_with_real_overlay(monkeypatch: pytest.MonkeyPatch, available: bool):
    monkeypatch.setattr(
        QtWidgets.QSystemTrayIcon,
        "isSystemTrayAvailable",
        staticmethod(lambda: available),
    )
    tray_mod = _import_tray_app(monkeypatch)
    overlay_mod = importlib.import_module("wordy.ui.overlay")
    overlay = overlay_mod.InputOverlay(on_submit=lambda _t: None)
    try:
        tray = tray_mod.TrayApp(overlay)
    except TypeError:
        try:
            tray = tray_mod.TrayApp(overlay=overlay)
        except TypeError as exc:
            pytest.fail(
                "TrayApp constructor must accept the InputOverlay instance "
                f"as the first positional or as overlay= kwarg. Got: {exc!r}"
            )
            raise
    return tray_mod, overlay, tray


# --------------------------------------------------------------------------
# T1: unavailable tray branch must skip safely (no QSystemTrayIcon created).
# --------------------------------------------------------------------------


def test_tray_unavailable_skip_branch_does_not_create_icon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=False)
    try:
        assert getattr(tray, "tray_icon", None) is None, (
            "When QSystemTrayIcon.isSystemTrayAvailable() is False, TrayApp "
            "must skip creating a tray icon and leave tray.tray_icon as None"
        )
        assert getattr(tray, "available", False) is False, (
            "TrayApp.available must be False when the system tray is unavailable"
        )
    finally:
        overlay.stop()


# --------------------------------------------------------------------------
# T2: menu has exactly three actions in order
#     ['打开设置','打开日志窗口','退出'].
# --------------------------------------------------------------------------


def test_tray_menu_has_exactly_three_actions_in_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        menu = getattr(tray, "menu", None)
        assert isinstance(menu, QtWidgets.QMenu), (
            "TrayApp must expose a QMenu instance via tray.menu when the "
            f"system tray is available; got {type(menu).__name__}"
        )
        actions = menu.actions()
        labels = [a.text() for a in actions]
        assert labels == ["打开设置", "打开日志窗口", "退出"], (
            "Tray menu must contain exactly three actions with Chinese labels "
            f"['打开设置','打开日志窗口','退出'] in that order; got {labels!r}"
        )
    finally:
        overlay.stop()


# --------------------------------------------------------------------------
# T3: settings action delegates to overlay._open_settings.
# --------------------------------------------------------------------------


def test_tray_settings_action_delegates_to_overlay_open_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        calls: list[int] = []
        original = overlay._open_settings

        def _spy() -> None:
            calls.append(1)
            # Don't actually open settings windows during the test.

        monkeypatch.setattr(overlay, "_open_settings", _spy)

        menu = tray.menu
        actions = menu.actions()
        assert actions and actions[0].text() == "打开设置"
        actions[0].trigger()
        QtWidgets.QApplication.processEvents()

        assert calls == [1], (
            "'打开设置' tray action must invoke overlay._open_settings() exactly "
            f"once; got {len(calls)} call(s)"
        )
    finally:
        overlay.stop()


# --------------------------------------------------------------------------
# T4: log action creates a black read-only QPlainTextEdit log window
#     and reuses it on the second trigger.
# --------------------------------------------------------------------------


def test_tray_log_action_creates_and_reuses_black_readonly_log_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        actions = tray.menu.actions()
        assert actions[1].text() == "打开日志窗口"
        actions[1].trigger()
        QtWidgets.QApplication.processEvents()

        window = getattr(tray, "log_window", None)
        assert window is not None, (
            "After triggering '打开日志窗口', TrayApp must store the log window "
            "on tray.log_window"
        )
        edits = window.findChildren(QtWidgets.QPlainTextEdit) if isinstance(
            window, QtWidgets.QWidget
        ) else []
        if isinstance(window, QtWidgets.QPlainTextEdit):
            text_edit = window
        else:
            assert edits, (
                "Log window must contain a QPlainTextEdit; "
                f"found children {[type(c).__name__ for c in window.children()]}"
            )
            text_edit = edits[0]

        assert text_edit.isReadOnly(), "Log QPlainTextEdit must be read-only"

        bg = text_edit.palette().color(QtGui.QPalette.ColorRole.Base)
        black = QtGui.QColor(QtCore.Qt.GlobalColor.black)
        assert bg.rgb() == black.rgb(), (
            f"Log text edit background must be black (#000000); "
            f"got {bg.name()}"
        )

        # Trigger again — same window object must be reused.
        actions[1].trigger()
        QtWidgets.QApplication.processEvents()
        assert tray.log_window is window, (
            "Second trigger of '打开日志窗口' must reuse the existing log window, "
            "not create a new one"
        )
    finally:
        overlay.stop()

# --------------------------------------------------------------------------
# T5: Pre-open log entries drain into the window when it is opened.
# --------------------------------------------------------------------------


def test_tray_pre_open_log_entries_drain_into_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        log_stream_mod = _import_log_stream(monkeypatch)
        stream = getattr(tray, "log_stream", None)
        assert stream is not None, (
            "TrayApp must own a LogStream exposed as tray.log_stream"
        )
        assert isinstance(stream, log_stream_mod.LogStream), (
            f"tray.log_stream must be a LogStream instance, "
            f"got {type(stream).__name__}"
        )

        # Emit logs BEFORE the window is opened — they must be buffered.
        stream.write("PRE-OPEN ALPHA")
        stream.write("PRE-OPEN BETA")

        actions = tray.menu.actions()
        actions[1].trigger()
        QtWidgets.QApplication.processEvents()

        window = tray.log_window
        if isinstance(window, QtWidgets.QPlainTextEdit):
            text_edit = window
        else:
            edits = window.findChildren(QtWidgets.QPlainTextEdit)
            assert edits
            text_edit = edits[0]
        content = text_edit.toPlainText()
        assert "PRE-OPEN ALPHA" in content, (
            "Log entries written before the window was opened must drain into "
            f"the QPlainTextEdit on first open; current content={content!r}"
        )
        assert "PRE-OPEN BETA" in content, (
            f"Pre-open log entry 'PRE-OPEN BETA' missing; content={content!r}"
        )
    finally:
        overlay.stop()


# --------------------------------------------------------------------------
# T6: Streaming INFO logs appear in the window via Qt event processing.
# --------------------------------------------------------------------------


def test_tray_streaming_info_logs_appear_after_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        actions = tray.menu.actions()
        actions[1].trigger()
        QtWidgets.QApplication.processEvents()
        window = tray.log_window
        text_edit = (
            window
            if isinstance(window, QtWidgets.QPlainTextEdit)
            else window.findChildren(QtWidgets.QPlainTextEdit)[0]
        )

        stream = tray.log_stream
        stream.write("STREAM INFO ALPHA")
        stream.write("STREAM INFO BETA")
        # Force Qt event delivery for queued signals/slots that LogStream
        # should use to marshal to the GUI thread.
        QtWidgets.QApplication.processEvents()
        QtWidgets.QApplication.processEvents()

        content = text_edit.toPlainText()
        assert "STREAM INFO ALPHA" in content, (
            "Streaming INFO log written after window open must appear via "
            f"Qt event processing; content={content!r}"
        )
        assert "STREAM INFO BETA" in content, (
            f"Streaming INFO log 'STREAM INFO BETA' missing; content={content!r}"
        )
    finally:
        overlay.stop()


# --------------------------------------------------------------------------
# T7: Maximum block count is capped at 5000 (ring-buffer protection).
# --------------------------------------------------------------------------


def test_tray_log_window_max_block_count_caps_at_5000(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        actions = tray.menu.actions()
        actions[1].trigger()
        QtWidgets.QApplication.processEvents()
        window = tray.log_window
        text_edit = (
            window
            if isinstance(window, QtWidgets.QPlainTextEdit)
            else window.findChildren(QtWidgets.QPlainTextEdit)[0]
        )
        assert text_edit.maximumBlockCount() == 5000, (
            "Log window QPlainTextEdit.maximumBlockCount() must cap at 5000 "
            f"to bound memory; got {text_edit.maximumBlockCount()}"
        )
    finally:
        overlay.stop()


# --------------------------------------------------------------------------
# T7b: Tray log window must surface stdlib logging output emitted through
#      a root handler installed via log_stream.install_log_stream().
#      This guards against TrayApp creating an independent LogStream that
#      is not connected to the RingBufferQtHandler pipeline used by the
#      production main.configure_logging() bootstrap.
# --------------------------------------------------------------------------


def test_tray_log_window_displays_stdlib_logging_info_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    log_stream_mod = _import_log_stream(monkeypatch)
    logger_name = "tests.tray_app_contract.stdlib_bridge"
    logger = logging.getLogger(logger_name)
    saved_handlers = list(logger.handlers)
    saved_level = logger.level
    saved_propagate = logger.propagate
    logger.handlers = []
    logger.propagate = True
    logger.setLevel(logging.INFO)

    root_logger = logging.getLogger()
    saved_root_handlers = list(root_logger.handlers)
    saved_root_level = root_logger.level
    root_logger.setLevel(logging.INFO)

    try:
        # Production wires install_log_stream() on the root logger via
        # main.configure_logging(); mirror that here so the tray log
        # window receives whatever flows through stdlib logging.
        broadcaster = log_stream_mod.install_log_stream(logger=root_logger)
        assert broadcaster is not None, (
            "install_log_stream must return a broadcaster so GUI consumers "
            "can subscribe to live log messages"
        )

        tray_mod, overlay, tray = _make_tray_with_real_overlay(
            monkeypatch, available=True
        )
        try:
            actions = tray.menu.actions()
            assert actions[1].text() == "打开日志窗口"
            actions[1].trigger()
            QtWidgets.QApplication.processEvents()

            window = tray.log_window
            assert window is not None
            text_edit = (
                window
                if isinstance(window, QtWidgets.QPlainTextEdit)
                else window.findChildren(QtWidgets.QPlainTextEdit)[0]
            )

            marker = "TRAY-STDLIB-LOG-BRIDGE-MARKER"
            logger.info(marker)
            # Allow queued Qt signal delivery to reach the slot bound to
            # the QPlainTextEdit (broadcaster.message_emitted.connect).
            QtWidgets.QApplication.processEvents()
            QtWidgets.QApplication.processEvents()

            content = text_edit.toPlainText()
            assert marker in content, (
                "Tray log window must display stdlib logging records emitted "
                "after install_log_stream() registered the root handler; "
                f"content={content!r}"
            )
            # Guard against duplicate emission caused by accidentally
            # attaching the same broadcaster slot more than once.
            assert content.count(marker) == 1, (
                "Each stdlib log record must surface exactly once in the "
                f"tray log window; got {content.count(marker)} copies of "
                f"{marker!r}: {content!r}"
            )
        finally:
            overlay.stop()
    finally:
        logger.handlers = saved_handlers
        logger.propagate = saved_propagate
        logger.setLevel(saved_level)
        # Remove any RingBufferQtHandler we installed on the root logger
        # so the test does not pollute subsequent tests.
        for handler in list(root_logger.handlers):
            if isinstance(handler, log_stream_mod.RingBufferQtHandler):
                root_logger.removeHandler(handler)
        for handler in saved_root_handlers:
            if handler not in root_logger.handlers:
                root_logger.addHandler(handler)
        root_logger.setLevel(saved_root_level)


# --------------------------------------------------------------------------
# T7d: Triggering the '退出' menu action must invoke overlay.stop() exactly
#      once. The action must also be exposed as tray.quit_action.
# --------------------------------------------------------------------------


def test_tray_quit_action_invokes_overlay_stop_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_stop = None
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        quit_action = getattr(tray, "quit_action", None)
        assert isinstance(quit_action, QtGui.QAction), (
            "TrayApp must expose the quit menu entry as tray.quit_action "
            f"(QAction); got {type(quit_action).__name__}"
        )
        assert quit_action.text() == "退出", (
            f"tray.quit_action label must be '退出'; got {quit_action.text()!r}"
        )

        real_stop = overlay.stop
        stop_spy = MagicMock()

        def stop_spy_only() -> None:
            stop_spy()

        monkeypatch.setattr(overlay, "stop", stop_spy_only)

        scheduled: list[tuple[int, object]] = []
        original_single_shot = QtCore.QTimer.singleShot

        def fake_single_shot(msec: int, slot: object) -> None:
            scheduled.append((msec, slot))

        monkeypatch.setattr(QtCore.QTimer, "singleShot", staticmethod(fake_single_shot))

        quit_action.trigger()
        QtWidgets.QApplication.processEvents()

        assert stop_spy.call_count == 0, (
            "Triggering the '退出' tray menu action must NOT call overlay.stop() "
            f"inline from the QAction slot; got {stop_spy.call_count} inline call(s). "
            "stop() must be deferred via QTimer.singleShot(0, ...)."
        )
        assert len(scheduled) == 1, (
            "Triggering '退出' must schedule overlay.stop() exactly once via "
            f"QTimer.singleShot; got {len(scheduled)} schedule(s)"
        )
        assert scheduled[0][0] == 0, (
            "QTimer.singleShot delay for deferred stop() must be 0 ms; "
            f"got {scheduled[0][0]!r}"
        )

        scheduled[0][1]()
        assert stop_spy.call_count == 1, (
            "Invoking the deferred slot must call overlay.stop() exactly once; "
            f"got {stop_spy.call_count} call(s)"
        )

        monkeypatch.setattr(QtCore.QTimer, "singleShot", staticmethod(original_single_shot))
    finally:
        if real_stop is not None:
            real_stop()


# --------------------------------------------------------------------------
# T8: dispose() hides the tray icon and disconnects safely (idempotent).
# --------------------------------------------------------------------------


def test_tray_dispose_hides_icon_and_disconnects_safely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        assert hasattr(tray, "dispose"), (
            "TrayApp must expose a dispose() method that hides the tray icon "
            "and disconnects signal handlers cleanly"
        )
        icon = getattr(tray, "tray_icon", None)
        assert isinstance(icon, QtWidgets.QSystemTrayIcon), (
            f"TrayApp.tray_icon must be a QSystemTrayIcon when available; "
            f"got {type(icon).__name__}"
        )
        # First dispose call must complete without raising.
        tray.dispose()
        QtWidgets.QApplication.processEvents()
        assert not icon.isVisible(), (
            "After dispose(), the QSystemTrayIcon must be hidden"
        )
        # Second call must be safe (idempotent) — no exception.
        tray.dispose()
    finally:
        overlay.stop()


# --------------------------------------------------------------------------
# T9 (new A): Quit triggered from tray menu must defer overlay.stop()
#             via zero-delay QTimer.singleShot, not call it inline.
# --------------------------------------------------------------------------


def test_tray_quit_action_defers_stop_via_timer_not_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    real_stop = overlay.stop
    try:
        quit_action = getattr(tray, "quit_action", None)
        assert isinstance(quit_action, QtGui.QAction)
        assert quit_action.text() == "退出"

        stop_calls: list[int] = []

        def stop_spy() -> None:
            stop_calls.append(1)

        monkeypatch.setattr(overlay, "stop", stop_spy)

        quit_action.trigger()

        assert len(stop_calls) == 0, (
            "overlay.stop() must NOT be called synchronously from the QAction "
            "callback; use QTimer.singleShot(0, ...) to defer. "
            "This prevents reentrant crash on Windows."
        )

        QtWidgets.QApplication.processEvents()
        assert len(stop_calls) == 1, (
            "After processEvents(), the deferred QTimer.singleShot(0) must "
            f"have invoked overlay.stop() exactly once; got {len(stop_calls)}"
        )

        QtWidgets.QApplication.processEvents()
        assert len(stop_calls) == 1, (
            "overlay.stop() must fire exactly once even after multiple "
            f"processEvents calls; got {len(stop_calls)}"
        )
    finally:
        real_stop()


# --------------------------------------------------------------------------
# T9 (new B): dispose() schedules deleteLater for tray_icon/menu/
#             actions/log_window, clears references to None,
#             and is idempotent.
# --------------------------------------------------------------------------


def test_tray_dispose_schedules_deletelater_and_clears_references(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        actions = tray.menu.actions()
        actions[1].trigger()
        QtWidgets.QApplication.processEvents()
        log_window = getattr(tray, "log_window", None)
        assert log_window is not None, "log_window must exist after first trigger"

        delete_later_calls: dict[str, int] = {}
        spy_targets: list[tuple[str, object]] = [
            ("tray_icon", tray.tray_icon),
            ("menu", tray.menu),
            ("settings_action", tray.settings_action),
            ("log_action", tray.log_action),
            ("quit_action", tray.quit_action),
            ("log_window", log_window),
        ]

        for name, obj in spy_targets:
            if obj is None:
                continue

            def _make_spy(n: str, target: object) -> None:
                def spy() -> None:
                    delete_later_calls[n] = delete_later_calls.get(n, 0) + 1

                monkeypatch.setattr(target, "deleteLater", spy)

            _make_spy(name, obj)

        tray.dispose()

        assert tray.tray_icon is None, "tray.tray_icon must be None after dispose"
        assert tray.menu is None, "tray.menu must be None after dispose"
        assert tray.settings_action is None, "tray.settings_action must be None after dispose"
        assert tray.log_action is None, "tray.log_action must be None after dispose"
        assert tray.quit_action is None, "tray.quit_action must be None after dispose"
        assert tray.log_window is None, "tray.log_window must be None after dispose"

        for key in ("tray_icon", "menu", "log_window"):
            assert delete_later_calls.get(key, 0) >= 1, (
                f"dispose() must call deleteLater() on {key}; "
                f"got {delete_later_calls.get(key, 0)} call(s)"
            )

        prior_counts = dict(delete_later_calls)

        tray.dispose()

        assert tray.tray_icon is None
        assert tray.menu is None
        assert tray.settings_action is None
        assert tray.log_action is None
        assert tray.quit_action is None
        assert tray.log_window is None

        for key, count in delete_later_calls.items():
            assert count == prior_counts.get(key, 0), (
                f"{key}.deleteLater() must not be called again by an "
                f"idempotent second dispose(); prior={prior_counts.get(key, 0)} "
                f"now={count}"
            )
    finally:
        overlay.stop()


# --------------------------------------------------------------------------
# T9 (old): Quit triggered from tray menu QAction callback must not call
#      processEvents() synchronously while inside the Qt event loop.
#      This prevents reentrant teardown crashes on Windows.
# --------------------------------------------------------------------------


def test_tray_quit_action_does_not_reenter_event_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tray_mod, overlay, tray = _make_tray_with_real_overlay(monkeypatch, available=True)
    try:
        assert hasattr(overlay, "_app"), "InputOverlay must hold _app reference"
        original_process_events = overlay._app.processEvents
        process_events_calls: list[int] = []

        def spy_process_events(*args: object, **kwargs: object) -> object:
            process_events_calls.append(1)
            return original_process_events(*args, **kwargs)

        monkeypatch.setattr(overlay._app, "processEvents", spy_process_events)

        quit_action = getattr(tray, "quit_action", None)
        assert isinstance(quit_action, QtGui.QAction)
        assert quit_action.text() == "退出"

        quit_action.trigger()

        assert len(process_events_calls) == 0, (
            "Quit triggered from QAction callback must NOT call "
            "processEvents() synchronously while inside the Qt event loop. "
            f"Got {len(process_events_calls)} synchronous call(s). "
            "This causes reentrant teardown crash on Windows."
        )
    finally:
        if overlay is not None and hasattr(overlay, "stop"):
            try:
                overlay.stop()
            except Exception:
                pass
