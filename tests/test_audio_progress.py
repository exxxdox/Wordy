from io import BytesIO
from threading import Event
from unittest.mock import Mock
import wave

import pytest
from wordy.audio.player import AudioPlayer


@pytest.mark.parametrize("actual_rate", [48000, 24000])
def test_progress_tracks_submitted_frames_after_resampling(monkeypatch, actual_rate):
    source = BytesIO()
    with wave.open(source, "wb") as wav:
        wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 4800)
    source.seek(0)
    player = AudioPlayer()
    player._last_actual_rate = actual_rate
    stream, api = Mock(), Mock()
    monkeypatch.setattr(player, "_open_pyaudio_stream", lambda *a, **kw: (api, stream))
    progress = []
    assert player.play_wav(source, on_progress=progress.append)
    assert progress[0] == 0
    assert progress[-1] == pytest.approx(.1)
    assert progress == sorted(progress)
    assert len(progress) == stream.write.call_count + 1


def test_cancelled_write_does_not_emit_late_progress(monkeypatch):
    source = BytesIO()
    with wave.open(source, "wb") as wav:
        wav.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        wav.writeframes(b"\0\0" * 4800)
    source.seek(0)
    cancel = Event()
    stream = Mock()
    stream.write.side_effect = lambda _: cancel.set()
    player = AudioPlayer()
    monkeypatch.setattr(player, "_open_pyaudio_stream", lambda *a, **kw: (Mock(), stream))
    progress = []
    assert not player.play_wav(source, cancel_event=cancel, on_progress=progress.append)
    assert progress == [0.]
