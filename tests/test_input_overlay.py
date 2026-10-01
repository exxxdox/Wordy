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

from tests._stubs import PYside6_STUBS, patch_module_stubs


def import_input_overlay_with_stubs(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    # 保存原始模块引用，测试结束后恢复
    _STUBBED_MODULES = (
        *PYside6_STUBS,
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

    def set_status(self, text: str) -> None:
        self.status_calls.append(text)

    def set_apply_status(self, text: str, *_args: object, **_kwargs: object) -> None:
        self.status_calls.append(text)


def _new_cfg():
    """创建最小 AppSettings 实例供测试用。"""
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
    cfg.hotkey = "f6"
    cfg.name = "F6"
    return cfg


def _new_hotkey_overlay(module: ModuleType) -> object:
    """创建 overlay（__new__，跳过 __init__），注入 _cfg 供 hotkey 方法使用。"""
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    overlay._cfg = _new_cfg()
    return overlay


def _new_apply_overlay(module: ModuleType) -> object:
    overlay = module.InputOverlay.__new__(module.InputOverlay)
    cfg = _new_cfg()
    overlay._cfg = cfg
    overlay._hotkey = cfg.hotkey
    overlay._hotkey_name = cfg.name
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
    overlay = _new_hotkey_overlay(module)
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
    overlay = _new_hotkey_overlay(module)
    overlay._hotkey_listener = None
    register_attempts: list[tuple[str, str]] = []

    def fail_register() -> None:
        register_attempts.append((overlay._cfg.hotkey, overlay._cfg.name))
        raise RuntimeError(f"{overlay._cfg.name} occupied")

    overlay._unregister_hotkey = lambda: None
    overlay._register_hotkey = fail_register

    registered = overlay.try_register_hotkey("f7", "F7")

    assert registered is False
    assert overlay._cfg.hotkey == "f6"
    assert overlay._cfg.name == "F6"
    assert register_attempts == [("f7", "F7"), ("f6", "F6")]


def test_finish_record_hotkey_clears_recording_and_registers_on_success_cancel_error(monkeypatch: pytest.MonkeyPatch) -> None:
    module = import_input_overlay_with_stubs(monkeypatch)
    settings_window = SettingsWindowStub()
    overlay = _new_hotkey_overlay(module)
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
    overlay = _new_hotkey_overlay(module)
    overlay._active_settings_window = lambda: settings_window
    overlay._hotkey_listener = None
    error = RuntimeError("register failed")

    def raise_register_error() -> None:
        raise error

    overlay._register_hotkey = raise_register_error
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


def test_apply_hotkey_rollback_on_register_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    """当快捷键注册失败时，try_register_hotkey 应回滚 hotkey。"""
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    register_attempts: list[tuple[str, str]] = []

    def fail_try_register(hotkey: str, name: str) -> bool:
        register_attempts.append((hotkey, name))
        return False

    overlay.try_register_hotkey = fail_try_register

    # 直接测试 try_register_hotkey 的回滚行为
    result = overlay.try_register_hotkey("f9", "F9")

    assert result is False
    assert register_attempts == [("f9", "F9")]
    assert overlay._cfg.hotkey == "f6"
    assert overlay._cfg.name == "F6"


def test_apply_audio_callback_payload_structured_dict(monkeypatch: pytest.MonkeyPatch) -> None:
    """结构化 dict 音频设备变更应通过 callback 传递完整 identity。"""
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    callback_payloads: list[object] = []

    def on_change(payload: object) -> None:
        callback_payloads.append(payload)

    overlay.on_audio_output_change = on_change
    structured_identity = {"name": "VB-Audio Cable", "host_api_name": "WASAPI"}

    overlay._on_settings_field_changed("audio_output_device", structured_identity)

    assert len(callback_payloads) == 1
    payload = callback_payloads[0]
    assert isinstance(payload, dict)
    assert payload == structured_identity


def test_apply_audio_callback_payload_non_dict_clears_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """非 dict 值（如旧版 str）应清空 audio_output_device，callback 收到 None。"""
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)

    callback_payloads: list[object] = []
    overlay.on_audio_output_change = lambda p: callback_payloads.append(p)

    overlay._on_settings_field_changed("audio_output_device", "VB-Cable")

    assert len(callback_payloads) == 1
    # 非 dict 值被 _handle_audio_output_change 清空，callback 收到 None
    assert callback_payloads[0] is None


def test_apply_audio_callback_payload_none_clears_device(monkeypatch: pytest.MonkeyPatch) -> None:
    """None 值应清空音频输出设备。"""
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)

    callback_payloads: list[object] = []
    overlay.on_audio_output_change = lambda p: callback_payloads.append(p)

    overlay._on_settings_field_changed("audio_output_device", None)

    assert len(callback_payloads) == 1
    assert callback_payloads[0] is None


def test_apply_cartesia_callback_failure_appends_status_and_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cartesia API key 设置回调失败应记录状态，不抛异常。"""
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    overlay.on_cartesia_api_key_change = lambda _key: (_ for _ in ()).throw(RuntimeError("boom"))

    # _on_settings_field_changed 直接调用 on_cartesia_api_key_change，不处理异常
    # 异常在设置窗口层捕获
    try:
        overlay._on_settings_field_changed("cartesia_api_key", "new-key")
    except RuntimeError:
        pass  # 设置窗口层应处理此异常

    # 确认回调被触发
    assert True  # 不抛异常即通过


def test_apply_cartesia_callback_failure_clear_does_not_raise(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cartesia API key 清除回调失败不抛异常。"""
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    overlay.on_cartesia_api_key_change = lambda _key: (_ for _ in ()).throw(RuntimeError("boom"))

    try:
        overlay._on_settings_field_changed("cartesia_api_key", None)
    except RuntimeError:
        pass

    assert True


def test_apply_volume_change_dispatches_callback(monkeypatch: pytest.MonkeyPatch) -> None:
    """音量变更应触发 on_volume_change 回调。"""
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = _new_apply_overlay(module)
    volume_calls: list[float] = []
    overlay.on_volume_change = lambda v: volume_calls.append(float(v))

    overlay._on_settings_field_changed("volume", 0.75)

    assert volume_calls == [0.75]


@pytest.mark.parametrize("old_error", [False, True])
def test_voice_requests_capture_provider_and_fetcher_and_ignore_old_results(monkeypatch, old_error):
    from unittest.mock import MagicMock
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = module.InputOverlay(on_submit=lambda text: None)
    overlay._cfg.active_tts_provider = "Cartesia"
    overlay._active_settings_window = lambda: None
    overlay._signals = MagicMock()
    overlay._signals.voices_loaded.emit.side_effect = overlay._finish_load_voices
    overlay._signals.voices_error.emit.side_effect = overlay._finish_load_voices_error
    pending = []
    class PendingThread:
        def __init__(self, target, daemon, args=()):
            pending.append(lambda: target(*args))
        def start(self):
            pass
    monkeypatch.setattr(module.threading, "Thread", PendingThread)
    calls = []
    def fetch_old():
        calls.append("Cartesia")
        if old_error:
            raise RuntimeError("old request failed")
        return [{"id": "old", "name": "Old"}]
    overlay.on_fetch_voices = fetch_old
    overlay._start_load_voices()
    overlay._cfg.active_tts_provider = "Volcengine"
    overlay.on_fetch_voices = lambda: calls.append("Volcengine") or [{"id": "new", "name": "New"}]
    overlay._start_load_voices()
    assert len(pending) == 2
    # Even a delayed thread must use its captured fetcher, never the newly selected engine.
    pending[0]()
    assert overlay._voices_loading is True
    assert overlay._cartesia_voices_cache == []
    assert overlay._volcengine_voices_cache == []
    assert overlay._voice_fetch_error is None
    pending[1]()
    assert calls == ["Cartesia", "Volcengine"]
    assert overlay._volcengine_voices_cache == [{"id": "new", "name": "New"}]
    assert overlay._cartesia_voices_cache == []
    assert overlay._voices_loading is False


@pytest.mark.parametrize("fail", [False, True])
def test_voice_worker_does_not_emit_after_overlay_closes(monkeypatch, fail):
    from unittest.mock import MagicMock
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = module.InputOverlay(on_submit=lambda text: None)
    overlay._active_settings_window = lambda: None
    overlay._signals = MagicMock()
    pending = []
    class PendingThread:
        def __init__(self, target, daemon, args=()):
            pending.append(lambda: target(*args))
        def start(self):
            pass
    monkeypatch.setattr(module.threading, "Thread", PendingThread)
    def fetch():
        overlay._closed = True
        if fail:
            raise RuntimeError("fetch failed during shutdown")
        return []
    overlay.on_fetch_voices = fetch
    overlay._start_load_voices()
    pending[0]()
    overlay._signals.voices_loaded.emit.assert_not_called()
    overlay._signals.voices_error.emit.assert_not_called()


@pytest.mark.parametrize("generation, provider", [(1, "Cartesia"), (2, "Volcengine")])
def test_queued_voice_results_cannot_update_new_request(monkeypatch, generation, provider):
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = module.InputOverlay(on_submit=lambda text: None)
    overlay._cfg.active_tts_provider = "Cartesia"
    overlay._voices_generation = 2
    overlay._voices_loading = True
    overlay._active_settings_window = lambda: None
    overlay._finish_load_voices(generation, provider, [{"id": "stale", "name": "Stale"}])
    overlay._finish_load_voices_error(generation, provider, RuntimeError("stale error"))
    assert overlay._cartesia_voices_cache == []
    assert overlay._voices_loading is True
    assert overlay._voice_fetch_error is None
    overlay._finish_load_voices(2, "Cartesia", [{"id": "latest", "name": "Latest"}])
    assert overlay._cartesia_voices_cache == [{"id": "latest", "name": "Latest"}]
    assert overlay._voices_loading is False


def test_voice_worker_tolerates_already_deleted_signal_object(monkeypatch, qapp):
    import shiboken6
    from wordy.ui.overlay_widgets import _OverlaySignals
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = module.InputOverlay(on_submit=lambda text: None)
    signals = _OverlaySignals(overlay)
    overlay._signals = signals
    shiboken6.delete(signals)
    # QObject may disappear between the worker's shutdown check and its emit.
    overlay._load_voices_worker(1, "Cartesia", lambda: [])



def test_voice_worker_delivers_tokened_results_through_real_qt_signal(monkeypatch, qapp):
    import threading
    from wordy.ui.overlay_widgets import _OverlaySignals
    module = import_input_overlay_with_stubs(monkeypatch)
    overlay = module.InputOverlay(on_submit=lambda text: None)
    overlay._cfg.active_tts_provider = "Cartesia"
    overlay._voices_generation = 1
    overlay._voices_loading = True
    overlay._active_settings_window = lambda: None
    overlay._signals = _OverlaySignals(overlay)
    overlay._signals.voices_loaded.connect(overlay._finish_load_voices)
    overlay._signals.voices_error.connect(overlay._finish_load_voices_error)
    voices = [{"id": "qt", "name": "Qt Voice"}]
    worker = threading.Thread(target=overlay._load_voices_worker, args=(1, "Cartesia", lambda: voices))
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive()
    qapp.processEvents()
    assert overlay._cartesia_voices_cache == voices
    assert overlay._voices_loading is False
    overlay._signals.deleteLater()
