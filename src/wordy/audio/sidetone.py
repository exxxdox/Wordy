#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""返听（Sidetone）包装器：将 TTS 音频同时输出到主设备和系统默认设备。

返听流写入通过容量为 1 的队列异步执行，避免慢设备无限积压。
"""

from __future__ import annotations

import logging
from queue import Full, Queue
from threading import Event, Thread
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wordy.audio.player import AudioPlayer

logger = logging.getLogger(__name__)

_STREAM_CLOSE_TIMEOUT_SECONDS = 0.1
_STREAM_STOP = object()


class SidetoneAudioPlayer:
    """TTSAudioPlayer 协议包装器，同时输出到主设备和系统默认返听设备。

    通过 ``set_sidetone_enabled()`` 控制返听开关。
    开关关闭时所有方法近乎零开销直通主播放器（仅一个 bool 判断）。
    返听失败不影响主链路：stream 打开失败、写入异常均记录 warning 并继续。

    返听 write_stream 通过单个后台线程异步执行。队列只允许一个待写块；
    慢设备造成积压时停用本次返听，主链路继续播放。
    """

    def __init__(self, main_player: AudioPlayer, sidetone_player: AudioPlayer) -> None:
        self._main = main_player
        self._sidetone = sidetone_player
        self._sidetone_enabled = False
        # 返听流是否已确认不可用（打开失败或写入异常后置位，避免重复报错）
        self._sidetone_stream_dead = False
        self._sidetone_queue: Queue[bytes | object] | None = None
        self._sidetone_thread: Thread | None = None

    # ── 开关 ──────────────────────────────────────────────────────────

    def set_sidetone_enabled(self, enabled: bool) -> None:
        """启用或停用返听。停用后需调用方自行 reset_audio_output 重建流。"""
        self._sidetone_enabled = enabled

    @property
    def sidetone_enabled(self) -> bool:
        return self._sidetone_enabled

    # 输出设备选择由 RoutingController 直接设置主播放器，包装器只代理 TTS 所需能力。
    # ── 属性代理（主播放器） ──────────────────────────────────────────

    @property
    def output_device_name(self) -> str | None:
        return self._main.output_device_name

    # ── TTSAudioPlayer 协议方法 ──────────────────────────────────────

    def _needs_sidetone(self, device_index: int | None) -> bool:
        # 主输出已经使用系统默认设备时，再返听会叠加同一段声音，造成爆音和回声。
        return self._sidetone_enabled and (
            device_index is not None or self._main.output_device_name is not None
        )

    def play_wav(
        self,
        wav_path: str | BytesIO,
        device_index: int | None = None,
        cancel_event: Event | None = None,
    ) -> bool:
        """同时播放主输出和返听；主输出结果决定调用是否成功。"""
        if not self._needs_sidetone(device_index):
            if cancel_event is None:
                return self._main.play_wav(wav_path, device_index=device_index)
            return self._main.play_wav(
                wav_path, device_index=device_index, cancel_event=cancel_event
            )

        # 两个播放器不能并发消费同一个 BytesIO 游标。
        sidetone_path = BytesIO(wav_path.getvalue()) if isinstance(wav_path, BytesIO) else wav_path

        def play_main() -> bool:
            if cancel_event is None:
                return self._main.play_wav(wav_path, device_index=device_index)
            return self._main.play_wav(
                wav_path, device_index=device_index, cancel_event=cancel_event
            )

        def play_sidetone() -> None:
            try:
                if cancel_event is None:
                    self._sidetone.play_wav(sidetone_path, device_index=None)
                else:
                    self._sidetone.play_wav(
                        sidetone_path, device_index=None, cancel_event=cancel_event
                    )
            except Exception:
                logger.warning("返听 WAV 播放失败", exc_info=True)

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="sidetone-wav") as executor:
            main_future = executor.submit(play_main)
            sidetone_future = executor.submit(play_sidetone)
            result = main_future.result()
            sidetone_future.result()
        return result and (cancel_event is None or not cancel_event.is_set())

    def open_stream(
        self,
        audio_format: object,
        channels: int,
        rate: int,
        device_index: int | None = None,
        frames_per_buffer: int = 1024,
    ) -> bool:
        """打开流：主设备 → 返听设备（如启用）。

        主设备打开失败直接返回 False。返听设备失败仅记录 warning，
        置位 _sidetone_stream_dead 避免后续 write_stream 重复尝试。
        """
        main_ok = self._main.open_stream(
            audio_format, channels, rate,
            device_index=device_index,
            frames_per_buffer=frames_per_buffer,
        )
        if not main_ok:
            return False

        self._sidetone_stream_dead = False
        if not self._needs_sidetone(device_index):
            self._sidetone_stream_dead = True
            return True

        try:
            # 主设备可能回退了格式或采样率，返听必须按实际 PCM 配置打开。
            main_config = self._main.get_stream_config()
            sidetone_ok = self._sidetone.open_stream(
                main_config.get("format") or audio_format, channels,
                main_config.get("rate") or rate,
                device_index=None,
                frames_per_buffer=frames_per_buffer,
            )
            if not sidetone_ok:
                self._sidetone_stream_dead = True
                logger.warning("返听设备流打开失败，仅主链路工作")
            elif self._sidetone.get_stream_config() != main_config:
                # 不把同一 PCM 当作另一种格式播放；不兼容时保留主输出。
                self._sidetone_stream_dead = True
                self._sidetone.close_stream()
                logger.warning("返听设备实际格式或采样率与主输出不一致，仅主链路工作")
        except Exception:
            self._sidetone_stream_dead = True
            logger.warning("返听设备流打开失败", exc_info=True)

        if not self._sidetone_stream_dead:
            self._sidetone_queue = Queue(maxsize=1)
            self._sidetone_thread = Thread(
                target=self._sidetone_write_loop,
                name="sidetone-write",
                daemon=True,
            )
            self._sidetone_thread.start()
        return True

    def write_stream(self, data: bytes) -> None:
        """写入流数据：返听先提交到后台线程 → 主设备同步写入。两者并行执行。"""
        # 单槽已满说明返听已落后；继续排队只会增加内存和听感延迟。
        if self._sidetone_enabled and not self._sidetone_stream_dead:
            queue = self._sidetone_queue
            if queue is not None:
                try:
                    queue.put_nowait(data)
                except Full:
                    self._sidetone_stream_dead = True
                    logger.warning("返听写入速度不足，本次流内不再继续排队")
        self._main.write_stream(data)

    def _sidetone_write_loop(self) -> None:
        """按顺序消费有界队列；线程为 daemon，异常设备不能阻止进程退出。"""
        queue = self._sidetone_queue
        if queue is None:
            return
        while True:
            item = queue.get()
            try:
                if item is _STREAM_STOP:
                    return
                self._sidetone_write_safe(item)  # type: ignore[arg-type]
            finally:
                queue.task_done()

    def _sidetone_write_safe(self, data: bytes) -> None:
        """在后台线程中执行返听写入，异常时标记流死。"""
        try:
            self._sidetone.write_stream(data)
        except Exception:
            self._sidetone_stream_dead = True
            logger.warning("返听流写入失败，本次流内不再重试", exc_info=True)

    def close_stream(self) -> None:
        """关闭流；慢返听最多等待固定时间，不能阻塞退出。"""
        queue, thread = self._sidetone_queue, self._sidetone_thread
        self._sidetone_queue = None
        self._sidetone_thread = None
        if queue is not None:
            try:
                queue.put(_STREAM_STOP, timeout=_STREAM_CLOSE_TIMEOUT_SECONDS)
            except Full:
                self._sidetone_stream_dead = True
        if thread is not None:
            thread.join(timeout=_STREAM_CLOSE_TIMEOUT_SECONDS)

        self._main.close_stream()
        # 开关可能已关闭，但原先打开的返听流仍须释放。
        if self._sidetone_enabled or thread is not None:
            try:
                self._sidetone.close_stream()
            except Exception:
                logger.warning("返听流关闭失败", exc_info=True)

    def get_stream_config(self) -> dict[str, int | None]:
        """返回主设备当前流的实际格式与采样率。"""
        return self._main.get_stream_config()

    def query_output_device_default_rate(self) -> int | None:
        """查询主设备默认采样率。"""
        return self._main.query_output_device_default_rate()
