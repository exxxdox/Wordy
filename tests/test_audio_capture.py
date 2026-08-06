#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""audio_capture 模块单元测试。"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from wordy.audio.capture import AudioCapture


class TestAudioCapture:
    """AudioCapture 测试集。"""

    def test_init_defaults(self):
        cap = AudioCapture()
        assert cap.device_name is None
        assert cap.sample_rate == 48000
        assert cap.channels == 1
        assert cap.blocksize == 480
        assert cap.on_data is None

    def test_init_custom(self):
        cb = MagicMock()
        cap = AudioCapture(device_name="Test Mic", sample_rate=44100, channels=2, on_data=cb)
        assert cap.device_name == "Test Mic"
        assert cap.sample_rate == 44100
        assert cap.channels == 2
        assert cap.on_data is cb

    @patch("wordy.audio.capture.sd.query_devices")
    @patch("wordy.audio.capture.sd.query_hostapis")
    @patch("wordy.audio.capture.sd.default")
    def test_list_input_devices(self, mock_default, mock_hostapis, mock_query_devices):
        mock_query_devices.return_value = [
            {"name": "CABLE Output", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0, "default_samplerate": 48000.0},
            {"name": "麦克风", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0, "default_samplerate": 48000.0},
            {"name": "立体声混音", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0, "default_samplerate": 44100.0},
            {"name": "Line In", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 1, "default_samplerate": 48000.0},
        ]
        mock_hostapis.return_value = [{"name": "Windows WASAPI"}, {"name": "MME"}]
        mock_default.device = [1, None]

        devices = AudioCapture.list_input_devices()
        # 只保留 WASAPI，且排除 CABLE Output
        assert len(devices) == 2
        assert devices[0]["name"] == "麦克风"
        assert devices[0]["is_default"] is True
        assert devices[1]["name"] == "立体声混音"

    @patch("wordy.audio.capture.sd.query_devices")
    @patch("wordy.audio.capture.sd.query_hostapis")
    def test_find_device_index(self, mock_hostapis, mock_query_devices):
        mock_query_devices.return_value = [
            {"name": "CABLE Output", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0, "default_samplerate": 48000.0},
            {"name": "麦克风", "max_input_channels": 2, "max_output_channels": 0, "hostapi": 0, "default_samplerate": 48000.0},
        ]
        mock_hostapis.return_value = [{"name": "Windows WASAPI"}]

        idx = AudioCapture.find_device_index("麦克风")
        assert idx == 1

        idx_none = AudioCapture.find_device_index("不存在的设备")
        assert idx_none is None

    @patch("wordy.audio.capture.sd.RawInputStream")
    def test_start_stop(self, mock_stream_class):
        mock_stream = MagicMock()
        mock_stream_class.return_value = mock_stream

        cap = AudioCapture(device_name="麦克风")
        with patch.object(AudioCapture, "find_device_index", return_value=1):
            result = cap.start()

        assert result is True
        assert cap.is_running() is True
        mock_stream.start.assert_called_once()

        cap.stop()
        assert cap.is_running() is False
        mock_stream.stop.assert_called_once()
        mock_stream.close.assert_called_once()


    @patch("wordy.audio.capture.sd.RawInputStream")
    def test_callback_forwards_data(self, mock_stream_class):
        mock_stream = MagicMock()
        mock_stream_class.return_value = mock_stream

        cb = MagicMock()
        cap = AudioCapture(device_name="麦克风", on_data=cb)
        with patch.object(AudioCapture, "find_device_index", return_value=1):
            cap.start()

        # 模拟回调
        test_data = np.array([[100], [200], [300]], dtype=np.int16)
        cap._callback(test_data, 3, None, None)

        cb.assert_called_once()
        forwarded = cb.call_args[0][0]
        assert np.array_equal(forwarded, test_data)

        cap.stop()
