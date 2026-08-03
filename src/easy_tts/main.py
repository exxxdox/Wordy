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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from queue import SimpleQueue
from typing import cast

from pathlib import Path
from tempfile import gettempdir

from easy_tts.config import AppSettings
from easy_tts.audio.player import AudioPlayer, OutputDeviceSelection
from easy_tts.audio.router import AudioRouter
from easy_tts.audio.driver import VBCableDriverManager
from easy_tts.ui.overlay import InputOverlay
from easy_tts.log import install_log_stream
import easy_tts.secret
from easy_tts.secret import KeyringUnavailableError
from easy_tts.ui.tray import TrayApp, TrayController
from easy_tts.tts.constants import TTS_BACKEND_CARTESIA_BYTES, TTS_BACKEND_CARTESIA_REALTIME
from easy_tts.tts.registry import create_tts_engine, resolve_tts_backend
from easy_tts.tts.engine import BackendTTSEngine


logger = logging.getLogger(__name__)

_TTS_EXECUTOR_THREAD_NAME_PREFIX = "WavTransTTS"
_JANITOR_THREAD_NAME = "WavTransTTSJanitor"
_JANITOR_JOIN_TIMEOUT_SECONDS = 30.0


@dataclass
class _RetiredWorker:
    """A retired TTS worker bundle awaiting deferred cleanup by the janitor thread."""

    executor: ThreadPoolExecutor
    engine: BackendTTSEngine


@dataclass
class _TTSWorker:
    """The active TTS engine and its serial executor."""

    executor: ThreadPoolExecutor
    engine: BackendTTSEngine


def _janitor_loop_inner(janitor_queue: SimpleQueue) -> None:
    """Drain retired TTS workers FIFO, shutting down each executor before closing its engine.

    Exits on a ``None`` sentinel. Exceptions in ``executor.shutdown`` or
    ``engine.close`` are logged and the loop continues with the next worker.
    If shutdown fails, close is still attempted; if close fails, processing
    moves on to the next worker.
    """
    while True:
        item = janitor_queue.get()
        if item is None:
            return
        executor = item.executor
        engine = item.engine
        try:
            executor.shutdown(wait=True)
        except Exception as e:
            logger.exception("TTS 清理：执行器关闭失败: %s", e)
        try:
            engine.close()
        except Exception as e:
            logger.exception("TTS 清理：引擎关闭失败: %s", e)


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


class WavTransApp:
    """常驻热键输入 → TTS 生成 → 播放，新提交不主动中断上一段播放。"""

    def __init__(self, tts_backend: str | None = None):
        # 音频路由引擎（先初始化为 None，避免 _create_audio_player 访问时出错）
        self._router: AudioRouter | None = None
        self._test_recording_path: str | None = None
        self._settings = AppSettings.load()
        self.player = self._create_audio_player()
        self.tts_backend = resolve_tts_backend(tts_backend or self._settings.tts_backend)
        self.cartesia_api_key = self._load_cartesia_api_key()
        self.voice_id = self._settings.voice_id
        self.volume = self._settings.volume
        self._install_tts_worker(self._create_tts_worker())

        self._init_audio_router()
        if self._router is not None:
            self.player.set_router(self._router)

        self._janitor_queue: SimpleQueue = SimpleQueue()
        self._janitor_thread = threading.Thread(
            target=self._janitor_loop,
            name=_JANITOR_THREAD_NAME,
            daemon=True,
        )
        self._janitor_thread.start()

        self.overlay = InputOverlay(
            on_submit=self._on_submit,
            on_voice_change=self._on_voice_change,
            on_volume_change=self._on_volume_change,
            on_tts_backend_change=self._on_tts_backend_change,
            on_fetch_voices=self._fetch_voices,
            on_audio_output_change=self._on_audio_output_change,
            on_cartesia_api_key_change=self._on_cartesia_api_key_change,
            on_audio_route_change=self._on_audio_route_change,
            on_audio_route_test=self._on_audio_route_test,
            on_play_route_test=self._play_route_test_recording,
            audio_player=self.player,
        )

    @staticmethod
    def _load_cartesia_api_key() -> str | None:
        """从 secret_store 读取 Cartesia API key，失败时不暴露异常内容。"""
        try:
            return easy_tts.secret.load_cartesia_api_key()
        except Exception as exc:
            if isinstance(exc, KeyringUnavailableError):
                return None
            raise RuntimeError("Cartesia API key 读取失败") from exc

    def _create_tts_engine(self) -> BackendTTSEngine:
        """按当前配置创建 TTS 引擎。"""
        return create_tts_engine(
            self.tts_backend,
            self.player,
            api_key=self.cartesia_api_key,
            voice_id=self.voice_id,
            volume=self.volume,
        )

    def _create_audio_player(self) -> AudioPlayer:
        s = self._settings
        return AudioPlayer(
            output_device=cast(OutputDeviceSelection, s.audio_output_device) if s.audio_output_device is not None else None,
            output_device_name=s.audio_output_device_name,
        )

    # ── 音频路由生命周期 ─────────────────────────────────────────────

    def _init_audio_router(self) -> None:
        """根据配置初始化音频路由引擎（仅启动时调用）。"""
        s = self._settings
        if not s.audio_routing_enabled:
            return
        self._router = self._try_create_router(
            mic_device=s.mic_input_device,
            bridge_device=s.bridge_source_device,
            virtual_output=s.virtual_output_device,
            mic_gain=s.mic_gain,
            bridge_gain=s.bridge_gain,
            tts_gain=s.tts_gain,
        )

    def _try_create_router(self, *, mic_device: object, bridge_device: object,
                           virtual_output: object,
                           mic_gain: object, bridge_gain: object, tts_gain: object,
                           ) -> AudioRouter | None:
        """创建并启动 AudioRouter，失败返回 None。"""
        if not VBCableDriverManager.is_installed():
            logger.warning("VB-CABLE 未安装，无法启动音频路由")
            return None
        try:
            router = AudioRouter(
                mic_device=mic_device if isinstance(mic_device, str) else None,
                bridge_device=bridge_device if isinstance(bridge_device, str) else None,
                virtual_output=virtual_output if isinstance(virtual_output, str) else None,
            )
            # 应用增益（仅传入有效数值）
            gains: dict[str, float] = {}
            for key, val in (("mic", mic_gain), ("bridge", bridge_gain), ("tts", tts_gain)):
                if isinstance(val, (int, float)):
                    gains[key] = float(val)
            if gains:
                router.set_gains(**gains)
            if router.start():
                logger.info("音频路由引擎已启动")
                return router
            logger.warning("音频路由引擎启动失败")
            return None
        except Exception as e:
            logger.exception("音频路由引擎启动失败: %s", e)
            return None

    def _on_audio_route_change(self, route_config: dict[str, object]) -> None:
        """音频路由配置变更回调。"""
        enabled = (
            bool(route_config["audio_routing_enabled"])
            if "audio_routing_enabled" in route_config
            else self._router is not None and self._router.is_running()
        )
        if not enabled:
            self._stop_router()
            return

        mic_device = route_config.get("mic_input_device")
        bridge_device = route_config.get("bridge_source_device")
        virtual_device = route_config.get("virtual_output_device")
        mic_gain = route_config.get("mic_gain")
        bridge_gain = route_config.get("bridge_gain")
        tts_gain = route_config.get("tts_gain")

        if self._router is None:
            # 新建路由器
            self._router = self._try_create_router(
                mic_device=mic_device, bridge_device=bridge_device,
                virtual_output=virtual_device,
                mic_gain=mic_gain, bridge_gain=bridge_gain, tts_gain=tts_gain,
            )
            self.player.set_router(self._router)
        else:
            # 动态更新运行中的路由器
            if "mic_input_device" in route_config:
                self._router.set_mic_device(mic_device if isinstance(mic_device, str) else None)
            if "bridge_source_device" in route_config:
                self._router.set_bridge_device(bridge_device if isinstance(bridge_device, str) else None)
            if "virtual_output_device" in route_config:
                self._router.set_virtual_output(virtual_device if isinstance(virtual_device, str) else None)
            gain_kwargs: dict[str, float] = {}
            for key, val in (("mic", mic_gain), ("bridge", bridge_gain), ("tts", tts_gain)):
                if isinstance(val, (int, float)):
                    gain_kwargs[key] = float(val)
            if gain_kwargs:
                self._router.set_gains(**gain_kwargs)
            logger.info("音频路由配置已更新")

    def _stop_router(self) -> None:
        """停止音频路由引擎并解除 player 绑定。"""
        if self._router is not None:
            self._router.stop()
            self._router = None
            self.player.set_router(None)
            logger.info("音频路由已禁用")

    def _on_audio_route_test(self) -> str | None:
        """启动音频路由测试：录制 5 秒混音（含 TTS "测试测试测试"），返回临时 WAV 文件路径。"""
        if self._router is None or not self._router.is_running():
            logger.warning("音频路由未运行，无法启动测试")
            return None

        # 启动录制
        self._router.start_test_recording()

        # 触发 TTS "测试测试测试"
        self._on_submit("测试测试测试")

        # 5 秒后停止并保存
        def _finish_test() -> None:
            try:
                router = self._router
                if router is None:
                    logger.warning("测试录制完成前路由引擎已停止，录制丢弃")
                    return
                wav_bytes = router.stop_test_recording()
                if wav_bytes is None:
                    logger.warning("测试录制未捕获到数据")
                    return
                test_path = Path(gettempdir()) / "wavtrans_route_test.wav"
                test_path.write_bytes(wav_bytes)
                self._test_recording_path = str(test_path)
                logger.info("测试录制已保存到 %s", self._test_recording_path)
                # 通知 settings_window
                if self.overlay is not None:
                    self.overlay.notify_route_test_finished(self._test_recording_path)
            except Exception:
                logger.exception("测试录制保存失败")

        timer = threading.Timer(5.0, _finish_test)
        timer.name = "RouteTestTimer"
        timer.daemon = True
        timer.start()
        return "recording"

    def _play_route_test_recording(self) -> bool:
        """播放最近一次录制的测试音频。在后台线程执行以避免阻塞 UI。"""
        path = getattr(self, "_test_recording_path", None)
        if not path:
            logger.warning("没有可用的测试录制文件")
            return False
        if not Path(path).exists():
            logger.warning("测试录制文件不存在: %s", path)
            return False

        def _play_in_background() -> None:
            try:
                success = self.player.play_wav(path)
                if not success:
                    logger.warning("播放测试录制返回失败")
            except Exception:
                logger.exception("播放测试录制失败")

        threading.Thread(target=_play_in_background, name="PlayRouteTest", daemon=True).start()
        return True

    @staticmethod
    def _create_tts_executor() -> ThreadPoolExecutor:
        """创建串行执行 TTS 任务的线程池（单 worker）。"""
        return ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=_TTS_EXECUTOR_THREAD_NAME_PREFIX,
        )

    def _create_tts_worker(self) -> _TTSWorker:
        """Create a fresh TTS worker bundle from the current app state."""
        return _TTSWorker(
            executor=self._create_tts_executor(),
            engine=self._create_tts_engine(),
        )

    def _install_tts_worker(self, worker: _TTSWorker) -> None:
        """Expose a worker bundle through the legacy app attributes."""
        self.tts_engine = worker.engine
        self._tts_executor = worker.executor

    def _on_submit(self, text: str) -> None:
        """用户提交文本后的回调：提交新的生成+播放任务到串行执行器。"""
        if not text.strip():
            logger.warning("提交文本为空，已忽略。")
            return

        if not self.voice_id:
            logger.error("缺少音色配置，请先打开设置，刷新音色列表并选择一个音色。")
            return

        # 新提交不会主动中断上一段播放；执行器单 worker 保证串行。
        engine = self.tts_engine
        backend = self.tts_backend
        self._tts_executor.submit(self._generate_and_play, engine, backend, text)

    def _fetch_voices(self) -> list[dict[str, object]]:
        """通过当前 TTS 引擎获取可用音色列表。"""
        return self.tts_engine.fetch_voices()

    def _on_voice_change(self, voice_id: str, voice_name: str) -> None:
        """音色配置变更后同步到当前 TTS 引擎。"""
        self.voice_id = voice_id
        self.tts_engine.set_voice(voice_id)
        logger.info("音色已切换为: %s", voice_name)

    def _on_volume_change(self, volume: float) -> None:
        """音量配置变更后同步到当前 TTS 引擎。"""
        self.volume = volume
        self.tts_engine.set_volume(volume)
        logger.info("音量已切换为: %.2fx", volume)

    def _on_audio_output_change(self, device: object) -> None:
        """音频输出设备变更后同步到当前播放器。"""
        if isinstance(device, dict) or device is None:
            self.player.set_output_device(cast(OutputDeviceSelection, device) if device is not None else None)
            if device is None:
                self.player.set_output_device_name(None)
            name_value = device.get("name") if isinstance(device, dict) else None
            device_name = name_value if isinstance(name_value, str) else None
            logger.info("音频输出设备已切换为: %s", device_name if device_name else "系统默认")
            return

        device_name = device if isinstance(device, str) else None
        self.player.set_output_device_name(device_name)
        logger.info("音频输出设备已切换为: %s", device_name if device_name else "系统默认")

    def _on_tts_backend_change(self, tts_backend: str) -> None:
        """TTS 模式变更后切换当前后端引擎。

        旧的 executor+engine 会作为一个 _RetiredWorker 入队，由 janitor 线程
        异步先 shutdown 再 close，避免阻塞 UI 线程；新的 executor/engine 立即可用。
        """
        next_backend = resolve_tts_backend(tts_backend)
        if next_backend == self.tts_backend:
            return

        previous_backend = self.tts_backend
        self.tts_backend = next_backend
        try:
            new_worker = self._create_tts_worker()
        except Exception:
            self.tts_backend = previous_backend
            raise
        self._enqueue_retire_current()
        self._install_tts_worker(new_worker)
        # 懒连接避免 UI 线程被 PyAudio/websocket 阻塞；首次播放会在 worker 中连接。
        logger.info("TTS 模式已切换为: %s", self.tts_backend)

    def _on_cartesia_api_key_change(self, api_key: str | None = None) -> None:
        """Cartesia API key 变更后重建当前 TTS worker。"""
        previous_api_key = self.cartesia_api_key
        new_api_key = api_key if api_key is not None else self._load_cartesia_api_key()
        self.cartesia_api_key = new_api_key
        try:
            new_worker = self._create_tts_worker()
        except Exception:
            self.cartesia_api_key = previous_api_key
            raise
        self._enqueue_retire_current()
        self._install_tts_worker(new_worker)
        logger.info("Cartesia API key 已更新，TTS 引擎已重建。")

    def _enqueue_retire_current(self) -> None:
        """把当前 executor+engine 作为一个 _RetiredWorker 入队 janitor。"""
        self._janitor_queue.put(_RetiredWorker(executor=self._tts_executor, engine=self.tts_engine))

    def _janitor_loop(self) -> None:
        """实例方法包装：把 self._janitor_queue 传给顶层 janitor 循环。"""
        _janitor_loop_inner(self._janitor_queue)

    def _generate_and_play(self, engine: BackendTTSEngine, backend: str, text: str) -> None:
        """在后台 worker 中通过指定 TTS 引擎生成并播放。"""
        try:
            # 单 worker 执行器保证同一时间只有一个 speak 在运行，
            # 新提交会排队，不主动中断上一段播放。
            engine.speak(text)
        except Exception as e:
            logger.error("%s 播放失败: %s", backend, e)

    def run(self) -> None:
        """启动常驻应用。"""
        logger.info("WavTrans 已启动，当前 TTS 后端: %s", self.tts_backend)
        if not self.voice_id:
            logger.warning("提示：尚未配置音色，请先打开设置，刷新音色列表并选择一个音色。")
        logger.info("按配置的全局快捷键弹出输入框，输入文本后回车播放。")
        tray_app: TrayApp | None = None
        tray_disposed = False

        def dispose_tray_once() -> None:
            nonlocal tray_disposed
            if tray_disposed:
                return
            tray_disposed = True
            if tray_app is not None:
                tray_app.dispose()

        def _stop_background_threads() -> None:
            """在 app.quit() 之前停止后台线程，避免 Qt 清理时线程仍在运行。"""
            self._stop_router()
            self._enqueue_retire_current()
            self._janitor_queue.put(None)
            self._janitor_thread.join(timeout=_JANITOR_JOIN_TIMEOUT_SECONDS)

        try:
            if self.cartesia_api_key or self.tts_backend not in {TTS_BACKEND_CARTESIA_BYTES, TTS_BACKEND_CARTESIA_REALTIME}:
                self.tts_engine.connect()
            self.overlay.prepare_ui()
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
            alive = self._janitor_thread.is_alive()
            if alive:
                logger.warning("TTS janitor 线程未能在 %s 秒内退出", _JANITOR_JOIN_TIMEOUT_SECONDS)


def main():
    configure_logging()
    app = WavTransApp()
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
