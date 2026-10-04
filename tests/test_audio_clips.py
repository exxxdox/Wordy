"""音频轮盘的文件所有权和解码回归。"""

from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os
import wave
from threading import Event, Timer

import pytest

from wordy.audio import clips


def make_wav(path: Path) -> bytes:
    pcm = b"\x10\x00\xf0\xff" * 100
    with wave.open(str(path), "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(pcm)
    return pcm


def test_import_keeps_managed_copy_after_source_removed(tmp_path: Path) -> None:
    source = tmp_path / "原始音频.WAV"
    make_wav(source)
    original = source.read_bytes()
    imported = clips.import_audio_clip(source, tmp_path / "profile" / "wordy.toml")
    assert imported.parent == tmp_path / "profile" / "audio_clips"
    assert imported.suffix == ".wav"
    assert imported.stem != source.stem
    source.unlink()
    assert imported.read_bytes() == original


@pytest.mark.skipif(os.name != "nt", reason="Windows 硬链接策略")
def test_windows_import_uses_hard_link(tmp_path: Path) -> None:
    source = tmp_path / "clip.wav"
    make_wav(source)
    imported = clips.import_audio_clip(source, tmp_path / "wordy.toml")
    assert os.path.samefile(source, imported)


def test_cross_volume_link_falls_back_to_copy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "clip.wav"
    make_wav(source)

    def fail_link(*args: object) -> None:
        raise OSError("cross-device link")

    monkeypatch.setattr(clips.os, "link", fail_link)
    imported = clips.import_audio_clip(source, tmp_path / "wordy.toml")
    assert imported.read_bytes() == source.read_bytes()
    assert not os.path.samefile(source, imported)


def test_import_rejects_non_audio_and_missing_source(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        clips.import_audio_clip(tmp_path / "missing.wav", tmp_path / "wordy.toml")
    source = tmp_path / "script.exe"
    source.write_bytes(b"not audio")
    with pytest.raises(ValueError, match="格式"):
        clips.import_audio_clip(source, tmp_path / "wordy.toml")
    assert not (tmp_path / "audio_clips").exists()


def test_pcm_wav_uses_existing_file(tmp_path: Path) -> None:
    source = tmp_path / "clip.wav"
    expected = make_wav(source)
    with clips.decoded_audio_clip(source) as decoded:
        assert decoded == source
        with wave.open(str(decoded), "rb") as audio:
            assert audio.readframes(audio.getnframes()) == expected
    assert source.exists()


def test_qt_decoder_outputs_pcm_and_removes_temporary_file(tmp_path: Path, qapp: object) -> None:
    # 使用真实解码器处理容器内容，避免测试依赖外部转码器或下载媒体。
    source = tmp_path / "clip.mp3"
    make_wav(source)
    with clips.decoded_audio_clip(source) as decoded:
        assert decoded != source
        assert decoded.exists()
        with wave.open(str(decoded), "rb") as audio:
            assert audio.getsampwidth() == 2
            assert audio.getnchannels() == 2
            assert audio.getframerate() == 48000
            assert 500 <= audio.getnframes() <= 600
            assert any(audio.readframes(audio.getnframes()))
    assert not decoded.exists()


def test_decode_invalid_audio_raises(tmp_path: Path, qapp: object) -> None:
    source = tmp_path / "bad.mp3"
    source.write_bytes(b"invalid audio")
    with pytest.raises(ValueError, match="解码"):
        with clips.decoded_audio_clip(source):
            pytest.fail("invalid audio must not play")


def test_decode_on_playback_worker(tmp_path: Path, qapp: object) -> None:
    source = tmp_path / "clip.mp3"
    make_wav(source)

    def decode() -> int:
        with clips.decoded_audio_clip(source) as decoded:
            with wave.open(str(decoded), "rb") as audio:
                return audio.getnframes()

    with ThreadPoolExecutor(max_workers=1) as executor:
        assert executor.submit(decode).result(timeout=5) > 0


@pytest.mark.parametrize("filename", ["../secret.wav", "..\\secret.wav", "C:\\secret.wav", "", "clip.exe"])
def test_resolve_rejects_external_paths(tmp_path: Path, filename: str) -> None:
    with pytest.raises(ValueError):
        clips.resolve_audio_clip(filename, tmp_path / "wordy.toml")


def test_remove_only_managed_link(tmp_path: Path) -> None:
    source = tmp_path / "clip.wav"
    make_wav(source)
    imported = clips.import_audio_clip(source, tmp_path / "wordy.toml")
    assert clips.resolve_audio_clip(imported.name, tmp_path / "wordy.toml") == imported
    clips.remove_audio_clip(imported.name, tmp_path / "wordy.toml")
    assert not imported.exists()
    assert source.exists()


def test_decode_cancelled_before_start(tmp_path: Path) -> None:
    source = tmp_path / "clip.wav"
    make_wav(source)
    cancelled = Event()
    cancelled.set()
    with pytest.raises(InterruptedError):
        with clips.decoded_audio_clip(source, cancel_event=cancelled):
            pytest.fail("cancelled file must not play")


def test_decoder_cancellation_does_not_wait_for_decoder_timeout(
    tmp_path: Path, qapp: object, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from PySide6 import QtMultimedia

    class PausedDecoder(QtMultimedia.QAudioDecoder):
        def start(self) -> None:
            pass

    monkeypatch.setattr(QtMultimedia, "QAudioDecoder", PausedDecoder)
    source = tmp_path / "clip.mp3"
    make_wav(source)
    cancelled = Event()
    timer = Timer(0.01, cancelled.set)
    timer.start()
    try:
        with pytest.raises(InterruptedError):
            with clips.decoded_audio_clip(source, cancel_event=cancelled):
                pytest.fail("cancelled decoder must not yield")
    finally:
        timer.cancel()
