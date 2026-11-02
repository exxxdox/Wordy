#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""「播放时自动按住某键」：按键校验、配置持久化与播放层按键注入。"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

import wordy.config
import wordy.hotkey.native as native
import wordy.tts_manager as tts_manager
from wordy.config import AppSettings


def _reload_from_disk(path: Path) -> AppSettings:
    """绕开 load() 的实例缓存，确认字段真的存得进、读得回。"""
    wordy.config._cached_settings = None
    wordy.config._cached_config_file = None
    return AppSettings.load(config_file=path)


# ---------------------------------------------------------------------------
# 按键解析（录制结果与手改配置共用同一套校验）
# ---------------------------------------------------------------------------

def test_hold_key_names_resolve_to_virtual_keys() -> None:
    assert native.virtual_key_for_key_name(" G ") == 0x47
    assert native.virtual_key_for_key_name("f9") == 0x78
    assert native.virtual_key_for_key_name("left ctrl") == 0xA2
    assert native.virtual_key_for_key_name("no such key") is None


# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

def test_auto_hold_defaults_off_with_g_key(tmp_path: Path) -> None:
    settings = AppSettings.load(config_file=tmp_path / "config.toml")

    assert settings.hold_key_enabled is False
    assert settings.hold_key == "g"


def test_auto_hold_key_round_trips_through_toml(tmp_path: Path) -> None:
    cfg = tmp_path / "hold.toml"
    settings = AppSettings.load(config_file=cfg)
    settings.hold_key = " H "
    settings.hold_key_enabled = True

    reloaded = _reload_from_disk(cfg)

    # 归一化后落盘，重启后显示的键与录制结果一致。
    assert reloaded.hold_key == "h"
    assert reloaded.hold_key_enabled is True


def test_invalid_hold_key_falls_back_to_default(tmp_path: Path) -> None:
    cfg = tmp_path / "invalid.toml"
    cfg.write_text('hold_key = "no such key"\n', encoding="utf-8")

    settings = _reload_from_disk(cfg)
    assert settings.hold_key == "g"

    settings.hold_key = "also invalid"
    assert settings.hold_key == "g"


# ---------------------------------------------------------------------------
# 播放层：开播前按下，播完/失败/取消后松开
# ---------------------------------------------------------------------------

@pytest.fixture
def hold_events(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    events: list[tuple[str, str]] = []
    monkeypatch.setattr(tts_manager, "HOLD_KEY_SETTLE_SECONDS", 0)
    monkeypatch.setattr(tts_manager, "_press_hold_key",
                        lambda key: events.append(("press", key)))
    monkeypatch.setattr(tts_manager, "_release_hold_key",
                        lambda key: events.append(("release", key)))
    return events


@contextmanager
def _fake_decoded(path, _cancel_event):
    yield path


class _Harness:
    def __init__(self, manager: tts_manager.TTSManager,
                 engine: MagicMock, player: MagicMock) -> None:
        self.manager = manager
        self.engine = engine
        self.player = player
        self.executor = cast(MagicMock, manager._executor)

    def run_submitted(self) -> None:
        """同步执行 manager 提交到 worker 的那一次操作。"""
        operation, *args = self.executor.submit.call_args.args
        operation(*args)


def _new_harness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, enabled: bool,
) -> _Harness:
    settings = AppSettings.load(config_file=tmp_path / f"auto-{enabled}.toml")
    settings.update(volcengine_voice_list=[{"id": "speaker", "name": "Speaker"}],
                    hold_key_enabled=enabled, hold_key="g")
    player, engine = MagicMock(), MagicMock()
    engine.speak.return_value = True
    monkeypatch.setattr(tts_manager, "create_tts_engine", lambda *a, **k: engine)
    manager = tts_manager.TTSManager(
        settings=settings,
        audio_player=player,
        cartesia_api_key=None,
        volcengine_access_key="key",
        tts_api_provider="Volcengine",
        tts_backend="Volcengine Streaming",
    )
    manager._executor = MagicMock()
    return _Harness(manager, engine, player)


def test_speech_presses_before_playback_and_releases_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    hold_events: list[tuple[str, str]],
) -> None:
    harness = _new_harness(tmp_path, monkeypatch, enabled=True)

    def speak(_text: str, _cancel: object) -> bool:
        # 播放期间必须已经按住，否则游戏收音是关的。
        assert hold_events == [("press", "g")]
        return True

    harness.engine.speak.side_effect = speak
    harness.manager.speak("马上到")
    harness.run_submitted()

    assert hold_events == [("press", "g"), ("release", "g")]
    harness.manager.shutdown()


def test_failed_speech_still_releases_hold_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    hold_events: list[tuple[str, str]],
) -> None:
    harness = _new_harness(tmp_path, monkeypatch, enabled=True)
    harness.engine.speak.side_effect = RuntimeError("boom")

    harness.manager.speak("马上到")
    harness.run_submitted()

    assert hold_events[-1] == ("release", "g")
    harness.manager.shutdown()


def test_audio_clip_presses_before_playback_and_releases_after(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    hold_events: list[tuple[str, str]],
) -> None:
    harness = _new_harness(tmp_path, monkeypatch, enabled=True)
    harness.player.play_wav.side_effect = lambda *a, **k: hold_events == [("press", "g")]
    monkeypatch.setattr(tts_manager, "decoded_audio_clip", _fake_decoded)

    harness.manager.play_audio_file(tmp_path / "clip.wav")
    harness.run_submitted()

    assert hold_events == [("press", "g"), ("release", "g")]
    harness.manager.shutdown()


def test_no_key_injection_when_feature_is_off(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    hold_events: list[tuple[str, str]],
) -> None:
    harness = _new_harness(tmp_path, monkeypatch, enabled=False)

    harness.manager.speak("马上到")
    harness.run_submitted()

    assert hold_events == []
    harness.manager.shutdown()


def test_interrupted_clip_still_releases_hold_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    hold_events: list[tuple[str, str]],
) -> None:
    harness = _new_harness(tmp_path, monkeypatch, enabled=True)
    monkeypatch.setattr(tts_manager, "decoded_audio_clip", _fake_decoded)

    harness.manager.play_audio_file(tmp_path / "clip.wav")
    harness.manager.stop_audio_file()
    harness.run_submitted()

    # 开播前已被取消：不按键，也不该留下按住的键。
    assert hold_events == []
    harness.manager.shutdown()
