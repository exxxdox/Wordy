#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口运行时状态。仅传递非持久化的动态数据（硬件枚举、密钥状态等）。"""

from __future__ import annotations

from dataclasses import dataclass, field

VoiceRecord = dict[str, object]
AudioOutputDevice = dict[str, object]
AudioOutputIdentity = dict[str, object]


@dataclass
class SettingsState:
    """打开设置窗口时传入的运行时状态快照（非持久化数据）。

    可持久化的字段（hotkey、volume、tts_backend 等）直接从
    ``AppSettings.load()`` 读取，不再通过此结构传递。
    """

    # 音频设备枚举（运行时硬件扫描结果）
    audio_output_devices: list[AudioOutputDevice] | list[str] = field(default_factory=list)
    audio_output_devices_error: Exception | None = None
    input_devices: list[dict[str, object]] = field(default_factory=list)

    # 音色缓存（从 TTS API 拉取，非持久化）
    voices_cache: list[VoiceRecord] = field(default_factory=list)
    voices_loading: bool = False
    voice_fetch_error: Exception | None = None

    # 密钥存储状态（从 keyring 探测，非持久化）
    cartesia_api_key_saved: bool = False
    volcengine_access_key_saved: bool = False

    # 驱动检测（运行时扫描）
    vb_cable_installed: bool = False
    # 麦克风侦听运行时状态
    mic_listen_configured: bool = False
