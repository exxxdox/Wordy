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
