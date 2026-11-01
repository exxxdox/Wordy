"""后台录制的保存事务、状态切换与快捷键冲突。"""

from __future__ import annotations

import wave
from pathlib import Path

import pytest

from wordy.config import AppSettings
from wordy.rolling_buffer_controller import (
    RollingBufferController,
    ensure_hotkey_available,
    save_buffer_clip,
)


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings.load(config_file=tmp_path / "wordy" / "config.toml")


def _wav(path: Path, frames: int = 4800) -> Path:
    with wave.open(str(path), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(b"\x00\x00" * frames * 2)
    return path


class _FakeRecorder:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._running = False
        self.started_seconds: float | None = None
        self.stopped = False
        self.cancelled = False

    @property
    def is_running(self) -> bool:
        return self._running

    def start(self, seconds: float) -> None:
        self._running = True
        self.started_seconds = seconds

    def stop(self) -> Path:
        self._running = False
        self.stopped = True
        return self._path

    def cancel(self) -> None:
        self._running = False
        self.cancelled = True


def test_save_buffer_clip_writes_slot_and_clears_text(tmp_path):
    settings = _settings(tmp_path)
    texts = ["读这段"] * 8
    labels = [""] * 8
    settings.update_wheel(0, texts=texts, labels=labels)
    source = _wav(tmp_path / "snapshot.wav")

    filename = save_buffer_clip(settings, source, 0, 2, "录音 120000")

    wheel = settings.get_wheel(0)
    assert wheel["slots"][2] == filename
    assert wheel["labels"][2] == "录音 120000"
    assert wheel["texts"][2] == ""
    assert (tmp_path / "wordy" / "audio_clips" / filename).is_file()
    assert source.is_file(), "临时源文件由调用方决定何时删除"


def test_save_buffer_clip_replaces_old_audio_and_keeps_referenced_clips(tmp_path):
    settings = _settings(tmp_path)
    clips = tmp_path / "wordy" / "audio_clips"
    clips.mkdir(parents=True)
    replaced = clips / "replaced.wav"
    shared = clips / "shared.wav"
    replaced.write_bytes(b"old")
    shared.write_bytes(b"shared")
    slots = [""] * 8
    slots[2], slots[3] = replaced.name, shared.name
    settings.update_wheel(0, slots=slots)
    settings.add_wheel("f10")
    settings.update_wheel(1, slots=[shared.name] + [""] * 7)
    source = _wav(tmp_path / "snapshot.wav")

    save_buffer_clip(settings, source, 0, 2, "录音 120000")
    save_buffer_clip(settings, source, 0, 3, "录音 120001")

    assert not replaced.exists()
    assert shared.exists(), "其他轮盘仍在引用，不能删除"


def test_save_buffer_clip_failure_keeps_old_slot_and_temp(tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    source = _wav(tmp_path / "snapshot.wav")
    old_slots = list(settings.get_wheel(0)["slots"])

    def fail(*_args: object, **_values: object) -> None:
        raise OSError("磁盘已满")

    monkeypatch.setattr(settings, "update_wheel", fail)

    with pytest.raises(OSError):
        save_buffer_clip(settings, source, 0, 0, "录音 120000")

    assert settings.get_wheel(0)["slots"] == old_slots
    assert source.is_file()
    clips = tmp_path / "wordy" / "audio_clips"
    assert not clips.exists() or not list(clips.iterdir()), "失败时不能留下托管副本"


def test_hotkey_conflicts_are_rejected(tmp_path):
    settings = _settings(tmp_path)
    with pytest.raises(ValueError, match="输入框"):
        ensure_hotkey_available(settings, settings.hotkey)
    with pytest.raises(ValueError, match="轮盘"):
        ensure_hotkey_available(settings, settings.chat_wheel_hotkey)
    ensure_hotkey_available(settings, "f9")


def test_toggle_starts_then_stops_and_opens_picker(qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    snapshot = _wav(tmp_path / "snapshot.wav")
    recorder = _FakeRecorder(snapshot)
    states: list[bool] = []
    notices: list[str] = []
    opened: list[Path] = []
    controller = RollingBufferController(
        settings,
        dialog_parent=lambda: None,
        play_preview=lambda _path, _done, _progress: None,
        stop_preview=lambda: None,
        on_state_changed=states.append,
        on_notify=notices.append,
        recorder_factory=lambda: recorder,
    )
    monkeypatch.setattr(controller, "_open_dialog", lambda path: opened.append(path))

    controller._toggle()
    assert recorder.started_seconds == settings.rolling_buffer_seconds
    assert states == [True]

    controller._toggle()
    assert recorder.stopped is True
    assert states == [True, False]
    assert opened == [snapshot]
    assert notices


def test_stop_failure_keeps_recorder_for_retry(qapp, tmp_path, monkeypatch):
    settings = _settings(tmp_path)
    snapshot = _wav(tmp_path / "snapshot.wav")

    class FlakyRecorder(_FakeRecorder):
        attempts = 0

        def stop(self) -> Path:
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("设备尚未停止")
            return super().stop()

    recorder = FlakyRecorder(snapshot)
    notices: list[str] = []
    opened: list[Path] = []
    controller = RollingBufferController(
        settings,
        dialog_parent=lambda: None,
        play_preview=lambda _path, _done, _progress: None,
        stop_preview=lambda: None,
        on_notify=notices.append,
        recorder_factory=lambda: recorder,
    )
    monkeypatch.setattr(controller, "_open_dialog", lambda path: opened.append(path))

    controller._toggle()
    controller._toggle()
    assert controller._recorder is recorder
    assert any("结束失败" in message for message in notices)

    controller._toggle()
    assert recorder.attempts == 2
    assert opened == [snapshot]
