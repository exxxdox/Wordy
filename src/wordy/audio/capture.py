#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频输入设备枚举。

麦克风侦听由 Windows 原生策略管理；这里只保留设置页面使用的设备枚举。
"""

from __future__ import annotations

import logging
from typing import TypedDict, cast

import sounddevice as sd

logger = logging.getLogger(__name__)


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


# Windows 已承担麦克风侦听，无生产录音调用，保留独立枚举函数即可。
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


__all__ = ["list_input_devices", "InputDeviceInfo"]
