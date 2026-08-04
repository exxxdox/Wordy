#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS 后端引擎抽象。"""

from abc import ABC, abstractmethod
from io import BytesIO
from typing import Protocol

VoiceInfo = dict[str, object]


class TTSAudioPlayer(Protocol):
    """TTS 后端所需的音频播放能力。"""

    output_device_name: str | None

    def play_wav(self, wav_path: str | BytesIO, device_index: int | None = None) -> bool:
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

    def write_stream(self, data: bytes) -> None:
        """写入流式音频数据。"""
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

    @abstractmethod
    def fetch_voices(self) -> list[VoiceInfo]:
        """获取当前后端可用的音色列表。"""
        ...

    @abstractmethod
    def speak(self, text: str) -> bool:
        """生成并播放输入文本。"""
        ...
