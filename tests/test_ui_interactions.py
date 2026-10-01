"""真实 Qt 交互回归：朗读入口、键盘导航和无边框关闭入口。"""

from unittest.mock import Mock
import ctypes
from types import SimpleNamespace

import pytest

from PySide6.QtCore import Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QPushButton

from wordy.ui.overlay import InputOverlay
from wordy.ui.settings import SettingsWindow
from wordy.ui.settings_state import SettingsState


def test_read_keeps_input_unchanged_until_next_action(qapp, monkeypatch):
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    submitted = []
    overlay = InputOverlay(submitted.append)
    overlay._ensure_ui()
    root = overlay.root
    assert root is not None
    root.show()
    # 原生 Windows 不会仅因 show 自动激活；模拟应用显示入口的激活动作。
    root.activateWindow()
    qapp.processEvents()
    root.entry.setFocus()
    qapp.processEvents()
    root.entry.setText("   ")
    assert not root.submit_button.isEnabled()
    root.entry.setText("  你好，Wordy  ")
    assert root.submit_button.isEnabled()
    root.entry.setCursorPosition(4)
    geometry = root.geometry()
    QTest.mouseClick(root.submit_button, Qt.MouseButton.LeftButton)
    assert submitted == ["你好，Wordy"]
    assert root.isVisible()
    assert root.entry.text() == "  你好，Wordy  "
    assert root.entry.cursorPosition() == 4
    assert root.entry.hasFocus()
    assert root.geometry() == geometry
    # UI 保留后仍可继续编辑、再次朗读，并由用户主动收起。
    root.entry.setText("下一句")
    QTest.keyClick(root.entry, Qt.Key.Key_Return)
    assert submitted == ["你好，Wordy", "下一句"]
    assert not root.isVisible()
    overlay.stop()


@pytest.mark.parametrize("key", [Qt.Key.Key_Return, Qt.Key.Key_Enter])
def test_enter_submits_and_hides_but_whitespace_stays_visible(qapp, monkeypatch, key):
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    submitted = []
    overlay = InputOverlay(submitted.append)
    overlay._ensure_ui()
    root = overlay.root
    assert root is not None
    root.show()
    root.entry.setText("   ")
    QTest.keyClick(root.entry, key)
    assert submitted == []
    assert root.isVisible()
    root.entry.setText("  回车提交  ")
    QTest.keyClick(root.entry, key)
    assert submitted == ["回车提交"]
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
    root.activateWindow()
    qapp.processEvents()
    root.entry.setFocus()
    qapp.processEvents()
    QTest.keyClick(root.entry, Qt.Key.Key_Tab)
    assert root.settings_button.hasFocus()
    assert root.isVisible()
    QTest.mouseClick(root.entry, Qt.MouseButton.LeftButton)
    assert root.isVisible()
    assert root.entry.hasFocus()
    QTest.keyClick(root.entry, Qt.Key.Key_Tab)
    QTest.keyClick(root.settings_button, Qt.Key.Key_Return)
    open_settings.assert_called_once()
    # 从可聚焦设置入口切到其他窗口时，不能残留置顶输入栏。
    overlay._ignore_focus_out = False
    root.settings_button.clearFocus()
    assert not root.isVisible()
    overlay.stop()


def test_mouse_settings_click_keeps_overlay_visible(qapp, monkeypatch):
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    open_settings = Mock()
    monkeypatch.setattr(overlay, "_open_settings", open_settings)
    root = overlay.root
    assert root is not None
    root.show()
    root.activateWindow()
    qapp.processEvents()
    root.entry.setFocus()
    qapp.processEvents()
    overlay._ignore_focus_out = False
    QTest.mousePress(root.settings_button, Qt.MouseButton.LeftButton)
    assert root.isVisible()
    open_settings.assert_not_called()
    QTest.mouseRelease(root.settings_button, Qt.MouseButton.LeftButton)
    assert root.isVisible()
    open_settings.assert_called_once()
    overlay.stop()


def test_scaled_cursor_click_on_read_is_not_treated_as_outside(qapp, monkeypatch):
    from ctypes import wintypes

    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    submitted = []
    overlay = InputOverlay(submitted.append)
    overlay._ensure_ui()
    root = overlay.root
    assert root is not None
    root.move(800, 400)
    root.show()
    root.entry.setText("缩放屏幕朗读")
    qapp.processEvents()
    cursor = root.submit_button.mapToGlobal(root.submit_button.rect().center())
    monkeypatch.setattr(QCursor, "pos", staticmethod(lambda: cursor))

    def physical_cursor(point):
        # Win32 物理坐标与 Qt 逻辑坐标在 150% 缩放时不同。
        output = ctypes.cast(point, ctypes.POINTER(wintypes.POINT)).contents
        output.x, output.y = round(cursor.x() * 1.5), round(cursor.y() * 1.5)
        return 1

    monkeypatch.setattr(ctypes.windll, "user32", SimpleNamespace(GetCursorPos=physical_cursor))
    # 契约测试可能重载模块，替换实际方法的引用，避免跨测试模块缓存影响此回归。
    monkeypatch.setitem(overlay._check_outside_click.__globals__, "is_left_button_down", lambda: True)
    overlay._ignore_focus_out = False
    overlay._outside_mouse_down = False
    QTest.mousePress(root.submit_button, Qt.MouseButton.LeftButton)
    overlay._check_outside_click()
    assert root.isVisible()
    QTest.mouseRelease(root.submit_button, Qt.MouseButton.LeftButton)
    assert submitted == ["缩放屏幕朗读"]
    assert root.isVisible()
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
