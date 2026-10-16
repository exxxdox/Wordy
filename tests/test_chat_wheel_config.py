from pathlib import Path

from wordy.config import AppSettings
from wordy._toml_serializer import read_toml


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
