#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS Worker 生命周期管理器。

从 ``main.py:WordyApp`` 抽出，封装 engine/executor 创建、退役、后端/服务商切换、
API key 更新、janitor 线程等逻辑。
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from queue import SimpleQueue

from wordy.config import AppSettings
from wordy.tts.constants import (
    DEFAULT_TTS_BACKEND,
    TTS_API_PROVIDER_VOLCENGINE,
    TTS_BACKEND_VOLCENGINE_STREAMING,
    TTS_BACKENDS,
)
from wordy.tts.engine import BackendTTSEngine, TTSAudioPlayer
from wordy.tts.registry import create_tts_engine, resolve_tts_backend

logger = logging.getLogger(__name__)

_TTS_EXECUTOR_THREAD_NAME_PREFIX = "WordyTTS"
_JANITOR_THREAD_NAME = "WordyTTSJanitor"
_JANITOR_JOIN_TIMEOUT_SECONDS = 30.0


@dataclass
class _RetiredWorker:
    """已退役的 TTS worker 包，等待 janitor 线程异步清理。"""

    executor: ThreadPoolExecutor
    engine: BackendTTSEngine


@dataclass
class _TTSWorker:
    """当前活跃的 TTS 引擎及其串行执行器。"""

    executor: ThreadPoolExecutor
    engine: BackendTTSEngine


def _janitor_loop_inner(janitor_queue: SimpleQueue) -> None:
    """FIFO 退役 worker，先 shutdown executor 再 close engine。

    ``None`` 哨兵退出。异常记日志后继续处理下一个。
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


class TTSManager:
    """TTS worker 生命周期管理器。

    对外暴露 engine/executor 属性，WordyApp 通过回调委托到此实例。
    """

    def __init__(
        self,
        settings: AppSettings,
        audio_player: TTSAudioPlayer,
        cartesia_api_key: str | None,
        volcengine_access_key: str | None,
        tts_api_provider: str,
        tts_backend: str,
    ) -> None:
        self._settings = settings
        self._audio_player = audio_player
        self.cartesia_api_key = cartesia_api_key
        self.volcengine_access_key = volcengine_access_key
        self.tts_api_provider = tts_api_provider
        self.tts_backend = tts_backend

        worker = self._create_worker()
        self.engine = worker.engine
        self._executor = worker.executor

        self._janitor_queue: SimpleQueue = SimpleQueue()
        self._janitor_thread = threading.Thread(
            target=self._janitor_loop,
            name=_JANITOR_THREAD_NAME,
            daemon=True,
        )
        self._janitor_thread.start()

    # ── 公开属性 ──────────────────────────────────────────────────────────

    @property
    def executor(self) -> ThreadPoolExecutor:
        return self._executor

    # ── Worker 创建 ───────────────────────────────────────────────────────

    def _create_engine(self) -> BackendTTSEngine:
        """按当前状态创建 TTS 引擎。"""
        return create_tts_engine(
            self.tts_backend,
            self._audio_player,
            api_key=self.cartesia_api_key,
            voice_id=self._get_active_voice_id(),
            volume=self._settings.volume,
            volcengine_access_key=self.volcengine_access_key,
        )

    @staticmethod
    def _create_executor() -> ThreadPoolExecutor:
        return ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix=_TTS_EXECUTOR_THREAD_NAME_PREFIX,
        )

    def _create_worker(self) -> _TTSWorker:
        return _TTSWorker(
            executor=self._create_executor(),
            engine=self._create_engine(),
        )

    def _install_worker(self, worker: _TTSWorker) -> None:
        self.engine = worker.engine
        self._executor = worker.executor

    # ── 退役/Janitor ──────────────────────────────────────────────────────

    def _enqueue_retire_current(self) -> None:
        self._janitor_queue.put(
            _RetiredWorker(executor=self._executor, engine=self.engine)
        )

    def _janitor_loop(self) -> None:
        _janitor_loop_inner(self._janitor_queue)

    # ── 通用 rebuild ──────────────────────────────────────────────────────

    def _rebuild_worker(self, label: str, rollback, *rollback_args: object) -> None:
        """创建新 worker 并退役旧 worker。失败时回滚状态。"""
        try:
            new_worker = self._create_worker()
        except Exception:
            rollback(*rollback_args)
            raise
        self._enqueue_retire_current()
        self._install_worker(new_worker)
        logger.info("%s，TTS 引擎已重建。", label)

    # ── 后端切换 ──────────────────────────────────────────────────────────

    def switch_backend(self, new_backend: str) -> None:
        """切换 TTS 后端模式。"""
        resolved = resolve_tts_backend(new_backend)
        if resolved == self.tts_backend:
            return
        previous = self.tts_backend
        self.tts_backend = resolved
        self._rebuild_worker(
            f"TTS 模式已切换为: {self.tts_backend}",
            setattr, self, "tts_backend", previous,
        )
        # 懒连接：首次播放时在 worker 中自动连接

    # ── 服务商切换 ────────────────────────────────────────────────────────

    def switch_provider(self, new_provider: str) -> None:
        """切换 TTS 服务商并同步 backend。"""
        if new_provider == self.tts_api_provider:
            return
        previous_provider = self.tts_api_provider
        previous_backend = self.tts_backend
        self.tts_api_provider = new_provider
        self.tts_backend = self._resolve_backend_for_provider()
        self._rebuild_worker(
            f"TTS 服务商已切换为 {new_provider}",
            self._rollback_provider, previous_provider, previous_backend,
        )

    def _rollback_provider(self, provider: str, backend: str) -> None:
        self.tts_api_provider = provider
        self.tts_backend = backend

    def _resolve_backend_for_provider(self) -> str:
        """按当前 provider 解析生效的 backend。"""
        if self.tts_api_provider == TTS_API_PROVIDER_VOLCENGINE:
            configured = getattr(self._settings, "volcengine_tts_backend", None)
            if configured and configured in TTS_BACKENDS:
                return configured
            return TTS_BACKEND_VOLCENGINE_STREAMING
        configured = getattr(self._settings, "cartesia_tts_backend", None)
        if configured and configured in TTS_BACKENDS:
            return configured
        return getattr(self._settings, "tts_backend", None) or DEFAULT_TTS_BACKEND

    # ── API Key 更新 ──────────────────────────────────────────────────────

    def update_cartesia_key(self, new_key: str | None) -> None:
        """更新 Cartesia API key 并重建 worker。"""
        previous = self.cartesia_api_key
        self.cartesia_api_key = new_key
        self._rebuild_worker(
            "Cartesia API key 已更新",
            setattr, self, "cartesia_api_key", previous,
        )

    def update_volcengine_credentials(self, new_key: str | None) -> None:
        """更新 Volcengine 凭据并重建 worker。"""
        previous = self.volcengine_access_key
        self.volcengine_access_key = new_key
        self._rebuild_worker(
            "Volcengine 凭据已更新",
            setattr, self, "volcengine_access_key", previous,
        )

    # ── 音色/音量 ──────────────────────────────────────────────────────────

    def _get_active_voice_id(self) -> str | None:
        """按当前 provider 从配置读取音色 ID。"""
        if self.tts_api_provider == TTS_API_PROVIDER_VOLCENGINE:
            return getattr(self._settings, "volcengine_voice_id", None)
        return getattr(self._settings, "cartesia_voice_id", None)

    def set_active_voice(self, voice_id: str, voice_name: str) -> None:
        """更新音色到配置和引擎。"""
        if self.tts_api_provider == TTS_API_PROVIDER_VOLCENGINE:
            self._settings.update(
                volcengine_voice_id=voice_id, volcengine_voice_name=voice_name
            )
        else:
            self._settings.update(
                cartesia_voice_id=voice_id, cartesia_voice_name=voice_name
            )
        self.engine.set_voice(voice_id)
        logger.info("音色已切换为: %s", voice_name)

    def set_volume(self, volume: float) -> None:
        """更新音量到配置和引擎。"""
        self._settings.volume = volume
        self.engine.set_volume(volume)
        logger.debug("音量已切换为: %.2fx", volume)

    def reset_audio_output(self) -> None:
        """强制 TTS 实时引擎重建音频流（输出设备变更后调用）。"""
        self.engine.reset_audio_output()

    # ── 播放/音色列表 ─────────────────────────────────────────────────────

    def speak(self, text: str) -> None:
        """提交文本到串行执行器生成并播放。"""
        if not text.strip():
            logger.warning("提交文本为空，已忽略。")
            return
        if not self._get_active_voice_id():
            logger.error("缺少音色配置，请先打开设置，刷新音色列表并选择一个音色。")
            return
        self._executor.submit(self._generate_and_play, text)

    def _generate_and_play(self, text: str) -> None:
        """在后台 worker 中生成 TTS 并播放（由 executor 执行）。"""
        try:
            self.engine.speak(text)
        except Exception as e:
            logger.error("%s 播放失败: %s", self.tts_backend, e)

    def fetch_voices(self) -> list[dict[str, object]]:
        """获取当前引擎的可用音色列表。"""
        return self.engine.fetch_voices()

    def connect_async(self) -> None:
        """后台预热 TTS 连接；失败记日志，首次播放时懒连接自动重试。"""
        try:
            self.engine.connect()
        except Exception as e:
            logger.warning(
                "TTS 后台预热连接失败: %s（首次播放时会自动重试）", e
            )

    # ── 关闭 ───────────────────────────────────────────────────────────────

    def shutdown(self) -> bool:
        """入队退役当前 worker + 哨兵，等待 janitor 线程退出。

        返回 True 表示 janitor 正常退出，False 表示超时。
        """
        self._enqueue_retire_current()
        self._janitor_queue.put(None)
        self._janitor_thread.join(timeout=_JANITOR_JOIN_TIMEOUT_SECONDS)
        alive = self._janitor_thread.is_alive()
        if alive:
            logger.warning(
                "TTS janitor 线程未能在 %s 秒内退出",
                _JANITOR_JOIN_TIMEOUT_SECONDS,
            )
            return False
        return True
