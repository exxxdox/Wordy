#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""audio_router 模块单元测试。"""

from unittest.mock import call, patch

import pytest

from wordy.audio.router import AudioRouter


@pytest.fixture(autouse=True)
def _isolated_journal(tmp_path, monkeypatch):
    """恢复日志必须落在临时目录，别写进真实用户配置目录。"""
    import wordy.audio.router as router_module
    monkeypatch.setattr(router_module, "_journal_path", lambda: tmp_path / "journal.json")


def test_enable_refuses_to_overwrite_unrecovered_journal(tmp_path, monkeypatch):
    """上次恢复没消费完的日志是本机原始状态唯一副本，不能被新接管覆盖。"""
    import wordy.audio.router as router_module
    from wordy.audio.listen_policy import ListenPolicyState

    journal = tmp_path / "journal.json"
    monkeypatch.setattr(router_module, "_journal_path", lambda: journal)
    journal.write_text("{}", encoding="utf-8")
    router = AudioRouter(virtual_output="CABLE Input")

    with (
        patch.object(router, "get_output_device",
                     return_value={"name": "CABLE Input", "host_api_name": "Windows WASAPI"}),
        patch("wordy.audio.listen_policy.get_listen_policy",
              return_value=ListenPolicyState(enabled=False, output_id=None)),
        patch("wordy.audio.listen_policy.restore_listen_policy", return_value=False),
    ):
        assert router._enable_mic_listen("Mic") is False

    assert journal.exists()


def test_failed_takeover_keeps_pending_restore(tmp_path, monkeypatch):
    """写入恢复日志后即视为待恢复；设置与回滚都失败时 stop() 仍要能重试。"""
    import wordy.audio.router as router_module
    from wordy.audio.listen_policy import ListenPolicyState

    journal = tmp_path / "journal.json"
    monkeypatch.setattr(router_module, "_journal_path", lambda: journal)
    router = AudioRouter(virtual_output="CABLE Input")

    with (
        patch.object(router, "get_output_device",
                     return_value={"name": "CABLE Input", "host_api_name": "Windows WASAPI"}),
        patch("wordy.audio.listen_policy.get_listen_policy",
              return_value=ListenPolicyState(enabled=False, output_id=None)),
        patch("wordy.audio.listen_policy.set_listen_policy", return_value=False),
        patch("wordy.audio.listen_policy.restore_listen_policy", return_value=False),
    ):
        assert router._enable_mic_listen("Mic") is False

    assert router._listen_configured is True
    assert journal.exists()


def test_listen_recovery_journal_restores_and_clears(tmp_path, monkeypatch):
    import wordy.audio.router as router_module
    from wordy.audio.listen_policy import ListenPolicyState

    journal = tmp_path / "listen-policy.json"
    monkeypatch.setattr(router_module, "_journal_path", lambda: journal)
    original = ListenPolicyState(enabled=True, output_id="render-id")
    router_module._write_listen_journal("Mic", original)

    with patch("wordy.audio.listen_policy.restore_listen_policy", return_value=True) as restore:
        assert router_module._recover_listen_journal() is True

    restore.assert_called_once_with("Mic", original)
    assert not journal.exists()


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

    def test_listen_takeover_restores_original_state(self):
        from wordy.audio.listen_policy import ListenPolicyState

        router = AudioRouter(virtual_output="CABLE Input")
        original = ListenPolicyState(enabled=True, output_id="original-render-id")
        cable = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}

        with (
            patch.object(router, "get_output_device", return_value=cable),
            patch("wordy.audio.listen_policy.get_listen_policy", return_value=original),
            patch("wordy.audio.listen_policy.set_listen_policy", return_value=True),
        ):
            assert router._enable_mic_listen("Mic") is True

        with patch(
            "wordy.audio.listen_policy.restore_listen_policy",
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
        """新设备启用失败时保留旧选择，并向调用方报告失败。"""
        router = AudioRouter(virtual_output="CABLE Input")
        router._active = True
        router._mic_device = "Old Mic"
        router._listen_configured = True

        with (
            patch.object(router, "_disable_mic_listen", return_value=True) as disable,
            patch.object(router, "_enable_mic_listen", return_value=False) as enable,
        ):
            ok = router.set_mic_device("New Mic")

        assert ok is False
        disable.assert_called_once_with("Old Mic")
        assert enable.call_args_list == [call("New Mic"), call("Old Mic")]
        assert router._mic_device == "Old Mic"
        assert router.get_stats().listen_configured is False

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


def test_stop_keeps_original_state_until_restore_retry_succeeds():
    from wordy.audio.listen_policy import ListenPolicyState

    router = AudioRouter(virtual_output="CABLE Input")
    original = ListenPolicyState(enabled=False, output_id="original-output")
    router._active = True
    router._mic_device = "Mic"
    router._listen_configured = True
    router._listen_original_state = original
    router._listen_original_device = "Mic"
    with patch("wordy.audio.listen_policy.restore_listen_policy", side_effect=[False, True]) as restore:
        assert router.stop() is False
        assert router.is_running() is False
        assert router._listen_original_state is original
        assert router.stop() is True
    assert restore.call_count == 2
    assert router._listen_original_state is None


@pytest.mark.parametrize("operation", ["start", "set_virtual_output", "set_mic_device"])
def test_pending_restore_cannot_be_overwritten_by_new_configuration(operation):
    from wordy.audio.listen_policy import ListenPolicyState

    router = AudioRouter(virtual_output="CABLE Input")
    original = ListenPolicyState(enabled=True, output_id="original-output")
    router._mic_device = "Old Mic"
    router._listen_configured = True
    router._listen_original_state = original
    router._listen_original_device = "Old Mic"
    with (
        patch("wordy.audio.listen_policy.restore_listen_policy", return_value=False),
        patch.object(router, "_resolve_output_device", return_value=99),
        patch.object(router, "_enable_mic_listen") as enable,
    ):
        if operation == "start":
            result = router.start(mic_device="New Mic")
        elif operation == "set_virtual_output":
            result = router.set_virtual_output("New Output")
        else:
            result = router.set_mic_device("New Mic")
    assert result is False
    assert router._mic_device == "Old Mic"
    assert router.virtual_output == "CABLE Input"
    assert router._listen_original_state is original
    enable.assert_not_called()
