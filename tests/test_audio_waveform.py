import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from wordy.ui.audio_waveform import WaveformSelection


def point(widget, time):
    return QPoint(round(widget._x_at(time)), round(widget.plot_rect().center().y()))


def test_drag_selects_both_directions_and_emits(qapp):
    widget = WaveformSelection(10)
    widget.resize(600, 220)
    changes = []
    widget.selectionChanged.connect(lambda a, b: changes.append((a, b)))
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=point(widget, 7))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=point(widget, 2))
    assert widget.start == pytest.approx(2, abs=.02)
    assert widget.end == pytest.approx(7, abs=.02)
    assert changes


def test_handles_clamp_without_crossing_and_keyboard_adjusts(qapp):
    widget = WaveformSelection(10)
    widget.resize(600, 220)
    widget.set_selection(2, 7)
    QTest.mousePress(widget, Qt.MouseButton.LeftButton, pos=point(widget, 2))
    QTest.mouseRelease(widget, Qt.MouseButton.LeftButton, pos=point(widget, 9))
    assert 0 <= widget.start < widget.end <= 10
    widget.set_selection(2, 7)
    QTest.keyClick(widget, Qt.Key.Key_Right)
    assert widget.start == pytest.approx(2.01)
    QTest.keyClick(widget, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier)
    assert widget.end == pytest.approx(6.99)


def test_silent_sync_and_bounded_peaks_render(qapp):
    widget = WaveformSelection(2)
    changes = []
    widget.selectionChanged.connect(lambda a, b: changes.append((a, b)))
    widget.set_selection(-1, 4)
    assert (widget.start, widget.end) == (0, 2)
    assert not changes
    widget.set_peaks([.4, float('nan'), 2] * 800)
    assert len(widget._peaks) == 1600
    assert max(widget._peaks) <= 1
    assert not widget.grab().isNull()


@pytest.mark.parametrize("peaks, expected", [
    ([.001, .004, .002], [.25, 1., .5]),
    ([0., 0.], [0., 0.]),
    ([], []),
])
def test_display_scales_to_whole_clip_peak(qapp, peaks, expected):
    widget = WaveformSelection(2)
    original = peaks.copy()
    widget.set_peaks(peaks)
    assert widget._peaks == pytest.approx(expected)
    assert peaks == original
    widget.set_selection(.5, 1)
    assert widget._peaks == pytest.approx(expected)
    assert not widget.grab().isNull()


def test_playhead_clamps_to_audio_and_clears(qapp):
    widget = WaveformSelection(2)
    widget.set_playhead(3)
    assert widget._playhead == 2
    assert not widget.grab().isNull()
    widget.set_playhead(-1)
    assert widget._playhead == 0
    widget.set_playhead(None)
    assert widget._playhead is None
