#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""应用配置读写和 TTS 后端常量。"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import NotRequired, TypedDict

import secret_store  # pyright: ignore[reportMissingImports]
from audio_identity import AudioIdentity, normalize_identity  # pyright: ignore[reportMissingImports]
from hotkey_utils import iter_hotkey_parts  # pyright: ignore[reportMissingImports]
from tts_backends.constants import DEFAULT_TTS_BACKEND, TTS_BACKENDS  # pyright: ignore[reportMissingImports]


class HotkeyConfig(TypedDict):
    """快捷键的字符串与展示名。"""

    hotkey: str
    name: str


class VoiceConfig(TypedDict):
    """已选 Cartesia 音色。"""

    voice_id: str
    voice_name: str


class StoredVoiceConfig(TypedDict):
    """`load_voice_config` 的返回结构，未配置音色时字段为 None。"""

    voice_id: str | None
    voice_name: str | None


class WindowPositionConfig(TypedDict):
    """输入框记忆位置。"""

    x: int
    y: int





class AppConfig(TypedDict):
    """`load_app_config` 返回的完整运行时配置。

    `voice_id`/`voice_name` 仅在用户已保存音色时出现，与原始 JSON 行为一致。
    """

    hotkey: str
    name: str
    volume: float
    overlay_opacity: float
    fixed_center: bool
    tts_backend: str
    log_level: str
    window_position: WindowPositionConfig | None
    audio_output_device_name: str | None
    audio_output_device: AudioIdentity | None
    cartesia_api_key_set: bool
    cartesia_api_key_storage: str
    voice_id: NotRequired[str]
    voice_name: NotRequired[str]


class InitialConfig(TypedDict):
    """`load_initial_config` 返回的启动配置。"""

    hotkey: str
    name: str
    voice_id: str | None
    voice_name: str | None
    volume: float
    overlay_opacity: float
    fixed_center: bool
    tts_backend: str
    log_level: str
    window_position: WindowPositionConfig | None
    audio_output_device_name: str | None
    audio_output_device: AudioIdentity | None
    cartesia_api_key_set: bool
    cartesia_api_key_storage: str


# 任意来自 JSON 文件的字典：键为 str，值类型未知，需要逐字段校验。
JsonConfig = Mapping[str, object]
ConfigUpdate = Mapping[str, object]
SECRET_CONFIG_FIELDS = frozenset({"cartesia_api_key", "api_key", "token", "secret"})


PROJECT_DIR = Path(__file__).resolve().parent
USER_CONFIG_FILE = Path.home() / ".wavtrans_config.json"
DEFAULT_HOTKEY: HotkeyConfig = {"hotkey": "f6", "name": "F6"}
DEFAULT_VOLUME = 1.0
DEFAULT_OVERLAY_OPACITY = 1.0
DEFAULT_FIXED_CENTER = True
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
DEFAULT_LOG_LEVEL = "INFO"
MIN_VOLUME = 0.5
MAX_VOLUME = 2.0
VOLUME_STEP = 0.05
MIN_OVERLAY_OPACITY = 0.30
MAX_OVERLAY_OPACITY = 1.0
OVERLAY_OPACITY_STEP = 0.05


def get_active_config_file() -> Path:
    """返回用户级配置文件路径。"""
    return USER_CONFIG_FILE


def display_hotkey(hotkey: str) -> str:
    """将 keyboard 包快捷键字符串格式化为用户可读名称。"""
    display_parts = []
    for _, key in iter_hotkey_parts(hotkey):
        if key in {"ctrl", "control"}:
            display_parts.append("Ctrl")
        elif key == "alt":
            display_parts.append("Alt")
        elif key == "shift":
            display_parts.append("Shift")
        elif key in {"windows", "win", "left windows", "right windows"}:
            display_parts.append("Win")
        elif key.startswith("f") and key[1:].isdigit():
            display_parts.append(key.upper())
        elif len(key) == 1:
            display_parts.append(key.upper())
        else:
            display_parts.append(" ".join(word.capitalize() for word in key.split()))

    return "+".join(display_parts) if display_parts else hotkey


def _nonempty_str(value: object) -> str | None:
    """返回非空字符串，否则返回 None。"""
    return value if isinstance(value, str) and value else None


def _clamp_volume(value: float) -> float:
    """限制音量到 [MIN_VOLUME, MAX_VOLUME] 范围。"""
    return min(max(float(value), MIN_VOLUME), MAX_VOLUME)


def _clamp_overlay_opacity(value: float) -> float:
    """限制输入框透明度到 [MIN_OVERLAY_OPACITY, MAX_OVERLAY_OPACITY] 范围。"""
    return min(max(float(value), MIN_OVERLAY_OPACITY), MAX_OVERLAY_OPACITY)


def parse_hotkey_config(config: JsonConfig) -> HotkeyConfig | None:
    """解析 keyboard 库格式的快捷键配置。"""
    hotkey = _nonempty_str(config.get("hotkey"))
    if hotkey is None:
        return None

    name = _nonempty_str(config.get("name")) or display_hotkey(hotkey)
    return {"hotkey": hotkey, "name": name}


def load_json_config(config_file: Path) -> JsonConfig | None:
    """读取 JSON 配置文件。"""
    if not config_file.exists():
        return None

    try:
        with config_file.open("r", encoding="utf-8") as f:
            config: object = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(config, dict):
        return None

    return {str(key): value for key, value in config.items()}


def parse_voice_config(config: JsonConfig) -> VoiceConfig | None:
    """解析音色配置。"""
    voice_id = _nonempty_str(config.get("voice_id"))
    if voice_id is None:
        return None

    voice_name = _nonempty_str(config.get("voice_name")) or voice_id
    return {"voice_id": voice_id, "voice_name": voice_name}


def parse_volume_config(config: JsonConfig) -> float:
    """解析音量配置并限制到 Cartesia 支持范围。"""
    volume = config.get("volume", DEFAULT_VOLUME)
    if not isinstance(volume, (int, float)):
        return DEFAULT_VOLUME

    return _clamp_volume(float(volume))


def parse_overlay_opacity_config(config: JsonConfig) -> float:
    """解析输入框透明度配置并限制到有效范围。"""
    opacity = config.get("overlay_opacity", DEFAULT_OVERLAY_OPACITY)
    if not isinstance(opacity, (int, float)):
        return DEFAULT_OVERLAY_OPACITY

    return _clamp_overlay_opacity(float(opacity))


def parse_fixed_center_config(config: JsonConfig) -> bool:
    """解析输入框是否固定居中显示。"""
    fixed_center = config.get("fixed_center", DEFAULT_FIXED_CENTER)
    return fixed_center if isinstance(fixed_center, bool) else DEFAULT_FIXED_CENTER


def parse_tts_backend_config(config: JsonConfig) -> str:
    """解析 TTS 模式配置。"""
    tts_backend = config.get("tts_backend")
    if isinstance(tts_backend, str) and tts_backend in TTS_BACKENDS:
        return tts_backend

    return DEFAULT_TTS_BACKEND


def parse_log_level_config(config: JsonConfig) -> str:
    """Parse the configured log display level."""
    log_level = config.get("log_level")
    if not isinstance(log_level, str):
        return DEFAULT_LOG_LEVEL

    normalized = log_level.strip().upper()
    if normalized in LOG_LEVELS:
        return normalized

    return DEFAULT_LOG_LEVEL


def parse_window_position_config(config: JsonConfig) -> WindowPositionConfig | None:
    """解析输入框记忆位置配置。"""
    raw_pos = config.get("window_position")
    if not isinstance(raw_pos, Mapping):
        return None

    x = raw_pos.get("x")
    y = raw_pos.get("y")
    if not isinstance(x, int) or not isinstance(y, int):
        return None

    return {"x": x, "y": y}


def parse_audio_output_device_name(config: JsonConfig) -> str | None:
    """解析音频输出设备名称，缺失/空串/非字符串返回 None。"""
    raw = config.get("audio_output_device_name")
    if not isinstance(raw, str):
        return None

    stripped = raw.strip()
    return stripped if stripped else None


def get_cartesia_api_key_status() -> dict[str, bool | str]:
    """返回 Cartesia API key 的元数据状态，不暴露原始密钥。"""
    status_getter = getattr(secret_store, "get_cartesia_api_key_status", None)
    if callable(status_getter):
        raw_status = status_getter()
        status = raw_status if isinstance(raw_status, Mapping) else {}
    else:
        try:
            storage_status = secret_store.get_storage_status()
        except Exception:
            storage_status = {}
        if isinstance(storage_status, Mapping):
            raw_storage = storage_status.get("backend", secret_store.STORAGE_NONE)
        else:
            raw_storage = getattr(storage_status, "backend", secret_store.STORAGE_NONE)
        storage_value = raw_storage if isinstance(raw_storage, str) else secret_store.STORAGE_NONE
        status = {
            "cartesia_api_key_set": storage_value != secret_store.STORAGE_NONE,
            "cartesia_api_key_storage": storage_value,
        }

    key_set = status.get("cartesia_api_key_set")
    storage = status.get("cartesia_api_key_storage")
    return {
        "cartesia_api_key_set": key_set if isinstance(key_set, bool) else False,
        "cartesia_api_key_storage": storage if isinstance(storage, str) else secret_store.STORAGE_NONE,
    }



def parse_audio_output_device(config: JsonConfig) -> AudioIdentity | None:
    """解析结构化的音频输出设备配置。

    要求 `audio_output_device` 是 mapping，包含非空字符串 `name`；
    `host_api_name` 必须缺失或为字符串/None，否则视为无效。
    """
    raw = config.get("audio_output_device")
    if not isinstance(raw, Mapping):
        return None

    name = raw.get("name")
    if not isinstance(name, str):
        return None
    stripped_name = name.strip()
    if not stripped_name:
        return None

    host_api_raw = raw.get("host_api_name") if "host_api_name" in raw else None
    if host_api_raw is None:
        host_api_name: str | None = None
    elif isinstance(host_api_raw, str):
        host_api_name = host_api_raw.strip() or None
    else:
        return None

    return normalize_identity(stripped_name, host_api_name)


def load_app_config() -> AppConfig:
    """从用户目录加载应用配置。"""
    key_status = get_cartesia_api_key_status()
    app_config: AppConfig = {
        "hotkey": DEFAULT_HOTKEY["hotkey"],
        "name": DEFAULT_HOTKEY["name"],
        "volume": DEFAULT_VOLUME,
        "overlay_opacity": DEFAULT_OVERLAY_OPACITY,
        "fixed_center": DEFAULT_FIXED_CENTER,
        "tts_backend": DEFAULT_TTS_BACKEND,
        "log_level": DEFAULT_LOG_LEVEL,
        "window_position": None,
        "audio_output_device_name": None,
        "audio_output_device": None,
        "cartesia_api_key_set": bool(key_status["cartesia_api_key_set"]),
        "cartesia_api_key_storage": str(key_status["cartesia_api_key_storage"]),
    }

    config = load_json_config(USER_CONFIG_FILE)
    if config is None:
        return app_config

    app_config["volume"] = parse_volume_config(config)
    app_config["overlay_opacity"] = parse_overlay_opacity_config(config)
    app_config["fixed_center"] = parse_fixed_center_config(config)
    app_config["tts_backend"] = parse_tts_backend_config(config)
    app_config["log_level"] = parse_log_level_config(config)
    app_config["window_position"] = parse_window_position_config(config)
    app_config["audio_output_device_name"] = parse_audio_output_device_name(config)
    app_config["audio_output_device"] = parse_audio_output_device(config)

    hotkey = parse_hotkey_config(config)
    if hotkey is not None:
        app_config["hotkey"] = hotkey["hotkey"]
        app_config["name"] = hotkey["name"]

    voice = parse_voice_config(config)
    if voice is not None:
        app_config["voice_id"] = voice["voice_id"]
        app_config["voice_name"] = voice["voice_name"]

    return app_config


def _write_json_config_atomic(config_file: Path, config: dict[str, object]) -> None:
    """Atomically write JSON config to file using temp + flush + fsync + replace.

    Ensures existing file is not corrupted if writing fails mid-way.
    """
    config_file.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=config_file.parent, prefix=".atomic_wavtrans_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, config_file)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def save_app_config(config_update: ConfigUpdate) -> Path:
    """合并保存应用配置到用户目录，并返回保存路径。"""
    config_file = get_active_config_file()
    merged: dict[str, object] = dict(load_app_config())
    for field in SECRET_CONFIG_FIELDS:
        merged.pop(field, None)
    merged.update(
        {
            key: value
            for key, value in config_update.items()
            if key not in SECRET_CONFIG_FIELDS
        }
    )

    _write_json_config_atomic(config_file, merged)

    return config_file


def load_hotkey_config() -> HotkeyConfig:
    """加载快捷键配置。"""
    config = load_app_config()
    return {"hotkey": config["hotkey"], "name": config["name"]}


def save_hotkey_config(hotkey: str, name: str) -> Path:
    """保存快捷键配置到用户目录，并返回保存路径。"""
    return save_app_config({"hotkey": hotkey, "name": name})


def load_voice_config() -> StoredVoiceConfig:
    """加载 JSON 中保存的音色配置。"""
    config = load_app_config()
    voice_id = config.get("voice_id")
    voice_name = config.get("voice_name")
    if voice_id is None:
        return {"voice_id": None, "voice_name": None}
    return {"voice_id": voice_id, "voice_name": voice_name}


def save_voice_config(voice_id: str, voice_name: str) -> Path:
    """保存音色配置到用户目录，并返回保存路径。"""
    return save_app_config({"voice_id": voice_id, "voice_name": voice_name})


def load_volume_config() -> float:
    """加载音量配置。"""
    return load_app_config()["volume"]


def save_volume_config(volume: float) -> Path:
    """保存音量配置到用户目录，并返回保存路径。"""
    return save_app_config({"volume": _clamp_volume(volume)})


def load_tts_backend_config() -> str:
    """从用户配置加载 TTS 模式。"""
    return load_app_config()["tts_backend"]


def save_tts_backend_config(tts_backend: str) -> Path:
    """保存 TTS 模式配置到用户目录，并返回保存路径。"""
    if tts_backend not in TTS_BACKENDS:
        raise ValueError(f"TTS 模式必须是 {', '.join(sorted(TTS_BACKENDS))}")

    return save_app_config({"tts_backend": tts_backend})


def load_log_level_config() -> str:
    """Load the configured log display level."""
    return load_app_config()["log_level"]


def save_log_level_config(log_level: str) -> Path:
    """Save the log display level."""
    normalized = log_level.strip().upper()
    if normalized not in LOG_LEVELS:
        raise ValueError(f"log_level must be one of {', '.join(LOG_LEVELS)}")

    return save_app_config({"log_level": normalized})


def load_audio_output_device_name() -> str | None:
    """加载音频输出设备名称。"""
    return load_app_config()["audio_output_device_name"]


def save_audio_output_device_name(name: str | None) -> Path:
    """保存音频输出设备名称到用户目录，并返回保存路径。"""
    if name is None:
        return save_app_config({"audio_output_device_name": None})

    stripped = name.strip()
    value: str | None = stripped if stripped else None
    return save_app_config({"audio_output_device_name": value})


def load_audio_output_device() -> AudioIdentity | None:
    """加载结构化的音频输出设备配置。

    优先返回 `audio_output_device` 对象；若缺失或非法，则回退到
    `audio_output_device_name` 字符串构造 `{name, host_api_name: None}`。
    完全缺失时返回 None。
    """
    config = load_json_config(USER_CONFIG_FILE)
    if config is None:
        return None

    # 显式存储但格式非法时，不再回退到 legacy 字符串。
    if "audio_output_device" in config:
        return parse_audio_output_device(config)

    legacy_name = parse_audio_output_device_name(config)
    if legacy_name is None:
        return None
    return {"name": legacy_name, "host_api_name": None}


def save_audio_output_device(device: AudioIdentity | None) -> Path:
    """保存结构化音频输出设备配置，同步写入 legacy 名称字段。

    传入 None 时清空 `audio_output_device` 与 `audio_output_device_name`。
    """
    if device is None:
        return save_app_config(
            {"audio_output_device": None, "audio_output_device_name": None}
        )

    name = device["name"].strip()
    host_api_raw = device.get("host_api_name")
    host_stripped = host_api_raw.strip() if isinstance(host_api_raw, str) else None
    structured = normalize_identity(name, host_stripped)
    return save_app_config(
        {
            "audio_output_device": dict(structured),
            "audio_output_device_name": name if name else None,
        }
    )


def load_initial_config() -> InitialConfig:
    """一次性加载启动所需配置。"""
    config = load_app_config()

    return {
        "hotkey": config["hotkey"],
        "name": config["name"],
        "voice_id": config.get("voice_id"),
        "voice_name": config.get("voice_name"),
        "volume": config["volume"],
        "overlay_opacity": config["overlay_opacity"],
        "fixed_center": config["fixed_center"],
        "tts_backend": config["tts_backend"],
        "log_level": config["log_level"],
        "window_position": config["window_position"],
        "audio_output_device_name": config["audio_output_device_name"],
        "audio_output_device": config["audio_output_device"],
        "cartesia_api_key_set": config["cartesia_api_key_set"],
        "cartesia_api_key_storage": config["cartesia_api_key_storage"],
    }
