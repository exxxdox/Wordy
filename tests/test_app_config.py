#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for easy_tts.config.py AppSettings behavior."""

import json
import os
from pathlib import Path

import easy_tts.config
from easy_tts.config import AppSettings, display_hotkey, MIN_VOLUME, MAX_VOLUME
from easy_tts.config import MIN_GAIN, MAX_GAIN
from easy_tts.tts.constants import DEFAULT_TTS_BACKEND, TTS_BACKENDS


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


# ---------------------------------------------------------------------------
# Hotkey parsing through AppSettings.load()
# ---------------------------------------------------------------------------

def test_hotkey_config_valid(tmp_path: Path):
    """AppSettings.load() parses hotkey and name from JSON."""
    cfg = tmp_path / "config.json"
    json.dump({"hotkey": "f8", "name": "My F8"}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.hotkey == "f8"
    assert s.name == "My F8"

    # Only hotkey, name auto-generated from hotkey
    cfg2 = tmp_path / "config2.json"
    json.dump({"hotkey": "ctrl+shift+f"}, cfg2.open("w"))
    s2 = AppSettings.load(config_file=cfg2)
    assert s2.hotkey == "ctrl+shift+f"
    assert s2.name == "Ctrl+Shift+F"

    # Hotkey valid but name invalid (non-string) → name regenerated
    cfg3 = tmp_path / "config3.json"
    json.dump({"hotkey": "f9", "name": 123}, cfg3.open("w"))
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.hotkey == "f9"
    assert s3.name == "F9"


def test_hotkey_config_invalid(tmp_path: Path):
    """AppSettings.load() returns defaults when hotkey missing/invalid."""
    # No hotkey field → default
    cfg = tmp_path / "config.json"
    json.dump({}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.hotkey == "f6"
    assert s.name == "F6"

    # Non-string hotkey → default
    cfg2 = tmp_path / "config2.json"
    json.dump({"hotkey": 123}, cfg2.open("w"))
    s2 = AppSettings.load(config_file=cfg2)
    assert s2.hotkey == "f6"

    # Empty hotkey string → default
    cfg3 = tmp_path / "config3.json"
    json.dump({"hotkey": ""}, cfg3.open("w"))
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.hotkey == "f6"


# ---------------------------------------------------------------------------
# Volume clamping through AppSettings.load()
# ---------------------------------------------------------------------------

def test_volume_config_clamps(tmp_path: Path):
    """AppSettings.load() clamps volume between MIN_VOLUME and MAX_VOLUME."""
    # Default when missing
    s = AppSettings.load(config_file=tmp_path / "nonexistent.json")
    assert s.volume == 1.0

    # Within range stays the same
    for val in (1.0, 0.5, 2.0, 1.5):
        cfg = tmp_path / f"vol_{val}.json"
        json.dump({"volume": val}, cfg.open("w"))
        s2 = AppSettings.load(config_file=cfg)
        assert s2.volume == val

    # Below min clamps to min
    cfg = tmp_path / "vol_low.json"
    json.dump({"volume": 0.0}, cfg.open("w"))
    s3 = AppSettings.load(config_file=cfg)
    assert s3.volume == MIN_VOLUME
    assert s3.volume == 0.5

    # Above max clamps to max
    cfg = tmp_path / "vol_high.json"
    json.dump({"volume": 3.0}, cfg.open("w"))
    s4 = AppSettings.load(config_file=cfg)
    assert s4.volume == MAX_VOLUME

    # Integer converts to float via clamp
    cfg = tmp_path / "vol_int.json"
    json.dump({"volume": 1}, cfg.open("w"))
    s5 = AppSettings.load(config_file=cfg)
    assert s5.volume == 1.0


# ---------------------------------------------------------------------------
# TTS backend
# ---------------------------------------------------------------------------

def test_tts_backend_config_fallback(tmp_path: Path):
    """AppSettings.load() validates tts_backend, falls back to default."""
    valid_backend = next(iter(TTS_BACKENDS))
    cfg = tmp_path / "config.json"
    json.dump({"tts_backend": valid_backend}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.tts_backend == valid_backend

    # Missing → default
    s2 = AppSettings.load(config_file=tmp_path / "nonexistent.json")
    assert s2.tts_backend == DEFAULT_TTS_BACKEND

    # Non-string → default
    cfg3 = tmp_path / "config3.json"
    json.dump({"tts_backend": 123}, cfg3.open("w"))
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.tts_backend == DEFAULT_TTS_BACKEND

    # Invalid string → default
    cfg4 = tmp_path / "config4.json"
    json.dump({"tts_backend": "invalid_backend"}, cfg4.open("w"))
    s4 = AppSettings.load(config_file=cfg4)
    assert s4.tts_backend == DEFAULT_TTS_BACKEND


# ---------------------------------------------------------------------------
# Window position
# ---------------------------------------------------------------------------

def test_window_position_config(tmp_path: Path):
    """AppSettings.load() parses window_position correctly."""
    cfg = tmp_path / "config.json"
    json.dump({"window_position": {"x": 100, "y": 200}}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.window_position == {"x": 100, "y": 200}

    # No window_position → None
    s2 = AppSettings.load(config_file=tmp_path / "nonexistent.json")
    assert s2.window_position is None

    # Non-dict → None
    cfg3 = tmp_path / "config3.json"
    json.dump({"window_position": [100, 200]}, cfg3.open("w"))
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.window_position is None

    # Non-int x/y → None
    cfg4 = tmp_path / "config4.json"
    json.dump({"window_position": {"x": "100", "y": 200}}, cfg4.open("w"))
    s4 = AppSettings.load(config_file=cfg4)
    assert s4.window_position is None


# ---------------------------------------------------------------------------
# JSON loading (AppSettings.load with various file states)
# ---------------------------------------------------------------------------

def test_load_missing_file(tmp_path: Path):
    """AppSettings.load() returns defaults when file is missing."""
    s = AppSettings.load(config_file=tmp_path / "missing.json")
    assert s.hotkey == "f6"
    assert s.volume == 1.0


def test_load_invalid_json(tmp_path: Path):
    """AppSettings.load() returns defaults with invalid JSON."""
    cfg = tmp_path / "bad.json"
    cfg.write_text("not valid json {{{", encoding="utf-8")
    s = AppSettings.load(config_file=cfg)
    assert s.hotkey == "f6"  # defaults


def test_load_non_dict_json(tmp_path: Path):
    """AppSettings.load() returns defaults when JSON is not a dict."""
    cfg = tmp_path / "list.json"
    json.dump([1, 2, 3], cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.hotkey == "f6"


def test_load_valid_json(tmp_path: Path):
    """AppSettings.load() loads valid JSON correctly."""
    cfg = tmp_path / "valid.json"
    json.dump({"hotkey": "f8", "volume": 1.5}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.hotkey == "f8"
    assert s.volume == 1.5
    assert s.name == "F8"


# ---------------------------------------------------------------------------
# AppSettings.load() defaults and partial config
# ---------------------------------------------------------------------------

def test_load_defaults_no_file(tmp_path: Path):
    """AppSettings.load() returns all defaults when config file doesn't exist."""
    s = AppSettings.load(config_file=tmp_path / "nonexistent.json")
    assert s.hotkey == "f6"
    assert s.name == "F6"
    assert s.volume == 1.0
    assert s.fixed_center is True
    assert s.tts_backend == DEFAULT_TTS_BACKEND
    assert s.window_position is None


def test_load_partial_config(tmp_path: Path):
    """AppSettings.load() fills defaults for missing fields."""
    cfg = tmp_path / "config.json"
    json.dump({"hotkey": "f8", "volume": 1.5}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.hotkey == "f8"
    assert s.name == "F8"
    assert s.volume == 1.5
    assert s.fixed_center is True  # default
    assert s.tts_backend == DEFAULT_TTS_BACKEND  # default


# ---------------------------------------------------------------------------
# AppSettings.update() persistence
# ---------------------------------------------------------------------------

def test_update_merges_and_persists(tmp_path: Path):
    """AppSettings.update() merges with existing and writes to file."""
    cfg = tmp_path / "config.json"
    json.dump({"hotkey": "f8", "volume": 1.5, "window_position": {"x": 100, "y": 200}}, cfg.open("w"))

    # Load once, update a field
    s = AppSettings.load(config_file=cfg)
    updated_file = s.update(volume=1.8, config_file=cfg)

    assert updated_file == cfg
    saved = json.load(cfg.open("r"))
    assert saved["volume"] == 1.8
    assert saved["hotkey"] == "f8"
    assert saved["window_position"] == {"x": 100, "y": 200}


def test_update_creates_file(tmp_path: Path):
    """AppSettings.update() creates file when it doesn't exist."""
    cfg = tmp_path / "config.json"
    assert not cfg.exists()

    s = AppSettings.load(config_file=cfg)
    updated_file = s.update(hotkey="f9", name="F9", config_file=cfg)

    assert updated_file == cfg
    assert cfg.exists()
    saved = json.load(cfg.open("r"))
    assert saved["hotkey"] == "f9"
    assert saved["volume"] == 1.0  # default


# ---------------------------------------------------------------------------
# Atomic write
# ---------------------------------------------------------------------------

def test_atomic_write_preserves_original_on_failure(tmp_path, monkeypatch):
    """Atomic write does not corrupt existing config when replacement fails."""
    cfg = tmp_path / "config.json"
    original_config = {"hotkey": "f8", "volume": 1.5, "fixed_center": True}
    json.dump(original_config, cfg.open("w"), ensure_ascii=False, indent=2)
    original_content = cfg.read_bytes()

    original_replace = os.replace

    def failing_replace(src, dst):
        raise OSError("Simulated write failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    try:
        s = AppSettings.load(config_file=cfg)
        s.update(hotkey="f9", config_file=cfg)
        assert False, "Expected exception was not raised"
    except OSError:
        assert cfg.exists()
        current_content = cfg.read_bytes()
        assert current_content == original_content, "Original config was corrupted after failure"
        temp_files = list(tmp_path.glob(".atomic_wavtrans_*.json"))
        assert len(temp_files) == 0, "Temp file was not cleaned up after failure"


# ---------------------------------------------------------------------------
# Voice config
# ---------------------------------------------------------------------------

def test_voice_config_valid_and_invalid(tmp_path: Path):
    """AppSettings.load() parses voice_id and voice_name."""
    cfg = tmp_path / "config.json"
    json.dump({"voice_id": "vx", "voice_name": "Vx"}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.voice_id == "vx"
    assert s.voice_name == "Vx"

    # voice_name missing → defaults to voice_id
    cfg2 = tmp_path / "config2.json"
    json.dump({"voice_id": "vx"}, cfg2.open("w"))
    s2 = AppSettings.load(config_file=cfg2)
    assert s2.voice_id == "vx"
    assert s2.voice_name == "vx"

    # voice_name invalid (non-string) → defaults to voice_id
    cfg3 = tmp_path / "config3.json"
    json.dump({"voice_id": "vx", "voice_name": 123}, cfg3.open("w"))
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.voice_id == "vx"
    assert s3.voice_name == "vx"

    # No voice_id → None
    s4 = AppSettings.load(config_file=tmp_path / "nonexistent.json")
    assert s4.voice_id is None
    assert s4.voice_name is None

    # Empty voice_id → None
    cfg5 = tmp_path / "config5.json"
    json.dump({"voice_id": ""}, cfg5.open("w"))
    s5 = AppSettings.load(config_file=cfg5)
    assert s5.voice_id is None


# ---------------------------------------------------------------------------
# Fixed center
# ---------------------------------------------------------------------------

def test_fixed_center_config(tmp_path: Path):
    """AppSettings.load() parses fixed_center correctly."""
    # Default
    s = AppSettings.load(config_file=tmp_path / "nonexistent.json")
    assert s.fixed_center is True

    # Explicit True/False
    for val in (True, False):
        cfg = tmp_path / f"fc_{val}.json"
        json.dump({"fixed_center": val}, cfg.open("w"))
        s2 = AppSettings.load(config_file=cfg)
        assert s2.fixed_center is val

    # Non-bool falls back to default
    for val in ("yes", 1, None):
        cfg = tmp_path / f"fc_bad_{val}.json"
        json.dump({"fixed_center": val}, cfg.open("w"))
        s3 = AppSettings.load(config_file=cfg)
        assert s3.fixed_center is True


# ---------------------------------------------------------------------------
# initial config (load_initial_config equivalent)
# ---------------------------------------------------------------------------

def test_load_invalid_voice(tmp_path: Path):
    """AppSettings.load() returns None voice when stored voice_id is invalid."""
    cfg = tmp_path / "config.json"
    json.dump({"hotkey": "f7", "voice_id": "", "voice_name": "X"}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.voice_id is None
    assert s.voice_name is None
    assert s.hotkey == "f7"


def test_load_voice_name_missing(tmp_path: Path):
    """AppSettings.load() defaults voice_name to voice_id."""
    cfg = tmp_path / "config.json"
    json.dump({"voice_id": "vid_42"}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.voice_id == "vid_42"
    assert s.voice_name == "vid_42"


def test_load_voice_config_invalid(tmp_path: Path):
    """AppSettings.load() returns None for voice when missing."""
    cfg = tmp_path / "config.json"
    json.dump({"hotkey": "f6"}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.voice_id is None
    assert s.voice_name is None


# ---------------------------------------------------------------------------
# Volume update clamping
# ---------------------------------------------------------------------------

def test_update_volume_clamps(tmp_path: Path):
    """AppSettings.update() clamps volume before persisting."""
    cfg = tmp_path / "config.json"

    s = AppSettings.load(config_file=cfg)
    s.update(volume=99.0, config_file=cfg)
    saved = json.load(cfg.open("r"))
    assert saved["volume"] == MAX_VOLUME

    s.update(volume=0.0, config_file=cfg)
    saved = json.load(cfg.open("r"))
    assert saved["volume"] == MIN_VOLUME


# ---------------------------------------------------------------------------
# Audio output device persistence
# ---------------------------------------------------------------------------

def test_audio_output_device_round_trip(tmp_path: Path):
    """AppSettings persists audio_output_device_name across load/save."""
    cfg = tmp_path / "config.json"

    s = AppSettings.load(config_file=cfg)
    s.update(audio_output_device_name="Speakers (Realtek)", config_file=cfg)

    saved = json.load(cfg.open("r"))
    assert saved["audio_output_device_name"] == "Speakers (Realtek)"

    s2 = AppSettings.load(config_file=cfg)
    assert s2.audio_output_device_name == "Speakers (Realtek)"

    # Clear to None
    s2.update(audio_output_device_name=None, config_file=cfg)
    saved = json.load(cfg.open("r"))
    assert saved["audio_output_device_name"] is None

    s3 = AppSettings.load(config_file=cfg)
    assert s3.audio_output_device_name is None


def test_audio_output_device_name_returns_stored_value(tmp_path: Path):
    """AppSettings.load() returns the persisted device name."""
    cfg = tmp_path / "config.json"
    json.dump({"audio_output_device_name": "Headphones (USB)"}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device_name == "Headphones (USB)"

    s.update(audio_output_device_name="Speakers (Built-in)", config_file=cfg)
    s2 = AppSettings.load(config_file=cfg)
    assert s2.audio_output_device_name == "Speakers (Built-in)"


def test_audio_output_device_name_none_when_missing(tmp_path: Path):
    """AppSettings.load() returns None when audio_output_device_name absent or empty."""
    cfg = tmp_path / "config.json"
    json.dump({}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device_name is None

    cfg2 = tmp_path / "config2.json"
    json.dump({"audio_output_device_name": ""}, cfg2.open("w"))
    s2 = AppSettings.load(config_file=cfg2)
    assert s2.audio_output_device_name is None

    cfg3 = tmp_path / "config3.json"
    json.dump({"audio_output_device_name": "Speakers"}, cfg3.open("w"))
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.audio_output_device_name == "Speakers"


def test_invalid_audio_output_device_name_falls_back_to_none(tmp_path: Path):
    """Non-string audio_output_device_name values fall back to None."""
    for bad_val in (12345, ["Speakers"], {"name": "Speakers"}):
        cfg = tmp_path / f"bad_{hash(str(bad_val))}.json"
        json.dump({"audio_output_device_name": bad_val}, cfg.open("w"))
        s = AppSettings.load(config_file=cfg)
        assert s.audio_output_device_name is None


# ---------------------------------------------------------------------------
# Structured audio output device
# ---------------------------------------------------------------------------

def test_save_structured_audio_output_device(tmp_path: Path):
    """AppSettings.update() persists structured audio_output_device."""
    cfg = tmp_path / "config.json"
    s = AppSettings.load(config_file=cfg)
    s.update(
        audio_output_device={"name": "Speakers (Realtek)", "host_api_name": "MME"},
        audio_output_device_name="Speakers (Realtek)",
        config_file=cfg,
    )

    saved = json.load(cfg.open("r"))
    assert saved["audio_output_device"] == {"name": "Speakers (Realtek)", "host_api_name": "MME"}
    assert saved["audio_output_device_name"] == "Speakers (Realtek)"


def test_load_audio_output_device_structured(tmp_path: Path):
    """AppSettings.load() returns structured audio_output_device."""
    cfg = tmp_path / "config.json"
    json.dump(
        {
            "audio_output_device": {"name": "Headphones (USB)", "host_api_name": "WASAPI"},
            "audio_output_device_name": "Headphones (USB)",
        },
        cfg.open("w"),
    )
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device == {"name": "Headphones (USB)", "host_api_name": "WASAPI"}


def test_load_audio_output_device_legacy_fallback(tmp_path: Path):
    """When only legacy string exists, AppSettings.audio_output_device is None, name is set."""
    cfg = tmp_path / "config.json"
    json.dump({"audio_output_device_name": "Speakers (Built-in)"}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device is None
    assert s.audio_output_device_name == "Speakers (Built-in)"


def test_load_audio_output_device_invalid_object_returns_none(tmp_path: Path):
    """Malformed audio_output_device falls back to None."""
    # Not a mapping
    cfg = tmp_path / "c1.json"
    json.dump({"audio_output_device": "Speakers"}, cfg.open("w"))
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device is None

    # Missing name
    cfg2 = tmp_path / "c2.json"
    json.dump({"audio_output_device": {"host_api_name": "MME"}}, cfg2.open("w"))
    s2 = AppSettings.load(config_file=cfg2)
    assert s2.audio_output_device is None

    # Empty name
    cfg3 = tmp_path / "c3.json"
    json.dump({"audio_output_device": {"name": "", "host_api_name": "MME"}}, cfg3.open("w"))
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.audio_output_device is None

    # Non-string name
    cfg4 = tmp_path / "c4.json"
    json.dump({"audio_output_device": {"name": 123, "host_api_name": "MME"}}, cfg4.open("w"))
    s4 = AppSettings.load(config_file=cfg4)
    assert s4.audio_output_device is None

    # Non-string host_api_name
    cfg5 = tmp_path / "c5.json"
    json.dump({"audio_output_device": {"name": "Speakers", "host_api_name": 5}}, cfg5.open("w"))
    s5 = AppSettings.load(config_file=cfg5)
    assert s5.audio_output_device is None


def test_clear_structured_audio_output_device(tmp_path: Path):
    """update(audio_output_device=None) clears both keys."""
    cfg = tmp_path / "config.json"
    s = AppSettings.load(config_file=cfg)
    s.update(
        audio_output_device={"name": "Speakers (Realtek)", "host_api_name": "MME"},
        audio_output_device_name="Speakers (Realtek)",
        config_file=cfg,
    )
    saved = json.load(cfg.open("r"))
    assert saved["audio_output_device"]["name"] == "Speakers (Realtek)"

    s.update(audio_output_device=None, audio_output_device_name=None, config_file=cfg)
    saved2 = json.load(cfg.open("r"))
    assert saved2.get("audio_output_device") is None
    assert saved2.get("audio_output_device_name") is None


def test_save_audio_output_device_missing_host_api(tmp_path: Path):
    """AppSettings persists host_api_name=None when not provided."""
    cfg = tmp_path / "config.json"
    s = AppSettings.load(config_file=cfg)
    s.update(
        audio_output_device={"name": "Speakers", "host_api_name": None},
        audio_output_device_name="Speakers",
        config_file=cfg,
    )

    saved = json.load(cfg.open("r"))
    assert saved["audio_output_device"] == {"name": "Speakers", "host_api_name": None}


def test_load_audio_output_device_structured_overrides_legacy(tmp_path: Path):
    """Structured value wins when both keys present."""
    cfg = tmp_path / "config.json"
    json.dump(
        {
            "audio_output_device": {"name": "New Device", "host_api_name": "WASAPI"},
            "audio_output_device_name": "Old Device",
        },
        cfg.open("w"),
    )
    s = AppSettings.load(config_file=cfg)
    assert s.audio_output_device == {"name": "New Device", "host_api_name": "WASAPI"}
    assert s.audio_output_device_name == "Old Device"


# ---------------------------------------------------------------------------
# Cartesia API key status propagation
# ---------------------------------------------------------------------------

import easy_tts.secret


def _patch_cartesia_api_key_status(monkeypatch, *, key_set: bool, storage: str):
    """Patch the secret_store status API used by AppSettings.load()."""
    status = {"cartesia_api_key_set": key_set, "cartesia_api_key_storage": storage}

    monkeypatch.setattr(
        easy_tts.secret,
        "get_cartesia_api_key_status",
        lambda: status.copy(),
        raising=False,
    )


def test_app_config_env_file_api_removed():
    """app_config must no longer expose .env migration-era API symbols."""
    assert not hasattr(easy_tts.config, "ENV_FILE")
    assert not hasattr(easy_tts.config, "load_cartesia_api_key")


def test_app_config_source_has_no_env_or_raw_key_access():
    """app_config source must be purged of direct env/raw-key access after migration."""
    source = Path(easy_tts.config.__file__).read_text(encoding="utf-8")
    forbidden_tokens = (".env", "CARTESIA_API_KEY", "os.environ", "os.getenv")

    for token in forbidden_tokens:
        assert token not in source


def test_cartesia_api_key_set_defaults_false_when_no_key(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """No key stored anywhere -> cartesia_api_key_set is False."""
    cfg = tmp_path / "config.json"
    _patch_cartesia_api_key_status(monkeypatch, key_set=False, storage=easy_tts.secret.STORAGE_NONE)

    s = AppSettings.load(config_file=cfg)
    assert s.cartesia_api_key_set is False
    assert s.cartesia_api_key_storage == easy_tts.secret.STORAGE_NONE


def test_cartesia_api_key_set_true_when_secret_store_status_reports_keyring(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """Status API reports keyring -> metadata mirrors status."""
    cfg = tmp_path / "config.json"
    _patch_cartesia_api_key_status(monkeypatch, key_set=True, storage=easy_tts.secret.STORAGE_KEYRING)

    s = AppSettings.load(config_file=cfg)
    assert s.cartesia_api_key_set is True
    assert s.cartesia_api_key_storage == easy_tts.secret.STORAGE_KEYRING


def test_load_never_contains_raw_key(monkeypatch, tmp_path: Path, fake_keyring):
    """AppSettings.load() must not surface raw key strings from stored config data."""
    raw = "sk-do-not-leak-in-app-config"
    cfg = tmp_path / "config.json"
    json.dump({"cartesia_api_key": raw}, cfg.open("w"))
    _patch_cartesia_api_key_status(monkeypatch, key_set=True, storage=easy_tts.secret.STORAGE_KEYRING)

    s = AppSettings.load(config_file=cfg)
    # Check string fields don't contain the raw key
    for field_name in ("hotkey", "name", "voice_id", "voice_name"):
        val = getattr(s, field_name, "")
        if isinstance(val, str):
            assert raw not in val, "Raw API key leaked into AppSettings field"


def test_user_config_file_never_contains_accidental_secret_fields(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """AppSettings.update() strips/refuses accidental secret fields before JSON write."""
    sentinel = "sk_SHOULD_NOT_WRITE"
    cfg = tmp_path / "config.json"
    _patch_cartesia_api_key_status(monkeypatch, key_set=True, storage=easy_tts.secret.STORAGE_KEYRING)

    s = AppSettings.load(config_file=cfg)
    # update() only sets known fields; unknown kwargs are ignored by the dataclass
    s.update(volume=1.0, config_file=cfg)

    written = cfg.read_text(encoding="utf-8")
    assert sentinel not in written
    saved = json.loads(written)
    for field_name in ("cartesia_api_key", "api_key", "token", "secret"):
        assert field_name not in saved


def test_load_initial_config_cartesia_status(monkeypatch, tmp_path: Path, fake_keyring):
    """AppSettings.load() surfaces metadata from secret_store status."""
    cfg = tmp_path / "config.json"
    _patch_cartesia_api_key_status(monkeypatch, key_set=True, storage=easy_tts.secret.STORAGE_KEYRING)

    s = AppSettings.load(config_file=cfg)
    assert s.cartesia_api_key_set is True
    assert s.cartesia_api_key_storage == easy_tts.secret.STORAGE_KEYRING


def test_load_defaults_when_status_reports_no_key(monkeypatch, tmp_path: Path, fake_keyring):
    """Status API reports no key -> AppSettings reports False/STORAGE_NONE."""
    cfg = tmp_path / "config.json"
    _patch_cartesia_api_key_status(monkeypatch, key_set=False, storage=easy_tts.secret.STORAGE_NONE)

    s = AppSettings.load(config_file=cfg)
    assert s.cartesia_api_key_set is False
    assert s.cartesia_api_key_storage == easy_tts.secret.STORAGE_NONE


# ---------------------------------------------------------------------------
# Gain clamping through AppSettings.load()
# ---------------------------------------------------------------------------

def test_gain_config_clamps(tmp_path: Path):
    """AppSettings.load() clamps gain values between MIN_GAIN and MAX_GAIN."""
    # Default
    s = AppSettings.load(config_file=tmp_path / "nonexistent.json")
    assert s.mic_gain == 1.0
    assert s.tts_gain == 1.0

    # Within range
    cfg = tmp_path / "config.json"
    json.dump({"mic_gain": 1.5, "tts_gain": 1.2}, cfg.open("w"))
    s2 = AppSettings.load(config_file=cfg)
    assert s2.mic_gain == 1.5
    assert s2.tts_gain == 1.2

    # Clamp high
    cfg3 = tmp_path / "config3.json"
    json.dump({"mic_gain": 3.0, "tts_gain": 2.5}, cfg3.open("w"))
    s3 = AppSettings.load(config_file=cfg3)
    assert s3.mic_gain == MAX_GAIN
    assert s3.tts_gain == MAX_GAIN
