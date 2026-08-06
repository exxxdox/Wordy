"""路由控制器测试。"""

from unittest.mock import MagicMock, patch

import pytest

from wordy.routing_controller import RoutingController


@pytest.fixture
def mock_player() -> MagicMock:
    p = MagicMock()
    p.output_device = None
    return p


@pytest.fixture
def mock_settings() -> MagicMock:
    s = MagicMock()
    s.audio_routing_enabled = False
    s.virtual_output_device = "CABLE Input"
    s.mic_input_device = "Test Mic"
    return s


class TestRoutingControllerInit:
    """构造时行为。"""

    def test_init_with_routing_disabled_does_not_create_router(
        self, mock_player, mock_settings,
    ):
        mock_settings.audio_routing_enabled = False
        ctrl = RoutingController(mock_settings, mock_player)
        assert ctrl.is_running is False

    def test_init_with_routing_enabled_no_vb_cable_does_not_create_router(
        self, mock_player, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=False):
            ctrl = RoutingController(mock_settings, mock_player)
        assert ctrl.is_running is False

    def test_init_with_routing_enabled_and_vb_cable_creates_router(
        self, mock_player, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            router = MockRouter.return_value
            router.start.return_value = True
            router.is_running.return_value = True
            router.get_output_device.return_value = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}
            router.get_stats.return_value.listen_configured = True

            ctrl = RoutingController(mock_settings, mock_player)
            assert ctrl.is_running is True

    def test_init_router_start_failure_sets_not_running(
        self, mock_player, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            MockRouter.return_value.start.return_value = False
            ctrl = RoutingController(mock_settings, mock_player)
            assert ctrl.is_running is False

    def test_init_router_exception_sets_not_running(
        self, mock_player, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter", side_effect=RuntimeError("boom")),
        ):
            ctrl = RoutingController(mock_settings, mock_player)
            assert ctrl.is_running is False


class TestRoutingControllerStop:
    """stop() 行为。"""

    def test_stop_when_not_running_is_noop(self, mock_player, mock_settings):
        ctrl = RoutingController(mock_settings, mock_player)
        ctrl.stop()
        assert ctrl.is_running is False

    def test_stop_restores_saved_output_device(self, mock_player, mock_settings):
        mock_settings.audio_routing_enabled = True
        mock_player.output_device = {"name": "Speakers", "host_api_name": "MME"}
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            router = MockRouter.return_value
            router.start.return_value = True
            router.is_running.return_value = True
            router.get_output_device.return_value = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}
            router.get_stats.return_value.listen_configured = False

            ctrl = RoutingController(mock_settings, mock_player)
            ctrl.stop()

        # 恢复后应调用 set_output_device 恢复原始设备
        assert mock_player.set_output_device.call_count >= 2
        last_call_args = mock_player.set_output_device.call_args_list[-1]
        assert last_call_args[0][0] == {"name": "Speakers", "host_api_name": "MME"}

    def test_stop_idempotent(self, mock_player, mock_settings):
        """重复 stop 不抛异常。"""
        ctrl = RoutingController(mock_settings, mock_player)
        ctrl.stop()
        ctrl.stop()
        assert ctrl.is_running is False


class TestRoutingControllerApplyConfig:
    """apply_config 配置变更。"""

    def test_apply_config_disable_stops_routing(self, mock_player, mock_settings):
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            router = MockRouter.return_value
            router.start.return_value = True
            router.is_running.return_value = True
            router.get_output_device.return_value = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}
            router.get_stats.return_value.listen_configured = False

            ctrl = RoutingController(mock_settings, mock_player)
            assert ctrl.is_running is True

            ctrl.apply_config({"audio_routing_enabled": False})
            assert ctrl.is_running is False

    def test_apply_config_enable_when_no_router_creates_one(
        self, mock_player, mock_settings,
    ):
        ctrl = RoutingController(mock_settings, mock_player)
        assert ctrl.is_running is False

        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            router = MockRouter.return_value
            router.start.return_value = True
            router.is_running.return_value = True
            router.get_output_device.return_value = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}
            router.get_stats.return_value.listen_configured = False

            ctrl.apply_config({
                "audio_routing_enabled": True,
                "virtual_output_device": "CABLE Input",
                "mic_input_device": "Test Mic",
            })
            assert ctrl.is_running is True

    def test_apply_config_mic_device_change_when_running(
        self, mock_player, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            router = MockRouter.return_value
            router.start.return_value = True
            router.get_output_device.return_value = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}
            router.get_stats.return_value.listen_configured = False
            router.set_mic_device.return_value = True

            ctrl = RoutingController(mock_settings, mock_player)
            ctrl.apply_config({"mic_input_device": "New Mic"})
            router.set_mic_device.assert_called_with("New Mic")

    def test_apply_config_virtual_device_change_when_running(
        self, mock_player, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            router = MockRouter.return_value
            router.start.return_value = True
            router.get_output_device.return_value = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}
            router.get_stats.return_value.listen_configured = False

            ctrl = RoutingController(mock_settings, mock_player)
            ctrl.apply_config({"virtual_output_device": "VB-Audio Point"})
            router.set_virtual_output.assert_called_with("VB-Audio Point")


class TestRoutingControllerOutputDevice:
    """on_output_device_change 输出设备变更。"""

    def test_change_when_routing_saves_only(self, mock_player, mock_settings):
        """路由运行时只保存设备，不修改当前输出。"""
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            router = MockRouter.return_value
            router.start.return_value = True
            router.is_running.return_value = True
            router.get_output_device.return_value = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}
            router.get_stats.return_value.listen_configured = False

            ctrl = RoutingController(mock_settings, mock_player)
            mock_player.set_output_device.reset_mock()

            new_device = {"name": "New Speakers", "host_api_name": "MME"}
            ctrl.on_output_device_change(new_device)
            mock_player.set_output_device.assert_not_called()

    def test_change_when_not_routing_applies_directly(self, mock_player, mock_settings):
        """路由未运行时直接应用设备到 player。"""
        ctrl = RoutingController(mock_settings, mock_player)
        new_device = {"name": "Speakers", "host_api_name": "MME"}
        ctrl.on_output_device_change(new_device)
        mock_player.set_output_device.assert_called_with(new_device)

    def test_change_none_when_not_routing_sets_default(self, mock_player, mock_settings):
        ctrl = RoutingController(mock_settings, mock_player)
        ctrl.on_output_device_change(None)
        mock_player.set_output_device.assert_called_with(None)


class TestRoutingControllerProperties:
    """属性访问。"""

    def test_is_mic_listen_configured_when_no_router(self, mock_player, mock_settings):
        ctrl = RoutingController(mock_settings, mock_player)
        assert ctrl.is_mic_listen_configured is False

    def test_is_mic_listen_configured_when_configured(
        self, mock_player, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            router = MockRouter.return_value
            router.start.return_value = True
            router.get_output_device.return_value = {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}
            router.get_stats.return_value.listen_configured = True

            ctrl = RoutingController(mock_settings, mock_player)
            assert ctrl.is_mic_listen_configured is True

    def test_output_device_changed_callback_invoked(self, mock_player, mock_settings):
        """输出设备变更时回调被调用。"""
        callback = MagicMock()
        mock_settings.audio_routing_enabled = False
        ctrl = RoutingController(mock_settings, mock_player, on_output_device_changed=callback)
        ctrl.on_output_device_change({"name": "Speakers", "host_api_name": "MME"})
        callback.assert_called_once()
