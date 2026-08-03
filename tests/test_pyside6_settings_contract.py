#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PySide6 contract tests for SettingsWindow pending settings behavior."""

from __future__ import annotations

import os
import sys
from dataclasses import fields, is_dataclass
from types import ModuleType
from typing import Any

import pytest

_ = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ = pytest.importorskip("PySide6")


def _install_native_dependency_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("pyaudio", "cartesia", "websockets.sync.client"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))

    cartesia_module = sys.modules["cartesia"]
    setattr(cartesia_module, "Cartesia", object)

    websocket_client_module = sys.modules["websockets.sync.client"]
    setattr(websocket_client_module, "ClientConnection", object)


def _settings_state_kwargs(SettingsState: Any, **overrides: Any) -> dict[str, Any]:
    """Build SettingsState kwargs with safe defaults for newer optional contracts."""
    values: dict[str, Any] = {
        "hotkey": "f6",
        "hotkey_name": "F6",
        "voice_id": "voice-a",
        "voice_name": "Old Voice",
        "volume": 1.0,
        "overlay_opacity": 1.0,
        "tts_backend": "cartesia-bytes",
        "fixed_center": True,
        "voices_cache": [{"id": "voice-a", "name": "Old Voice"}],
        "voices_loading": False,
        "voice_fetch_error": None,
        "cartesia_api_key_saved": False,
    }
    values.update(overrides)
    if is_dataclass(SettingsState):
        field_names = {field.name for field in fields(SettingsState)}
        return {name: value for name, value in values.items() if name in field_names}
    return values


def _new_settings_window(
    monkeypatch: pytest.MonkeyPatch,
    on_close=None,
    **state_overrides: Any,
):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _install_native_dependency_stubs(monkeypatch)

    _ = pytest.importorskip("PySide6.QtWidgets")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover - dependency-specific import failures
        pytest.skip(f"QApplication cannot be imported: {exc}")
        raise
    try:
        from easy_tts.ui.settings import SettingsState, SettingsWindow
    except Exception as exc:  # pragma: no cover - dependency-specific import failures
        pytest.skip(f"SettingsWindow dependencies cannot be imported: {exc}")
        raise

    app = QApplication.instance() or QApplication(sys.argv[:1])
    state = SettingsState(**_settings_state_kwargs(SettingsState, **state_overrides))
    window: Any | None = None
    try:
        window = SettingsWindow(
            None,
            state,
            on_record_hotkey=lambda: None,
            on_refresh_voices=lambda: None,
            on_apply=lambda _window: None,
            on_close=on_close or (lambda: None),
        )
    except Exception as exc:  # pragma: no cover - environment-specific Qt failures
        pytest.skip(f"SettingsWindow cannot be instantiated on this platform: {exc}")
    assert window is not None
    return app, window


def _device_entry(name: str, host_api_name: str, display_name: str | None = None) -> dict[str, str]:
    """Build a Host-API-aware audio output device descriptor for tests."""
    return {
        "name": name,
        "host_api_name": host_api_name,
        "display_name": display_name if display_name is not None else f"{name} [{host_api_name}]",
    }


def _new_settings_window_with_audio_output(
    monkeypatch: pytest.MonkeyPatch,
    audio_output_devices: list[dict[str, str]],
    audio_output_device_identity: dict[str, str] | None,
    audio_output_devices_error: Exception | None = None,
):
    """Construct SettingsWindow with Host-API-aware audio output state.

    ``audio_output_devices`` is a list of structured descriptors with ``name``,
    ``host_api_name`` and ``display_name`` keys. ``audio_output_device_identity``
    is the persisted structured identity ``{"name": ..., "host_api_name": ...}``
    (or ``None`` for the 系统默认 fallback).

    Production currently expects ``audio_output_devices: list[str]`` and a flat
    ``audio_output_device_name: str | None``. Tests using this helper therefore
    stay RED until production migrates to the Host-API-aware contract.
    """
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _install_native_dependency_stubs(monkeypatch)

    _ = pytest.importorskip("PySide6.QtWidgets")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover - dependency-specific import failures
        pytest.skip(f"QApplication cannot be imported: {exc}")
        raise
    try:
        from easy_tts.ui.settings import SettingsState, SettingsWindow
    except Exception as exc:  # pragma: no cover - dependency-specific import failures
        pytest.skip(f"SettingsWindow dependencies cannot be imported: {exc}")
        raise

    app = QApplication.instance() or QApplication(sys.argv[:1])
    state = SettingsState(
        **_settings_state_kwargs(
            SettingsState,
            audio_output_devices=audio_output_devices,
            audio_output_device_identity=audio_output_device_identity,
            audio_output_devices_error=audio_output_devices_error,
        )
    )
    window: Any | None = None
    try:
        window = SettingsWindow(
            None,
            state,
            on_record_hotkey=lambda: None,
            on_refresh_voices=lambda: None,
            on_apply=lambda _window: None,
            on_close=lambda: None,
        )
    except Exception as exc:  # pragma: no cover - environment-specific Qt failures
        pytest.skip(f"SettingsWindow cannot be instantiated on this platform: {exc}")
    assert window is not None
    return app, window


SENTINEL_EXISTING_API_KEY = "sk_existing_DO_NOT_LEAK"


def _line_edit_texts(window: Any) -> list[str]:
    from PySide6.QtWidgets import QLineEdit

    return [line_edit.text() for line_edit in window.window.findChildren(QLineEdit)]


def _label_texts(window: Any) -> list[str]:
    from PySide6.QtWidgets import QLabel

    return [label.text() for label in window.window.findChildren(QLabel)]


def test_settings_window_api_key_input_is_masked_and_never_prefills_existing_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app, window = _new_settings_window(
        monkeypatch,
        cartesia_api_key_saved=True,
        cartesia_api_key_value=SENTINEL_EXISTING_API_KEY,
    )
    try:
        from PySide6.QtWidgets import QLineEdit

        api_key_input = getattr(window, "api_key_input", None)
        assert api_key_input is not None, "SettingsWindow must expose masked api_key_input"
        assert isinstance(api_key_input, QLineEdit)
        assert api_key_input.echoMode() == QLineEdit.EchoMode.Password
        assert api_key_input.text() == "", "saved Cartesia API key must never be prefilled"

        placeholder = api_key_input.placeholderText()
        assert placeholder, "api_key_input placeholder must indicate saved/unsaved state"
        assert "已保存" in placeholder or "saved" in placeholder.lower()

        visible_text = "\n".join([placeholder, *_line_edit_texts(window), *_label_texts(window)])
        assert SENTINEL_EXISTING_API_KEY not in visible_text
    finally:
        window.close()


def test_settings_window_api_key_unsaved_placeholder_has_no_raw_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _app, window = _new_settings_window(monkeypatch, cartesia_api_key_saved=False)
    try:
        api_key_input = getattr(window, "api_key_input", None)
        assert api_key_input is not None, "SettingsWindow must expose masked api_key_input"

        placeholder = api_key_input.placeholderText()
        assert placeholder, "api_key_input placeholder must tell user no key is saved"
        assert "未保存" in placeholder or "not saved" in placeholder.lower() or "unsaved" in placeholder.lower()
        assert "sk_" not in placeholder
    finally:
        window.close()


def test_settings_window_api_key_entry_normalizes_env_assignment_to_pending_set(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app, window = _new_settings_window(monkeypatch, cartesia_api_key_saved=False)
    try:
        api_key_input = getattr(window, "api_key_input", None)
        assert api_key_input is not None, "SettingsWindow must expose masked api_key_input"

        api_key_input.setText("CARTESIA_API_KEY=sk_test_NEW")
        pending = window.get_pending_settings()

        assert getattr(pending, "cartesia_api_key_action", None) == "set"
        assert getattr(pending, "cartesia_api_key_value", None) == "sk_test_NEW"
    finally:
        window.close()


def test_settings_window_blank_api_key_field_keeps_pending_key_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app, window = _new_settings_window(monkeypatch, cartesia_api_key_saved=True)
    try:
        api_key_input = getattr(window, "api_key_input", None)
        assert api_key_input is not None, "SettingsWindow must expose masked api_key_input"

        api_key_input.setText("   ")
        pending = window.get_pending_settings()

        assert getattr(pending, "cartesia_api_key_action", None) == "unchanged"
        assert getattr(pending, "cartesia_api_key_value", "unexpected") is None
    finally:
        window.close()


def test_settings_window_clear_api_key_control_sets_pending_clear_intent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app, window = _new_settings_window(monkeypatch, cartesia_api_key_saved=True)
    try:
        clear_control = getattr(window, "clear_api_key_button", None)
        assert clear_control is not None, "SettingsWindow must expose explicit clear_api_key_button"

        clear_control.click()
        pending = window.get_pending_settings()

        assert getattr(pending, "cartesia_api_key_action", None) == "clear"
        assert getattr(pending, "cartesia_api_key_value", "unexpected") is None
    finally:
        window.close()


def test_settings_window_pending_settings_and_refreshed_voice_label_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    _app, window = _new_settings_window(monkeypatch)
    try:
        labels = window.set_voices_loaded(
            [
                {"id": "voice-a", "name": "Old Voice"},
                {"id": "voice-b", "name": "Refreshed Voice"},
            ],
            "voice-b",
        )
        assert "Refreshed Voice" in labels
        assert window.voice_combo.currentText() == "Refreshed Voice"

        window._on_voice_selected(window.voice_combo.currentText())
        window.tts_backend_combo.setCurrentText("Cartesia Realtime")
        window.volume_slider.setValue(window._volume_to_slider(1.35))
        window.fixed_center_check.setChecked(False)

        pending = window.get_pending_settings()

        assert pending.hotkey == "f6"
        assert pending.hotkey_name == "F6"
        assert pending.voice_id == "voice-b"
        assert pending.voice_name == "Refreshed Voice"
        assert pending.volume == 1.35
        assert pending.tts_backend == "Cartesia Realtime"
        assert pending.fixed_center is False
    finally:
        window.close()


class _FakeWheelEvent:
    def __init__(self) -> None:
        self.ignored = False

    def ignore(self) -> None:
        self.ignored = True


def test_settings_window_dropdowns_ignore_collapsed_mouse_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mouse wheel over closed dropdowns must not change selection or trap scrolling."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        window.set_voices_loaded(
            [
                {"id": "voice-a", "name": "Old Voice"},
                {"id": "voice-b", "name": "Refreshed Voice"},
            ],
            "voice-a",
        )
        window.audio_output_combo.addItems(["系统默认", "Speakers [Windows WASAPI]"])

        for combo in (window.voice_combo, window.tts_backend_combo, window.audio_output_combo):
            if combo.count() < 2:
                combo.addItem("extra")
            combo.setCurrentIndex(0)
            event = _FakeWheelEvent()

            combo.wheelEvent(event)

            assert event.ignored is True
            assert combo.currentIndex() == 0
    finally:
        window.close()


def test_settings_window_sliders_ignore_mouse_wheel(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mouse wheel over sliders must not change volume or opacity accidentally."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        for slider in (window.volume_slider, window.opacity_slider):
            slider.setValue(slider.minimum())
            event = _FakeWheelEvent()

            slider.wheelEvent(event)

            assert event.ignored is True
            assert slider.value() == slider.minimum()
    finally:
        window.close()


def test_settings_window_recording_button_lifecycle(monkeypatch: pytest.MonkeyPatch) -> None:
    _app, window = _new_settings_window(monkeypatch)
    try:
        window.set_recording_started()

        assert window.record_button.isEnabled() is False
        assert window.record_button.text() == "录制中..."

        window.set_record_result("f7", "F7")
        pending = window.get_pending_settings()

        assert window.record_button.isEnabled() is True
        assert window.record_button.text() == "重新录制"
        assert pending.hotkey == "f7"
        assert pending.hotkey_name == "F7"
        assert window.pending_label.text() == "待应用：F7"
    finally:
        window.close()


def test_settings_window_escape_during_recording_is_accepted_without_closing(monkeypatch: pytest.MonkeyPatch) -> None:
    close_calls = []
    _app, window = _new_settings_window(monkeypatch, on_close=lambda: close_calls.append("closed"))
    try:
        try:
            from PySide6.QtCore import QEvent, Qt
            from PySide6.QtGui import QKeyEvent
        except Exception as exc:  # pragma: no cover - dependency-specific import failures
            pytest.skip(f"Qt key event dependencies cannot be imported: {exc}")
            raise

        window.set_recording_started()
        event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier)

        window.window.keyPressEvent(event)

        assert event.isAccepted() is True
        assert window.exists() is True
        assert window._closed is False
        assert close_calls == []

        window.set_record_result(None, None)

        assert window.record_button.isEnabled() is True
    finally:
        window.close()


def test_settings_window_space_hotkey_press_during_recording_is_accepted_without_popup_or_closing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app, window = _new_settings_window(monkeypatch)
    try:
        try:
            from PySide6.QtCore import QEvent, Qt
            from PySide6.QtGui import QKeyEvent
        except Exception as exc:  # pragma: no cover - dependency-specific import failures
            pytest.skip(f"Qt key event dependencies cannot be imported: {exc}")
            raise

        window.set_recording_started()
        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
        )

        window.window.keyPressEvent(event)

        assert event.isAccepted() is True
        assert window.exists() is True
        assert window._closed is False
        assert window.voice_combo.view().isVisible() is False
    finally:
        window.close()


def test_settings_window_space_hotkey_on_focused_button_during_recording_does_not_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    close_calls = []
    app, window = _new_settings_window(monkeypatch, on_close=lambda: close_calls.append("closed"))
    try:
        try:
            from PySide6.QtCore import QEvent, Qt
            from PySide6.QtGui import QKeyEvent
            from PySide6.QtWidgets import QPushButton
        except Exception as exc:  # pragma: no cover - dependency-specific import failures
            pytest.skip(f"Qt key event dependencies cannot be imported: {exc}")
            raise

        cancel_button = window.window.findChild(QPushButton, "cancelButton")
        assert cancel_button is not None
        cancel_button.setFocus(Qt.FocusReason.OtherFocusReason)
        window.set_recording_started()

        event = QKeyEvent(
            QEvent.Type.KeyPress,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
        )

        app.sendEvent(cancel_button, event)

        assert event.isAccepted() is True
        assert window.exists() is True
        assert window._closed is False
        assert close_calls == []
    finally:
        window.close()


def test_settings_window_space_hotkey_release_during_recording_is_accepted_without_closing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app, window = _new_settings_window(monkeypatch)
    try:
        try:
            from PySide6.QtCore import QEvent, Qt
            from PySide6.QtGui import QKeyEvent
        except Exception as exc:  # pragma: no cover - dependency-specific import failures
            pytest.skip(f"Qt key event dependencies cannot be imported: {exc}")
            raise

        window.set_recording_started()
        event = QKeyEvent(
            QEvent.Type.KeyRelease,
            Qt.Key.Key_Space,
            Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier,
        )

        window.window.keyReleaseEvent(event)

        assert event.isAccepted() is True
        assert window.exists() is True
        assert window._closed is False
    finally:
        window.close()



def test_settings_window_recording_cancel_and_error_reenable(monkeypatch: pytest.MonkeyPatch) -> None:
    _app, window = _new_settings_window(monkeypatch)
    try:
        window.set_recording_started()
        window.set_record_result(None, None)

        assert window.record_button.isEnabled() is True
        assert window.record_button.text() == "录制快捷键"

        window.set_recording_started()
        window.set_record_result(None, None, RuntimeError("failed"))

        assert window.record_button.isEnabled() is True
        assert window.record_button.text() == "重新录制"
    finally:
        window.close()



def test_settings_window_exposes_inline_apply_status_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """SettingsWindow must provide inline apply status methods for non-modal apply feedback."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        set_status = getattr(window, "set_apply_status", None)
        clear_status = getattr(window, "clear_apply_status", None)
        assert callable(set_status), "SettingsWindow must expose set_apply_status(message)"
        assert callable(clear_status), "SettingsWindow must expose clear_apply_status()"

        set_status("设置已应用")
        status_label = getattr(window, "apply_status_label", None)
        assert status_label is not None, "SettingsWindow must expose apply_status_label"
        assert "设置已应用" in status_label.text()
        assert status_label.isVisible() is True

        clear_status()
        assert status_label.text() == ""
    finally:
        window.close()


def test_settings_window_uses_frameless_flag_and_preserves_topmost(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings dialog chrome must be frameless while staying above the main overlay."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtCore import Qt

        flags = window.window.windowFlags()
        assert flags & Qt.WindowType.FramelessWindowHint, "settings window must use FramelessWindowHint"
        assert flags & Qt.WindowType.WindowStaysOnTopHint, "settings window must preserve WindowStaysOnTopHint"
    finally:
        window.close()


def test_settings_window_frameless_chrome_keeps_visible_internal_title(monkeypatch: pytest.MonkeyPatch) -> None:
    """Frameless settings window must still expose its own visible in-dialog title."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtWidgets import QLabel

        window.window.show()
        _app.processEvents()
        title_label = window.window.findChild(QLabel, "dialogTitle")
        assert title_label is not None, "frameless dialog must contain an internal dialogTitle widget"
        assert title_label.isVisible() is True
        assert title_label.text() == "设置"
    finally:
        window.close()


def test_settings_window_uses_translucent_background_for_rounded_corners(monkeypatch: pytest.MonkeyPatch) -> None:
    """Frameless rounded settings dialog must not paint an opaque square behind rounded corners."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtCore import Qt

        assert window.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is True
    finally:
        window.close()


def test_settings_window_translucent_background_preserves_size_grip(monkeypatch: pytest.MonkeyPatch) -> None:
    """Transparent dialog background must not disable or hide the resize grip."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtWidgets import QSizeGrip

        window.window.show()
        _app.processEvents()
        grip = window.window.findChild(QSizeGrip)
        assert window.window.isSizeGripEnabled() is True
        assert grip is not None, "settings dialog must keep a QSizeGrip child"
        assert grip.isVisible() is True
    finally:
        window.close()


def test_settings_window_translucent_background_preserves_dialog_shell(monkeypatch: pytest.MonkeyPatch) -> None:
    """QDialog may be transparent, but QFrame#dialogShell must remain the visible rounded shell."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtWidgets import QFrame

        window.window.show()
        _app.processEvents()
        shell = window.window.findChild(QFrame, "dialogShell")
        assert shell is not None, "settings dialog must contain QFrame#dialogShell"
        assert shell.isVisible() is True
    finally:
        window.close()


def test_settings_window_default_height_fits_all_sections_without_initial_scroll(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default settings dialog height must fit all configuration sections without initial scrolling."""
    _app, window = _new_settings_window(
        monkeypatch,
        cartesia_api_key_saved=True,
        audio_output_devices=["Speakers [Windows WASAPI]"],
        audio_output_device_name="Speakers [Windows WASAPI]",
    )
    try:
        from PySide6.QtWidgets import QScrollArea

        window.window.show()
        _app.processEvents()
        scroll_area = window.window.findChild(QScrollArea)
        assert scroll_area is not None, "settings dialog must contain a QScrollArea"

        content = scroll_area.widget()
        assert content is not None, "settings QScrollArea must have a content widget"
        scrollbar_maximum = scroll_area.verticalScrollBar().maximum()
        dialog_height = window.window.height()
        viewport_height = scroll_area.viewport().height()
        content_height = content.height()
        content_size_hint_height = content.sizeHint().height()

        assert scrollbar_maximum == 0, (
            "settings dialog default height must fit all configuration items without initial vertical scrolling; "
            f"dialog_height={dialog_height}, viewport_height={viewport_height}, "
            f"content_height={content_height}, content_size_hint_height={content_size_hint_height}, "
            f"scrollbar_maximum={scrollbar_maximum}"
        )
    finally:
        window.close()


def test_settings_window_close_path_invokes_on_close_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Closing the frameless settings dialog must still route through on_close exactly once."""
    close_calls: list[str] = []
    _app, window = _new_settings_window(monkeypatch, on_close=lambda: close_calls.append("closed"))

    window.close()
    window.close()

    assert close_calls == ["closed"]


def test_settings_window_idle_escape_routes_through_close_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Idle Esc must close through SettingsWindow so owner cleanup callbacks run."""
    close_calls: list[str] = []
    _app, window = _new_settings_window(monkeypatch, on_close=lambda: close_calls.append("closed"))
    try:
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

        window.window.show()
        _app.processEvents()
        assert window.window.isVisible() is True

        window.window.keyPressEvent(
            QKeyEvent(
                QEvent.Type.KeyPress,
                Qt.Key.Key_Escape,
                Qt.KeyboardModifier.NoModifier,
            )
        )
        _app.processEvents()

        assert close_calls == ["closed"]
        assert window._closed is True
        assert window.exists() is False
    finally:
        if window.exists():
            window.close()


def test_settings_window_dialog_close_marks_closed_when_on_close_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A close callback failure must not leave SettingsWindow looking alive."""

    def raise_on_close() -> None:
        raise RuntimeError("close callback failed")

    _app, window = _new_settings_window(monkeypatch, on_close=raise_on_close)

    with pytest.raises(RuntimeError, match="close callback failed"):
        window._handle_dialog_close()

    assert window.exists() is False
    assert window._closed is True


def test_settings_window_exists_returns_false_for_missing_dialog_reference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale or cleared dialog reference must not be reported as focusable."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        window.window = None

        assert window.exists() is False
    finally:
        window._closed = True


def _mouse_event(event_type: Any, local_x: int, local_y: int, global_x: int, global_y: int):
    from PySide6.QtCore import QPointF, Qt
    from PySide6.QtGui import QMouseEvent

    return QMouseEvent(
        event_type,
        QPointF(local_x, local_y),
        QPointF(global_x, global_y),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )


def test_settings_window_title_area_drag_moves_frameless_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mouse drag on dialogTitle must move the frameless settings dialog."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtWidgets import QLabel

        dialog = window.window
        title_label = dialog.findChild(QLabel, "dialogTitle")
        assert title_label is not None, "frameless dialog must expose QLabel#dialogTitle as the drag handle"
        assert hasattr(dialog, "eventFilter"), "_SettingsDialog must implement eventFilter for title dragging"

        dialog.move(QPoint(80, 90))
        start_pos = dialog.pos()

        assert dialog.eventFilter(title_label, _mouse_event(QEvent.Type.MouseButtonPress, 8, 8, 200, 220)) is True
        assert dialog.eventFilter(title_label, _mouse_event(QEvent.Type.MouseMove, 28, 30, 240, 260)) is True

        assert dialog.pos() != start_pos
    finally:
        window.close()


def test_settings_window_title_drag_stops_on_mouse_release(monkeypatch: pytest.MonkeyPatch) -> None:
    """After mouse release, later title-area move events must not continue dragging."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtCore import QEvent, QPoint
        from PySide6.QtWidgets import QLabel

        dialog = window.window
        title_label = dialog.findChild(QLabel, "dialogTitle")
        assert title_label is not None, "frameless dialog must expose QLabel#dialogTitle as the drag handle"

        dialog.move(QPoint(100, 110))
        assert dialog.eventFilter(title_label, _mouse_event(QEvent.Type.MouseButtonPress, 10, 10, 250, 260)) is True
        assert dialog.eventFilter(title_label, _mouse_event(QEvent.Type.MouseMove, 20, 20, 280, 300)) is True
        moved_pos = dialog.pos()

        assert dialog.eventFilter(title_label, _mouse_event(QEvent.Type.MouseButtonRelease, 20, 20, 280, 300)) is True
        assert getattr(dialog, "_drag_active", None) is False
        assert dialog.eventFilter(title_label, _mouse_event(QEvent.Type.MouseMove, 40, 40, 340, 360)) is False
        assert dialog.pos() == moved_pos
    finally:
        window.close()


def test_settings_window_non_title_content_press_does_not_start_drag(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mouse press on non-title content must not activate window dragging."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtCore import QEvent, QPoint

        dialog = window.window
        dialog.move(QPoint(120, 130))
        start_pos = dialog.pos()

        assert dialog.eventFilter(window.voice_combo, _mouse_event(QEvent.Type.MouseButtonPress, 10, 10, 300, 320)) is False
        assert getattr(dialog, "_drag_active", None) is False
        assert dialog.eventFilter(window.voice_combo, _mouse_event(QEvent.Type.MouseMove, 30, 30, 360, 380)) is False
        assert dialog.pos() == start_pos
    finally:
        window.close()


# ---------------------------------------------------------------------------

# RED contract tests for Host-API-aware audio output device wiring (S1).
# ---------------------------------------------------------------------------


SYSTEM_DEFAULT_LABEL = "系统默认"


def _combo_item_texts(combo: Any) -> list[str]:
    return [combo.itemText(i) for i in range(combo.count())]


def test_settings_window_renders_output_device_combo_with_system_default_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Output device combo must list 系统默认 first followed by Host-API display names."""
    devices = [
        _device_entry("Speakers (Realtek)", "MME"),
        _device_entry("VB-Audio Virtual Cable", "Windows WASAPI"),
    ]
    _app, window = _new_settings_window_with_audio_output(
        monkeypatch,
        audio_output_devices=devices,
        audio_output_device_identity=None,
    )
    try:
        combo = getattr(window, "audio_output_combo", None)
        assert combo is not None, "SettingsWindow must expose audio_output_combo"

        items = _combo_item_texts(combo)
        assert items[0] == SYSTEM_DEFAULT_LABEL, (
            f"first audio output combo item must be '{SYSTEM_DEFAULT_LABEL}', got {items!r}"
        )
        assert items[1:] == [
            "Speakers (Realtek) [MME]",
            "VB-Audio Virtual Cable [Windows WASAPI]",
        ], f"discovered devices must render display_name in order, got {items!r}"
        assert combo.currentText() == SYSTEM_DEFAULT_LABEL
    finally:
        window.close()


def test_settings_window_output_device_selection_propagates_structured_identity_to_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting a display row must update pending settings with structured identity."""
    devices = [
        _device_entry("Speakers (Realtek)", "MME"),
        _device_entry("VB-Audio Virtual Cable", "Windows WASAPI"),
    ]
    _app, window = _new_settings_window_with_audio_output(
        monkeypatch,
        audio_output_devices=devices,
        audio_output_device_identity=None,
    )
    try:
        combo = getattr(window, "audio_output_combo", None)
        assert combo is not None, "SettingsWindow must expose audio_output_combo"

        combo.setCurrentText("VB-Audio Virtual Cable [Windows WASAPI]")
        pending = window.get_pending_settings()
        identity = getattr(pending, "audio_output_device_identity", None)
        assert identity == {
            "name": "VB-Audio Virtual Cable",
            "host_api_name": "Windows WASAPI",
        }, (
            "pending.audio_output_device_identity must carry both name and host_api_name, "
            f"got {identity!r}"
        )

        combo.setCurrentText(SYSTEM_DEFAULT_LABEL)
        pending_default = window.get_pending_settings()
        assert getattr(pending_default, "audio_output_device_identity", "unset") is None, (
            "selecting 系统默认 must clear structured identity to None"
        )
    finally:
        window.close()


def test_settings_window_output_device_combo_preselects_matching_host_api_for_duplicate_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Duplicate raw names must remain distinguishable via host_api_name on restart."""
    devices = [
        _device_entry("Speakers", "MME"),
        _device_entry("Speakers", "Windows WASAPI"),
        _device_entry("Speakers", "Windows DirectSound"),
    ]
    _app, window = _new_settings_window_with_audio_output(
        monkeypatch,
        audio_output_devices=devices,
        audio_output_device_identity={
            "name": "Speakers",
            "host_api_name": "Windows WASAPI",
        },
    )
    try:
        combo = getattr(window, "audio_output_combo", None)
        assert combo is not None, "SettingsWindow must expose audio_output_combo"

        items = _combo_item_texts(combo)
        assert items == [
            SYSTEM_DEFAULT_LABEL,
            "Speakers [MME]",
            "Speakers [Windows WASAPI]",
            "Speakers [Windows DirectSound]",
        ], f"duplicate raw names must each be rendered with their host API tag, got {items!r}"

        assert combo.currentText() == "Speakers [Windows WASAPI]", (
            "stored identity must preselect the WASAPI variant rather than the first match, "
            f"got currentText={combo.currentText()!r}"
        )

        pending = window.get_pending_settings()
        identity = getattr(pending, "audio_output_device_identity", None)
        assert identity == {
            "name": "Speakers",
            "host_api_name": "Windows WASAPI",
        }, (
            "pending identity must round-trip the preselected duplicate-name variant, "
            f"got {identity!r}"
        )
    finally:
        window.close()


def test_settings_window_output_device_combo_falls_back_to_default_when_stored_identity_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When stored identity is not in the discovered list, combo selects 系统默认."""
    devices = [_device_entry("Speakers (Realtek)", "MME")]
    _app, window = _new_settings_window_with_audio_output(
        monkeypatch,
        audio_output_devices=devices,
        audio_output_device_identity={
            "name": "Headphones (Disconnected)",
            "host_api_name": "Windows WASAPI",
        },
    )
    try:
        combo = getattr(window, "audio_output_combo", None)
        assert combo is not None, "SettingsWindow must expose audio_output_combo"

        assert combo.currentText() == SYSTEM_DEFAULT_LABEL, (
            "missing stored identity must fall back to 系统默认 selection"
        )
        pending = window.get_pending_settings()
        assert getattr(pending, "audio_output_device_identity", "unset") is None
    finally:
        window.close()


def test_settings_window_output_devices_error_displays_warning_status_and_only_default_option(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When enumeration errored, combo holds only 系统默认 and status is warning."""
    _app, window = _new_settings_window_with_audio_output(
        monkeypatch,
        audio_output_devices=[],
        audio_output_device_identity=None,
        audio_output_devices_error=RuntimeError("PyAudio init failed"),
    )
    try:
        combo = getattr(window, "audio_output_combo", None)
        status_label = getattr(window, "audio_output_status_label", None)
        assert combo is not None, "SettingsWindow must expose audio_output_combo"
        assert status_label is not None, "SettingsWindow must expose audio_output_status_label"

        items = _combo_item_texts(combo)
        assert items == [SYSTEM_DEFAULT_LABEL], (
            f"error path must leave only 系统默认 in combo, got {items!r}"
        )

        from easy_tts.ui.theme import TEXT_WARNING

        status_text = status_label.text()
        assert "PyAudio init failed" in status_text or "失败" in status_text, (
            f"error status must surface enumeration failure, got {status_text!r}"
        )
        stylesheet = status_label.styleSheet() or ""
        assert TEXT_WARNING in stylesheet, (
            f"audio output status label must use TEXT_WARNING color, got {stylesheet!r}"
        )
    finally:
        window.close()
