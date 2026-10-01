#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""可配置全局热键 PySide6 输入框。"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeAlias, cast

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox

import wordy.secret
from wordy.config import AppSettings, get_active_config_file
from wordy.hotkey import display_hotkey
from wordy.tts.constants import (
    TTS_API_PROVIDER_CARTESIA,
    TTS_API_PROVIDER_VOLCENGINE,
    TTS_BACKEND_VOLCENGINE_STREAMING,
    TTS_BACKENDS_BY_PROVIDER,
)
from wordy.audio.capture import list_input_devices
from wordy.identity import normalize_identity
from wordy.audio.driver import VBCableDriverManager
from wordy.ui.settings_state import SettingsState
from wordy.ui.settings import SettingsWindow
from wordy.ui.overlay_widgets import _OverlaySignals, _OverlayWidget
from wordy.ui.window import activate_window, center_window, clamp_window_position, get_cursor_position, is_left_button_down, is_point_in_widget

from wordy.qt_lifecycle import safe_qt_call

if TYPE_CHECKING:
    from wordy.hotkey import NativeHotkeyListener

logger = logging.getLogger(__name__)

# 仅 InputOverlay 自身使用的常量
OUTSIDE_CLICK_INTERVAL_MS = 80
FOCUS_CLEAR_DELAY_MS = 260
SETTINGS_IGNORE_FOCUS_DELAY_MS = 300
VoiceInfo: TypeAlias = dict[str, object]


def normalize_input_text(text: str) -> str:
    """归一化用户输入文本。"""
    return text.strip()


# _OverlaySignals / _OverlayWidget 定义在 overlay_widgets.py，此处导入使用。


class InputOverlay:
    """全局热键弹出的无边框置顶输入框。"""

    def __init__(
        self,
        on_submit: Callable[[str], None],
        on_voice_change: Callable[[str, str], None] | None = None,
        on_volume_change: Callable[[float], None] | None = None,
        on_tts_backend_change: Callable[[str], None] | None = None,
        on_fetch_voices: Callable[[], list[VoiceInfo]] | None = None,
        on_audio_output_change: Callable[[object], None] | None = None,
        on_cartesia_api_key_change: Callable[[str | None], None] | None = None,
        on_tts_api_provider_change: Callable[[str], None] | None = None,
        on_volcengine_credentials_change: Callable[[str | None], None] | None = None,
        on_audio_route_change: Callable[[dict[str, object]], None] | None = None,
        on_sidetone_change: Callable[[bool], None] | None = None,
        on_query_mic_listen_status: Callable[[], bool] | None = None,
        audio_player: object | None = None,
        width: int = 540,
        height: int = 58,
        poll_interval_ms: int = 30,
    ):
        if sys.platform != "win32":
            raise RuntimeError("InputOverlay 仅支持 Windows")

        self._cfg = AppSettings.load()  # 单例——唯一数据源，不镜像副本
        self.on_submit = on_submit
        self.on_voice_change = on_voice_change
        self.on_volume_change = on_volume_change
        self.on_tts_backend_change = on_tts_backend_change
        self.on_fetch_voices = on_fetch_voices
        self.on_audio_output_change = on_audio_output_change
        self.on_cartesia_api_key_change = on_cartesia_api_key_change
        self.on_tts_api_provider_change = on_tts_api_provider_change
        self.on_volcengine_credentials_change = on_volcengine_credentials_change
        self.on_audio_route_change = on_audio_route_change
        self.on_sidetone_change = on_sidetone_change
        self.on_query_mic_listen_status = on_query_mic_listen_status
        self._audio_player = audio_player
        self.width = width
        self.height = height
        self.poll_interval_ms = poll_interval_ms
        self._closed = False
        self._hotkey_listener: NativeHotkeyListener | None = None
        self._settings_window: SettingsWindow | None = None
        self._ignore_focus_out = False
        self._recording_hotkey = False
        # 按 provider 独立的音色缓存
        self._cartesia_voices_cache: list[VoiceInfo] = []
        self._volcengine_voices_cache: list[VoiceInfo] = []
        self._voices_loading = False
        self._voices_generation = 0
        self._voice_fetch_error: Exception | None = None
        self._voices_started = False
        self._outside_click_watcher_running = False
        self._outside_mouse_down = False
        self._drag_start_mouse_x = 0
        self._drag_start_mouse_y = 0
        self._drag_start_window_x = 0
        self._drag_start_window_y = 0
        self._is_dragging_window = False
        self._last_saved_config_file = get_active_config_file()
        self._app: QApplication | None = None
        self._owns_app = False
        self._signals: _OverlaySignals | None = None
        self.root: _OverlayWidget | None = None
        self.entry: QLineEdit | None = None
        self.settings_icon: QLabel | None = None
        self._outside_click_timer: QTimer | None = None
        self._pre_stop_hook: Callable[[], None] | None = None
        self._event_loop_started = False

    # ── 按当前 provider 路由音色/backend 状态 ──────────────────────────

    def _get_active_voice_id(self) -> str | None:
        """返回当前服务商的音色 ID。"""
        if self._cfg.active_tts_provider == TTS_API_PROVIDER_VOLCENGINE:
            return self._cfg.volcengine_voice_id
        return self._cfg.cartesia_voice_id

    def _get_active_voice_name(self) -> str | None:
        """返回当前服务商的音色名称。"""
        if self._cfg.active_tts_provider == TTS_API_PROVIDER_VOLCENGINE:
            return self._cfg.volcengine_voice_name
        return self._cfg.cartesia_voice_name

    def _get_active_tts_backend(self) -> str:
        """返回当前服务商的默认生成模式。"""
        if self._cfg.active_tts_provider == TTS_API_PROVIDER_VOLCENGINE:
            return self._cfg.volcengine_tts_backend
        return self._cfg.cartesia_tts_backend

    def _get_active_voices_cache(self) -> list[VoiceInfo]:
        """返回当前服务商的音色缓存。"""
        if self._cfg.active_tts_provider == TTS_API_PROVIDER_VOLCENGINE:
            return self._volcengine_voices_cache
        return self._cartesia_voices_cache


    def _set_active_voices_cache(self, voices: list[VoiceInfo]) -> None:
        """设置当前服务商的音色缓存。"""
        if self._cfg.active_tts_provider == TTS_API_PROVIDER_VOLCENGINE:
            self._volcengine_voices_cache = voices
        else:
            self._cartesia_voices_cache = voices

    def prepare_ui(self) -> None:
        """Ensure UI is created (idempotent)."""
        self._ensure_ui()

    @property
    def qt_app(self) -> QApplication | None:
        """Return the QApplication instance used by this overlay."""
        return self._app

    def set_pre_stop_hook(self, hook: Callable[[], None] | None) -> None:
        """Set a hook to be called exactly once during stop(), before root is destroyed."""
        self._pre_stop_hook = hook

    def run(self) -> None:
        """启动热键监听并进入 Qt 事件循环。"""
        self._ensure_ui()
        if self._try_register_startup_hotkey():
            logger.info("已启动输入框监听，按 %s 弹出输入框。", self._cfg.name)
        logger.warning("提示：独占全屏游戏不保证能显示置顶窗口，请优先使用无边框窗口化。")
        try:
            if self._app is not None:
                self._event_loop_started = True
                self._app.exec()
        finally:
            self._closed = True
            self._unregister_hotkey()

    def stop(self) -> None:
        """注销热键并关闭窗口。"""
        if self._closed:
            return
        self._closed = True
        self._unregister_hotkey()
        hook = self._pre_stop_hook
        self._pre_stop_hook = None
        if hook is not None:
            hook()
        self._stop_outside_click_watcher()
        if self._settings_window is not None:
            self._settings_window.close()
            self._settings_window = None
        if self._outside_click_timer is not None:
            self._outside_click_timer.stop()
            self._outside_click_timer = None

        app = self._app
        owns_app = self._owns_app
        signals = self._signals
        entry = self.entry
        settings_icon = self.settings_icon

        def _finalize() -> None:
            # Detach event filters from helper widgets before tearing them
            # down so the parentless _OverlaySignals QObject cannot receive
            # callbacks after we release it.
            if signals is not None:
                if entry is not None:
                    entry_widget = entry
                    safe_qt_call(lambda: entry_widget.removeEventFilter(signals))  # type: ignore[reportArgumentType]
                if settings_icon is not None:
                    settings_icon_widget = settings_icon
                    safe_qt_call(lambda: settings_icon_widget.removeEventFilter(signals))  # type: ignore[reportArgumentType]
                safe_qt_call(signals.deleteLater)
            self._signals = None
            if self.root is not None:
                self.root.close()
                self.root.deleteLater()
                self.root = None
            self.entry = None
            self.settings_icon = None
            # Detach the log-stream Qt broadcaster while the Qt event loop is
            # still running so background threads (TTS/janitor) cannot emit
            # into a destroyed QObject after app.quit().
            try:
                import wordy.log
                wordy.log.shutdown_log_stream()
            except Exception:
                # Best-effort: never let logging-cleanup failures block the
                # GUI shutdown path.
                pass
            if owns_app and app is not None:
                app.quit()

        receiver = self.root if self.root is not None else app
        if receiver is not None and app is not None and self._event_loop_started:
            QTimer.singleShot(0, receiver, _finalize)
        else:
            _finalize()

    def show(self) -> None:
        """按位置设置显示输入框并聚焦。"""
        self._ensure_ui()
        if self.root is None or self.entry is None:
            return
        self._ignore_focus_out = True
        self._position_window_for_show()
        self.entry.clear()
        self.root.show()
        self.root.raise_()
        self.root.activateWindow()
        self._focus_entry()
        for delay in (30, 90, 180):
            QTimer.singleShot(delay, self._focus_entry)
        QTimer.singleShot(FOCUS_CLEAR_DELAY_MS, self._clear_ignore_focus_out)
        QTimer.singleShot(OUTSIDE_CLICK_INTERVAL_MS, self._watch_outside_click)

    def hide(self) -> None:
        """隐藏输入框。"""
        self._stop_outside_click_watcher()
        if self.root is not None:
            self.root.hide()

    def toggle(self) -> None:
        """切换输入框显示状态。"""
        self._ensure_ui()
        if self.root is None or not self.root.isVisible():
            self.show()
        else:
            self.hide()

    def try_register_hotkey(self, hotkey: str, name: str) -> bool:
        """只更新运行中的全局快捷键，不保存配置。"""
        old_hotkey = self._cfg.hotkey
        old_name = self._cfg.name
        self._unregister_hotkey()
        self._cfg.hotkey = hotkey
        self._cfg.name = name
        try:
            self._register_hotkey()
        except Exception as e:
            logger.warning("注册 %s 失败: %s", name, e)
            self._cfg.hotkey = old_hotkey
            self._cfg.name = old_name
            try:
                self._register_hotkey()
            except Exception as restore_error:
                logger.warning("恢复全局快捷键 %s 失败: %s", old_name, restore_error)
            return False
        return True

    def _try_register_startup_hotkey(self) -> bool:
        """Register the configured startup hotkey, or guide the user to settings."""
        try:
            self._register_hotkey()
        except Exception as error:
            logger.warning("启动时注册全局快捷键 %s 失败: %s", self._cfg.name, error)
            self._show_startup_hotkey_conflict(error)
            return False
        return True

    def _show_startup_hotkey_conflict(self, error: Exception) -> None:
        """Open settings with an actionable hotkey-conflict message."""
        settings = self._active_settings_window()
        if settings is None:
            self._create_settings_window()
            settings = self._active_settings_window()
        if settings is None:
            return

        message = (
            f"当前全局快捷键 {self._cfg.name} 无法注册，可能已被其他程序占用。"
            "请录制并应用新的全局快捷键。"
        )
        settings.set_hotkey_warning(message)
        settings.set_status(f"快捷键未启用：{error}")

    def _ensure_ui(self) -> None:
        # Qt widgets must be created on the GUI thread; normal flow calls this from run/show/toggle.
        if self._app is None:
            existing_app = QApplication.instance()
            if existing_app is not None and not isinstance(existing_app, QApplication):
                # A bare QCoreApplication (e.g. left behind by non-GUI Qt tests)
                # cannot host widgets; tear it down so a real QApplication can
                # take its place without leaving the singleton ambiguous.
                existing_app.shutdown()
                del existing_app
                import gc
                gc.collect()
                existing_app = QApplication.instance()
            if existing_app is None:
                self._app = QApplication(sys.argv[:1])
                self._app.setQuitOnLastWindowClosed(False)
                self._owns_app = True
            else:
                self._app = cast(QApplication, existing_app)  # type: ignore[reportAttributeAccessIssue]
                self._owns_app = False
        if self._signals is None:
            self._signals = _OverlaySignals(self)
            self._signals.hotkey_triggered.connect(self._handle_hotkey_triggered)
            self._signals.record_finished.connect(self._finish_record_hotkey)
            self._signals.voices_loaded.connect(self._finish_load_voices)
            self._signals.voices_error.connect(self._finish_load_voices_error)
        if self.root is None and self._signals is not None:
            self.root = _OverlayWidget(self, self._signals)
            self.root.set_overlay_opacity(self._cfg.overlay_opacity)
            self.entry = self.root.entry
            self.settings_icon = self.root.settings_button
            self.root.hide()
            outside_click_timer = QTimer(self.root)
            outside_click_timer.setInterval(OUTSIDE_CLICK_INTERVAL_MS)
            outside_click_timer.timeout.connect(self._check_outside_click)
            self._outside_click_timer = outside_click_timer
        if not self._voices_started:
            self._voices_started = True
            self._start_load_voices(show_status=False)

    def _event_filter(self, watched: QObject, event: Any) -> bool:
        if self.root is None:
            return False
        event_type = event.type()
        if watched is self.root.settings_button:
            if event_type in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
                self.root.set_settings_hover(event_type == QEvent.Type.FocusIn)
                # 设置入口现在可聚焦；切到其他窗口时也必须执行原有收起逻辑。
                if event_type == QEvent.Type.FocusOut and event.reason() not in (
                    Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason,
                ):
                    self._on_focus_out()
                return False
            if event_type == QEvent.Type.KeyPress and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter, Qt.Key.Key_Space):
                self._on_settings_button_press()
                self._on_settings_button_release()
                return True
            if event_type == QEvent.Type.Enter:
                self.root.set_settings_hover(True)
                return False
            if event_type == QEvent.Type.Leave:
                self.root.set_settings_hover(False)
                return False
            if event_type == QEvent.Type.MouseButtonPress:
                self._on_settings_button_press()
                return True
            if event_type == QEvent.Type.MouseButtonRelease:
                self._on_settings_button_release()
                return True
        if watched is self.root.entry:
            if event_type == QEvent.Type.FocusIn:
                self.root.animate_focus(True)
            if event_type == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.hide()
                return True
            if event_type == QEvent.Type.FocusOut:
                self.root.animate_focus(False)
                # Tab 导航到设置入口时保持输入栏打开，外部失焦仍按原逻辑收起。
                if event.reason() not in (Qt.FocusReason.TabFocusReason, Qt.FocusReason.BacktabFocusReason):
                    self._on_focus_out()
        return False

    def _submit_text(self, text: str, *, hide_after: bool = False) -> None:
        """提交非空文本；回车收起，朗读按钮保留输入现场。"""
        if text:
            self.on_submit(text)
            # 区分键盘提交和按钮试听，避免修复回车时改变朗读按钮行为。
            if hide_after:
                self.hide()

    def _register_hotkey(self) -> None:
        self._unregister_hotkey()
        from wordy.hotkey import NativeHotkeyListener
        hotkey_listener = NativeHotkeyListener(self._cfg.hotkey, self._cfg.name, self._on_global_hotkey)
        self._hotkey_listener = hotkey_listener
        hotkey_listener.start()

    def _unregister_hotkey(self) -> None:
        if self._hotkey_listener is None:
            return
        try:
            self._hotkey_listener.stop()
        except Exception as e:
            logger.warning("注销全局快捷键 %s 失败: %s", self._cfg.name, e)
        finally:
            self._hotkey_listener = None

    def _on_global_hotkey(self) -> None:
        if self._recording_hotkey:
            logger.debug("全局快捷键触发，但当前正在录制快捷键，已忽略。")
            return
        logger.debug("全局快捷键触发: %s", self._cfg.name)
        signals = self._signals
        if signals is not None:
            signals.hotkey_triggered.emit()

    def _handle_hotkey_triggered(self) -> None:
        self.toggle()

    def _position_window_for_show(self) -> None:
        if self.root is None:
            return
        if self._cfg.fixed_center or self._cfg.window_position is None:
            center_window(self.root, self.width, self.height)
            return
        x, y = self._clamp_window_position(self._cfg.window_position["x"], self._cfg.window_position["y"])
        self.root.setGeometry(x, y, self.width, self.height)

    def _clamp_window_position(self, x: int, y: int) -> tuple[int, int]:
        root = self.root
        return clamp_window_position(root, self.width, self.height, x, y)

    def _active_settings_window(self) -> SettingsWindow | None:
        if self._settings_window is not None and self._settings_window.exists():
            return self._settings_window
        return None

    def _settings_window_exists(self) -> bool:
        return self._active_settings_window() is not None

    def _on_settings_button_press(self) -> None:
        self._ignore_focus_out = True
        QTimer.singleShot(SETTINGS_IGNORE_FOCUS_DELAY_MS, self._clear_ignore_focus_out)

    def _on_settings_button_release(self) -> None:
        self._open_settings()

    def _on_overlay_mouse_press(self, event: QMouseEvent) -> None:
        self._focus_entry()
        if self._cfg.fixed_center or self.root is None:
            return
        self._ignore_focus_out = True
        self._is_dragging_window = True
        position = event.globalPosition().toPoint()
        self._drag_start_mouse_x = position.x()
        self._drag_start_mouse_y = position.y()
        self._drag_start_window_x = self.root.x()
        self._drag_start_window_y = self.root.y()

    def _on_overlay_mouse_move(self, event: QMouseEvent) -> None:
        if self._cfg.fixed_center or not self._is_dragging_window or self.root is None:
            return
        position = event.globalPosition().toPoint()
        x = self._drag_start_window_x + position.x() - self._drag_start_mouse_x
        y = self._drag_start_window_y + position.y() - self._drag_start_mouse_y
        x, y = self._clamp_window_position(x, y)
        self.root.setGeometry(x, y, self.width, self.height)

    def _on_overlay_mouse_release(self) -> None:
        if self._cfg.fixed_center or not self._is_dragging_window or self.root is None:
            return
        self._is_dragging_window = False
        x, y = self._clamp_window_position(self.root.x(), self.root.y())
        self._cfg.window_position = {"x": x, "y": y}
        config_file = AppSettings.load().update(window_position=self._cfg.window_position)
        self._last_saved_config_file = config_file
        QTimer.singleShot(100, self._clear_ignore_focus_out)

    def _open_settings(self) -> None:
        self._ignore_focus_out = True
        settings = self._active_settings_window()
        if settings is not None:
            settings.lift_and_focus()
            QTimer.singleShot(SETTINGS_IGNORE_FOCUS_DELAY_MS, self._clear_ignore_focus_out)
            return
        self._create_settings_window()
        QTimer.singleShot(SETTINGS_IGNORE_FOCUS_DELAY_MS, self._clear_ignore_focus_out)

    def _create_settings_window(self) -> None:
        try:
            cartesia_status = wordy.secret.get_cartesia_api_key_status()
            cartesia_api_key_saved = bool(cartesia_status.get("cartesia_api_key_set", False))
        except Exception as error:  # pragma: no cover - defensive
            logger.warning("读取 Cartesia API Key 状态失败: %s", error)
            cartesia_api_key_saved = False
        try:
            volc_status = wordy.secret.get_volcengine_access_key_status()
            volcengine_access_key_saved = bool(volc_status.get("volcengine_access_key_set", False))
        except Exception as error:
            logger.warning("读取 Volcengine Access Key 状态失败: %s", error)
            volcengine_access_key_saved = False

        # 运行时数据（非持久化）
        mic_listen_configured = (
            self.on_query_mic_listen_status()
            if callable(self.on_query_mic_listen_status)
            else False
        )
        state = SettingsState(
            voices_cache=self._get_active_voices_cache(),
            voices_loading=self._voices_loading,
            voice_fetch_error=self._voice_fetch_error,
            # 先显示保存的选择；硬件扫描延后到用户进入音频页，不阻塞设置打开。
            audio_output_devices=[dict(self._cfg.audio_output_device)] if self._cfg.audio_output_device else [],
            input_devices=self._enumerate_input_devices(),
            cartesia_api_key_saved=cartesia_api_key_saved,
            volcengine_access_key_saved=volcengine_access_key_saved,
            vb_cable_installed=VBCableDriverManager.is_installed(),
            mic_listen_configured=mic_listen_configured,
        )
        self._recording_hotkey = False
        self._settings_window = SettingsWindow(
            self.root,
            state,
            on_record_hotkey=self._start_record_hotkey,
            on_refresh_voices=self._start_load_voices,
            on_field_changed=self._on_settings_field_changed,
            on_close=self._on_settings_window_closed,
            on_audio_outputs_needed=self._refresh_settings_audio_outputs,
        )

    def _refresh_settings_audio_outputs(self) -> None:
        settings = self._active_settings_window()
        if settings is None:
            return
        # PortAudio 初始化/销毁不能并行执行；按需在 GUI 线程枚举，避免扫描线程竞争。
        devices, error = self._enumerate_audio_output_devices()
        settings.set_audio_output_devices(cast(list[dict[str, object]] | list[str], devices), error)

    def _enumerate_audio_output_devices(self) -> tuple[list[object], Exception | None]:
        """Return (devices, error) preserving structured dicts or bare-name strings."""
        raw_devices: object = None
        used_fallback = False

        audio_player = self._audio_player
        instance_helper = getattr(audio_player, "list_output_devices", None) if audio_player is not None else None
        if callable(instance_helper):
            try:
                raw_devices = instance_helper()
            except TypeError:
                used_fallback = True
            except Exception as error:  # pragma: no cover - defensive
                logger.warning("通过实例枚举音频输出设备失败: %s", error)
                return [], error
        else:
            used_fallback = True

        if used_fallback:
            try:
                import wordy.audio.player as audio_player_module
                module_helper = getattr(audio_player_module, "list_output_devices", None)
                if not callable(module_helper):
                    return [], None
                raw_devices = module_helper()
            except Exception as error:  # pragma: no cover - defensive
                logger.warning("通过模块枚举音频输出设备失败: %s", error)
                return [], error

        devices: list[object] = []
        iterable_devices: list[object] = list(raw_devices) if isinstance(raw_devices, (list, tuple)) else []
        for device in iterable_devices:
            if isinstance(device, str):
                devices.append(device)
            elif isinstance(device, dict):
                name = device.get("name")
                if isinstance(name, str):
                    devices.append(dict(device))
            else:
                name_attr = getattr(device, "name", None)
                if isinstance(name_attr, str):
                    devices.append(name_attr)
        return devices, None

    def _enumerate_input_devices(self) -> list[dict[str, object]]:
        """枚举系统输入设备，返回结构化的设备列表。"""
        try:
            devices = list_input_devices()
            return [dict(dev) for dev in devices]
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("枚举输入设备失败: %s", e)
            return []

    def _on_settings_window_closed(self) -> None:
        was_recording_hotkey = self._recording_hotkey
        self._recording_hotkey = False
        self._settings_window = None
        if was_recording_hotkey:
            try:
                self._register_hotkey()
            except Exception as e:
                logger.warning("设置窗口关闭后重新注册全局快捷键 %s 失败: %s", self._cfg.name, e)

    def _start_record_hotkey(self) -> None:
        if self._recording_hotkey:
            return
        self._recording_hotkey = True
        settings = self._active_settings_window()
        if settings is not None:
            settings.set_recording_started()
        self._unregister_hotkey()
        threading.Thread(target=self._read_hotkey_worker, daemon=True).start()

    def _read_hotkey_worker(self) -> None:
        try:
            import keyboard
            hotkey = keyboard.read_hotkey(suppress=False)
        except Exception as e:
            signals = self._signals
            if signals is not None:
                signals.record_finished.emit(None, e)
            return
        recorded_hotkey = None if hotkey == "esc" else hotkey
        signals = self._signals
        if signals is not None:
            signals.record_finished.emit(recorded_hotkey, None)

    def _finish_record_hotkey(self, hotkey: object, error: object) -> None:
        settings = self._active_settings_window()
        self._recording_hotkey = False
        register_error: Exception | None = None
        if self._hotkey_listener is None:
            try:
                self._register_hotkey()
            except Exception as e:
                logger.warning("重新注册全局快捷键 %s 失败: %s", self._cfg.name, e)
                register_error = e
        if settings is None:
            return
        if register_error is not None:
            settings.set_record_result(None, None, register_error)
            return
        if isinstance(error, Exception):
            settings.set_record_result(None, None, error)
            return
        if not isinstance(hotkey, str):
            settings.set_record_result(None, None)
            return
        settings.set_record_result(hotkey, display_hotkey(hotkey))

    def _start_load_voices(self, show_status: bool = True) -> None:
        """后台加载音色列表，每次请求捕获自己的服务商和引擎。"""
        if self._closed:
            return
        # 切换服务商或更新凭据不等待旧请求；只有最新请求能更新 UI 与缓存。
        self._voices_generation += 1
        generation = self._voices_generation
        provider = self._cfg.active_tts_provider
        fetch_voices = self.on_fetch_voices
        self._voices_loading = True
        self._voice_fetch_error = None
        settings = self._active_settings_window()
        if show_status and settings is not None:
            settings.set_voices_loading()
        threading.Thread(
            target=self._load_voices_worker,
            args=(generation, provider, fetch_voices), daemon=True,
        ).start()

    def _load_voices_worker(
        self, generation: int, provider: str,
        fetch_voices: Callable[[], list[VoiceInfo]] | None,
    ) -> None:
        try:
            if fetch_voices is None:
                raise RuntimeError("当前 TTS 后端未提供音色列表获取方法")
            voices = fetch_voices()
        except Exception as error:
            signals = self._signals
            if not self._closed and signals is not None:
                # Qt 对象可能在检查后被 GUI 线程释放，复用既有安全调用兜底。
                safe_qt_call(lambda: signals.voices_error.emit(generation, provider, error))
            return
        signals = self._signals
        if not self._closed and signals is not None:
            safe_qt_call(lambda: signals.voices_loaded.emit(generation, provider, voices))

    def _finish_load_voices(self, generation: int, provider: str, voices: object) -> None:
        # 已排队的信号也可能在切换或退出后才送达，必须在消费结果时再次校验。
        if self._closed or generation != self._voices_generation or provider != self._cfg.active_tts_provider:
            return
        self._voices_loading = False
        if isinstance(voices, list) and all(isinstance(voice, dict) for voice in voices):
            self._set_active_voices_cache([dict(voice) for voice in voices])
        else:
            self._set_active_voices_cache([])
        self._voice_fetch_error = None
        settings = self._active_settings_window()
        if settings is not None:
            active_cache = self._get_active_voices_cache()
            settings.set_voices_loaded(active_cache, self._get_active_voice_id())

    def _finish_load_voices_error(self, generation: int, provider: str, error: object) -> None:
        if self._closed or generation != self._voices_generation or provider != self._cfg.active_tts_provider:
            return
        self._voices_loading = False
        self._voice_fetch_error = error if isinstance(error, Exception) else RuntimeError(str(error))
        settings = self._active_settings_window()
        if settings is not None:
            settings.set_voices_error(self._voice_fetch_error)

    def _on_settings_field_changed(self, field_name: str, value: object) -> None:
        """设置变更副作用——持久化已由 AppSettings.__setattr__ 完成，self._cfg 即该单例。"""
        if field_name == "hotkey":
            settings = self._active_settings_window()
            if settings is not None and settings._recorded_hotkey and settings._recorded_hotkey_name:
                hotkey = settings._recorded_hotkey
                hotkey_name = settings._recorded_hotkey_name
                if self.try_register_hotkey(hotkey, hotkey_name):
                    self._cfg.update(hotkey=hotkey, name=hotkey_name)
                else:
                    settings.set_hotkey_warning("快捷键注册失败，可能被占用")

        elif field_name == "volume":
            if self.on_volume_change:
                self.on_volume_change(float(value))  # type: ignore[arg-type]

        elif field_name == "overlay_opacity":
            if self.root:
                self.root.set_overlay_opacity(float(value))  # type: ignore[arg-type]

        elif field_name == "overlay_placeholder":
            if self.entry is not None:
                self.entry.setPlaceholderText(str(value))

        elif field_name == "fixed_center":
            if bool(value) and self.root:
                center_window(self.root, self.width, self.height)

        elif field_name == "log_level":
            import logging as _log_mod
            _log_mod.getLogger().setLevel(getattr(_log_mod, str(value), _log_mod.INFO))

        elif field_name == "active_tts_provider":
            if self.on_tts_api_provider_change:
                self.on_tts_api_provider_change(str(value))

        elif field_name in ("cartesia_voice_id", "cartesia_voice_name",
                            "volcengine_voice_id", "volcengine_voice_name"):
            if self.on_voice_change:
                active_vid = self._get_active_voice_id() or ""
                active_vname = self._get_active_voice_name() or ""
                self.on_voice_change(active_vid, active_vname)

        elif field_name in ("cartesia_tts_backend", "volcengine_tts_backend"):
            if self.on_tts_backend_change:
                self.on_tts_backend_change(str(value) if value else self._get_active_tts_backend())

        elif field_name == "audio_output_device":
            self._handle_audio_output_change(value)

        elif field_name == "audio_routing_enabled":
            if self.on_audio_route_change:
                self.on_audio_route_change({"audio_routing_enabled": bool(value)})
            self._refresh_mic_listen_status()

        elif field_name == "mic_input_device":
            if self.on_audio_route_change:
                self.on_audio_route_change({"mic_input_device": value if isinstance(value, str) else None})
            self._refresh_mic_listen_status()

        elif field_name == "sidetone_enabled":
            if self.on_sidetone_change:
                self.on_sidetone_change(bool(value))

        elif field_name == "cartesia_api_key":
            if self.on_cartesia_api_key_change:
                self.on_cartesia_api_key_change(str(value) if value else None)
            # 密钥保存后自动刷新音色列表
            self._start_load_voices(show_status=True)

        elif field_name == "volcengine_access_key":
            if self.on_volcengine_credentials_change:
                self.on_volcengine_credentials_change(str(value) if value else None)

    def _refresh_mic_listen_status(self) -> None:
        """路由变更后刷新设置窗口中麦克风侦听状态。"""
        settings = self._active_settings_window()
        if settings is None:
            return
        listen_ok = (
            self.on_query_mic_listen_status()
            if callable(self.on_query_mic_listen_status)
            else False
        )
        if hasattr(settings, '_state'):
            settings._state.mic_listen_configured = listen_ok  # type: ignore[union-attr]
        settings._update_mic_listen_status(
            self._cfg,
            getattr(settings, '_state', None),
        )

    def _handle_audio_output_change(self, value: object) -> None:
        """音频输出设备变更的运行时处理。"""
        if isinstance(value, dict):
            self._cfg.audio_output_device = dict(value)  # type: ignore[assignment]
            name = value.get("name")
            self._cfg.audio_output_device_name = name if isinstance(name, str) else None
        else:
            self._cfg.audio_output_device = None  # type: ignore[assignment]
            self._cfg.audio_output_device_name = None
        if self.on_audio_output_change:
            try:
                self.on_audio_output_change(self._cfg.audio_output_device)
            except Exception:
                logger.exception("音频输出设备变更通知失败")

    def _clear_ignore_focus_out(self) -> None:
        self._ignore_focus_out = False

    def _is_point_in_tracked_windows(self, x: int, y: int) -> bool:
        if self.root is not None and is_point_in_widget(self.root, x, y):
            return True
        settings = self._active_settings_window()
        if settings is not None and is_point_in_widget(settings.window, x, y):
            return True
        return False

    def _watch_outside_click(self) -> None:
        if self._closed or self.root is None or not self.root.isVisible():
            self._stop_outside_click_watcher()
            return
        if self._outside_click_watcher_running:
            return
        self._outside_click_watcher_running = True
        self._outside_mouse_down = is_left_button_down()
        if self._outside_click_timer is not None:
            self._outside_click_timer.start()

    def _stop_outside_click_watcher(self) -> None:
        self._outside_click_watcher_running = False
        if self._outside_click_timer is not None:
            self._outside_click_timer.stop()

    def _check_outside_click(self) -> None:
        if self._closed or self.root is None or not self.root.isVisible():
            self._stop_outside_click_watcher()
            return
        left_button_down = is_left_button_down()
        cursor_position = get_cursor_position()
        if (
            left_button_down
            and not self._outside_mouse_down
            and cursor_position is not None
            and not self._is_point_in_tracked_windows(*cursor_position)
            and not self._ignore_focus_out
        ):
            self.hide()
            return
        self._outside_mouse_down = left_button_down

    def _focus_entry(self) -> None:
        if self.root is None or self.entry is None or not self.root.isVisible():
            return
        self.root.raise_()
        activate_window(self.root)
        self.entry.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        self.entry.end(False)

    def _on_return(self) -> None:
        if self.entry is None:
            return
        self._submit_text(normalize_input_text(self.entry.text()), hide_after=True)

    def _on_read(self) -> None:
        if self.entry is None:
            return
        self._submit_text(normalize_input_text(self.entry.text()))

    def _on_focus_out(self) -> None:
        if self._ignore_focus_out or self._settings_window_exists():
            return
        # 只在焦点离开整个输入栏时收起；设置图标与输入框之间切换不是外部失焦。
        focused = QApplication.focusWidget()
        if self.root is not None and focused is not None and (
            focused is self.root or self.root.isAncestorOf(focused)
        ):
            return
        self.hide()
