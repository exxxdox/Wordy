#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""driver_manager 模块单元测试。"""

from unittest.mock import patch

from wordy.audio.driver import (
    VB_CABLE_INPUT_NAME,
    VB_CABLE_OUTPUT_NAME,
    VB_CABLE_DOWNLOAD_URL,
    VBCableDriverManager,
)


class TestVBCableDriverManager:
    """VBCableDriverManager 测试集。"""

    def test_is_installed_true_when_cable_output_found(self):
        """设备名包含 CABLE Output 时返回 True。"""
        with patch("wordy.audio.driver.sd.query_devices") as mock_query:
            mock_query.return_value = [
                {"name": "CABLE Output (VB-Audio Virtual Cable)", "max_input_channels": 2,
                 "max_output_channels": 0},
            ]
            assert VBCableDriverManager.is_installed() is True

    def test_is_installed_true_when_cable_input_found(self):
        """设备名包含 CABLE Input 时返回 True。"""
        with patch("wordy.audio.driver.sd.query_devices") as mock_query:
            mock_query.return_value = [
                {"name": "CABLE Input (VB-Audio Virtual Cable)", "max_input_channels": 0,
                 "max_output_channels": 2},
            ]
            assert VBCableDriverManager.is_installed() is True

    def test_is_installed_false_when_no_cable(self):
        """无 VB-CABLE 设备时返回 False。"""
        with patch("wordy.audio.driver.sd.query_devices") as mock_query:
            mock_query.return_value = [
                {"name": "Realtek Audio", "max_input_channels": 2, "max_output_channels": 2},
            ]
            assert VBCableDriverManager.is_installed() is False

    def test_is_installed_handles_sd_error(self):
        """sd.query_devices 异常时返回 False 而非抛出。"""
        with patch("wordy.audio.driver.sd.query_devices", side_effect=Exception("PortAudio error")):
            assert VBCableDriverManager.is_installed() is False

    def test_get_status_full_cable(self):
        """完整 VB-CABLE 安装返回完整状态。"""
        devices = [
            {
                "name": "麦克风 (Realtek)",
                "max_input_channels": 2,
                "max_output_channels": 0,
            },
            {
                "name": "CABLE Output (VB-Audio)",
                "max_input_channels": 2,
                "max_output_channels": 0,
            },
            {
                "name": "CABLE Input (VB-Audio)",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
        ]
        with patch("wordy.audio.driver.sd.query_devices", return_value=devices):
            status = VBCableDriverManager.get_status()
            assert status["installed"] is True
            assert status["input_device_index"] == 1
            assert status["output_device_index"] == 2
            assert "CABLE Output" in status["input_device_name"]
            assert "CABLE Input" in status["output_device_name"]

    def test_get_status_prefers_wasapi_over_legacy_host_apis(self):
        """同名重复端点中必须选择与 MMDevice 一致的 WASAPI 条目。"""
        devices = [
            {
                "name": "CABLE Input (VB-Audio)",
                "max_input_channels": 0,
                "max_output_channels": 2,
                "hostapi": 0,
            },
            {
                "name": "CABLE Input (VB-Audio Virtual Cable)",
                "max_input_channels": 0,
                "max_output_channels": 2,
                "hostapi": 1,
            },
        ]
        hostapis = [{"name": "MME"}, {"name": "Windows WASAPI"}]
        with (
            patch("wordy.audio.driver.sd.query_devices", return_value=devices),
            patch("wordy.audio.driver.sd.query_hostapis", return_value=hostapis),
        ):
            status = VBCableDriverManager.get_status()

        assert status["output_device_index"] == 1
        assert status["output_device_name"] == "CABLE Input (VB-Audio Virtual Cable)"

    def test_get_status_falls_back_when_host_api_query_fails(self):
        devices = [
            {
                "name": "CABLE Input (VB-Audio)",
                "max_input_channels": 0,
                "max_output_channels": 2,
            },
        ]
        with (
            patch("wordy.audio.driver.sd.query_devices", return_value=devices),
            patch("wordy.audio.driver.sd.query_hostapis", side_effect=RuntimeError("failed")),
        ):
            status = VBCableDriverManager.get_status()

        assert status["installed"] is True
        assert status["output_device_index"] == 0

    def test_get_status_partial_cable(self):
        """仅安装输入端时的状态。"""
        devices = [
            {
                "name": "CABLE Output (VB-Audio)",
                "max_input_channels": 2,
                "max_output_channels": 0,
            },
        ]
        with patch("wordy.audio.driver.sd.query_devices", return_value=devices):
            status = VBCableDriverManager.get_status()
            assert status["installed"] is True
            assert status["input_device_index"] == 0
            assert status["output_device_index"] is None

    def test_get_status_handles_query_error(self):
        """查询异常时返回默认空状态。"""
        with patch("wordy.audio.driver.sd.query_devices", side_effect=Exception("error")):
            status = VBCableDriverManager.get_status()
            assert status["installed"] is False
            assert status["input_device_index"] is None

    def test_get_virtual_output_index(self):
        """get_virtual_output_index 返回输出设备索引。"""
        devices = [
            {"name": "CABLE Input (VB-Audio)", "max_input_channels": 0, "max_output_channels": 2},
        ]
        with patch("wordy.audio.driver.sd.query_devices", return_value=devices):
            idx = VBCableDriverManager.get_virtual_output_index()
            assert idx == 0

    def test_get_virtual_output_index_none_when_no_device(self):
        """无虚拟输出设备时返回 None。"""
        with patch("wordy.audio.driver.sd.query_devices", return_value=[]):
            idx = VBCableDriverManager.get_virtual_output_index()
            assert idx is None


    def test_constants(self):
        """验证常量值。"""
        assert VB_CABLE_INPUT_NAME == "CABLE Output"
        assert VB_CABLE_OUTPUT_NAME == "CABLE Input"
        assert "vb-audio.com" in VB_CABLE_DOWNLOAD_URL

