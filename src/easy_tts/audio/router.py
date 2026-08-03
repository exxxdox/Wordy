#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频路由引擎：实时混音、多线程音频路由、TTS 注入。

将物理麦克风、可选桥接源、TTS 音频实时混合后输出到虚拟音频设备。
基于 sounddevice 的 RawStream，使用 numpy 进行向量化混音。
"""

from __future__ import annotations

import io
import logging
import threading
import time
import wave
from collections import deque
from dataclasses import dataclass
from typing import Callable

import numpy as np
import sounddevice as sd

from easy_tts.audio.capture import AudioCapture, DEFAULT_BLOCKSIZE, DEFAULT_SAMPLE_RATE
from easy_tts.audio.driver import VBCableDriverManager

logger = logging.getLogger(__name__)

MIX_BUFFER_DURATION_MS = 200  # 环形缓冲区时长
MAX_TTS_QUEUE_SIZE = 20
MAX_TEST_BUFFER_SECONDS = 30  # 测试录制最大时长，防止内存无界增长
GAIN_MIC_DEFAULT = 1.0
GAIN_BRIDGE_DEFAULT = 1.0
GAIN_TTS_DEFAULT = 1.0
CLIP_MIN = np.iinfo(np.int16).min
CLIP_MAX = np.iinfo(np.int16).max


def _pcm_to_wav(pcm_data: bytes, sample_rate: int, channels: int, sample_width: int = 2) -> bytes:
    """将原始 PCM 数据包装为 WAV 格式字节流。"""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(sample_width)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_data)
    return buf.getvalue()


def _resample_linear(data: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """线性插值重采样。无外部依赖，适合 TTS 语音。"""
    if src_rate == dst_rate:
        return data.copy()  # 防御性副本，避免调用方意外修改原数组
    n_out = int(len(data) * dst_rate / src_rate)
    x_old = np.arange(len(data), dtype=np.float64)
    x_new = np.linspace(0, len(data) - 1, n_out, dtype=np.float64)
    return np.interp(x_new, x_old, data.astype(np.float64)).astype(data.dtype)


@dataclass
class RouterStats:
    """音频路由实时统计。"""

    is_running: bool = False
    latency_ms: float = 0.0
    underrun_count: int = 0
    overrun_count: int = 0
    mix_peak_db: float = -96.0


class RingBuffer:
    """线程安全的单声道 int16 环形缓冲区。"""

    def __init__(self, capacity_frames: int, channels: int = 1):
        self.capacity = capacity_frames
        self.channels = channels
        self._buffer = np.zeros((capacity_frames, channels), dtype=np.int16)
        self._write_pos = 0
        self._read_pos = 0
        self._available = 0
        self._lock = threading.Lock()

    def write(self, data: np.ndarray) -> int:
        """写入数据，返回实际写入的帧数。使用 numpy 切片代替逐帧循环。"""
        with self._lock:
            frames = len(data)
            available = self.capacity - self._available
            if frames > available:
                frames = available

            write_end = self._write_pos + frames
            if write_end <= self.capacity:
                self._buffer[self._write_pos:write_end] = data[:frames]
            else:
                first = self.capacity - self._write_pos
                self._buffer[self._write_pos:] = data[:first]
                self._buffer[:frames - first] = data[first:frames]

            self._write_pos = write_end % self.capacity
            self._available += frames
            return frames

    def read(self, frames: int) -> np.ndarray:
        """读取指定帧数，不足时补静音。使用 numpy 切片代替逐帧循环。"""
        with self._lock:
            out = np.zeros((frames, self.channels), dtype=np.int16)
            to_read = min(frames, self._available)

            read_end = self._read_pos + to_read
            if read_end <= self.capacity:
                out[:to_read] = self._buffer[self._read_pos:read_end]
            else:
                first = self.capacity - self._read_pos
                out[:first] = self._buffer[self._read_pos:]
                out[first:to_read] = self._buffer[:to_read - first]

            self._read_pos = read_end % self.capacity
            self._available -= to_read
            return out

    def clear(self) -> None:
        """清空缓冲区。"""
        with self._lock:
            self._write_pos = 0
            self._read_pos = 0
            self._available = 0
            self._buffer.fill(0)

    @property
    def available(self) -> int:
        with self._lock:
            return self._available


class AudioRouter:
    """音频路由引擎。

    同时管理：
    - 麦克风捕获（主输入）
    - 桥接源捕获（可选的第二输入）
    - TTS 音频注入队列
    - 混音输出到虚拟设备

    所有音频统一为 48kHz / 16bit / 单声道。输入设备采样率不一致时自动重采样。
    """

    def __init__(
        self,
        mic_device: str | None = None,
        bridge_device: str | None = None,
        virtual_output: str | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = 1,
        blocksize: int = DEFAULT_BLOCKSIZE,
    ):
        self.mic_device = mic_device
        self.bridge_device = bridge_device
        self.virtual_output = virtual_output or VBCableDriverManager.get_status().get("output_device_name")
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize

        self.gain_mic = GAIN_MIC_DEFAULT
        self.gain_bridge = GAIN_BRIDGE_DEFAULT
        self.gain_tts = GAIN_TTS_DEFAULT

        self._mic_capture: AudioCapture | None = None
        self._bridge_capture: AudioCapture | None = None
        self._output_stream: sd.RawOutputStream | None = None
        self._mixer_thread: threading.Thread | None = None
        self._shutdown_event = threading.Event()

        # TTS 注入队列（非阻塞，丢弃最旧）
        self._tts_queue: deque[np.ndarray] = deque(maxlen=MAX_TTS_QUEUE_SIZE)
        self._tts_lock = threading.Lock()

        # 麦克风数据暂存（回调写入，混音线程读取）
        self._mic_ring: RingBuffer = RingBuffer(
            capacity_frames=int(sample_rate * MIX_BUFFER_DURATION_MS / 1000),
            channels=channels,
        )
        self._bridge_ring: RingBuffer = RingBuffer(
            capacity_frames=int(sample_rate * MIX_BUFFER_DURATION_MS / 1000),
            channels=channels,
        )

        self._stats = RouterStats()
        self._stats_lock = threading.Lock()
        self._on_stats: Callable[[RouterStats], None] | None = None

        # 测试录制（用于设置界面的 5 秒测试）
        self._test_recording = False
        self._test_buffer: list[np.ndarray] = []
        self._test_lock = threading.Lock()

    def set_mic_device(self, device_name: str | None) -> bool:
        """切换麦克风设备。运行中时会自动重启对应捕获流。"""
        if self.mic_device == device_name:
            return True
        self.mic_device = device_name
        if self._mic_capture is not None and self._mic_capture.is_running():
            return self._mic_capture.set_device(device_name)
        return True

    def set_bridge_device(self, device_name: str | None) -> bool:
        """切换桥接源设备。传入 ``None`` 禁用桥接。"""
        if self.bridge_device == device_name:
            return True
        was_running = self.is_running()
        had_bridge = self.bridge_device is not None
        self.bridge_device = device_name

        if not was_running:
            return True

        if device_name is None and had_bridge and self._bridge_capture is not None:
            self._bridge_capture.stop()
            self._bridge_ring.clear()
            return True

        if device_name is not None:
            if self._bridge_capture is None:
                self._bridge_capture = AudioCapture(
                    device_name=device_name,
                    sample_rate=self.sample_rate,
                    channels=self.channels,
                    blocksize=self.blocksize,
                    on_data=self._on_bridge_data,
                )
            return self._bridge_capture.set_device(device_name)

        return True

    def set_virtual_output(self, device_name: str | None) -> bool:
        """切换虚拟输出设备。运行中时需要重启整个路由。"""
        if self.virtual_output == device_name:
            return True
        was_running = self.is_running()
        self.stop()
        self.virtual_output = device_name
        if was_running:
            return self.start()
        return True

    def set_gains(self, mic: float | None = None, bridge: float | None = None, tts: float | None = None) -> None:
        """设置各通道增益系数。"""
        if mic is not None:
            self.gain_mic = max(0.0, float(mic))
        if bridge is not None:
            self.gain_bridge = max(0.0, float(bridge))
        if tts is not None:
            self.gain_tts = max(0.0, float(tts))

    def inject_tts(self, data: bytes) -> None:
        """注入 TTS 音频数据。数据应为 48kHz / 16bit / 单声道 的原始 PCM。"""
        try:
            arr = np.frombuffer(data, dtype=np.int16)
            if self.channels == 1 and arr.ndim > 1:
                arr = arr[:, 0]
            if arr.ndim == 1:
                arr = arr.reshape(-1, 1)
            with self._tts_lock:
                if len(self._tts_queue) >= MAX_TTS_QUEUE_SIZE:
                    self._tts_queue.popleft()
                self._tts_queue.append(arr)
        except Exception:
            logger.exception("TTS 音频注入失败")

    def inject_tts_array(self, arr: np.ndarray) -> None:
        """直接注入 numpy 数组形式的 TTS 音频。"""
        try:
            if self.channels == 1 and arr.ndim > 1:
                arr = arr[:, 0]
            if arr.ndim == 1:
                arr = arr.reshape(-1, 1)
            with self._tts_lock:
                if len(self._tts_queue) >= MAX_TTS_QUEUE_SIZE:
                    self._tts_queue.popleft()
                self._tts_queue.append(arr.astype(np.int16))
        except Exception:
            logger.exception("TTS 数组注入失败")

    def inject_tts_from_wav(
        self,
        pcm_data: bytes,
        src_rate: int,
        src_channels: int,
    ) -> None:
        """注入 WAV 格式的 PCM 数据，自动转换为路由器所需的 48kHz/单声道/16bit。

        播放器在播放任意格式的 WAV 时调用此方法，确保注入混音的数据格式正确。
        """
        try:
            arr = np.frombuffer(pcm_data, dtype=np.int16)
            # 多声道转单声道（取第一声道）
            if src_channels > 1:
                arr = arr.reshape(-1, src_channels)
                arr = arr[:, 0]
            # 重采样到路由器采样率
            if src_rate != self.sample_rate:
                arr = _resample_linear(arr, src_rate, self.sample_rate)
            # 转为 (N, 1) 形状
            if arr.ndim == 1:
                arr = arr.reshape(-1, 1)
            with self._tts_lock:
                if len(self._tts_queue) >= MAX_TTS_QUEUE_SIZE:
                    self._tts_queue.popleft()
                self._tts_queue.append(arr.astype(np.int16))
        except Exception:
            logger.exception("TTS WAV 注入失败")

    def start_test_recording(self) -> None:
        """启动测试录制。混音线程会将输出数据保存到内存缓冲区。"""
        with self._test_lock:
            self._test_recording = True
            self._test_buffer.clear()
        logger.info("测试录制已启动")

    def stop_test_recording(self) -> bytes | None:
        """停止测试录制，将捕获的混音数据转为 WAV 格式返回。"""
        with self._test_lock:
            self._test_recording = False
            if not self._test_buffer:
                logger.info("测试录制已停止，无数据")
                return None
            combined = np.concatenate(self._test_buffer, axis=0)
            self._test_buffer.clear()

        wav_bytes = _pcm_to_wav(combined.tobytes(), self.sample_rate, self.channels, 2)
        logger.info("测试录制已停止，生成 WAV %s bytes", len(wav_bytes))
        return wav_bytes

    def _on_mic_data(self, data: np.ndarray) -> None:
        """麦克风数据回调。"""
        self._mic_ring.write(data)

    def _on_bridge_data(self, data: np.ndarray) -> None:
        """桥接源数据回调。"""
        self._bridge_ring.write(data)

    def _resolve_output_device(self) -> int | None:
        """解析输出设备索引。"""
        if self.virtual_output is not None:
            try:
                devices = sd.query_devices()
                for idx, dev in enumerate(devices):
                    name = dev.get("name", "")
                    if isinstance(name, str) and self.virtual_output in name:
                        max_out = dev.get("max_output_channels", 0)
                        if max_out > 0:
                            return idx
            except Exception as e:  # noqa: BLE001
                logger.warning("查询输出设备失败: %s", e)

        # 回退到 VB-CABLE
        idx = VBCableDriverManager.get_virtual_output_index()
        if idx is not None:
            return idx

        logger.error("无法解析虚拟输出设备")
        return None

    def _mixer_loop(self) -> None:
        """混音主循环：在独立线程中运行。"""
        logger.info("混音线程已启动")
        block_frames = self.blocksize
        tts_offset = 0
        current_tts: np.ndarray | None = None

        while not self._shutdown_event.is_set():
            t0 = time.perf_counter()

            # 1. 从各源读取数据
            mic_data = self._mic_ring.read(block_frames)
            bridge_data = (
                self._bridge_ring.read(block_frames)
                if self.bridge_device is not None
                else np.zeros((block_frames, self.channels), dtype=np.int16)
            )

            # 2. 获取 TTS 数据
            tts_data = np.zeros((block_frames, self.channels), dtype=np.int16)
            if current_tts is None or tts_offset >= len(current_tts):
                with self._tts_lock:
                    if self._tts_queue:
                        current_tts = self._tts_queue.popleft()
                        tts_offset = 0
                    else:
                        current_tts = None

            if current_tts is not None:
                remaining = len(current_tts) - tts_offset
                to_copy = min(block_frames, remaining)
                tts_data[:to_copy] = current_tts[tts_offset : tts_offset + to_copy]
                tts_offset += to_copy
                if tts_offset >= len(current_tts):
                    current_tts = None

            # 3. 混音（防止溢出）
            mix = (
                mic_data.astype(np.int32) * self.gain_mic
                + bridge_data.astype(np.int32) * self.gain_bridge
                + tts_data.astype(np.int32) * self.gain_tts
            )
            mix = np.clip(mix, CLIP_MIN, CLIP_MAX).astype(np.int16)

            # 3.5 测试录制（限制最大容量防止内存无界增长）
            with self._test_lock:
                if self._test_recording:
                    max_frames = MAX_TEST_BUFFER_SECONDS * self.sample_rate
                    current_frames = sum(b.shape[0] for b in self._test_buffer)
                    if current_frames + mix.shape[0] <= max_frames:
                        self._test_buffer.append(mix.copy())

            # 4. 写入输出流
            if self._output_stream is not None:
                try:
                    self._output_stream.write(mix)
                except sd.PortAudioError as e:
                    logger.warning("输出流写入失败: %s", e)
                    with self._stats_lock:
                        self._stats.underrun_count += 1

            # 5. 更新统计
            elapsed_ms = (time.perf_counter() - t0) * 1000
            peak = np.max(np.abs(mix))
            peak_db = 20 * np.log10(peak / 32768.0 + 1e-10) if peak > 0 else -96.0
            with self._stats_lock:
                self._stats.latency_ms = elapsed_ms
                self._stats.mix_peak_db = peak_db

            # 6. 回调通知
            if self._on_stats is not None:
                with self._stats_lock:
                    stats_copy = RouterStats(
                        is_running=self._stats.is_running,
                        latency_ms=self._stats.latency_ms,
                        underrun_count=self._stats.underrun_count,
                        overrun_count=self._stats.overrun_count,
                        mix_peak_db=self._stats.mix_peak_db,
                    )
                self._on_stats(stats_copy)

            # 7. 音频时钟：每轮应恰好消耗 blocksize 帧对应的时间
            target_interval = self.blocksize / self.sample_rate
            sleep_time = max(0.0, target_interval - (time.perf_counter() - t0))
            if sleep_time > 0:
                time.sleep(sleep_time)

        logger.info("混音线程已退出")

    def start(self) -> bool:
        """启动音频路由引擎。"""
        if self.is_running():
            return True

        # 检查虚拟设备
        output_idx = self._resolve_output_device()
        if output_idx is None:
            logger.error("无法找到虚拟输出设备，音频路由无法启动")
            return False

        try:
            # 启动麦克风捕获
            self._mic_capture = AudioCapture(
                device_name=self.mic_device,
                sample_rate=self.sample_rate,
                channels=self.channels,
                blocksize=self.blocksize,
                on_data=self._on_mic_data,
            )
            if not self._mic_capture.start():
                logger.error("麦克风捕获启动失败")
                self._mic_capture = None
                return False

            # 启动桥接捕获（如启用）
            if self.bridge_device is not None:
                self._bridge_capture = AudioCapture(
                    device_name=self.bridge_device,
                    sample_rate=self.sample_rate,
                    channels=self.channels,
                    blocksize=self.blocksize,
                    on_data=self._on_bridge_data,
                )
                if not self._bridge_capture.start():
                    logger.warning("桥接源捕获启动失败，将继续无桥接模式")
                    self._bridge_capture = None

            # 启动输出流
            self._output_stream = sd.RawOutputStream(
                device=output_idx,
                samplerate=self.sample_rate,
                channels=self.channels,
                blocksize=self.blocksize,
                dtype=np.int16,
            )
            self._output_stream.start()

            # 启动混音线程
            self._shutdown_event.clear()
            self._mixer_thread = threading.Thread(target=self._mixer_loop, name="AudioRouterMixer", daemon=True)
            self._mixer_thread.start()

            with self._stats_lock:
                self._stats.is_running = True

            logger.info("音频路由引擎已启动")
            return True

        except Exception as e:
            logger.exception("启动音频路由引擎失败: %s", e)
            self.stop()
            return False

    def stop(self) -> None:
        """停止音频路由引擎并清理所有资源。"""
        logger.info("正在停止音频路由引擎...")
        self._shutdown_event.set()

        if self._mixer_thread is not None:
            self._mixer_thread.join(timeout=2.0)
            self._mixer_thread = None

        if self._mic_capture is not None:
            self._mic_capture.stop()
            self._mic_capture = None

        if self._bridge_capture is not None:
            self._bridge_capture.stop()
            self._bridge_capture = None

        if self._output_stream is not None:
            try:
                self._output_stream.stop()
                self._output_stream.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("关闭输出流失败: %s", e)
            self._output_stream = None

        self._mic_ring.clear()
        self._bridge_ring.clear()
        with self._tts_lock:
            self._tts_queue.clear()

        with self._stats_lock:
            self._stats.is_running = False

        logger.info("音频路由引擎已停止")

    def is_running(self) -> bool:
        """返回引擎是否正在运行。"""
        with self._stats_lock:
            return self._stats.is_running

    def get_stats(self) -> RouterStats:
        """获取当前统计信息副本。"""
        with self._stats_lock:
            return RouterStats(
                is_running=self._stats.is_running,
                latency_ms=self._stats.latency_ms,
                underrun_count=self._stats.underrun_count,
                overrun_count=self._stats.overrun_count,
                mix_peak_db=self._stats.mix_peak_db,
            )

    def set_on_stats(self, callback: Callable[[RouterStats], None] | None) -> None:
        """设置统计信息更新回调。"""
        self._on_stats = callback


__all__ = [
    "AudioRouter",
    "RouterStats",
    "RingBuffer",
    "GAIN_MIC_DEFAULT",
    "GAIN_BRIDGE_DEFAULT",
    "GAIN_TTS_DEFAULT",
]
