"""真实 Qt 交互回归：朗读入口、键盘导航和无边框关闭入口。"""

from unittest.mock import Mock

from PySide6.QtCore import Qt
from PySide6.QtGui import QFocusEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QPushButton

from wordy.ui.overlay import InputOverlay
from wordy.ui.settings import SettingsWindow
from wordy.ui.settings_state import SettingsState


def test_read_button_rejects_whitespace_and_submits_trimmed_text(qapp, monkeypatch):
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    submitted = []
    overlay = InputOverlay(submitted.append)
    overlay._ensure_ui()
    root = overlay.root
    assert root is not None
    root.show()
    root.entry.setFocus()
    qapp.processEvents()
    root.entry.setText("   ")
    assert not root.submit_button.isEnabled()
    root.entry.setText("  你好，Wordy  ")
    assert root.submit_button.isEnabled()
    QTest.mouseClick(root.submit_button, Qt.MouseButton.LeftButton)
    assert submitted == ["你好，Wordy"]
    assert not root.isVisible()
    overlay.stop()


def test_tab_to_settings_stays_visible_and_enter_opens_settings(qapp, monkeypatch):
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    open_settings = Mock()
    monkeypatch.setattr(overlay, "_open_settings", open_settings)
    root = overlay.root
    assert root is not None
    root.show()
    root.entry.setFocus()
    qapp.processEvents()
    QTest.keyClick(root.entry, Qt.Key.Key_Tab)
    assert root.settings_button.hasFocus()
    assert root.isVisible()
    QTest.keyClick(root.settings_button, Qt.Key.Key_Return)
    open_settings.assert_called_once()
    # 从可聚焦设置入口切到其他窗口时，不能残留置顶输入栏。
    overlay._ignore_focus_out = False
    QApplication.sendEvent(root.settings_button, QFocusEvent(QFocusEvent.Type.FocusOut, Qt.FocusReason.OtherFocusReason))
    assert not root.isVisible()
    overlay.stop()


def test_title_close_flushes_pending_settings(qapp, fake_keyring, monkeypatch):
    closed = Mock()
    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda field, value: None, closed)
    flush = Mock()
    monkeypatch.setattr(settings._settings, "flush", flush)
    assert settings.window is not None
    close_button = settings.window.findChild(QPushButton, "dialogCloseButton")
    assert close_button is not None
    QTest.mouseClick(close_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    closed.assert_called_once()
    assert flush.called
    assert settings._closed


def test_enter_in_credentials_does_not_activate_title_close(qapp, fake_keyring):
    closed = Mock()
    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda field, value: None, closed)
    assert settings.window is not None
    from PySide6.QtWidgets import QTabWidget
    tabs = settings.window.findChild(QTabWidget, "settingsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(2)
    settings.api_key_input.setFocus()
    qapp.processEvents()
    QTest.keyClick(settings.api_key_input, Qt.Key.Key_Return)
    closed.assert_not_called()
    assert settings.window.isVisible()
    settings.close()
