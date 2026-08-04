#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""应用配置：``AppSettings`` dataclass 统一管理所有持久化设置。

用法::

    settings = AppSettings.load()         # 启动时加载一次
    volume = settings.volume              # 属性读取（无磁盘 I/O）
    settings.update(volume=1.5)           # 单字段更新 + 自动持久化
    settings.save()                       # 显式持久化
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import ClassVar

import wordy.secret
from wordy.identity import AudioIdentity, normalize_identity
from wordy.hotkey import iter_hotkey_parts
from wordy.tts.constants import (
    DEFAULT_TTS_API_PROVIDER,
    DEFAULT_TTS_BACKEND,
    TTS_API_PROVIDERS,
)

# ---------------------------------------------------------------------------
# UI 常量 — settings_window.py / overlay.py 使用
# ---------------------------------------------------------------------------
MIN_VOLUME = 0.5
MAX_VOLUME = 2.0
VOLUME_STEP = 0.05
MIN_OVERLAY_OPACITY = 0.30
MAX_OVERLAY_OPACITY = 1.0
OVERLAY_OPACITY_STEP = 0.05
MIN_GAIN = 0.0
MAX_GAIN = 2.0
GAIN_STEP = 0.05
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# 向后兼容常量
DEFAULT_VOLUME = 1.0
DEFAULT_OVERLAY_OPACITY = 1.0
DEFAULT_FIXED_CENTER = True
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_HOTKEY: dict[str, str] = {"hotkey": "f6", "name": "F6"}

_USER_CONFIG_FILE: Path = Path.home() / ".wavtrans_config.json"
USER_CONFIG_FILE: Path = _USER_CONFIG_FILE  # 向后兼容：测试 monkeypatch 使用

# load() 缓存：同一配置文件多次调用复用同一实例，避免重复磁盘 I/O
_cached_settings: AppSettings | None = None
_cached_config_file: Path | None = None


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def get_active_config_file() -> Path:
    """返回用户级配置文件路径。"""
    return _USER_CONFIG_FILE


def display_hotkey(hotkey: str) -> str:
    """将 keyboard 包快捷键字符串格式化为用户可读名称。"""
    display_parts: list[str] = []
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


def _clamp(value: float, lo: float, hi: float) -> float:
    return min(max(float(value), lo), hi)


# ---------------------------------------------------------------------------
# AppSettings — 统一配置对象
# ---------------------------------------------------------------------------

@dataclass
class AppSettings:
    """所有应用设置的单一数据源。

    启动时 ``AppSettings.load()`` 一次，后续通过属性读取（零磁盘 I/O）。
    修改设置使用 ``update(**kwargs)`` 自动持久化。
    """

    # ---- 快捷键与界面 ----
    hotkey: str = "f6"
    name: str = "F6"  # JSON 持久化 key 为 "name"（非 hotkey_name）
    volume: float = 1.0
    overlay_opacity: float = 1.0
    fixed_center: bool = True
    log_level: str = "INFO"
    window_position: dict[str, int] | None = None

    # ---- 音频输出 ----
    audio_output_device_name: str | None = None
    audio_output_device: AudioIdentity | None = None

    # ---- TTS 引擎配置（结构化嵌套存储） ----
    # JSON: {"Cartesia": {...}, "Volcengine": {...}}
    tts_providers: dict[str, dict[str, object]] = field(default_factory=lambda: {
        "Cartesia": {"voice_id": None, "voice_name": None, "backend": "Cartesia Bytes",
                      "api_key_set": False, "api_key_storage": "none"},
        "Volcengine": {"voice_id": None, "voice_name": None, "backend": "Volcengine Streaming",
                        "api_key_set": False, "api_key_storage": "none"},
    })
    active_tts_provider: str = DEFAULT_TTS_API_PROVIDER

    # ── 向后兼容属性：从嵌套 dict 读取，旧代码无需修改 ──────────────

    @property
    def tts_backend(self) -> str:
        return str(self.tts_providers.get(self.active_tts_provider, {}).get("backend", DEFAULT_TTS_BACKEND))
    @tts_backend.setter
    def tts_backend(self, value: str) -> None:
        self.tts_providers.setdefault(self.active_tts_provider, {})["backend"] = value

    @property
    def tts_api_provider(self) -> str:
        return self.active_tts_provider
    @tts_api_provider.setter
    def tts_api_provider(self, value: str) -> None:
        self.active_tts_provider = value

    # -- Cartesia read/write
    @property
    def cartesia_voice_id(self) -> str | None:
        v = self.tts_providers.get("Cartesia", {}).get("voice_id"); return v if isinstance(v, str) else None
    @cartesia_voice_id.setter
    def cartesia_voice_id(self, value: str | None) -> None: self.tts_providers.setdefault("Cartesia", {})["voice_id"] = value

    @property
    def cartesia_voice_name(self) -> str | None:
        v = self.tts_providers.get("Cartesia", {}).get("voice_name"); return v if isinstance(v, str) else None
    @cartesia_voice_name.setter
    def cartesia_voice_name(self, value: str | None) -> None: self.tts_providers.setdefault("Cartesia", {})["voice_name"] = value

    @property
    def cartesia_tts_backend(self) -> str:
        return str(self.tts_providers.get("Cartesia", {}).get("backend", DEFAULT_TTS_BACKEND))
    @cartesia_tts_backend.setter
    def cartesia_tts_backend(self, value: str) -> None: self.tts_providers.setdefault("Cartesia", {})["backend"] = value

    @property
    def cartesia_api_key_set(self) -> bool:
        return bool(self.tts_providers.get("Cartesia", {}).get("api_key_set", False))
    @cartesia_api_key_set.setter
    def cartesia_api_key_set(self, value: bool) -> None: self.tts_providers.setdefault("Cartesia", {})["api_key_set"] = value

    @property
    def cartesia_api_key_storage(self) -> str:
        return str(self.tts_providers.get("Cartesia", {}).get("api_key_storage", "none"))
    @cartesia_api_key_storage.setter
    def cartesia_api_key_storage(self, value: str) -> None: self.tts_providers.setdefault("Cartesia", {})["api_key_storage"] = value

    # -- Volcengine read/write
    @property
    def volcengine_voice_id(self) -> str | None:
        v = self.tts_providers.get("Volcengine", {}).get("voice_id"); return v if isinstance(v, str) else None
    @volcengine_voice_id.setter
    def volcengine_voice_id(self, value: str | None) -> None: self.tts_providers.setdefault("Volcengine", {})["voice_id"] = value

    @property
    def volcengine_voice_name(self) -> str | None:
        v = self.tts_providers.get("Volcengine", {}).get("voice_name"); return v if isinstance(v, str) else None
    @volcengine_voice_name.setter
    def volcengine_voice_name(self, value: str | None) -> None: self.tts_providers.setdefault("Volcengine", {})["voice_name"] = value

    @property
    def volcengine_tts_backend(self) -> str:
        return str(self.tts_providers.get("Volcengine", {}).get("backend", "Volcengine Streaming"))
    @volcengine_tts_backend.setter
    def volcengine_tts_backend(self, value: str) -> None: self.tts_providers.setdefault("Volcengine", {})["backend"] = value

    @property
    def volcengine_access_key_set(self) -> bool:
        return bool(self.tts_providers.get("Volcengine", {}).get("api_key_set", False))
    @volcengine_access_key_set.setter
    def volcengine_access_key_set(self, value: bool) -> None: self.tts_providers.setdefault("Volcengine", {})["api_key_set"] = value

    @property
    def volcengine_access_key_storage(self) -> str:
        return str(self.tts_providers.get("Volcengine", {}).get("api_key_storage", "none"))
    @volcengine_access_key_storage.setter
    def volcengine_access_key_storage(self, value: str) -> None: self.tts_providers.setdefault("Volcengine", {})["api_key_storage"] = value

    # ---- 音频路由 ----
    audio_routing_enabled: bool = False
    mic_input_device: str | None = None
    virtual_output_device: str | None = None
    mic_gain: float = 1.0
    tts_gain: float = 1.0

    # ---- 返听 (sidetone) ----
    sidetone_enabled: bool = False

    # ---- 内部 ----
    _loaded: bool = field(default=False, init=False, repr=False)

    # ── 工厂方法 ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, *, config_file: Path | None = None) -> AppSettings:
        """从 JSON 文件加载配置，缺失时使用默认值。

        同一配置文件多次调用复用缓存实例，避免重复磁盘 I/O。
        """
        global _cached_settings, _cached_config_file
        file = config_file or USER_CONFIG_FILE  # 使用模块变量以支持 monkeypatch

        # 同一配置文件且已有有效缓存，直接返回
        if _cached_settings is not None and _cached_config_file == file and _cached_settings._loaded:
            return _cached_settings

        settings = cls()

        # 注入 Cartesia key 状态（不触碰磁盘密钥本身）
        key_status = _read_cartesia_key_status()
        settings.cartesia_api_key_set = bool(key_status["cartesia_api_key_set"])
        settings.cartesia_api_key_storage = str(key_status["cartesia_api_key_storage"])

        # 注入 Volcengine key 状态
        volc_status = _read_volcengine_key_status()
        settings.volcengine_access_key_set = bool(volc_status["volcengine_access_key_set"])
        settings.volcengine_access_key_storage = str(volc_status["volcengine_access_key_storage"])


        raw = _read_json(file)
        if raw is None:
            settings._loaded = True
            _cached_settings = settings
            _cached_config_file = file
            return settings

        # 逐字段解析
        _apply_if_present(raw, "volume", settings, _clamp, (MIN_VOLUME, MAX_VOLUME))
        _apply_if_present(raw, "overlay_opacity", settings, _clamp, (MIN_OVERLAY_OPACITY, MAX_OVERLAY_OPACITY))
        _apply_if_present(raw, "fixed_center", settings, lambda v: v if isinstance(v, bool) else None)

        # 结构化 tts_providers
        raw_providers = raw.get("tts_providers")
        if isinstance(raw_providers, dict):
            for provider_name, provider_data in raw_providers.items():
                if isinstance(provider_data, dict) and provider_name in settings.tts_providers:
                    target = settings.tts_providers[provider_name]
                    for k in ("voice_id", "voice_name", "backend", "api_key_set", "api_key_storage"):
                        if k in provider_data:
                            target[k] = provider_data[k]
        # active_tts_provider
        _apply_if_present(raw, "active_tts_provider", settings, lambda v: v if isinstance(v, str) and v in TTS_API_PROVIDERS else None)
        _apply_if_present(raw, "log_level", settings, lambda v: v.strip().upper() if isinstance(v, str) and v.strip().upper() in LOG_LEVELS else None)
        _apply_if_present(raw, "window_position", settings, _parse_window_pos)
        _apply_if_present(raw, "audio_output_device_name", settings, _nonempty_str)
        _apply_if_present(raw, "audio_output_device", settings, _parse_audio_output_device)
        _apply_if_present(raw, "audio_routing_enabled", settings, lambda v: v if isinstance(v, bool) else None)
        _apply_if_present(raw, "sidetone_enabled", settings, lambda v: v if isinstance(v, bool) else None)

        for dev_key in ("mic_input_device", "virtual_output_device"):
            _apply_if_present(raw, dev_key, settings, _nonempty_str)

        for gain_key in ("mic_gain", "tts_gain"):
            _apply_if_present(raw, gain_key, settings, _parse_gain_value)

        if (hotkey := _nonempty_str(raw.get("hotkey"))) is not None:
            settings.hotkey = hotkey
            settings.name = _nonempty_str(raw.get("name")) or display_hotkey(hotkey)

        settings._loaded = True
        _cached_settings = settings
        _cached_config_file = file
        return settings

    # ── 持久化 ─────────────────────────────────────────────────────────

    def save(self, *, config_file: Path | None = None) -> Path:
        """将当前设置写入 JSON 文件（原子写入）。

        写入后清空缓存，确保下次 load() 重新读磁盘并走字段验证逻辑。
        """
        global _cached_settings, _cached_config_file
        file = config_file or USER_CONFIG_FILE
        data: dict[str, object] = {}
        for f in fields(self):
            if f.name.startswith("_"):
                continue
            value = getattr(self, f.name)
            if value is not None or f.default is not None:
                # 始终写入所有非 None 字段 + 默认非 None 字段
                pass
            data[f.name] = value
        _write_json_atomic(file, data)
        # 清空缓存；但当前实例仍是有效缓存——如果 config_file 匹配则恢复
        if _cached_config_file == file:
            _cached_settings = None
            _cached_config_file = None
        return file

    _CLAMP_RANGES: ClassVar[dict[str, tuple[float, float]]] = {
        "volume": (MIN_VOLUME, MAX_VOLUME),
        "overlay_opacity": (MIN_OVERLAY_OPACITY, MAX_OVERLAY_OPACITY),
        "mic_gain": (MIN_GAIN, MAX_GAIN),
        "tts_gain": (MIN_GAIN, MAX_GAIN),
    }

    def update(self, *, config_file: Path | None = None, **kwargs: object) -> Path:
        """部分更新设置并持久化。float 类型字段自动钳位到有效范围。

        用法: ``settings.update(volume=1.5, hotkey="f9", name="F9")``
        """
        for key, value in kwargs.items():
            if key.startswith("_") or not hasattr(self, key):
                continue
            if key in self._CLAMP_RANGES and isinstance(value, (int, float)):
                lo, hi = self._CLAMP_RANGES[key]
                value = _clamp(float(value), lo, hi)
            setattr(self, key, value)
        return self.save(config_file=config_file)


# ---------------------------------------------------------------------------
# JSON ↔ 字段解析辅助
# ---------------------------------------------------------------------------

def _read_json(path: Path) -> dict[str, object] | None:
    """读取并校验 JSON 文件，失败返回 None。"""
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data: object = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): v for k, v in data.items()}


def _write_json_atomic(path: Path, data: dict[str, object]) -> None:
    """原子写入 JSON：temp → flush → fsync → replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".atomic_wavtrans_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _nonempty_str(value: object) -> str | None:
    """非空字符串，否则 None。"""
    if not isinstance(value, str):
        return None
    s = value.strip()
    return s if s else None


def _parse_window_pos(raw: object) -> dict[str, int] | None:
    if not isinstance(raw, Mapping):
        return None
    x = raw.get("x")
    y = raw.get("y")
    if isinstance(x, int) and isinstance(y, int):
        return {"x": x, "y": y}
    return None


def _parse_audio_output_device(raw: object) -> AudioIdentity | None:
    if not isinstance(raw, Mapping):
        return None
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip():
        return None
    host_raw = raw.get("host_api_name") if "host_api_name" in raw else None
    host: str | None = None
    if host_raw is not None:
        if isinstance(host_raw, str):
            host = host_raw.strip() or None
        else:
            return None
    return normalize_identity(name.strip(), host)


def _parse_gain_value(value: object) -> float | None:
    """解析增益值，钳位到 [0, 2]。"""
    if not isinstance(value, (int, float)):
        return None
    return _clamp(float(value), MIN_GAIN, MAX_GAIN)


def _apply_if_present(
    raw: dict[str, object],
    key: str,
    settings: AppSettings,
    parser,  # Callable[[object], T | None]
    parser_args: tuple = (),
) -> None:
    """如果 JSON 中存在 key 且解析成功，则覆盖 settings 对应字段。"""
    if key not in raw:
        return
    parsed = parser(raw[key], *parser_args) if parser_args else parser(raw[key])
    if parsed is not None:
        setattr(settings, key, parsed)


def _read_cartesia_key_status() -> dict[str, bool | str]:
    """读取 Cartesia API key 元数据（不暴露密钥原文）。"""
    status_getter = getattr(wordy.secret, "get_cartesia_api_key_status", None)
    if callable(status_getter):
        raw_status = status_getter()
        status = raw_status if isinstance(raw_status, Mapping) else {}
    else:
        try:
            storage_status = wordy.secret.get_storage_status()
        except Exception:
            storage_status = {}
        if isinstance(storage_status, Mapping):
            raw_backend = storage_status.get("backend", wordy.secret.STORAGE_NONE)
        else:
            raw_backend = getattr(storage_status, "backend", wordy.secret.STORAGE_NONE)
        storage = raw_backend if isinstance(raw_backend, str) else wordy.secret.STORAGE_NONE
        status = {
            "cartesia_api_key_set": storage != wordy.secret.STORAGE_NONE,
            "cartesia_api_key_storage": storage,
        }

    key_set = status.get("cartesia_api_key_set")
    storage = status.get("cartesia_api_key_storage")
    return {
        "cartesia_api_key_set": key_set if isinstance(key_set, bool) else False,
        "cartesia_api_key_storage": storage if isinstance(storage, str) else wordy.secret.STORAGE_NONE,
    }


def _read_volcengine_key_status() -> dict[str, bool | str]:
    """读取 Volcengine access key 元数据（不暴露密钥原文）。"""
    status_getter = getattr(wordy.secret, "get_volcengine_access_key_status", None)
    if callable(status_getter):
        raw_status = status_getter()
        status = raw_status if isinstance(raw_status, Mapping) else {}
    else:
        try:
            storage_status = wordy.secret.get_volcengine_storage_status()
        except Exception:
            storage_status = {}
        if isinstance(storage_status, Mapping):
            raw_backend = storage_status.get("backend", wordy.secret.STORAGE_NONE)
        else:
            raw_backend = getattr(storage_status, "backend", wordy.secret.STORAGE_NONE)
        storage = raw_backend if isinstance(raw_backend, str) else wordy.secret.STORAGE_NONE
        status = {
            "volcengine_access_key_set": storage != wordy.secret.STORAGE_NONE,
            "volcengine_access_key_storage": storage,
        }

    key_set = status.get("volcengine_access_key_set")
    storage = status.get("volcengine_access_key_storage")
    return {
        "volcengine_access_key_set": key_set if isinstance(key_set, bool) else False,
        "volcengine_access_key_storage": storage if isinstance(storage, str) else wordy.secret.STORAGE_NONE,
    }


