"""后台录制设置的持久化、钳位与快捷键冲突。"""

from __future__ import annotations

from pathlib import Path

import pytest

import wordy.config
from wordy.config import (
    MAX_ROLLING_BUFFER_SECONDS,
    MIN_ROLLING_BUFFER_SECONDS,
    AppSettings,
)


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings.load(config_file=tmp_path / "wordy" / "config.toml")


def test_defaults_are_safe(tmp_path):
    settings = _settings(tmp_path)
    assert settings.rolling_buffer_hotkey == "f10"
    assert settings.rolling_buffer_seconds == 30.0
    assert settings.rolling_buffer_target == [0, 0]


def test_seconds_are_clamped_on_save(tmp_path):
    settings = _settings(tmp_path)
    settings.rolling_buffer_seconds = 1000
    assert settings.rolling_buffer_seconds == MAX_ROLLING_BUFFER_SECONDS
    settings.rolling_buffer_seconds = 1
    assert settings.rolling_buffer_seconds == MIN_ROLLING_BUFFER_SECONDS


def test_settings_round_trip(tmp_path, monkeypatch):
    config_file = tmp_path / "wordy" / "config.toml"
    monkeypatch.setattr(wordy.config, "USER_CONFIG_FILE", config_file)
    settings = AppSettings.load(config_file=config_file)
    settings.add_wheel("f10")  # 目标盘必须真实存在，否则会被钳回首盘
    settings.update(rolling_buffer_hotkey="ctrl+f9", rolling_buffer_seconds=45,
                    rolling_buffer_target=[1, 3])

    raw = wordy.config.read_toml(config_file) or {}
    assert raw["rolling_buffer_hotkey"] == "ctrl+f9"
    assert raw["rolling_buffer_seconds"] == 45
    assert raw["rolling_buffer_target"] == [1, 3]


def test_invalid_hotkey_and_target_fall_back(tmp_path):
    path = tmp_path / "wordy" / "config.toml"
    path.parent.mkdir(parents=True)
    path.write_text(
        'rolling_buffer_hotkey = "not-a-key"\n'
        "rolling_buffer_seconds = 9999\n"
        "rolling_buffer_target = [99, 99]\n",
        encoding="utf-8",
    )
    settings = AppSettings.load(config_file=path)
    assert settings.rolling_buffer_hotkey == "f10"
    assert settings.rolling_buffer_seconds == MAX_ROLLING_BUFFER_SECONDS
    assert settings.rolling_buffer_target == [0, 7]


def test_target_slot_is_clamped_on_store(tmp_path):
    settings = _settings(tmp_path)
    settings.rolling_buffer_target = [-2, 20]
    assert settings.rolling_buffer_target == [0, 7]
