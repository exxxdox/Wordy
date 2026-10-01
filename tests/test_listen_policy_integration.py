#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Windows 真实音频端点集成测试；默认跳过，避免常规测试修改系统设置。"""

from __future__ import annotations

import os

import pytest

from wordy.audio.capture import list_input_devices
from wordy.audio.driver import VBCableDriverManager
from wordy.audio.listen_policy import (
    get_listen_policy,
    restore_listen_policy,
    set_listen_policy,
)


def test_listen_policy_round_trip_restores_original_state() -> None:
    """对真实 WASAPI 端点执行启用/验证，并始终恢复接管前状态。"""
    if os.environ.get("EASY_TTS_RUN_AUDIO_INTEGRATION") != "1":
        pytest.skip("设置 EASY_TTS_RUN_AUDIO_INTEGRATION=1 后运行真实音频测试")

    status = VBCableDriverManager.get_status()
    target = status.get("output_device_name")
    if not isinstance(target, str):
        pytest.skip("没有 Windows WASAPI VB-CABLE 输出端点")

    mic = os.environ.get("EASY_TTS_INTEGRATION_MIC")
    if not mic:
        pytest.skip("设置 EASY_TTS_INTEGRATION_MIC 为要测试的 WASAPI 输入设备名")

    input_names = {device["name"] for device in list_input_devices()}
    if mic not in input_names:
        pytest.skip(f"指定设备不是活动的 Windows WASAPI 输入端点: {mic}")

    original_state = get_listen_policy(mic)
    if original_state is None:
        pytest.skip("无法安全读取原侦听状态，不执行写入测试")

    try:
        assert set_listen_policy(mic, target, enabled=True), "启用并读回验证侦听策略失败"
    finally:
        assert restore_listen_policy(mic, original_state), "恢复原侦听状态失败"
