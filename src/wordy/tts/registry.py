#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS 后端注册表和工厂。"""

from .cartesia import CartesiaBytesTTS, CartesiaRealtimeTTS
from .constants import (
    DEFAULT_TTS_BACKEND,
    TTS_BACKENDS_BY_PROVIDER,
    TTS_BACKEND_CARTESIA_BYTES,
    TTS_BACKEND_CARTESIA_REALTIME,
    TTS_BACKEND_VOLCENGINE_STREAMING,
)
from .engine import BackendTTSEngine, TTSAudioPlayer
from .volcengine import VolcengineStreamingTTS

TTS_ENGINE_REGISTRY = {
    TTS_BACKEND_CARTESIA_BYTES: CartesiaBytesTTS,
    TTS_BACKEND_CARTESIA_REALTIME: CartesiaRealtimeTTS,
    TTS_BACKEND_VOLCENGINE_STREAMING: VolcengineStreamingTTS,
}


def resolve_tts_backend(tts_backend: str | None = None, provider: str | None = None) -> str:
    """校验 TTS 后端配置。"""
    backend = tts_backend or DEFAULT_TTS_BACKEND
    allowed = TTS_BACKENDS_BY_PROVIDER.get(provider, tuple(TTS_ENGINE_REGISTRY)) if provider else tuple(TTS_ENGINE_REGISTRY)
    if backend not in allowed:
        if provider is not None:
            raise ValueError(f"{provider} TTS_BACKEND 必须是 {', '.join(allowed)}")
        raise ValueError(f"TTS_BACKEND 必须是 {', '.join(sorted(TTS_ENGINE_REGISTRY))}")

    return backend


def create_tts_engine(
    backend: str,
    audio_player: TTSAudioPlayer,
    api_key: str | None,
    voice_id: str | None = None,
    volume: float = 1.0,
    volcengine_access_key: str | None = None,
) -> BackendTTSEngine:
    """按后端配置创建 TTS 引擎。"""
    backend = resolve_tts_backend(backend)
    engine_cls = TTS_ENGINE_REGISTRY[backend]

    # 不允许服务商之间回退凭据，否则缺少火山密钥时会把 Cartesia 密钥发给第三方。
    if backend == TTS_BACKEND_VOLCENGINE_STREAMING:
        return engine_cls(
            audio_player,
            api_key=volcengine_access_key,
            voice_id=voice_id,
            volume=volume,
        )

    return engine_cls(audio_player, api_key=api_key, voice_id=voice_id, volume=volume)
