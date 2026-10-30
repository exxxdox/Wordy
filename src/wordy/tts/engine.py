#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS 后端引擎抽象。"""


from collections.abc import Callable
import logging
from abc import ABC, abstractmethod
from io import BytesIO
from threading import Event
from typing import Protocol

from .constants import SUPPORTED_SAMPLE_RATES

logger = logging.getLogger(__name__)

VoiceInfo = dict[str, object]


def resolve_stream_rate(requested: int, device_rate: object) -> int:
    """把请求采样率对齐到设备原生值，避免 Windows Audio Engine 的 SRC 失真。

    只在引擎确实支持的采样率里采纳设备值——设备默认 96k/192k 时保持请求值，
    交给 Windows 做 SRC，而不是拿引擎未必支持的采样率去开流。
    """
    if (
        isinstance(device_rate, int)
        and device_rate != requested
        and device_rate in SUPPORTED_SAMPLE_RATES
    ):
        logger.warning(
            "设备默认采样率为 %s Hz，请求的 %s Hz 已对齐为设备原生采样率以避免 SRC 失真",
            device_rate,
            requested,
        )
        return device_rate
    return requested


class TTSAudioPlayer(Protocol):
    """TTS 后端所需的音频播放能力。"""

    output_device_name: str | None

    def play_wav(self, wav_path: str | BytesIO, device_index: int | None = None,
                 cancel_event: Event | None = None, *, volume: float = 1.0, on_progress: Callable[[float], None] | None = None) -> bool:
        """播放 WAV 音频。"""
        ...

    def open_stream(
        self,
        audio_format: object,
        channels: int,
        rate: int,
        device_index: int | None = None,
        frames_per_buffer: int = 1024,
    ) -> bool:
        """打开流式音频输出。"""
        ...

    def begin_playback(self, cancel_event: Event) -> None:
        """登记本次播放的取消句柄；播放层据此在写入前拦截已取消的音频。"""
        ...

    def end_playback(self, cancel_event: Event) -> None:
        """注销本次播放的取消句柄。"""
        ...

    def write_stream(self, data: bytes) -> None:
        """写入流式音频数据；播放已取消时抛 InterruptedError。"""
        ...

    def close_stream(self) -> None:
        """关闭流式音频输出。"""
        ...

    def get_stream_config(self) -> dict[str, int | None]:
        """返回当前已打开流的实际格式与采样率。"""
        ...

    def query_output_device_default_rate(self) -> int | None:
        """查询当前选定输出设备的默认采样率。"""
        ...


class BackendTTSEngine(ABC):
    """TTS 后端引擎基类。"""

    def __init__(self, audio_player: TTSAudioPlayer, voice_id: str | None = None, volume: float = 1.0):
        self.audio_player: TTSAudioPlayer = audio_player
        self.voice_id: str | None = voice_id
        self.volume: float = volume

    def connect(self) -> None:
        """建立后端连接；不需要连接的后端保持 no-op。"""

    def close(self) -> None:
        """释放后端资源；无持久资源的后端保持 no-op。"""

    def set_voice(self, voice_id: str) -> None:
        """更新后端使用的音色。"""
        self.voice_id = voice_id

    def set_volume(self, volume: float) -> None:
        """更新后端使用的音量。"""
        self.volume = volume

    def reset_audio_output(self) -> None:
        """音频输出设备变更后重建底层流；默认 no-op，由实时引擎按需覆写。"""

    def release_audio_output(self) -> None:
        """释放音频设备但保留后端连接（本地片段播放前调用）。

        ``close()`` 会连 websocket 一起拆掉：关闭握手最长可等 10 秒，片段起播
        被拖慢，下次朗读还要重新建连。这里只让出设备，连接留给实时引擎复用。
        """
        self.audio_player.close_stream()

    @abstractmethod
    def fetch_voices(self) -> list[VoiceInfo]:
        """获取当前后端可用的音色列表。"""
        ...

    @abstractmethod
    def speak(self, text: str, cancel_event: Event | None = None) -> bool:
        """生成并播放输入文本；``cancel_event`` 置位时尽快停止。

        引擎只能协作式取消：由播放线程在写入循环里自检，不做跨线程关闭音频流
        （那会和 PortAudio 生命周期竞争，直接原生访问违例）。
        """
        ...
