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

import math
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import ClassVar, TypedDict

import wordy.secret
from wordy._toml_serializer import read_toml, write_toml_atomic
from wordy.hotkey import display_hotkey
from wordy.identity import AudioIdentity, normalize_identity
from wordy.paths import config_directory
from wordy.tts.constants import (
    DEFAULT_TTS_API_PROVIDER,
    DEFAULT_TTS_BACKEND,
    TTS_API_PROVIDERS,
    TTS_BACKENDS_BY_PROVIDER,
)
from wordy.constants import (
    LOG_LEVELS,
    MAX_OVERLAY_OPACITY,
    MAX_VOLUME,
    MIN_OVERLAY_OPACITY,
    MIN_VOLUME,
)

_USER_CONFIG_FILE: Path = config_directory() / "config.toml"
USER_CONFIG_FILE: Path = _USER_CONFIG_FILE  # 测试 monkeypatch 使用
CHAT_WHEEL_STYLES = ("classic", "glass", "petals")

# load() 缓存：同一配置文件多次调用复用同一实例，避免重复磁盘 I/O
_cached_settings: AppSettings | None = None
_cached_config_file: Path | None = None


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------

def _clamp(value: float, lo: float, hi: float) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("配置数值必须是有限值")
    return min(max(result, lo), hi)


def _parse_clamped(value: object, lo: float, hi: float) -> float | None:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return _clamp(value, lo, hi)
    except ValueError:
        return None


def _normalize_voice_entries(raw: object) -> list[dict[str, str]]:
    """把配置里的音色列表收拾成 ``[{"id": ..., "name": ...}]``。

    配置可能被手改或来自旧版本，所以逐条校验：``id`` 必须是非空字符串，
    同 id 只留第一条；``name`` 缺失或不是字符串时用 id 兜底——下拉列表按名称
    做标签，没有名称的条目会在 ``build_voice_label_maps`` 里被静默丢掉。
    """
    if not isinstance(raw, list):
        return []

    entries: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        voice_id = item.get("id")
        if not isinstance(voice_id, str):
            continue
        voice_id = voice_id.strip()
        if not voice_id or voice_id in seen:
            continue
        raw_name = item.get("name")
        name = raw_name.strip() if isinstance(raw_name, str) else ""
        seen.add(voice_id)
        entries.append({"id": voice_id, "name": name or voice_id})
    return entries


class ChatWheelConfig(TypedDict):
    name: str
    hotkey: str
    slots: list[str]
    labels: list[str]
    texts: list[str]
    volumes: list[float]


# 自定义名称是记忆标签，不是数据键：限制长度避免撑破设置页的轮盘选择行。
WHEEL_NAME_MAX_LENGTH = 20


def _parse_wheel_name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    return value.strip()[:WHEEL_NAME_MAX_LENGTH]


def new_chat_wheel(hotkey: str = "f8", name: str = "") -> ChatWheelConfig:
    return {"name": name, "hotkey": hotkey, "slots": [""] * 8, "labels": [""] * 8,
            "texts": [""] * 8, "volumes": [1.0] * 8}


def _parse_wheel_volumes(raw: object) -> list[float]:
    # 手改配置中的非法增益回到原音量，缺少的槽位不能误变成静音。
    values = raw[:8] if isinstance(raw, list) else []
    clean = [_parse_clamped(value, 0.0, 2.0) for value in values]
    return [value if value is not None else 1.0 for value in clean] + [1.0] * (8 - len(clean))


def _parse_chat_wheel(raw: object) -> ChatWheelConfig | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("hotkey"), str):
        return None
    from wordy.hotkey.native import parse_hotkey
    try:
        parse_hotkey(raw["hotkey"])
    except ValueError:
        return None
    wheel = new_chat_wheel(raw["hotkey"])
    name = _parse_wheel_name(raw.get("name"))
    if name is not None:
        wheel["name"] = name
    for key in ("slots", "labels", "texts"):
        values = raw.get(key)
        if isinstance(values, list):
            clean = [value if isinstance(value, str) and (key != "slots" or
                     not any(c in value for c in ("/", "\\", ":")) and value not in (".", ".."))
                     else "" for value in values[:8]]
            wheel[key] = clean + [""] * (8 - len(clean))
    wheel["volumes"] = _parse_wheel_volumes(raw.get("volumes"))
    return wheel


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
    # 固定八槽与显示名称分开，托管文件改名不影响用户轮盘标签。
    # 首盘的自定义名称；空字段回退显示「轮盘 1」。
    chat_wheel_name: str = ""
    chat_wheel_hotkey: str = "f8"
    # 全部轮盘共用外观；缺省保留原有样式，不因升级改变用户视觉习惯。
    chat_wheel_style: str = "classic"
    chat_wheel_slots: list[str] = field(default_factory=lambda: [""] * 8)
    chat_wheel_labels: list[str] = field(default_factory=lambda: [""] * 8)
    # 与文件名互斥：非空则选中该槽时用当前 TTS 朗读这段文字，而不是播放音频。
    chat_wheel_texts: list[str] = field(default_factory=lambda: [""] * 8)
    chat_wheel_volumes: list[float] = field(default_factory=lambda: [1.0] * 8)
    additional_chat_wheels: list[ChatWheelConfig] = field(default_factory=list)
    volume: float = 1.0
    overlay_opacity: float = 1.0
    overlay_placeholder: str = "Hello World~"
    # 界面首选字体族；空 = 用 theme.UI_FONT_FAMILIES 内置栈。
    # 只影响 QSS 界面（设置窗口、悬浮输入框、托盘菜单）。
    ui_font: str = ""
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
        "Volcengine": {"voice_list": [], "backend": "Volcengine Streaming",
                        "api_key_set": False, "api_key_storage": "none"},
    })
    active_tts_provider: str = DEFAULT_TTS_API_PROVIDER

    # ---- 音频侦听 ----
    audio_routing_enabled: bool = False
    mic_input_device: str | None = None
    virtual_output_device: str | None = None

    # ---- 返听 (sidetone) ----
    sidetone_enabled: bool = False

    # ---- 内部 ----
    _loaded: bool = field(default=False, init=False, repr=False)
    _config_file: Path | None = field(default=None, init=False, repr=False)
    _save_timer: threading.Timer | None = field(default=None, init=False, repr=False)
    _save_error: Exception | None = field(default=None, init=False, repr=False)
    _save_lock: ClassVar[threading.Lock] = threading.Lock()

    # 滑块类字段用 debounce，其他字段立即写入
    # 提示文字逐字编辑时也合并保存，避免每次按键都在 UI 线程写磁盘。
    _DEBOUNCED_FIELDS: ClassVar[set[str]] = {"volume", "overlay_opacity", "overlay_placeholder"}

    _CLAMP_RANGES: ClassVar[dict[str, tuple[float, float]]] = {
        "volume": (MIN_VOLUME, MAX_VOLUME),
        "overlay_opacity": (MIN_OVERLAY_OPACITY, MAX_OVERLAY_OPACITY),
    }

    @property
    def wheel_count(self) -> int:
        return 1 + len(self.additional_chat_wheels)

    def get_wheel(self, index: int) -> ChatWheelConfig:
        if not 0 <= index < self.wheel_count:
            raise IndexError("轮盘不存在")
        if index:
            return self.additional_chat_wheels[index - 1]
        return {"name": self.chat_wheel_name, "hotkey": self.chat_wheel_hotkey,
                "slots": self.chat_wheel_slots, "labels": self.chat_wheel_labels,
                "texts": self.chat_wheel_texts, "volumes": self.chat_wheel_volumes}

    def replace_wheels(self, wheels: list[ChatWheelConfig]) -> None:
        if not wheels:
            raise ValueError("至少保留一个轮盘")
        first = wheels[0]
        old = {key: getattr(self, key) for key in (
            "chat_wheel_name", "chat_wheel_hotkey", "chat_wheel_slots", "chat_wheel_labels",
            "chat_wheel_texts", "chat_wheel_volumes", "additional_chat_wheels")}
        try:
            # 首盘和其余轮盘在同一事务写入，删除首盘也不会错位。
            self.update(chat_wheel_name=first["name"], chat_wheel_hotkey=first["hotkey"],
                        chat_wheel_slots=first["slots"],
                        chat_wheel_labels=first["labels"], chat_wheel_texts=first["texts"],
                        chat_wheel_volumes=first["volumes"],
                        additional_chat_wheels=wheels[1:])
        except Exception:
            for key, value in old.items():
                object.__setattr__(self, key, value)
            raise

    def update_wheel(self, index: int, **values: object) -> None:
        self.get_wheel(index)
        wheels = [dict(self.get_wheel(i)) for i in range(self.wheel_count)]
        wheels[index].update(values)
        parsed = [_parse_chat_wheel(wheel) for wheel in wheels]
        if any(wheel is None for wheel in parsed):
            raise ValueError("轮盘配置无效")
        self.replace_wheels([wheel for wheel in parsed if wheel is not None])

    def add_wheel(self, hotkey: str) -> int:
        wheels = [self.get_wheel(i) for i in range(self.wheel_count)]
        wheel = _parse_chat_wheel(new_chat_wheel(hotkey))
        if wheel is None:
            raise ValueError("轮盘快捷键无效")
        self.replace_wheels(wheels + [wheel])
        return len(wheels)

    def remove_wheel(self, index: int) -> None:
        self.get_wheel(index)
        self.replace_wheels([self.get_wheel(i) for i in range(self.wheel_count) if i != index])

    # ── ProviderConfig ────────────────────────────────────────────────

    def get_provider(self, name: str | None = None) -> dict[str, object]:
        """读取 provider 配置 dict。name=None 返回当前 active 的。"""
        key = name if name is not None else self.active_tts_provider
        return self.tts_providers.setdefault(key, {})

    def active_provider_config(self) -> dict[str, object]:
        """当前活跃 provider 的配置。"""
        return self.get_provider(self.active_tts_provider)

    # ── Provider 字段写入辅助 ──────────────────────────────────────────

    def _set_provider_field(self, provider: str, key: str, value: object) -> None:
        """写入 provider 子 dict 中的字段，并触发 notify + debounce save。"""
        self.get_provider(provider)[key] = value
        if self._loaded:
            self._debounced_save()

    # ── 向后兼容属性：委托给 get_provider ────────────────────────────

    @property
    def tts_backend(self) -> str:
        return str(self.active_provider_config().get("backend", DEFAULT_TTS_BACKEND))
    @tts_backend.setter
    def tts_backend(self, value: str) -> None:
        self.active_provider_config()["backend"] = value
        if self._loaded:
            self._debounced_save()

    @property
    def tts_api_provider(self) -> str:
        return self.active_tts_provider
    @tts_api_provider.setter
    def tts_api_provider(self, value: str) -> None:
        self.active_tts_provider = value

    # -- Cartesia
    @property
    def cartesia_voice_id(self) -> str | None:
        v = self.get_provider("Cartesia").get("voice_id"); return v if isinstance(v, str) else None
    @cartesia_voice_id.setter
    def cartesia_voice_id(self, value: str | None) -> None: self._set_provider_field("Cartesia", "voice_id", value)

    @property
    def cartesia_voice_name(self) -> str | None:
        v = self.get_provider("Cartesia").get("voice_name"); return v if isinstance(v, str) else None
    @cartesia_voice_name.setter
    def cartesia_voice_name(self, value: str | None) -> None: self._set_provider_field("Cartesia", "voice_name", value)

    @property
    def cartesia_tts_backend(self) -> str:
        return str(self.get_provider("Cartesia").get("backend", TTS_BACKENDS_BY_PROVIDER["Cartesia"][0]))
    @cartesia_tts_backend.setter
    def cartesia_tts_backend(self, value: str) -> None:
        if value not in TTS_BACKENDS_BY_PROVIDER["Cartesia"]:
            value = TTS_BACKENDS_BY_PROVIDER["Cartesia"][0]
        self._set_provider_field("Cartesia", "backend", value)

    @property
    def cartesia_api_key_set(self) -> bool:
        return bool(self.get_provider("Cartesia").get("api_key_set", False))
    @cartesia_api_key_set.setter
    def cartesia_api_key_set(self, value: bool) -> None: self._set_provider_field("Cartesia", "api_key_set", value)

    @property
    def cartesia_api_key_storage(self) -> str:
        return str(self.get_provider("Cartesia").get("api_key_storage", "none"))
    @cartesia_api_key_storage.setter
    def cartesia_api_key_storage(self, value: str) -> None: self._set_provider_field("Cartesia", "api_key_storage", value)

    # -- Volcengine
    # 火山音色没有独立的选择字段：列表顺序就是选择，首条即当前音色。
    @property
    def volcengine_voice_id(self) -> str | None:
        entries = self.volcengine_voice_list
        return entries[0]["id"] if entries else None

    @property
    def volcengine_voice_name(self) -> str | None:
        entries = self.volcengine_voice_list
        return entries[0]["name"] if entries else None

    @property
    def volcengine_voice_list(self) -> list[dict[str, str]]:
        """用户手动维护的火山引擎音色列表（id + 显示名），纯本地，不联网拉取。"""
        return _normalize_voice_entries(self.get_provider("Volcengine").get("voice_list"))
    @volcengine_voice_list.setter
    def volcengine_voice_list(self, value: list[dict[str, str]]) -> None:
        self._set_provider_field("Volcengine", "voice_list", _normalize_voice_entries(value))

    @property
    def volcengine_tts_backend(self) -> str:
        return str(self.get_provider("Volcengine").get("backend", "Volcengine Streaming"))
    @volcengine_tts_backend.setter
    def volcengine_tts_backend(self, value: str) -> None:
        if value not in TTS_BACKENDS_BY_PROVIDER["Volcengine"]:
            value = TTS_BACKENDS_BY_PROVIDER["Volcengine"][0]
        self._set_provider_field("Volcengine", "backend", value)

    @property
    def volcengine_access_key_set(self) -> bool:
        return bool(self.get_provider("Volcengine").get("api_key_set", False))
    @volcengine_access_key_set.setter
    def volcengine_access_key_set(self, value: bool) -> None: self._set_provider_field("Volcengine", "api_key_set", value)

    @property
    def volcengine_access_key_storage(self) -> str:
        return str(self.get_provider("Volcengine").get("api_key_storage", "none"))
    @volcengine_access_key_storage.setter
    def volcengine_access_key_storage(self, value: str) -> None: self._set_provider_field("Volcengine", "api_key_storage", value)

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
        raw = read_toml(file)
        if raw is None:
            _inject_key_status(settings)
            settings._loaded = True
            _cached_settings = settings
            _cached_config_file = file
            return settings

        # 逐字段解析
        _apply_if_present(raw, "volume", settings, _parse_clamped, (MIN_VOLUME, MAX_VOLUME))
        _apply_if_present(raw, "overlay_opacity", settings, _parse_clamped, (MIN_OVERLAY_OPACITY, MAX_OVERLAY_OPACITY))
        # 空字符串表示不显示提示；旧配置缺少字段时保留默认文字。
        _apply_if_present(raw, "overlay_placeholder", settings, lambda v: v if isinstance(v, str) else None)
        # 界面首选字体族。空串是合法值（= 用内置字体栈），别用 _nonempty_str 滤掉。
        _apply_if_present(raw, "ui_font", settings, lambda v: v.strip() if isinstance(v, str) else None)
        _apply_if_present(raw, "fixed_center", settings, lambda v: v if isinstance(v, bool) else None)
        from wordy.hotkey.native import parse_hotkey
        # 自定义名称：空串是合法值（回退「轮盘 N」），不能用非空校验滤掉。
        _apply_if_present(raw, "chat_wheel_name", settings, _parse_wheel_name)
        # 读取与直接赋值共用 __setattr__ 校验，未知或损坏的样式回退原有外观。
        setattr(settings, "chat_wheel_style", raw.get("chat_wheel_style", "classic"))
        wheel_hotkey = raw.get("chat_wheel_hotkey")
        if isinstance(wheel_hotkey, str):
            try:
                parse_hotkey(wheel_hotkey)
                settings.chat_wheel_hotkey = wheel_hotkey
            except ValueError:
                pass
        for key in ("chat_wheel_slots", "chat_wheel_labels", "chat_wheel_texts"):
            values = raw.get(key)
            if isinstance(values, list):
                # 文件名只允许目录内单个文件，拒绝配置中的绝对路径和目录穿越；
                # 标签与朗读文字是自由文本，只要求是字符串。
                clean = [v if isinstance(v, str) and (key != "chat_wheel_slots" or
                         ("/" not in v and "\\" not in v and ":" not in v and v not in (".", "..")))
                         else "" for v in values[:8]]
                setattr(settings, key, clean + [""] * (8 - len(clean)))

        settings.chat_wheel_volumes = _parse_wheel_volumes(raw.get("chat_wheel_volumes"))
        raw_wheels = raw.get("additional_chat_wheels")
        if isinstance(raw_wheels, list):
            settings.additional_chat_wheels = [wheel for item in raw_wheels
                                               if (wheel := _parse_chat_wheel(item)) is not None]

        # 结构化 tts_providers
        raw_providers = raw.get("tts_providers")
        if isinstance(raw_providers, dict):
            for provider_name, provider_data in raw_providers.items():
                if isinstance(provider_data, dict) and provider_name in settings.tts_providers:
                    target = settings.tts_providers[provider_name]
                    # 白名单必须与持久化的 provider 字段保持一致：漏掉的键会被静默丢弃，
                    # 表现就是「设置里改了、重启后没了」。Volcengine 只认 voice_list，
                    # 旧的 voice_id/voice_name 不再读入；列表读取时由 getter 统一清洗。
                    provider_keys = ("backend", "api_key_set", "api_key_storage")
                    if provider_name == "Volcengine":
                        provider_keys += ("voice_list",)
                    else:
                        provider_keys += ("voice_id", "voice_name")
                    for k in provider_keys:
                        if k in provider_data:
                            value = provider_data[k]
                            if k == "backend" and value not in TTS_BACKENDS_BY_PROVIDER[provider_name]:
                                continue
                            target[k] = value
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
        if name == "chat_wheel_style" and (not isinstance(value, str) or value not in CHAT_WHEEL_STYLES):
            value = "classic"
        # float 范围字段：clamp
        if name in self._CLAMP_RANGES and isinstance(value, (int, float)):
            lo, hi = self._CLAMP_RANGES[name]
            value = _clamp(float(value), lo, hi)
        old_value = getattr(self, name, None)
        object.__setattr__(self, name, value)
        if self._loaded:
            if name in self._DEBOUNCED_FIELDS:
                self._debounced_save()
            else:
                try:
                    self.save()
                except Exception:
                    # 立即保存失败时内存必须回到磁盘仍代表的旧值。
                    object.__setattr__(self, name, old_value)
                    raise


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
            try:
                self._save_to_disk_inner()
                self._save_error = None
            except Exception as error:
                # Timer 线程不能操作 Qt；flush 会重试并把持续错误交回 GUI 调用方。
                self._save_error = error

    # ── 持久化 ─────────────────────────────────────────────────────────

    def save(self, *, config_file: Path | None = None) -> Path:
        """将当前设置写入 TOML 文件（原子写入，线程安全）。

        不清空缓存——in-memory 实例始终是最新的。
        返回写入的文件路径。
        """
        file = config_file or self._config_file or USER_CONFIG_FILE
        with self._save_lock:
            self._save_to_disk_inner(file)
            self._save_error = None
        return file

    def _save_to_disk_inner(self, config_file: Path | None = None) -> None:
        """内部写入逻辑（不加锁，调用方负责加锁）。"""
        file = config_file or self._config_file or USER_CONFIG_FILE
        data: dict[str, object] = {}
        for f in fields(self):
            if f.name.startswith("_"):
                continue
            data[f.name] = getattr(self, f.name)
        write_toml_atomic(file, data)

    # ── 批量更新（保留兼容）───────────────────────────────────────────

    def update(self, *, config_file: Path | None = None, **kwargs: object) -> Path:
        """批量更新设置并一次性持久化（不会逐字段触发 debounce）。

        用法: ``settings.update(volume=1.5, hotkey="f9")``
        """
        old_loaded = self._loaded
        # 保存失败时内存必须回到磁盘仍代表的值，否则界面/引擎会与配置分叉。
        previous = {
            key: getattr(self, key)
            for key in kwargs
            if not key.startswith("_") and hasattr(self, key)
        }
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
        try:
            return self.save(config_file=config_file)
        except Exception:
            for key, value in previous.items():
                object.__setattr__(self, key, value)
            raise


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
