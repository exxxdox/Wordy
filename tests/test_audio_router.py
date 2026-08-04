#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""audio_router 模块单元测试。"""

from unittest.mock import MagicMock, patch

import pytest

from easy_tts.audio.router import AudioRouter, RouterStats


class TestAudioRouter:
    """AudioRouter 测试集。"""

    def test_init(self):
        router = AudioRouter(virtual_output="CABLE Input")
        assert router.is_running() is False

    @patch("easy_tts.audio.router.VBCableDriverManager.get_virtual_output_index")
    def test_start_stop(self, mock_vb_idx):
        mock_vb_idx.return_value = 99
        router = AudioRouter(virtual_output="CABLE Input")
        result = router.start()
        assert result is True
        assert router.is_running() is True

        router.stop()
        assert router.is_running() is False

    def test_listen_takeover_restores_original_state(self):
        from easy_tts.audio.listen_policy import ListenPolicyState

        router = AudioRouter(virtual_output="CABLE Input")
        original = ListenPolicyState(enabled=True, output_id="original-render-id")
        cable = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}

        with (
            patch.object(router, "get_output_device", return_value=cable),
            patch("easy_tts.audio.listen_policy.get_listen_policy", return_value=original),
            patch("easy_tts.audio.listen_policy.set_listen_policy", return_value=True),
        ):
            assert router._enable_mic_listen("Mic") is True

        with patch(
            "easy_tts.audio.listen_policy.restore_listen_policy",
            return_value=True,
        ) as restore:
            assert router._disable_mic_listen("Mic") is True

        restore.assert_called_once_with("Mic", original)
        assert router._listen_original_state is None
        assert router._listen_original_device is None

    def test_switch_mic_disables_old_device_before_enabling_new(self):
        router = AudioRouter(virtual_output="CABLE Input")
        router._active = True
        router._mic_device = "Old Mic"
        router._listen_configured = True

        with (
            patch.object(router, "_disable_mic_listen", return_value=True) as disable,
            patch.object(router, "_enable_mic_listen", return_value=True) as enable,
        ):
            ok = router.set_mic_device("New Mic")

        assert ok is True
        disable.assert_called_once_with("Old Mic")
        enable.assert_called_once_with("New Mic")
        assert router._mic_device == "New Mic"
        assert router.get_stats().listen_configured is True

    def test_switch_mic_aborts_when_old_state_restore_fails(self):
        router = AudioRouter(virtual_output="CABLE Input")
        router._active = True
        router._mic_device = "Old Mic"
        router._listen_configured = True

        with (
            patch.object(router, "_disable_mic_listen", return_value=False),
            patch.object(router, "_enable_mic_listen", return_value=True) as enable,
        ):
            ok = router.set_mic_device("New Mic")

        assert ok is False
        enable.assert_not_called()
        assert router._mic_device == "Old Mic"
        assert router.get_stats().listen_configured is True

    def test_switch_mic_rolls_back_when_new_enable_fails(self):
        """新设备 _enable_mic_listen 失败时应回滚到旧设备。"""
        router = AudioRouter(virtual_output="CABLE Input")
        router._active = True
        router._mic_device = "Old Mic"
        router._listen_configured = True

        enable_results = [False, True]  # new fails, old restore succeeds

        def enable_side_effect(name):
            return enable_results.pop(0)

        with (
            patch.object(router, "_disable_mic_listen", return_value=True) as disable,
            patch.object(router, "_enable_mic_listen", side_effect=enable_side_effect) as enable,
        ):
            ok = router.set_mic_device("New Mic")

        assert ok is False
        # 先禁用旧设备，再尝试启用新设备（失败），最后恢复旧设备
        disable.assert_called_once_with("Old Mic")
        assert enable.call_count == 2
        assert enable.call_args_list[0].args == ("New Mic",)
        assert enable.call_args_list[1].args == ("Old Mic",)
        # 回滚后状态应与初始一致
        assert router._mic_device == "Old Mic"
        assert router.get_stats().listen_configured is True

    def test_switch_mic_no_op_when_same_device(self):
        router = AudioRouter(virtual_output="CABLE Input")
        router._active = True
        router._mic_device = "Same Mic"
        router._listen_configured = True

        with (
            patch.object(router, "_disable_mic_listen") as disable,
            patch.object(router, "_enable_mic_listen") as enable,
        ):
            ok = router.set_mic_device("Same Mic")

        assert ok is True
        disable.assert_not_called()
        enable.assert_not_called()

    @patch("easy_tts.audio.router.sd.query_hostapis")
    @patch("easy_tts.audio.router.sd.query_devices")
    @patch("easy_tts.audio.router.VBCableDriverManager.get_status")
    def test_get_output_device_requires_wasapi(
        self, mock_status, mock_devices, mock_hostapis,
    ):
        mock_status.return_value = {
            "output_device_index": 1,
            "output_device_name": "CABLE Input (VB-Audio Virtual Cable)",
        }
        mock_devices.return_value = [
            {"hostapi": 0},
            {"hostapi": 1},
        ]
        mock_hostapis.return_value = [
            {"name": "MME"},
            {"name": "Windows WASAPI"},
        ]

        device = AudioRouter(virtual_output="CABLE Input").get_output_device()

        assert device == {
            "name": "CABLE Input (VB-Audio Virtual Cable)",
            "host_api_name": "Windows WASAPI",
        }

    @patch("easy_tts.audio.router.VBCableDriverManager.get_virtual_output_index")
    def test_start_no_virtual_device(self, mock_vb_idx):
        mock_vb_idx.return_value = None
        router = AudioRouter(virtual_output="__nonexistent__")
        result = router.start()
        assert result is False
        assert router.is_running() is False

    def test_stats(self):
        router = AudioRouter()
        stats = router.get_stats()
        assert stats.is_running is False

    def test_get_output_device(self):
        """get_output_device 应返回结构化设备信息（需真实 VB-CABLE）。"""
        router = AudioRouter()
        dev = router.get_output_device()
        # 如果未安装 VB-CABLE 则返回 None
        if dev is not None:
            assert isinstance(dev, dict)
            assert "name" in dev
            assert "host_api_name" in dev
            assert "CABLE" in dev["name"]

    @patch("easy_tts.audio.router.VBCableDriverManager.get_virtual_output_index")
    def test_start_without_mic_skips_listen(self, mock_vb_idx):
        """mic_device=None 时 start 不配置侦听——用户需手动选择麦克风。"""
        mock_vb_idx.return_value = 99
        router = AudioRouter(virtual_output="CABLE Input")

        with patch.object(router, "_enable_mic_listen") as enable:
            result = router.start(mic_device=None)

        assert result is True
        enable.assert_not_called()
        assert router._mic_device is None
        assert router.get_stats().listen_configured is False

    def test_set_mic_device_none_disables_listen(self):
        """set_mic_device(None) 停用侦听，返回 True。"""
        router = AudioRouter(virtual_output="CABLE Input")
        router._active = True
        router._mic_device = "Old Mic"
        router._listen_configured = True

        with (
            patch.object(router, "_disable_mic_listen", return_value=True) as disable,
            patch.object(router, "_enable_mic_listen") as enable,
        ):
            ok = router.set_mic_device(None)

        assert ok is True
        disable.assert_called_once_with("Old Mic")
        enable.assert_not_called()
        assert router._mic_device is None
        assert router.get_stats().listen_configured is False

    def test_noop_methods_dont_crash(self):
        """兼容的 no-op 方法不应抛异常。"""
        router = AudioRouter()
        router.set_mic_device("test")
        router.set_gains(mic=1.0, tts=1.0)
        router.inject_tts(b"")
        router.inject_tts_array(None)  # type: ignore[arg-type]
        router.inject_tts_from_wav(b"", 44100, 1)
        router.set_on_stats(None)
