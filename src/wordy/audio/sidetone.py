#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""返听（Sidetone）包装器：将 TTS 音频同时输出到主设备和系统默认设备。

返听流写入通过小容量队列异步执行；队列吸收设备与线程调度抖动，
生产侧永不阻塞主链路，慢设备持续落后时才停用本次返听。
"""

from __future__ import annotations

import logging
from queue import Full, Queue
from threading import Event, Thread
from io import BytesIO
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from wordy.audio.player import AudioPlayer

logger = logging.getLogger(__name__)

# 返听队列缓冲的音频块数：吸收 worker 启动延迟与设备写入抖动，避免一次抖动就整条静音。
_STREAM_QUEUE_BLOCKS = 8
_STREAM_CLOSE_TIMEOUT_SECONDS = 0.1
_STREAM_STOP = object()

# 文件播放时等待返听收尾的上限。主输出已经同步播完，正常情况只剩设备缓冲的
# 一点尾巴；卡死的设备必须在这里放弃等待——调用方是串行 TTS worker，等下去
# 会把后续所有朗读一起拖住。
_SIDETONE_TAIL_TIMEOUT_SECONDS = 2.0


class SidetoneAudioPlayer:
    """TTSAudioPlayer 协议包装器，同时输出到主设备和系统默认返听设备。

    通过 ``set_sidetone_enabled()`` 控制返听开关。
    开关关闭时所有方法近乎零开销直通主播放器（仅一个 bool 判断）。
    返听失败不影响主链路：stream 打开失败、写入异常均记录 warning 并继续。

    返听 write_stream 通过单个后台线程异步执行，提交永不阻塞主链路；
    队列缓存若干块以吸收设备抖动，持续落后时才停用本次返听。
    """

    def __init__(self, main_player: AudioPlayer, sidetone_player: AudioPlayer) -> None:
        self._main = main_player
        self._sidetone = sidetone_player
        self._sidetone_enabled = False
        # 返听流是否已确认不可用（打开失败或写入异常后置位，避免重复报错）
        self._sidetone_stream_dead = False
        self._sidetone_queue: Queue[bytes | object] | None = None
        self._sidetone_thread: Thread | None = None
        # 文件播放的返听尾巴线程；仍在跑时说明上一段返听还没收尾。
        self._sidetone_tail: Thread | None = None

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
        *, volume: float = 1.0,
    ) -> bool:
        """同时播放主输出和返听；主输出结果决定调用是否成功。

        主输出在调用线程里同步播完（异常照旧向上传播），返听另起守护线程，
        只在返回前有界地等它收尾。
        """
        # 两路绑定同一次播放的增益，不能读取会被后续槽位覆盖的共享音量。
        if not self._needs_sidetone(device_index):
            if cancel_event is None:
                return self._main.play_wav(wav_path, device_index=device_index, volume=volume)
            return self._main.play_wav(
                wav_path, device_index=device_index, cancel_event=cancel_event, volume=volume
            )

        if self._sidetone_tail is not None and self._sidetone_tail.is_alive():
            # 上一段返听仍在跑（设备落后或卡死）：本次只播主输出，不排队叠加。
            logger.warning("上一段返听尚未收尾，本次跳过返听")
        else:
            # 两个播放器不能并发消费同一个 BytesIO 游标。
            sidetone_path = (
                BytesIO(wav_path.getvalue()) if isinstance(wav_path, BytesIO) else wav_path
            )
            self._sidetone_tail = Thread(
                target=self._play_sidetone_safe,
                args=(sidetone_path, cancel_event, volume),
                name="sidetone-wav",
                daemon=True,
            )
            self._sidetone_tail.start()

        try:
            if cancel_event is None:
                result = self._main.play_wav(wav_path, device_index=device_index, volume=volume)
            else:
                result = self._main.play_wav(
                    wav_path, device_index=device_index, cancel_event=cancel_event, volume=volume
                )
        finally:
            # 主输出抛异常时也要等返听：调用方（decoded_audio_clip）随后就会删掉
            # 音源临时文件，返听线程再握着它就是卸载失败加一段仍在响的残音。
            self._wait_sidetone_tail()
        return result and (cancel_event is None or not cancel_event.is_set())

    def _play_sidetone_safe(self, wav_path: str | BytesIO, cancel_event: Event | None, volume: float) -> None:
        """在返听线程中播放；失败只记 warning，不影响主链路。"""
        try:
            if cancel_event is None:
                self._sidetone.play_wav(wav_path, device_index=None, volume=volume)
            else:
                self._sidetone.play_wav(wav_path, device_index=None, cancel_event=cancel_event, volume=volume)
        except Exception:
            logger.warning("返听 WAV 播放失败", exc_info=True)

    def _wait_sidetone_tail(self) -> None:
        """只等返听收尾，不等整段；超时即放弃，线程是守护线程自会随进程退出。"""
        thread = self._sidetone_tail
        if thread is None:
            return
        thread.join(timeout=_SIDETONE_TAIL_TIMEOUT_SECONDS)
        if thread.is_alive():
            logger.warning(
                "返听未在 %s 秒内收尾，本次不再等待（下一段返听会等它结束后再恢复）",
                _SIDETONE_TAIL_TIMEOUT_SECONDS,
            )

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
            queue: Queue[bytes | object] = Queue(maxsize=_STREAM_QUEUE_BLOCKS)
            thread = Thread(
                target=self._sidetone_write_loop,
                args=(queue,),
                name="sidetone-write",
                daemon=True,
            )
            self._sidetone_queue = queue
            self._sidetone_thread = thread
            thread.start()
        return True

    def write_stream(self, data: bytes) -> None:
        """写入流数据：返听先提交到后台线程 → 主设备同步写入。两者并行执行。"""
        # 提交永不等待：返听拖慢主输出比返听漏一段更糟。队列满说明返听已持续落后。
        if self._sidetone_enabled and not self._sidetone_stream_dead:
            queue = self._sidetone_queue
            if queue is not None:
                try:
                    queue.put_nowait(data)
                except Full:
                    self._sidetone_stream_dead = True
                    logger.warning("返听写入持续落后，本次流内不再继续排队")
        self._main.write_stream(data)

    def _sidetone_write_loop(self, queue: Queue[bytes | object]) -> None:
        """按顺序消费本次流的队列；关闭时清空实例引用不会丢失待写块。"""
        while True:
            item = queue.get()
            try:
                if item is _STREAM_STOP:
                    return
                self._sidetone_write_safe(cast(bytes, item))
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
