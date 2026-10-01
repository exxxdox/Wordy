"""输入设备枚举测试；麦克风侦听由 Windows 管理，无需捕获生命周期。"""

from unittest.mock import patch

from wordy.audio.capture import list_input_devices


@patch("wordy.audio.capture.sd.query_devices")
@patch("wordy.audio.capture.sd.query_hostapis")
@patch("wordy.audio.capture.sd.default")
def test_list_input_devices(mock_default, mock_hostapis, mock_query_devices):
    mock_query_devices.return_value = [
        {"name": "CABLE Output", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0, "default_samplerate": 48000.0},
        {"name": "麦克风", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0, "default_samplerate": 48000.0},
        {"name": "立体声混音", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0, "default_samplerate": 44100.0},
        {"name": "Line In", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 1, "default_samplerate": 48000.0},
    ]
    mock_hostapis.return_value = [{"name": "Windows WASAPI"}, {"name": "MME"}]
    mock_default.device = [1, None]

    devices = list_input_devices()
    # 只保留 WASAPI，且排除 CABLE Output
    assert len(devices) == 2
    assert devices[0]["name"] == "麦克风"
    assert devices[0]["is_default"] is True
    assert devices[1]["name"] == "立体声混音"


@patch("wordy.audio.capture.sd.query_devices", side_effect=RuntimeError("unavailable"))
def test_list_input_devices_returns_empty_on_query_failure(mock_query_devices):
    assert list_input_devices() == []
