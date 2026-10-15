#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""SidetoneAudioPlayer 单元测试。"""

from __future__ import annotations

from io import BytesIO
from threading import Barrier, Event, Thread
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
        main.get_stream_config.return_value = {"format": 8, "rate": 48000}
        sidetone = MagicMock()
        sidetone.output_device_name = None
        sidetone.get_stream_config.return_value = {"format": 8, "rate": 48000}
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
        # 需要先 open_stream 创建异步 executor
        wrapper.open_stream(8, 1, 48000)  # paInt16
        data = b"\x00\x01\x02\x03"
        wrapper.write_stream(data)
        main.write_stream.assert_called_once_with(data)
        # 异步写入，需 close_stream 等待 executor 完成再断言
        wrapper.close_stream()
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
        wrapper.open_stream(8, 1, 48000)  # paInt16
        wrapper.write_stream(b"\x00\x01")
        # 异步写入，需等待 executor 完成
        wrapper.close_stream()
        assert wrapper._sidetone_stream_dead is True
        # 第二次写入不应再尝试返听
        sidetone.write_stream.reset_mock()
        wrapper.write_stream(b"\x02\x03")
        sidetone.write_stream.assert_not_called()

    def test_write_stream_disables_sidetone_when_single_slot_queue_is_full(self):
        """慢返听最多保留一个待写块，不能让延迟和内存持续增长。"""
        main, sidetone = self._make_players()
        writing = Event()
        release = Event()

        def block_write(_data):
            writing.set()
            release.wait()

        sidetone.write_stream.side_effect = block_write
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        wrapper.open_stream(8, 1, 48000)

        wrapper.write_stream(b"first")
        assert writing.wait(1)
        wrapper.write_stream(b"queued")
        wrapper.write_stream(b"overflow")

        assert wrapper._sidetone_stream_dead is True
        release.set()
        wrapper.close_stream()
        assert sidetone.write_stream.call_count <= 2

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

    def test_open_stream_resets_dead_flag(self):
        """open_stream 重置 dead 标记，新流开始时重新尝试返听。"""
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        wrapper._sidetone_stream_dead = True
        wrapper.open_stream(8, 1, 48000)  # paInt16
        assert wrapper._sidetone_stream_dead is False

    def test_close_stream_sidetone_failure_not_fatal(self):
        main, sidetone = self._make_players()
        sidetone.close_stream.side_effect = RuntimeError("cleanup failed")
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        wrapper.close_stream()  # 不应抛出
        main.close_stream.assert_called_once()
        sidetone.close_stream.assert_called_once()

    def test_close_stream_does_not_wait_forever_for_blocked_sidetone(self):
        main, sidetone = self._make_players()
        writing = Event()
        release = Event()
        sidetone.write_stream.side_effect = lambda _data: (writing.set(), release.wait())
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        wrapper.open_stream(8, 1, 48000)
        wrapper.write_stream(b"blocked")
        assert writing.wait(1)

        closing = Thread(target=wrapper.close_stream)
        closing.start()
        closing.join(0.5)
        try:
            assert not closing.is_alive()
            main.close_stream.assert_called_once()
        finally:
            release.set()
            closing.join(1)

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

    def test_play_wav_starts_main_and_sidetone_concurrently_and_waits_for_both(self):
        main, sidetone = self._make_players()
        started = Barrier(3)
        release = Event()

        def wait_for_other(*_args, **_kwargs):
            started.wait(timeout=1)
            release.wait()
            return True

        main.play_wav.side_effect = wait_for_other
        sidetone.play_wav.side_effect = wait_for_other
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)

        result = []
        playback = Thread(target=lambda: result.append(wrapper.play_wav("test.wav", device_index=3)))
        playback.start()
        started.wait(timeout=1)
        assert playback.is_alive()
        release.set()
        playback.join(1)

        assert result == [True]

    def test_play_wav_main_failure_still_returns_false(self):
        main, sidetone = self._make_players()
        main.play_wav.return_value = False
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        result = wrapper.play_wav("missing.wav")
        assert result is False
        sidetone.play_wav.assert_called_once()

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

    def test_default_output_does_not_play_duplicate_sidetone(self):
        main, sidetone = self._make_players()
        main.output_device_name = None
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        assert wrapper.open_stream(8, 1, 48000)
        wrapper.write_stream(b"\x00\x01")
        wrapper.close_stream()
        assert wrapper.play_wav("test.wav")
        sidetone.open_stream.assert_not_called()
        sidetone.write_stream.assert_not_called()
        sidetone.play_wav.assert_not_called()

    def test_sidetone_uses_actual_main_format_and_rate(self):
        main, sidetone = self._make_players()
        main.get_stream_config.return_value = {"format": 8, "rate": 44100}
        sidetone.get_stream_config.return_value = {"format": 8, "rate": 44100}
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        assert wrapper.open_stream(1, 1, 48000)
        sidetone.open_stream.assert_called_once_with(
            8, 1, 44100, device_index=None, frames_per_buffer=1024,
        )
        wrapper.close_stream()

    def test_incompatible_sidetone_never_receives_pcm(self):
        main, sidetone = self._make_players()
        sidetone.get_stream_config.return_value = {"format": 1, "rate": 44100}
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        assert wrapper.open_stream(8, 1, 48000)
        wrapper.write_stream(b"\x00\x01")
        sidetone.write_stream.assert_not_called()
        sidetone.close_stream.assert_called_once()
        main.write_stream.assert_called_once_with(b"\x00\x01")
        wrapper.close_stream()

    def test_disabling_sidetone_still_closes_open_stream(self):
        main, sidetone = self._make_players()
        wrapper = SidetoneAudioPlayer(main, sidetone)
        wrapper.set_sidetone_enabled(True)
        assert wrapper.open_stream(8, 1, 48000)
        wrapper.set_sidetone_enabled(False)
        wrapper.close_stream()
        sidetone.close_stream.assert_called_once()
def test_cancelled_concurrent_playback_returns_false_and_shares_event():
    from threading import Event

    main, sidetone = MagicMock(), MagicMock()
    main.output_device_name = "CABLE Input"
    cancelled = Event()

    def play_then_cancel(*args, **kwargs):
        cancelled.set()
        return True

    main.play_wav.side_effect = play_then_cancel
    wrapper = SidetoneAudioPlayer(main, sidetone)
    wrapper.set_sidetone_enabled(True)
    assert wrapper.play_wav("test.wav", cancel_event=cancelled) is False
    main.play_wav.assert_called_once_with("test.wav", device_index=None, cancel_event=cancelled)
    sidetone.play_wav.assert_called_once_with(
        "test.wav", device_index=None, cancel_event=cancelled
    )
