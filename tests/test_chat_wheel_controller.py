from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from wordy.config import AppSettings
from wordy.chat_wheel_controller import ChatWheelController


def test_hotkey_conflict_preserves_previous_binding(qapp, tmp_path: Path):
    settings = AppSettings.load(config_file=tmp_path / "config.toml")
    with patch("wordy.chat_wheel_controller.NativeHotkeyListener") as listener:
        controller = ChatWheelController(settings, MagicMock(), MagicMock())
        controller.start()
        old = listener.return_value
        failed = MagicMock()
        failed.start.side_effect = RuntimeError("occupied")
        listener.return_value = failed
        with pytest.raises(RuntimeError, match="occupied"):
            controller.set_hotkey("ctrl+f9")
        assert settings.chat_wheel_hotkey == "f8"
        old.stop.assert_not_called()
        controller.stop()
        old.stop.assert_called_once()


def test_hotkey_cannot_duplicate_input_binding(qapp, tmp_path: Path):
    settings = AppSettings.load(config_file=tmp_path / "config.toml")
    controller = ChatWheelController(settings, MagicMock(), MagicMock())
    with pytest.raises(ValueError):
        controller.set_hotkey("F6")


def test_selection_plays_only_managed_existing_file(qapp, tmp_path: Path):
    settings = AppSettings.load(config_file=tmp_path / "config.toml")
    playback, error = MagicMock(), MagicMock()
    controller = ChatWheelController(settings, playback, error)
    controller.play_slot(0)
    playback.assert_not_called()
    settings.chat_wheel_slots = ["missing.wav"] + [""] * 7
    controller.play_slot(0)
    qapp.processEvents()
    error.assert_called_once()
    controller.stop()


def test_pending_hotkey_after_stop_does_not_reopen(qapp, tmp_path: Path):
    settings = AppSettings.load(config_file=tmp_path / "config.toml")
    controller = ChatWheelController(settings, MagicMock(), MagicMock())
    controller.stop()
    controller._open()
    assert not controller.wheel.isVisible()


def test_slot_resolves_saved_managed_clip_and_label(qapp, tmp_path: Path):
    settings = AppSettings.load(config_file=tmp_path / "config.toml")
    directory = tmp_path / "audio_clips"
    directory.mkdir()
    clip = directory / "clip.wav"
    clip.write_bytes(b"audio")
    settings.update(chat_wheel_slots=["clip.wav"] + [""] * 7,
                    chat_wheel_labels=["你好"] + [""] * 7)
    playback = MagicMock()
    controller = ChatWheelController(settings, playback, MagicMock())
    controller.play_slot(0)
    assert playback.call_args.args[0] == clip
    controller._open()
    assert controller.wheel._labels == ["你好"] + [""] * 7
    controller.stop()


def test_failed_hotkey_save_rolls_back_memory_and_keeps_listener(qapp, tmp_path: Path, monkeypatch):
    settings = AppSettings.load(config_file=tmp_path / "config.toml")
    with patch("wordy.chat_wheel_controller.NativeHotkeyListener") as factory:
        controller = ChatWheelController(settings, MagicMock(), MagicMock())
        controller.start()
        old = factory.return_value
        replacement = MagicMock()
        factory.return_value = replacement
        monkeypatch.setattr(AppSettings, "save", MagicMock(side_effect=OSError("disk full")))
        with pytest.raises(OSError, match="disk full"):
            controller.set_hotkey("f9")
        assert settings.chat_wheel_hotkey == "f8"
        old.stop.assert_not_called()
        replacement.stop.assert_called_once()
        controller.stop()
