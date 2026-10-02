#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PySide6 contract tests for SettingsWindow immediate settings behavior."""

from __future__ import annotations

import os
from unittest.mock import Mock
from typing import Any

import pytest

_ = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_ = pytest.importorskip("PySide6")


# 共享 QApplication；conftest 隔离配置与密钥，真实 API 错误必须失败。
pytestmark = pytest.mark.usefixtures("qapp", "fake_keyring")


def _new_settings_window(
    monkeypatch: pytest.MonkeyPatch,
    on_close=None,
    **state_overrides: Any,
):
    from PySide6.QtWidgets import QApplication
    from wordy.config import AppSettings
    from wordy.ui.settings import SettingsWindow
    from wordy.ui.settings_state import SettingsState

    settings = AppSettings.load()
    settings.update(hotkey="f6", name="F6", active_tts_provider="Cartesia",
                    cartesia_voice_id="voice-a", cartesia_voice_name="Old Voice",
                    cartesia_tts_backend="Cartesia Bytes", audio_routing_enabled=False)
    # 持久化选择属于 config，运行时枚举属于 SettingsState。
    selected_identity = state_overrides.pop("audio_output_device_identity", None)
    selected_name = state_overrides.pop("audio_output_device_name", None)
    settings.update(audio_output_device=selected_identity, audio_output_device_name=selected_name)
    state = SettingsState(voices_cache=[{"id": "voice-a", "name": "Old Voice"}], **state_overrides)
    app = QApplication.instance()
    assert app is not None
    window = SettingsWindow(
        None, state, on_record_hotkey=lambda: None, on_refresh_voices=lambda: None,
        on_field_changed=Mock(), on_close=on_close or (lambda: None),
    )
    # 构建阶段同步音频输出；后续断言只记录受测交互触发的通知。
    window.on_field_changed.reset_mock()
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
    return _new_settings_window(
        monkeypatch, audio_output_devices=audio_output_devices,
        audio_output_device_identity=audio_output_device_identity,
        audio_output_devices_error=audio_output_devices_error,
    )


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
    import wordy.secret
    wordy.secret.save_cartesia_api_key(SENTINEL_EXISTING_API_KEY)
    _app, window = _new_settings_window(
        monkeypatch,
        cartesia_api_key_saved=True,
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
        assert "新密钥" in placeholder
        assert "已保存" in "\n".join(_label_texts(window))

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
        assert "粘贴密钥" in placeholder
        assert "未保存" in "\n".join(_label_texts(window))
        assert "sk_" not in placeholder
    finally:
        window.close()


def test_settings_window_api_key_save_normalizes_env_assignment_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _app, window = _new_settings_window(monkeypatch, cartesia_api_key_saved=False)
    try:
        api_key_input = getattr(window, "api_key_input", None)
        assert api_key_input is not None, "SettingsWindow must expose masked api_key_input"

        api_key_input.setText("CARTESIA_API_KEY=sk_test_NEW")
        from wordy.ui.tts_panels import PROVIDER_PANELS
        import wordy.secret
        PROVIDER_PANELS["Cartesia"].save_key_btn.click()
        assert wordy.secret.load_cartesia_api_key() == "sk_test_NEW"
        assert api_key_input.text() == ""
        window.on_field_changed.assert_called_once_with("cartesia_api_key", "sk_test_NEW")
    finally:
        window.close()


def test_settings_window_blank_api_key_save_keeps_existing_key_unchanged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wordy.secret
    wordy.secret.save_cartesia_api_key(SENTINEL_EXISTING_API_KEY)
    _app, window = _new_settings_window(monkeypatch, cartesia_api_key_saved=True)
    try:
        api_key_input = getattr(window, "api_key_input", None)
        assert api_key_input is not None, "SettingsWindow must expose masked api_key_input"

        api_key_input.setText("   ")
        from wordy.ui.tts_panels import PROVIDER_PANELS
        PROVIDER_PANELS["Cartesia"].save_key_btn.click()
        assert wordy.secret.load_cartesia_api_key() == SENTINEL_EXISTING_API_KEY
        window.on_field_changed.assert_not_called()
    finally:
        window.close()


def test_settings_window_clear_api_key_control_deletes_key_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wordy.secret
    wordy.secret.save_cartesia_api_key(SENTINEL_EXISTING_API_KEY)
    _app, window = _new_settings_window(monkeypatch, cartesia_api_key_saved=True)
    try:
        clear_control = getattr(window, "clear_api_key_button", None)
        assert clear_control is not None, "SettingsWindow must expose explicit clear_api_key_button"

        clear_control.click()
        assert wordy.secret.load_cartesia_api_key() is None
        assert window._settings.cartesia_api_key_set is False
        window.on_field_changed.assert_called_once_with("cartesia_api_key", None)
    finally:
        window.close()


def test_settings_window_changes_update_live_settings_and_flush_on_close(monkeypatch: pytest.MonkeyPatch) -> None:
    _app, window = _new_settings_window(monkeypatch)
    try:
        labels = window.set_voices_loaded(
            [
                {"id": "voice-a", "name": "Old Voice"},
                {"id": "voice-b", "name": "Refreshed Voice"},
            ],
            "voice-a",
        )
        assert "Refreshed Voice" in labels
        window.voice_combo.setCurrentText("Refreshed Voice")
        window.tts_backend_combo.setCurrentText("Cartesia Realtime")
        window.volume_slider.setValue(window._volume_to_slider(1.35))
        window.fixed_center_check.setChecked(False)

        settings = window._settings
        assert settings.hotkey == "f6"
        assert settings.name == "F6"
        assert settings.cartesia_voice_id == "voice-b"
        assert settings.cartesia_voice_name == "Refreshed Voice"
        assert settings.volume == 1.35
        assert settings.tts_backend == "Cartesia Realtime"
        assert settings.fixed_center is False
        window.on_field_changed.assert_any_call("cartesia_voice_id", "voice-b")
        window.on_field_changed.assert_any_call("volume", 1.35)
        # 关闭须 flush 滑块 debounce，并保留即时生效的其他设置。
        window.close()
        import tomllib
        from wordy.config import USER_CONFIG_FILE
        saved = tomllib.loads(USER_CONFIG_FILE.read_text(encoding="utf-8"))
        assert saved["volume"] == 1.35
        assert saved["tts_providers"]["Cartesia"]["voice_id"] == "voice-b"
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

        assert window.record_button.isEnabled() is True
        assert window.record_button.text() == "重新录制"
        assert window._recorded_hotkey == "f7"
        assert window._recorded_hotkey_name == "F7"
        window.on_field_changed.assert_called_once_with("hotkey", "f7")
        assert window.pending_label.text() == "当前：F7"
    finally:
        window.close()


def test_settings_window_escape_during_recording_is_accepted_without_closing(monkeypatch: pytest.MonkeyPatch) -> None:
    close_calls = []
    _app, window = _new_settings_window(monkeypatch, on_close=lambda: close_calls.append("closed"))
    try:
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

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
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

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
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent
        from PySide6.QtWidgets import QPushButton

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
        from PySide6.QtCore import QEvent, Qt
        from PySide6.QtGui import QKeyEvent

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



def test_settings_window_exposes_inline_status_api(monkeypatch: pytest.MonkeyPatch) -> None:
    """当前状态接口支持非模态反馈与清空。"""
    _app, window = _new_settings_window(monkeypatch)
    try:
        window.set_status("设置已应用")
        status_label = window.status_label
        assert "设置已应用" in status_label.text()
        assert status_label.isVisible() is True

        window.set_status("")
        assert status_label.text() == ""
    finally:
        window.close()


def test_settings_window_uses_native_controls_and_preserves_topmost(monkeypatch: pytest.MonkeyPatch) -> None:
    """原生窗口提供最小化、最大化和关闭，同时保留置顶行为。"""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtCore import Qt

        flags = window.window.windowFlags()
        assert window.window.windowType() == Qt.WindowType.Window
        assert not flags & Qt.WindowType.FramelessWindowHint
        controls = Qt.WindowType.WindowMinMaxButtonsHint | Qt.WindowType.WindowCloseButtonHint
        assert flags & controls == controls
        assert flags & Qt.WindowType.WindowStaysOnTopHint, "settings window must preserve WindowStaysOnTopHint"
    finally:
        window.close()


def test_settings_window_native_title_replaces_internal_chrome(monkeypatch: pytest.MonkeyPatch) -> None:
    """标题和关闭由系统提供，内容区不重复绘制标题栏。"""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtWidgets import QLabel, QPushButton

        window.window.show()
        _app.processEvents()
        assert window.window.windowTitle() == "设置"
        assert window.window.findChild(QLabel, "dialogTitle") is None
        assert window.window.findChild(QPushButton, "dialogCloseButton") is None
    finally:
        window.close()


def test_settings_window_has_opaque_background_for_first_frame(monkeypatch: pytest.MonkeyPatch) -> None:
    """Settings must have a first-frame background rather than transparent composition."""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtCore import Qt

        assert window.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground) is False
        assert window.window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground) is False
    finally:
        window.close()


def test_settings_window_native_resize_needs_no_size_grip(monkeypatch: pytest.MonkeyPatch) -> None:
    """原生边框承担缩放，内容区不再放置重复的尺寸手柄。"""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtWidgets import QSizeGrip

        window.window.show()
        _app.processEvents()
        assert window.window.isSizeGripEnabled() is False
        assert window.window.findChild(QSizeGrip) is None
    finally:
        window.close()


def test_settings_window_native_chrome_preserves_dialog_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """原生标题栏替换不影响深色内容面板。"""
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


def test_settings_compact_width_keeps_navigation_and_forms_visible(monkeypatch: pytest.MonkeyPatch) -> None:
    """收窄窗口后，两种服务商与所有标签页都不应产生横向裁切。"""
    app, settings = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtWidgets import QScrollArea, QTabWidget

        dialog = settings.window
        assert dialog.width() == 480
        tabs = dialog.findChild(QTabWidget, "settingsTabs")
        assert tabs is not None
        bar = tabs.tabBar()
        assert not bar.expanding()
        assert bar.tabRect(3).width() < bar.tabRect(0).width()
        dialog.resize(dialog.minimumWidth(), dialog.height())
        # 加载长音色名也不应撑宽表单或把同一行的刷新按钮挤出窗口。
        settings.set_voices_loaded([{"id": "long-voice", "name": "Long Voice Name " * 12}], "long-voice")
        for provider in ("Cartesia", "Volcengine"):
            settings.tts_api_combo.setCurrentText(provider)
            for index in range(tabs.count()):
                tabs.setCurrentIndex(index)
                app.processEvents()
                bar = tabs.tabBar()
                assert bar.rect().contains(bar.tabRect(index))
                page = tabs.widget(index)
                assert isinstance(page, QScrollArea)
                assert page.horizontalScrollBar().maximum() == 0
                assert page.widget().minimumSizeHint().width() <= page.viewport().width()
    finally:
        settings.close()


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


@pytest.mark.parametrize("close_path", ["owner", "native"])
def test_settings_window_close_path_invokes_on_close_once(monkeypatch: pytest.MonkeyPatch, close_path: str) -> None:
    """系统关闭事件和应用关闭入口均只 flush、通知一次。"""
    close_calls: list[str] = []
    _app, window = _new_settings_window(monkeypatch, on_close=lambda: close_calls.append("closed"))

    flush = Mock()
    monkeypatch.setattr(window._settings, "flush", flush)
    close = window.close if close_path == "owner" else window.window.close
    close()
    close()

    flush.assert_called_once()
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


def test_settings_window_native_minimize_and_restore_preserves_content(monkeypatch: pytest.MonkeyPatch) -> None:
    """原生最小化和恢复只改变窗口状态，不触发关闭或丢失输入。"""
    _app, window = _new_settings_window(monkeypatch)
    try:
        dialog = window.window
        window.placeholder_input.setText("继续编辑")
        dialog.showMinimized()
        _app.processEvents()
        assert dialog.isMinimized()
        assert window.exists()
        dialog.showNormal()
        _app.processEvents()
        assert not dialog.isMinimized()
        assert dialog.isVisible()
        assert window.placeholder_input.text() == "继续编辑"
    finally:
        window.close()


def test_settings_window_native_maximize_and_restore_keeps_window_alive(monkeypatch: pytest.MonkeyPatch) -> None:
    """原生最大化和恢复保留设置窗口实例与可见内容。"""
    _app, window = _new_settings_window(monkeypatch)
    try:
        dialog = window.window
        dialog.showMaximized()
        _app.processEvents()
        assert dialog.isMaximized()
        assert window.exists()
        dialog.showNormal()
        _app.processEvents()
        assert not dialog.isMaximized()
        assert dialog.isVisible()
        assert window.window is dialog
    finally:
        window.close()


def test_settings_window_content_press_does_not_start_custom_drag(monkeypatch: pytest.MonkeyPatch) -> None:
    """内容区事件不会启动已移除的自绘窗口拖动。"""
    _app, window = _new_settings_window(monkeypatch)
    try:
        from PySide6.QtCore import QEvent, QPoint

        dialog = window.window
        dialog.move(QPoint(120, 130))
        start_pos = dialog.pos()

        assert dialog.eventFilter(window.voice_combo, _mouse_event(QEvent.Type.MouseButtonPress, 10, 10, 300, 320)) is False
        assert not hasattr(dialog, "_drag_active")
        assert not hasattr(dialog, "_drag_position")
        assert dialog.eventFilter(window.voice_combo, _mouse_event(QEvent.Type.MouseMove, 30, 30, 360, 380)) is False
        assert dialog.pos() == start_pos
    finally:
        window.close()


# ---------------------------------------------------------------------------

# Host-API-aware audio output device contracts.
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


def test_settings_window_output_device_selection_persists_structured_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Selecting a display row must update live settings with structured identity."""
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
        identity = window._settings.audio_output_device
        assert identity == {
            "name": "VB-Audio Virtual Cable",
            "host_api_name": "Windows WASAPI",
        }, (
            "audio_output_device must carry both name and host_api_name, "
            f"got {identity!r}"
        )

        window.on_field_changed.assert_called_once_with("audio_output_device", identity)
        combo.setCurrentText(SYSTEM_DEFAULT_LABEL)
        window.on_field_changed.assert_called_with("audio_output_device", None)
        assert window._settings.audio_output_device is None, (
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

        identity = window._settings.audio_output_device
        assert identity == {
            "name": "Speakers",
            "host_api_name": "Windows WASAPI",
        }, (
            "stored identity must round-trip the preselected duplicate-name variant, "
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
        # 显示回退不等于用户主动切换设备；暂时断开也不能覆盖保存的选择。
        assert window._settings.audio_output_device == {
            "name": "Headphones (Disconnected)", "host_api_name": "Windows WASAPI",
        }
        window.on_field_changed.assert_not_called()
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

        from wordy.ui.theme import TEXT_WARNING

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
