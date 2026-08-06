#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频输入设备枚举和实时捕获。

使用 sounddevice 提供跨 Host API 的输入设备枚举和实时音频捕获。
与 audio_player.py 保持一致的接口风格，便于上层统一调用。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TypedDict, cast

import numpy as np
import sounddevice as sd

logger = logging.getLogger(__name__)

DEFAULT_SAMPLE_RATE = 48000
DEFAULT_CHANNELS = 1
DEFAULT_BLOCKSIZE = 480  # 10ms @ 48kHz
DEFAULT_DTYPE = np.int16


class InputDeviceInfo(TypedDict):
    """枚举出的输入设备记录。"""

    index: int
    name: str
    host_api_name: str
    host_api_index: int
    display_name: str
    is_default: bool
    default_sample_rate: float
    max_input_channels: int


class AudioCapture:
    """基于 sounddevice 的实时音频捕获器。

    使用后台线程 + 回调模式，将捕获到的音频块通过 ``on_data`` 回调传递给消费者。
    支持动态启动/停止和设备切换。
    """

    def __init__(
        self,
        device_name: str | None = None,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        channels: int = DEFAULT_CHANNELS,
        blocksize: int = DEFAULT_BLOCKSIZE,
        dtype: type = DEFAULT_DTYPE,
        on_data: Callable[[np.ndarray], None] | None = None,
    ):
        self.device_name = device_name
        self.sample_rate = sample_rate
        self.channels = channels
        self.blocksize = blocksize
        self.dtype = dtype
        self.on_data = on_data

        self._stream: sd.RawInputStream | None = None
        self._lock = threading.Lock()
        self._running = False

    @staticmethod
    def list_input_devices() -> list[InputDeviceInfo]:
        """枚举系统可用的输入设备。

        返回包含 ``index`` / ``name`` / ``host_api_name`` / ``display_name`` /
        ``is_default`` 等字段的 dict 列表。仅包含 ``max_input_channels > 0``
        且 Host API 为 Windows WASAPI 的设备（避免同一硬件在 MME/DirectSound
        /WASAPI 下的重复条目）。枚举失败时返回 ``[]``。
        """
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
        except Exception as e:  # noqa: BLE001
            logger.warning("查询 sounddevice 设备失败: %s", e)
            return []

        try:
            default_input = sd.default.device[0]
        except Exception:  # noqa: BLE001
            default_input = None

        result: list[InputDeviceInfo] = []
        for idx, info in enumerate(devices):
            max_input = info.get("max_input_channels", 0)
            if not isinstance(max_input, (int, float)) or max_input <= 0:
                continue

            name = info.get("name", "")
            if not isinstance(name, str) or not name:
                continue

            host_api_idx = info.get("hostapi", 0)
            host_api_name = "Unknown"
            try:
                host_api_name = hostapis[host_api_idx].get("name", "Unknown")
            except Exception:  # noqa: BLE001
                pass

            # 仅保留 WASAPI，避免同一硬件的多重条目
            if host_api_name != "Windows WASAPI":
                continue

            # 排除 VB-CABLE 虚拟输入端（用户不应把它当麦克风）
            if "CABLE Output" in name or "CABLE Input" in name:
                continue

            display_name = f"{name} [{host_api_name}]"
            result.append(
                InputDeviceInfo(
                    index=cast(int, idx),
                    name=name,
                    host_api_name=host_api_name,
                    host_api_index=cast(int, host_api_idx),
                    display_name=display_name,
                    is_default=idx == default_input,
                    default_sample_rate=float(info.get("default_samplerate", 48000.0)),
                    max_input_channels=int(max_input),
                )
            )

        return result

    @staticmethod
    def find_device_index(device_name: str) -> int | None:
        """按名称查找输入设备索引，未找到返回 ``None``。"""
        devices = AudioCapture.list_input_devices()
        for dev in devices:
            if dev["name"] == device_name:
                return dev["index"]
        return None

    def _resolve_device_index(self) -> int | None:
        """解析目标设备索引。"""
        if self.device_name is None:
            try:
                return int(sd.default.device[0])
            except Exception:  # noqa: BLE001
                return None
        idx = AudioCapture.find_device_index(self.device_name)
        if idx is not None:
            return idx
        logger.warning("未找到输入设备 %r，回退到默认设备", self.device_name)
        try:
            return int(sd.default.device[0])
        except Exception:  # noqa: BLE001
            return None

    def _callback(self, indata: np.ndarray, _frames: int, _time_info, _status) -> None:
        """sounddevice 回调：在独立音频线程中执行。"""
        if self.on_data is not None and self._running:
            try:
                # cffi buffer 不支持 .copy()，通过 np.array() 创建副本
                self.on_data(np.array(indata, dtype=self.dtype))
            except Exception:
                logger.exception("音频捕获回调异常")

    def start(self) -> bool:
        """启动捕获流。"""
        with self._lock:
            if self._running:
                return True

            device_idx = self._resolve_device_index()
            if device_idx is None:
                logger.error("无法解析输入设备索引")
                return False

            try:
                self._stream = sd.RawInputStream(
                    device=device_idx,
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    blocksize=self.blocksize,
                    dtype=self.dtype,
                    callback=self._callback,
                )
                self._stream.start()
                self._running = True
                logger.info(
                    "音频捕获已启动: device=%s, sr=%s, ch=%s, block=%s",
                    self.device_name or f"[{device_idx}]",
                    self.sample_rate,
                    self.channels,
                    self.blocksize,
                )
                return True
            except Exception as e:
                logger.error("启动音频捕获失败: %s", e)
                self._stream = None
                return False

    def stop(self) -> None:
        """停止捕获流并清理资源。"""
        with self._lock:
            self._running = False
            stream = self._stream
            self._stream = None

        if stream is not None:
            try:
                stream.stop()
                stream.close()
                logger.debug("音频捕获已停止")
            except Exception as e:
                logger.warning("停止音频捕获流失败: %s", e)

    def is_running(self) -> bool:
        """返回当前是否正在捕获。"""
        with self._lock:
            return self._running and self._stream is not None


__all__ = ["AudioCapture", "InputDeviceInfo", "DEFAULT_SAMPLE_RATE", "DEFAULT_BLOCKSIZE"]
