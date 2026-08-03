#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""VB-CABLE 虚拟音频驱动检测与管理。

不捆绑驱动文件，仅检测系统是否已安装 VB-CABLE，并在缺失时引导用户自行安装。
"""

from __future__ import annotations

import logging
import subprocess
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
        """获取 VB-CABLE 详细状态，包括设备索引。"""
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

        for idx, dev in enumerate(devices):
            name = dev.get("name", "")
            if not isinstance(name, str):
                continue

            max_input = dev.get("max_input_channels", 0)
            max_output = dev.get("max_output_channels", 0)

            if VB_CABLE_INPUT_NAME in name and max_input > 0:
                status["installed"] = True
                status["input_device_index"] = idx
                status["input_device_name"] = name

            if VB_CABLE_OUTPUT_NAME in name and max_output > 0:
                status["installed"] = True
                status["output_device_index"] = idx
                status["output_device_name"] = name

        return status

    @staticmethod
    def get_virtual_output_index() -> int | None:
        """获取虚拟扬声器（CABLE Input）的设备索引，用于写入。"""
        status = VBCableDriverManager.get_status()
        return status.get("output_device_index")

    @staticmethod
    def get_virtual_input_index() -> int | None:
        """获取虚拟麦克风（CABLE Output）的设备索引，用于读取。"""
        status = VBCableDriverManager.get_status()
        return status.get("input_device_index")

    @staticmethod
    def open_download_page() -> None:
        """打开 VB-CABLE 下载页面。"""
        logger.info("正在打开 VB-CABLE 下载页面: %s", VB_CABLE_DOWNLOAD_URL)
        webbrowser.open(VB_CABLE_DOWNLOAD_URL)

    @staticmethod
    def get_install_instructions() -> str:
        """返回安装指导文本。"""
        return (
            "本功能需要 VB-CABLE 虚拟音频驱动。\n\n"
            "安装步骤:\n"
            "1. 点击“打开下载页面”前往 vb-audio.com\n"
            "2. 下载 VBCABLE_Driver_Pack45.zip\n"
            "3. 解压后右键 VBCABLE_Setup_x64.exe → 以管理员身份运行\n"
            "4. 安装完成后重启电脑\n"
            "5. 返回本应用，音频路由功能将自动启用"
        )

    @staticmethod
    def test_signing_enabled() -> bool:
        """检查系统是否启用了测试签名模式（开发调试用）。"""
        try:
            result = subprocess.run(
                ["bcdedit", "/enum", "{current}"],
                capture_output=True,
                text=True,
                check=False,
                timeout=5,
            )
            return "testsigning" in result.stdout.lower() and "Yes" in result.stdout
        except Exception:  # noqa: BLE001
            return False


__all__ = [
    "VBCableDriverManager",
    "VBCableStatus",
    "VB_CABLE_INPUT_NAME",
    "VB_CABLE_OUTPUT_NAME",
    "VB_CABLE_DOWNLOAD_URL",
]
