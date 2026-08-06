#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""VB-CABLE 虚拟音频驱动检测与管理。

不捆绑驱动文件，仅检测系统是否已安装 VB-CABLE，并在缺失时引导用户自行安装。
"""

from __future__ import annotations

import logging
import webbrowser
from typing import TypedDict

import sounddevice as sd

logger = logging.getLogger(__name__)

VB_CABLE_INPUT_NAME = "CABLE Output"
VB_CABLE_OUTPUT_NAME = "CABLE Input"
VB_CABLE_DOWNLOAD_URL = "https://vb-audio.com/Cable/"


class VBCableStatus(TypedDict):
    """VB-CABLE 状态信息。"""

    installed: bool
    input_device_index: int | None
    output_device_index: int | None
    input_device_name: str | None
    output_device_name: str | None


class VBCableDriverManager:
    """VB-CABLE 驱动管理器。

    职责：
    - 检测系统是否已安装 VB-CABLE
    - 查询虚拟设备在 sounddevice 中的索引
    - 引导用户到官网下载安装
    """

    @staticmethod
    def is_installed() -> bool:
        """检查系统中是否存在 VB-CABLE 设备。"""
        try:
            devices = sd.query_devices()
        except Exception as e:  # noqa: BLE001
            logger.warning("查询音频设备失败: %s", e)
            return False

        for dev in devices:
            name = dev.get("name", "")
            if isinstance(name, str) and (VB_CABLE_INPUT_NAME in name or VB_CABLE_OUTPUT_NAME in name):
                return True
        return False

    @staticmethod
    def get_status() -> VBCableStatus:
        """获取 VB-CABLE 状态，并优先返回 Windows WASAPI 端点。

        同一设备通常会被 PortAudio 以 MME/DirectSound/WASAPI 重复枚举；侦听
        策略使用 MMDevice，因此必须优先选择与其命名域一致的 WASAPI 条目。
        """
        status: VBCableStatus = {
            "installed": False,
            "input_device_index": None,
            "output_device_index": None,
            "input_device_name": None,
            "output_device_name": None,
        }

        try:
            devices = sd.query_devices()
        except Exception as e:  # noqa: BLE001
            logger.warning("查询音频设备失败: %s", e)
            return status
        try:
            hostapis = sd.query_hostapis()
        except Exception as e:  # noqa: BLE001
            # Host API 元数据失败时仍保留名称检测能力，只是不再具备 WASAPI 优先级。
            logger.warning("查询 Host API 失败，回退到设备枚举顺序: %s", e)
            hostapis = []

        input_rank = -1
        output_rank = -1
        for idx, dev in enumerate(devices):
            name = dev.get("name", "")
            if not isinstance(name, str):
                continue

            try:
                host_api_idx = dev.get("hostapi", -1)
                host_api_name = hostapis[host_api_idx].get("name", "")
            except Exception:  # noqa: BLE001 - 元数据缺失时仍允许兼容旧设备列表
                host_api_name = ""
            rank = 1 if host_api_name == "Windows WASAPI" else 0

            max_input = dev.get("max_input_channels", 0)
            max_output = dev.get("max_output_channels", 0)

            if VB_CABLE_INPUT_NAME in name and max_input > 0:
                status["installed"] = True
                if rank > input_rank:
                    input_rank = rank
                    status["input_device_index"] = idx
                    status["input_device_name"] = name

            if VB_CABLE_OUTPUT_NAME in name and max_output > 0:
                status["installed"] = True
                if rank > output_rank:
                    output_rank = rank
                    status["output_device_index"] = idx
                    status["output_device_name"] = name

        return status

    @staticmethod
    def get_virtual_output_index() -> int | None:
        """获取虚拟扬声器（CABLE Input）的设备索引，用于写入。"""
        status = VBCableDriverManager.get_status()
        return status.get("output_device_index")

    @staticmethod
    def open_download_page() -> None:
        """打开 VB-CABLE 下载页面。"""
        logger.debug("正在打开 VB-CABLE 下载页面: %s", VB_CABLE_DOWNLOAD_URL)
        webbrowser.open(VB_CABLE_DOWNLOAD_URL)




__all__ = [
    "VBCableDriverManager",
    "VBCableStatus",
    "VB_CABLE_INPUT_NAME",
    "VB_CABLE_OUTPUT_NAME",
    "VB_CABLE_DOWNLOAD_URL",
]
