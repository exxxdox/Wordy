#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for app_config.py pure config I/O behavior."""

import json
import os
from pathlib import Path

import app_config
from tts_backends.constants import DEFAULT_TTS_BACKEND, TTS_BACKENDS


def test_display_hotkey():
    """Test display_hotkey formats hotkeys correctly."""
    # Simple single key
    assert app_config.display_hotkey("f6") == "F6"
    assert app_config.display_hotkey("a") == "A"
    assert app_config.display_hotkey("ctrl") == "Ctrl"
    
    # Combinations
    assert app_config.display_hotkey("ctrl+alt+delete") == "Ctrl+Alt+Delete"
    assert app_config.display_hotkey("shift+f10") == "Shift+F10"
    assert app_config.display_hotkey("win+space") == "Win+Space"
    
    # With underscores/spaces
    assert app_config.display_hotkey("left windows+g") == "Win+G"
    assert app_config.display_hotkey("control+shift") == "Ctrl+Shift"
    
    # Empty should return original
    assert app_config.display_hotkey("") == ""


def test_parse_hotkey_config_valid():
    """Test parse_hotkey_config with valid input."""
    # Complete config
    config = {"hotkey": "f8", "name": "My F8"}
    result = app_config.parse_hotkey_config(config)
    assert result == {"hotkey": "f8", "name": "My F8"}
    
    # Only hotkey, generate name
    config = {"hotkey": "ctrl+shift+f"}
    result = app_config.parse_hotkey_config(config)
    assert result == {"hotkey": "ctrl+shift+f", "name": "Ctrl+Shift+F"}
    
    # Hotkey is valid but name is invalid
    config = {"hotkey": "f9", "name": 123}
    result = app_config.parse_hotkey_config(config)
    assert result == {"hotkey": "f9", "name": "F9"}


def test_parse_hotkey_config_invalid():
    """Test parse_hotkey_config with invalid input."""
    # No hotkey
    assert app_config.parse_hotkey_config({}) is None
    # Hotkey not string
    assert app_config.parse_hotkey_config({"hotkey": 123}) is None
    # Empty hotkey string
    assert app_config.parse_hotkey_config({"hotkey": ""}) is None


def test_parse_volume_config_clamps():
    """Test parse_volume_config clamps values between MIN_VOLUME and MAX_VOLUME."""
    # Default when missing
    assert app_config.parse_volume_config({}) == app_config.DEFAULT_VOLUME
    # Default when not a number
    assert app_config.parse_volume_config({"volume": "not a number"}) == app_config.DEFAULT_VOLUME
    
    # Within range stays the same
    assert app_config.parse_volume_config({"volume": 1.0}) == 1.0
    assert app_config.parse_volume_config({"volume": 0.5}) == 0.5
    assert app_config.parse_volume_config({"volume": 2.0}) == 2.0
    assert app_config.parse_volume_config({"volume": 1.5}) == 1.5
    
    # Below min clamps to min
    assert app_config.parse_volume_config({"volume": 0.0}) == app_config.MIN_VOLUME
    assert app_config.parse_volume_config({"volume": 0.4}) == app_config.MIN_VOLUME
    
    # Above max clamps to max
    assert app_config.parse_volume_config({"volume": 3.0}) == app_config.MAX_VOLUME
    assert app_config.parse_volume_config({"volume": 2.5}) == app_config.MAX_VOLUME
    
    # Integer converts to float
    assert app_config.parse_volume_config({"volume": 1}) == 1.0


def test_parse_tts_backend_config_fallback():
    """Test parse_tts_backend_config falls back to DEFAULT_TTS_BACKEND when invalid."""
    # Valid backend returns it
    valid_backend = next(iter(TTS_BACKENDS))
    assert app_config.parse_tts_backend_config({"tts_backend": valid_backend}) == valid_backend
    
    # Missing returns default
    assert app_config.parse_tts_backend_config({}) == DEFAULT_TTS_BACKEND
    
    # Not a string returns default
    assert app_config.parse_tts_backend_config({"tts_backend": 123}) == DEFAULT_TTS_BACKEND
    
    # Invalid string returns default
    assert app_config.parse_tts_backend_config({"tts_backend": "invalid_backend"}) == DEFAULT_TTS_BACKEND


def test_parse_window_position_config():
    """Test parse_window_position_config handles valid and invalid cases."""
    # Valid position
    config = {"window_position": {"x": 100, "y": 200}}
    result = app_config.parse_window_position_config(config)
    assert result == {"x": 100, "y": 200}
    
    # No window_position returns None
    assert app_config.parse_window_position_config({}) is None
    
    # Not a dict returns None
    assert app_config.parse_window_position_config({"window_position": [100, 200]}) is None
    assert app_config.parse_window_position_config({"window_position": "100,200"}) is None
    
    # X or Y not int returns None
    assert app_config.parse_window_position_config({"window_position": {"x": "100", "y": 200}}) is None
    assert app_config.parse_window_position_config({"window_position": {"x": 100, "y": "200"}}) is None
    assert app_config.parse_window_position_config({"window_position": {"x": 100.5, "y": 200}}) is None


def test_load_json_config_missing(tmp_path: Path):
    """Test load_json_config returns None when file is missing."""
    missing_file = tmp_path / "missing.json"
    assert app_config.load_json_config(missing_file) is None


def test_load_json_config_invalid_json(tmp_path: Path):
    """Test load_json_config returns None with invalid JSON."""
    bad_json = tmp_path / "bad.json"
    bad_json.write_text("not valid json {{{", encoding="utf-8")
    assert app_config.load_json_config(bad_json) is None


def test_load_json_config_non_dict(tmp_path: Path):
    """Test load_json_config returns None when JSON is not a dict."""
    list_file = tmp_path / "list.json"
    json.dump([1, 2, 3], list_file.open("w"))
    assert app_config.load_json_config(list_file) is None
    
    str_file = tmp_path / "str.json"
    json.dump("not a dict", str_file.open("w"))
    assert app_config.load_json_config(str_file) is None


def test_load_json_config_valid(tmp_path: Path):
    """Test load_json_config returns dict when valid."""
    valid_file = tmp_path / "valid.json"
    test_data = {"hotkey": "f8", "volume": 1.5}
    json.dump(test_data, valid_file.open("w"))
    result = app_config.load_json_config(valid_file)
    assert result == test_data


def test_load_app_config_defaults_no_file(monkeypatch, tmp_path: Path):
    """Test load_app_config returns defaults when config file doesn't exist."""
    # Monkeypatch USER_CONFIG_FILE to non-existent in tmp
    test_config_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", test_config_file)
    
    config = app_config.load_app_config()
    
    # Check all defaults
    assert config["hotkey"] == app_config.DEFAULT_HOTKEY["hotkey"]
    assert config["name"] == app_config.DEFAULT_HOTKEY["name"]
    assert config["volume"] == app_config.DEFAULT_VOLUME
    assert config["fixed_center"] == app_config.DEFAULT_FIXED_CENTER
    assert config["tts_backend"] == DEFAULT_TTS_BACKEND
    assert config["window_position"] is None


def test_load_app_config_partial_config(monkeypatch, tmp_path: Path):
    """Test load_app_config merges partial config correctly."""
    test_config_file = tmp_path / "config.json"
    test_config = {
        "hotkey": "f8",
        "volume": 1.5,
    }
    json.dump(test_config, test_config_file.open("w"))
    
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", test_config_file)
    config = app_config.load_app_config()
    
    # Updated values
    assert config["hotkey"] == "f8"
    assert config["name"] == "F8"  # Generated
    assert config["volume"] == 1.5
    
    # Still defaults
    assert config["fixed_center"] == app_config.DEFAULT_FIXED_CENTER
    assert config["tts_backend"] == DEFAULT_TTS_BACKEND


def test_save_app_config_merges_existing(monkeypatch, tmp_path: Path):
    """Test save_app_config merges with existing config instead of overwriting."""
    test_config_file = tmp_path / "config.json"
    
    # Create initial config with some values
    initial_config = {
        "hotkey": "f8",
        "volume": 1.5,
        "window_position": {"x": 100, "y": 200},
    }
    json.dump(initial_config, test_config_file.open("w"))
    
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", test_config_file)
    
    # Save only volume update
    saved_path = app_config.save_app_config({"volume": 1.8})
    assert saved_path == test_config_file
    
    # Read back and check
    saved_config = json.load(test_config_file.open("r"))
    
    # Updated volume, keep other values
    assert saved_config["volume"] == 1.8
    assert saved_config["hotkey"] == "f8"
    assert saved_config["window_position"] == {"x": 100, "y": 200}
    # Defaults are also present (from load_app_config)
    assert "fixed_center" in saved_config
    assert "tts_backend" in saved_config


def test_save_app_config_creates_file(monkeypatch, tmp_path: Path):
    """Test save_app_config creates file when it doesn't exist."""
    test_config_file = tmp_path / "config.json"
    assert not test_config_file.exists()
    
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", test_config_file)
    
    saved_path = app_config.save_app_config({"hotkey": "f9"})
    assert saved_path == test_config_file
    assert test_config_file.exists()
    
    saved_config = json.load(test_config_file.open("r"))
    assert saved_config["hotkey"] == "f9"
    # Defaults present
    assert saved_config["volume"] == app_config.DEFAULT_VOLUME


def test_atomic_write_preserves_original_on_failure(monkeypatch, tmp_path: Path):
    """Test that atomic write does not corrupt existing config when replacement fails."""
    test_config_file = tmp_path / "config.json"
    # Write original valid config
    original_config = {"hotkey": "f8", "volume": 1.5, "fixed_center": True}
    json.dump(original_config, test_config_file.open("w"), ensure_ascii=False, indent=2)
    original_content = test_config_file.read_bytes()  # Save original content
    
    # Monkeypatch os.replace to raise an error
    original_replace = os.replace
    def failing_replace(src, dst):
        raise OSError("Simulated write failure")
    
    monkeypatch.setattr(os, "replace", failing_replace)
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", test_config_file)
    
    # Attempt to save new config, should raise
    try:
        app_config.save_app_config({"hotkey": "f9"})
        assert False, "Expected exception was not raised"
    except OSError:
        # Expected failure, original file should remain intact
        assert test_config_file.exists()
        current_content = test_config_file.read_bytes()
        assert current_content == original_content, "Original config was corrupted after failure"
        # Temp file should be cleaned up
        temp_files = list(tmp_path.glob(".atomic_wavtrans_*.json"))
        assert len(temp_files) == 0, "Temp file was not cleaned up after failure"


def test_parse_voice_config_valid_and_invalid():
    """parse_voice_config returns dict for valid id, fills name from id when missing."""
    assert app_config.parse_voice_config({"voice_id": "vx", "voice_name": "Vx"}) == {
        "voice_id": "vx", "voice_name": "Vx",
    }
    assert app_config.parse_voice_config({"voice_id": "vx"}) == {
        "voice_id": "vx", "voice_name": "vx",
    }
    assert app_config.parse_voice_config({"voice_id": "vx", "voice_name": 123}) == {
        "voice_id": "vx", "voice_name": "vx",
    }
    assert app_config.parse_voice_config({"voice_id": "vx", "voice_name": ""}) == {
        "voice_id": "vx", "voice_name": "vx",
    }
    assert app_config.parse_voice_config({}) is None
    assert app_config.parse_voice_config({"voice_id": ""}) is None
    assert app_config.parse_voice_config({"voice_id": 42}) is None


def test_parse_fixed_center_config_fallback():
    """parse_fixed_center_config falls back to default when missing or non-bool."""
    assert app_config.parse_fixed_center_config({}) == app_config.DEFAULT_FIXED_CENTER
    assert app_config.parse_fixed_center_config({"fixed_center": True}) is True
    assert app_config.parse_fixed_center_config({"fixed_center": False}) is False
    assert app_config.parse_fixed_center_config({"fixed_center": "yes"}) == app_config.DEFAULT_FIXED_CENTER
    assert app_config.parse_fixed_center_config({"fixed_center": 1}) == app_config.DEFAULT_FIXED_CENTER
    assert app_config.parse_fixed_center_config({"fixed_center": None}) == app_config.DEFAULT_FIXED_CENTER


def test_load_initial_config_invalid_voice(monkeypatch, tmp_path: Path):
    """load_initial_config returns None voice fields when stored voice_id is invalid."""
    cfg_file = tmp_path / "config.json"
    json.dump({"hotkey": "f7", "voice_id": "", "voice_name": "X"}, cfg_file.open("w"))
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    initial = app_config.load_initial_config()
    assert initial["voice_id"] is None
    assert initial["voice_name"] is None
    assert initial["hotkey"] == "f7"


def test_load_initial_config_voice_name_missing(monkeypatch, tmp_path: Path):
    """load_initial_config defaults voice_name to voice_id when voice_name absent."""
    cfg_file = tmp_path / "config.json"
    json.dump({"voice_id": "vid_42"}, cfg_file.open("w"))
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    initial = app_config.load_initial_config()
    assert initial["voice_id"] == "vid_42"
    assert initial["voice_name"] == "vid_42"


def test_load_voice_config_invalid_returns_nulls(monkeypatch, tmp_path: Path):
    """load_voice_config returns {None, None} when voice_id missing or invalid."""
    cfg_file = tmp_path / "config.json"
    json.dump({"hotkey": "f6"}, cfg_file.open("w"))
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    assert app_config.load_voice_config() == {"voice_id": None, "voice_name": None}


def test_save_volume_clamps_out_of_range(monkeypatch, tmp_path: Path):
    """save_volume_config clamps before persisting."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    app_config.save_volume_config(99.0)
    saved = json.load(cfg_file.open("r"))
    assert saved["volume"] == app_config.MAX_VOLUME

    app_config.save_volume_config(0.0)
    saved = json.load(cfg_file.open("r"))
    assert saved["volume"] == app_config.MIN_VOLUME


def test_save_app_config_preserves_audio_output_device_name_through_round_trip(
    monkeypatch, tmp_path: Path
):
    """save_app_config persists audio_output_device_name and survives load round-trip."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    app_config.save_app_config({"audio_output_device_name": "Speakers (Realtek)"})
    saved = json.load(cfg_file.open("r"))
    assert saved["audio_output_device_name"] == "Speakers (Realtek)"

    loaded = app_config.load_app_config()
    assert loaded["audio_output_device_name"] == "Speakers (Realtek)"

    initial = app_config.load_initial_config()
    assert initial["audio_output_device_name"] == "Speakers (Realtek)"

    app_config.save_app_config({"audio_output_device_name": None})
    saved_after_none = json.load(cfg_file.open("r"))
    assert saved_after_none.get("audio_output_device_name", None) is None

    loaded_after_none = app_config.load_app_config()
    assert loaded_after_none["audio_output_device_name"] is None

    initial_after_none = app_config.load_initial_config()
    assert initial_after_none["audio_output_device_name"] is None


def test_load_audio_output_device_name_returns_stored_value(monkeypatch, tmp_path: Path):
    """load_audio_output_device_name returns the persisted device name string."""
    cfg_file = tmp_path / "config.json"
    json.dump({"audio_output_device_name": "Headphones (USB)"}, cfg_file.open("w"))
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    assert app_config.load_audio_output_device_name() == "Headphones (USB)"

    app_config.save_audio_output_device_name("Speakers (Built-in)")
    assert app_config.load_audio_output_device_name() == "Speakers (Built-in)"
    saved = json.load(cfg_file.open("r"))
    assert saved["audio_output_device_name"] == "Speakers (Built-in)"


def test_parse_audio_output_device_name_returns_none_when_missing():
    """parse_audio_output_device_name returns None when field absent or empty."""
    assert app_config.parse_audio_output_device_name({}) is None
    assert app_config.parse_audio_output_device_name({"audio_output_device_name": ""}) is None
    assert (
        app_config.parse_audio_output_device_name({"audio_output_device_name": "Speakers"})
        == "Speakers"
    )


def test_load_app_config_invalid_audio_output_device_name_falls_back_to_none(
    monkeypatch, tmp_path: Path
):
    """Non-string stored audio_output_device_name values fall back to None on load."""
    cfg_file = tmp_path / "config.json"
    json.dump({"audio_output_device_name": 12345}, cfg_file.open("w"))
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    loaded = app_config.load_app_config()
    assert loaded["audio_output_device_name"] is None

    initial = app_config.load_initial_config()
    assert initial["audio_output_device_name"] is None

    assert app_config.load_audio_output_device_name() is None

    json.dump({"audio_output_device_name": ["Speakers"]}, cfg_file.open("w"))
    assert app_config.load_app_config()["audio_output_device_name"] is None
    json.dump({"audio_output_device_name": {"name": "Speakers"}}, cfg_file.open("w"))
    assert app_config.load_app_config()["audio_output_device_name"] is None


def test_save_audio_output_device_writes_structured_and_legacy_keys(
    monkeypatch, tmp_path: Path
):
    """save_audio_output_device persists both the structured object and legacy name string."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    app_config.save_audio_output_device(
        {"name": "Speakers (Realtek)", "host_api_name": "MME"}
    )

    saved = json.load(cfg_file.open("r"))
    assert saved["audio_output_device"] == {
        "name": "Speakers (Realtek)",
        "host_api_name": "MME",
    }
    # Legacy key written for downgrade safety.
    assert saved["audio_output_device_name"] == "Speakers (Realtek)"


def test_load_audio_output_device_returns_structured_value_when_present(
    monkeypatch, tmp_path: Path
):
    """load_audio_output_device prefers the structured object when it is well-formed."""
    cfg_file = tmp_path / "config.json"
    json.dump(
        {
            "audio_output_device": {
                "name": "Headphones (USB)",
                "host_api_name": "WASAPI",
            },
            "audio_output_device_name": "Headphones (USB)",
        },
        cfg_file.open("w"),
    )
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    assert app_config.load_audio_output_device() == {
        "name": "Headphones (USB)",
        "host_api_name": "WASAPI",
    }


def test_load_audio_output_device_falls_back_to_legacy_name_string(
    monkeypatch, tmp_path: Path
):
    """When only the legacy string exists, load_audio_output_device returns {name} with no host."""
    cfg_file = tmp_path / "config.json"
    json.dump(
        {"audio_output_device_name": "Speakers (Built-in)"},
        cfg_file.open("w"),
    )
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    assert app_config.load_audio_output_device() == {
        "name": "Speakers (Built-in)",
        "host_api_name": None,
    }


def test_load_audio_output_device_invalid_object_returns_none(
    monkeypatch, tmp_path: Path
):
    """Malformed audio_output_device objects fall back to None without using legacy key."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    # Not a mapping
    json.dump({"audio_output_device": "Speakers"}, cfg_file.open("w"))
    assert app_config.load_audio_output_device() is None

    # Missing required name field
    json.dump({"audio_output_device": {"host_api_name": "MME"}}, cfg_file.open("w"))
    assert app_config.load_audio_output_device() is None

    # Empty name
    json.dump(
        {"audio_output_device": {"name": "", "host_api_name": "MME"}},
        cfg_file.open("w"),
    )
    assert app_config.load_audio_output_device() is None

    # Non-string name
    json.dump(
        {"audio_output_device": {"name": 123, "host_api_name": "MME"}},
        cfg_file.open("w"),
    )
    assert app_config.load_audio_output_device() is None

    # Non-string host_api_name (and no name)
    json.dump(
        {"audio_output_device": {"name": "Speakers", "host_api_name": 5}},
        cfg_file.open("w"),
    )
    assert app_config.load_audio_output_device() is None

    # No keys at all and no legacy fallback
    json.dump({}, cfg_file.open("w"))
    assert app_config.load_audio_output_device() is None


def test_save_audio_output_device_none_clears_both_keys(monkeypatch, tmp_path: Path):
    """save_audio_output_device(None) clears both structured and legacy keys."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    app_config.save_audio_output_device(
        {"name": "Speakers (Realtek)", "host_api_name": "MME"}
    )
    saved = json.load(cfg_file.open("r"))
    assert saved["audio_output_device"]["name"] == "Speakers (Realtek)"
    assert saved["audio_output_device_name"] == "Speakers (Realtek)"

    app_config.save_audio_output_device(None)

    saved_after_none = json.load(cfg_file.open("r"))
    assert saved_after_none.get("audio_output_device") is None
    assert saved_after_none.get("audio_output_device_name") is None

    assert app_config.load_audio_output_device() is None
    assert app_config.load_audio_output_device_name() is None


def test_save_audio_output_device_accepts_missing_host_api_name(
    monkeypatch, tmp_path: Path
):
    """save_audio_output_device persists host_api_name=None when not provided."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    app_config.save_audio_output_device({"name": "Speakers", "host_api_name": None})

    saved = json.load(cfg_file.open("r"))
    assert saved["audio_output_device"] == {"name": "Speakers", "host_api_name": None}
    assert saved["audio_output_device_name"] == "Speakers"

    loaded = app_config.load_audio_output_device()
    assert loaded == {"name": "Speakers", "host_api_name": None}


def test_load_audio_output_device_structured_overrides_mismatched_legacy(
    monkeypatch, tmp_path: Path
):
    """Structured value wins even when legacy audio_output_device_name disagrees."""
    cfg_file = tmp_path / "config.json"
    json.dump(
        {
            "audio_output_device": {
                "name": "New Device",
                "host_api_name": "WASAPI",
            },
            "audio_output_device_name": "Old Device",
        },
        cfg_file.open("w"),
    )
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)

    assert app_config.load_audio_output_device() == {
        "name": "New Device",
        "host_api_name": "WASAPI",
    }


# ---------------------------------------------------------------------------
# Wave 2 T2: cartesia_api_key_set / cartesia_api_key_storage propagation
#
# These tests encode the contract that app_config integrates with
# secret_store in keyring-primary mode:
#   - load_app_config() exposes `cartesia_api_key_set: bool` and
#     `cartesia_api_key_storage: str`.
#   - load_initial_config() propagates the same fields.
#   - The raw key value is NEVER written into USER_CONFIG_FILE.
#
# These tests should fail RED against the current app_config skeleton
# because the new fields do not yet exist.
# ---------------------------------------------------------------------------

import secret_store


def _patch_cartesia_api_key_status(monkeypatch, *, key_set: bool, storage: str):
    """Patch the future metadata-only secret_store status API used by app_config."""
    status = {
        "cartesia_api_key_set": key_set,
        "cartesia_api_key_storage": storage,
    }

    monkeypatch.setattr(
        secret_store,
        "get_cartesia_api_key_status",
        lambda: status.copy(),
        raising=False,
    )


def test_app_config_env_file_api_removed():
    """app_config must no longer expose .env migration-era API symbols."""
    assert not hasattr(app_config, "ENV_FILE")
    assert not hasattr(app_config, "load_cartesia_api_key")


def test_app_config_source_has_no_env_or_raw_key_access():
    """app_config source must be purged of direct env/raw-key access after migration."""
    source = Path(app_config.__file__).read_text(encoding="utf-8")
    forbidden_tokens = (".env", "CARTESIA_API_KEY", "os.environ", "os.getenv")

    for token in forbidden_tokens:
        assert token not in source


def test_cartesia_api_key_set_defaults_false_when_no_key(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """No key stored anywhere -> cartesia_api_key_set is False."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)
    _patch_cartesia_api_key_status(
        monkeypatch,
        key_set=False,
        storage=secret_store.STORAGE_NONE,
    )

    config = app_config.load_app_config()
    assert config["cartesia_api_key_set"] is False
    assert config["cartesia_api_key_storage"] == secret_store.STORAGE_NONE


def test_cartesia_api_key_set_true_when_secret_store_status_reports_keyring(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """Status API reports keyring -> metadata mirrors status without loading raw key."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)
    _patch_cartesia_api_key_status(
        monkeypatch,
        key_set=True,
        storage=secret_store.STORAGE_KEYRING,
    )

    config = app_config.load_app_config()
    assert config["cartesia_api_key_set"] is True
    assert config["cartesia_api_key_storage"] == secret_store.STORAGE_KEYRING


def test_load_app_config_never_contains_raw_key(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """load_app_config() must not surface raw key strings from stored config data."""
    raw = "sk-do-not-leak-in-app-config"
    cfg_file = tmp_path / "config.json"
    json.dump({"cartesia_api_key": raw}, cfg_file.open("w"))
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)
    _patch_cartesia_api_key_status(
        monkeypatch,
        key_set=True,
        storage=secret_store.STORAGE_KEYRING,
    )

    config = app_config.load_app_config()
    for value in config.values():
        if isinstance(value, str):
            assert raw not in value, "Raw API key leaked into app config"


def test_user_config_file_never_contains_accidental_secret_fields(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """save_app_config strips/refuses accidental secret fields before JSON write."""
    sentinel = "sk_SHOULD_NOT_WRITE"
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)
    _patch_cartesia_api_key_status(
        monkeypatch,
        key_set=True,
        storage=secret_store.STORAGE_KEYRING,
    )

    app_config.save_app_config(
        {
            "cartesia_api_key": sentinel,
            "api_key": sentinel,
            "token": sentinel,
            "secret": sentinel,
            "volume": 1.0,
        }
    )

    written = cfg_file.read_text(encoding="utf-8")
    assert sentinel not in written
    saved = json.loads(written)
    for field_name in ("cartesia_api_key", "api_key", "token", "secret"):
        assert field_name not in saved


def test_load_initial_config_propagates_cartesia_api_key_status(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """load_initial_config() must surface metadata from secret_store status."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)
    _patch_cartesia_api_key_status(
        monkeypatch,
        key_set=True,
        storage=secret_store.STORAGE_KEYRING,
    )

    initial = app_config.load_initial_config()
    assert initial["cartesia_api_key_set"] is True
    assert initial["cartesia_api_key_storage"] == secret_store.STORAGE_KEYRING


def test_load_initial_config_defaults_when_status_reports_no_key(
    monkeypatch, tmp_path: Path, fake_keyring
):
    """Status API reports no key -> initial config reports False/STORAGE_NONE."""
    cfg_file = tmp_path / "config.json"
    monkeypatch.setattr(app_config, "USER_CONFIG_FILE", cfg_file)
    _patch_cartesia_api_key_status(
        monkeypatch,
        key_set=False,
        storage=secret_store.STORAGE_NONE,
    )

    initial = app_config.load_initial_config()
    assert initial["cartesia_api_key_set"] is False
    assert initial["cartesia_api_key_storage"] == secret_store.STORAGE_NONE
