#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频侦听引擎。

不捕获麦克风——由 Windows "侦听此设备" 功能将 mic 直通到 VB-CABLE Input。
本模块只负责：启用时将 TTS 音频输出重定向到 VB-CABLE Input，
让 TTS 语音和 mic 在系统层叠加后从 CABLE Output 输出。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypedDict

import sounddevice as sd

from wordy.audio.driver import VBCableDriverManager

logger = logging.getLogger(__name__)


class RouterOutputDevice(TypedDict):
    """VB-CABLE Input 输出设备的结构化选择（兼容 AudioPlayer 接口）。"""
    name: str
    host_api_name: str


@dataclass
class RouterStats:
    """音频侦听状态，区分 TTS 路由与麦克风侦听是否均已就绪。"""

    is_running: bool = False
    listen_configured: bool = False


class AudioRouter:
    """音频侦听引擎。

    启用时 TTS 输出重定向到 VB-CABLE Input。
    麦克风直通由 Windows 系统"侦听此设备"功能处理——零延迟、零 CPU。

    使用方式：
        router = AudioRouter()
        if router.start():
            player.set_output_device(router.get_output_device())
    """

    def __init__(self, virtual_output: str | None = None):
        self.virtual_output = virtual_output or VBCableDriverManager.get_status().get("output_device_name")
        self._active = False
        self._mic_device: str | None = None
        self._listen_configured = False

    # ── 生命周期 ─────────────────────────────────────────────────────────

    def start(self, *, mic_device: str | None = None, configure_listen: bool = True) -> bool:
        """启用 TTS 路由，仅在用户主动开启时配置 Windows 侦听。"""
        if self._active:
            return True
        # Windows 侦听策略与播放器都依赖 WASAPI 端点，不能用其他 Host API 假启动。
        if self.get_output_device() is None:
            logger.error("无法找到 Windows WASAPI 的 VB-CABLE 输出设备")
            return False
        if self._resolve_output_device() is None:
            logger.error("无法解析 VB-CABLE 输出设备")
            return False

        self._active = True
        self._mic_device = mic_device
        self._listen_configured = False
        if configure_listen and mic_device is not None:
            self._listen_configured = self._enable_mic_listen(mic_device)

        logger.info("TTS 音频侦听已启用（TTS → CABLE Input）")
        return True

    def stop(self, *, preserve_listen: bool = False) -> bool:
        """停用路由。退出应用时保留 Windows 当前的侦听状态。"""
        if self._active and not preserve_listen and self._mic_device is not None:
            # 显式关闭作用于当前麦克风，也覆盖启动时从 Windows 沿用的状态。
            if not self._disable_mic_listen(self._mic_device):
                return False
        self._listen_configured = False
        self._active = False
        logger.info("TTS 音频路由已停用")
        return True

    def set_mic_device(self, device_name: str | None) -> bool:
        """路由开启时，切换麦克风会关闭旧设备并开启新设备。"""
        from wordy.audio.listen_policy import get_listen_policy, restore_listen_policy

        old = self._mic_device
        if old == device_name:
            return True

        previous = None
        if self._active and old is not None and device_name is not None:
            previous = get_listen_policy(old)
        if self._active and old is not None and not self._disable_mic_listen(old):
            logger.error("无法关闭旧麦克风侦听，已取消切换: %s → %s", old, device_name)
            return False

        if self._active and device_name is not None:
            if not self._enable_mic_listen(device_name):
                # 仅回退本次失败的切换，保留旧设备刚才使用的目标。
                self._listen_configured = False
                if old is not None and previous is not None:
                    self._listen_configured = restore_listen_policy(old, previous) and previous.enabled
                return False
            self._listen_configured = True
        else:
            self._listen_configured = False
        self._mic_device = device_name

        if device_name is not None:
            logger.debug("麦克风侦听已切换: %s → %s", old, device_name)
        else:
            logger.debug("麦克风侦听已停用（原: %s）", old)
        return True

    def _enable_mic_listen(self, mic_name: str) -> bool:
        """使用 WASAPI 的 VB-CABLE 名称配置 Windows Core Audio 侦听。"""
        from wordy.audio.listen_policy import set_listen_policy

        cable_device = self.get_output_device()
        if cable_device is None:
            logger.warning("未找到 Windows WASAPI 的 CABLE Input，无法配置麦克风侦听")
            return False
        return set_listen_policy(mic_name, cable_device["name"], enabled=True)

    def _disable_mic_listen(self, mic_name: str) -> bool:
        """关闭当前设备的 Windows 侦听，保留系统记录的输出目标。"""
        from wordy.audio.listen_policy import set_listen_policy

        return set_listen_policy(mic_name, "", enabled=False)

    def is_running(self) -> bool:
        """返回是否正在运行。"""
        return self._active

    # ── 设备查询 ─────────────────────────────────────────────────────────

    def get_output_device(self) -> RouterOutputDevice | None:
        """返回与 MMDevice 命名域一致的 WASAPI CABLE Input。"""
        status = VBCableDriverManager.get_status()
        index = status.get("output_device_index")
        name = status.get("output_device_name")
        if index is None or not isinstance(name, str):
            return None

        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            device = devices[index]
            host_api_index = device.get("hostapi", -1)
            host_api_name = hostapis[host_api_index].get("name", "")
        except Exception as e:  # noqa: BLE001
            logger.warning("无法解析 VB-CABLE Host API: %s", e)
            return None

        if host_api_name != "Windows WASAPI":
            logger.warning("VB-CABLE 缺少 Windows WASAPI 输出端点，当前仅找到: %s", host_api_name)
            return None
        return RouterOutputDevice(name=name, host_api_name=host_api_name)

    def _resolve_output_device(self) -> int | None:
        """优先解析 WASAPI VB-CABLE 索引，兼容显式旧配置。"""
        idx = VBCableDriverManager.get_virtual_output_index()
        if idx is not None:
            return idx

        if self.virtual_output is not None:
            try:
                devices = sd.query_devices()
                for index, dev in enumerate(devices):
                    name = dev.get("name", "")
                    if isinstance(name, str) and self.virtual_output in name:
                        if dev.get("max_output_channels", 0) > 0:
                            return index
            except Exception as e:  # noqa: BLE001
                logger.warning("查询输出设备失败: %s", e)

        logger.error("无法解析虚拟输出设备")
        return None

    # ── 兼容接口 ─────────────────────────────────────────────────────────

    def get_stats(self) -> RouterStats:
        return RouterStats(
            is_running=self._active,
            listen_configured=self._listen_configured,
        )

    # 兼容旧调用方；生产 UI 不提供虚拟输出切换。
    def set_virtual_output(self, device_name: str | None) -> bool:
        was_running = self._active
        mic_device = self._mic_device
        self.stop(preserve_listen=True)
        self.virtual_output = device_name
        if was_running:
            return self.start(mic_device=mic_device, configure_listen=False)
        return True
