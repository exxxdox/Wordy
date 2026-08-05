#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS 后端注册表和工厂。"""

from .cartesia import CartesiaBytesTTS, CartesiaRealtimeTTS
from .constants import (
    DEFAULT_TTS_BACKEND,
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
    volcengine_access_key: str | None = None,
) -> BackendTTSEngine:
    """按后端配置创建 TTS 引擎。"""
    backend = resolve_tts_backend(backend)
    engine_cls = TTS_ENGINE_REGISTRY[backend]

    # Volcengine：用 volcengine_access_key 作为主 api_key（X-Api-Key 鉴权）
    if backend == TTS_BACKEND_VOLCENGINE_STREAMING:
        return engine_cls(
            audio_player,
            api_key=volcengine_access_key or api_key,
            access_key=volcengine_access_key,
            voice_id=voice_id,
            volume=volume,
        )

    return engine_cls(audio_player, api_key=api_key, voice_id=voice_id, volume=volume)


def create_tts_engine_from_config(
    backend: str,
    audio_player: TTSAudioPlayer,
    config: object,
) -> BackendTTSEngine:
    """从 AppSettings 直接创建 TTS 引擎。

    从 config.tts_providers 读取 voice/backend，从 secret 读取密钥。
    """
    import wordy.secret as _secret

    backend = resolve_tts_backend(backend)
    engine_cls = TTS_ENGINE_REGISTRY[backend]
    provider = getattr(config, "active_tts_provider", "Cartesia")
    providers: dict = getattr(config, "tts_providers", {})
    provider_cfg = providers.get(provider, {})
    voice_id = provider_cfg.get("voice_id")
    volume = getattr(config, "volume", 1.0)

    if backend == TTS_BACKEND_VOLCENGINE_STREAMING:
        ve_key = _secret.load_volcengine_access_key()
        return engine_cls(audio_player, api_key=ve_key, access_key=ve_key, voice_id=voice_id, volume=volume)
    else:
        cartesia_key = _secret.load_cartesia_api_key()
        return engine_cls(audio_player, api_key=cartesia_key, voice_id=voice_id, volume=volume)
