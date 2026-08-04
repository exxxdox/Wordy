#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""返听（Sidetone）包装器：将 TTS 音频同时输出到主设备和系统默认设备。

返听流写入通过单线程 executor 异步执行，避免阻塞主链路 chunk 写入。
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wordy.audio.player import AudioPlayer, OutputDeviceSelection

logger = logging.getLogger(__name__)


class SidetoneAudioPlayer:
    """TTSAudioPlayer 协议包装器，同时输出到主设备和系统默认返听设备。

    通过 ``set_sidetone_enabled()`` 控制返听开关。
    开关关闭时所有方法近乎零开销直通主播放器（仅一个 bool 判断）。
    返听失败不影响主链路：stream 打开失败、写入异常均记录 warning 并继续。

    返听 write_stream 通过 ``ThreadPoolExecutor(max_workers=1)`` 异步执行，
    主链路写入立即返回，不等待返听设备。chunk 写入顺序由单 worker 保证。
    """

    def __init__(self, main_player: AudioPlayer, sidetone_player: AudioPlayer) -> None:
        self._main = main_player
        self._sidetone = sidetone_player
        self._sidetone_enabled = False
        # 返听流是否已确认不可用（打开失败或写入异常后置位，避免重复报错）
        self._sidetone_stream_dead = False
        # 返听异步写入 executor，close_stream 时 shutdown
        self._sidetone_executor: ThreadPoolExecutor | None = None

    # ── 开关 ──────────────────────────────────────────────────────────

    def set_sidetone_enabled(self, enabled: bool) -> None:
        """启用或停用返听。停用后需调用方自行 reset_audio_output 重建流。"""
        self._sidetone_enabled = enabled

    @property
    def sidetone_enabled(self) -> bool:
        return self._sidetone_enabled

    # ── 属性代理（主播放器） ──────────────────────────────────────────

    @property
    def output_device_name(self) -> str | None:
        return self._main.output_device_name

    @output_device_name.setter
    def output_device_name(self, value: str | None) -> None:
        self._main.output_device_name = value

    @property
    def output_device(self) -> OutputDeviceSelection | None:
        return self._main.output_device

    @output_device.setter
    def output_device(self, value: OutputDeviceSelection | None) -> None:
        self._main.output_device = value

    # ── TTSAudioPlayer 协议方法 ──────────────────────────────────────

    def play_wav(
        self,
        wav_path: str | BytesIO,
        device_index: int | None = None,
    ) -> bool:
        """播放 WAV：主设备 → 返听设备（如启用）。

        BytesIO 在主播放后被消费，seek(0) 重置后传给返听播放器。
        """
        result = self._main.play_wav(wav_path, device_index=device_index)
        if not result or not self._sidetone_enabled:
            return result
        try:
            if isinstance(wav_path, BytesIO):
                wav_path.seek(0)
            self._sidetone.play_wav(wav_path, device_index=None)
        except Exception:
            logger.warning("返听 WAV 播放失败", exc_info=True)
        return result

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
        if not self._sidetone_enabled:
            return True

        try:
            sidetone_ok = self._sidetone.open_stream(
                audio_format, channels, rate,
                device_index=None,
                frames_per_buffer=frames_per_buffer,
            )
            if not sidetone_ok:
                self._sidetone_stream_dead = True
                logger.warning("返听设备流打开失败，仅主链路工作")
        except Exception:
            self._sidetone_stream_dead = True
            logger.warning("返听设备流打开失败", exc_info=True)

        # 创建新的 executor 用于本次流期间的异步返听写入
        self._sidetone_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="sidetone-write")
        return True

    def write_stream(self, data: bytes) -> None:
        """写入流数据：返听先提交到后台线程 → 主设备同步写入。两者并行执行。"""
        # 先提交返听（后台线程立即开始 write），主链路 write 同时进行
        if self._sidetone_enabled and not self._sidetone_stream_dead:
            executor = self._sidetone_executor
            if executor is not None:
                try:
                    executor.submit(self._sidetone_write_safe, data)
                except Exception:
                    self._sidetone_stream_dead = True
                    logger.warning("返听异步写入提交失败，本次流内不再重试", exc_info=True)
        self._main.write_stream(data)

    def _sidetone_write_safe(self, data: bytes) -> None:
        """在 executor 线程中执行返听写入，异常时标记流死。"""
        try:
            self._sidetone.write_stream(data)
        except Exception:
            self._sidetone_stream_dead = True
            logger.warning("返听流写入失败，本次流内不再重试", exc_info=True)

    def close_stream(self) -> None:
        """关闭流：等待返听异步写入完成 → 关主设备 → 关返听设备。"""
        executor = self._sidetone_executor
        self._sidetone_executor = None
        if executor is not None:
            executor.shutdown(wait=True)

        self._main.close_stream()
        if self._sidetone_enabled:
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

    def set_output_device(self, output_device: OutputDeviceSelection | None) -> None:
        """设置主设备输出目标（不影响返听设备）。"""
        self._main.set_output_device(output_device)

    def set_output_device_name(self, name: str | None) -> None:
        """设置主设备输出名称（不影响返听设备）。"""
        self._main.set_output_device_name(name)
