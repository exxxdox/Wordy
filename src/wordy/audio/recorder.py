#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""系统声音（WASAPI loopback）录制。

只负责把默认输出端点正在播放的声音采集成标准 PCM WAV；托管、命名和播放
交给现有链路（``wordy.audio.clips.import_audio_clip``），本模块不碰托管目录。
"""

from __future__ import annotations

import logging
import os
import threading
import time
import wave
from collections import deque
from pathlib import Path
from tempfile import mkstemp
from typing import Any

import numpy as np

logger = logging.getLogger(__name__)

MAX_RECORD_SECONDS = 60.0
MIN_RECORD_SECONDS = 0.5
TARGET_SAMPLE_RATE = 48000
TARGET_CHANNELS = 2
FRAMES_PER_BUFFER = 1024
_JOIN_TIMEOUT_SECONDS = 2.0
_CAPTURE_POLL_SECONDS = 0.01

# pyaudiowpatch 的 PortAudio 初始化计数是进程级非原子量：滚动录制与设置页录音
# 可能同时新建/释放实例，必须像 AudioPlayer 一样只串行化生命周期调用。
_PYAUDIO_LIFECYCLE_LOCK = threading.Lock()

# 无类型桩的二进制模块，声明成 Any 才能同时容纳“缺依赖”的 None 分支。
_pyaudio: Any = None
try:
    import pyaudiowpatch as _pyaudio
except Exception:  # noqa: BLE001 - 缺依赖时只让录音不可用，不影响设置页其他功能
    logger.debug("未安装 pyaudiowpatch，系统声音录制不可用", exc_info=True)


class RecordingError(RuntimeError):
    """录音无法开始、被中止或没有可用内容。"""


# Qt 退出不一定给顶层窗口派发 Close，退出流程需要一个进程级兜底句柄。
_ACTIVE: SystemAudioRecorder | None = None
_ACTIVE_ROLLING: RollingAudioRecorder | None = None


def cancel_active_recording() -> None:
    """退出时丢弃仍在进行的录音；没有进行中的录音时什么也不做。"""
    active = _ACTIVE
    if active is not None:
        active.cancel()


def cancel_active_rolling_recording() -> None:
    """退出时丢弃后台滚动录制；没有进行中的会话时什么也不做。"""
    active = _ACTIVE_ROLLING
    if active is not None:
        active.cancel()


class SystemAudioRecorder:
    """在后台线程里采集 WASAPI loopback，停止后给出临时 WAV 路径。

    采集线程只接触 numpy 和音频流，不接触任何 Qt 对象——设置窗口可能在录制
    过程中被销毁。
    """

    def __init__(self) -> None:
        self._api: Any = None
        self._stream: Any = None
        self._path: Path | None = None
        self._chunks: list[np.ndarray] = []
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._channels = TARGET_CHANNELS
        self._rate = TARGET_SAMPLE_RATE
        self._started_at = 0.0
        self._final_elapsed = 0.0

    @property
    def elapsed_seconds(self) -> float:
        """已录时长；采集结束后不再增长，供 UI 与时长上限判断使用。"""
        thread = self._thread
        if thread is not None and thread.is_alive():
            return time.monotonic() - self._started_at
        return self._final_elapsed

    def start(self) -> None:
        global _ACTIVE

        if _ACTIVE is not None:
            # 超时的原生读取可能仍持有设备，不能再开第二个实例或回收它的流。
            if _ACTIVE._thread is not None and _ACTIVE._thread.is_alive():
                raise RecordingError("上一次录音设备尚未停止，请稍后重试")
            _ACTIVE.cancel()

        # 打开逻辑与后台滚动录制共用，错误文案和生命周期锁只有一份。
        instance, stream, channels, rate = _open_loopback_stream()

        self._api = instance
        self._stream = stream
        try:
            self._channels = channels
            self._rate = rate
            self._chunks = []
            self._stop_event.clear()
            self._started_at = time.monotonic()
            self._final_elapsed = 0.0
            self._path = _create_temp_path()
            self._thread = threading.Thread(
                target=self._capture, name="ChatWheelRecord", daemon=True,
            )
            self._thread.start()
            _ACTIVE = self
        except Exception as error:
            # 流已打开后的任何初始化失败都必须释放设备，否则只能靠重启恢复录音。
            self._thread = None
            self._release_device()
            self._discard_path()
            raise RecordingError(f"无法启动系统声音录制：{error}") from error

    def stop(self) -> Path:
        """结束录制并返回临时 WAV；内容不足时抛 ``RecordingError`` 并丢弃临时文件。"""
        global _ACTIVE

        thread = self._thread
        if thread is None:
            raise RecordingError("当前没有正在进行的录音")

        self._stop_event.set()
        thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
        # close/terminate 与原生 read 并发会直接访问违例，超时也必须保留活对象。
        if thread.is_alive():
            raise RecordingError("录音设备尚未停止，请稍后重试")
        self._release_device()
        self._thread = None
        if _ACTIVE is self:
            _ACTIVE = None
        try:
            path = self._write_wav(self._collect_frames())
        except Exception:
            # 兜底：任何未预期的失败（如拼接缓冲时的 MemoryError）都不能把临时
            # 文件留在 %TEMP% 里再没人认领；_discard_path 可重复调用。
            self._discard_path()
            raise
        # 所有权移交给调用方：导入成功后由调用方删除，失败留在调用方手里重试。
        self._path = None
        return path

    def cancel(self) -> None:
        """中止录制并删除临时文件；没有进行中的录音时也不报错。"""
        global _ACTIVE

        thread = self._thread
        if thread is not None:
            self._stop_event.set()
            thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
            if thread.is_alive():
                # _ACTIVE 保留所有权，待读取退出后重试取消或下次 start 时再回收。
                logger.warning("录音线程仍在读取，保留采集流以避免并发关闭")
                return
            self._thread = None
        self._release_device()
        self._chunks = []
        self._discard_path()
        if _ACTIVE is self:
            _ACTIVE = None

    def _capture(self) -> None:
        # 采集上限在采集线程里也判一次：UI 计时器可能因为页签切换而停摆。
        deadline = self._started_at + MAX_RECORD_SECONDS
        stream = self._stream
        while not self._stop_event.is_set() and time.monotonic() < deadline:
            try:
                # WASAPI 静音时可能不产生数据包，固定帧数的阻塞 read 会永远等不到停止信号。
                available = stream.get_read_available()
                if available <= 0:
                    self._stop_event.wait(_CAPTURE_POLL_SECONDS)
                    continue
                data = stream.read(min(FRAMES_PER_BUFFER, available), exception_on_overflow=False)
            except Exception as error:  # noqa: BLE001 - 设备被拔掉等，按已录内容收尾
                logger.warning("系统声音采集中断: %s", error)
                break
            if data:
                self._chunks.append(np.frombuffer(data, dtype=np.int16))
        self._final_elapsed = time.monotonic() - self._started_at

    def _collect_frames(self) -> np.ndarray:
        chunks, self._chunks = self._chunks, []
        if not chunks:
            self._discard_path()
            if self._final_elapsed >= MIN_RECORD_SECONDS:
                raise RecordingError("未录到系统声音，请播放声音后重试")
            raise RecordingError(f"录音太短，请至少录制 {MIN_RECORD_SECONDS:g} 秒")

        frames = np.concatenate(chunks)
        # 缓冲区边界不保证按帧对齐，尾部不足一帧的样本丢弃。
        usable = len(frames) - len(frames) % self._channels
        frames = frames[:usable].reshape(-1, self._channels)
        if frames.shape[0] < MIN_RECORD_SECONDS * self._rate:
            self._discard_path()
            raise RecordingError(f"录音太短，请至少录制 {MIN_RECORD_SECONDS:g} 秒")
        return frames

    def _write_wav(self, frames: np.ndarray) -> Path:
        path = self._path
        if path is None:
            raise RecordingError("录音文件不可用")
        normalized = _normalize(frames, self._rate)
        try:
            _write_pcm_wav(path, normalized, TARGET_SAMPLE_RATE)
        except (OSError, wave.Error) as error:
            # 写盘失败时清掉半成品，否则临时文件再没人认领，会一直留在 %TEMP%。
            self._discard_path()
            raise RecordingError(f"录音写入失败：{error}") from error
        return path

    def _release_device(self) -> None:
        stream, self._stream = self._stream, None
        api, self._api = self._api, None
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception as error:  # noqa: BLE001
                logger.warning("停止采集流失败: %s", error)
            try:
                stream.close()
            except Exception as error:  # noqa: BLE001
                logger.warning("关闭采集流失败: %s", error)
        if api is not None:
            _terminate_pyaudio(api)

    def _discard_path(self) -> None:
        path, self._path = self._path, None
        if path is None:
            return
        try:
            path.unlink(missing_ok=True)
        except OSError as error:  # 残留临时文件交给系统清理，不升级为用户可见错误
            logger.warning("清理录音临时文件失败: %s", error)


class RollingAudioRecorder:
    """持续采集系统声音，内存里只保留最近 N 秒。

    录制期间不写盘；``stop()`` 才把当前窗口压成临时 WAV 交给截取流程。
    """

    def __init__(self) -> None:
        self._api: Any = None
        self._stream: Any = None
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._chunks: deque[np.ndarray] = deque()
        self._buffered_frames = 0
        self._max_frames = 1
        self._channels = TARGET_CHANNELS
        self._rate = TARGET_SAMPLE_RATE
        self._started_at = 0.0
        self._final_elapsed = 0.0

    @property
    def is_running(self) -> bool:
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def elapsed_seconds(self) -> float:
        if self.is_running:
            return time.monotonic() - self._started_at
        return self._final_elapsed

    def start(self, seconds: float) -> None:
        global _ACTIVE_ROLLING

        if self.is_running:
            raise RecordingError("后台录制已经在进行中")
        # 超时的原生读取可能仍持有设备，不能再开第二个实例或回收它的流。
        active = _ACTIVE_ROLLING
        if active is not None and active is not self:
            if active.is_running:
                raise RecordingError("上一次后台录制尚未停止，请稍后重试")
            active.cancel()

        instance, stream, channels, rate = _open_loopback_stream()
        self._api, self._stream = instance, stream
        try:
            self._channels, self._rate = channels, rate
            self._set_capacity(max(0.1, float(seconds)))
            with self._lock:
                self._chunks.clear()
                self._buffered_frames = 0
            self._stop_event.clear()
            self._started_at = time.monotonic()
            self._final_elapsed = 0.0
            self._thread = threading.Thread(
                target=self._capture, name="RollingRecord", daemon=True,
            )
            self._thread.start()
            _ACTIVE_ROLLING = self
        except Exception as error:
            self._thread = None
            self._release_device()
            raise RecordingError(f"无法启动后台录制：{error}") from error

    def _set_capacity(self, seconds: float) -> None:
        self._max_frames = max(1, int(seconds * self._rate))
        self._trim()

    def _capture(self) -> None:
        # WASAPI 静音时可能不产生数据包，固定帧数的阻塞 read 会永远等不到停止信号。
        stream = self._stream
        while not self._stop_event.is_set():
            try:
                available = stream.get_read_available()
                if available <= 0:
                    self._stop_event.wait(_CAPTURE_POLL_SECONDS)
                    continue
                data = stream.read(min(FRAMES_PER_BUFFER, available), exception_on_overflow=False)
            except Exception as error:  # noqa: BLE001 - 设备被拔掉等，按已缓冲内容收尾
                logger.warning("后台录制采集中断: %s", error)
                break
            if data:
                self._push(data)
        self._final_elapsed = time.monotonic() - self._started_at

    def _push(self, data: bytes) -> None:
        frames = np.frombuffer(data, dtype=np.int16)
        # 块边界不保证按帧对齐；尾部不足一帧的样本丢弃，避免 L/R 错位。
        usable = len(frames) - len(frames) % self._channels
        if usable <= 0:
            return
        block = frames[:usable].reshape(-1, self._channels)
        with self._lock:
            self._chunks.append(block)
            self._buffered_frames += block.shape[0]
        self._trim()

    def _trim(self) -> None:
        with self._lock:
            while self._buffered_frames > self._max_frames and self._chunks:
                excess = self._buffered_frames - self._max_frames
                first = self._chunks[0]
                if first.shape[0] <= excess:
                    self._chunks.popleft()
                    self._buffered_frames -= first.shape[0]
                else:
                    self._chunks[0] = first[excess:]
                    self._buffered_frames -= excess

    def _snapshot(self) -> np.ndarray:
        with self._lock:
            chunks = list(self._chunks)
        if not chunks:
            return np.empty((0, self._channels), dtype=np.int16)
        return np.concatenate(chunks)

    def stop(self) -> Path:
        """结束录制并返回最近窗口的临时 WAV；内容不足时抛 ``RecordingError``。"""
        global _ACTIVE_ROLLING

        thread = self._thread
        if thread is None:
            raise RecordingError("当前没有正在进行的后台录制")
        self._stop_event.set()
        thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
        # close/terminate 与原生 read 并发会直接访问违例，超时也必须保留活对象。
        if thread.is_alive():
            raise RecordingError("录制设备尚未停止，请稍后重试")
        self._release_device()
        self._thread = None
        if _ACTIVE_ROLLING is self:
            _ACTIVE_ROLLING = None

        frames = self._snapshot()
        with self._lock:
            self._chunks.clear()
            self._buffered_frames = 0
        if frames.shape[0] == 0:
            if self._final_elapsed >= MIN_RECORD_SECONDS:
                raise RecordingError("未录到系统声音，请播放声音后重试")
            raise RecordingError(f"录音太短，请至少录制 {MIN_RECORD_SECONDS:g} 秒")
        if frames.shape[0] < MIN_RECORD_SECONDS * self._rate:
            raise RecordingError(f"录音太短，请至少录制 {MIN_RECORD_SECONDS:g} 秒")

        path = _create_temp_path("wordy-rolling-")
        try:
            _write_pcm_wav(path, _normalize(frames, self._rate), TARGET_SAMPLE_RATE)
        except (OSError, wave.Error) as error:
            path.unlink(missing_ok=True)
            raise RecordingError(f"录音写入失败：{error}") from error
        # 所有权移交给调用方：保存成功后由调用方删除，失败留在调用方手里重试。
        return path

    def cancel(self) -> None:
        """中止录制并丢弃缓冲；没有进行中的录制时也不报错。"""
        global _ACTIVE_ROLLING

        thread = self._thread
        if thread is None:
            return
        self._stop_event.set()
        thread.join(timeout=_JOIN_TIMEOUT_SECONDS)
        # 超时与录音器一致：不碰仍在 read() 的流，保留句柄等读取线程退出。
        if thread.is_alive():
            return
        self._release_device()
        self._thread = None
        with self._lock:
            self._chunks.clear()
            self._buffered_frames = 0
        if _ACTIVE_ROLLING is self:
            _ACTIVE_ROLLING = None

    def _release_device(self) -> None:
        stream, self._stream = self._stream, None
        api, self._api = self._api, None
        if stream is not None:
            try:
                stream.stop_stream()
            except Exception as error:  # noqa: BLE001
                logger.warning("停止采集流失败: %s", error)
            try:
                stream.close()
            except Exception as error:  # noqa: BLE001
                logger.warning("关闭采集流失败: %s", error)
        if api is not None:
            _terminate_pyaudio(api)


def _create_temp_path(prefix: str = "wordy-record-") -> Path:
    """一次性临时文件：不用 TemporaryDirectory，避免对象被回收时删掉尚未导入的录音。"""
    handle, name = mkstemp(prefix=prefix, suffix=".wav")
    os.close(handle)
    return Path(name)


def _write_pcm_wav(path: Path, frames: np.ndarray, rate: int) -> None:
    """wave 在 close 时才写 RIFF 长度，必须完整退出 with 再交给导入方。"""
    with wave.open(str(path), "wb") as output:
        output.setnchannels(TARGET_CHANNELS)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(frames.tobytes())


def _open_loopback_stream() -> tuple[Any, Any, int, int]:
    """按端点 mix format 打开默认输出端点的 WASAPI 回环采集流。"""
    api = _pyaudio
    if api is None:
        raise RecordingError("未安装 PyAudioWPatch，无法录制系统声音")
    with _PYAUDIO_LIFECYCLE_LOCK:
        instance = api.PyAudio()
    try:
        device = instance.get_default_wasapi_loopback()
    except Exception as error:  # noqa: BLE001 - 没有 WASAPI 渲染端点时抛 LookupError
        _terminate_pyaudio(instance)
        raise RecordingError("找不到可录制的系统声音输出设备") from error

    # WASAPI 共享模式必须匹配端点 mix format，采样率和声道数都按端点原样请求；
    # 多出来的声道在 _normalize 里截掉，8 声道虚拟声卡也不会打不开。
    channels = max(1, int(device["maxInputChannels"]))
    rate = int(device["defaultSampleRate"])
    try:
        stream = instance.open(
            format=api.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=device["index"],
            frames_per_buffer=FRAMES_PER_BUFFER,
        )
    except Exception as error:  # noqa: BLE001
        _terminate_pyaudio(instance)
        raise RecordingError(f"无法打开系统声音采集流：{error}") from error
    return instance, stream, channels, rate


def _terminate_pyaudio(instance: Any) -> None:
    with _PYAUDIO_LIFECYCLE_LOCK:
        try:
            instance.terminate()
        except Exception as error:  # noqa: BLE001
            logger.warning("释放 PyAudio 失败: %s", error)


def _normalize(frames: np.ndarray, rate: int) -> np.ndarray:
    """统一成 48000 Hz 双声道。

    播放回退链在设备拒绝源采样率时只替换 rate、不重采样数据，直接播出 44100
    内容会静默变调，所以录制端必须对齐托管音频的格式。
    """
    if frames.shape[1] < TARGET_CHANNELS:
        # 单声道端点复制成双声道，避免播放侧把相邻样本错拆成 L/R。
        frames = np.repeat(frames, TARGET_CHANNELS, axis=1)
    elif frames.shape[1] > TARGET_CHANNELS:
        frames = frames[:, :TARGET_CHANNELS]
    if rate == TARGET_SAMPLE_RATE or frames.shape[0] == 0:
        return frames

    count = max(1, int(round(frames.shape[0] * TARGET_SAMPLE_RATE / rate)))
    # shortcut: 线性插值重采样，轮盘音效够用；对音质有要求时再换更高质量的算法。
    source = np.linspace(0.0, frames.shape[0] - 1, count)
    axis = np.arange(frames.shape[0], dtype=np.float64)
    resampled = np.stack(
        [
            np.interp(source, axis, frames[:, channel].astype(np.float64))
            for channel in range(TARGET_CHANNELS)
        ],
        axis=1,
    )
    return np.clip(np.rint(resampled), -32768, 32767).astype(np.int16)


__all__ = [
    "MAX_RECORD_SECONDS",
    "MIN_RECORD_SECONDS",
    "TARGET_CHANNELS",
    "TARGET_SAMPLE_RATE",
    "RecordingError",
    "RollingAudioRecorder",
    "SystemAudioRecorder",
    "cancel_active_recording",
    "cancel_active_rolling_recording",
]
