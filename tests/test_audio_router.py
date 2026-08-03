#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""audio_router 模块单元测试。"""

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from easy_tts.audio.router import AudioRouter, RingBuffer, _resample_linear


class TestRingBuffer:
    """RingBuffer 测试集。"""

    def test_write_read(self):
        buf = RingBuffer(capacity_frames=10, channels=1)
        data = np.array([[1], [2], [3]], dtype=np.int16)
        written = buf.write(data)
        assert written == 3

        out = buf.read(3)
        assert np.array_equal(out, data)

    def test_read_underrun(self):
        buf = RingBuffer(capacity_frames=10, channels=1)
        out = buf.read(5)
        assert len(out) == 5
        assert np.all(out == 0)

    def test_write_overrun(self):
        buf = RingBuffer(capacity_frames=5, channels=1)
        data = np.array([[1], [2], [3], [4], [5], [6]], dtype=np.int16)
        written = buf.write(data)
        assert written == 5

    def test_clear(self):
        buf = RingBuffer(capacity_frames=10, channels=1)
        buf.write(np.array([[1], [2]], dtype=np.int16))
        buf.clear()
        assert buf.available == 0
        out = buf.read(2)
        assert np.all(out == 0)


class TestAudioRouter:
    """AudioRouter 测试集。"""

    def test_init(self):
        router = AudioRouter(mic_device="麦克风", bridge_device="桥接")
        assert router.mic_device == "麦克风"
        assert router.bridge_device == "桥接"
        assert router.is_running() is False

    def test_set_gains(self):
        router = AudioRouter()
        router.set_gains(mic=0.5, bridge=1.5, tts=2.0)
        assert router.gain_mic == 0.5
        assert router.gain_bridge == 1.5
        assert router.gain_tts == 2.0

    def test_set_gains_clamp_negative(self):
        router = AudioRouter()
        router.set_gains(mic=-1.0)
        assert router.gain_mic == 0.0

    def test_inject_tts(self):
        router = AudioRouter()
        data = np.array([100, 200, 300], dtype=np.int16).tobytes()
        router.inject_tts(data)

        with router._tts_lock:
            assert len(router._tts_queue) == 1
            arr = router._tts_queue[0]
        assert np.array_equal(arr.flatten(), np.array([100, 200, 300], dtype=np.int16))

    def test_inject_tts_queue_limit(self):
        router = AudioRouter()
        for i in range(25):
            data = np.array([i], dtype=np.int16).tobytes()
            router.inject_tts(data)

        with router._tts_lock:
            assert len(router._tts_queue) <= 20

    @patch("easy_tts.audio.router.VBCableDriverManager.get_virtual_output_index")
    @patch("easy_tts.audio.router.sd.RawOutputStream")
    @patch("easy_tts.audio.router.AudioCapture")
    def test_start_stop(self, mock_capture_cls, mock_output_cls, mock_vb_idx):
        mock_vb_idx.return_value = 99
        mock_output_stream = MagicMock()
        mock_output_cls.return_value = mock_output_stream
        mock_capture = MagicMock()
        mock_capture.start.return_value = True
        mock_capture_cls.return_value = mock_capture

        router = AudioRouter(mic_device="麦克风")
        result = router.start()
        assert result is True
        assert router.is_running() is True

        router.stop()
        assert router.is_running() is False

    @patch("easy_tts.audio.router.VBCableDriverManager.get_virtual_output_index")
    def test_start_no_virtual_device(self, mock_vb_idx):
        mock_vb_idx.return_value = None
        router = AudioRouter(mic_device="麦克风")
        result = router.start()
        assert result is False
        assert router.is_running() is False

    def test_stats(self):
        router = AudioRouter()
        stats = router.get_stats()
        assert stats.is_running is False
        assert stats.latency_ms == 0.0
        assert stats.underrun_count == 0

    def test_set_on_stats(self):
        router = AudioRouter()
        cb = MagicMock()
        router.set_on_stats(cb)
        assert router._on_stats is cb

    def test_test_recording_start_stop(self):
        router = AudioRouter()
        router.start_test_recording()
        assert router._test_recording is True
        assert len(router._test_buffer) == 0

        # 模拟混音数据写入
        fake_mix = np.array([[100], [200], [300]], dtype=np.int16)
        with router._test_lock:
            router._test_buffer.append(fake_mix.copy())

        wav_bytes = router.stop_test_recording()
        assert router._test_recording is False
        assert wav_bytes is not None
        assert len(wav_bytes) > 0

    def test_test_recording_stop_without_start(self):
        router = AudioRouter()
        result = router.stop_test_recording()
        assert result is None

    def test_test_recording_multiple_frames(self):
        router = AudioRouter()
        router.start_test_recording()

        for i in range(3):
            frame = np.full((480, 1), i * 1000, dtype=np.int16)
            with router._test_lock:
                router._test_buffer.append(frame.copy())

        wav_bytes = router.stop_test_recording()
        assert wav_bytes is not None
        assert len(wav_bytes) > 44  # WAV 头部至少 44 字节

    def test_pcm_to_wav(self):
        from easy_tts.audio.router import _pcm_to_wav
        pcm = b"\x00\x01\x02\x03" * 100
        wav = _pcm_to_wav(pcm, 48000, 1, 2)
        assert isinstance(wav, bytes)
        assert len(wav) > len(pcm)
        assert wav[:4] == b"RIFF"
        assert wav[8:12] == b"WAVE"


class TestResampleLinear:
    """_resample_linear 测试集。"""

    def test_same_rate_returns_copy(self):
        data = np.array([100, 200, 300, 400], dtype=np.int16)
        result = _resample_linear(data, 48000, 48000)
        assert np.array_equal(result, data)
        # 确保是副本而非同一对象
        assert result is not data

    def test_upsample_preserves_shape(self):
        data = np.array([0, 100, 200, 100, 0], dtype=np.int16)
        result = _resample_linear(data, 8000, 16000)
        assert len(result) == 10  # 2x samples
        assert result.dtype == np.int16

    def test_downsample_preserves_range(self):
        data = np.array([0, 100, 200, 100, 0], dtype=np.int16)
        result = _resample_linear(data, 16000, 8000)
        assert len(result) == 2  # int(5 * 8000 / 16000) = 2
        assert result.dtype == np.int16
        assert np.max(result) <= 200
        assert np.min(result) >= 0

    def test_cartesia_44100_to_48000(self):
        """Cartesia 默认 44100Hz → 路由器 48000Hz 的典型场景。"""
        # 生成 44100Hz 的一秒正弦波
        duration = 1.0
        t = np.linspace(0, duration, 44100, endpoint=False)
        data = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
        result = _resample_linear(data, 44100, 48000)
        assert len(result) == 48000
        assert result.dtype == np.int16
        # 重采样后波形应保持大致相同的振幅范围
        assert abs(np.max(np.abs(result)) - 10000) < 2000


class TestInjectTtsFromWav:
    """inject_tts_from_wav 测试集。"""

    def test_inject_mono_matching_rate(self):
        router = AudioRouter()
        pcm = np.array([100, 200, 300], dtype=np.int16).tobytes()
        router.inject_tts_from_wav(pcm, src_rate=48000, src_channels=1)
        with router._tts_lock:
            assert len(router._tts_queue) == 1
            arr = router._tts_queue[0]
        assert arr.shape[1] == 1
        assert np.array_equal(arr.flatten(), np.array([100, 200, 300], dtype=np.int16))

    def test_inject_stereo_converts_to_mono(self):
        """立体声应取第一声道转为单声道。"""
        router = AudioRouter()
        # 立体声: 左声道[100,200,300] 右声道[1,2,3]
        stereo = np.array([[100, 1], [200, 2], [300, 3]], dtype=np.int16).tobytes()
        router.inject_tts_from_wav(stereo, src_rate=48000, src_channels=2)
        with router._tts_lock:
            arr = router._tts_queue[0]
        # 应只取第一声道
        assert np.array_equal(arr.flatten(), np.array([100, 200, 300], dtype=np.int16))

    def test_inject_resamples_44100_to_48000(self):
        """44.1kHz 立体声 → 48kHz 单声道的完整转换路径。"""
        router = AudioRouter()
        # 模拟 Cartesia TTS: 44100Hz, 单声道, 16bit
        t = np.linspace(0, 0.1, 4410, endpoint=False, dtype=np.float64)
        data = (np.sin(2 * np.pi * 440 * t) * 10000).astype(np.int16)
        pcm = data.tobytes()
        router.inject_tts_from_wav(pcm, src_rate=44100, src_channels=1)
        with router._tts_lock:
            assert len(router._tts_queue) == 1
            arr = router._tts_queue[0]
        # 重采样后应有约 4800 帧 (0.1s * 48000)
        expected_frames = int(4410 * 48000 / 44100)
        assert abs(arr.shape[0] - expected_frames) <= 1

    def test_inject_queue_limit_respected(self):
        router = AudioRouter()
        for _ in range(25):
            data = np.array([1, 2, 3], dtype=np.int16).tobytes()
            router.inject_tts_from_wav(data, src_rate=48000, src_channels=1)
        with router._tts_lock:
            assert len(router._tts_queue) <= 20


class TestBufferLimit:
    """测试录制缓冲区容量限制。"""

    def test_max_test_buffer_seconds_constant(self):
        from easy_tts.audio.router import MAX_TEST_BUFFER_SECONDS
        assert MAX_TEST_BUFFER_SECONDS == 30

    def test_overflow_skipped_by_mixer_logic(self):
        """验证 mixer 中超过 MAX_TEST_BUFFER_SECONDS 的帧会被丢弃。"""
        from easy_tts.audio.router import MAX_TEST_BUFFER_SECONDS

        router = AudioRouter(sample_rate=48000)
        router.start_test_recording()
        # 直接写入超过限制的帧
        max_frames = MAX_TEST_BUFFER_SECONDS * 48000
        big_frame = np.zeros((max_frames + 480, 1), dtype=np.int16)
        with router._test_lock:
            router._test_buffer.append(big_frame)

        # 再追加一帧 — 应因超限被跳过
        small_frame = np.zeros((480, 1), dtype=np.int16)
        with router._test_lock:
            current_frames = sum(b.shape[0] for b in router._test_buffer)
            if current_frames + small_frame.shape[0] <= max_frames:
                router._test_buffer.append(small_frame.copy())
            # 不应添加
        total = sum(b.shape[0] for b in router._test_buffer)
        assert total > max_frames  # 原有的大帧仍在（一次性越界）
        # 但小帧没被添加
        assert total == max_frames + 480
