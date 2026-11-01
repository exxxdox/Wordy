from pathlib import Path

from PySide6.QtWidgets import QApplication, QDialog

from tests.test_audio_trim import recording
from wordy.ui.audio_trim_dialog import AudioTrimDialog


def test_dialog_validates_range_and_transfers_clip(qapp: QApplication, tmp_path: Path) -> None:
    source = recording(tmp_path)
    dialog = AudioTrimDialog(source)
    dialog.start_input.setValue(1)
    dialog.end_input.setValue(.5)
    dialog.accept()
    assert dialog.result() != QDialog.DialogCode.Accepted
    assert dialog.error_label.text()
    dialog.end_input.setValue(1.5)
    dialog.accept()
    assert dialog.result() == QDialog.DialogCode.Accepted
    clip = dialog.take_clip()
    assert clip.is_file()
    assert source.is_file()
    clip.unlink()


def test_preview_uses_selected_clip_and_cancel_cleans_it(qapp: QApplication, tmp_path: Path) -> None:
    source = recording(tmp_path)
    previews: list[Path] = []
    stops: list[bool] = []
    dialog = AudioTrimDialog(source, play_preview=previews.append, stop_preview=lambda: stops.append(True))
    dialog.preview_button.click()
    assert len(previews) == 1
    assert previews[0].exists()
    dialog.reject()
    assert len(stops) == 2
    assert not previews[0].exists()
    assert source.exists()


def test_waveform_range_drives_saved_pcm_frames(qapp, tmp_path):
    import wave
    dialog = AudioTrimDialog(recording(tmp_path))
    dialog.waveform.selectionChanged.emit(.25, .75)
    assert dialog.start_input.value() == .25
    assert dialog.end_input.value() == .75
    assert "0.500" in dialog.range_label.text()
    assert not dialog.precise_panel.isVisible()
    dialog.accept()
    clip = dialog.take_clip()
    try:
        with wave.open(str(clip)) as audio:
            assert audio.getnframes() == 50
    finally:
        clip.unlink()


def test_preview_toggle_and_stale_completion(qapp, tmp_path):
    played = []
    stopped = []
    dialog = AudioTrimDialog(recording(tmp_path), play_preview=played.append,
                             stop_preview=lambda: stopped.append(True))
    dialog._preview()
    first = played[-1]
    assert dialog.preview_button.text() == "停止试听"
    dialog._preview()
    assert dialog.preview_button.text() == "试听片段"
    dialog._preview()
    second = played[-1]
    dialog.preview_finished(str(first))
    assert dialog._previewing
    dialog.preview_finished(str(second))
    assert not dialog._previewing
    dialog.reject()
    assert all(not path.exists() for path in played)


def test_waveform_load_after_close_is_ignored(qapp, tmp_path):
    dialog = AudioTrimDialog(recording(tmp_path))
    dialog.reject()
    dialog._waveform_loaded([1.], "late error")
    assert dialog.error_label.text() != "late error"


def test_preview_playhead_follows_matching_clip_and_resets(qapp, tmp_path):
    played = []
    dialog = AudioTrimDialog(recording(tmp_path), play_preview=played.append)
    dialog.waveform.selectionChanged.emit(.25, 1.75)
    dialog._preview()
    filename = str(played[-1])
    assert dialog.waveform._playhead is None
    dialog.preview_progress(filename, .5)
    assert dialog.waveform._playhead == .75
    dialog.preview_progress("old.wav", 1.)
    assert dialog.waveform._playhead == .75
    dialog.preview_progress(filename, 9.)
    assert dialog.waveform._playhead == 1.75
    dialog._preview()
    assert dialog.waveform._playhead is None
    dialog.preview_progress(filename, .5)
    assert dialog.waveform._playhead is None
    dialog.reject()


def test_target_selector_reports_chosen_wheel_and_slot(qapp, tmp_path):
    from wordy.config import AppSettings
    from wordy.ui.target_slot_picker import TargetSlotPicker

    settings = AppSettings.load(config_file=tmp_path / "config.toml")
    settings.add_wheel("f10")
    picker = TargetSlotPicker(settings)
    dialog = AudioTrimDialog(recording(tmp_path), target_picker=picker, target_index=9)
    assert dialog.target_index == 9
    picker.set_index(0)
    assert dialog.target_index == 0
    dialog.reject()


def test_without_targets_selector_is_hidden(qapp, tmp_path):
    dialog = AudioTrimDialog(recording(tmp_path))
    assert dialog.target_picker is None
    assert dialog.target_index == 0
    dialog.reject()

