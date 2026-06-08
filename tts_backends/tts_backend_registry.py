#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS 后端注册表和工厂。"""

from .cartesia_connect import CartesiaBytesTTS, CartesiaRealtimeTTS
from .constants import DEFAULT_TTS_BACKEND, TTS_BACKEND_CARTESIA_BYTES, TTS_BACKEND_CARTESIA_REALTIME
from .tts_engine import BackendTTSEngine, TTSAudioPlayer

TTS_ENGINE_REGISTRY = {
    TTS_BACKEND_CARTESIA_BYTES: CartesiaBytesTTS,
    TTS_BACKEND_CARTESIA_REALTIME: CartesiaRealtimeTTS,
}


def resolve_tts_backend(tts_backend: str | None = None) -> str:
    """校验 TTS 后端配置。"""
    backend = tts_backend or DEFAULT_TTS_BACKEND
    if backend not in TTS_ENGINE_REGISTRY:
        raise ValueError(f"TTS_BACKEND 必须是 {', '.join(sorted(TTS_ENGINE_REGISTRY))}")

    return backend


def create_tts_engine(
    backend: str,
    audio_player: TTSAudioPlayer,
    api_key: str | None,
    voice_id: str | None = None,
    volume: float = 1.0,
) -> BackendTTSEngine:
    """按后端配置创建 TTS 引擎。"""
    backend = resolve_tts_backend(backend)
    engine_cls = TTS_ENGINE_REGISTRY[backend]
    return engine_cls(audio_player, api_key=api_key, voice_id=voice_id, volume=volume)
