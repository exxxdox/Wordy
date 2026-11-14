#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""audio_router 模块单元测试。"""

from unittest.mock import call, patch

import pytest

from wordy.audio.router import AudioRouter


class TestAudioRouter:
    """AudioRouter 测试集。"""

    def test_init(self):
        router = AudioRouter(virtual_output="CABLE Input")
        assert router.is_running() is False

    @patch.object(AudioRouter, "get_output_device")
    @patch("wordy.audio.router.VBCableDriverManager.get_virtual_output_index")
    def test_start_stop(self, mock_vb_idx, mock_output_device):
        mock_vb_idx.return_value = 99
        mock_output_device.return_value = {
            "name": "CABLE Input",
            "host_api_name": "Windows WASAPI",
        }
        router = AudioRouter(virtual_output="CABLE Input")
        result = router.start()
        assert result is True
        assert router.is_running() is True

        router.stop()
        assert router.is_running() is False

    @pytest.mark.parametrize("enabled", [False, True])
    def test_start_preserves_current_windows_listen(self, enabled, tmp_path, monkeypatch):
        from wordy.audio.listen_policy import ListenPolicyState

        recovery_file = tmp_path / "listen_policy_recovery.json"
        recovery_file.write_text('{"device":"Mic","enabled":false}', encoding="utf-8")
        monkeypatch.setattr("wordy.audio.router.config_directory", lambda: tmp_path, raising=False)

        with (
            patch.object(AudioRouter, "get_output_device", return_value={
                "name": "CABLE Input", "host_api_name": "Windows WASAPI",
            }),
            patch.object(AudioRouter, "_resolve_output_device", return_value=99),
            patch("wordy.audio.listen_policy.get_listen_policy",
                  return_value=ListenPolicyState(enabled=enabled, output_id="existing-target")),
            patch("wordy.audio.listen_policy.restore_listen_policy") as restore,
            patch("wordy.audio.listen_policy.set_listen_policy") as write,
        ):
            router = AudioRouter(virtual_output="CABLE Input")
            assert router.start(mic_device="Mic", configure_listen=False) is True
            assert router.stop(preserve_listen=True) is True

        restore.assert_not_called()
        write.assert_not_called()

    def test_stop_explicitly_disables_mic_regardless_of_initial_state(self):
        router = AudioRouter(virtual_output="CABLE Input")
        router._active = True
        router._mic_device = "Mic"

        with (
            patch("wordy.audio.listen_policy.set_listen_policy", return_value=True) as write,
            patch("wordy.audio.listen_policy.restore_listen_policy") as restore,
        ):
            assert router.stop() is True

        write.assert_called_once_with("Mic", "", enabled=False)
        restore.assert_not_called()
        assert router.is_running() is False

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

    def test_switch_mic_aborts_when_disabling_old_mic_fails(self):
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
        from wordy.audio.listen_policy import ListenPolicyState

        router = AudioRouter(virtual_output="CABLE Input")
        router._active = True
        router._mic_device = "Old Mic"
        current = ListenPolicyState(enabled=True, output_id="current-target")

        with (
            patch.object(router, "_disable_mic_listen", return_value=True) as disable,
            patch.object(router, "_enable_mic_listen", return_value=False) as enable,
            patch("wordy.audio.listen_policy.get_listen_policy", return_value=current),
            patch("wordy.audio.listen_policy.restore_listen_policy") as restore,
        ):
            assert router.set_mic_device("New Mic") is False

        disable.assert_called_once_with("Old Mic")
        enable.assert_called_once_with("New Mic")
        restore.assert_called_once_with("Old Mic", current)
        assert router._mic_device == "Old Mic"

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

    @patch("wordy.audio.router.sd.query_hostapis")
    @patch("wordy.audio.router.sd.query_devices")
    @patch("wordy.audio.router.VBCableDriverManager.get_status")
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

    @patch("wordy.audio.router.VBCableDriverManager.get_virtual_output_index")
    def test_start_no_virtual_device(self, mock_vb_idx):
        mock_vb_idx.return_value = None
        router = AudioRouter(virtual_output="__nonexistent__")
        result = router.start()
        assert result is False
        assert router.is_running() is False

    def test_start_requires_wasapi_output_before_enabling_mic_listen(self):
        router = AudioRouter(virtual_output="CABLE Input")

        with (
            patch.object(router, "get_output_device", return_value=None),
            patch.object(router, "_resolve_output_device", return_value=99),
            patch.object(router, "_enable_mic_listen") as enable,
        ):
            result = router.start(mic_device="Mic")

        assert result is False
        assert router.is_running() is False
        enable.assert_not_called()

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

    @patch.object(AudioRouter, "get_output_device")
    @patch("wordy.audio.router.VBCableDriverManager.get_virtual_output_index")
    def test_start_without_mic_skips_listen(self, mock_vb_idx, mock_output_device):
        """mic_device=None 时 start 不配置侦听——用户需手动选择麦克风。"""
        mock_vb_idx.return_value = 99
        mock_output_device.return_value = {
            "name": "CABLE Input",
            "host_api_name": "Windows WASAPI",
        }
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

    def test_set_mic_device_with_string_does_not_crash(self):
        """set_mic_device 应能接受字符串设备名不抛异常。"""
        router = AudioRouter()
        router.set_mic_device("test")
        assert router._mic_device == "test"


def test_stop_failure_keeps_route_running_for_retry():
    router = AudioRouter(virtual_output="CABLE Input")
    router._active = True
    router._mic_device = "Mic"

    with patch("wordy.audio.listen_policy.set_listen_policy", side_effect=[False, True]) as write:
        assert router.stop() is False
        assert router.is_running() is True
        assert router.stop() is True

    assert write.call_args_list == [call("Mic", "", enabled=False)] * 2
    assert router.is_running() is False
