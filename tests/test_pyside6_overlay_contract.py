#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PySide6 contract tests for InputOverlay behavior."""

from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from types import ModuleType
from collections.abc import Callable
from typing import Any

import pytest


_ = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ = pytest.importorskip("PySide6")


_CONFIG = {
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
    """为 wordy.hotkey 包安装轻量 stub，保留 config.py 等模块需要的其它导出。"""
    module = ModuleType("wordy.hotkey")

    class NativeHotkeyListener:
        started: bool

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.started = False

    # 保留 hotkey 包的其它公开 API，避免 config.py 等模块 import 失败
    setattr(module, "NativeHotkeyListener", NativeHotkeyListener)
    from wordy.hotkey import (
        display_hotkey,
        iter_hotkey_parts,
        normalize_key_part,
        split_hotkey,
    )
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
    _test_settings.cartesia_voice_id = _CONFIG["voice_id"]
    _test_settings.cartesia_voice_name = _CONFIG["voice_name"]
    _test_settings.volume = _CONFIG["volume"]
    _test_settings.overlay_opacity = _CONFIG["overlay_opacity"]
    _test_settings.cartesia_tts_backend = _CONFIG["tts_backend"]
    _test_settings.fixed_center = _CONFIG["fixed_center"]
    monkeypatch.setattr(AppSettings, "load", lambda **kw: _test_settings)
    monkeypatch.setattr(AppSettings, "update", lambda self, **kw: Path("/tmp/wavtrans-test-config.json"))
    original_module = sys.modules.pop("wordy.ui.overlay", None)
    module = importlib.import_module("wordy.ui.overlay")
    def skip_voice_loading(_self: object, show_status: bool = True) -> None:
        _ = show_status

    monkeypatch.setattr(module.InputOverlay, "_start_load_voices", skip_voice_loading)
    if original_module is not None:
        monkeypatch.setitem(sys.modules, "input_overlay_original_for_contract", original_module)
    return module


def _new_overlay(monkeypatch: pytest.MonkeyPatch, submitted: list[str]):
    module = _import_input_overlay(monkeypatch)
    try:
        overlay: Any = module.InputOverlay(on_submit=submitted.append)
        overlay._ensure_ui()
    except Exception as exc:  # pragma: no cover - environment-specific Qt failures
        pytest.skip(f"InputOverlay cannot be instantiated on this platform: {exc}")
        raise
    if overlay.root is None or overlay.entry is None:
        pytest.skip("InputOverlay did not create Qt widgets in this environment")
    return overlay


def test_prepare_ui_is_idempotent_and_exposes_reused_qapplication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_input_overlay(monkeypatch)
    overlay: Any = module.InputOverlay(on_submit=lambda _text: None)
    try:
        overlay.prepare_ui()
        first_app = overlay.qt_app
        first_root = overlay.root
        first_entry = overlay.entry

        overlay.prepare_ui()

        assert overlay.qt_app is first_app
        assert overlay.qt_app is module.QApplication.instance()
        assert overlay.root is first_root
        assert overlay.entry is first_entry
    finally:
        overlay.stop()


def test_prepare_ui_owned_qapplication_disables_quit_on_last_window_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_input_overlay(monkeypatch)
    overlay: Any = module.InputOverlay(on_submit=lambda _text: None)
    try:
        overlay.prepare_ui()

        assert overlay.qt_app is module.QApplication.instance()
        assert overlay.qt_app.quitOnLastWindowClosed() is False
    finally:
        overlay.stop()


def test_prepare_ui_reuses_existing_qapplication_without_changing_quit_preference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _import_input_overlay(monkeypatch)
    existing_app = module.QApplication.instance() or module.QApplication([])
    existing_app.setQuitOnLastWindowClosed(True)
    overlay: Any = module.InputOverlay(on_submit=lambda _text: None)
    try:
        overlay.prepare_ui()

        assert overlay.qt_app is existing_app
        assert existing_app.quitOnLastWindowClosed() is True
    finally:
        overlay.stop()


def test_stop_invokes_pre_stop_hook_once_before_root_is_destroyed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)
    calls: list[bool] = []

    def pre_stop_hook() -> None:
        assert overlay.root is not None
        calls.append(overlay.root.isVisible())

    overlay.set_pre_stop_hook(pre_stop_hook)

    overlay.stop()
    overlay.stop()

    assert len(calls) == 1


def test_overlay_return_normalizes_calls_callback_once_and_hides(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)
    try:
        overlay.root.show()
        overlay.entry.setText("  hello contract  ")

        overlay._on_return()

        assert submitted == ["hello contract"]
        assert not overlay.root.isVisible()
        assert overlay.entry.text() == "  hello contract  "
    finally:
        overlay.stop()


def test_overlay_whitespace_submit_does_not_call_callback_or_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)
    try:
        overlay.root.show()
        overlay.entry.setText(" \n\t ")

        overlay._on_return()

        assert submitted == []
        assert overlay.root.isVisible()
    finally:
        overlay.stop()


def test_overlay_escape_closed_settings_can_reopen_new_visible_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)
    try:
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QApplication

        overlay._open_settings()
        first = overlay._settings_window
        assert first is not None
        first.window.show()
        QApplication.processEvents()
        assert first.window.isVisible() is True
        assert first.record_button.isEnabled() is True

        first.window.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        QApplication.processEvents()

        assert overlay._settings_window is None

        overlay._open_settings()
        second = overlay._settings_window
        assert second is not None
        assert second is not first
        assert second.window.isVisible() is True
    finally:
        overlay.stop()


# ---------------------------------------------------------------------------
# RED contract tests for audio output device wiring (S2).
# ---------------------------------------------------------------------------


class _FakeAudioPlayer:
    """Minimal AudioPlayer stand-in for verifying overlay enumeration wiring.

    ``list_output_devices`` is parameterized so individual tests can return
    either legacy bare-name strings (back-compat path) or the structured
    ``{"name": ..., "host_api_name": ...}`` dicts that production now emits.
    """

    def __init__(self, devices: list[Any]) -> None:
        self._devices = list(devices)
        self.calls = 0
        self.set_output_device_name_calls: list[str | None] = []
        self.set_output_device_calls: list[Any] = []

    def list_output_devices(self) -> list[Any]:
        self.calls += 1
        return list(self._devices)

    def set_output_device_name(self, name: str | None) -> None:
        self.set_output_device_name_calls.append(name)

    def set_output_device(self, device: Any) -> None:
        self.set_output_device_calls.append(device)


def _new_overlay_with_audio_player(
    monkeypatch: pytest.MonkeyPatch,
    audio_player: _FakeAudioPlayer,
    on_audio_output_change: Any,
):
    module = _import_input_overlay(monkeypatch)
    return module.InputOverlay(
        on_submit=lambda _text: None,
        audio_player=audio_player,
        on_audio_output_change=on_audio_output_change,
    )


def test_overlay_settings_state_seeds_output_devices_from_list_helper(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output devices load only when the audio page requests them."""
    audio_player = _FakeAudioPlayer(["Speakers (Realtek)", "VB-Audio Virtual Cable"])
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=lambda _name: None,
    )
    captured_states: list[Any] = []

    class _FakeSettingsWindow:
        def __init__(self, _root: object, state: object, **_kwargs: object) -> None:
            captured_states.append(state)
            self.request_audio = _kwargs["on_audio_outputs_needed"]

        def set_audio_output_devices(self, devices: object, error: object) -> None:
            captured_states[-1].audio_output_devices = devices
            captured_states[-1].audio_output_devices_error = error

        def exists(self) -> bool:
            return True

        def lift_and_focus(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr("wordy.ui.overlay.SettingsWindow", _FakeSettingsWindow)

    try:
        overlay._open_settings()

        assert audio_player.calls == 0, "Opening the local page must not scan output hardware"
        overlay._settings_window.request_audio()
        assert audio_player.calls == 1
        assert captured_states, "SettingsState must be constructed when opening settings"
        state = captured_states[-1]
        assert state.audio_output_devices == [
            "Speakers (Realtek)",
            "VB-Audio Virtual Cable",
        ], f"SettingsState audio_output_devices must come from list helper, got {state!r}"
        # audio_output_device_name 已迁移到 AppSettings，SettingsState 不再携带
        assert hasattr(state, "input_devices"), (
            "SettingsState must receive input_devices field"
        )
    finally:
        overlay.stop()


# ---------------------------------------------------------------------------
# Contract test for STRUCTURED audio output device identity (S3 follow-up).
#
# The structured dict ``{"name": str, "host_api_name": str | None}`` must survive
# enumeration: identical names under different host APIs are not collapsed.
# Persisting and clearing the structured value now go through the live
# ``on_field_changed`` instant-apply path, covered in test_pyside6_settings_contract.
# ---------------------------------------------------------------------------


_STRUCTURED_DEVICES: list[dict[str, Any]] = [
    {"name": "Speakers (Realtek)", "host_api_name": "MME"},
    {"name": "Speakers (Realtek)", "host_api_name": "WASAPI"},
    {"name": "VB-Audio Virtual Cable", "host_api_name": "WASAPI"},
]


def test_overlay_enumerate_audio_output_devices_preserves_structured_dicts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_enumerate_audio_output_devices must return structured dicts, not bare names.

    Production currently flattens dicts to ``list[str]`` of just the ``name`` field,
    which collapses host-API duplicates. The new contract: the helper must preserve
    each dict (with both ``name`` and ``host_api_name``) so the settings UI can
    disambiguate identical names across host APIs.
    """
    audio_player = _FakeAudioPlayer(list(_STRUCTURED_DEVICES))
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=lambda _device: None,
    )
    try:
        devices, error = overlay._enumerate_audio_output_devices()

        assert error is None, f"Enumeration must not surface an error, got {error!r}"
        assert isinstance(devices, list), f"Result must be a list, got {type(devices).__name__}"
        assert len(devices) == len(_STRUCTURED_DEVICES), (
            f"Enumeration must preserve every structured entry (no dedup/flatten), "
            f"got {devices!r}"
        )
        for entry in devices:
            assert isinstance(entry, dict), (
                f"Each enumerated device must remain a structured dict, got {entry!r}"
            )
            assert "name" in entry and isinstance(entry["name"], str), (
                f"Each enumerated entry must carry a string 'name', got {entry!r}"
            )
            assert "host_api_name" in entry, (
                f"Each enumerated entry must carry a 'host_api_name' key, got {entry!r}"
            )
        # Both Realtek host-API variants must survive (no name-based dedup).
        realtek_entries = [d for d in devices if d.get("name") == "Speakers (Realtek)"]
        assert len(realtek_entries) == 2, (
            f"Identical names with different host APIs must not be collapsed, got {devices!r}"
        )
        host_apis = sorted(d.get("host_api_name") for d in realtek_entries)
        assert host_apis == ["MME", "WASAPI"], (
            f"Both MME and WASAPI variants must be preserved, got {host_apis!r}"
        )
    finally:
        overlay.stop()


# ---------------------------------------------------------------------------
# Contract tests for InputOverlay.stop() deferred shutdown behavior.
#
# stop() must not call app.processEvents() synchronously or destroy
# root/quit the app inline. Destructive teardown is deferred with
# QTimer.singleShot(0, ...) so the current Qt callback stack can unwind
# before windows and tray-owned objects are torn down on Windows.
# ---------------------------------------------------------------------------


def test_stop_does_not_call_process_events_synchronously(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop() MUST NOT call qt_app.processEvents() synchronously during the stop call.

    Synchronous processEvents() during shutdown causes reentrant event processing
    that can trigger use-after-free during Qt teardown on Windows.
    """
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)
    process_events_called = 0

    original_process = overlay.qt_app.processEvents

    def tracked_processEvents(*args: Any, **kwargs: Any) -> Any:
        nonlocal process_events_called
        process_events_called += 1
        return original_process(*args, **kwargs)

    monkeypatch.setattr(overlay.qt_app, "processEvents", tracked_processEvents)

    overlay.stop()

    assert process_events_called == 0, (
        "stop() must NOT call QApplication.processEvents() synchronously during shutdown. "
        f"Got {process_events_called} inline calls - this causes reentrant teardown crashes."
    )


def test_stop_defers_root_destruction_via_singleshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop() MUST NOT destroy root synchronously; root destruction must be deferred via QTimer.singleShot(0).

    Synchronous destruction during stop() allows reentrant events to access destroyed
    widgets while the stack is still unwinding, causing heap corruption on Windows.
    """
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)
    assert overlay.root is not None, "overlay must have created root"

    singleshot_calls: list[tuple[int, Callable[..., object]]] = []
    from PySide6.QtCore import QTimer

    def tracked_singleShot(msec: int, *args: object) -> None:
        callback = args[-1]
        if isinstance(callback, Callable):
            singleshot_calls.append((msec, callback))

    monkeypatch.setattr(QTimer, "singleShot", tracked_singleShot)
    monkeypatch.setattr(overlay.qt_app.thread(), "loopLevel", lambda: 1)
    overlay._event_loop_started = True

    pre_stop_root = overlay.root

    overlay.stop()

    assert overlay.root is pre_stop_root, (
        "stop() must defer root destruction to QTimer.singleShot(0). "
        "Current implementation destroys root synchronously inline - this causes reentrant corruption."
    )

    assert any(msec == 0 for (msec, _) in singleshot_calls), (
        "stop() must schedule deferred destruction via QTimer.singleShot(0). "
        "No zero-delay singleShot callback found - destruction must not be inline."
    )
    for _, callback in singleshot_calls:
        callback()


def test_stop_is_idempotent_under_reentry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Multiple stop() calls during reentry must be idempotent and not crash.

    When deferred shutdown runs, multiple entries into stop() must not cause
    double-free or attribute errors from accessing already-destroyed widgets.
    """
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)
    assert overlay.root is not None

    stop_call_count = 0

    def reentrant_stop():
        nonlocal stop_call_count
        stop_call_count += 1
        overlay.stop()

    overlay.set_pre_stop_hook(reentrant_stop)

    overlay.stop()
    overlay.stop()

    assert stop_call_count >= 1, "reentrant stop must execute without crash"


def test_stop_schedules_singleshot_even_without_loop_level(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """stop() must defer via QTimer.singleShot(0) without depending on QThread.loopLevel.

    PySide6 does not reliably expose ``QThread.loopLevel``. The previous
    implementation gated deferral on a callable ``loopLevel`` returning > 0,
    which never deferred under PySide6 and left parentless Qt helpers
    (such as ``_OverlaySignals``) surviving past ``app.quit()`` —
    the root cause of the Windows -1073740791 crash on tray quit.
    """
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)
    pre_stop_root = overlay.root
    assert pre_stop_root is not None
    overlay._event_loop_started = True

    singleshot_calls: list[tuple[int, Callable[..., object]]] = []
    from PySide6.QtCore import QTimer

    def tracked_singleShot(msec: int, *args: object) -> None:
        callback = args[-1]
        if isinstance(callback, Callable):
            singleshot_calls.append((msec, callback))

    monkeypatch.setattr(QTimer, "singleShot", tracked_singleShot)

    thread = overlay.qt_app.thread()
    if hasattr(thread, "loopLevel"):
        monkeypatch.delattr(thread, "loopLevel", raising=False)

    overlay.stop()

    assert overlay.root is pre_stop_root, (
        "stop() must defer root destruction even when QThread.loopLevel is unavailable"
    )
    assert any(msec == 0 for (msec, _) in singleshot_calls), (
        "stop() must schedule deferred destruction via QTimer.singleShot(0) "
        "independent of QThread.loopLevel"
    )

    for _, callback in singleshot_calls:
        callback()


def test_stop_finalize_detaches_event_filters_and_releases_signals(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_finalize must remove event filters and clear _signals so parentless QObjects do not survive app.quit()."""
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)
    entry = overlay.entry
    settings_icon = overlay.settings_icon
    signals = overlay._signals
    assert entry is not None and settings_icon is not None and signals is not None

    remove_calls: list[object] = []
    original_entry_remove = entry.removeEventFilter
    original_icon_remove = settings_icon.removeEventFilter

    def tracked_entry_remove(obj: object) -> None:
        remove_calls.append(("entry", obj))
        return original_entry_remove(obj)

    def tracked_icon_remove(obj: object) -> None:
        remove_calls.append(("icon", obj))
        return original_icon_remove(obj)

    monkeypatch.setattr(entry, "removeEventFilter", tracked_entry_remove)
    monkeypatch.setattr(settings_icon, "removeEventFilter", tracked_icon_remove)

    from PySide6.QtCore import QTimer

    scheduled: list[Callable[..., object]] = []

    def tracked_singleShot(msec: int, *args: object) -> None:
        callback = args[-1]
        if isinstance(callback, Callable):
            scheduled.append(callback)

    monkeypatch.setattr(QTimer, "singleShot", tracked_singleShot)

    overlay.stop()
    for callback in scheduled:
        callback()

    assert ("entry", signals) in remove_calls, (
        f"_finalize must remove the _OverlaySignals filter from entry, got {remove_calls!r}"
    )
    assert ("icon", signals) in remove_calls, (
        f"_finalize must remove the _OverlaySignals filter from settings_icon, got {remove_calls!r}"
    )
    assert overlay._signals is None, (
        "_finalize must release the parentless _OverlaySignals reference"
    )


def test_stop_finalize_invokes_log_stream_shutdown_before_app_quit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_finalize must call log_stream.shutdown_log_stream() so background threads cannot emit into a destroyed broadcaster after app.quit()."""
    submitted: list[str] = []
    overlay = _new_overlay(monkeypatch, submitted)

    import wordy.log

    shutdown_calls: list[object] = []
    quit_calls: list[None] = []

    def fake_shutdown(logger: object = None) -> None:
        shutdown_calls.append(logger)
        if overlay._owns_app and overlay._app is not None:
            assert not quit_calls, (
                "log_stream.shutdown_log_stream must run BEFORE QApplication.quit"
            )

    monkeypatch.setattr(wordy.log, "shutdown_log_stream", fake_shutdown)
    if overlay._app is not None:
        original_quit = overlay._app.quit

        def tracked_quit() -> None:
            quit_calls.append(None)
            original_quit()

        monkeypatch.setattr(overlay._app, "quit", tracked_quit)

    from PySide6.QtCore import QTimer

    scheduled: list[Callable[..., object]] = []

    def tracked_singleShot(msec: int, *args: object) -> None:
        callback = args[-1]
        if isinstance(callback, Callable):
            scheduled.append(callback)

    monkeypatch.setattr(QTimer, "singleShot", tracked_singleShot)

    overlay.stop()
    for callback in scheduled:
        callback()

    assert shutdown_calls, (
        "_finalize must call log_stream.shutdown_log_stream() during overlay shutdown"
    )
