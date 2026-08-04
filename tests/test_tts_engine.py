#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for BackendTTSEngine ABC and TTSAudioPlayer Protocol."""

from io import BytesIO

import pytest

from wordy.tts.engine import BackendTTSEngine, TTSAudioPlayer, VoiceInfo


# ---------------------------------------------------------------------------
# Minimal test doubles
# ---------------------------------------------------------------------------

class _DummyAudioPlayer:
    """A TTSAudioPlayer stub that satisfies the Protocol structurally."""

    def play_wav(self, wav_path: str | BytesIO, device_index: int | None = None) -> bool:
        return True

    def open_stream(
        self,
        audio_format: object,
        channels: int,
        rate: int,
        device_index: int | None = None,
        frames_per_buffer: int = 1024,
    ) -> bool:
        return True

    def write_stream(self, data: bytes) -> None:
        pass

    def close_stream(self) -> None:
        pass


class _ConcreteEngine(BackendTTSEngine):
    """A minimal concrete TTS engine for testing the ABC methods."""

    def fetch_voices(self) -> list[VoiceInfo]:
        return [{"id": "v1", "name": "Alice"}]

    def speak(self, text: str) -> bool:
        return len(text) > 0


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBackendTTSEngineCannotInstantiateDirectly:
    """The ABC must not be instantiated without concrete abstract methods."""

    def test_direct_instantiation_raises_type_error(self) -> None:
        player = _DummyAudioPlayer()
        with pytest.raises(TypeError):
            BackendTTSEngine(player)  # type: ignore[abstract]


class TestConcreteEngineInit:
    """__init__ should store the provided attributes."""

    def test_init_sets_audio_player(self) -> None:
        player = _DummyAudioPlayer()
        engine = _ConcreteEngine(player)
        assert engine.audio_player is player

    def test_init_default_voice_id_is_none(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer())
        assert engine.voice_id is None

    def test_init_default_volume_is_one(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer())
        assert engine.volume == 1.0

    def test_init_explicit_voice_id(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer(), voice_id="v42")
        assert engine.voice_id == "v42"

    def test_init_explicit_volume(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer(), volume=0.5)
        assert engine.volume == 0.5


class TestSetVoice:
    """set_voice updates voice_id on the engine."""

    def test_set_voice_updates_voice_id(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer(), voice_id="old")
        engine.set_voice("new_voice")
        assert engine.voice_id == "new_voice"

    def test_set_voice_to_none(self) -> None:
        """voice_id can be set to None (e.g. clearing selection)."""
        engine = _ConcreteEngine(_DummyAudioPlayer(), voice_id="v1")
        engine.set_voice(None)  # type: ignore[arg-type]
        assert engine.voice_id is None


class TestSetVolume:
    """set_volume updates volume on the engine."""

    def test_set_volume_updates_volume(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer(), volume=1.0)
        engine.set_volume(0.75)
        assert engine.volume == 0.75

    def test_set_volume_zero(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer(), volume=1.0)
        engine.set_volume(0.0)
        assert engine.volume == 0.0

    def test_set_volume_above_one(self) -> None:
        """Volume can be > 1.0 (amplification)."""
        engine = _ConcreteEngine(_DummyAudioPlayer())
        engine.set_volume(2.0)
        assert engine.volume == 2.0


class TestConnectAndCloseAreNoOps:
    """connect() and close() are no-ops that should not raise."""

    def test_connect_does_not_raise(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer())
        # connect is a no-op on the base class
        engine.connect()

    def test_close_does_not_raise(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer())
        engine.close()

    def test_connect_then_close_does_not_raise(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer())
        engine.connect()
        engine.close()


class TestConcreteEngineAbstractMethods:
    """The concrete subclass must implement fetch_voices and speak."""

    def test_fetch_voices_returns_list_of_voice_info(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer())
        voices = engine.fetch_voices()
        assert isinstance(voices, list)
        assert len(voices) == 1
        assert voices[0]["id"] == "v1"
        assert voices[0]["name"] == "Alice"

    def test_speak_returns_true_for_non_empty_text(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer())
        assert engine.speak("Hello") is True

    def test_speak_returns_false_for_empty_text(self) -> None:
        engine = _ConcreteEngine(_DummyAudioPlayer())
        assert engine.speak("") is False


class TestTTSAudioPlayerProtocol:
    """Verify that _DummyAudioPlayer satisfies the TTSAudioPlayer Protocol."""

    def test_dummy_player_has_required_methods(self) -> None:
        player = _DummyAudioPlayer()
        assert callable(player.play_wav)
        assert callable(player.open_stream)
        assert callable(player.write_stream)
        assert callable(player.close_stream)

    def test_play_wav_returns_true(self) -> None:
        player = _DummyAudioPlayer()
        assert player.play_wav("test.wav") is True

    def test_open_stream_returns_true(self) -> None:
        player = _DummyAudioPlayer()
        assert player.open_stream(audio_format=None, channels=2, rate=44100) is True

    def test_write_and_close_stream_do_not_raise(self) -> None:
        player = _DummyAudioPlayer()
        player.write_stream(b"audio data")
        player.close_stream()
