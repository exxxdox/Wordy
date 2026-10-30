#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS Worker 生命周期管理器。

从 ``main.py:WordyApp`` 抽出，封装 engine/executor 创建、退役、后端/服务商切换、
API key 更新、janitor 线程等逻辑。
"""

from __future__ import annotations

import logging
import threading
from threading import Lock
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from collections.abc import Callable
from pathlib import Path
from dataclasses import dataclass, field
from queue import SimpleQueue

from wordy.config import AppSettings
from wordy.audio.clips import decoded_audio_clip
from wordy.tts.constants import (
    TTS_API_PROVIDER_VOLCENGINE,
    TTS_BACKENDS_BY_PROVIDER,
)
from wordy.tts.engine import BackendTTSEngine, TTSAudioPlayer
from wordy.tts.registry import create_tts_engine, resolve_tts_backend

logger = logging.getLogger(__name__)

_TTS_EXECUTOR_THREAD_NAME_PREFIX = "WordyTTS"
_JANITOR_THREAD_NAME = "WordyTTSJanitor"
_JANITOR_JOIN_TIMEOUT_SECONDS = 30.0


@dataclass
class _TTSWorker:
    """TTS 引擎及其串行执行器；退役状态由清理队列表示，复用同一包。"""

    executor: ThreadPoolExecutor
    engine: BackendTTSEngine
    # 新旧引擎共用播放器；旧 worker 清理完后才允许后继打开音频流。
    ready: threading.Event | None = None
    next_ready: threading.Event | None = None


@dataclass
class _SpeechJob:
    """一次朗读及其提交时依赖；待播槽只保留最新一项。"""

    text: str
    engine: BackendTTSEngine
    executor: ThreadPoolExecutor
    ready: threading.Event | None
    backend: str
    on_finished: Callable[[], None] | None
    on_error: Callable[[str], None] | None
    # 每次朗读独立的取消句柄：stop_speech 只打断提交时对应的那一次，
    # 用引擎级共享事件会误杀紧接着提交的下一段。
    cancel: threading.Event = field(default_factory=threading.Event)


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
        if item.ready is not None:
            item.ready.wait()
        try:
            executor.shutdown(wait=True)
        except Exception as e:
            logger.exception("TTS 清理：执行器关闭失败: %s", e)
        try:
            engine.close()
        except Exception as e:
            logger.exception("TTS 清理：引擎关闭失败: %s", e)
        finally:
            if item.next_ready is not None:
                item.next_ready.set()


class TTSManager:
    """TTS worker 生命周期管理器。

    对外暴露 engine/executor 属性，WordyApp 通过 GUI 线程回调提交操作。
    预热与播放由 worker executor 执行，切换状态只在 GUI 线程更新。
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
        self._worker_ready: threading.Event | None = None
        # 本地文件可能很长；退出必须停止文件任务，不能等待整段音频播完。
        self._audio_files_stopped = threading.Event()
        # 单次文件播放的取消句柄：让「停止试听」只打断这一次，不牵连后续播放。
        self._clip_cancel: threading.Event | None = None
        self._speech_lock = Lock()
        self._speech_running = False
        self._pending_speech: _SpeechJob | None = None
        # 正在朗读那一次的取消句柄；worker 结束后清空。
        self._speech_cancel: threading.Event | None = None

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
        # 引擎构建失败时不提前分配一个无人关闭的 executor。
        engine = self._create_engine()
        return _TTSWorker(
            executor=self._create_executor(),
            engine=engine,
        )

    def _install_worker(self, worker: _TTSWorker) -> None:
        self.engine = worker.engine
        self._executor = worker.executor
        self._worker_ready = worker.ready

    # ── 退役/Janitor ──────────────────────────────────────────────────────

    def _enqueue_retire_current(self, next_ready: threading.Event | None = None) -> None:
        self._janitor_queue.put(
            _TTSWorker(executor=self._executor, engine=self.engine,
                       ready=self._worker_ready, next_ready=next_ready)
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
        new_worker.ready = threading.Event()
        self._enqueue_retire_current(next_ready=new_worker.ready)
        self._install_worker(new_worker)
        logger.info("%s，TTS 引擎已重建。", label)

    # ── 后端切换 ──────────────────────────────────────────────────────────

    def switch_backend(self, new_backend: str) -> None:
        """切换 TTS 后端模式。"""
        resolved = resolve_tts_backend(new_backend, self.tts_api_provider)
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
        allowed = TTS_BACKENDS_BY_PROVIDER[self.tts_api_provider]
        if self.tts_api_provider == TTS_API_PROVIDER_VOLCENGINE:
            configured = getattr(self._settings, "volcengine_tts_backend", None)
            if configured and configured in allowed:
                return configured
            return allowed[0]
        configured = getattr(self._settings, "cartesia_tts_backend", None)
        if configured and configured in allowed:
            return configured
        fallback = getattr(self._settings, "tts_backend", None)
        if fallback and fallback in allowed:
            return fallback
        return allowed[0]

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
            # 火山音色只存列表：首条即当前音色。面板通常已排好序，避免重复落盘。
            entries = self._settings.volcengine_voice_list
            if not entries or entries[0]["id"] != voice_id:
                self._settings.update(
                    volcengine_voice_list=[
                        {"id": voice_id, "name": voice_name},
                        *[entry for entry in entries if entry["id"] != voice_id],
                    ]
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
        """强制 TTS 实时引擎重建音频流（输出设备/返听开关变更后调用）。

        调用方在 GUI 线程，而实时引擎的 ``reset_audio_output`` 要抢与 ``speak()``
        同一把锁——引擎正在朗读时同步执行会把界面卡到整段读完。投递到串行 worker
        既让出 GUI 线程，又靠 FIFO 保住「先关流、后开流」的顺序。
        """
        # 交接期间新引擎尚未打开音频流，无需触碰旧 worker 正在使用的播放器。
        if self._worker_ready is not None and not self._worker_ready.is_set():
            return
        try:
            self._executor.submit(
                self._reset_engine_output, self.engine, self._worker_ready
            )
        except RuntimeError as error:
            # 退出后 executor 已关闭；调用方在 GUI 线程，不能把异常抛回去。
            logger.warning("TTS worker 不可用，音频流重建已跳过: %s", error)

    def _reset_engine_output(
        self, engine: BackendTTSEngine, ready: threading.Event | None
    ) -> None:
        """在 worker 线程中重建引擎音频流；失败只记日志，不影响后续队列。"""
        try:
            if ready is not None:
                ready.wait()
            engine.reset_audio_output()
        except Exception as e:
            logger.warning("重建 TTS 音频流失败: %s", e)

    # ── 播放/音色列表 ─────────────────────────────────────────────────────

    def speak(self, text: str, on_finished: Callable[[], None] | None = None,
              on_error: Callable[[str], None] | None = None) -> None:
        """提交文本到串行执行器生成并播放。

        ``on_finished`` 在朗读结束（含早退、失败、被取消）后调用一次。
        朗读与本地音频共用串行 worker：新朗读先停掉正在播的片段，否则要整段
        播完才轮到文字（音频槽开播同样会先停朗读，见 ``ChatWheelController``）。
        """
        if not text.strip():
            logger.warning("提交文本为空，已忽略。")
            self._notify_finished(on_finished)
            return
        if not self._get_active_voice_id():
            logger.error("缺少音色配置，请先打开设置，刷新音色列表并选择一个音色。")
            self._notify_finished(on_finished)
            return
        # 文字也要能打断音频槽：不先停片段，新朗读会排队等它整段播完。
        self.stop_audio_file()
        # 提交时绑定引擎和 executor，服务商切换后待播文本也不会串到新 worker。
        job = _SpeechJob(text, self.engine, self._executor, self._worker_ready,
                         self.tts_backend, on_finished, on_error)
        replaced: _SpeechJob | None = None
        submit_now = False
        with self._speech_lock:
            if self._speech_running:
                replaced = self._pending_speech
                self._pending_speech = job
            else:
                self._speech_running = True
                submit_now = True
                self._speech_cancel = job.cancel
        if replaced is not None:
            # 回调可能再次提交朗读，不能在持有队列锁时调用用户代码。
            self._notify_finished(replaced.on_finished)
        if not submit_now:
            return
        self._submit_speech(job)

    def stop_speech(self) -> None:
        """打断当前文字朗读，并丢弃排队中的待读文本。

        引擎没有强杀能力，worker 线程在下一次写入前的自检处退出；排队任务不会再
        被提交，所以它的完成回调必须在这里补发。
        """
        with self._speech_lock:
            cancel = self._speech_cancel
            pending = self._pending_speech
            self._pending_speech = None
        if cancel is not None:
            cancel.set()
        if pending is not None:
            # 回调可能再次提交朗读，不能在持有队列锁时调用用户代码。
            self._notify_finished(pending.on_finished)

    def _submit_speech(self, job: _SpeechJob) -> None:
        """提交已绑定 worker 的任务；executor 关闭时也释放队列状态。"""
        try:
            job.executor.submit(self._generate_and_play, job)
        except RuntimeError as error:
            logger.warning("TTS worker 不可用，文本未提交: %s", error)
            self._notify_finished(job.on_finished)
            self._advance_speech_queue()

    @staticmethod
    def _notify_finished(on_finished: Callable[[], None] | None) -> None:
        if on_finished is not None:
            on_finished()

    def _generate_and_play(self, job: _SpeechJob) -> None:
        """在后台 worker 中生成 TTS 并播放（由 executor 执行）。"""
        try:
            if job.ready is not None:
                job.ready.wait()
            # 排队等待旧引擎交接期间也可能已被停止，置位后不必再开口。
            if job.cancel.is_set():
                return
            if not job.engine.speak(job.text, job.cancel):
                # 被取消不是失败：引擎按约定返回 False 表示「没播完」。
                if job.cancel.is_set():
                    return
                message = f"{job.backend} 播放失败：未输出音频"
                logger.error(message)
                if job.on_error is not None:
                    job.on_error(message)
        except Exception as e:
            if job.cancel.is_set():
                return
            message = f"{job.backend} 播放失败：{e}"
            logger.error(message)
            if job.on_error is not None:
                job.on_error(message)
        finally:
            with self._speech_lock:
                if self._speech_cancel is job.cancel:
                    self._speech_cancel = None
            self._notify_finished(job.on_finished)
            self._advance_speech_queue()

    def _advance_speech_queue(self) -> None:
        with self._speech_lock:
            next_job = self._pending_speech
            self._pending_speech = None
            if next_job is None:
                self._speech_running = False
                return
        self._submit_speech(next_job)

    def fetch_voices(self) -> list[dict[str, object]]:
        """获取当前引擎的可用音色列表。"""
        return self.engine.fetch_voices()

    def play_audio_file(self, path: Path, on_error: Callable[[str], None] | None = None,
                        on_finished: Callable[[], None] | None = None, *, volume: float = 1.0,
                        on_progress: Callable[[float], None] | None = None) -> None:
        """本地文件与 TTS 共用串行 worker，不要求密钥或音色。"""
        # 新播放顶掉旧播放，避免连点两次后两条音频叠在一起。
        self.stop_audio_file()
        cancel = threading.Event()
        self._clip_cancel = cancel
        if not self._audio_files_stopped.is_set():
            progress: dict[str, Any] = {"on_progress": on_progress} if on_progress is not None else {}
            self._executor.submit(self._play_audio_file, path, self.engine, self._worker_ready,
                                  on_error, volume, cancel, on_finished, **progress)

    def stop_audio_file(self) -> None:
        """打断当前文件播放；没有正在播放的片段时什么也不做。

        只作用于这一次播放——全局退出走 ``_audio_files_stopped``。
        """
        cancel = self._clip_cancel
        if cancel is not None:
            cancel.set()

    def _play_audio_file(self, path: Path, engine: BackendTTSEngine,
                         ready: threading.Event | None, on_error: Callable[[str], None] | None,
                         volume: float,
                         cancel: threading.Event,
                         on_finished: Callable[[], None] | None,
                         on_progress: Callable[[float], None] | None = None) -> None:
        try:
            if ready is not None:
                ready.wait()
            if self._audio_files_stopped.is_set():
                return
            # reset 会立即重开实时流；关闭引擎才真正释放设备，下次 speak 自动连接。
            engine.close()
            with decoded_audio_clip(path, cancel) as wav_path:
                # 按提交时的槽位音量播放，避免排队期间切槽或调音串到其他音频。
                progress: dict[str, Any] = {"on_progress": on_progress} if on_progress is not None else {}
                if not self._audio_player.play_wav(str(wav_path), cancel_event=cancel, volume=volume, **progress):
                    if cancel.is_set():
                        return
                    # play_wav 现在对「打不开设备」和「播到一半失败」都返回 False，
                    # 文案不能再断言是打开失败。
                    raise RuntimeError("音频输出不可用")
        except InterruptedError:
            return
        except Exception as error:
            logger.error("音频文件播放失败: %s", error)
            if on_error is not None:
                on_error(f"音频播放失败：{error}")
        finally:
            if self._clip_cancel is cancel:
                self._clip_cancel = None
            # 播完、被打断、失败都要回调，否则设置页的「停止」按钮会一直挂着。
            if on_finished is not None:
                on_finished()

    def connect_async(self) -> None:
        """后台预热 TTS 连接；失败记日志，首次播放时懒连接自动重试。"""
        # 预热与播放共用 worker 队列，防止退役关闭后后台线程再次打开旧音频流。
        try:
            self._executor.submit(self._connect_engine, self.engine, self._worker_ready)
        except RuntimeError as e:
            logger.warning("TTS 预热提交失败: %s", e)

    def _connect_engine(self, engine: BackendTTSEngine, ready: threading.Event | None) -> None:
        try:
            if ready is not None:
                ready.wait()
            engine.connect()
        except Exception as e:
            logger.warning(
                "TTS 后台预热连接失败: %s（首次播放时会自动重试）", e
            )

    # ── 关闭 ───────────────────────────────────────────────────────────────

    def shutdown(self) -> bool:
        """入队退役当前 worker + 哨兵，等待 janitor 线程退出。

        返回 True 表示 janitor 正常退出，False 表示超时。
        """
        self._audio_files_stopped.set()
        # 播放层现在只看单次句柄，退出必须显式打断正在播的那一段。
        self.stop_audio_file()
        # 文字朗读同理：不打断就要等引擎把整段播完（bytes 模式还可能等满 HTTP 超时）。
        self.stop_speech()
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
