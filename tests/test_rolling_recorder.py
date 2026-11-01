"""持续后台录制：环形缓冲、快照格式与失败路径。

真载 pyaudiowpatch 会碰真实声卡，所有用例通过模块级 ``_pyaudio`` 注入假实现。
"""

from __future__ import annotations

import threading
import wave
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock

import numpy as np
import pytest

from wordy.audio import recorder as recorder_module
from wordy.audio.recorder import (
    TARGET_CHANNELS,
    TARGET_SAMPLE_RATE,
    RecordingError,
    RollingAudioRecorder,
)

PA_INT16 = 8


class _FakeStream:
    def __init__(self, buffers: list[bytes] | None, stop_event: threading.Event,
                 channel_count: int) -> None:
        self._buffers = list(buffers) if buffers is not None else None
        self._stop = stop_event
        self._channel_count = channel_count
        self.drained = threading.Event()
        self.stopped = False
        self.closed = False
        self.read_calls: list[int] = []

    def get_read_available(self) -> int:
        if self._buffers is None:
            return 1024
        if self._buffers:
            return len(self._buffers[0]) // (2 * self._channel_count)
        self.drained.set()
        return 0

    def read(self, count: int, exception_on_overflow: bool = True) -> bytes:
        self.read_calls.append(count)
        if self._buffers is None:
            return b"\x00\x00" * count * self._channel_count
        if self._buffers:
            size = count * 2 * self._channel_count
            data, self._buffers[0] = self._buffers[0][:size], self._buffers[0][size:]
            if not self._buffers[0]:
                self._buffers.pop(0)
            return data
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
                 open_error: Exception | None = None) -> None:
        self._stop = stop_event
        self._rate = rate
        self._channels = channels
        self._buffers = buffers
        self._open_error = open_error
        self.stream: _FakeStream | None = None
        self.open_kwargs: dict[str, Any] = {}
        self.terminated = False

    def get_default_wasapi_loopback(self) -> dict[str, Any]:
        return {
            "index": 64,
            "name": "output [Loopback]",
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
    monkeypatch.setattr(
        recorder_module, "_pyaudio", SimpleNamespace(PyAudio=lambda: audio, paInt16=PA_INT16)
    )


def _started(monkeypatch: pytest.MonkeyPatch,
             seconds: float = 30.0,
             **kwargs: Any) -> tuple[RollingAudioRecorder, _FakePyAudio]:
    recorder = RollingAudioRecorder()
    audio = _FakePyAudio(recorder._stop_event, **kwargs)
    _install(monkeypatch, audio)
    recorder.start(seconds=seconds)
    return recorder, audio


def _buffer(frames: int, channels: int, value: int = 0) -> bytes:
    return np.full(frames * channels, value, dtype=np.int16).tobytes()


def _read_wav(path: Path) -> tuple[int, int, int, np.ndarray]:
    with wave.open(str(path), "rb") as audio:
        return (
            audio.getnchannels(),
            audio.getsampwidth(),
            audio.getframerate(),
            np.frombuffer(audio.readframes(audio.getnframes()), dtype=np.int16),
        )


def _push(recorder: RollingAudioRecorder, frames: int, value: int) -> None:
    recorder._push(_buffer(frames, recorder._channels, value))


def test_ring_buffer_keeps_only_the_last_configured_seconds():
    recorder = RollingAudioRecorder()
    recorder._channels, recorder._rate = 2, 48000
    recorder._set_capacity(1.0)

    _push(recorder, 24000, 1)
    _push(recorder, 24000, 2)
    _push(recorder, 24000, 3)

    samples = recorder._snapshot()
    # 最近 1 秒 = 两段 0.5 秒；最早的一段被挤掉。
    assert samples.shape == (48000, TARGET_CHANNELS)
    assert set(np.unique(samples)) == {2, 3}


def test_ring_buffer_trims_partially_consumed_chunk():
    recorder = RollingAudioRecorder()
    recorder._channels, recorder._rate = 2, 48000
    recorder._set_capacity(1.0)

    _push(recorder, 24000, 7)
    # 超过容量半个块：只保留尾部 24000 帧，不能把 L/R 拆错位。
    _push(recorder, 36000, 8)

    samples = recorder._snapshot()
    assert samples.shape == (48000, TARGET_CHANNELS)
    assert recorder._buffered_frames == 48000


def test_stop_writes_48k_stereo_snapshot(monkeypatch):
    recorder, audio = _started(monkeypatch, seconds=30, buffers=[_buffer(48000, 2)])
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)

    path = recorder.stop()

    channels, width, rate, samples = _read_wav(path)
    assert (channels, width, rate) == (TARGET_CHANNELS, 2, TARGET_SAMPLE_RATE)
    assert samples.size == 48000 * TARGET_CHANNELS
    assert audio.stream.stopped and audio.stream.closed and audio.terminated
    path.unlink()


def test_snapshot_is_resampled_from_endpoint_rate(monkeypatch):
    recorder, audio = _started(monkeypatch, seconds=30, rate=44100.0, channels=2,
                               buffers=[_buffer(44100, 2)])
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)

    path = recorder.stop()

    channels, width, rate, samples = _read_wav(path)
    assert (channels, width, rate) == (TARGET_CHANNELS, 2, TARGET_SAMPLE_RATE)
    assert samples.size == TARGET_SAMPLE_RATE * TARGET_CHANNELS
    path.unlink()


def test_stop_without_audio_reports_missing_system_sound(monkeypatch):
    recorder, audio = _started(monkeypatch, buffers=[])
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)
    recorder._started_at -= 1.0

    with pytest.raises(RecordingError, match="未录到系统声音"):
        recorder.stop()
    assert audio.terminated is True


def test_cancel_stops_device_without_leaving_temp_file(monkeypatch):
    recorder, audio = _started(monkeypatch, buffers=[])
    assert audio.stream is not None
    assert audio.stream.drained.wait(timeout=2)

    recorder.cancel()

    assert recorder.is_running is False
    assert audio.stream.closed and audio.terminated
    with pytest.raises(RecordingError):
        recorder.stop()


def test_stream_open_failure_releases_pyaudio(monkeypatch):
    recorder = RollingAudioRecorder()
    audio = _FakePyAudio(recorder._stop_event, open_error=OSError("设备被独占"))
    _install(monkeypatch, audio)
    with pytest.raises(RecordingError, match="设备被独占"):
        recorder.start(seconds=30)
    assert audio.terminated is True


def test_stuck_read_keeps_stream_alive_until_reader_exits(monkeypatch):
    recorder = RollingAudioRecorder()
    audio = _FakePyAudio(recorder._stop_event)
    entered, release = threading.Event(), threading.Event()

    class BlockedStream(_FakeStream):
        def get_read_available(self) -> int:
            return 1024

        def read(self, count: int, exception_on_overflow: bool = True) -> bytes:
            entered.set()
            release.wait(timeout=5)
            return b""

    stream = BlockedStream([], recorder._stop_event, 2)
    monkeypatch.setattr(audio, "open", Mock(return_value=stream))
    monkeypatch.setattr(recorder_module, "_JOIN_TIMEOUT_SECONDS", .01)
    _install(monkeypatch, audio)
    recorder.start(seconds=30)
    thread = recorder._thread
    assert thread is not None
    try:
        assert entered.wait(timeout=2)
        with pytest.raises(RecordingError, match="未停止"):
            recorder.stop()
        assert recorder._thread is thread
        assert not stream.closed and not audio.terminated
    finally:
        release.set()
        thread.join(timeout=2)
        recorder.cancel()
    assert stream.stopped and stream.closed and audio.terminated
