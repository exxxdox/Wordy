#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for overlay.py input normalization and hotkey recording lifecycle."""

import importlib
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import cast

import pytest

import wordy.secret

from tests._stubs import PYside6_STUBS, patch_module_stubs


def import_input_overlay_with_stubs(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    # 保存原始模块引用，测试结束后恢复
    _STUBBED_MODULES = (
        *PYside6_STUBS,
        "wordy.hotkey",
        "wordy.ui.settings",
        "wordy.ui.settings_state",
        "wordy.ui.settings_widgets",
        "wordy.ui.settings_style",
        "wordy.ui.window",
        "wordy.ui.overlay_widgets",
    )
    _saved: dict[str, ModuleType | None] = {}
    for name in _STUBBED_MODULES:
        _saved[name] = sys.modules.get(name)
    patch_module_stubs(monkeypatch, _STUBBED_MODULES)
    # 同时清除 overlay 缓存以确保重新导入
    _saved["wordy.ui.overlay"] = sys.modules.pop("wordy.ui.overlay", None)
    try:
        module = importlib.import_module("wordy.ui.overlay")
        sys.modules.pop("wordy.ui.overlay", None)
        return module
    finally:
        # 恢复所有被修改的模块条目
        for name, original in _saved.items():
            if original is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = original


class ThreadStub:
    started_count: int = 0

    def __init__(self, target: Callable[[], None], daemon: bool) -> None:
        self.target: Callable[[], None] = target
        self.daemon: bool = daemon

    def start(self) -> None:
        type(self).started_count += 1


class SettingsWindowStub:
    def __init__(self) -> None:
        self.recording_started_count: int = 0
        self.record_results: list[tuple[object, object, object | None]] = []
        self.hotkey_warnings: list[str] = []
        self.status_calls: list[str] = []

    def set_recording_started(self) -> None:
        self.recording_started_count += 1

    def set_record_result(
        self,
        hotkey: object,
        hotkey_name: object,
        error: object | None = None,
    ) -> None:
        self.record_results.append((hotkey, hotkey_name, error))

    def set_hotkey_warning(self, text: str) -> None:
        self.hotkey_warnings.append(text)

    def set_apply_status(self, text: str, *_args: object, **_kwargs: object) -> None:
        self.status_calls.append(text)


class ApplyLabelStub:
    def __init__(self) -> None:
        self.texts: list[str] = []

    def setText(self, text: str) -> None:
        self.texts.append(text)


class ApplySettingsWindowStub:
    def __init__(self, pending: object) -> None:
        self._pending = pending
        self.current_label = ApplyLabelStub()
        self.window = object()
        self.status_calls: list[str] = []

    def get_pending_settings(self) -> object:
        return self._pending

    def set_apply_status(self, text: str, *_args: object, **_kwargs: object) -> None:
        self.status_calls.append(text)


def _new_apply_overlay(module: ModuleType) -> object:
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    # 注入 _cfg（AppSettings 单例），覆盖旧 mirror 字段模式
    from wordy.config import AppSettings
    cfg = AppSettings()
    cfg.active_tts_provider = "Cartesia"
    cfg.cartesia_voice_id = "voice-a"
    cfg.cartesia_voice_name = "Voice A"
    cfg.cartesia_tts_backend = "cartesia-bytes"
    cfg.volcengine_voice_id = None
    cfg.volcengine_voice_name = None
    cfg.volcengine_tts_backend = "Volcengine Bytes"
    cfg.volume = 1.0
    cfg.overlay_opacity = 1.0
    cfg.log_level = "INFO"
    cfg.fixed_center = True
    cfg.audio_output_device_name = None
    cfg.audio_output_device = None
    cfg.window_position = None
    overlay._cfg = cfg
    overlay._hotkey = "f6"
    overlay._hotkey_name = "F6"
    overlay._tts_api_provider = "Cartesia"
    overlay._last_saved_config_file = Path("config.json")
    overlay.root = None
    overlay.width = 0
    overlay.height = 0
    overlay.try_register_hotkey = lambda _hotkey, _name: True
    overlay.on_voice_change = None
    overlay.on_volume_change = None
    overlay.on_tts_backend_change = None
    overlay.on_audio_output_change = None
    overlay.on_cartesia_api_key_change = None
    return overlay


def _pending_settings(overlay: object, **overrides: object) -> object:
    cfg = overlay._cfg
    class Pending:
        hotkey = overlay._hotkey
        hotkey_name = overlay._hotkey_name
        voice_id = overlay._get_active_voice_id()
        voice_name = overlay._get_active_voice_name()
        volume = cfg.volume
        overlay_opacity = cfg.overlay_opacity
        tts_backend = overlay._get_active_tts_backend()
        log_level = cfg.log_level
        fixed_center = cfg.fixed_center
        audio_output_device_name = cfg.audio_output_device_name
        cartesia_api_key_action = "unchanged"
        cartesia_api_key_value = None
        volcengine_access_key_action = "unchanged"
        volcengine_access_key_value = None
        tts_api_provider = "Cartesia"
        volcengine_app_id = None

    pending = Pending()
    for name, value in overrides.items():
        setattr(pending, name, value)
    return pending


@pytest.fixture()
def normalize_input_text(monkeypatch: pytest.MonkeyPatch) -> Callable[[str], str]:
    imported = import_input_overlay_with_stubs(monkeypatch).normalize_input_text
    assert callable(imported)
    return cast(Callable[[str], str], imported)


def test_normalize_input_text_trims_whitespace(normalize_input_text: Callable[[str], str]) -> None:
    """Test that leading/trailing whitespace is trimmed."""
    assert normalize_input_text("  hello world  ") == "hello world"
    assert normalize_input_text("\nhello\n") == "hello"
    assert normalize_input_text("\thello\t") == "hello"
    assert normalize_input_text("  multiple   spaces  ") == "multiple   spaces"


def test_normalize_input_text_empty_input(normalize_input_text: Callable[[str], str]) -> None:
    """Test that empty/whitespace-only input becomes empty string."""
    assert normalize_input_text("") == ""
    assert normalize_input_text("   ") == ""
    assert normalize_input_text("\n\t  \n") == ""


def test_normalize_input_text_preserves_inner_spaces(normalize_input_text: Callable[[str], str]) -> None:
    """Test that inner spaces are preserved."""
    assert normalize_input_text("hello   world") == "hello   world"
    assert normalize_input_text("  hello   world  ") == "hello   world"


def test_start_record_hotkey_ignores_duplicate_start(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    settings_window = SettingsWindowStub()
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    overlay._recording_hotkey = False
    overlay._active_settings_window = lambda: settings_window
    overlay._read_hotkey_worker = lambda: None
    unregister_calls = []
    overlay._unregister_hotkey = lambda: unregister_calls.append("unregistered")
    monkeypatch.setattr(module.threading, "Thread", ThreadStub)
    ThreadStub.started_count = 0

    overlay._start_record_hotkey()
    overlay._start_record_hotkey()

    assert unregister_calls == ["unregistered"]
    assert ThreadStub.started_count == 1
    assert settings_window.recording_started_count == 1


def test_startup_hotkey_conflict_opens_settings_with_actionable_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    settings_window = SettingsWindowStub()
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    overlay._hotkey_name = "F6"
    overlay._settings_window = None
    error = RuntimeError("快捷键已被其他程序占用")
    created: list[str] = []

    def raise_register_error() -> None:
        raise error

    def create_settings_window() -> None:
        created.append("created")
        overlay._settings_window = settings_window

    overlay._register_hotkey = raise_register_error
    overlay._create_settings_window = create_settings_window
    overlay._active_settings_window = lambda: overlay._settings_window

    registered = overlay._try_register_startup_hotkey()

    assert registered is False
    assert created == ["created"]
    assert settings_window.hotkey_warnings == [
        "当前全局快捷键 F6 无法注册，可能已被其他程序占用。请录制并应用新的全局快捷键。"
    ]
    assert settings_window.status_calls == ["快捷键未启用：快捷键已被其他程序占用"]


def test_try_register_hotkey_returns_false_when_new_and_restore_both_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    overlay._hotkey = "f6"
    overlay._hotkey_name = "F6"
    overlay._hotkey_listener = None
    register_attempts: list[tuple[str, str]] = []

    def fail_register() -> None:
        register_attempts.append((overlay._hotkey, overlay._hotkey_name))
        raise RuntimeError(f"{overlay._hotkey_name} occupied")

    overlay._unregister_hotkey = lambda: None
    overlay._register_hotkey = fail_register

    registered = overlay.try_register_hotkey("f7", "F7")

    assert registered is False
    assert overlay._hotkey == "f6"
    assert overlay._hotkey_name == "F6"
    assert register_attempts == [("f7", "F7"), ("f6", "F6")]


def test_finish_record_hotkey_clears_recording_and_registers_on_success_cancel_error(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    settings_window = SettingsWindowStub()
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    overlay._active_settings_window = lambda: settings_window
    overlay._hotkey_listener = None
    register_calls = []
    overlay._register_hotkey = lambda: register_calls.append("registered")

    overlay._recording_hotkey = True
    overlay._finish_record_hotkey("ctrl+alt+h", None)

    assert overlay._recording_hotkey is False
    assert register_calls == ["registered"]
    assert settings_window.record_results[-1] == ("ctrl+alt+h", "Ctrl+Alt+H", None)

    overlay._recording_hotkey = True
    overlay._finish_record_hotkey(None, None)

    assert overlay._recording_hotkey is False
    assert register_calls == ["registered", "registered"]
    assert settings_window.record_results[-1] == (None, None, None)

    error = RuntimeError("boom")
    overlay._recording_hotkey = True
    overlay._finish_record_hotkey(None, error)

    assert overlay._recording_hotkey is False
    assert register_calls == ["registered", "registered", "registered"]
    assert settings_window.record_results[-1] == (None, None, error)


def test_finish_record_hotkey_skips_register_when_listener_already_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    register_calls = []
    overlay._hotkey_listener = object()
    overlay._active_settings_window = lambda: None
    overlay._register_hotkey = lambda: register_calls.append("registered")
    overlay._recording_hotkey = True

    overlay._finish_record_hotkey(None, None)

    assert overlay._recording_hotkey is False
    assert register_calls == []


def test_finish_record_hotkey_register_failure_clears_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    settings_window = SettingsWindowStub()
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    overlay._active_settings_window = lambda: settings_window
    overlay._hotkey_listener = None
    error = RuntimeError("register failed")

    def raise_register_error() -> None:
        raise error

    overlay._register_hotkey = raise_register_error
    overlay._hotkey_name = "F8"
    overlay._recording_hotkey = True

    overlay._finish_record_hotkey("f8", None)

    assert overlay._recording_hotkey is False
    assert settings_window.record_results[-1] == (None, None, error)


def test_on_settings_window_closed_reregisters_when_recording(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    register_calls = []
    overlay._register_hotkey = lambda: register_calls.append("registered")
    overlay._recording_hotkey = True
    overlay._settings_window = object()

    overlay._on_settings_window_closed()

    assert overlay._recording_hotkey is False
    assert overlay._settings_window is None
    assert register_calls == ["registered"]


def test_apply_pending_settings_no_changes_does_not_save_config(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    saved_updates: list[dict[str, object]] = []

    from wordy.config import AppSettings

    def fake_update(self, **kwargs: object) -> Path:
        saved_updates.append({str(k): v for k, v in kwargs.items()})
        return Path("updated-config.json")

    monkeypatch.setattr(AppSettings, "load", lambda **kw: AppSettings())
    monkeypatch.setattr(AppSettings, "update", fake_update)
    window = ApplySettingsWindowStub(_pending_settings(overlay))

    module.InputOverlay._apply_pending_settings(overlay, window)

    assert saved_updates == []
    assert window.status_calls == ["没有设置变更"]
    assert window.current_label.texts == []


def test_apply_pending_settings_saves_and_reports_only_changed_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    saved_updates: list[dict[str, object]] = []

    from wordy.config import AppSettings

    def fake_update(self, **kwargs: object) -> Path:
        saved_updates.append({str(k): v for k, v in kwargs.items()})
        return Path("updated-config.json")

    monkeypatch.setattr(AppSettings, "load", lambda **kw: AppSettings())
    monkeypatch.setattr(AppSettings, "update", fake_update)
    window = ApplySettingsWindowStub(_pending_settings(overlay, volume=0.75))

    module.InputOverlay._apply_pending_settings(overlay, window)

    assert saved_updates == [{"volume": 0.75}]
    assert overlay._volume == 0.75
    assert len(window.status_calls) == 1
    status = window.status_calls[0]
    assert "音量已更新为 0.75x" in status
    assert "全局快捷键" not in status
    assert "音色" not in status
    assert "模式" not in status


def test_apply_oserror_rolls_back_hotkey_and_prevents_mutation(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    try_register_calls: list[tuple[str, str]] = []
    original_try_register = overlay.try_register_hotkey

    def tracked_try_register(hotkey: str, name: str) -> bool:
        try_register_calls.append((hotkey, name))
        return original_try_register(hotkey, name)

    overlay.try_register_hotkey = tracked_try_register

    from wordy.config import AppSettings

    def failing_update(self, **kwargs: object) -> Path:
        raise OSError("write failed")

    monkeypatch.setattr(AppSettings, "load", lambda **kw: AppSettings())
    monkeypatch.setattr(AppSettings, "update", failing_update)
    monkeypatch.setattr(module, "QMessageBox", type("QMB", (), {"critical": staticmethod(lambda *a, **kw: None)}))
    window = ApplySettingsWindowStub(
        _pending_settings(overlay, hotkey="f7", hotkey_name="F7", volume=0.5)
    )

    module.InputOverlay._apply_pending_settings(overlay, window)

    assert try_register_calls == [("f7", "F7"), ("f6", "F6")]
    assert overlay._hotkey == "f6"
    assert overlay._hotkey_name == "F6"
    assert overlay._volume == 1.0
    assert overlay._cartesia_voice_id == "voice-a"
    assert window.status_calls == []


def test_apply_audio_callback_payload_structured_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    callback_payloads: list[object] = []

    def on_change(payload: object) -> None:
        callback_payloads.append(payload)

    overlay.on_audio_output_change = on_change

    saved_updates: list[dict[str, object]] = []

    from wordy.config import AppSettings

    def fake_update(self, **kwargs: object) -> Path:
        saved_updates.append({str(k): v for k, v in kwargs.items()})
        return Path("updated.json")

    monkeypatch.setattr(AppSettings, "load", lambda **kw: AppSettings())
    monkeypatch.setattr(AppSettings, "update", fake_update)
    structured_identity = {"name": "VB-Audio Cable", "host_api_name": "WASAPI"}
    window = ApplySettingsWindowStub(
        _pending_settings(overlay,
                          audio_output_device_identity=dict(structured_identity),
                          audio_output_device_name="VB-Audio Cable")
    )

    module.InputOverlay._apply_pending_settings(overlay, window)

    assert len(callback_payloads) == 1
    payload = callback_payloads[0]
    assert isinstance(payload, dict)
    assert payload == structured_identity


def test_apply_audio_callback_payload_legacy_str(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)

    callback_payloads: list[object] = []
    overlay.on_audio_output_change = lambda p: callback_payloads.append(p)
    overlay._audio_output_device_name = None
    overlay._audio_output_device = None

    saved_updates: list[dict[str, object]] = []

    from wordy.config import AppSettings

    def fake_update(self, **kwargs: object) -> Path:
        saved_updates.append({str(k): v for k, v in kwargs.items()})
        return Path("updated.json")

    monkeypatch.setattr(AppSettings, "load", lambda **kw: AppSettings())
    monkeypatch.setattr(AppSettings, "update", fake_update)
    window = ApplySettingsWindowStub(
        _pending_settings(overlay, audio_output_device_name="VB-Cable")
    )

    module.InputOverlay._apply_pending_settings(overlay, window)

    assert len(callback_payloads) == 1
    payload = callback_payloads[0]
    assert isinstance(payload, str) or payload is None
    assert payload == "VB-Cable"


def test_apply_audio_callback_payload_legacy_none(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    overlay._audio_output_device_name = "Old Device"
    overlay._audio_output_device = {"name": "Old Device", "host_api_name": None}

    callback_payloads: list[object] = []
    overlay.on_audio_output_change = lambda p: callback_payloads.append(p)

    saved_updates: list[dict[str, object]] = []

    from wordy.config import AppSettings

    def fake_update(self, **kwargs: object) -> Path:
        saved_updates.append({str(k): v for k, v in kwargs.items()})
        return Path("updated.json")

    monkeypatch.setattr(AppSettings, "load", lambda **kw: AppSettings())
    monkeypatch.setattr(AppSettings, "update", fake_update)
    window = ApplySettingsWindowStub(
        _pending_settings(overlay, audio_output_device_name=None)
    )

    module.InputOverlay._apply_pending_settings(overlay, window)

    assert len(callback_payloads) == 1
    assert callback_payloads[0] is None


def test_apply_cartesia_callback_failure_appends_status_and_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    overlay.on_cartesia_api_key_change = lambda _key: (_ for _ in ()).throw(RuntimeError("boom"))

    class FakeStorageStatus:
        fallback_active = False
        backend = "keyring"

    from wordy.config import AppSettings

    monkeypatch.setattr(AppSettings, "load", lambda **kw: AppSettings())
    monkeypatch.setattr(AppSettings, "update", lambda self, **kw: Path("updated.json"))
    monkeypatch.setattr(wordy.secret, "save_cartesia_api_key", lambda *_a, **_kw: FakeStorageStatus())
    window = ApplySettingsWindowStub(
        _pending_settings(overlay, cartesia_api_key_action="set", cartesia_api_key_value="new-key")
    )

    module.InputOverlay._apply_pending_settings(overlay, window)

    assert any("刷新失败" in s for s in window.status_calls)
    assert window.status_calls
    combined = "".join(window.status_calls)
    assert "new-key" not in combined


def test_apply_cartesia_callback_failure_clear_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    overlay.on_cartesia_api_key_change = lambda _key: (_ for _ in ()).throw(RuntimeError("boom"))

    from wordy.config import AppSettings

    monkeypatch.setattr(AppSettings, "load", lambda **kw: AppSettings())
    monkeypatch.setattr(AppSettings, "update", lambda self, **kw: Path("updated.json"))
    monkeypatch.setattr(wordy.secret, "delete_cartesia_api_key", lambda: None)
    window = ApplySettingsWindowStub(
        _pending_settings(overlay, cartesia_api_key_action="clear")
    )

    module.InputOverlay._apply_pending_settings(overlay, window)

    assert any("刷新失败" in s for s in window.status_calls)
    assert window.status_calls
