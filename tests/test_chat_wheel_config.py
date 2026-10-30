from pathlib import Path

import pytest

from wordy.config import AppSettings
from wordy._toml_serializer import read_toml


def test_wheel_style_defaults_to_classic_for_missing_setting(tmp_path):
    file = tmp_path / "old-config.toml"
    file.write_text('chat_wheel_hotkey = "f9"\n', encoding="utf-8")
    assert AppSettings().chat_wheel_style == "classic"
    assert AppSettings.load(config_file=file).chat_wheel_style == "classic"


@pytest.mark.parametrize("style", ["classic", "glass", "petals"])
def test_wheel_style_round_trip_and_direct_assignment(tmp_path, style):
    from wordy.config import CHAT_WHEEL_STYLES

    assert CHAT_WHEEL_STYLES == ("classic", "glass", "petals")
    file = tmp_path / "style.toml"
    settings = AppSettings.load(config_file=file)
    settings.chat_wheel_style = style
    raw = read_toml(file)
    assert raw is not None
    assert raw["chat_wheel_style"] == style
    settings._loaded = False
    assert AppSettings.load(config_file=file).chat_wheel_style == style


@pytest.mark.parametrize("raw_value", ['"unknown"', '""', "42", "true", "[]", "{}"])
def test_wheel_style_load_rejects_invalid_values(tmp_path, raw_value):
    file = tmp_path / "invalid-style.toml"
    file.write_text(f"chat_wheel_style = {raw_value}\n", encoding="utf-8")
    assert AppSettings.load(config_file=file).chat_wheel_style == "classic"


@pytest.mark.parametrize("value", ["unknown", "", None, 42, True, [], {}])
def test_wheel_style_update_normalizes_invalid_values(tmp_path, value):
    settings = AppSettings.load(config_file=tmp_path / "style-update.toml")
    settings.update(chat_wheel_style="glass")
    settings.update(chat_wheel_style=value)
    assert settings.chat_wheel_style == "classic"
    setattr(settings, "chat_wheel_style", value)
    assert settings.chat_wheel_style == "classic"


@pytest.mark.parametrize("direct_assignment", [False, True])
def test_wheel_style_save_failure_restores_previous_value(tmp_path, monkeypatch, direct_assignment):
    from unittest.mock import Mock

    settings = AppSettings.load(config_file=tmp_path / "style-fail.toml")
    settings.update(chat_wheel_style="glass")
    monkeypatch.setattr(AppSettings, "save", Mock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        if direct_assignment:
            settings.chat_wheel_style = "petals"
        else:
            settings.update(chat_wheel_style="petals")
    assert settings.chat_wheel_style == "glass"


def test_wheel_settings_round_trip(tmp_path: Path):
    file = tmp_path / "config.toml"
    settings = AppSettings.load(config_file=file)
    settings.update(chat_wheel_hotkey="ctrl+f8", chat_wheel_slots=["clip.wav"] + [""] * 7,
                    chat_wheel_labels=["你好"] + [""] * 7)
    raw = read_toml(file)
    assert raw is not None
    assert raw["chat_wheel_slots"] == ["clip.wav"] + [""] * 7
    settings._loaded = False
    loaded = AppSettings.load(config_file=file)
    assert loaded.chat_wheel_hotkey == "ctrl+f8"
    assert loaded.chat_wheel_labels[0] == "你好"
    assert loaded.chat_wheel_slots[0] == "clip.wav"


def test_wheel_load_sanitizes_slots_and_invalid_hotkey(tmp_path: Path):
    file = tmp_path / "config.toml"
    file.write_text('chat_wheel_hotkey = "garbage"\nchat_wheel_slots = ["../escape.wav", "ok.wav", 3]\n', encoding="utf-8")
    loaded = AppSettings.load(config_file=file)
    assert loaded.chat_wheel_hotkey == "f8"
    assert loaded.chat_wheel_slots == ["", "ok.wav"] + [""] * 6


def test_wheel_name_round_trip_and_first_wheel_removal(tmp_path):
    file = tmp_path / "name.toml"
    settings = AppSettings.load(config_file=file)
    settings.update_wheel(0, name="工作")
    settings.add_wheel("f9")
    settings.update_wheel(1, name="游戏")
    assert settings.chat_wheel_name == "工作"
    settings._loaded = False
    loaded = AppSettings.load(config_file=file)
    assert loaded.get_wheel(0)["name"] == "工作"
    assert loaded.get_wheel(1)["name"] == "游戏"
    loaded.remove_wheel(0)
    assert loaded.chat_wheel_name == "游戏"
    assert loaded.get_wheel(0)["name"] == "游戏"


def test_wheel_name_load_sanitizes_and_truncates(tmp_path):
    file = tmp_path / "bad-name.toml"
    file.write_text('chat_wheel_name = 3\n'
                    '[[additional_chat_wheels]]\nhotkey="f9"\nname=5\n', encoding="utf-8")
    loaded = AppSettings.load(config_file=file)
    assert loaded.chat_wheel_name == ""
    assert loaded.get_wheel(1)["name"] == ""
    file.write_text(f'chat_wheel_name = "{"长" * 40}"\n', encoding="utf-8")
    loaded._loaded = False
    assert len(AppSettings.load(config_file=file).chat_wheel_name) == 20


def test_wheel_defaults_have_eight_independent_slots():
    first, second = AppSettings(), AppSettings()
    first.chat_wheel_slots[0] = "a.wav"
    assert second.chat_wheel_slots == [""] * 8


def test_wheel_texts_round_trip(tmp_path: Path):
    file = tmp_path / "config.toml"
    settings = AppSettings.load(config_file=file)
    settings.update(chat_wheel_texts=["马上到：3 号会议室"] + [""] * 7)
    settings._loaded = False
    assert AppSettings.load(config_file=file).chat_wheel_texts[0] == "马上到：3 号会议室"


def test_wheel_texts_survive_load_sanitizing(tmp_path: Path):
    """朗读文本是自由文本：路径字符规则不能套在它身上，含 : 和 / 的句子要活下来。"""
    file = tmp_path / "config.toml"
    file.write_text('chat_wheel_texts = ["a/b:c", "", 3]\n', encoding="utf-8")
    loaded = AppSettings.load(config_file=file)
    assert loaded.chat_wheel_texts == ["a/b:c", ""] + [""] * 6


def test_wheel_arrays_are_independent():
    settings = AppSettings()
    settings.chat_wheel_texts[0] = "朗读我"
    assert settings.chat_wheel_slots == [""] * 8
    assert settings.chat_wheel_labels == [""] * 8


def test_multiple_wheels_round_trip_and_isolated_slots(tmp_path):
    settings = AppSettings.load(config_file=tmp_path / "multi.toml")
    index = settings.add_wheel("ctrl+f9")
    settings.update_wheel(index, texts=["second"] + [""] * 7)
    assert settings.get_wheel(0)["texts"] == [""] * 8
    settings._loaded = False
    loaded = AppSettings.load(config_file=tmp_path / "multi.toml")
    assert loaded.wheel_count == 2
    assert loaded.get_wheel(1)["hotkey"] == "ctrl+f9"
    assert loaded.get_wheel(1)["texts"][0] == "second"


def test_multiple_wheels_save_failure_restores_all(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    import pytest
    settings = AppSettings.load(config_file=tmp_path / "fail.toml")
    monkeypatch.setattr(AppSettings, "save", MagicMock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        settings.add_wheel("f9")
    assert settings.wheel_count == 1


def test_slot_volumes_round_trip_across_wheels_and_first_removal(tmp_path):
    file = tmp_path / "volume.toml"
    settings = AppSettings.load(config_file=file)
    settings.add_wheel("f9")
    settings.update_wheel(0, volumes=[0.0, 0.5] + [1.0] * 6)
    settings.update_wheel(1, volumes=[1.5] + [1.0] * 7)
    settings._loaded = False
    settings = AppSettings.load(config_file=file)
    assert settings.get_wheel(0)["volumes"] == [0.0, 0.5] + [1.0] * 6
    assert settings.get_wheel(1)["volumes"][0] == 1.5
    settings.remove_wheel(0)
    assert settings.chat_wheel_volumes == [1.5] + [1.0] * 7


def test_slot_volume_load_sanitizes_and_pads(tmp_path):
    file = tmp_path / "bad-volume.toml"
    file.write_text('chat_wheel_volumes = [0, -1, 5, nan, inf, "bad", true]\n'
                    '[[additional_chat_wheels]]\nhotkey="f9"\nvolumes=[0.25, nan]\n', encoding="utf-8")
    settings = AppSettings.load(config_file=file)
    assert settings.chat_wheel_volumes == [0.0, 0.0, 2.0, 1.0, 1.0, 1.0, 1.0, 1.0]
    assert settings.get_wheel(1)["volumes"] == [0.25] + [1.0] * 7


def test_slot_volume_save_failure_restores_all_wheels(tmp_path, monkeypatch):
    from unittest.mock import MagicMock
    import pytest
    settings = AppSettings.load(config_file=tmp_path / "volume-fail.toml")
    settings.add_wheel("f9")
    monkeypatch.setattr(AppSettings, "save", MagicMock(side_effect=OSError("disk full")))
    with pytest.raises(OSError):
        settings.update_wheel(1, volumes=[0.2] * 8)
    assert settings.get_wheel(0)["volumes"] == [1.0] * 8
    assert settings.get_wheel(1)["volumes"] == [1.0] * 8
