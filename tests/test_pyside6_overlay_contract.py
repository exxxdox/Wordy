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
from unittest.mock import MagicMock

import pytest

import wordy.config

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
        iter_hotkey_parts,
        normalize_key_part,
        split_hotkey,
    )
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


def test_overlay_submit_normalizes_calls_callback_once_and_hides(monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert not overlay.root.isVisible()
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
    """InputOverlay must call audio_player.list_output_devices() and seed SettingsState."""
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

        def exists(self) -> bool:
            return True

        def lift_and_focus(self) -> None:
            pass

        def close(self) -> None:
            pass

    monkeypatch.setattr("wordy.ui.overlay.SettingsWindow", _FakeSettingsWindow)

    try:
        overlay._open_settings()

        assert audio_player.calls >= 1, (
            "InputOverlay must call audio_player.list_output_devices() when opening settings"
        )
        assert captured_states, "SettingsState must be constructed when opening settings"
        state = captured_states[-1]
        assert state.audio_output_devices == [
            "Speakers (Realtek)",
            "VB-Audio Virtual Cable",
        ], f"SettingsState audio_output_devices must come from list helper, got {state!r}"
        assert hasattr(state, "audio_output_device_name"), (
            "SettingsState must receive audio_output_device_name kwarg"
        )
    finally:
        overlay.stop()


def test_overlay_apply_persists_audio_output_device_name_and_invokes_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applying pending settings with a non-None device must save + invoke callback."""
    audio_player = _FakeAudioPlayer(["VB-Audio Virtual Cable"])
    callback_calls: list[str | None] = []
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=callback_calls.append,
    )

    saved_updates: list[dict] = []

    def fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device_name = "VB-Audio Virtual Cable"

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert callback_calls == ["VB-Audio Virtual Cable"], (
            f"on_audio_output_change must be invoked with selected device, got {callback_calls!r}"
        )
        merged_updates: dict = {}
        for update in saved_updates:
            merged_updates.update(update)
        assert merged_updates.get("audio_output_device_name") == "VB-Audio Virtual Cable", (
            f"save_app_config must persist audio_output_device_name, got {saved_updates!r}"
        )
    finally:
        overlay.stop()


def test_overlay_apply_clears_audio_output_device_name_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applying with audio_output_device_name=None must persist None and invoke callback(None)."""
    audio_player = _FakeAudioPlayer(["Speakers (Realtek)"])
    callback_calls: list[str | None] = []
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=callback_calls.append,
    )
    overlay._audio_output_device_name = "VB-Audio Virtual Cable"

    saved_updates: list[dict] = []

    def fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device_name = None

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert callback_calls == [None], (
            f"on_audio_output_change must be invoked with None to clear device, got {callback_calls!r}"
        )
        merged_updates: dict = {}
        for update in saved_updates:
            merged_updates.update(update)
        assert "audio_output_device_name" in merged_updates, (
            f"save_app_config must include audio_output_device_name key, got {saved_updates!r}"
        )
        assert merged_updates["audio_output_device_name"] is None, (
            f"audio_output_device_name must persist as None, got {merged_updates!r}"
        )
    finally:
        overlay.stop()


def test_overlay_apply_success_uses_inline_status_without_information_popup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Successful apply must update the settings window inline and avoid modal info popups."""
    audio_player = _FakeAudioPlayer(["Speakers (Realtek)"])
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=lambda _device: None,
    )

    saved_updates: list[dict] = []

    def fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", fake_update)
    message_box = MagicMock()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", message_box)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = 0.75
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device_name = getattr(overlay, "_audio_output_device_name", None)

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
            self.clear_status_calls = 0

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def clear_apply_status(self) -> None:
            self.clear_status_calls += 1

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()

    try:
        overlay._apply_pending_settings(fake_window)

        assert saved_updates, "successful apply must still persist changed settings"
        message_box.information.assert_not_called()
        assert fake_window.status_calls, (
            "successful apply must report completion through SettingsWindow.set_apply_status"
        )
    finally:
        overlay.stop()


# ---------------------------------------------------------------------------
# RED contract tests for STRUCTURED audio output device identity (S3 follow-up).
#
# New requirement: the structured dict ``{"name": str, "host_api_name": str | None}``
# (or ``None``) flows end-to-end through the overlay: enumeration preserves dicts,
# apply persists the structured value, and clearing writes ``None``. Legacy bare
# name strings are no longer the contract surface for the UI/config callback.
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


def test_overlay_apply_persists_structured_audio_output_device_and_invokes_callback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applying pending settings with a structured device must save it and notify with structured identity.

    Contract:
      * ``save_app_config`` is invoked with an ``audio_output_device`` key whose
        value is the structured dict ``{"name": ..., "host_api_name": ...}``.
      * ``on_audio_output_change`` is invoked exactly once with that same dict
        (not the legacy bare name string).
    """
    structured_device = {"name": "Speakers (Realtek)", "host_api_name": "WASAPI"}
    audio_player = _FakeAudioPlayer([structured_device])
    callback_calls: list[Any] = []
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=callback_calls.append,
    )

    saved_updates: list[dict] = []

    def fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        # New structured field; legacy bare-name field kept for back-compat surface.
        audio_output_device = {"name": "Speakers (Realtek)", "host_api_name": "WASAPI"}
        audio_output_device_name = "Speakers (Realtek)"

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert len(callback_calls) == 1, (
            f"on_audio_output_change must fire exactly once, got {callback_calls!r}"
        )
        invoked_with = callback_calls[0]
        assert isinstance(invoked_with, dict), (
            f"on_audio_output_change must be invoked with a structured dict, "
            f"got {type(invoked_with).__name__}: {invoked_with!r}"
        )
        assert invoked_with == {"name": "Speakers (Realtek)", "host_api_name": "WASAPI"}, (
            f"Callback must receive full structured identity (name + host_api_name), "
            f"got {invoked_with!r}"
        )

        merged_updates: dict = {}
        for update in saved_updates:
            merged_updates.update(update)
        assert "audio_output_device" in merged_updates, (
            f"save_app_config must persist 'audio_output_device' key, got {saved_updates!r}"
        )
        saved_structured = merged_updates["audio_output_device"]
        assert isinstance(saved_structured, dict), (
            f"Saved 'audio_output_device' must be a structured dict, got {saved_structured!r}"
        )
        assert saved_structured == {
            "name": "Speakers (Realtek)",
            "host_api_name": "WASAPI",
        }, (
            f"Saved structured value must match selected device, got {saved_structured!r}"
        )
    finally:
        overlay.stop()


def test_overlay_apply_clears_structured_audio_output_device_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Applying with audio_output_device=None must persist None and invoke callback(None).

    Contract: clearing selection writes ``audio_output_device: None`` via save_app_config
    and the on_audio_output_change callback is invoked with ``None`` (not an empty dict
    and not a stale legacy string).
    """
    structured_device = {"name": "Speakers (Realtek)", "host_api_name": "MME"}
    audio_player = _FakeAudioPlayer([structured_device])
    callback_calls: list[Any] = []
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=callback_calls.append,
    )
    # Seed the overlay's currently-selected device so clearing is a real transition.
    overlay._audio_output_device = {"name": "VB-Audio Virtual Cable", "host_api_name": "WASAPI"}
    # Keep legacy mirror in sync to avoid spurious "no change" short-circuits.
    overlay._audio_output_device_name = "VB-Audio Virtual Cable"

    saved_updates: list[dict] = []

    def fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device = None
        audio_output_device_name = None

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert callback_calls == [None], (
            f"on_audio_output_change must be invoked with None to clear device, "
            f"got {callback_calls!r}"
        )

        merged_updates: dict = {}
        for update in saved_updates:
            merged_updates.update(update)
        assert "audio_output_device" in merged_updates, (
            f"save_app_config must include 'audio_output_device' key when clearing, "
            f"got {saved_updates!r}"
        )
        assert merged_updates["audio_output_device"] is None, (
            f"'audio_output_device' must persist as None when cleared, got {merged_updates!r}"
        )
    finally:
        overlay.stop()


# ---------------------------------------------------------------------------
# RED contract tests for Cartesia API key secret-store apply flow (S1/S2).
#
# These tests drive the _apply_pending_settings path with pending
# cartesia_api_key_action and cartesia_api_key_value fields that
# the production _apply_pending_settings does NOT yet handle.  They
# fail (RED) with clean assertion messages rather than ImportError or
# AttributeError. Once production implements the branching, all
# assertions below should transition to GREEN without modification.
# ---------------------------------------------------------------------------


def test_overlay_apply_saves_cartesia_api_key_when_action_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S1: action='set' must save via secret_store exactly once, persist
    no raw key via save_app_config, and surface a non-leaky inline status."""
    audio_player = _FakeAudioPlayer([])
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=lambda _device: None,
    )

    import wordy.secret as secret_store_mod

    save_calls: list[tuple[str, bool]] = []

    def _fake_save_cartesia(
        value: str, allow_plaintext_fallback: bool = False
    ) -> secret_store_mod.StorageStatus:
        save_calls.append((value, allow_plaintext_fallback))
        return secret_store_mod.StorageStatus(
            backend=secret_store_mod.STORAGE_KEYRING,
            has_key=True,
            keyring_available=True,
            fallback_active=False,
        )

    monkeypatch.setattr(secret_store_mod, "save_cartesia_api_key", _fake_save_cartesia)

    saved_updates: list[dict] = []

    def _fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", _fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device_name = getattr(overlay, "_audio_output_device_name", None)
        cartesia_api_key_action = "set"
        cartesia_api_key_value = "sk_test_NEW"

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert len(save_calls) == 1, (
            f"secret_store.save_cartesia_api_key must be called exactly once, "
            f"got {len(save_calls)} calls"
        )
        called_value, called_fallback = save_calls[0]
        assert called_value == "sk_test_NEW", (
            f"save_cartesia_api_key must receive pending key value, got {called_value!r}"
        )
        assert called_fallback is False, (
            f"save_cartesia_api_key must be called with allow_plaintext_fallback=False "
            f"for the UI-apply flow, got {called_fallback!r}"
        )

        merged: dict = {}
        for u in saved_updates:
            merged.update(u)
        for leak_key in ("cartesia_api_key", "cartesia_api_key_value"):
            assert leak_key not in merged, (
                f"save_app_config must not persist {leak_key!r}, "
                f"got keys {list(merged.keys())!r}"
            )

        assert fake_window.status_calls, (
            "set_apply_status must be called to report success"
        )
        status_text = " ".join(
            str(a) for args, _ in fake_window.status_calls for a in args
        )
        assert "sk_test_NEW" not in status_text, (
            "Inline status must not leak the API key value"
        )
        assert ("已保存" in status_text) or ("keyring" in status_text.lower()), (
            "Inline status must mention the key was saved/keyring-stored; "
            f"got status_text={status_text!r}"
        )
    finally:
        overlay.stop()


def test_overlay_apply_does_not_touch_secret_store_when_action_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2: action='unchanged' must NOT call save or delete on secret_store."""
    audio_player = _FakeAudioPlayer([])
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=lambda _device: None,
    )

    import wordy.secret as secret_store_mod

    save_calls: list[tuple] = []
    delete_calls: list[tuple] = []

    monkeypatch.setattr(
        secret_store_mod, "save_cartesia_api_key",
        lambda *a, **kw: save_calls.append((a, kw))
        or secret_store_mod.StorageStatus(
            backend=secret_store_mod.STORAGE_KEYRING, has_key=True,
            keyring_available=True, fallback_active=False,
        ),
    )
    monkeypatch.setattr(
        secret_store_mod, "delete_cartesia_api_key",
        lambda *a, **kw: delete_calls.append((a, kw))
        or secret_store_mod.StorageStatus(
            backend=secret_store_mod.STORAGE_NONE, has_key=False,
            keyring_available=True, fallback_active=False,
        ),
    )

    saved_updates: list[dict] = []

    def _fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", _fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device_name = getattr(overlay, "_audio_output_device_name", None)
        cartesia_api_key_action = "unchanged"
        cartesia_api_key_value = ""

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert len(save_calls) == 0, (
            f"secret_store.save_cartesia_api_key must NOT be called when "
            f"action is unchanged, got {len(save_calls)} calls"
        )
        assert len(delete_calls) == 0, (
            f"secret_store.delete_cartesia_api_key must NOT be called when "
            f"action is unchanged, got {len(delete_calls)} calls"
        )
    finally:
        overlay.stop()


def test_overlay_apply_clears_cartesia_api_key_when_action_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S2: action='clear' must call delete_cartesia_api_key() and
    report 'cleared' on inline status."""
    audio_player = _FakeAudioPlayer([])
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=lambda _device: None,
    )

    import wordy.secret as secret_store_mod

    delete_calls: list[tuple] = []

    def _fake_delete() -> secret_store_mod.StorageStatus:
        delete_calls.append(())
        return secret_store_mod.StorageStatus(
            backend=secret_store_mod.STORAGE_NONE, has_key=False,
            keyring_available=True, fallback_active=False,
        )

    monkeypatch.setattr(secret_store_mod, "delete_cartesia_api_key", _fake_delete)

    saved_updates: list[dict] = []

    def _fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", _fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device_name = getattr(overlay, "_audio_output_device_name", None)
        cartesia_api_key_action = "clear"
        cartesia_api_key_value = ""

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert len(delete_calls) == 1, (
            f"secret_store.delete_cartesia_api_key must be called exactly once, "
            f"got {len(delete_calls)} calls"
        )

        assert fake_window.status_calls, (
            "set_apply_status must be called after clearing"
        )
        status_text = " ".join(
            str(a) for args, _ in fake_window.status_calls for a in args
        )
        assert ("清除" in status_text
                or "已删除" in status_text
                or "removed" in status_text.lower()), (
            "Inline status must mention the API key was cleared/removed; "
            f"got status_text={status_text!r}"
        )
    finally:
        overlay.stop()


def test_overlay_apply_cartesia_plaintext_fallback_shows_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fallback: save returns StorageStatus(fallback_active=True);
    inline status must contain a warning (e.g. 明文 or 高风险) and
    MUST NOT expose the raw key."""
    audio_player = _FakeAudioPlayer([])
    overlay = _new_overlay_with_audio_player(
        monkeypatch,
        audio_player,
        on_audio_output_change=lambda _device: None,
    )

    import wordy.secret as secret_store_mod

    save_calls: list[tuple[str, bool]] = []

    def _fake_save_cartesia(
        value: str, allow_plaintext_fallback: bool = False
    ) -> secret_store_mod.StorageStatus:
        save_calls.append((value, allow_plaintext_fallback))
        return secret_store_mod.StorageStatus(
            backend=secret_store_mod.STORAGE_PLAINTEXT,
            has_key=True,
            keyring_available=False,
            fallback_active=True,
        )

    monkeypatch.setattr(secret_store_mod, "save_cartesia_api_key", _fake_save_cartesia)

    saved_updates: list[dict] = []

    def _fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", _fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device_name = getattr(overlay, "_audio_output_device_name", None)
        cartesia_api_key_action = "set"
        cartesia_api_key_value = "sk_test_FALLBACK"

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert len(save_calls) == 1, (
            f"secret_store.save_cartesia_api_key must be called once, "
            f"got {len(save_calls)} calls"
        )

        assert fake_window.status_calls, (
            "set_apply_status must be called even when fallback is active"
        )
        status_text = " ".join(
            str(a) for args, _ in fake_window.status_calls for a in args
        )
        has_warning = any(
            ind in status_text for ind in ("明文", "高风险", "plaintext", "fallback")
        )
        assert has_warning, (
            "Inline status must contain a warning about plaintext/fallback "
            f"when StorageStatus.fallback_active=True; "
            f"got status_text={status_text!r}"
        )
        assert "sk_test_FALLBACK" not in status_text, (
            "Inline status must not leak the secret value even under fallback"
        )
    finally:
        overlay.stop()


def test_overlay_apply_cartesia_set_reports_rebuild_callback_failure_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_player = _FakeAudioPlayer([])

    def _raise_rebuild_failure() -> None:
        raise RuntimeError("engine rebuild failed")

    module = _import_input_overlay(monkeypatch)
    overlay = module.InputOverlay(
        on_submit=lambda _text: None,
        audio_player=audio_player,
        on_audio_output_change=lambda _device: None,
        on_cartesia_api_key_change=_raise_rebuild_failure,
    )

    import wordy.secret as secret_store_mod

    save_calls: list[tuple[str, bool]] = []

    def _fake_save_cartesia(
        value: str, allow_plaintext_fallback: bool = False
    ) -> secret_store_mod.StorageStatus:
        save_calls.append((value, allow_plaintext_fallback))
        return secret_store_mod.StorageStatus(
            backend=secret_store_mod.STORAGE_KEYRING,
            has_key=True,
            keyring_available=True,
            fallback_active=False,
        )

    monkeypatch.setattr(secret_store_mod, "save_cartesia_api_key", _fake_save_cartesia)

    saved_updates: list[dict] = []

    def _fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", _fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device_name = getattr(overlay, "_audio_output_device_name", None)
        cartesia_api_key_action = "set"
        cartesia_api_key_value = "sk_test_REBUILD_FAILURE"

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert save_calls == [("sk_test_REBUILD_FAILURE", False)], (
            "secret_store.save_cartesia_api_key must succeed before rebuild failure is reported"
        )
        merged: dict = {}
        for update in saved_updates:
            merged.update(update)
        assert "cartesia_api_key" not in merged
        assert "cartesia_api_key_value" not in merged
        assert fake_window.status_calls, (
            "set_apply_status must report on_cartesia_api_key_change rebuild failures inline"
        )
        status_text = " ".join(
            str(part)
            for args, kwargs in fake_window.status_calls
            for part in (*args, *kwargs.values())
        )
        assert "sk_test_REBUILD_FAILURE" not in status_text, (
            "Inline rebuild-failure status must not leak the raw API key"
        )
        lowered = status_text.lower()
        assert "error" in lowered or "failed" in lowered or "失败" in status_text, (
            f"Inline status must indicate rebuild failure, got status_text={status_text!r}"
        )
    finally:
        overlay.stop()


def test_overlay_apply_cartesia_clear_reports_rebuild_callback_failure_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audio_player = _FakeAudioPlayer([])

    def _raise_rebuild_failure() -> None:
        raise RuntimeError("engine rebuild failed")

    module = _import_input_overlay(monkeypatch)
    overlay = module.InputOverlay(
        on_submit=lambda _text: None,
        audio_player=audio_player,
        on_audio_output_change=lambda _device: None,
        on_cartesia_api_key_change=_raise_rebuild_failure,
    )

    import wordy.secret as secret_store_mod

    delete_calls: list[tuple] = []

    def _fake_delete() -> secret_store_mod.StorageStatus:
        delete_calls.append(())
        return secret_store_mod.StorageStatus(
            backend=secret_store_mod.STORAGE_NONE,
            has_key=False,
            keyring_available=True,
            fallback_active=False,
        )

    monkeypatch.setattr(secret_store_mod, "delete_cartesia_api_key", _fake_delete)

    saved_updates: list[dict] = []

    def _fake_update(self, **kwargs: Any) -> Path:
        saved_updates.append(dict(kwargs))
        return Path("/tmp/wavtrans-test-config.json")

    monkeypatch.setattr(wordy.config.AppSettings, "update", _fake_update)

    class _FakePending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._voice_id
        voice_name = overlay._voice_name
        volume = overlay._volume
        overlay_opacity = overlay._overlay_opacity
        tts_backend = overlay._tts_backend
        fixed_center = overlay._fixed_center
        audio_output_device_name = getattr(overlay, "_audio_output_device_name", None)
        cartesia_api_key_action = "clear"
        cartesia_api_key_value = ""

    class _FakeSettingsWindow:
        def __init__(self) -> None:
            self.current_label = MagicMock()
            self.window = MagicMock()
            self.closed = False
            self.status_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

        def get_pending_settings(self) -> _FakePending:
            return _FakePending()

        def set_apply_status(self, *args: Any, **kwargs: Any) -> None:
            self.status_calls.append((args, kwargs))

        def close(self) -> None:
            self.closed = True

    fake_window = _FakeSettingsWindow()
    monkeypatch.setattr("wordy.ui.overlay.QMessageBox", MagicMock())

    try:
        overlay._apply_pending_settings(fake_window)

        assert delete_calls == [()], (
            "secret_store.delete_cartesia_api_key must succeed before rebuild failure is reported"
        )
        merged: dict = {}
        for update in saved_updates:
            merged.update(update)
        assert "cartesia_api_key" not in merged
        assert "cartesia_api_key_value" not in merged
        assert fake_window.status_calls, (
            "set_apply_status must report on_cartesia_api_key_change rebuild failures inline"
        )
        status_text = " ".join(
            str(part)
            for args, kwargs in fake_window.status_calls
            for part in (*args, *kwargs.values())
        )
        assert "sk_" not in status_text, (
            "Inline rebuild-failure status must not include any raw API key material"
        )
        lowered = status_text.lower()
        assert "error" in lowered or "failed" in lowered or "失败" in status_text, (
            f"Inline status must indicate rebuild failure, got status_text={status_text!r}"
        )
    finally:
        overlay.stop()


# ---------------------------------------------------------------------------
# Cartesia API key engine-rebuild ownership note
#
# The _apply_pending_settings method (defined in overlay.py) is
# responsible for persisting the API key but does NOT own the TTS-engine
# rebuild.  Rebuilding the engine with the new key is handled in main.py
# via on_cartesia_api_key_change or an equivalent callback wired into
# InputOverlay.__init__.
# ---------------------------------------------------------------------------


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
