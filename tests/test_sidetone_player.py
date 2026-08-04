#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""SidetoneAudioPlayer 单元测试。"""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

from wordy.audio.sidetone import SidetoneAudioPlayer


class TestSidetoneAudioPlayer:
    """SidetoneAudioPlayer 包装器测试。"""

    @staticmethod
    def _make_players():
        """创建 mock 主播放器和返听播放器。"""
        main = MagicMock()
        main.output_device_name = "Test Device"
        main.output_device = {"name": "Test Device", "host_api_name": "Windows WASAPI"}
        sidetone = MagicMock()
        sidetone.output_device_name = None
        return main, sidetone

    # ── 构造与开关 ──────────────────────────────────────────────────

    def test_init_stores_players_disabled(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        assert wrapper._main is main
        assert wrapper._sidetone is sidetone
        assert wrapper.sidetone_enabled is False

    def test_set_sidetone_enabled(self):
        wrapper = SidetoneAudioPlayer(*self._make_players())
        wrapper.set_sidetone_enabled(True)
        assert wrapper.sidetone_enabled is True
        wrapper.set_sidetone_enabled(False)
        assert wrapper.sidetone_enabled is False

    # ── 属性代理 ────────────────────────────────────────────────────

    def test_output_device_name_delegates_to_main(self):
        main, sidetone = self._make_players()
        main.output_device_name = "Speakers"
        wrapper = SidetoneAudioPlayer(main, sidetone)
        assert wrapper.output_device_name == "Speakers"

    def test_output_device_name_setter_delegates_to_main(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.output_device_name = "New Device"
        assert main.output_device_name == "New Device"

    def test_output_device_delegates_to_main(self):
        main, sidetone = self._make_players()
        dev = {"name": "Test", "host_api_name": "WASAPI"}
        main.output_device = dev
        wrapper = SidetoneAudioPlayer(main, sidetone)
        assert wrapper.output_device is dev

    def test_output_device_setter_delegates_to_main(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        dev = {"name": "New", "host_api_name": "WASAPI"}
        wrapper.output_device = dev
        assert main.output_device == dev

    # ── open_stream ─────────────────────────────────────────────────

    def test_open_stream_disabled_opens_main_only(self):
        main, sidetone = self._make_players()
        main.open_stream.return_value = True
        wrapper = SidetoneAudioPlayer(main, sidetone)
        result = wrapper.open_stream(8, 1, 48000)
        assert result is True
        main.open_stream.assert_called_once_with(8, 1, 48000,
                                                  device_index=None, frames_per_buffer=1024)
        sidetone.open_stream.assert_not_called()

    def test_open_stream_enabled_opens_both(self):
        main, sidetone = self._make_players()
        main.open_stream.return_value = True
        sidetone.open_stream.return_value = True
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        result = wrapper.open_stream(8, 1, 48000)
        assert result is True
        main.open_stream.assert_called_once()
        sidetone.open_stream.assert_called_once_with(8, 1, 48000,
                                                      device_index=None, frames_per_buffer=1024)

    def test_open_stream_main_fails_returns_false(self):
        main, sidetone = self._make_players()
        main.open_stream.return_value = False
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        result = wrapper.open_stream(8, 1, 48000)
        assert result is False
        sidetone.open_stream.assert_not_called()

    def test_open_stream_sidetone_fails_main_ok_returns_true(self):
        main, sidetone = self._make_players()
        main.open_stream.return_value = True
        sidetone.open_stream.return_value = False
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        result = wrapper.open_stream(8, 1, 48000)
        assert result is True  # 主成功即返回 True
        assert wrapper._sidetone_stream_dead is True

    def test_open_stream_sidetone_raises_main_ok_returns_true(self):
        main, sidetone = self._make_players()
        main.open_stream.return_value = True
        sidetone.open_stream.side_effect = RuntimeError("device gone")
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        result = wrapper.open_stream(8, 1, 48000)
        assert result is True
        assert wrapper._sidetone_stream_dead is True

    # ── write_stream ────────────────────────────────────────────────

    def test_write_stream_disabled_writes_main_only(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        data = b"\x00\x01\x02\x03"
        wrapper.write_stream(data)
        main.write_stream.assert_called_once_with(data)
        sidetone.write_stream.assert_not_called()

    def test_write_stream_enabled_writes_both(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        data = b"\x00\x01\x02\x03"
        wrapper.write_stream(data)
        main.write_stream.assert_called_once_with(data)
        sidetone.write_stream.assert_called_once_with(data)

    def test_write_stream_skips_sidetone_when_dead(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        wrapper._sidetone_stream_dead = True
        wrapper.write_stream(b"\x00\x01")
        main.write_stream.assert_called_once()
        sidetone.write_stream.assert_not_called()

    def test_write_stream_sidetone_failure_sets_dead_flag(self):
        main, sidetone = self._make_players()
        sidetone.write_stream.side_effect = RuntimeError("stream broken")
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        wrapper.write_stream(b"\x00\x01")
        assert wrapper._sidetone_stream_dead is True
        # 第二次写入不应再尝试返听
        sidetone.write_stream.reset_mock()
        wrapper.write_stream(b"\x02\x03")
        sidetone.write_stream.assert_not_called()

    # ── close_stream ────────────────────────────────────────────────

    def test_close_stream_disabled_closes_main_only(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.close_stream()
        main.close_stream.assert_called_once()
        sidetone.close_stream.assert_not_called()

    def test_close_stream_enabled_closes_both(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        wrapper.close_stream()
        main.close_stream.assert_called_once()
        sidetone.close_stream.assert_called_once()

    def test_close_stream_resets_dead_flag(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper._sidetone_stream_dead = True
        wrapper.close_stream()
        assert wrapper._sidetone_stream_dead is False

    def test_close_stream_sidetone_failure_not_fatal(self):
        main, sidetone = self._make_players()
        sidetone.close_stream.side_effect = RuntimeError("cleanup failed")
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        wrapper.close_stream()  # 不应抛出
        main.close_stream.assert_called_once()
        sidetone.close_stream.assert_called_once()

    # ── play_wav ────────────────────────────────────────────────────

    def test_play_wav_disabled_calls_main_only(self):
        main, sidetone = self._make_players()
        main.play_wav.return_value = True
        wrapper = SidetoneAudioPlayer(main, sidetone)
        result = wrapper.play_wav("test.wav")
        assert result is True
        main.play_wav.assert_called_once_with("test.wav", device_index=None)
        sidetone.play_wav.assert_not_called()

    def test_play_wav_enabled_calls_both(self):
        main, sidetone = self._make_players()
        main.play_wav.return_value = True
        sidetone.play_wav.return_value = True
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        result = wrapper.play_wav("test.wav", device_index=3)
        assert result is True
        main.play_wav.assert_called_once_with("test.wav", device_index=3)
        sidetone.play_wav.assert_called_once_with("test.wav", device_index=None)

    def test_play_wav_main_fails_skips_sidetone(self):
        main, sidetone = self._make_players()
        main.play_wav.return_value = False
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        result = wrapper.play_wav("missing.wav")
        assert result is False
        sidetone.play_wav.assert_not_called()

    def test_play_wav_bytesio_resets_position(self):
        main, sidetone = self._make_players()
        main.play_wav.return_value = True
        sidetone.play_wav.return_value = True
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)

        bio = BytesIO(b"RIFF....WAVE...")
        bio.seek(10)  # 模拟已消费
        result = wrapper.play_wav(bio)
        assert result is True
        # 验证 sidetone 调用时 BytesIO 已被 seek(0)
        sidetone.play_wav.assert_called_once()
        called_arg = sidetone.play_wav.call_args[0][0]
        assert called_arg.tell() == 0

    def test_play_wav_sidetone_failure_not_fatal(self):
        main, sidetone = self._make_players()
        main.play_wav.return_value = True
        sidetone.play_wav.side_effect = RuntimeError("playback failed")
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        result = wrapper.play_wav("test.wav")
        assert result is True  # 主成功，返听失败不影响返回值

    # ── 其他代理方法 ────────────────────────────────────────────────

    def test_get_stream_config_delegates_to_main(self):
        main, sidetone = self._make_players()
        main.get_stream_config.return_value = {"format": 8, "rate": 48000}
        wrapper = SidetoneAudioPlayer(main, sidetone)
        assert wrapper.get_stream_config() == {"format": 8, "rate": 48000}

    def test_query_output_device_default_rate_delegates_to_main(self):
        main, sidetone = self._make_players()
        main.query_output_device_default_rate.return_value = 44100
        wrapper = SidetoneAudioPlayer(main, sidetone)
        assert wrapper.query_output_device_default_rate() == 44100

    def test_set_output_device_affects_main_only(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_output_device({"name": "CABLE Input", "host_api_name": "WASAPI"})
        main.set_output_device.assert_called_once_with(
            {"name": "CABLE Input", "host_api_name": "WASAPI"})

    def test_set_output_device_name_affects_main_only(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_output_device_name("Speakers")
        main.set_output_device_name.assert_called_once_with("Speakers")
