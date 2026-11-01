"""截取目标选择器：空槽/音频/文字的直观呈现与目标下标。"""

from __future__ import annotations

from pathlib import Path

from wordy.config import AppSettings
from wordy.ui.target_slot_picker import TargetSlotPicker


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings.load(config_file=tmp_path / "config.toml")


def test_slots_show_existing_content_and_type(qapp, tmp_path):
    settings = _settings(tmp_path)
    slots = [""] * 8
    slots[0], slots[1] = "old.wav", "textless.wav"
    labels = [""] * 8
    labels[0] = "开场"
    texts = [""] * 8
    texts[1] = "这是一段已有的朗读文字，保存会替换"
    settings.update_wheel(0, slots=slots, labels=labels, texts=texts)

    picker = TargetSlotPicker(settings)

    assert picker.picker.buttons[0].text() == "开场"
    assert picker.picker.buttons[1].text().startswith("这是一段")
    assert picker.picker.buttons[2].text() == "空"
    picker._select_slot(0)
    assert "原有音频" in picker.detail_label.text()
    picker._select_slot(1)
    assert "原有文字" in picker.detail_label.text()
    picker._select_slot(2)
    assert "空槽" in picker.detail_label.text()


def test_audio_without_label_still_shows_as_occupied(qapp, tmp_path):
    settings = _settings(tmp_path)
    slots = [""] * 8
    slots[3] = "clip.wav"
    settings.update_wheel(0, slots=slots)

    picker = TargetSlotPicker(settings)

    assert picker.picker.buttons[3].text() == "音频"


def test_wheel_switch_and_saved_index(qapp, tmp_path):
    settings = _settings(tmp_path)
    settings.add_wheel("f10")
    settings.update_wheel(1, name="备用", labels=["备用开场"] + [""] * 7,
                          slots=["other.wav"] + [""] * 7)

    picker = TargetSlotPicker(settings)
    picker.set_index(9)

    assert picker.selected_target() == (1, 1)
    assert picker.target_index == 9
    assert picker.picker.buttons[0].text() == "备用开场"

    picker.set_index(0)
    assert picker.selected_target() == (0, 0)
    assert picker.picker.buttons[0].text() == "空"
