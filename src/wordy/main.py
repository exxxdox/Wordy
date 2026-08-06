#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
常驻模式：按配置的全局快捷键弹出输入框，输入文本后生成并播放语音。
默认播放到系统默认输出设备。
"""

import logging
import os
import sys
import threading
from typing import cast

from wordy.audio.player import AudioPlayer, OutputDeviceSelection
from wordy.audio.sidetone import SidetoneAudioPlayer
from wordy.config import AppSettings
from wordy.log import install_log_stream
import wordy.secret
from wordy.secret import KeyringUnavailableError
from wordy.routing_controller import RoutingController
from wordy.tts.constants import (
    DEFAULT_TTS_BACKEND,
    TTS_API_PROVIDER_VOLCENGINE,
    TTS_BACKEND_CARTESIA_BYTES,
    TTS_BACKEND_CARTESIA_REALTIME,
    TTS_BACKEND_VOLCENGINE_STREAMING,
    TTS_BACKENDS,
)
from wordy.tts.engine import TTSAudioPlayer
from wordy.tts.registry import resolve_tts_backend, create_tts_engine  # noqa: F401 — re-export
from wordy.tts_manager import TTSManager, _RetiredWorker, _janitor_loop_inner  # noqa: F401 — test imports
from wordy.ui.overlay import InputOverlay
from wordy.ui.tray import TrayApp, TrayController


logger = logging.getLogger(__name__)


def configure_logging() -> None:
    """配置应用日志输出。"""
    log_level = getattr(logging, AppSettings.load().log_level, logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logging.getLogger().setLevel(log_level)
    install_log_stream()


class WordyApp:
    """常驻热键输入 → TTS 生成 → 播放。

    TTS 生命周期委托给 TTSManager，音频侦听委托给 RoutingController。
    WordyApp 只负责组件装配、UI 回调接线和关机编排。
    """

    def __init__(self, tts_backend: str | None = None):
        self._settings = AppSettings.load()

        # ── 音频播放器 ──────────────────────────────────────────────────
        self.player = self._create_audio_player()

        # ── 返听 ───────────────────────────────────────────────────────
        self._sidetone_player = AudioPlayer()
        self._sidetone_wrapper = SidetoneAudioPlayer(
            self.player, self._sidetone_player
        )
        self._sidetone_wrapper.set_sidetone_enabled(
            self._settings.sidetone_enabled
        )

        # ── 凭据 ───────────────────────────────────────────────────────
        self.tts_api_provider = getattr(
            self._settings, "tts_api_provider", "Cartesia"
        )
        self.cartesia_api_key = self._load_cartesia_api_key()
        self.volcengine_access_key = self._load_volcengine_access_key()
        self.tts_backend = self._resolve_active_tts_backend(tts_backend)

        # ── TTS Manager ────────────────────────────────────────────────
        self.tts_manager = TTSManager(
            settings=self._settings,
            audio_player=cast(TTSAudioPlayer, self._sidetone_wrapper),
            cartesia_api_key=self.cartesia_api_key,
            volcengine_access_key=self.volcengine_access_key,
            tts_api_provider=self.tts_api_provider,
            tts_backend=self.tts_backend,
        )

        # ── 路由控制器 ─────────────────────────────────────────────────
        self.routing = RoutingController(
            settings=self._settings,
            player=self.player,
            on_output_device_changed=self.tts_manager.reset_audio_output,
        )

        # ── UI ─────────────────────────────────────────────────────────
        self.overlay = InputOverlay(
            on_submit=self._on_submit,
            on_voice_change=self._on_voice_change,
            on_volume_change=self.tts_manager.set_volume,
            on_tts_backend_change=self._on_tts_backend_change,
            on_fetch_voices=self.tts_manager.fetch_voices,
            on_audio_output_change=self.routing.on_output_device_change,
            on_cartesia_api_key_change=self._on_cartesia_api_key_change,
            on_tts_api_provider_change=self._on_tts_api_provider_change,
            on_volcengine_credentials_change=self._on_volcengine_credentials_change,
            on_audio_route_change=self.routing.apply_config,
            on_sidetone_change=self._on_sidetone_change,
            on_query_mic_listen_status=lambda: self.routing.is_mic_listen_configured,
            audio_player=self.player,
        )

    # ── 凭据加载 ───────────────────────────────────────────────────────────

    @staticmethod
    def _load_cartesia_api_key() -> str | None:
        try:
            return wordy.secret.load_cartesia_api_key()
        except Exception as exc:
            if isinstance(exc, KeyringUnavailableError):
                return None
            raise RuntimeError("Cartesia API key 读取失败") from exc

    @staticmethod
    def _load_volcengine_access_key() -> str | None:
        try:
            return wordy.secret.load_volcengine_access_key()
        except Exception as exc:
            if isinstance(exc, KeyringUnavailableError):
                return None
            raise RuntimeError("Volcengine access key 读取失败") from exc

    def _resolve_active_tts_backend(self, cli_backend: str | None) -> str:
        """解析生效的 TTS 后端（CLI > 配置 > 默认）。"""
        if cli_backend is not None:
            return resolve_tts_backend(cli_backend)
        if self.tts_api_provider == TTS_API_PROVIDER_VOLCENGINE:
            configured = getattr(
                self._settings, "volcengine_tts_backend", None
            )
            if configured and configured in TTS_BACKENDS:
                return configured
            return TTS_BACKEND_VOLCENGINE_STREAMING
        configured = getattr(self._settings, "cartesia_tts_backend", None)
        if configured and configured in TTS_BACKENDS:
            return configured
        # 兼容旧配置文件中的旧常量值（如 "cartesia-bytes" → "Cartesia Bytes"）
        fallback = getattr(self._settings, "tts_backend", None)
        if fallback and fallback in TTS_BACKENDS:
            return fallback
        return DEFAULT_TTS_BACKEND

    # ── 音频播放器 ────────────────────────────────────────────────────────

    def _create_audio_player(self) -> AudioPlayer:
        s = self._settings
        return AudioPlayer(
            output_device=cast(OutputDeviceSelection, s.audio_output_device) if s.audio_output_device is not None else None,
        )

    # ── 测试兼容属性（委托到 tts_manager）─────────────────────────────────

    @property
    def tts_engine(self):
        return self.tts_manager.engine

    @tts_engine.setter
    def tts_engine(self, engine):
        self.tts_manager.engine = engine

    @property
    def _tts_executor(self):
        return self.tts_manager.executor

    @_tts_executor.setter
    def _tts_executor(self, executor):
        self.tts_manager._executor = executor

    @property
    def _janitor_queue(self):
        return self.tts_manager._janitor_queue

    @_janitor_queue.setter
    def _janitor_queue(self, q):
        self.tts_manager._janitor_queue = q

    @property
    def _janitor_thread(self):
        return self.tts_manager._janitor_thread

    @_janitor_thread.setter
    def _janitor_thread(self, t):
        self.tts_manager._janitor_thread = t

    # ── UI 回调 ───────────────────────────────────────────────────────────

    def _on_submit(self, text: str) -> None:
        """用户提交文本 → TTSManager 串行播放。"""
        self.tts_manager.speak(text)

    def _on_voice_change(self, voice_id: str, voice_name: str) -> None:
        """音色变更 → 写配置 + 更新引擎。"""
        self.tts_manager.set_active_voice(voice_id, voice_name)

    def _on_tts_backend_change(self, tts_backend: str) -> None:
        """TTS 后端切换。"""
        self.tts_manager.switch_backend(tts_backend)
        self.tts_backend = self.tts_manager.tts_backend

    def _on_cartesia_api_key_change(self, api_key: str | None = None) -> None:
        """Cartesia API key 变更。"""
        new_key = (
            api_key if api_key is not None else self._load_cartesia_api_key()
        )
        previous = self.cartesia_api_key
        self.cartesia_api_key = new_key
        try:
            self.tts_manager.update_cartesia_key(new_key)
        except Exception:
            self.cartesia_api_key = previous
            raise

    def _on_tts_api_provider_change(self, provider: str) -> None:
        """TTS 服务商切换。"""
        if provider == self.tts_api_provider:
            return
        self.tts_api_provider = provider
        self.tts_manager.switch_provider(provider)
        # 触发后台加载音色列表（含 loading 指示器）
        self.overlay._start_load_voices(show_status=True)

    def _on_volcengine_credentials_change(
        self, access_key: str | None = None
    ) -> None:
        """Volcengine 凭据变更。"""
        new_key = (
            access_key
            if access_key is not None
            else self._load_volcengine_access_key()
        )
        previous = self.volcengine_access_key
        self.volcengine_access_key = new_key
        try:
            self.tts_manager.update_volcengine_credentials(new_key)
        except Exception:
            self.volcengine_access_key = previous
            raise

    def _on_sidetone_change(self, enabled: bool) -> None:
        """返听开关。"""
        self._sidetone_wrapper.set_sidetone_enabled(enabled)
        self.tts_manager.reset_audio_output()
        logger.debug("返听%s", "已启用" if enabled else "已禁用")

    # ── 启动/关闭 ─────────────────────────────────────────────────────────

    def _should_prewarm(self) -> bool:
        """是否需要后台预热 TTS 连接。"""
        if self.cartesia_api_key:
            return True
        return self.tts_backend not in {
            TTS_BACKEND_CARTESIA_BYTES,
            TTS_BACKEND_CARTESIA_REALTIME,
        }

    def run(self) -> None:
        """启动常驻应用。"""
        logger.info("Wordy 已启动，当前 TTS 后端: %s", self.tts_backend)
        if not self.tts_manager._get_active_voice_id():
            logger.warning(
                "提示：尚未配置音色，请先打开设置，刷新音色列表并选择一个音色。"
            )
        logger.info("按配置的全局快捷键弹出输入框，输入文本后回车播放。")
        tray_app: TrayApp | None = None
        tray_disposed = False
        background_stopped = False

        def dispose_tray_once() -> None:
            nonlocal tray_disposed
            if tray_disposed:
                return
            tray_disposed = True
            if tray_app is not None:
                tray_app.dispose()

        def _stop_background_threads() -> None:
            """在 app.quit() 之前停止后台线程（幂等）。"""
            nonlocal background_stopped
            if background_stopped:
                return
            background_stopped = True
            self.routing.stop()
            self.tts_manager.shutdown()

        try:
            self.overlay.prepare_ui()
            if self._should_prewarm():
                threading.Thread(
                    target=self.tts_manager.connect_async,
                    daemon=True,
                    name="tts-connect",
                ).start()
            tray_app = TrayApp(self.overlay)
            _ = TrayController(tray_app, self.overlay)

            def _pre_stop() -> None:
                dispose_tray_once()
                _stop_background_threads()

            self.overlay.set_pre_stop_hook(_pre_stop)
            self.overlay.run()
        finally:
            dispose_tray_once()
            _stop_background_threads()


def main():
    configure_logging()
    app = WordyApp()
    app.run()


def _flush_std_streams_and_logging() -> None:
    """Best-effort flush of process-visible streams and logging handlers."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.flush()
        except Exception:
            pass
    try:
        logging.shutdown()
    except Exception:
        pass


def _hard_exit_success() -> None:
    """Flush durable breadcrumbs/logs, then skip interpreter teardown on clean exit."""
    _flush_std_streams_and_logging()
    os._exit(0)


def _run_as_main() -> None:
    """Run the app as a script, hard-exiting only after clean completion."""
    try:
        main()
    except SystemExit as exc:
        if exc.code is None or exc.code == 0:
            _hard_exit_success()
            return
        raise
    _hard_exit_success()


if __name__ == "__main__":
    _run_as_main()
