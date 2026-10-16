"""真实 Qt 交互回归：朗读入口、键盘导航和无边框关闭入口。"""

from unittest.mock import Mock
import ctypes
from types import FunctionType, SimpleNamespace
from typing import cast

import pytest

from PySide6.QtCore import QPoint, QSize, Qt
from PySide6.QtGui import QCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel

from wordy.ui.overlay import InputOverlay
from wordy.ui.settings import SettingsWindow
from wordy.ui.settings_state import SettingsState


def _move_cursor_onto(widget) -> None:
    from PySide6.QtGui import QCursor
    QCursor.setPos(widget.mapToGlobal(widget.rect().center()))


def _settings_icon_is_idle(root) -> bool:
    """设置入口当前是否处于未高亮状态。"""
    from wordy.ui.theme import CONFIG_BUTTON_IDLE
    pixmap = root.settings_button.pixmap()
    if pixmap is None:
        return False
    idle = root._make_settings_icon(root._settings_icon_size, CONFIG_BUTTON_IDLE)
    return pixmap.toImage() == idle.toImage()


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
                # 设置入口是 QLabel（pixmap），朗读入口是 QPushButton（icon）；
                # 用 isinstance 收窄联合类型，`is` 比较不让 pyright 收窄。
                if isinstance(button, QLabel):
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
        # 取样点跟着胶囊走：窗口比胶囊大一圈，投影留白不在里面。
        # 噪点按窗口坐标平铺，取样点平移后落在噪点的另一个相位上，
        # 半透明档的实测值会有几个 alpha 的抖动，所以上限留出余量。
        capsule = root.content_rect()
        probe = capsule.topLeft() + QPoint(200, 5)
        opaque = root.grab().toImage().pixelColor(probe).alpha()
        settings.opacity_slider.setValue(0)
        qapp.processEvents()
        translucent = root.grab().toImage().pixelColor(probe).alpha()
        assert opaque == 255
        assert 70 <= translucent <= 115, (opaque, translucent)
        assert root.grab().toImage().pixelColor(capsule.topLeft() + QPoint(300, 30)).alpha() < 110
    finally:
        settings.close()
        overlay.stop()


def test_overlay_shadow_padding_stays_outside_the_hit_area(qapp, monkeypatch):
    """投影留白只用来画阴影：不算窗口内部，按在上面也不触发拖动。"""
    from wordy.ui.overlay_widgets import SHADOW_PAD

    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    root = overlay.root
    assert root is not None
    root.move(300, 200)
    root.show()
    qapp.processEvents()
    try:
        capsule = root.content_rect()
        assert capsule.topLeft() == QPoint(SHADOW_PAD, SHADOW_PAD)
        assert capsule.size() == QSize(overlay.width, overlay.height)

        inside = root.mapToGlobal(capsule.center())
        padding = root.mapToGlobal(QPoint(SHADOW_PAD // 2, SHADOW_PAD // 2))
        assert root.contains_global_point(inside.x(), inside.y())
        assert not root.contains_global_point(padding.x(), padding.y())
        assert overlay._is_point_in_tracked_windows(inside.x(), inside.y())
        assert not overlay._is_point_in_tracked_windows(padding.x(), padding.y())

        # 留白区的按下不进入拖动状态，否则那圈看不见的区域也能拖着窗口走。
        QTest.mousePress(root, Qt.MouseButton.LeftButton, pos=QPoint(SHADOW_PAD // 2, SHADOW_PAD // 2))
        assert not overlay._is_dragging_window
        QTest.mouseRelease(root, Qt.MouseButton.LeftButton, pos=QPoint(SHADOW_PAD // 2, SHADOW_PAD // 2))
    finally:
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


def _settings_button_overlay(monkeypatch, qapp):
    """建一个已显示、未真正创建设置窗口的输入框。"""
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    monkeypatch.setattr(overlay, "_create_settings_window", lambda: None)
    root = overlay.root
    assert root is not None
    root.show()
    qapp.processEvents()
    return overlay, root


def test_opening_settings_clears_the_settings_button_highlight(qapp, monkeypatch):
    """点开设置后设置入口必须立刻回到未高亮状态。

    高亮只靠 Leave 事件收回，而鼠标停在齿轮上时那个事件不会来——设置窗口只是
    被置顶盖住，光标没离开输入框——图标就会一直亮着。
    """
    overlay, root = _settings_button_overlay(monkeypatch, qapp)
    try:
        root.set_settings_hover(True)
        assert not _settings_icon_is_idle(root), "前置条件：设置入口应处于高亮"

        overlay._open_settings()

        assert _settings_icon_is_idle(root), "进入设置后设置入口仍在高亮"
    finally:
        overlay.stop()


def test_hiding_overlay_clears_the_settings_button_highlight(qapp, monkeypatch):
    """收起输入框也要收掉高亮：隐藏时不会再有 Leave，留着下次显示还是亮的。"""
    overlay, root = _settings_button_overlay(monkeypatch, qapp)
    try:
        root.set_settings_hover(True)
        assert not _settings_icon_is_idle(root)

        overlay.hide()

        assert _settings_icon_is_idle(root), "收起输入框后设置入口仍在高亮"
    finally:
        overlay.stop()


def test_enter_does_not_highlight_while_settings_window_is_open(qapp, monkeypatch):
    """设置窗口开着时输入框不再是交互面，即使光标还停在齿轮上也不该点亮。

    层叠顺序变化时 Qt 会补发没有配对的 Enter，照单全收会让图标一直亮着。
    """
    from PySide6.QtCore import QEvent

    overlay, root = _settings_button_overlay(monkeypatch, qapp)
    try:
        monkeypatch.setattr(overlay, "_active_settings_window", lambda: object())
        _move_cursor_onto(root.settings_button)

        qapp.sendEvent(root.settings_button, QEvent(QEvent.Type.Enter))

        assert _settings_icon_is_idle(root), "设置窗口已打开时不应被 Enter 点亮"
    finally:
        overlay.stop()


def test_enter_highlights_the_settings_button_while_settings_is_closed(qapp, monkeypatch):
    """设置窗口没开时，真实进入事件必须点亮图标——这是 hover 高亮的唯一来源。"""
    from PySide6.QtCore import QEvent

    overlay, root = _settings_button_overlay(monkeypatch, qapp)
    try:
        assert overlay._active_settings_window() is None

        qapp.sendEvent(root.settings_button, QEvent(QEvent.Type.Enter))

        assert not _settings_icon_is_idle(root), "悬停设置入口时必须高亮"
    finally:
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
    monkeypatch.setitem(cast(FunctionType, overlay._check_outside_click).__globals__, "is_left_button_down", lambda: True)
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
    # 自绘关闭按钮通过相同的 Qt closeEvent 保存待写入设置。
    from PySide6.QtWidgets import QPushButton

    close_button = settings.window.findChild(QPushButton, "dialogCloseButton")
    assert close_button is not None
    close_button.click()
    qapp.processEvents()
    closed.assert_called_once()
    flush.assert_called_once()
    assert settings._closed


def test_enter_in_credentials_does_not_activate_title_close(qapp, fake_keyring):
    closed = Mock()
    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda field, value: None, closed)
    assert settings.window is not None
    from PySide6.QtWidgets import QLineEdit, QTabWidget
    tabs = settings.window.findChild(QTabWidget, "settingsTabs")
    assert tabs is not None
    tabs.setCurrentIndex(2)
    api_key_input: QLineEdit | None = settings.api_key_input
    assert api_key_input is not None
    api_key_input.setFocus()
    qapp.processEvents()
    QTest.keyClick(api_key_input, Qt.Key.Key_Return)
    closed.assert_not_called()
    assert settings.window.isVisible()
    settings.close()


def test_placeholder_updates_live_and_persists_on_close(qapp, monkeypatch):
    import wordy.config as config

    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    entry = overlay.entry
    assert entry is not None
    settings = SettingsWindow(overlay.root, SettingsState(), lambda: None, lambda: None,
                              overlay._on_settings_field_changed, lambda: None)
    assert entry.placeholderText() == "Hello World~"
    settings.placeholder_input.setText("写下你想说的话")
    assert entry.placeholderText() == "写下你想说的话"
    settings.close()
    # 清除单例后读取磁盘，证明重启也会保留用户提示文字。
    monkeypatch.setattr(config, "_cached_settings", None)
    assert config.AppSettings.load().overlay_placeholder == "写下你想说的话"
    settings = SettingsWindow(overlay.root, SettingsState(), lambda: None, lambda: None,
                              overlay._on_settings_field_changed, lambda: None)
    settings.placeholder_input.clear()
    assert entry.placeholderText() == ""
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
    window = settings.window
    assert window is not None
    visible_during_flush = []
    flush = Mock(side_effect=lambda: visible_during_flush.append(window.isVisible()))
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
    driver = cast(FunctionType, overlay._create_settings_window).__globals__["VBCableDriverManager"]
    monkeypatch.setattr(driver, "is_installed", lambda: False)
    try:
        overlay._open_settings()
        settings = overlay._settings_window
        assert settings is not None
        window = settings.window
        assert window is not None and window.isVisible()
        scan.assert_not_called()
        tabs = window.findChild(QTabWidget, "settingsTabs")
        assert tabs is not None
        tabs.setCurrentIndex(1)
        # 设备枚举延后到页签首帧之后，避免阻塞切换。
        QTest.qWait(settings.AUDIO_SCAN_DELAY_MS + 20)
        scan.assert_called_once()
        assert settings.audio_output_combo.findText("测试输出") >= 0
        tabs.setCurrentIndex(0)
        tabs.setCurrentIndex(1)
        scan.assert_called_once()
        settings.close()
    finally:
        overlay.stop()


def test_prewarm_builds_settings_tree_without_showing_it(qapp, fake_keyring):
    """预热只建控件树，绝不把设置窗口显示出来。"""
    settings = SettingsWindow(
        None,
        SettingsState(),
        lambda: None,
        lambda: None,
        lambda field, value: None,
        lambda: None,
        prewarm=True,
    )
    try:
        assert settings.title_bar is not None, "预热必须真的建出控件树，否则首次点击省不下成本"
        window = settings.window
        assert window is not None
        assert not window.isVisible(), "预热不能把设置窗口显示出来"
    finally:
        settings.close()


def test_prewarm_window_is_discarded_without_writing_config(qapp, fake_keyring, monkeypatch):
    """丢弃预热实例要走正常关闭路径，但不该为一次没改过配置的丢弃落盘。"""
    from wordy.config import AppSettings

    flush = Mock()
    monkeypatch.setattr(AppSettings.load(), "flush", flush)
    settings = SettingsWindow(
        None,
        SettingsState(),
        lambda: None,
        lambda: None,
        lambda field, value: None,
        lambda: None,
        prewarm=True,
    )
    settings.close()
    flush.assert_not_called()


def test_prewarm_settings_window_leaves_no_open_window(qapp, fake_keyring, monkeypatch):
    """预热实例必须被丢弃：留着会让下一次打开读到过期的设备与音色状态。"""
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    monkeypatch.setattr(overlay, "_enumerate_input_devices", lambda: [])
    driver = cast(FunctionType, overlay._create_settings_window).__globals__["VBCableDriverManager"]
    monkeypatch.setattr(driver, "is_installed", lambda: False)
    try:
        overlay.prewarm_settings_window()

        assert overlay._active_settings_window() is None, "预热实例没有被丢弃"
        overlay._open_settings()
        settings = overlay._active_settings_window()
        assert settings is not None
        window = settings.window
        assert window is not None and window.isVisible()
        settings.close()
    finally:
        overlay.stop()


def test_prewarm_settings_window_skips_while_overlay_is_open(qapp, fake_keyring, monkeypatch):
    """输入框正显示时不插入这段一次性成本，改为稍后重试。"""
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    root = overlay.root
    assert root is not None
    create = Mock()
    monkeypatch.setattr(overlay, "_create_settings_window", create)
    retry = Mock()
    monkeypatch.setattr("wordy.ui.overlay.QTimer.singleShot", retry)
    try:
        root.show()
        qapp.processEvents()

        overlay.prewarm_settings_window()

        create.assert_not_called()
        retry.assert_called_once()
    finally:
        overlay.stop()


def test_ui_font_change_restyles_the_overlay_immediately(qapp, fake_keyring, monkeypatch):
    """设置页换字体后，悬浮输入框的文字要当场跟着换。"""
    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._ensure_ui()
    root = overlay.root
    assert root is not None
    try:
        assert '"Consolas"' not in root.entry.styleSheet()

        overlay._on_settings_field_changed("ui_font", "Consolas")

        for widget in (root.entry, root.prompt_label, root.submit_button):
            assert '"Consolas"' in widget.styleSheet()
    finally:
        overlay.stop()


def test_ui_font_selection_restyles_the_settings_window_and_persists(qapp, fake_keyring):
    """在设置页选字体，本窗口当场换字体，并写进配置。"""
    from PySide6.QtGui import QFont

    from wordy.config import AppSettings
    from wordy.ui.theme import ui_font_stack

    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda field, value: None, lambda: None)
    try:
        window = settings.window
        assert window is not None
        settings.ui_font_combo.setCurrentFont(QFont("Consolas"))
        # offscreen 平台没有字体库，族名会被替换；断言机制而非具体族名：
        # 下拉框当前值、配置、样式表三者必须一致。
        chosen = settings.ui_font_combo.currentFont().family()

        assert AppSettings.load().ui_font == chosen
        assert f"font-family: {ui_font_stack(chosen)};" in window.styleSheet()
    finally:
        settings.close()


def test_saved_font_missing_on_this_machine_falls_back_with_a_notice(qapp, fake_keyring):
    """存的族本机没装：窗口照常构建、选中族仍排在最前、提示说明已回退。"""
    from wordy.config import AppSettings

    missing = "一个一定不存在的字体族"
    AppSettings.load().update(ui_font=missing)
    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda field, value: None, lambda: None)
    try:
        window = settings.window
        assert window is not None
        assert f'"{missing}"' in window.styleSheet()
        assert "回退" in settings.ui_font_hint.text()
        assert missing in settings.ui_font_hint.text()
    finally:
        settings.close()


def test_ui_font_picker_excludes_bitmap_legacy_families(qapp, fake_keyring):
    """字体列表不能带点阵遗留字体。

    Qt 为这类族建 DirectWrite 字面必然失败并往控制台刷
    "CreateFontFaceFromHDC() failed"，而 QFontComboBox 默认用每个族自己的
    字面渲染列表项——滚一下弹窗就刷一屏。它们本身也不适合当界面字体。
    """
    from PySide6.QtGui import QFontDatabase
    from PySide6.QtWidgets import QFontComboBox

    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda field, value: None, lambda: None)
    try:
        combo = settings.ui_font_combo
        assert combo.fontFilters() == QFontComboBox.FontFilter.ScalableFonts

        bitmap = {f for f in QFontDatabase.families() if not QFontDatabase.isSmoothlyScalable(f)}
        listed = {combo.itemText(i) for i in range(combo.count())}
        assert not (listed & bitmap), f"列表里混进了点阵字体: {sorted(listed & bitmap)}"
    finally:
        settings.close()


def test_font_picker_declares_a_point_size(qapp, fake_keyring):
    """字体下拉框必须声明点值字号。

    QFontComboBox 弹窗内部会拿当前字体的 pointSize 重设字号；设置页全局 QSS 用的
    是 px，pointSize() 返回 -1，Qt 就会打 "Point size <= 0 (-1)"。给它一条自己的
    规则、按当前 DPI 从基准 px 折算成 pt：警告消失，渲染尺寸不变。
    """
    from wordy.ui.settings_style import BASE_FONT_PX

    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda field, value: None, lambda: None)
    try:
        window = settings.window
        assert window is not None
        expected = BASE_FONT_PX * 72.0 / max(1.0, float(window.logicalDpiY()))

        assert f"font-size: {expected:.2f}pt;" in settings.ui_font_combo.styleSheet()
    finally:
        settings.close()


def test_settings_tab_switch_has_no_fade_and_defers_audio_scan(qapp, fake_keyring):
    """页签切换必须是瞬时的，设备枚举仍延后执行。"""
    scan = Mock()
    settings = SettingsWindow(
        None,
        SettingsState(),
        lambda: None,
        lambda: None,
        lambda field, value: None,
        lambda: None,
        on_audio_outputs_needed=scan,
    )
    try:
        from PySide6.QtWidgets import QTabWidget

        window = settings.window
        assert window is not None
        tabs = window.findChild(QTabWidget, "settingsTabs")
        assert tabs is not None
        tabs.setCurrentIndex(1)

        page = tabs.widget(1)
        assert page is not None
        assert page.graphicsEffect() is None, "切页不应给新页挂图形效果"
        scan.assert_not_called()

        QTest.qWait(settings.AUDIO_SCAN_DELAY_MS + 20)
        scan.assert_called_once()
    finally:
        settings.close()


def test_settings_tab_switch_renders_content_at_full_opacity_immediately(qapp, fake_keyring):
    """切换页签后首帧必须已是最终画面。

    新页被压暗再亮回来，看起来就是内容「跳一下」：整页亮度先掉一档，
    导航栏却不变，视觉上像闪了一下。
    """
    settings = SettingsWindow(
        None,
        SettingsState(),
        lambda: None,
        lambda: None,
        lambda field, value: None,
        lambda: None,
    )
    try:
        from PySide6.QtWidgets import QTabWidget

        window = settings.window
        assert window is not None
        tabs = window.findChild(QTabWidget, "settingsTabs")
        assert tabs is not None

        tabs.setCurrentIndex(2)
        QTest.qWait(settings.AUDIO_SCAN_DELAY_MS + 20)
        settled = window.grab().toImage()

        tabs.setCurrentIndex(0)
        QTest.qWait(settings.AUDIO_SCAN_DELAY_MS + 20)
        tabs.setCurrentIndex(2)
        immediate = window.grab().toImage()

        assert immediate == settled, "切页首帧必须与稳定后一致，否则内容会闪一下"
    finally:
        settings.close()


@pytest.mark.parametrize("provider", ["Cartesia", "Volcengine"])
def test_settings_click_keeps_windows_visible_without_native_focus_reentry(qapp, monkeypatch, provider):
    from PySide6.QtCore import QEvent, QObject

    monkeypatch.setattr(InputOverlay, "_start_load_voices", lambda *args, **kwargs: None)
    overlay = InputOverlay(lambda text: None)
    overlay._cfg.active_tts_provider = provider
    overlay._ensure_ui()
    monkeypatch.setattr(overlay, "_enumerate_input_devices", lambda: [])
    driver = cast(FunctionType, overlay._create_settings_window).__globals__["VBCableDriverManager"]
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
    assert root is not None
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
        window = settings.window
        assert window is not None
        # 玻璃底由窗口自绘，因此走透明合成；但对用户可见时必须完全不透明——
        # 不能等到子面板首帧才出现底色。
        assert window.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        assert window.windowOpacity() == 1.0
        initial_geometry = window.geometry()
        qapp.processEvents()
        assert window.geometry() == initial_geometry
        assert root.isVisible() and window.isVisible()
        if qapp.platformName() == "windows":
            assert window.isActiveWindow()
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
    assert window is not None
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


def test_frameless_window_can_maximize_minimize_restore_and_close(qapp):
    closed = Mock()
    settings = SettingsWindow(None, SettingsState(), lambda: None, lambda: None,
                              lambda *args: None, closed)
    window = settings.window
    assert window is not None
    try:
        # 无边框窗口自行绘制三个控制按钮，不再有原生样式位可断言；
        # 这里守住的是三种窗口状态与关闭回调在自绘 chrome 下仍然成立。
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
