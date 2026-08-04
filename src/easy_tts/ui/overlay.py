#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""可配置全局热键 PySide6 输入框。"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, TypeAlias

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtWidgets import QApplication, QLabel, QLineEdit, QMessageBox

import easy_tts.secret
from easy_tts.config import AppSettings, display_hotkey, get_active_config_file
from easy_tts.audio.capture import AudioCapture
from easy_tts.identity import normalize_identity
from easy_tts.audio.driver import VBCableDriverManager
from easy_tts.ui.settings_state import SettingsState
from easy_tts.ui.settings import SettingsWindow
from easy_tts.ui.overlay_widgets import _OverlaySignals, _OverlayWidget
from easy_tts.ui.window import activate_window, center_window, clamp_window_position, get_cursor_position, is_left_button_down, is_point_in_widget

from easy_tts.qt_lifecycle import safe_qt_call

if TYPE_CHECKING:
    from easy_tts.hotkey import NativeHotkeyListener

logger = logging.getLogger(__name__)

# 仅 InputOverlay 自身使用的常量
OUTSIDE_CLICK_INTERVAL_MS = 80
FOCUS_CLEAR_DELAY_MS = 260
SETTINGS_IGNORE_FOCUS_DELAY_MS = 300
VoiceInfo: TypeAlias = dict[str, object]
WindowPosition: TypeAlias = dict[str, int]


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
        on_audio_route_change: Callable[[dict[str, object]], None] | None = None,
        on_sidetone_change: Callable[[bool], None] | None = None,
        audio_player: object | None = None,
        width: int = 540,
        height: int = 58,
        poll_interval_ms: int = 30,
    ):
        if sys.platform != "win32":
            raise RuntimeError("InputOverlay 仅支持 Windows")

        config = AppSettings.load()
        self.on_submit = on_submit
        self.on_voice_change = on_voice_change
        self.on_volume_change = on_volume_change
        self.on_tts_backend_change = on_tts_backend_change
        self.on_fetch_voices = on_fetch_voices
        self.on_audio_output_change = on_audio_output_change
        self.on_cartesia_api_key_change = on_cartesia_api_key_change
        self.on_audio_route_change = on_audio_route_change
        self.on_sidetone_change = on_sidetone_change
        self._audio_player = audio_player
        self.width = width
        self.height = height
        self.poll_interval_ms = poll_interval_ms
        self._hotkey = config.hotkey
        self._hotkey_name = config.name
        self._voice_id = config.voice_id
        self._voice_name = config.voice_name
        self._volume = config.volume
        self._overlay_opacity = config.overlay_opacity
        self._tts_backend = config.tts_backend
        self._log_level = config.log_level
        self._fixed_center = config.fixed_center
        self._window_position: WindowPosition | None = config.window_position
        self._audio_output_device_name: str | None = config.audio_output_device_name
        self._audio_output_device: dict[str, object] | None = self._init_audio_output_device(config)
        # 音频路由配置
        self._audio_routing_enabled: bool = config.audio_routing_enabled
        self._mic_input_device: str | None = config.mic_input_device
        self._virtual_output_device: str | None = config.virtual_output_device
        # 返听 (sidetone)
        self._sidetone_enabled: bool = config.sidetone_enabled
        self._closed = False
        self._hotkey_listener: NativeHotkeyListener | None = None
        self._settings_window: SettingsWindow | None = None
        self._ignore_focus_out = False
        self._recording_hotkey = False
        self._voices_cache: list[VoiceInfo] = []
        self._voices_loading = False
        self._voice_fetch_error: Exception | None = None
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
        self._voices_started = False
        self._pre_stop_hook: Callable[[], None] | None = None
        self._event_loop_started = False

    @staticmethod
    def _init_audio_output_device(config: AppSettings) -> dict[str, object] | None:
        """从 AppSettings 提取音频输出设备身份信息."""
        raw = config.audio_output_device
        if isinstance(raw, dict):
            name = raw.get("name")
            if isinstance(name, str) and name:
                host_api = raw.get("host_api_name")
                host_api_value = host_api if isinstance(host_api, str) and host_api else None
                return dict(normalize_identity(name, host_api_value))
        legacy_name = config.audio_output_device_name
        if isinstance(legacy_name, str) and legacy_name:
            return dict(normalize_identity(legacy_name))
        return None

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
            logger.info("已启动输入框监听，按 %s 弹出输入框。", self._hotkey_name)
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
                    safe_qt_call(lambda: entry_widget.removeEventFilter(signals))
                if settings_icon is not None:
                    settings_icon_widget = settings_icon
                    safe_qt_call(lambda: settings_icon_widget.removeEventFilter(signals))
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
                import easy_tts.log
                easy_tts.log.shutdown_log_stream()
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
        old_hotkey = self._hotkey
        old_name = self._hotkey_name
        self._unregister_hotkey()
        self._hotkey = hotkey
        self._hotkey_name = name
        try:
            self._register_hotkey()
        except Exception as e:
            logger.warning("注册 %s 失败: %s", name, e)
            self._hotkey = old_hotkey
            self._hotkey_name = old_name
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
            logger.warning("启动时注册全局快捷键 %s 失败: %s", self._hotkey_name, error)
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
            f"当前全局快捷键 {self._hotkey_name} 无法注册，可能已被其他程序占用。"
            "请录制并应用新的全局快捷键。"
        )
        settings.set_hotkey_warning(message)
        settings.set_apply_status(f"快捷键未启用：{error}")

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
                self._app = existing_app
                self._owns_app = False
        if self._signals is None:
            self._signals = _OverlaySignals(self)
            self._signals.hotkey_triggered.connect(self._handle_hotkey_triggered)
            self._signals.record_finished.connect(self._finish_record_hotkey)
            self._signals.voices_loaded.connect(self._finish_load_voices)
            self._signals.voices_error.connect(self._finish_load_voices_error)
        if self.root is None and self._signals is not None:
            self.root = _OverlayWidget(self, self._signals)
            self.root.set_overlay_opacity(self._overlay_opacity)
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
            if event_type == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self.hide()
                return True
            if event_type == QEvent.Type.FocusOut:
                self._on_focus_out()
        return False

    def _submit_text(self, text: str) -> None:
        """隐藏窗口并提交非空文本。"""
        self.hide()
        if text:
            self.on_submit(text)

    def _register_hotkey(self) -> None:
        self._unregister_hotkey()
        from easy_tts.hotkey import NativeHotkeyListener
        hotkey_listener = NativeHotkeyListener(self._hotkey, self._hotkey_name, self._on_global_hotkey)
        self._hotkey_listener = hotkey_listener
        hotkey_listener.start()

    def _unregister_hotkey(self) -> None:
        if self._hotkey_listener is None:
            return
        try:
            self._hotkey_listener.stop()
        except Exception as e:
            logger.warning("注销全局快捷键 %s 失败: %s", self._hotkey_name, e)
        finally:
            self._hotkey_listener = None

    def _on_global_hotkey(self) -> None:
        if self._recording_hotkey:
            logger.info("全局快捷键触发，但当前正在录制快捷键，已忽略。")
            return
        logger.info("全局快捷键触发: %s", self._hotkey_name)
        signals = self._signals
        if signals is not None:
            signals.hotkey_triggered.emit()

    def _handle_hotkey_triggered(self) -> None:
        self.toggle()

    def _position_window_for_show(self) -> None:
        if self.root is None:
            return
        if self._fixed_center or self._window_position is None:
            center_window(self.root, self.width, self.height)
            return
        x, y = self._clamp_window_position(self._window_position["x"], self._window_position["y"])
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
        if self._fixed_center or self.root is None:
            return
        self._ignore_focus_out = True
        self._is_dragging_window = True
        position = event.globalPosition().toPoint()
        self._drag_start_mouse_x = position.x()
        self._drag_start_mouse_y = position.y()
        self._drag_start_window_x = self.root.x()
        self._drag_start_window_y = self.root.y()

    def _on_overlay_mouse_move(self, event: QMouseEvent) -> None:
        if self._fixed_center or not self._is_dragging_window or self.root is None:
            return
        position = event.globalPosition().toPoint()
        x = self._drag_start_window_x + position.x() - self._drag_start_mouse_x
        y = self._drag_start_window_y + position.y() - self._drag_start_mouse_y
        x, y = self._clamp_window_position(x, y)
        self.root.setGeometry(x, y, self.width, self.height)

    def _on_overlay_mouse_release(self) -> None:
        if self._fixed_center or not self._is_dragging_window or self.root is None:
            return
        self._is_dragging_window = False
        x, y = self._clamp_window_position(self.root.x(), self.root.y())
        self._window_position = {"x": x, "y": y}
        config_file = AppSettings.load().update(window_position=self._window_position)
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
        audio_output_devices, audio_output_devices_error = self._enumerate_audio_output_devices()
        try:
            storage_status = easy_tts.secret.get_storage_status()
            cartesia_api_key_saved = bool(storage_status.has_key)
        except Exception as error:  # pragma: no cover - defensive
            logger.warning("读取 Cartesia API Key 状态失败: %s", error)
            cartesia_api_key_saved = False
        state = SettingsState(
            hotkey=self._hotkey,
            hotkey_name=self._hotkey_name,
            voice_id=self._voice_id,
            voice_name=self._voice_name,
            volume=self._volume,
            tts_backend=self._tts_backend,
            fixed_center=self._fixed_center,
            overlay_opacity=self._overlay_opacity,
            voices_cache=self._voices_cache,
            voices_loading=self._voices_loading,
            voice_fetch_error=self._voice_fetch_error,
            audio_output_devices=audio_output_devices,
            audio_output_device_name=self._audio_output_device_name,
            audio_output_device_identity=dict(self._audio_output_device) if self._audio_output_device is not None else None,
            audio_output_devices_error=audio_output_devices_error,
            cartesia_api_key_saved=cartesia_api_key_saved,
            log_level=self._log_level,
            audio_routing_enabled=self._audio_routing_enabled,
            input_devices=self._enumerate_input_devices(),
            mic_input_device=self._mic_input_device,
            virtual_output_device=self._virtual_output_device,
            vb_cable_installed=VBCableDriverManager.is_installed(),
            sidetone_enabled=self._sidetone_enabled,
        )
        self._recording_hotkey = False
        self._settings_window = SettingsWindow(
            self.root,
            state,
            on_record_hotkey=self._start_record_hotkey,
            on_refresh_voices=self._start_load_voices,
            on_apply=self._apply_pending_settings,
            on_close=self._on_settings_window_closed,
        )

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
                import easy_tts.audio.player as audio_player_module
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
            devices = AudioCapture.list_input_devices()
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
                logger.warning("设置窗口关闭后重新注册全局快捷键 %s 失败: %s", self._hotkey_name, e)

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
                logger.warning("重新注册全局快捷键 %s 失败: %s", self._hotkey_name, e)
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
        if self._voices_loading:
            return
        self._voices_loading = True
        settings = self._active_settings_window()
        if show_status and settings is not None:
            settings.set_voices_loading()
        threading.Thread(target=self._load_voices_worker, daemon=True).start()

    def _load_voices_worker(self) -> None:
        try:
            fetch_voices = self.on_fetch_voices
            if fetch_voices is None:
                raise RuntimeError("当前 TTS 后端未提供音色列表获取方法")
            voices = fetch_voices()
        except Exception as e:
            signals = self._signals
            if signals is not None:
                signals.voices_error.emit(e)
            return
        signals = self._signals
        if signals is not None:
            signals.voices_loaded.emit(voices)

    def _finish_load_voices(self, voices: object) -> None:
        self._voices_loading = False
        if isinstance(voices, list) and all(isinstance(voice, dict) for voice in voices):
            self._voices_cache = [dict(voice) for voice in voices]
        else:
            self._voices_cache = []
        self._voice_fetch_error = None
        settings = self._active_settings_window()
        if settings is not None:
            settings.set_voices_loaded(self._voices_cache, self._voice_id)

    def _finish_load_voices_error(self, error: object) -> None:
        self._voices_loading = False
        self._voice_fetch_error = error if isinstance(error, Exception) else RuntimeError(str(error))
        settings = self._active_settings_window()
        if settings is not None:
            settings.set_voices_error(self._voice_fetch_error)

    def _apply_pending_settings(self, settings: SettingsWindow) -> None:
        pending = settings.get_pending_settings()
        config_update: dict[str, object] = {}
        saved_messages: list[str] = []

        # ---- 快捷键（特殊处理：注册失败则回滚，不继续保存） ----
        old_hotkey = self._hotkey
        old_name = self._hotkey_name
        hotkey_changed = pending.hotkey != old_hotkey or pending.hotkey_name != old_name
        if hotkey_changed and not self.try_register_hotkey(pending.hotkey, pending.hotkey_name):
            QMessageBox.critical(settings.window, "快捷键设置失败",
                f"无法注册 {pending.hotkey_name}，请确认快捷键未被其他程序占用。")
            return
        if hotkey_changed:
            config_update["hotkey"] = pending.hotkey
            config_update["name"] = pending.hotkey_name

        # ---- 音频输出（复杂 identity 处理，委托给专用方法） ----
        audio_changed, audio_apply = self._compute_audio_output_change(pending)
        config_update.update(audio_apply.get("config_patch", {}))
        # 缓存窗口位置（在 config_update 保存前计算）
        pending_window_position: WindowPosition | None = None
        window_position_changed = False
        if not pending.fixed_center and self.root is not None:
            x, y = self._clamp_window_position(self.root.x(), self.root.y())
            pending_window_position = {"x": x, "y": y}
            window_position_changed = pending_window_position != self._window_position
            if window_position_changed:
                config_update["window_position"] = pending_window_position

        # ---- 简单标量字段：compare → 存 config_update  ----
        self._collect_scalar_change(pending, config_update, "voice_id")
        self._collect_scalar_change(pending, config_update, "voice_name")
        self._collect_scalar_change(pending, config_update, "volume")
        self._collect_scalar_change(pending, config_update, "overlay_opacity")
        self._collect_scalar_change(pending, config_update, "tts_backend")
        self._collect_scalar_change(pending, config_update, "log_level")
        self._collect_scalar_change(pending, config_update, "fixed_center")

        # ---- 音频路由字段 ----
        route_update: dict[str, object] = {}
        for key in ("audio_routing_enabled", "mic_input_device", "virtual_output_device"):
            self._collect_scalar_change(pending, route_update, key, local_attr=f"_{key}")
        if route_update:
            config_update.update(route_update)

        # ---- 返听字段（独立于路由，单独回调） ----
        sidetone_changed = self._collect_scalar_change(pending, config_update, "sidetone_enabled",
                                                        local_attr="_sidetone_enabled")

        # ---- 持久化到 JSON ----
        if config_update:
            try:
                config_file = AppSettings.load().update(**config_update)
            except OSError as e:
                if hotkey_changed:
                    self.try_register_hotkey(old_hotkey, old_name)
                QMessageBox.critical(settings.window, "保存失败", f"无法保存设置：{e}")
                return
            self._last_saved_config_file = config_file

        # ---- 应用变更到运行时状态 + 回调 + 消息 ----
        if hotkey_changed:
            saved_messages.append(f"全局快捷键已更新为 {pending.hotkey_name}")
            settings.current_label.setText(f"当前快捷键：{pending.hotkey_name}")

        self._apply_scalar_and_notify(pending, saved_messages, "voice_id", "_voice_id",
            callback=lambda: self.on_voice_change and self.on_voice_change(self._voice_id, self._voice_name or ""),
            msg=lambda: f"音色已更新为 {self._voice_name}")
        self._apply_scalar_and_notify(pending, saved_messages, "voice_name", "_voice_name")
        self._apply_scalar_and_notify(pending, saved_messages, "volume", "_volume",
            callback=lambda: self.on_volume_change and self.on_volume_change(self._volume),
            msg=lambda: f"音量已更新为 {self._volume:.2f}x")
        self._apply_scalar_and_notify(pending, saved_messages, "overlay_opacity", "_overlay_opacity",
            side_effect=lambda: self.root is not None and self.root.set_overlay_opacity(self._overlay_opacity),
            msg=lambda: f"输入框透明度已更新为 {round(self._overlay_opacity * 100)}%")
        self._apply_scalar_and_notify(pending, saved_messages, "tts_backend", "_tts_backend",
            callback=lambda: self.on_tts_backend_change and self.on_tts_backend_change(self._tts_backend),
            msg=lambda: f"模式已切换为 {self._tts_backend}")
        self._apply_scalar_and_notify(pending, saved_messages, "log_level", "_log_level",
            side_effect=lambda: logging.getLogger().setLevel(getattr(logging, self._log_level, logging.INFO)),
            msg=lambda: f"日志显示等级已切换为 {self._log_level}")

        # 音频路由：应用变更并通知 WavTransApp
        if route_update:
            for key in ("audio_routing_enabled", "mic_input_device", "virtual_output_device"):
                self._apply_scalar_and_notify(pending, saved_messages, key, f"_{key}")
            if self.on_audio_route_change is not None:
                self.on_audio_route_change(dict(route_update))
            if "audio_routing_enabled" in route_update:
                label = "音频路由已启用" if self._audio_routing_enabled else "音频路由已禁用"
                saved_messages.append(label)
            if "mic_input_device" in route_update:
                if self._mic_input_device:
                    saved_messages.append(f"麦克风侦听已切换为 {self._mic_input_device}")
                else:
                    saved_messages.append("麦克风侦听已停用")

        # 返听：应用变更并通知 WavTransApp
        if sidetone_changed:
            self._apply_scalar_and_notify(pending, saved_messages, "sidetone_enabled", "_sidetone_enabled",
                callback=lambda: self.on_sidetone_change and self.on_sidetone_change(self._sidetone_enabled),
                msg=lambda: "返听已启用" if self._sidetone_enabled else "返听已禁用")

        # fixed_center 有额外 UI 副作用
        self._apply_scalar_and_notify(pending, saved_messages, "fixed_center", "_fixed_center",
            side_effect=lambda: self._apply_fixed_center_side_effect(pending_window_position),
            msg=lambda: ("输入窗口已设置为固定居中" if self._fixed_center
                         else "输入窗口已设置为可拖动并记住位置"))
        if not pending.fixed_center and window_position_changed:
            self._window_position = pending_window_position
            saved_messages.append("输入窗口位置已更新")

        # 音频输出设备变更
        if audio_changed:
            self._apply_audio_output_runtime(audio_apply, saved_messages)

        self._apply_cartesia_api_key_change(pending, saved_messages)

        # ---- 最终状态 ----
        if not saved_messages:
            settings.set_apply_status("没有设置变更")
            return
        status_text = "；".join(saved_messages)
        if config_update:
            status_text = f"{status_text}\n已保存到：{self._last_saved_config_file}"
        settings.set_apply_status(status_text)

    # ── _apply_pending_settings 辅助方法 ─────────────────────────────────

    def _collect_scalar_change(self, pending: object, config: dict[str, object],
                                key: str, *, local_attr: str | None = None) -> bool:
        """比较 pending.key 与 self.<local_attr>，有变更则写入 config dict。返回是否有变更。"""
        local_key = local_attr if local_attr is not None else f"_{key}"
        old_val = getattr(self, local_key, None)
        new_val = getattr(pending, key, old_val)
        if new_val != old_val:
            config[key] = new_val
            return True
        return False

    def _apply_scalar_and_notify(self, pending: object, messages: list[str],
                                  key: str, local_attr: str, *,
                                  callback: Callable[[], None] | None = None,
                                  side_effect: Callable[[], object] | None = None,
                                  msg: Callable[[], str] | None = None) -> bool:
        """应用单个字段的运行时变更：更新本地状态 → 执行副作用 → 调回调 → 添加消息。"""
        old_val = getattr(self, local_attr, None)
        new_val = getattr(pending, key, old_val)
        if new_val == old_val:
            return False
        setattr(self, local_attr, new_val)
        if side_effect is not None:
            side_effect()
        if callback is not None:
            callback()
        if msg is not None:
            messages.append(msg())
        return True

    def _apply_fixed_center_side_effect(self, pending_window_position: WindowPosition | None) -> None:
        """fixed_center 变更的 UI 副作用：居中或恢复拖拽位置。"""
        if self._fixed_center and self.root is not None:
            center_window(self.root, self.width, self.height)
        elif pending_window_position is not None:
            self._window_position = pending_window_position

    def _apply_audio_output_runtime(self, audio_apply: dict, saved_messages: list[str]) -> None:
        """应用音频输出设备的运行时状态变更。"""
        identity = audio_apply.get("identity")
        device_name = audio_apply.get("device_name")
        has_structured = audio_apply.get("has_structured_identity", False)
        self._audio_output_device_name = device_name
        if has_structured:
            self._audio_output_device = dict(identity) if identity is not None else None
            callback_payload: object = dict(identity) if identity is not None else None
        else:
            self._audio_output_device = (
                dict(normalize_identity(device_name)) if device_name is not None else None
            )
            callback_payload = device_name
        if self.on_audio_output_change is not None:
            self.on_audio_output_change(callback_payload)
        saved_messages.append(f"音频输出设备已切换为 {device_name or '系统默认'}")

    def _compute_audio_output_change(self, pending: object) -> tuple[bool, dict]:
        """计算音频输出设备变更，返回 (changed, apply_dict)。

        apply_dict 包含: config_patch, identity, device_name, has_structured_identity
        """
        _sentinel = object()
        pending_identity_raw: object = getattr(pending, "audio_output_device_identity", _sentinel)
        if pending_identity_raw is _sentinel:
            pending_identity_raw = getattr(pending, "audio_output_device", _sentinel)
        has_structured = pending_identity_raw is not _sentinel
        pending_name_raw: object = getattr(pending, "audio_output_device_name", self._audio_output_device_name)
        pending_device_name: str | None = pending_name_raw if isinstance(pending_name_raw, str) else None
        config_patch: dict[str, object] = {}

        if has_structured:
            pending_identity: dict[str, object] | None
            if isinstance(pending_identity_raw, dict):
                pending_identity = dict(pending_identity_raw)
            else:
                pending_identity = None
            if pending_identity is not None:
                identity_name = pending_identity.get("name")
                pending_device_name = identity_name if isinstance(identity_name, str) else None
            else:
                pending_device_name = None
            changed = (pending_identity != self._audio_output_device
                        or pending_device_name != self._audio_output_device_name)
            if changed:
                config_patch["audio_output_device"] = dict(pending_identity) if pending_identity is not None else None
                config_patch["audio_output_device_name"] = pending_device_name
        else:
            pending_identity = None
            changed = pending_device_name != self._audio_output_device_name
            if changed:
                config_patch["audio_output_device_name"] = pending_device_name

        return changed, {
            "config_patch": config_patch,
            "identity": pending_identity,
            "device_name": pending_device_name,
            "has_structured_identity": has_structured,
        }

    def _apply_cartesia_api_key_change(self, pending: object, saved_messages: list[str]) -> None:
        cartesia_action = getattr(pending, "cartesia_api_key_action", "unchanged")
        cartesia_value_raw = getattr(pending, "cartesia_api_key_value", None)
        if cartesia_action == "set":
            cartesia_value = cartesia_value_raw if isinstance(cartesia_value_raw, str) else ""
            try:
                storage_status = easy_tts.secret.save_cartesia_api_key(
                    cartesia_value, allow_plaintext_fallback=False
                )
            except easy_tts.secret.SecretStoreError as error:
                saved_messages.append(f"Cartesia API Key 保存失败：{error}")
            else:
                if storage_status.fallback_active or storage_status.backend == easy_tts.secret.STORAGE_PLAINTEXT:
                    saved_messages.append(
                        "Cartesia API Key 已保存（明文回退，高风险，请尽快配置 keyring）"
                    )
                else:
                    saved_messages.append("Cartesia API Key 已保存到 keyring")
                if self.on_cartesia_api_key_change is not None:
                    try:
                        self.on_cartesia_api_key_change(cartesia_value)
                    except Exception:
                        logger.exception("Cartesia API key change callback failed")
                        saved_messages.append("Cartesia 引擎刷新失败")
        elif cartesia_action == "clear":
            try:
                easy_tts.secret.delete_cartesia_api_key()
            except easy_tts.secret.SecretStoreError as error:
                saved_messages.append(f"Cartesia API Key 清除失败：{error}")
            else:
                saved_messages.append("Cartesia API Key 已清除")
                if self.on_cartesia_api_key_change is not None:
                    try:
                        self.on_cartesia_api_key_change(None)
                    except Exception:
                        logger.exception("Cartesia API key change callback failed")
                        saved_messages.append("Cartesia 引擎刷新失败")

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
        self._submit_text(normalize_input_text(self.entry.text()))

    def _on_focus_out(self) -> None:
        if self._ignore_focus_out or self._settings_window_exists():
            return
        self.hide()
