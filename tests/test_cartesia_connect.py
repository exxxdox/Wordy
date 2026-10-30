#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for Cartesia connect helper functions and logging redaction."""

import logging
from typing import TYPE_CHECKING, Iterator, Protocol, cast
from unittest.mock import Mock

import pytest
import requests

# Stub out external dependencies that won't be available on CI.
# Must happen before importing cartesia_connect below.
from tests._stubs import CARTESIA_NATIVE_DEPS, install_module_stubs

install_module_stubs(CARTESIA_NATIVE_DEPS)

# requests 在本文件顶部已无条件导入，Response 必然可用。
# 原先的 except 分支内联定义了同名 class，让 Response 符号变成「声明类型」，
# 下方的 Mock(spec=Response) 反而以此为准而报错 —— 删掉死掉的回落分支。
from requests import Response

from wordy.tts.engine import VoiceInfo
from wordy.tts.labels import VoiceLabelMaps, build_voice_label_maps
from wordy.tts.cartesia import (
    _ensure_cartesia_config,
    _voice_specifier,
    _generation_config,
    _wav_output_format,
    _raw_int16_output_format,
    _raise_for_status,
    logger as cartesia_logger,
    DEFAULT_CARTESIA_SAMPLE_RATE,
    DEFAULT_CARTESIA_VERSION,
)

if TYPE_CHECKING:
    # cartesia 内部类只在类型层面引用：运行期不回落到已 stub 的模块。
    from cartesia.lib._tts import TTSResourceConnection, TTSResourceConnectionManager
    from wordy.tts.cartesia import CartesiaRealtimeTTS


class TestBuildVoiceLabelMaps:
    """Test build_voice_label_maps handling of names and selection."""

    def test_unique_names(self):
        """Test all unique names are preserved without suffixes."""
        # list 不变型：裸字面量推断为 list[dict[str, str]]，需显式标成 VoiceInfo。
        voices: list[VoiceInfo] = [
            {"id": "id1", "name": "Alice"},
            {"id": "id2", "name": "Bob"},
            {"id": "id3", "name": "Charlie"},
        ]
        result = build_voice_label_maps(voices, selected_voice_id=None)
        
        assert isinstance(result, VoiceLabelMaps)
        assert len(result.labels) == 3
        assert result.labels == ["Alice", "Bob", "Charlie"]
        assert result.label_to_id == {
            "Alice": "id1",
            "Bob": "id2",
            "Charlie": "id3",
        }
        assert result.label_to_name == {
            "Alice": "Alice",
            "Bob": "Bob",
            "Charlie": "Charlie",
        }
        assert result.selected_label is None

    def test_duplicate_names_get_suffixed(self):
        """Test duplicate names get (id prefix) suffix to disambiguate."""
        voices: list[VoiceInfo] = [
            {"id": "abcdef12345", "name": "Alice"},
            {"id": "abcdef67890", "name": "Alice"},
            {"id": "xyz000", "name": "Bob"},
        ]
        result = build_voice_label_maps(voices, selected_voice_id=None)
        
        assert len(result.labels) == 3
        assert "Alice" in result.labels
        assert "Alice (abcdef67)" in result.labels
        assert "Bob" in result.labels
        assert result.label_to_id["Alice"] == "abcdef12345"
        assert result.label_to_id["Alice (abcdef67)"] == "abcdef67890"
        assert result.label_to_name["Alice"] == "Alice"
        assert result.label_to_name["Alice (abcdef67)"] == "Alice"

    def test_selected_voice_found(self):
        """Test selected_voice_id correctly maps to selected_label."""
        voices: list[VoiceInfo] = [
            {"id": "id1", "name": "Alice"},
            {"id": "id2", "name": "Bob"},
        ]
        result = build_voice_label_maps(voices, selected_voice_id="id2")
        
        assert result.selected_label == "Bob"

    def test_selected_voice_not_found(self):
        """Test selected_label is None when selected_voice_id not found."""
        voices: list[VoiceInfo] = [
            {"id": "id1", "name": "Alice"},
        ]
        result = build_voice_label_maps(voices, selected_voice_id="nonexistent")
        
        assert result.selected_label is None


class TestEnsureCartesiaConfig:
    """Test _ensure_cartesia_config config validation."""

    def test_missing_api_key_raises_settings_wording(self):
        """Test missing API key raises RuntimeError with settings-based wording."""
        with pytest.raises(RuntimeError) as exc_info:
            _ensure_cartesia_config(api_key=None, voice_id="some-id")

        error_text = str(exc_info.value)
        assert "缺少 Cartesia API Key" in error_text
        assert "设置" in error_text
        assert "环境变量" not in error_text
        assert ".env" not in error_text
        assert "CARTESIA_API_KEY" not in error_text
        assert "os.environ" not in error_text

    def test_missing_voice_id_does_not_leak_api_key_in_exception_text(self):
        """Test validation errors do not expose configured API key values."""
        sentinel_key = "sentinel-cartesia-key-never-log"

        with pytest.raises(RuntimeError) as exc_info:
            _ensure_cartesia_config(api_key=sentinel_key, voice_id=None)

        assert sentinel_key not in str(exc_info.value)
        assert sentinel_key not in repr(exc_info.value)

    def test_missing_voice_id_raises(self):
        """Test missing voice selection raises RuntimeError."""
        with pytest.raises(RuntimeError, match="缺少音色配置"):
            _ensure_cartesia_config(api_key="fake-key", voice_id=None)

    def test_valid_config_does_not_raise(self):
        """Test valid config does not raise any error."""
        _ensure_cartesia_config(api_key="fake-key", voice_id="fake-id")


def test_voice_specifier():
    """Test _voice_specifier returns correct structure."""
    result = _voice_specifier("my-voice-id")
    assert result == {"mode": "id", "id": "my-voice-id"}


def test_generation_config():
    """Test _generation_config returns correct structure with volume."""
    result = _generation_config(0.75)
    assert result == {"volume": 0.75, "speed": 1}


def test_wav_output_format():
    """Test _wav_output_format returns correct wav format config."""
    result = _wav_output_format(44100)
    assert result == {
        "container": "wav",
        "encoding": "pcm_s16le",
        "sample_rate": 44100,
    }


def test_raw_int16_output_format():
    """Test _raw_int16_output_format returns correct raw format config."""
    result = _raw_int16_output_format(24000)
    assert result == {
        "container": "raw",
        "encoding": "pcm_s16le",
        "sample_rate": 24000,
    }


class TestRaiseForStatus:
    """Test _raise_for_status error handling and truncation."""

    def test_truncates_long_response(self):
        """Test response text is truncated to 300 characters."""
        mock_response = Mock(spec=Response)
        long_text = "x" * 500
        mock_response.text = long_text
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = requests.HTTPError("Test error")

        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_status(mock_response, "Test prefix")
        
        error_msg = str(exc_info.value)
        assert "Test prefix" in error_msg
        assert "HTTP 500" in error_msg
        response_excerpt = error_msg.split("HTTP 500 ", 1)[1]
        assert len(response_excerpt) == 300
        assert set(response_excerpt) == {"x"}

    def test_replaces_newlines_with_spaces(self):
        """Test newlines in response text are replaced with spaces."""
        mock_response = Mock(spec=Response)
        mock_response.text = "line1\nline2\r\nline3"
        mock_response.status_code = 400
        mock_response.raise_for_status.side_effect = requests.HTTPError("Test error")

        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_status(mock_response, "Test prefix")
        
        error_msg = str(exc_info.value)
        assert "line1 line2\r line3" in error_msg
        assert "\n" not in error_msg.split("HTTP 400 ")[1]

    def test_http_error_text_masks_api_key_sentinel(self):
        """Test HTTP error formatting masks API-key-like secrets from response text."""
        sentinel_key = "sk_test_DO_NOT_LEAK"
        mock_response = Mock(spec=Response)
        mock_response.text = f"upstream rejected key {sentinel_key} in request"
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = requests.HTTPError("Test error")

        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_status(mock_response, "Test prefix")

        assert sentinel_key not in str(exc_info.value)
        assert sentinel_key not in repr(exc_info.value)
        assert "sk_***" in str(exc_info.value)

    def test_http_error_text_masks_dash_api_key_sentinel(self):
        """Test HTTP error formatting masks dash-separated API-key-like secrets."""
        sentinel_key = "sk-test-DO-NOT-LEAK"
        mock_response = Mock(spec=Response)
        mock_response.text = f"upstream rejected key {sentinel_key} in request"
        mock_response.status_code = 401
        mock_response.raise_for_status.side_effect = requests.HTTPError("Test error")

        with pytest.raises(RuntimeError) as exc_info:
            _raise_for_status(mock_response, "Test prefix")

        assert sentinel_key not in str(exc_info.value)
        assert sentinel_key not in repr(exc_info.value)
        assert "sk_***" in str(exc_info.value)


class TestLoggingTextRedactionExposure:
    """Debug 日志行只带长度元信息与 context_id，绝不带用户原文。"""

    def test_bytes_tts_debug_log_redacts_raw_text(self, caplog, monkeypatch):
        """驱动真实 generate()：原文必须进请求体，但绝不能进日志。"""
        from wordy.tts.cartesia import CartesiaBytesTTS
        from wordy.tts.engine import TTSAudioPlayer

        mock_player = Mock(spec=TTSAudioPlayer)
        tts = CartesiaBytesTTS(
            audio_player=mock_player,
            api_key="fake-key",
            voice_id="fake-id",
        )

        secret_text = "This is a private user message that should not be in logs"
        caplog.set_level(logging.DEBUG, logger=cartesia_logger.name)

        mock_resp = Mock()
        mock_resp.content = b""
        mock_resp.raise_for_status = lambda: None
        sent: list[dict] = []
        monkeypatch.setattr(
            "requests.post",
            lambda *args, **kwargs: (sent.append(kwargs["json"]) or mock_resp),
        )

        tts.generate(secret_text)

        # 前提断言：生产路径确实跑过（原文进了请求体）。少掉它，「日志里没有原文」
        # 会因为压根没跑到而假绿 —— 原来的 try/except: pass 正是这么吞掉的。
        assert sent and sent[0]["transcript"] == secret_text, "生产路径未被驱动，日志断言会假绿"

        messages = [record.message for record in caplog.records]
        assert not any(secret_text in message for message in messages), (
            f"bytes 日志泄露了用户原文: {messages!r}"
        )
        assert any(str(len(secret_text)) in message for message in messages), (
            f"bytes 日志缺少长度元信息: {messages!r}"
        )

    def test_realtime_tts_debug_log_redacts_raw_text(self, caplog):
        """驱动真实 _send_and_play_once：原文必须送达 context，但绝不能进日志。"""
        tts, _ = _make_realtime_tts()

        pushed: list[str] = []
        captured_kwargs: dict = {}

        class _RecordingCtx:
            def push(self, text):
                pushed.append(text)

            def no_more_inputs(self):
                pass

            def receive(self):
                class _Done:
                    type = "done"

                yield _Done()

        fake_conn = _FakeConnection()
        fake_conn.context = lambda **kwargs: (captured_kwargs.update(kwargs) or _RecordingCtx())
        _install_connection(tts, fake_conn)

        secret_text = "Another secret text that should be redacted from debug logs"
        caplog.set_level(logging.DEBUG, logger=cartesia_logger.name)

        tts._send_and_play_once(secret_text)

        # 前提断言：生产代码确实走完了那行日志（原文送达 context）。少掉它，
        # 下面的「日志里没有原文」会因为压根没跑到而假绿 —— 正是这个类原先的病。
        assert pushed == [secret_text], "生产路径未被驱动，日志断言会假绿"

        messages = [record.message for record in caplog.records]
        assert not any(secret_text in message for message in messages), (
            f"realtime 日志泄露了用户原文: {messages!r}"
        )
        assert any(str(len(secret_text)) in message for message in messages), (
            f"realtime 日志缺少长度元信息: {messages!r}"
        )
        assert any(captured_kwargs["context_id"] in message for message in messages), (
            f"realtime 日志缺少 context_id: {messages!r}"
        )


class _TTSContext(Protocol):
    """realtime 一次请求的 context 最小接口，用作假连接的 context() 返回类型。"""

    def push(self, text: str) -> None: ...
    def no_more_inputs(self) -> None: ...
    def receive(self) -> Iterator[object]: ...


def _install_connection(tts: "CartesiaRealtimeTTS", connection: object) -> None:
    """把 duck-typed 假连接挂到私有属性。

    _connection 的声明类型是 cartesia 内部具体类，假连接只做结构模拟，
    无法名义兼容；用 cast 收窄，运行期仍是原对象。
    """
    tts._connection = cast("TTSResourceConnection", connection)


def _install_connection_manager(tts: "CartesiaRealtimeTTS", manager: object) -> None:
    tts._connection_manager = cast("TTSResourceConnectionManager", manager)


class _FakeConnection:
    """Fake websocket connection that records context() calls."""

    def __init__(self):
        self.context_calls = []

    # 显式返回类型 _TTSContext：否则推断成 Mock，测试里替换 context 的 lambda
    # 返回 _RecordingCtx/_FakeCtx 时会因返回类型不兼容而报错。
    def context(self, **kwargs: object) -> _TTSContext:
        self.context_calls.append(kwargs)
        return Mock()


class _FakeConnectionManager:
    """Fake context manager mirroring tts.websocket_connect() return value."""

    def __init__(self, connection=None, raise_on_exit=False):
        self.connection = connection or _FakeConnection()
        self.entered = 0
        self.exited = 0
        self.exit_args = None
        self.raise_on_exit = raise_on_exit

    def __enter__(self):
        self.entered += 1
        return self.connection

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.exited += 1
        self.exit_args = (exc_type, exc_val, exc_tb)
        if self.raise_on_exit:
            raise RuntimeError("simulated exit failure")
        return False


def _make_realtime_tts():
    from wordy.tts.cartesia import CartesiaRealtimeTTS
    from wordy.tts.engine import TTSAudioPlayer

    mock_player = Mock(spec=TTSAudioPlayer)
    mock_player.open_stream.return_value = True
    tts = CartesiaRealtimeTTS(
        audio_player=mock_player,
        api_key="fake-key",
        voice_id="fake-id",
    )
    return tts, mock_player


class TestRealtimeLifecycleLocking:
    """Locking + cleanup guarantees for CartesiaRealtimeTTS."""

    def test_lock_is_reentrant(self):
        """speak() holds the lock and calls connect(); RLock must allow reentry."""
        import threading

        tts, _ = _make_realtime_tts()
        assert isinstance(tts._lock, type(threading.RLock()))

        with tts._lock:
            with tts._lock:
                pass

    def test_connect_is_idempotent_when_connection_set(self):
        """connect() must return immediately if _connection already exists."""
        tts, mock_player = _make_realtime_tts()
        existing = _FakeConnection()
        _install_connection(tts, existing)
        _install_connection_manager(tts, _FakeConnectionManager(existing))

        tts.connect()

        mock_player.open_stream.assert_not_called()
        assert tts._connection is existing

    def test_close_websocket_invokes_exit_and_clears_refs(self):
        """_close_websocket must call __exit__ and clear connection refs."""
        tts, _ = _make_realtime_tts()
        manager = _FakeConnectionManager()
        _install_connection_manager(tts, manager)
        _install_connection(tts, manager.connection)

        tts._close_websocket()

        assert manager.exited == 1
        assert manager.exit_args == (None, None, None)
        assert tts._connection_manager is None
        assert tts._connection is None

    def test_close_websocket_handles_exit_failure(self):
        """Exceptions from __exit__ are swallowed and refs still cleared."""
        tts, _ = _make_realtime_tts()
        manager = _FakeConnectionManager(raise_on_exit=True)
        _install_connection_manager(tts, manager)
        _install_connection(tts, manager.connection)

        tts._close_websocket()

        assert manager.exited == 1
        assert tts._connection_manager is None
        assert tts._connection is None

    def test_close_websocket_noop_without_manager(self):
        """_close_websocket is safe to call when nothing was opened."""
        tts, _ = _make_realtime_tts()
        tts._close_websocket()
        assert tts._connection_manager is None
        assert tts._connection is None

    def test_close_acquires_lock_and_closes_audio(self):
        """close() must take the lock and also close the audio stream."""
        tts, mock_player = _make_realtime_tts()
        manager = _FakeConnectionManager()
        _install_connection_manager(tts, manager)
        _install_connection(tts, manager.connection)

        tts.close()

        assert manager.exited == 1
        mock_player.close_stream.assert_called_once()
        assert tts._connection is None
        assert tts._connection_manager is None

    def test_close_under_held_lock_does_not_deadlock(self):
        """RLock allows close() to run while the caller already owns the lock."""
        tts, mock_player = _make_realtime_tts()

        with tts._lock:
            tts.close()

        mock_player.close_stream.assert_called_once()

    def test_connect_under_held_lock_does_not_deadlock(self):
        """RLock allows connect() to run while the caller already owns the lock."""
        tts, _ = _make_realtime_tts()
        existing = _FakeConnection()
        _install_connection(tts, existing)

        with tts._lock:
            tts.connect()

        assert tts._connection is existing


class TestRealtimeAudioStream:
    """Realtime output must use formats and buffers compatible with the selected device."""

    def test_cable_stream_uses_safe_buffer_size(self):
        """VB-CABLE's 7168-sample latency requires client buffers to stay below 7168 / 3."""
        from wordy.tts.cartesia import pyaudio

        tts, mock_player = _make_realtime_tts()
        mock_player.output_device_name = "CABLE Input (VB-Audio Virtual Cable)"
        mock_player.query_output_device_default_rate.return_value = 48000
        mock_player.get_stream_config.return_value = {
            "format": pyaudio.paInt16,
            "rate": 48000,
        }

        tts._open_audio_stream()

        mock_player.open_stream.assert_called_once_with(
            audio_format=pyaudio.paInt16,
            channels=1,
            rate=48000,
            frames_per_buffer=1024,
        )

    def test_default_output_device_still_uses_int16(self):
        """配置名为空（用户把 CABLE Input 设成 Windows 默认设备）时也必须走 int16。

        之前用配置名判断设备类型，此时 output_device_name 是空串，会退回 float32
        并被虚拟设备的 WASAPI 端点错当 int16 解析——正是要避免的变声。
        """
        from wordy.tts.cartesia import pyaudio

        tts, mock_player = _make_realtime_tts()
        mock_player.output_device_name = None
        mock_player.query_output_device_default_rate.return_value = 44100
        mock_player.get_stream_config.return_value = {
            "format": pyaudio.paInt16,
            "rate": 44100,
        }

        tts._open_audio_stream()

        mock_player.open_stream.assert_called_once_with(
            audio_format=pyaudio.paInt16,
            channels=1,
            rate=44100,
            frames_per_buffer=1024,
        )
        assert tts.sample_rate == 44100


class TestSharedRequestArgs:
    """Shared payload construction via CartesiaTTS._build_request_args."""

    # 只暴露各用例实际覆写的 volume；原先的 **overrides 异构字典
    # 拆包给固定签名构造器时无法静态校验，改为显式参数。
    def _make_bytes(self, volume: float = 0.5):
        from wordy.tts.cartesia import CartesiaBytesTTS

        mock_player = Mock()
        return CartesiaBytesTTS(
            audio_player=mock_player, api_key="fake-key", voice_id="voice-1", volume=volume
        )

    def _make_realtime(self, volume: float = 0.5):
        from wordy.tts.cartesia import CartesiaRealtimeTTS

        mock_player = Mock()
        return CartesiaRealtimeTTS(
            audio_player=mock_player, api_key="fake-key", voice_id="voice-1", volume=volume
        )

    def test_default_constants_preserved(self):
        """DEFAULT_CARTESIA_VERSION and default model/sample_rate/volume must stay stable."""
        assert DEFAULT_CARTESIA_VERSION == "2026-03-01"
        assert DEFAULT_CARTESIA_SAMPLE_RATE == 48000

        bytes_tts = self._make_bytes(volume=1.0)
        assert bytes_tts.model_id == "sonic-3.5"
        assert bytes_tts.sample_rate == DEFAULT_CARTESIA_SAMPLE_RATE
        assert bytes_tts.volume == 1.0
        assert bytes_tts.version == DEFAULT_CARTESIA_VERSION
        assert bytes_tts.timeout == 60

        realtime_tts = self._make_realtime(volume=1.0)
        assert realtime_tts.model_id == "sonic-3.5"
        assert realtime_tts.sample_rate == DEFAULT_CARTESIA_SAMPLE_RATE
        assert realtime_tts.volume == 1.0

    def test_build_request_args_structure(self):
        """_build_request_args returns the shared payload fields with caller-supplied output_format."""
        bytes_tts = self._make_bytes()
        output_format = _wav_output_format(bytes_tts.sample_rate)
        result = bytes_tts._build_request_args(output_format)

        assert result == {
            "model_id": "sonic-3.5",
            "voice": {"mode": "id", "id": "voice-1"},
            "output_format": output_format,
            "language": "zh",
            "generation_config": {"volume": 0.5, "speed": 1},
        }

    def test_bytes_and_realtime_share_common_fields(self):
        """Both backends produce identical model/voice/language/generation_config; only output_format differs."""
        bytes_tts = self._make_bytes()
        realtime_tts = self._make_realtime()

        bytes_args = bytes_tts._build_request_args(_wav_output_format(bytes_tts.sample_rate))
        realtime_args = realtime_tts._build_request_args(
            _raw_int16_output_format(realtime_tts.sample_rate)
        )

        common_keys = ("model_id", "voice", "language", "generation_config")
        for key in common_keys:
            assert bytes_args[key] == realtime_args[key], f"shared field {key} drifted between backends"

        assert bytes_args["output_format"]["container"] == "wav"
        assert realtime_args["output_format"]["container"] == "raw"

    def test_bytes_generate_payload_uses_shared_args(self, monkeypatch):
        """CartesiaBytesTTS.generate() builds payload with shared fields plus transcript+speed."""
        captured = {}

        class _Resp:
            content = b""
            def raise_for_status(self):
                pass

        def _fake_post(url, json, headers, timeout):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers
            captured["timeout"] = timeout
            return _Resp()

        monkeypatch.setattr("wordy.tts.cartesia.requests.post", _fake_post)

        bytes_tts = self._make_bytes()
        bytes_tts.generate("hello world")

        payload = captured["json"]
        expected_shared = bytes_tts._build_request_args(_wav_output_format(bytes_tts.sample_rate))
        for key, value in expected_shared.items():
            assert payload[key] == value
        assert payload["transcript"] == "hello world"
        assert payload["speed"] == "normal"

    def test_realtime_context_kwargs_use_shared_args(self):
        """CartesiaRealtimeTTS._send_and_play_once passes shared args + context_id to connection.context().

        流固定按 paInt16 打开，请求编码就固定 pcm_s16le——不再看流格式分支。
        """
        from wordy.tts.cartesia import CartesiaRealtimeTTS

        mock_player = Mock()
        mock_player.open_stream.return_value = True
        tts = CartesiaRealtimeTTS(
            audio_player=mock_player,
            api_key="fake-key",
            voice_id="voice-1",
            volume=0.5,
        )

        fake_conn = _FakeConnection()

        class _FakeCtx:
            def push(self, text): pass
            def no_more_inputs(self): pass
            def receive(self):
                class _R:
                    type = "done"
                yield _R()

        fake_conn.context = lambda **kwargs: (captured_kwargs.update(kwargs) or _FakeCtx())
        captured_kwargs = {}
        _install_connection(tts, fake_conn)

        tts._send_and_play_once("hi")

        expected_shared = tts._build_request_args(_raw_int16_output_format(tts.sample_rate))
        assert captured_kwargs["context_id"].startswith("cartesia-tts-")
        for key, value in expected_shared.items():
            assert captured_kwargs[key] == value

    def test_realtime_speak_cancels_context_and_stops_writing(self, monkeypatch):
        """取消要在 worker 线程里向服务端 cancel，并停止写入后续音频块。"""
        import sys
        import threading
        from types import ModuleType

        from wordy.tts.cartesia import CartesiaRealtimeTTS

        # speak() 会 from websockets.exceptions import ConnectionClosed，
        # 而 websockets 在本文件被替换成 DummyModule（不能作为包导入子模块）。
        exceptions = ModuleType("websockets.exceptions")

        class _ConnectionClosed(Exception):
            pass

        exceptions.ConnectionClosed = _ConnectionClosed  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "websockets.exceptions", exceptions)

        mock_player = Mock()
        mock_player.open_stream.return_value = True
        tts = CartesiaRealtimeTTS(
            audio_player=mock_player,
            api_key="fake-key",
            voice_id="voice-1",
        )

        class _Chunk:
            type = "chunk"
            audio = b"\x00\x01" * 10
            context_id = "cartesia-tts-test"

        class _CancelCtx:
            cancelled = 0

            def push(self, text): pass
            def no_more_inputs(self): pass
            def cancel(self): self.cancelled += 1
            def receive(self):
                while True:
                    yield _Chunk()

        ctx = _CancelCtx()
        fake_conn = _FakeConnection()
        fake_conn.context = lambda **kwargs: ctx
        _install_connection(tts, fake_conn)

        cancel = threading.Event()
        # 第一块写完即取消：不能继续写第二块。
        mock_player.write_stream.side_effect = lambda _data: cancel.set()

        assert tts.speak("hi", cancel) is False
        assert ctx.cancelled == 1
        assert mock_player.write_stream.call_count == 1
