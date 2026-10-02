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


def test_overlay_button_hover_changes_icons_without_background_highlight(qapp, monkeypatch):
    """真实进入/离开事件应给图标着色，按钮背景像素保持不变。"""
    from PySide6.QtCore import QEvent
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    root = overlay.root
    assert root is not None
    try:
        root.entry.setText("hover")
        for button in (root.settings_button, root.submit_button):
            def icon_image():
                if button is root.settings_button:
                    return button.pixmap().toImage()
                return button.icon().pixmap(16, 16).toImage()
            qapp.sendEvent(button, QEvent(QEvent.Type.Leave))
            normal = icon_image()
            background = button.grab().toImage().pixelColor(1, 1)
            qapp.sendEvent(button, QEvent(QEvent.Type.Enter))
            assert icon_image() != normal
            assert button.grab().toImage().pixelColor(1, 1) == background
            qapp.sendEvent(button, QEvent(QEvent.Type.Leave))
            assert icon_image() == normal
            if button is root.submit_button:
                # 先悬停再清空输入也必须重置，不等鼠标移开才取消高亮。
                qapp.sendEvent(button, QEvent(QEvent.Type.Enter))
                for text in ("", "   "):
                    root.entry.setText(text)
                    assert not button.isEnabled()
                    assert icon_image() == normal
                    assert button.cursor().shape() == Qt.CursorShape.ArrowCursor
                    inactive = button.grab().toImage()
                    qapp.sendEvent(button, QEvent(QEvent.Type.Enter))
                    assert icon_image() == normal
                    assert button.grab().toImage() == inactive
    finally:
        overlay.stop()


def test_opacity_slider_changes_rendered_overlay_background(qapp, monkeypatch):
    """验证滑块到实际像素的链路，避免只更新配置而背景仍不透明。"""
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    root = overlay.root
    assert root is not None
    settings = SettingsWindow(root, SettingsState(), lambda: None, lambda: None,
                              overlay._on_settings_field_changed, lambda: None)
    try:
        root.show()
        qapp.processEvents()
        if qapp.platformName() == "windows":
            # Windows 必须使用分层窗口，像素 alpha 才能透出桌面，而非仅离屏绘制透明。
            get_style = ctypes.windll.user32.GetWindowLongPtrW
            get_style.argtypes = [ctypes.c_void_p, ctypes.c_int]
            get_style.restype = ctypes.c_ssize_t
            assert get_style(int(root.winId()), -20) & 0x00080000  # WS_EX_LAYERED
        settings.opacity_slider.setValue(settings.opacity_slider.maximum())
        opaque = root.grab().toImage().pixelColor(200, 5).alpha()
        settings.opacity_slider.setValue(0)
        qapp.processEvents()
        translucent = root.grab().toImage().pixelColor(200, 5).alpha()
        assert opaque == 255
        assert 70 <= translucent <= 100, (opaque, translucent)
        assert root.grab().toImage().pixelColor(300, 30).alpha() < 110
    finally:
        settings.close()
        overlay.stop()


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


def test_native_close_flushes_pending_settings(qapp, fake_keyring, monkeypatch):
    closed = Mock()
    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda field, value: None, closed)
    flush = Mock()
    monkeypatch.setattr(settings._settings, "flush", flush)
    assert settings.window is not None
    # 原生关闭按钮通过相同的 Qt closeEvent 保存待写入设置。
    assert settings.window.windowFlags() & Qt.WindowType.WindowCloseButtonHint
    settings.window.close()
    qapp.processEvents()
    closed.assert_called_once()
    flush.assert_called_once()
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


def test_placeholder_updates_live_and_persists_on_close(qapp, monkeypatch):
    import wordy.config as config

    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    settings = SettingsWindow(overlay.root, SettingsState(), lambda: None, lambda: None,
                              overlay._on_settings_field_changed, lambda: None)
    assert overlay.entry.placeholderText() == "Hello World~"
    settings.placeholder_input.setText("写下你想说的话")
    assert overlay.entry.placeholderText() == "写下你想说的话"
    settings.close()
    # 清除单例后读取磁盘，证明重启也会保留用户提示文字。
    monkeypatch.setattr(config, "_cached_settings", None)
    assert config.AppSettings.load().overlay_placeholder == "写下你想说的话"
    settings = SettingsWindow(overlay.root, SettingsState(), lambda: None, lambda: None,
                              overlay._on_settings_field_changed, lambda: None)
    settings.placeholder_input.clear()
    assert overlay.entry.placeholderText() == ""
    settings.close()
    monkeypatch.setattr(config, "_cached_settings", None)
    assert config.AppSettings.load().overlay_placeholder == ""
    overlay.stop()


def test_settings_open_has_no_output_side_effect_and_close_flushes_once(qapp, monkeypatch):
    from wordy.config import AppSettings

    changed = Mock()
    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              changed, lambda: None)
    changed.assert_not_called()
    assert settings.window is not None
    visible_during_flush = []
    flush = Mock(side_effect=lambda: visible_during_flush.append(settings.window.isVisible()))
    monkeypatch.setattr(AppSettings.load(), "flush", flush)
    settings.close()
    flush.assert_called_once()
    assert visible_during_flush == [False]


def test_output_scan_only_runs_on_first_audio_tab_visit(qapp, monkeypatch):
    from PySide6.QtWidgets import QTabWidget
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    scan = Mock(return_value=(["测试输出"], None))
    monkeypatch.setattr(overlay, "_enumerate_audio_output_devices", scan)
    monkeypatch.setattr(overlay, "_enumerate_input_devices", lambda: [])
    driver = overlay._create_settings_window.__globals__["VBCableDriverManager"]
    monkeypatch.setattr(driver, "is_installed", lambda: False)
    try:
        overlay._open_settings()
        settings = overlay._settings_window
        assert settings is not None and settings.window.isVisible()
        scan.assert_not_called()
        tabs = settings.window.findChild(QTabWidget, "settingsTabs")
        tabs.setCurrentIndex(1)
        scan.assert_called_once()
        assert settings.audio_output_combo.findText("测试输出") >= 0
        tabs.setCurrentIndex(0)
        tabs.setCurrentIndex(1)
        scan.assert_called_once()
        settings.close()
    finally:
        overlay.stop()


@pytest.mark.parametrize("provider", ["Cartesia", "Volcengine"])
def test_settings_click_keeps_windows_visible_without_native_focus_reentry(qapp, monkeypatch, provider):
    from PySide6.QtCore import QEvent, QObject

    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._cfg.active_tts_provider = provider
    overlay._ensure_ui()
    monkeypatch.setattr(overlay, "_enumerate_input_devices", lambda: [])
    driver = overlay._create_settings_window.__globals__["VBCableDriverManager"]
    monkeypatch.setattr(driver, "is_installed", lambda: False)
    native_focus = Mock()
    monkeypatch.setitem(SettingsWindow.lift_and_focus.__globals__, "activate_window", native_focus)
    transitions = []
    real_disable = SettingsWindow.__init__.__globals__["disable_window_transitions"]

    def disable_before_show(window):
        result = real_disable(window)
        transitions.append((window.isVisible(), result))
        return result

    monkeypatch.setitem(SettingsWindow.__init__.__globals__, "disable_window_transitions", disable_before_show)
    hidden = []
    shown_windows = []

    class Watch(QObject):
        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Show and watched.isWidgetType() and watched.isWindow():
                shown_windows.append(watched)
            if event.type() == QEvent.Type.Hide and watched.isWidgetType() and watched.isWindow():
                hidden.append(watched)
            return False

    watcher = Watch()
    root = overlay.root
    root.show()
    root.activateWindow()
    qapp.processEvents()
    # 全局监听必须早于点击，才能捕获构造期间瞬间显示后又隐藏的临时窗口。
    qapp.installEventFilter(watcher)
    try:
        QTest.mouseClick(root.settings_button, Qt.MouseButton.LeftButton)
        settings = overlay._settings_window
        assert settings is not None
        assert shown_windows == [settings.window]
        # 设置外层必须有首帧底色，不能依赖透明合成等到子面板首次绘制。
        assert not settings.window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert not settings.window.testAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        from PySide6.QtGui import QPalette
        assert settings.window.palette().color(QPalette.ColorRole.Window).alpha() == 255
        initial_geometry = settings.window.geometry()
        qapp.processEvents()
        assert settings.window.geometry() == initial_geometry
        assert root.isVisible() and settings.window.isVisible()
        if qapp.platformName() == "windows":
            assert settings.window.isActiveWindow()
            assert transitions == [(False, True)]
        settings.lift_and_focus()
        qapp.processEvents()
        assert hidden == []
        native_focus.assert_not_called()
    finally:
        qapp.removeEventFilter(watcher)
        overlay.stop()


def test_settings_opens_immediately_at_full_opacity(qapp):
    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda *args: None, lambda: None)
    window = settings.window
    geometry = window.geometry()
    try:
        assert window.isVisible()
        assert window.windowOpacity() == 1.0
        settings.lift_and_focus()
        qapp.processEvents()
        assert window.geometry() == geometry
        settings.close()
        assert not window.isVisible()
    finally:
        settings.close()


def test_native_window_can_maximize_minimize_restore_and_close(qapp):
    closed = Mock()
    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda *args: None, closed)
    window = settings.window
    try:
        flags = window.windowFlags()
        assert not flags & Qt.WindowType.FramelessWindowHint
        assert flags & Qt.WindowType.WindowMinimizeButtonHint
        assert flags & Qt.WindowType.WindowMaximizeButtonHint
        assert flags & Qt.WindowType.WindowCloseButtonHint
        if qapp.platformName() == "windows":
            # 验证真正的 Win32 边框也含标题栏、系统菜单与最小/最大化按钮。
            getter = ctypes.windll.user32.GetWindowLongPtrW
            getter.argtypes = [ctypes.c_void_p, ctypes.c_int]
            getter.restype = ctypes.c_ssize_t
            native_style = getter(int(window.winId()), -16)
            for bit in (0x00C00000, 0x00080000, 0x00020000, 0x00010000):
                assert native_style & bit == bit
        window.showMaximized()
        qapp.processEvents()
        assert window.isMaximized()
        window.showMinimized()
        qapp.processEvents()
        assert window.isMinimized()
        settings.lift_and_focus()
        qapp.processEvents()
        assert not window.isMinimized()
        assert window.isMaximized()
        window.showNormal()
        qapp.processEvents()
        assert not window.isMaximized()
        window.close()
        closed.assert_called_once()
    finally:
        settings.close()
