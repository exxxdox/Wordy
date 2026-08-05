#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口数据类：SettingsState（输入）和 PendingSettings（输出）。"""

from __future__ import annotations

from dataclasses import dataclass, field

VoiceRecord = dict[str, object]
AudioOutputDevice = dict[str, object]
AudioOutputIdentity = dict[str, object]


@dataclass
class SettingsState:
    """打开设置窗口时传入的当前状态快照。"""

    hotkey: str
    hotkey_name: str
    voice_id: str | None
    voice_name: str | None
    volume: float
    overlay_opacity: float
    tts_backend: str
    fixed_center: bool
    voices_cache: list[VoiceRecord]
    voices_loading: bool
    voice_fetch_error: Exception | None
    cartesia_voice_id: str | None = None
    cartesia_voice_name: str | None = None
    volcengine_voice_id: str | None = None
    volcengine_voice_name: str | None = None
    audio_output_devices: list[AudioOutputDevice] | list[str] = field(default_factory=list)
    audio_output_device_name: str | None = None
    audio_output_device_identity: AudioOutputIdentity | None = None
    audio_output_devices_error: Exception | None = None
    tts_api_provider: str = "Cartesia"
    cartesia_api_key_saved: bool = False
    volcengine_access_key_saved: bool = False
    log_level: str = "INFO"
    # 音频路由状态
    audio_routing_enabled: bool = False
    input_devices: list[dict[str, object]] = field(default_factory=list)
    mic_input_device: str | None = None
    virtual_output_device: str | None = None
    vb_cable_installed: bool = False
    mic_gain: float = 1.0
    tts_gain: float = 1.0
    # 返听 (sidetone)
    sidetone_enabled: bool = False


@dataclass
class PendingSettings:
    """设置窗口关闭时输出的待应用设置。"""

    hotkey: str
    hotkey_name: str
    voice_id: str | None
    voice_name: str | None
    volume: float
    overlay_opacity: float
    tts_backend: str
    fixed_center: bool
    cartesia_voice_id: str | None = None
    cartesia_voice_name: str | None = None
    volcengine_voice_id: str | None = None
    volcengine_voice_name: str | None = None
    audio_output_device_name: str | None = None
    audio_output_device_identity: AudioOutputIdentity | None = None
    cartesia_api_key_action: str = "unchanged"
    cartesia_api_key_value: str | None = None
    tts_api_provider: str = "Cartesia"
    volcengine_access_key_action: str = "unchanged"
    volcengine_access_key_value: str | None = None
    log_level: str = "INFO"
    # 音频路由待应用配置
    audio_routing_enabled: bool = False
    mic_input_device: str | None = None
    virtual_output_device: str | None = None
    mic_gain: float = 1.0
    tts_gain: float = 1.0
    # 返听 (sidetone)
    sidetone_enabled: bool = False
