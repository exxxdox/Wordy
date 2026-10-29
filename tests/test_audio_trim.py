import struct
import wave
from pathlib import Path

import pytest

from wordy.audio.trim import trim_recording, recording_duration


def recording(tmp_path: Path) -> Path:
    path = tmp_path / "source.wav"
    with wave.open(str(path), "wb") as output:
        output.setparams((2, 2, 100, 0, "NONE", "not compressed"))
        output.writeframes(b"".join(struct.pack("<hh", i, -i) for i in range(200)))
    return path


def test_trim_preserves_exact_frames_and_source(tmp_path: Path) -> None:
    source = recording(tmp_path)
    original = source.read_bytes()
    result = trim_recording(source, 0.25, 0.75)
    try:
        with wave.open(str(result), "rb") as audio:
            assert audio.getparams()[:3] == (2, 2, 100)
            assert audio.getnframes() == 50
            assert audio.readframes(1) == struct.pack("<hh", 25, -25)
        assert source.read_bytes() == original
        assert recording_duration(source) == 2
    finally:
        result.unlink()


@pytest.mark.parametrize("start,end", [(-1, 1), (0, 3), (1, 1), (1, 0), (float("nan"), 1), (0, float("inf")), (0, .001)])
def test_invalid_range_leaves_source_unchanged(tmp_path: Path, start: float, end: float) -> None:
    source = recording(tmp_path)
    original = source.read_bytes()
    with pytest.raises(ValueError):
        trim_recording(source, start, end)
    assert source.read_bytes() == original
