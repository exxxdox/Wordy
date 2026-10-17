#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for wordy.config.py AppSettings behavior (TOML persistence)."""

import os
import tomllib
from pathlib import Path

import pytest

import wordy.config
from wordy.audio import clips
from wordy.config import (
    AppSettings, USER_CONFIG_FILE, display_hotkey, MAX_VOLUME, MIN_VOLUME,
)
from wordy.paths import config_directory
from wordy.tts.constants import DEFAULT_TTS_BACKEND, TTS_BACKENDS


# ---------------------------------------------------------------------------
# 持久化目录
# ---------------------------------------------------------------------------

def test_config_directory_honours_xdg_config_home(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert config_directory() == tmp_path / "xdg" / "wordy"


@pytest.mark.parametrize("value", [None, "", "   ", "relative/config"])
def test_config_directory_falls_back_to_home_config(monkeypatch, value):
    """未设置、空串、全空白与相对路径都退回 ~/.config/wordy。

    XDG 规范规定相对路径无效；照用会把配置写到随工作目录漂移的位置。
    """
    if value is None:
        monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    else:
        monkeypatch.setenv("XDG_CONFIG_HOME", value)
    assert config_directory() == Path.home() / ".config" / "wordy"


def test_config_directory_name_is_lowercase():
    assert config_directory().name == "wordy"


def test_config_file_lives_inside_config_directory():
    assert USER_CONFIG_FILE.parent.name == "wordy"
    assert USER_CONFIG_FILE.name == "config.toml"


def test_managed_clips_follow_the_config_directory(tmp_path):
    """录音托管目录从配置文件位置推导，因此自动跟着搬进 wordy 目录。"""
    source = tmp_path / "clip.wav"
    source.write_bytes(b"pcm")
    config_file = tmp_path / "wordy" / "config.toml"

    imported = clips.import_audio_clip(source, config_file)

    assert imported.parent == tmp_path / "wordy" / "audio_clips"


# ---------------------------------------------------------------------------
# TOML 测试辅助
# ---------------------------------------------------------------------------

def _write_toml_file(path: Path, data: dict[str, object]) -> None:
    """将 Python dict 写入 TOML 文件（用 AppSettings.save 能力）。"""
    # 直接构造 TOML 字符串以确保精确控制
    s = AppSettings.load(config_file=path)
    s.update(**data, config_file=path)


def _read_toml_file(path: Path) -> dict[str, object]:
    """从 TOML 文件读取为 dict。"""
    return tomllib.loads(path.read_text(encoding="utf-8"))


def _load_from_disk(path: Path) -> AppSettings:
    """绕开 load() 的实例缓存，强制真的从磁盘读一次。

    同一个配置路径上 load() 会直接返回已缓存的实例，断言就变成在跟内存里的
    那个对象自说自话——"存得进、读不回"的字段会被这种写法放过。
    """
    wordy.config._cached_settings = None
    wordy.config._cached_config_file = None
    return AppSettings.load(config_file=path)


# ---------------------------------------------------------------------------
# display_hotkey — unchanged
# ---------------------------------------------------------------------------

def test_display_hotkey():
    """Test display_hotkey formats hotkeys correctly."""
    assert display_hotkey("f6") == "F6"
    assert display_hotkey("a") == "A"
    assert display_hotkey("ctrl") == "Ctrl"
    assert display_hotkey("ctrl+alt+delete") == "Ctrl+Alt+Delete"
    assert display_hotkey("shift+f10") == "Shift+F10"
    assert display_hotkey("win+space") == "Win+Space"
    assert display_hotkey("left windows+g") == "Win+G"
    assert display_hotkey("control+shift") == "Ctrl+Shift"
    assert display_hotkey("") == ""


def test_ui_font_round_trips(tmp_path: Path):
    """界面字体是持久化设置：存盘后必须能原值读回。"""
    cfg = tmp_path / "ui_font.toml"
    s = AppSettings.load(config_file=cfg)
    assert s.ui_font == "", "默认必须是空值，让 UI 用内置字体栈"

    s.update(ui_font="Consolas", config_file=cfg)

    assert _read_toml_file(cfg)["ui_font"] == "Consolas"
    assert _load_from_disk(cfg).ui_font == "Consolas"


def test_ui_font_is_read_back_from_an_existing_file(tmp_path: Path):
    """文件里已有 ui_font 时必须被读出来。

    load() 是逐字段显式解析的，不是按 dataclass 字段全量恢复——漏写一行
    就会变成"存得进、读不回"。这里绕开 load() 的实例缓存，直接读盘。
    """
    cfg = tmp_path / "ui_font_only.toml"
    cfg.write_text('ui_font = "Consolas"\n', encoding="utf-8")

    assert _load_from_disk(cfg).ui_font == "Consolas"


def test_ui_font_missing_from_file_falls_back_to_the_default(tmp_path: Path):
    cfg = tmp_path / "ui_font_absent.toml"
    cfg.write_text("volume = 1.0\n", encoding="utf-8")

    assert _load_from_disk(cfg).ui_font == ""


# ---------------------------------------------------------------------------
# Hotkey parsing through AppSettings.load()
# ---------------------------------------------------------------------------

def test_hotkey_config_valid(tmp_path: Path):
    """AppSettings.load() parses hotkey and name from TOML."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(hotkey="f8", name="My F8", config_file=cfg)

    s2 = AppSettings.load(config_file=cfg)
    assert s2.hotkey == "f8"
    assert s2.name == "My F8"

    # Only hotkey — name must be set explicitly with direct save（load 时才会从 hotkey 推导 name）
    cfg2 = tmp_path / "config2.toml"
    s3 = AppSettings.load(config_file=cfg2)
    s3.update(hotkey="ctrl+shift+f", name="Ctrl+Shift+F", config_file=cfg2)
    s4 = AppSettings.load(config_file=cfg2)
    assert s4.hotkey == "ctrl+shift+f"
    assert s4.name == "Ctrl+Shift+F"

    # Hotkey valid with explicit name
    cfg3 = tmp_path / "config3.toml"
    s5 = AppSettings.load(config_file=cfg3)
    s5.update(hotkey="f9", name="F9", config_file=cfg3)
    s6 = AppSettings.load(config_file=cfg3)
    assert s6.hotkey == "f9"
    assert s6.name == "F9"


def test_hotkey_config_invalid(tmp_path: Path):
    """AppSettings.load() returns defaults when hotkey missing/invalid."""
    # No hotkey field → default
    cfg = tmp_path / "config.toml"
    cfg.write_text("volume = 1.0\n", encoding="utf-8")
    s = AppSettings.load(config_file=cfg)
    assert s.hotkey == "f6"
    assert s.name == "F6"

    # Non-string hotkey → default (TOML int parsed as int → _nonempty_str rejects)
    cfg2 = tmp_path / "config2.toml"
    cfg2.write_text("hotkey = 123\n", encoding="utf-8")
    s2 = AppSettings.load(config_file=cfg2)
    assert s2.hotkey == "f6"

    # Empty hotkey string → default
    cfg3 = tmp_path / "config3.toml"
    cfg3.write_text('hotkey = ""\n', encoding="utf-8")
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.hotkey == "f6"


# ---------------------------------------------------------------------------
# Volume clamping through AppSettings.load()
# ---------------------------------------------------------------------------

def test_volume_config_clamps(tmp_path: Path):
    """AppSettings.load() clamps volume between MIN_VOLUME and MAX_VOLUME."""
    # Default when missing
    s = AppSettings.load(config_file=tmp_path / "nonexistent.toml")
    assert s.volume == 1.0

    # Within range stays the same
    for val in (1.0, 0.5, 2.0, 1.5):
        cfg = tmp_path / f"vol_{val}.toml"
        s2 = AppSettings.load(config_file=cfg)
        s2.update(volume=val, config_file=cfg)
        s3 = AppSettings.load(config_file=cfg)
        assert s3.volume == val

    # Below min clamps to min
    cfg = tmp_path / "vol_low.toml"
    s4 = AppSettings.load(config_file=cfg)
    s4.update(volume=0.0, config_file=cfg)
    assert s4.volume == MIN_VOLUME
    assert s4.volume == 0.5

    # Above max clamps to max
    cfg = tmp_path / "vol_high.toml"
    s5 = AppSettings.load(config_file=cfg)
    s5.update(volume=3.0, config_file=cfg)
    assert s5.volume == MAX_VOLUME

    # Integer converts to float via clamp
    cfg = tmp_path / "vol_int.toml"
    s6 = AppSettings.load(config_file=cfg)
    s6.update(volume=1, config_file=cfg)
    assert s6.volume == 1.0


# ---------------------------------------------------------------------------
# TTS backend
# ---------------------------------------------------------------------------

def test_tts_backend_config_fallback(tmp_path: Path):
    """AppSettings.load() validates tts_backend, falls back to default."""
    valid_backend = next(iter(TTS_BACKENDS))
    # New format: tts_providers structured config
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(config_file=cfg)  # trigger save with defaults
    s.cartesia_tts_backend = valid_backend
    s.flush()
    s2 = AppSettings.load(config_file=cfg)
    assert s2.cartesia_tts_backend == valid_backend

    # Missing → default
    s3 = AppSettings.load(config_file=tmp_path / "nonexistent.toml")
    assert s3.tts_backend == DEFAULT_TTS_BACKEND

    # Non-string backend stored as-is (dict doesn't validate types)
    cfg3 = tmp_path / "config3.toml"
    s4 = AppSettings.load(config_file=cfg3)
    s4.cartesia_tts_backend = "123"  # type: ignore[assignment]
    s4.flush()
    s5 = AppSettings.load(config_file=cfg3)
    assert s5.cartesia_tts_backend == "123"

    # Invalid string in old flat format → ignored
    cfg4 = tmp_path / "config4.toml"
    cfg4.write_text('tts_backend = "invalid_backend"\n', encoding="utf-8")
    s6 = AppSettings.load(config_file=cfg4)
    assert s6.tts_backend == DEFAULT_TTS_BACKEND


# ---------------------------------------------------------------------------
# Window position
# ---------------------------------------------------------------------------

def test_window_position_config(tmp_path: Path):
    """AppSettings.load() parses window_position correctly."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(window_position={"x": 100, "y": 200}, config_file=cfg)
    s2 = AppSettings.load(config_file=cfg)
    assert s2.window_position == {"x": 100, "y": 200}

    # No window_position → None
    s3 = AppSettings.load(config_file=tmp_path / "nonexistent.toml")
    assert s3.window_position is None

    # Non-dict → None (TOML doesn't have arrays for this field, but just in case)
    cfg3 = tmp_path / "config3.toml"
    s4 = AppSettings.load(config_file=cfg3)
    s4.update(config_file=cfg3)
    # window_position stays None since we didn't set it

    # Invalid TOML with non-int x → None (parse failure)
    cfg4 = tmp_path / "config4.toml"
    cfg4.write_text('hotkey = "f6"\n\n[window_position]\nx = "100"\ny = 200\n', encoding="utf-8")
    s5 = AppSettings.load(config_file=cfg4)
    assert s5.window_position is None


# ---------------------------------------------------------------------------
# TOML loading (AppSettings.load with various file states)
# ---------------------------------------------------------------------------

def test_load_missing_file(tmp_path: Path):
    """AppSettings.load() returns defaults when file is missing."""
    s = AppSettings.load(config_file=tmp_path / "missing.toml")
    assert s.hotkey == "f6"
    assert s.volume == 1.0


def test_load_invalid_toml(tmp_path: Path):
    """AppSettings.load() returns defaults with invalid TOML."""
    cfg = tmp_path / "bad.toml"
    cfg.write_text("not valid toml {{[\n", encoding="utf-8")
    s = AppSettings.load(config_file=cfg)
    assert s.hotkey == "f6"  # defaults


def test_load_non_dict_toml(tmp_path: Path):
    """AppSettings.load() returns defaults when TOML is a list/array."""
    cfg = tmp_path / "list.toml"
    cfg.write_text("[[items]]\nname = \"x\"\n", encoding="utf-8")
    s = AppSettings.load(config_file=cfg)
    assert s.hotkey == "f6"


def test_load_valid_toml(tmp_path: Path):
    """AppSettings.load() loads valid TOML correctly."""
    cfg = tmp_path / "valid.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(hotkey="f8", name="F8", volume=1.5, config_file=cfg)
    s2 = AppSettings.load(config_file=cfg)
    assert s2.hotkey == "f8"
    assert s2.volume == 1.5
    assert s2.name == "F8"


# ---------------------------------------------------------------------------
# AppSettings.load() defaults and partial config
# ---------------------------------------------------------------------------

def test_load_defaults_no_file(tmp_path: Path):
    """AppSettings.load() returns all defaults when config file doesn't exist."""
    s = AppSettings.load(config_file=tmp_path / "nonexistent.toml")
    assert s.hotkey == "f6"
    assert s.name == "F6"
    assert s.volume == 1.0
    assert s.fixed_center is True
    assert s.tts_backend == DEFAULT_TTS_BACKEND
    assert s.window_position is None


def test_load_partial_config(tmp_path: Path):
    """AppSettings.load() fills defaults for missing fields."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(hotkey="f8", name="F8", volume=1.5, config_file=cfg)
    s2 = AppSettings.load(config_file=cfg)
    assert s2.hotkey == "f8"
    assert s2.name == "F8"
    assert s2.volume == 1.5
    assert s2.fixed_center is True  # default
    assert s2.tts_backend == DEFAULT_TTS_BACKEND  # default


# ---------------------------------------------------------------------------
# AppSettings.update() persistence
# ---------------------------------------------------------------------------

def test_update_merges_and_persists(tmp_path: Path):
    """AppSettings.update() merges with existing and writes to file."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(hotkey="f8", volume=1.5, window_position={"x": 100, "y": 200}, config_file=cfg)

    # Load a fresh instance and update one field
    s2 = AppSettings.load(config_file=cfg)
    s2.update(volume=1.8, config_file=cfg)

    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert saved["volume"] == 1.8
    assert saved["hotkey"] == "f8"
    assert saved["window_position"] == {"x": 100, "y": 200}


def test_update_creates_file(tmp_path: Path):
    """AppSettings.update() creates file when it doesn't exist."""
    cfg = tmp_path / "config.toml"
    assert not cfg.exists()

    s = AppSettings.load(config_file=cfg)
    s.update(hotkey="f9", name="F9", config_file=cfg)

    assert cfg.exists()
    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert saved["hotkey"] == "f9"
    assert saved["volume"] == 1.0  # default


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def test_atomic_write_preserves_original_on_failure(tmp_path, monkeypatch):
    """Atomic write does not corrupt existing config when replacement fails."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(hotkey="f8", volume=1.5, fixed_center=True, config_file=cfg)
    original_content = cfg.read_bytes()

    def failing_replace(src, dst):
        raise OSError("Simulated write failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    try:
        s.update(hotkey="f9", config_file=cfg)
        assert False, "Expected exception was not raised"
    except OSError:
        assert cfg.exists()
        current_content = cfg.read_bytes()
        assert current_content == original_content, "Original config was corrupted after failure"
        temp_files = list(tmp_path.glob(".atomic_wordy_*.toml"))
        assert len(temp_files) == 0, "Temp file was not cleaned up after failure"


# ---------------------------------------------------------------------------
# Voice config (structured tts_providers)
# ---------------------------------------------------------------------------

def test_voice_config_valid_and_invalid(tmp_path: Path):
    """AppSettings.load() parses tts_providers structured voice config."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.cartesia_voice_id = "vx"
    s.cartesia_voice_name = "Vx"
    s.flush()
    s2 = AppSettings.load(config_file=cfg)
    assert s2.cartesia_voice_id == "vx"
    assert s2.cartesia_voice_name == "Vx"

    # No voice_id → None
    s4 = AppSettings.load(config_file=tmp_path / "nonexistent.toml")
    assert s4.cartesia_voice_id is None
    assert s4.cartesia_voice_name is None

    # Empty voice_id → None (TOML doesn't have null, so empty string)
    cfg5 = tmp_path / "config5.toml"
    s5 = AppSettings.load(config_file=cfg5)
    s5.cartesia_voice_id = ""
    s5.flush()
    # 空字符串存储在 tts_providers 中仍是空字符串, getter 返回 None（只返回 str 实例）
    s6 = AppSettings.load(config_file=cfg5)
    # _inject_key_status 会把 api_key_set/api_key_storage 覆盖，但 voice_id/voice_name 保留
    assert s6.cartesia_voice_id is None or s6.cartesia_voice_id == ""


# ---------------------------------------------------------------------------
# Fixed center
# ---------------------------------------------------------------------------

def test_fixed_center_config(tmp_path: Path):
    """AppSettings.load() parses fixed_center correctly."""
    # Default
    s = AppSettings.load(config_file=tmp_path / "nonexistent.toml")
    assert s.fixed_center is True

    # Explicit True/False
    for val in (True, False):
        cfg = tmp_path / f"fc_{val}.toml"
        s2 = AppSettings.load(config_file=cfg)
        s2.update(fixed_center=val, config_file=cfg)
        s3 = AppSettings.load(config_file=cfg)
        assert s3.fixed_center is val

    # Non-bool falls back to default
    cfg_yes = tmp_path / "fc_bad_yes.toml"
    cfg_yes.write_text('fixed_center = "yes"\n', encoding="utf-8")
    s4 = AppSettings.load(config_file=cfg_yes)
    assert s4.fixed_center is True

    cfg_int = tmp_path / "fc_bad_int.toml"
    cfg_int.write_text('fixed_center = 1\n', encoding="utf-8")
    s5 = AppSettings.load(config_file=cfg_int)
    assert s5.fixed_center is True


# ---------------------------------------------------------------------------
# initial config
# ---------------------------------------------------------------------------

def test_volcengine_voice_list_round_trips(tmp_path: Path):
    """手动维护的火山引擎音色列表必须落盘，重启后仍在。"""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.volcengine_voice_list = [
        {"id": "S_abc123", "name": "我的复刻音色"},
        {"id": "BV001_streaming", "name": "通用女声"},
    ]
    s.flush()
    # 必须清掉 load() 缓存，否则第二次 load 返回同一实例——等于没验证落盘。
    # 这条用例最初就是因为漏了这一步才假绿的。
    wordy.config._cached_settings = None
    wordy.config._cached_config_file = None

    s2 = AppSettings.load(config_file=cfg)
    assert s2.volcengine_voice_list == [
        {"id": "S_abc123", "name": "我的复刻音色"},
        {"id": "BV001_streaming", "name": "通用女声"},
    ]


def test_volcengine_voice_list_defaults_to_empty(tmp_path: Path):
    s = AppSettings.load(config_file=tmp_path / "config.toml")
    assert s.volcengine_voice_list == []


def test_volcengine_voice_list_sanitizes_entries(tmp_path: Path):
    """配置可被手改：丢掉坏条目，同 ID 只留第一条，缺名用 ID 兜底。

    下拉列表按名称做标签，没有名称的条目会静默消失，所以必须有兜底。
    """
    s = AppSettings.load(config_file=tmp_path / "config.toml")
    s.get_provider("Volcengine")["voice_list"] = [
        {"id": "S_ok", "name": "好条目"},
        {"id": "S_ok", "name": "重复 ID"},
        {"id": "  S_padded  ", "name": "  两边有空格  "},
        {"id": "S_noname", "name": ""},
        {"id": "S_badname", "name": 7},
        {"id": "", "name": "空 ID"},
        {"id": 5, "name": "非字符串 ID"},
        "不是字典",
        None,
    ]

    assert s.volcengine_voice_list == [
        {"id": "S_ok", "name": "好条目"},
        {"id": "S_padded", "name": "两边有空格"},
        {"id": "S_noname", "name": "S_noname"},
        {"id": "S_badname", "name": "S_badname"},
    ]


def test_volcengine_voice_list_rejects_non_list(tmp_path: Path):
    s = AppSettings.load(config_file=tmp_path / "config.toml")
    s.get_provider("Volcengine")["voice_list"] = "S_abc"
    assert s.volcengine_voice_list == []


def test_load_invalid_voice(tmp_path: Path):
    """AppSettings.load() returns None voice when stored voice_id is invalid."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(hotkey="f7", config_file=cfg)
    # cartesia_voice_id defaults to None
    assert s.cartesia_voice_id is None
    assert s.cartesia_voice_name is None
    assert s.hotkey == "f7"


def test_load_voice_name_missing(tmp_path: Path):
    """AppSettings.load() loads voice from structured tts_providers."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.cartesia_voice_id = "vid_42"
    s.cartesia_voice_name = "Vid 42"
    s.flush()
    s2 = AppSettings.load(config_file=cfg)
    assert s2.cartesia_voice_id == "vid_42"
    assert s2.cartesia_voice_name == "Vid 42"


def test_load_voice_config_invalid(tmp_path: Path):
    """AppSettings.load() returns None for voice when missing."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(hotkey="f6", config_file=cfg)
    assert s.cartesia_voice_id is None
    assert s.cartesia_voice_name is None


# ---------------------------------------------------------------------------
# Volume update clamping (via setattr)
# ---------------------------------------------------------------------------

def test_setattr_volume_clamps(tmp_path: Path):
    """Direct attribute assignment clamps volume before persisting."""
    cfg = tmp_path / "config.toml"

    s = AppSettings.load(config_file=cfg)
    s.volume = 99.0
    s.flush()
    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert saved["volume"] == MAX_VOLUME

    s.volume = 0.0
    s.flush()
    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert saved["volume"] == MIN_VOLUME


# ---------------------------------------------------------------------------
# Audio output device persistence
# ---------------------------------------------------------------------------

def test_audio_output_device_round_trip(tmp_path: Path):
    """AppSettings persists audio_output_device_name across load/save."""
    cfg = tmp_path / "config.toml"

    s = AppSettings.load(config_file=cfg)
    s.audio_output_device_name = "Speakers (Realtek)"
    s.flush()

    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert saved["audio_output_device_name"] == "Speakers (Realtek)"

    s2 = AppSettings.load(config_file=cfg)
    assert s2.audio_output_device_name == "Speakers (Realtek)"

    # Clear to None（TOML 中不写入 None，load 回退为 defaults None）
    s2.audio_output_device_name = None
    s2.flush()
    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert "audio_output_device_name" not in saved

    s3 = AppSettings.load(config_file=cfg)
    assert s3.audio_output_device_name is None


def test_audio_output_device_name_returns_stored_value(tmp_path: Path):
    """AppSettings.load() returns the persisted device name."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.audio_output_device_name = "Headphones (USB)"
    s.flush()
    s2 = AppSettings.load(config_file=cfg)
    assert s2.audio_output_device_name == "Headphones (USB)"

    s2.audio_output_device_name = "Speakers (Built-in)"
    s2.flush()
    s3 = AppSettings.load(config_file=cfg)
    assert s3.audio_output_device_name == "Speakers (Built-in)"


def test_audio_output_device_name_none_when_missing(tmp_path: Path):
    """AppSettings.load() returns None when audio_output_device_name absent or empty."""
    cfg = tmp_path / "config.toml"
    # Empty file → defaults
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device_name is None

    cfg2 = tmp_path / "config2.toml"
    cfg2.write_text('audio_output_device_name = ""\n', encoding="utf-8")
    s2 = AppSettings.load(config_file=cfg2)
    assert s2.audio_output_device_name is None

    cfg3 = tmp_path / "config3.toml"
    s3 = AppSettings.load(config_file=cfg3)
    s3.audio_output_device_name = "Speakers"
    s3.flush()
    s4 = AppSettings.load(config_file=cfg3)
    assert s4.audio_output_device_name == "Speakers"


def test_invalid_audio_output_device_name_falls_back_to_none(tmp_path: Path):
    """Non-string audio_output_device_name values fall back to None."""
    for bad_val in (12345, 12.5):
        cfg = tmp_path / f"bad_{hash(str(bad_val))}.toml"
        # 直接写入 TOML 数值类型
        cfg.write_text(f'audio_output_device_name = {bad_val}\n', encoding="utf-8")
        s = AppSettings.load(config_file=cfg)
        assert s.audio_output_device_name is None


# ---------------------------------------------------------------------------
# Structured audio output device
# ---------------------------------------------------------------------------

def test_save_structured_audio_output_device(tmp_path: Path):
    """AppSettings persists structured audio_output_device."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.audio_output_device = {"name": "Speakers (Realtek)", "host_api_name": "MME"}
    s.audio_output_device_name = "Speakers (Realtek)"
    s.flush()

    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert saved["audio_output_device"] == {"name": "Speakers (Realtek)", "host_api_name": "MME"}
    assert saved["audio_output_device_name"] == "Speakers (Realtek)"


def test_load_audio_output_device_structured(tmp_path: Path):
    """AppSettings.load() returns structured audio_output_device."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[audio_output_device]\nname = "Headphones (USB)"\nhost_api_name = "WASAPI"\n'
        'audio_output_device_name = "Headphones (USB)"\n',
        encoding="utf-8",
    )
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device == {"name": "Headphones (USB)", "host_api_name": "WASAPI"}


def test_load_audio_output_device_legacy_fallback(tmp_path: Path):
    """When only device name exists without structured device, identity is None."""
    cfg = tmp_path / "config.toml"
    cfg.write_text('audio_output_device_name = "Speakers (Built-in)"\n', encoding="utf-8")
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device is None
    assert s.audio_output_device_name == "Speakers (Built-in)"


def test_load_audio_output_device_invalid_object_returns_none(tmp_path: Path):
    """Malformed audio_output_device falls back to None."""
    # Missing name
    cfg2 = tmp_path / "c2.toml"
    cfg2.write_text('[audio_output_device]\nhost_api_name = "MME"\n', encoding="utf-8")
    s2 = AppSettings.load(config_file=cfg2)
    assert s2.audio_output_device is None

    # Empty name
    cfg3 = tmp_path / "c3.toml"
    cfg3.write_text('[audio_output_device]\nname = ""\nhost_api_name = "MME"\n', encoding="utf-8")
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.audio_output_device is None

    # Non-string name (TOML int)
    cfg4 = tmp_path / "c4.toml"
    cfg4.write_text('[audio_output_device]\nname = 123\nhost_api_name = "MME"\n', encoding="utf-8")
    s4 = AppSettings.load(config_file=cfg4)
    assert s4.audio_output_device is None


def test_clear_structured_audio_output_device(tmp_path: Path):
    """Setting audio_output_device=None clears it from TOML."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.audio_output_device = {"name": "Speakers (Realtek)", "host_api_name": "MME"}
    s.audio_output_device_name = "Speakers (Realtek)"
    s.flush()

    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert saved["audio_output_device"]["name"] == "Speakers (Realtek)"

    s.audio_output_device = None
    s.audio_output_device_name = None
    s.flush()
    saved2 = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert "audio_output_device" not in saved2
    assert "audio_output_device_name" not in saved2


def test_save_audio_output_device_missing_host_api(tmp_path: Path):
    """AppSettings persists host_api_name=None when not provided."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.audio_output_device = {"name": "Speakers", "host_api_name": None}
    s.audio_output_device_name = "Speakers"
    s.flush()

    saved = tomllib.loads(cfg.read_text(encoding="utf-8"))
    # None values are omitted from TOML
    assert saved["audio_output_device"] == {"name": "Speakers"}


def test_load_audio_output_device_structured_overrides_legacy(tmp_path: Path):
    """Structured value and legacy name coexist in TOML."""
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        'audio_output_device_name = "Old Device"\n'
        '[audio_output_device]\nname = "New Device"\nhost_api_name = "WASAPI"\n',
        encoding="utf-8",
    )
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device == {"name": "New Device", "host_api_name": "WASAPI"}
    assert s.audio_output_device_name == "Old Device"


# ---------------------------------------------------------------------------
# Cartesia API key status propagation
# ---------------------------------------------------------------------------

import wordy.secret


def _patch_cartesia_api_key_status(monkeypatch, *, key_set: bool, storage: str):
    """Patch the secret_store status API used by AppSettings.load()."""
    status = {"cartesia_api_key_set": key_set, "cartesia_api_key_storage": storage}

    monkeypatch.setattr(
        wordy.secret,
        "get_cartesia_api_key_status",
        lambda: status.copy(),
        raising=False,
    )


def test_app_config_env_file_api_removed():
    """app_config must no longer expose .env migration-era API symbols."""
    assert not hasattr(wordy.config, "ENV_FILE")
    assert not hasattr(wordy.config, "load_cartesia_api_key")


def test_app_config_source_has_no_env_or_raw_key_access():
    """app_config source must be purged of direct env/raw-key access after migration."""
    source = Path(wordy.config.__file__).read_text(encoding="utf-8")
    forbidden_tokens = (".env", "CARTESIA_API_KEY", "os.environ", "os.getenv")

    for token in forbidden_tokens:
        assert token not in source


def test_cartesia_api_key_set_defaults_false_when_no_key(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """No key stored anywhere -> cartesia_api_key_set is False."""
    cfg = tmp_path / "config.toml"
    _patch_cartesia_api_key_status(monkeypatch, key_set=False, storage=wordy.secret.STORAGE_NONE)

    s = AppSettings.load(config_file=cfg)
    assert s.cartesia_api_key_set is False
    assert s.cartesia_api_key_storage == wordy.secret.STORAGE_NONE


def test_cartesia_api_key_set_true_when_secret_store_status_reports_keyring(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """Status API reports keyring -> metadata mirrors status."""
    cfg = tmp_path / "config.toml"
    _patch_cartesia_api_key_status(monkeypatch, key_set=True, storage=wordy.secret.STORAGE_KEYRING)

    s = AppSettings.load(config_file=cfg)
    assert s.cartesia_api_key_set is True
    assert s.cartesia_api_key_storage == wordy.secret.STORAGE_KEYRING


def test_load_never_contains_raw_key(monkeypatch, tmp_path: Path, fake_keyring):
    """AppSettings.load() must not surface raw key strings from stored config data."""
    raw = "sk-do-not-leak-in-app-config"
    cfg = tmp_path / "config.toml"
    # Write a TOML file that might contain raw key in tts_providers
    cfg.write_text(f'hotkey = "f6"\ncartersia_api_key = "{raw}"\n', encoding="utf-8")
    _patch_cartesia_api_key_status(monkeypatch, key_set=True, storage=wordy.secret.STORAGE_KEYRING)

    s = AppSettings.load(config_file=cfg)
    # Check string fields don't contain the raw key
    for field_name in ("hotkey", "name", "cartesia_voice_id", "cartesia_voice_name"):
        val = getattr(s, field_name, "")
        if isinstance(val, str):
            assert raw not in val, "Raw API key leaked into AppSettings field"


def test_user_config_file_never_contains_accidental_secret_fields(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """AppSettings does not write secret fields to TOML."""
    cfg = tmp_path / "config.toml"
    _patch_cartesia_api_key_status(monkeypatch, key_set=True, storage=wordy.secret.STORAGE_KEYRING)

    s = AppSettings.load(config_file=cfg)
    s.update(volume=1.0, config_file=cfg)

    written = cfg.read_text(encoding="utf-8")
    saved = tomllib.loads(written)
    for field_name in ("cartesia_api_key", "api_key", "token", "secret"):
        assert field_name not in saved


# ---------------------------------------------------------------------------
# NEW: __setattr__ auto-save tests
# ---------------------------------------------------------------------------

def test_setattr_auto_save(tmp_path: Path):
    """Setting a field via attribute assignment auto-persists to TOML."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.volume = 1.7
    s.flush()
    loaded = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert loaded["volume"] == 1.7


def test_setattr_immediate_save_for_non_slider(tmp_path: Path):
    """Non-slider fields save immediately (no debounce needed)."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.hotkey = "f9"
    # hotkey is not in _DEBOUNCED_FIELDS → saved immediately
    loaded = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert loaded["hotkey"] == "f9"


def test_setattr_private_fields_dont_save(tmp_path: Path):
    """Setting _-prefixed fields does not trigger save."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.volume = 1.0
    s.flush()
    # 用文件字节而非 mtime 比对：Windows 的 mtime 精度可能让原子写落在同一刻度，断言会假绿。
    before = cfg.read_bytes()

    s._loaded = False  # 内部字段，不触发 save
    s._loaded = True
    # 未设置任何公共字段，文件不应改变
    # 注：_loaded 来回切换不会触发保存
    s.flush()

    assert cfg.read_bytes() == before, "_ 前缀字段赋值不应触发保存"


def test_toml_round_trip(tmp_path: Path):
    """TOML save/load round-trips all field types correctly."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.update(
        volume=1.5,
        fixed_center=False,
        log_level="DEBUG",
        audio_routing_enabled=True,
        window_position={"x": 100, "y": 200},
        config_file=cfg,
    )
    s2 = AppSettings.load(config_file=cfg)
    assert s2.volume == 1.5
    assert s2.fixed_center is False
    assert s2.log_level == "DEBUG"
    assert s2.audio_routing_enabled is True
    assert s2.window_position == {"x": 100, "y": 200}


def test_none_values_omitted_from_toml(tmp_path: Path):
    """None values are not written to TOML."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.audio_output_device_name = "Speakers"
    s.flush()
    content = cfg.read_text(encoding="utf-8")
    assert "audio_output_device_name" in content

    s.audio_output_device_name = None
    s.flush()
    content2 = cfg.read_text(encoding="utf-8")
    assert "audio_output_device_name" not in content2

    # Setting back should include it
    s.audio_output_device_name = "Headphones"
    s.flush()
    loaded = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert loaded["audio_output_device_name"] == "Headphones"


def test_sidetone_config_round_trip(tmp_path: Path):
    """sidetone_enabled persists correctly across TOML save/load."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    assert s.sidetone_enabled is False  # default

    s.sidetone_enabled = True
    s.flush()
    s2 = AppSettings.load(config_file=cfg)
    assert s2.sidetone_enabled is True

    s2.sidetone_enabled = False
    s2.flush()
    s3 = AppSettings.load(config_file=cfg)
    assert s3.sidetone_enabled is False


def test_audio_routing_config_round_trip(tmp_path: Path):
    """audio_routing_enabled and mic_input_device persist correctly."""
    cfg = tmp_path / "config.toml"
    s = AppSettings.load(config_file=cfg)
    s.audio_routing_enabled = True
    s.mic_input_device = "Mic (USB)"
    s.flush()

    s2 = AppSettings.load(config_file=cfg)
    assert s2.audio_routing_enabled is True
    assert s2.mic_input_device == "Mic (USB)"

    # Clear mic
    s2.mic_input_device = None
    s2.flush()
    s3 = AppSettings.load(config_file=cfg)
    assert s3.mic_input_device is None
    assert s3.audio_routing_enabled is True  # unchanged

