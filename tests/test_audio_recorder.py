"""系统声音录制的采集循环、格式归一化与失败路径。

真正加载 pyaudiowpatch 会碰真实声卡，所有用例都通过模块级 ``_pyaudio`` 注入假实现。
"""

from __future__ import annotations

import threading
import time
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import pytest

from wordy.audio import recorder as recorder_module
from wordy.audio.recorder import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    RecordingError,
    SystemAudioRecorder,
)

PA_INT16 = 8


class _FakeStream:
    """按脚本吐缓冲；吐完后挂起到录音停止，避免测试与采集线程竞速。"""

    def __init__(self, buffers: list[bytes] | None, stop_event: threading.Event,
                 channel_count: int) -> None:
        self._buffers = list(buffers) if buffers is not None else None
        self._stop = stop_event
        self._channel_count = channel_count
        self.drained = threading.Event()
        self.stopped = False
        self.closed = False

    def read(self, count: int, exception_on_overflow: bool = True) -> bytes:
        self.last_exception_on_overflow = bool(exception_on_overflow)
        if self._buffers is None:
            return b"\x00\x00" * count * self._channel_count
        if self._buffers:
            return self._buffers.pop(0)
        self.drained.set()
        self._stop.wait(timeout=5)
        return b""

    def stop_stream(self) -> None:
        self.stopped = True

    def close(self) -> None:
        self.closed = True


class _FakePyAudio:
    def __init__(self, stop_event: threading.Event, *, rate: float = 48000.0,
                 channels: int = 2, buffers: list[bytes] | None = None,
                 loopback_error: Exception | None = None,
                 open_error: Exception | None = None) -> None:
        self._stop = stop_event
        self._rate = rate
        self._channels = channels
        self._buffers = buffers
        self._loopback_error = loopback_error
        self._open_error = open_error
        self.stream: _FakeStream | None = None
        self.open_kwargs: dict[str, Any] = {}
        self.terminated = False

    def get_default_wasapi_loopback(self) -> dict[str, Any]:
        if self._loopback_error is not None:
            raise self._loopback_error
        return {
            "index": 64,
            "name": "output [Loopback]",
            "isLoopbackDevice": True,
            "defaultSampleRate": self._rate,
            "maxInputChannels": self._channels,
        }

    def open(self, **kwargs: Any) -> _FakeStream:
        if self._open_error is not None:
            raise self._open_error
        self.open_kwargs = kwargs
        self.stream = _FakeStream(self._buffers, self._stop, int(kwargs["channels"]))
        return self.stream

    def terminate(self) -> None:
        self.terminated = True


def _install(monkeypatch: pytest.MonkeyPatch, audio: _FakePyAudio) -> None:
    """把假 PyAudio 装进模块全局；recorder 在调用时才读这个全局。"""
    monkeypatch.setattr(
        recorder_module, "_pyaudio", SimpleNamespace(PyAudio=lambda: audio, paInt16=PA_INT16)
    )


def _started(monkeypatch: pytest.MonkeyPatch,
             **kwargs: Any) -> tuple[SystemAudioRecorder, _FakePyAudio]:
    recorder = SystemAudioRecorder()
    audio = _FakePyAudio(recorder._stop_event, **kwargs)
    _install(monkeypatch, audio)
    recorder.start()
    return recorder, audio


def _buffer(frames: int, channels: int) -> bytes:
    """按帧数生成静音 PCM；时长要过 MIN_RECORD_SECONDS，所以按真实量级给。"""
    return b"\x00\x00" * frames * channels


def _read_wav(path: Path) -> tuple[int, int, int, int]:
    with wave.open(str(path), "rb") as audio:
        return (
            audio.getnchannels(),
            audio.getsampwidth(),
            audio.getframerate(),
            audio.getnframes(),
        )


def test_start_without_dependency_reports_reason(monkeypatch):
    monkeypatch.setattr(recorder_module, "_pyaudio", None)
    recorder = SystemAudioRecorder()
    with pytest.raises(RecordingError, match="PyAudioWPatch"):
        recorder.start()


def test_missing_loopback_endpoint_reports_reason(monkeypatch):
    """没有 WASAPI 渲染端点时 get_default_wasapi_loopback 抛 LookupError，不是打开失败。"""
    recorder = SystemAudioRecorder()
    audio = _FakePyAudio(recorder._stop_event, loopback_error=LookupError("no loopback"))
    _install(monkeypatch, audio)
    with pytest.raises(RecordingError, match="系统声音"):
        recorder.start()
    assert audio.terminated is True


def test_stream_open_failure_releases_pyaudio(monkeypatch):
    recorder = SystemAudioRecorder()
    audio = _FakePyAudio(recorder._stop_event, open_error=OSError("设备被独占"))
    _install(monkeypatch, audio)
    with pytest.raises(RecordingError, match="设备被独占"):
        recorder.start()
    assert audio.terminated is True


def test_stop_writes_48k_stereo_pcm_and_releases_device(monkeypatch):
    buffers = [_buffer(24000, 2), _buffer(12000, 2)]
    recorder, audio = _started(monkeypatch, rate=48000.0, channels=2, buffers=buffers)
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)

    path = recorder.stop()

    assert _read_wav(path) == (TARGET_CHANNELS, 2, TARGET_SAMPLE_RATE, 36000)
    assert audio.open_kwargs["input"] is True
    assert audio.open_kwargs["input_device_index"] == 64
    assert audio.open_kwargs["format"] == PA_INT16
    assert audio.stream.stopped is True
    assert audio.stream.closed is True
    assert audio.terminated is True
    path.unlink()


def test_recorded_clip_is_resampled_to_target_rate(monkeypatch):
    """44100 端点产物必须归一化：否则播放回退链只换采样率不换数据，静默变调。"""
    buffers = [_buffer(44100, 2)]
    recorder, audio = _started(monkeypatch, rate=44100.0, channels=2, buffers=buffers)
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)

    path = recorder.stop()

    channels, width, rate, frames = _read_wav(path)
    assert (channels, width, rate) == (TARGET_CHANNELS, 2, TARGET_SAMPLE_RATE)
    assert frames == round(44100 * TARGET_SAMPLE_RATE / 44100)
    path.unlink()


def test_mono_endpoint_is_expanded_to_stereo(monkeypatch):
    buffers = [_buffer(48000, 1)]
    recorder, audio = _started(monkeypatch, channels=1, buffers=buffers)
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)

    path = recorder.stop()

    assert _read_wav(path) == (TARGET_CHANNELS, 2, TARGET_SAMPLE_RATE, 48000)
    path.unlink()


def test_multichannel_endpoint_is_truncated_to_stereo(monkeypatch):
    """多声道端点按 mix format 原样打开，多出的声道在下游截掉。"""
    buffers = [_buffer(48000, 6)]
    recorder, audio = _started(monkeypatch, channels=6, buffers=buffers)
    assert audio.stream is not None
    assert audio.open_kwargs["channels"] == 6
    assert audio.stream.drained.wait(timeout=2)

    path = recorder.stop()

    assert _read_wav(path) == (TARGET_CHANNELS, 2, TARGET_SAMPLE_RATE, 48000)
    path.unlink()


def test_write_failure_discards_temp_and_raises(monkeypatch):
    """写盘失败不能留下永远没人认领的临时文件，也不能让异常穿给调用方。"""
    buffers = [_buffer(48000, 2)]
    recorder, audio = _started(monkeypatch, rate=48000.0, channels=2, buffers=buffers)
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)
    temp = recorder._path
    assert temp is not None
    monkeypatch.setattr(recorder_module.wave, "open", Mock(side_effect=OSError("磁盘已满")))

    with pytest.raises(RecordingError, match="写入失败"):
        recorder.stop()

    assert not temp.exists()


def test_capture_stops_at_max_seconds(monkeypatch):
    monkeypatch.setattr(recorder_module, "MAX_RECORD_SECONDS", 0.2)
    recorder, audio = _started(monkeypatch, channels=2)
    assert audio.stream is not None

    deadline = time.monotonic() + 5
    thread = recorder._thread
    while thread is not None and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert thread is not None and not thread.is_alive(), "采集线程未在时长上限处自行停止"

    assert recorder.elapsed_seconds >= 0.2
    path = recorder.stop()
    assert _read_wav(path)[3] > 0
    path.unlink()


def test_stop_hands_temp_file_over_to_caller(monkeypatch):
    """录好的文件归调用方所有：recorder 不能在被回收时把它删掉。"""
    buffers = [_buffer(48000, 2)]
    recorder, audio = _started(monkeypatch, rate=48000.0, channels=2, buffers=buffers)
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)

    path = recorder.stop()

    assert recorder._path is None
    recorder.cancel()
    assert path.is_file()
    path.unlink()


def test_unexpected_failure_still_discards_temp(monkeypatch):
    """未预期的异常也不能把临时文件留在 %TEMP% 里没人认领。"""
    buffers = [_buffer(48000, 2)]
    recorder, audio = _started(monkeypatch, rate=48000.0, channels=2, buffers=buffers)
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)
    temp = recorder._path
    assert temp is not None
    monkeypatch.setattr(
        recorder, "_collect_frames", Mock(side_effect=MemoryError("out of memory"))
    )

    with pytest.raises(MemoryError):
        recorder.stop()

    assert not temp.exists()


def test_too_short_recording_is_rejected(monkeypatch):
    recorder, audio = _started(monkeypatch, channels=2, buffers=[])
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)
    with pytest.raises(RecordingError, match="太短"):
        recorder.stop()
    assert audio.terminated is True


def test_cancel_discards_temp_file(monkeypatch):
    recorder, audio = _started(monkeypatch, channels=2, buffers=[])
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)
    temp = recorder._path
    assert temp is not None and temp.is_file()

    recorder.cancel()

    assert not temp.exists()
    assert audio.stream.closed is True
    assert audio.terminated is True
    with pytest.raises(RecordingError):
        recorder.stop()


def test_cancel_active_recording_ends_current_session(monkeypatch):
    recorder, audio = _started(monkeypatch, channels=2, buffers=[])
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)
    temp = recorder._path
    assert temp is not None

    recorder_module.cancel_active_recording()

    assert not temp.exists()
    assert audio.terminated is True


def test_cancel_active_recording_without_session_is_noop():
    recorder_module.cancel_active_recording()
