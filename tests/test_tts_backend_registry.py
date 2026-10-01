#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for tts_backend_registry.py."""

import sys
from unittest.mock import Mock

import pytest

# Stub out external dependencies that won't be available on CI.
# Must happen before importing wordy.tts modules below.
from tests._stubs import CARTESIA_NATIVE_DEPS, DummyModule, install_module_stubs

install_module_stubs(CARTESIA_NATIVE_DEPS)
try:
    import requests  # noqa: F401
except ImportError:
    sys.modules['requests'] = DummyModule()

from wordy.tts.registry import resolve_tts_backend, create_tts_engine
from wordy.tts.constants import DEFAULT_TTS_BACKEND
from wordy.tts.engine import BackendTTSEngine, TTSAudioPlayer


class FakeFakeEngine(BackendTTSEngine):
    """Fake TTS engine for testing."""
    def __init__(self, audio_player, api_key=None, voice_id=None, volume=1.0):
        self.audio_player = audio_player
        self.api_key = api_key
        self.voice_id = voice_id
        self.volume = volume

    def fetch_voices(self) -> list[dict]:
        return []

    def speak(self, text: str) -> bool:
        return True

    def stop(self) -> None:
        pass


def test_resolve_tts_backend_default_when_none():
    """Test that None returns default backend."""
    assert resolve_tts_backend(None) == DEFAULT_TTS_BACKEND


def test_resolve_tts_backend_default_when_empty():
    """Test that empty string returns default backend."""
    assert resolve_tts_backend("") == DEFAULT_TTS_BACKEND


def test_resolve_tts_backend_valid():
    """Test that valid backend returns itself."""
    from wordy.tts.constants import TTS_BACKEND_CARTESIA_BYTES
    assert resolve_tts_backend(TTS_BACKEND_CARTESIA_BYTES) == TTS_BACKEND_CARTESIA_BYTES


def test_resolve_tts_backend_invalid_raises():
    """Test that invalid backend raises ValueError."""
    with pytest.raises(ValueError, match="TTS_BACKEND 必须是"):
        resolve_tts_backend("Invalid Backend")
    with pytest.raises(ValueError):
        resolve_tts_backend(" ")


def test_create_tts_engine_creates_correct_instance(monkeypatch):
    """Test create_tts_engine creates proper instance from registry."""
    # Create fake audio player
    fake_audio_player = Mock(spec=TTSAudioPlayer)
    
    # Monkeypatch the registry with our fake engine
    from wordy.tts import registry
    original_registry = registry.TTS_ENGINE_REGISTRY.copy()
    monkeypatch.setattr(
        registry,
        "TTS_ENGINE_REGISTRY",
        {"Fake": FakeFakeEngine, **original_registry}
    )
    
    # Create engine
    engine = create_tts_engine(
        "Fake",
        fake_audio_player,
        api_key="test_key",
        voice_id="test_voice",
        volume=0.8
    )
    
    # Verify it's the correct instance and parameters are passed
    assert isinstance(engine, FakeFakeEngine)
    assert engine.audio_player is fake_audio_player
    assert engine.api_key == "test_key"
    assert engine.voice_id == "test_voice"
    assert engine.volume == 0.8


@pytest.mark.parametrize("api_key, provider_key, expected_key", [(None, None, None), ("fallback", None, "fallback"), ("fallback", "provider", "provider")])
def test_create_tts_engine_resolves_default(monkeypatch, api_key, provider_key, expected_key):
    """Test that None backend resolves to default before creation."""
    fake_audio_player = Mock(spec=TTSAudioPlayer)
    
    from wordy.tts import registry
    original_registry = registry.TTS_ENGINE_REGISTRY.copy()
    monkeypatch.setattr(
        registry,
        "TTS_ENGINE_REGISTRY",
        {"Fake": FakeFakeEngine, DEFAULT_TTS_BACKEND: FakeFakeEngine}
    )
    
    # Should resolve default backend
    # The provider credential must still win after removing the unused constructor alias.
    engine = create_tts_engine(None, fake_audio_player, api_key, volcengine_access_key=provider_key)
    assert isinstance(engine, FakeFakeEngine)
    assert engine.api_key == expected_key
