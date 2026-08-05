#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""应用配置：``AppSettings`` dataclass 统一管理所有持久化设置。

用法::

    settings = AppSettings.load()         # 启动时加载一次（单例）
    volume = settings.volume              # 属性读取（无磁盘 I/O）
    settings.volume = 1.5                 # 直接赋值 → 自动持久化到 TOML
    settings.flush()                      # 显式刷新待写入的 debounced 变更
"""

from __future__ import annotations

import os
import tempfile
import threading
import tomllib
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
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

# 向后兼容常量
DEFAULT_VOLUME = 1.0
DEFAULT_OVERLAY_OPACITY = 1.0
DEFAULT_FIXED_CENTER = True
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_HOTKEY: dict[str, str] = {"hotkey": "f6", "name": "F6"}

_USER_CONFIG_FILE: Path = Path.home() / ".wordy.toml"
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
    直接赋值属性（``settings.volume = 1.5``）自动持久化到 TOML 文件。
    """

    # ---- 快捷键与界面 ----
    hotkey: str = "f6"
    name: str = "F6"  # TOML 持久化 key 为 "name"（非 hotkey_name）
    volume: float = 1.0
    overlay_opacity: float = 1.0
    fixed_center: bool = True
    log_level: str = "INFO"
    window_position: dict[str, int] | None = None

    # ---- 音频输出 ----
    audio_output_device_name: str | None = None
    audio_output_device: AudioIdentity | None = None

    # ---- TTS 引擎配置（结构化嵌套存储） ----
    # TOML: [tts_providers.Cartesia] / [tts_providers.Volcengine]
    tts_providers: dict[str, dict[str, object]] = field(default_factory=lambda: {
        "Cartesia": {"voice_id": None, "voice_name": None, "backend": "Cartesia Bytes",
                      "api_key_set": False, "api_key_storage": "none"},
        "Volcengine": {"voice_id": None, "voice_name": None, "backend": "Volcengine Streaming",
                        "api_key_set": False, "api_key_storage": "none"},
    })
    active_tts_provider: str = DEFAULT_TTS_API_PROVIDER

    # ---- 音频路由 ----
    audio_routing_enabled: bool = False
    mic_input_device: str | None = None
    virtual_output_device: str | None = None

    # ---- 返听 (sidetone) ----
    sidetone_enabled: bool = False

    # ---- 内部 ----
    _loaded: bool = field(default=False, init=False, repr=False)
    _config_file: Path | None = field(default=None, init=False, repr=False)
    _listeners: list = field(default_factory=list, init=False, repr=False)
    _save_timer: threading.Timer | None = field(default=None, init=False, repr=False)
    _save_lock: ClassVar[threading.Lock] = threading.Lock()

    # 滑块类字段用 debounce，其他字段立即写入
    _DEBOUNCED_FIELDS: ClassVar[set[str]] = {"volume", "overlay_opacity"}

    _CLAMP_RANGES: ClassVar[dict[str, tuple[float, float]]] = {
        "volume": (MIN_VOLUME, MAX_VOLUME),
        "overlay_opacity": (MIN_OVERLAY_OPACITY, MAX_OVERLAY_OPACITY),
    }

    # ── ProviderConfig ────────────────────────────────────────────────

    def get_provider(self, name: str | None = None) -> dict[str, object]:
        """读取 provider 配置 dict。name=None 返回当前 active 的。"""
        key = name if name is not None else self.active_tts_provider
        return self.tts_providers.setdefault(key, {})

    def active_provider_config(self) -> dict[str, object]:
        """当前活跃 provider 的配置。"""
        return self.get_provider(self.active_tts_provider)

    # ── 向后兼容属性：委托给 get_provider ────────────────────────────

    @property
    def tts_backend(self) -> str:
        return str(self.active_provider_config().get("backend", DEFAULT_TTS_BACKEND))
    @tts_backend.setter
    def tts_backend(self, value: str) -> None:
        self.active_provider_config()["backend"] = value
        if self._loaded:
            self._notify_listeners("tts_backend", value)
            self._debounced_save()

    @property
    def tts_api_provider(self) -> str:
        return self.active_tts_provider
    @tts_api_provider.setter
    def tts_api_provider(self, value: str) -> None:
        self.active_tts_provider = value
        # 赋值 self.active_tts_provider 触发 __setattr__，无需手动 save

    # -- Cartesia read/write
    @property
    def cartesia_voice_id(self) -> str | None:
        v = self.get_provider("Cartesia").get("voice_id"); return v if isinstance(v, str) else None
    @cartesia_voice_id.setter
    def cartesia_voice_id(self, value: str | None) -> None:
        self.get_provider("Cartesia")["voice_id"] = value
        if self._loaded:
            self._notify_listeners("cartesia_voice_id", value)
            self._debounced_save()

    @property
    def cartesia_voice_name(self) -> str | None:
        v = self.get_provider("Cartesia").get("voice_name"); return v if isinstance(v, str) else None
    @cartesia_voice_name.setter
    def cartesia_voice_name(self, value: str | None) -> None:
        self.get_provider("Cartesia")["voice_name"] = value
        if self._loaded:
            self._notify_listeners("cartesia_voice_name", value)
            self._debounced_save()

    @property
    def cartesia_tts_backend(self) -> str:
        return str(self.get_provider("Cartesia").get("backend", DEFAULT_TTS_BACKEND))
    @cartesia_tts_backend.setter
    def cartesia_tts_backend(self, value: str) -> None:
        self.get_provider("Cartesia")["backend"] = value
        if self._loaded:
            self._notify_listeners("cartesia_tts_backend", value)
            self._debounced_save()

    @property
    def cartesia_api_key_set(self) -> bool:
        return bool(self.get_provider("Cartesia").get("api_key_set", False))
    @cartesia_api_key_set.setter
    def cartesia_api_key_set(self, value: bool) -> None:
        self.get_provider("Cartesia")["api_key_set"] = value
        if self._loaded:
            self._notify_listeners("cartesia_api_key_set", value)
            self._debounced_save()

    @property
    def cartesia_api_key_storage(self) -> str:
        return str(self.get_provider("Cartesia").get("api_key_storage", "none"))
    @cartesia_api_key_storage.setter
    def cartesia_api_key_storage(self, value: str) -> None:
        self.get_provider("Cartesia")["api_key_storage"] = value
        if self._loaded:
            self._notify_listeners("cartesia_api_key_storage", value)
            self._debounced_save()

    # -- Volcengine read/write
    @property
    def volcengine_voice_id(self) -> str | None:
        v = self.get_provider("Volcengine").get("voice_id"); return v if isinstance(v, str) else None
    @volcengine_voice_id.setter
    def volcengine_voice_id(self, value: str | None) -> None:
        self.get_provider("Volcengine")["voice_id"] = value
        if self._loaded:
            self._notify_listeners("volcengine_voice_id", value)
            self._debounced_save()

    @property
    def volcengine_voice_name(self) -> str | None:
        v = self.get_provider("Volcengine").get("voice_name"); return v if isinstance(v, str) else None
    @volcengine_voice_name.setter
    def volcengine_voice_name(self, value: str | None) -> None:
        self.get_provider("Volcengine")["voice_name"] = value
        if self._loaded:
            self._notify_listeners("volcengine_voice_name", value)
            self._debounced_save()

    @property
    def volcengine_tts_backend(self) -> str:
        return str(self.get_provider("Volcengine").get("backend", "Volcengine Streaming"))
    @volcengine_tts_backend.setter
    def volcengine_tts_backend(self, value: str) -> None:
        self.get_provider("Volcengine")["backend"] = value
        if self._loaded:
            self._notify_listeners("volcengine_tts_backend", value)
            self._debounced_save()

    @property
    def volcengine_access_key_set(self) -> bool:
        return bool(self.get_provider("Volcengine").get("api_key_set", False))
    @volcengine_access_key_set.setter
    def volcengine_access_key_set(self, value: bool) -> None:
        self.get_provider("Volcengine")["api_key_set"] = value
        if self._loaded:
            self._notify_listeners("volcengine_access_key_set", value)
            self._debounced_save()

    @property
    def volcengine_access_key_storage(self) -> str:
        return str(self.get_provider("Volcengine").get("api_key_storage", "none"))
    @volcengine_access_key_storage.setter
    def volcengine_access_key_storage(self, value: str) -> None:
        self.get_provider("Volcengine")["api_key_storage"] = value
        if self._loaded:
            self._notify_listeners("volcengine_access_key_storage", value)
            self._debounced_save()

    # ── 工厂方法 ──────────────────────────────────────────────────────

    @classmethod
    def load(cls, *, config_file: Path | None = None) -> AppSettings:
        """从 TOML 文件加载配置，缺失时使用默认值。

        同一配置文件多次调用复用缓存实例，避免重复磁盘 I/O。
        """
        global _cached_settings, _cached_config_file
        file = config_file or USER_CONFIG_FILE  # 使用模块变量以支持 monkeypatch

        # 同一配置文件且已有有效缓存，直接返回
        if _cached_settings is not None and _cached_config_file == file and _cached_settings._loaded:
            return _cached_settings

        settings = cls()
        settings._config_file = file  # 记住配置文件路径，后续 save() 默认使用
        raw = _read_toml(file)
        if raw is None:
            _inject_key_status(settings)
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

        if (hotkey := _nonempty_str(raw.get("hotkey"))) is not None:
            settings.hotkey = hotkey
            settings.name = _nonempty_str(raw.get("name")) or display_hotkey(hotkey)

        _inject_key_status(settings)
        settings._loaded = True
        _cached_settings = settings
        _cached_config_file = file
        return settings

    # ── __setattr__ 自动保存 ──────────────────────────────────────────

    def __setattr__(self, name: str, value: object) -> None:
        """属性赋值时自动持久化（_loaded 之后）。

        私有字段（``_`` 前缀）直接赋值不触发保存。
        滑块类字段走 debounce，其他字段立即写入。
        """
        # 内部/私有字段：直接赋值，不触发保存
        if name.startswith("_"):
            object.__setattr__(self, name, value)
            return
        # float 范围字段：clamp
        if name in self._CLAMP_RANGES and isinstance(value, (int, float)):
            lo, hi = self._CLAMP_RANGES[name]
            value = _clamp(float(value), lo, hi)
        object.__setattr__(self, name, value)
        if self._loaded:
            self._notify_listeners(name, value)
            if name in self._DEBOUNCED_FIELDS:
                self._debounced_save()
            else:
                self.save()

    # ── 监听器 ─────────────────────────────────────────────────────────

    def add_listener(self, cb) -> None:
        """注册字段变更监听器 ``cb(field_name, value)``。"""
        self._listeners.append(cb)

    def remove_listener(self, cb) -> bool:
        """移除监听器。返回 True 表示成功移除。"""
        try:
            self._listeners.remove(cb)
            return True
        except ValueError:
            return False

    def _notify_listeners(self, name: str, value: object) -> None:
        """通知所有监听器某字段已变更。"""
        for cb in self._listeners:
            try:
                cb(name, value)
            except Exception:
                pass  # 监听器异常不应影响保存流程

    # ── 去抖动保存 ────────────────────────────────────────────────────

    def _debounced_save(self) -> None:
        """150ms 去抖动：滑块快速拖拽只写一次磁盘。"""
        if self._save_timer is not None:
            self._save_timer.cancel()
        self._save_timer = threading.Timer(0.15, self._do_save)
        self._save_timer.daemon = True
        self._save_timer.start()

    def flush(self) -> None:
        """取消 pending timer 并立即写入磁盘（对话框关闭/应用退出时调用）。"""
        if self._save_timer is not None:
            self._save_timer.cancel()
            self._save_timer = None
        self.save()

    def _do_save(self) -> None:
        """Timer 回调：在独立线程中执行写入。"""
        with self._save_lock:
            self._save_timer = None
            self._save_to_disk_inner()

    # ── 持久化 ─────────────────────────────────────────────────────────

    def save(self, *, config_file: Path | None = None) -> Path:
        """将当前设置写入 TOML 文件（原子写入）。

        不清空缓存——in-memory 实例始终是最新的。
        返回写入的文件路径。
        """
        file = config_file or self._config_file or USER_CONFIG_FILE
        self._save_to_disk_inner(file)
        return file

    def _save_to_disk_inner(self, config_file: Path | None = None) -> None:
        """内部写入逻辑（不加锁，调用方负责加锁）。"""
        file = config_file or self._config_file or USER_CONFIG_FILE
        data: dict[str, object] = {}
        for f in fields(self):
            if f.name.startswith("_"):
                continue
            data[f.name] = getattr(self, f.name)
        _write_toml_atomic(file, data)

    # ── 批量更新（保留兼容）───────────────────────────────────────────

    def update(self, *, config_file: Path | None = None, **kwargs: object) -> Path:
        """批量更新设置并一次性持久化（不会逐字段触发 debounce）。

        用法: ``settings.update(volume=1.5, hotkey="f9")``
        """
        old_loaded = self._loaded
        self._loaded = False  # 抑制逐字段 __setattr__ 保存
        try:
            for key, value in kwargs.items():
                if key.startswith("_") or not hasattr(self, key):
                    continue
                if key in self._CLAMP_RANGES and isinstance(value, (int, float)):
                    lo, hi = self._CLAMP_RANGES[key]
                    value = _clamp(float(value), lo, hi)
                setattr(self, key, value)
        finally:
            self._loaded = old_loaded
        # 批量通知（一次性）
        for key, value in kwargs.items():
            if not key.startswith("_") and hasattr(self, key):
                self._notify_listeners(key, value)
        return self.save(config_file=config_file)


def _inject_key_status(settings: AppSettings) -> None:
    """从 live keyring 注入密钥状态，覆盖 TOML 中可能已过期的旧值。"""
    for provider, status_fn in (("Cartesia", wordy.secret.get_cartesia_api_key_status), ("Volcengine", wordy.secret.get_volcengine_access_key_status)):
        try:
            s = status_fn()
        except Exception:
            s = {}
        settings.tts_providers.setdefault(provider, {})["api_key_set"] = bool(s.get("cartesia_api_key_set", s.get("volcengine_access_key_set", s.get("key_set", False))))
        settings.tts_providers.setdefault(provider, {})["api_key_storage"] = str(s.get("cartesia_api_key_storage", s.get("volcengine_access_key_storage", s.get("storage", "none"))))


# ---------------------------------------------------------------------------
# TOML 读写辅助
# ---------------------------------------------------------------------------

def _read_toml(path: Path) -> dict[str, object] | None:
    """读取并校验 TOML 文件，失败返回 None。"""
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            data: object = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): v for k, v in data.items()}


def _write_toml_atomic(path: Path, data: dict[str, object]) -> None:
    """原子写入 TOML：temp → flush → fsync → replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".atomic_wordy_", suffix=".toml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(_to_toml(data))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ---------------------------------------------------------------------------
# 手写 TOML 序列化器（零依赖，替代 tomli-w）
# ---------------------------------------------------------------------------

def _to_toml(data: dict[str, object]) -> str:
    """将扁平 + 一级嵌套 dict 序列化为 TOML 字符串。"""
    lines: list[str] = []
    nested: dict[str, dict[str, object]] = {}
    for key, value in data.items():
        if isinstance(value, dict) and not _is_inline_table(value):
            nested[key] = value
        elif value is not None:
            lines.append(f"{key} = {_toml_value(value)}")
    for table_name, table_data in nested.items():
        lines.append("")
        lines.append(f"[{table_name}]")
        for k, v in table_data.items():
            if v is not None:
                lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def _is_inline_table(value: dict[str, object]) -> bool:
    """判断 dict 是否应为 TOML 内联表（小、扁平、无嵌套 dict 值）。"""
    if len(value) > 3:
        return False
    for v in value.values():
        if isinstance(v, dict):
            return False
    return True


def _toml_value(value: object) -> str:
    """将单个 Python 值转为 TOML 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        # 转义反斜杠和双引号
        escaped = value.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if isinstance(value, dict):
        # 内联表：{name = "Speakers", host_api_name = "MME"}
        items = ", ".join(f"{k} = {_toml_value(v)}"
                          for k, v in value.items() if v is not None)
        return "{" + items + "}"
    if value is None:
        return '""'  # TOML 无 null，写入空字符串作为标记
    return f'"{value}"'


# ---------------------------------------------------------------------------
# 字段解析辅助
# ---------------------------------------------------------------------------

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


def _apply_if_present(
    raw: dict[str, object],
    key: str,
    settings: AppSettings,
    parser,  # Callable[[object], T | None]
    parser_args: tuple = (),
) -> None:
    """如果 TOML 中存在 key 且解析成功，则覆盖 settings 对应字段。"""
    if key not in raw:
        return
    parsed = parser(raw[key], *parser_args) if parser_args else parser(raw[key])
    if parsed is not None:
        setattr(settings, key, parsed)


# ---------------------------------------------------------------------------
# 密钥状态读取（保留兼容）
# ---------------------------------------------------------------------------

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
