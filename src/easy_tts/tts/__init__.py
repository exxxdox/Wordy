#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS 后端：Cartesia 引擎、注册表、语音标签。"""

from easy_tts.tts.engine import TTSAudioPlayer, BackendTTSEngine
from easy_tts.tts.registry import resolve_tts_backend, create_tts_engine
from easy_tts.tts.labels import VoiceLabelMaps, build_voice_label_maps

__all__ = [
    "TTSAudioPlayer",
    "BackendTTSEngine",
    "resolve_tts_backend",
    "create_tts_engine",
    "VoiceLabelMaps",
    "build_voice_label_maps",
]
