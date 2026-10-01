#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""测试 Volcengine TTS 后端（volcengine.py）。"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import Mock

import pytest

from wordy.tts.volcengine import (
    TTS_SSE_URL,
    VolcengineStreamingTTS,
    _build_auth_headers,
    _build_request_body,
    _decode_base64_audio,
    _ensure_volcengine_config,
)
from wordy.tts.engine import TTSAudioPlayer


class TestBuildAuthHeaders:
    def test_api_key_header(self) -> None:
        headers = _build_auth_headers(api_key="k1")
        assert headers == {"X-Api-Key": "k1"}

    def test_no_key_returns_empty(self) -> None:
        assert _build_auth_headers(None) == {}


class TestBuildRequestBody:
    def test_default_format(self) -> None:
        body = _build_request_body("你好", "BV001_streaming")
        assert body["req_params"]["text"] == "你好"
        assert body["req_params"]["speaker"] == "BV001_streaming"
        assert body["req_params"]["audio_params"]["format"] == "pcm"


class TestDecodeBase64Audio:
    def test_standard_decode(self) -> None:
        import base64
        original = b"\x00\x01\x02\x03"
        encoded = base64.b64encode(original).decode()
        assert _decode_base64_audio(encoded) == original

    def test_unpadded_decode(self) -> None:
        import base64
        original = b"hello world!"
        encoded = base64.b64encode(original).decode().rstrip("=")
        assert _decode_base64_audio(encoded) == original


class TestEnsureVolcengineConfig:
    def test_auth_ok(self) -> None:
        _ensure_volcengine_config(api_key="k", voice_id="BV001")

    def test_missing_api_key_raises(self) -> None:
        with pytest.raises(RuntimeError, match="缺少火山引擎"):
            _ensure_volcengine_config(api_key=None, voice_id="BV001")

    def test_missing_voice_id_raises(self) -> None:
        with pytest.raises(RuntimeError, match="缺少音色"):
            _ensure_volcengine_config(api_key="k", voice_id=None)


class TestVolcengineStreamingTTS:
    @staticmethod
    def _make_engine(api_key: str | None = "fake-key", voice_id: str | None = "BV001_streaming") -> VolcengineStreamingTTS:
        player = Mock(spec=TTSAudioPlayer)
        player.output_device_name = None
        player.open_stream.return_value = True
        player.get_stream_config.return_value = {"format": 16, "rate": 24000}
        return VolcengineStreamingTTS(audio_player=player, api_key=api_key, voice_id=voice_id)

    def test_fetch_voices_returns_empty(self) -> None:
        engine = self._make_engine()
        assert engine.fetch_voices() == []

    def test_speak_missing_config_raises(self) -> None:
        engine = self._make_engine(api_key=None)
        engine.audio_player.output_device_name = None  # type: ignore[attr-defined]
        with pytest.raises(RuntimeError, match="缺少火山引擎"):
            engine.speak("测试")

    def test_speak_missing_voice_raises(self) -> None:
        engine = self._make_engine(voice_id=None)
        with pytest.raises(RuntimeError, match="缺少音色"):
            engine.speak("测试")

    def test_speak_streaming_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import base64
        pcm_data = b"\x00\x01" * 100
        audio_b64 = base64.b64encode(pcm_data).decode()
        sse_lines = [
            json.dumps({"code": 0, "message": "", "data": audio_b64}),
            json.dumps({"code": 20000000, "message": "ok"}),
        ]

        def _fake_iter_lines(decode_unicode=True):  # noqa: ARG001
            yield from sse_lines

        fake_response = Mock()
        fake_response.status_code = 200
        fake_response.iter_lines = _fake_iter_lines

        monkeypatch.setattr("requests.post", lambda *a, **kw: fake_response)
        engine = self._make_engine()
        result = engine.speak("你好世界")
        assert result is True
        assert engine.audio_player.write_stream.call_count >= 1  # type: ignore[attr-defined]

    def test_sse_endpoint_url(self) -> None:
        assert TTS_SSE_URL == "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"


def test_int16_stream_failure_does_not_play_pcm_as_float32():
    # 其它测试会替换 sys.modules 中的 pyaudio，断言引擎实际持有的依赖常量。
    from wordy.tts.volcengine import pyaudio

    player = Mock()
    player.output_device_name = "Speakers"
    player.open_stream.side_effect = [False, True]
    engine = VolcengineStreamingTTS(audio_player=player, api_key="key", voice_id="voice")
    with pytest.raises(RuntimeError, match="int16"):
        engine._open_audio_stream()
    player.open_stream.assert_called_once_with(
        audio_format=pyaudio.paInt16, channels=1, rate=48000, frames_per_buffer=1024,
    )
    player.write_stream.assert_not_called()
