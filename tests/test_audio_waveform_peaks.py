import struct
import wave
from pathlib import Path
from threading import Event

import pytest

from wordy.audio.waveform import waveform_peaks


def write_audio(path: Path, data: bytes, channels: int = 2, width: int = 2) -> Path:
    with wave.open(str(path), "wb") as audio:
        audio.setparams((channels, width, 100, 0, "NONE", "not compressed"))
        audio.writeframes(data)
    return path


def test_peaks_preserve_stereo_transients_and_negative_full_scale(tmp_path):
    frames = [(0, 0)] * 100
    frames[49] = (32767, -32768)
    source = write_audio(tmp_path / "stereo.wav", b"".join(struct.pack("<hh", *frame) for frame in frames))
    assert waveform_peaks(source, Event(), 2) == [1.0, 0.0]
    assert len(waveform_peaks(source, Event())) == 100


def test_silence_has_zero_peaks(tmp_path):
    source = write_audio(tmp_path / "silence.wav", b"\0" * 40)
    assert waveform_peaks(source, Event(), 3) == [0.0] * 3


def test_cancel_does_not_open_source(tmp_path):
    cancelled = Event()
    cancelled.set()
    assert waveform_peaks(tmp_path / "missing.wav", cancelled) == []


@pytest.mark.parametrize("data,width", [(b"", 2), (b"\0" * 8, 1)])
def test_empty_or_non_pcm16_rejected(tmp_path, data, width):
    source = write_audio(tmp_path / "invalid.wav", data, width=width)
    with pytest.raises(ValueError):
        waveform_peaks(source, Event())


def test_truncated_pcm_rejected(tmp_path):
    source = write_audio(tmp_path / "truncated.wav", b"\0" * 40)
    source.write_bytes(source.read_bytes()[:-4])
    with pytest.raises(ValueError, match="不完整"):
        waveform_peaks(source, Event())


def test_reads_are_bounded_and_can_cancel_mid_read(tmp_path, monkeypatch):
    source = write_audio(tmp_path / "long.wav", b"\0" * (70000 * 4))
    cancelled = Event()
    original = wave.Wave_read.readframes
    requests = []

    def read(audio, count):
        requests.append(count)
        result = original(audio, count)
        cancelled.set()
        return result

    monkeypatch.setattr(wave.Wave_read, "readframes", read)
    assert waveform_peaks(source, cancelled, 1) == []
    assert requests == [65536]
