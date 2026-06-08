#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音色 combobox 标签与反查映射构造，独立于具体 TTS 后端。"""

from dataclasses import dataclass

from .tts_engine import VoiceInfo


@dataclass
class VoiceLabelMaps:
    labels: list[str]
    label_to_id: dict[str, str]
    label_to_name: dict[str, str]
    selected_label: str | None


def build_voice_label_maps(voices: list[VoiceInfo], selected_voice_id: str | None) -> VoiceLabelMaps:
    """构造音色 combobox 标签和反查映射。"""
    labels: list[str] = []
    seen_voice_names: set[str] = set()
    label_to_id: dict[str, str] = {}
    label_to_name: dict[str, str] = {}
    selected_label: str | None = None

    for voice in voices:
        voice_id = voice["id"]
        name = voice["name"]
        if not isinstance(voice_id, str) or not isinstance(name, str):
            continue
        label = name if name not in seen_voice_names else f"{name} ({voice_id[:8]})"
        labels.append(label)
        seen_voice_names.add(name)
        label_to_id[label] = voice_id
        label_to_name[label] = name
        if voice_id == selected_voice_id:
            selected_label = label

    return VoiceLabelMaps(
        labels=labels,
        label_to_id=label_to_id,
        label_to_name=label_to_name,
        selected_label=selected_label,
    )
