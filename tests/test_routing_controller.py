"""路由控制器测试。"""

from unittest.mock import MagicMock, patch

import pytest

from wordy.routing_controller import RoutingController


@pytest.fixture
def mock_settings() -> MagicMock:
    s = MagicMock()
    s.audio_routing_enabled = False
    s.virtual_output_device = "CABLE Input"
    s.mic_input_device = "Test Mic"
    return s


class TestRoutingControllerInit:
    """构造时行为。"""

    @pytest.mark.parametrize("enabled", [False, True])
    def test_startup_uses_windows_listening_instead_of_saved_switch(
        self, mock_settings, enabled,
    ):
        from wordy.audio.listen_policy import ListenPolicyState

        mock_settings.audio_routing_enabled = not enabled
        with (
            patch("wordy.routing_controller.get_listen_policy",
                  return_value=ListenPolicyState(enabled=enabled, output_id="windows-target")),
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as create,
        ):
            create.return_value.start.return_value = True
            create.return_value.is_running.return_value = True
            controller = RoutingController(mock_settings)

        assert controller.is_running is enabled
        if enabled:
            create.return_value.start.assert_called_once_with(
                mic_device="Test Mic", configure_listen=False,
            )
        else:
            create.assert_not_called()

    def test_init_with_routing_disabled_does_not_create_router(
        self, mock_settings,
    ):
        mock_settings.audio_routing_enabled = False
        ctrl = RoutingController(mock_settings)
        assert ctrl.is_running is False

    def test_init_with_routing_enabled_no_vb_cable_does_not_create_router(
        self, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=False):
            ctrl = RoutingController(mock_settings)
        assert ctrl.is_running is False
        assert mock_settings.audio_routing_enabled is False

    def test_init_with_routing_enabled_and_vb_cable_creates_router(
        self, mock_settings,
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

            ctrl = RoutingController(mock_settings)
            assert ctrl.is_running is True
            router.start.assert_called_once_with(mic_device="Test Mic", configure_listen=False)

    def test_init_router_start_failure_sets_not_running(
        self, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter") as MockRouter,
        ):
            MockRouter.return_value.start.return_value = False
            ctrl = RoutingController(mock_settings)
            assert ctrl.is_running is False
            assert mock_settings.audio_routing_enabled is False

    def test_init_router_exception_sets_not_running(
        self, mock_settings,
    ):
        mock_settings.audio_routing_enabled = True
        with (
            patch("wordy.routing_controller.VBCableDriverManager.is_installed", return_value=True),
            patch("wordy.routing_controller.AudioRouter", side_effect=RuntimeError("boom")),
        ):
            ctrl = RoutingController(mock_settings)
            assert ctrl.is_running is False


class TestRoutingControllerStop:
    """stop() 行为。"""

    def test_explicit_disable_closes_windows_listening_without_wordy_router(
        self, mock_settings,
    ):
        with (
            patch("wordy.routing_controller.get_listen_policy", return_value=None),
            patch("wordy.routing_controller.set_listen_policy", return_value=True) as write,
        ):
            controller = RoutingController(mock_settings)
            assert controller.apply_config({"audio_routing_enabled": False})

        write.assert_called_once_with("Test Mic", "", enabled=False)

    def test_stop_when_not_running_is_noop(self, mock_settings):
        ctrl = RoutingController(mock_settings)
        ctrl.stop()
        assert ctrl.is_running is False

    def test_stop_idempotent(self, mock_settings):
        """重复 stop 不抛异常。"""
        ctrl = RoutingController(mock_settings)
        ctrl.stop()
        ctrl.stop()
        assert ctrl.is_running is False


class TestRoutingControllerApplyConfig:
    """apply_config 配置变更。"""

    def test_apply_config_disable_stops_routing(self, mock_settings):
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

            ctrl = RoutingController(mock_settings)
            assert ctrl.is_running is True

            ctrl.apply_config({"audio_routing_enabled": False})
            assert ctrl.is_running is False

    def test_apply_config_enable_when_no_router_creates_one(
        self, mock_settings,
    ):
        ctrl = RoutingController(mock_settings)
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

    def test_apply_config_enable_reports_router_start_failure(
        self, mock_settings,
    ):
        ctrl = RoutingController(mock_settings)

        with patch(
            "wordy.routing_controller.VBCableDriverManager.is_installed",
            return_value=False,
        ):
            assert ctrl.apply_config({"audio_routing_enabled": True}) is False

        assert ctrl.is_running is False

    def test_apply_config_mic_device_change_when_running(
        self, mock_settings,
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

            ctrl = RoutingController(mock_settings)
            ctrl.apply_config({"mic_input_device": "New Mic"})
            router.set_mic_device.assert_called_with("New Mic")

    def test_apply_config_virtual_device_change_when_running(
        self, mock_settings,
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

            ctrl = RoutingController(mock_settings)
            ctrl.apply_config({"virtual_output_device": "VB-Audio Point"})
            router.set_virtual_output.assert_called_with("VB-Audio Point")


class TestRoutingControllerProperties:
    """属性访问。"""

    def test_mic_listen_state_none_without_device(self, mock_settings):
        mock_settings.mic_input_device = None
        ctrl = RoutingController(mock_settings)
        assert ctrl.mic_listen_state is None

    def test_mic_listen_state_reads_windows_state(
        self, mock_settings,
    ):
        from wordy.audio.listen_policy import ListenPolicyState

        state = ListenPolicyState(enabled=True, output_id="cable-id")
        ctrl = RoutingController(mock_settings)
        with patch("wordy.routing_controller.get_listen_policy", return_value=state) as query:
            assert ctrl.mic_listen_state is state
        query.assert_called_once_with("Test Mic")


def test_failed_explicit_disable_keeps_route_active_until_retry(mock_settings):
    from wordy.audio.router import AudioRouter

    ctrl = RoutingController(mock_settings)
    router = AudioRouter(virtual_output="CABLE Input")
    router._active = True
    router._mic_device = "Mic"
    router._listen_configured = True
    ctrl._router = router
    with patch("wordy.audio.listen_policy.set_listen_policy", side_effect=[False, True]) as write:
        assert ctrl.apply_config({"audio_routing_enabled": False}) is False
        assert ctrl._router is router
        assert ctrl.is_running is True
        assert ctrl.apply_config({"audio_routing_enabled": False}) is True
    assert write.call_count == 2
    assert write.call_args.args[0] == "Mic"
    assert write.call_args.kwargs["enabled"] is False
    assert ctrl._router is None


def test_stop_for_shutdown_preserves_system_listening(mock_settings):
    ctrl = RoutingController(mock_settings)
    router = MagicMock()
    router.is_running.return_value = True
    router.stop.return_value = True
    ctrl._router = router

    assert ctrl.stop(preserve_listen=True) is True

    router.stop.assert_called_once_with(preserve_listen=True)


def test_virtual_output_change_start_failure_preserves_windows_listening(mock_settings):
    from wordy.audio.router import AudioRouter

    ctrl = RoutingController(mock_settings)
    router = AudioRouter(virtual_output="CABLE Input")
    router._active = True
    router._mic_device = "Mic"
    router._listen_configured = True
    ctrl._router = router
    with (
        patch.object(router, "get_output_device", return_value=None),
        patch("wordy.audio.listen_policy.set_listen_policy") as write,
    ):
        assert ctrl.apply_config({"virtual_output_device": "New Output"}) is False
    write.assert_not_called()
    assert ctrl.is_running is False
