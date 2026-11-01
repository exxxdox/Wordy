"""聊天轮盘设置：导入结果、失败回滚与快捷键确认。"""

from pathlib import Path
import threading
from typing import cast
from unittest.mock import Mock
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from wordy.config import AppSettings
from wordy.ui.chat_wheel_settings import ChatWheelSettings


@pytest.fixture(autouse=True)
def isolated_audio_config(monkeypatch, tmp_path):
    monkeypatch.setattr("wordy.config.USER_CONFIG_FILE", tmp_path / "wordy.toml")
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.remove_audio_clip", Mock())


def test_hotkey_is_committed_only_after_registration(qapp):
    """快捷键与其他设置一样在编辑结束时自动提交，失败回退输入框。"""
    settings = AppSettings()
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    panel.hotkey_input.setKeySequence("Ctrl+F8")
    panel.hotkey_input.editingFinished.emit()
    changed.assert_called_once_with("chat_wheel_hotkey", "ctrl+f8")
    assert settings.chat_wheel_hotkey == "f8"
    panel.set_hotkey_result("快捷键已被占用")
    assert "占用" in panel.status_label.text()
    assert panel.hotkey_input.keySequence().toString().lower() == "f8"


def test_action_buttons_use_short_text_and_icons(qapp):
    """动作行用短文案与图标表态：导入不再写「选择文件」，录制/试听带图。"""
    panel = ChatWheelSettings(AppSettings(chat_wheel_slots=["clip.wav"] * 8), Mock())
    assert panel.choose_button.text() == "导入"
    assert not panel.record_button.icon().isNull()
    assert not panel.preview_button.icon().isNull()


def test_unchanged_hotkey_is_not_reapplied(qapp):
    """失焦也会触发 editingFinished，值没变时不重复注册。"""
    changed = Mock()
    panel = ChatWheelSettings(AppSettings(), changed)
    panel.hotkey_input.editingFinished.emit()
    changed.assert_not_called()


@pytest.mark.parametrize("style", ["glass", "petals", "game"])
def test_style_choice_is_saved_and_restored_without_touching_slots(qapp, tmp_path, style):
    config_file = tmp_path / "styles.toml"
    settings = AppSettings(chat_wheel_labels=["保留名称"] * 8)
    settings.save(config_file=config_file)
    settings._config_file = config_file
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    assert [panel.style_combo.itemText(i) for i in range(panel.style_combo.count())] == [
        "经典玻璃", "轻薄玻璃", "悬浮花瓣", "竞技轮盘",
    ]
    panel.style_combo.setCurrentIndex(panel.style_combo.findData(style))
    assert settings.chat_wheel_style == style
    assert panel.wheel_picker.wheel_style == style
    assert AppSettings.load(config_file=config_file).chat_wheel_style == style
    assert settings.chat_wheel_labels == ["保留名称"] * 8
    changed.assert_called_once_with("chat_wheel_style", style)
    restored = ChatWheelSettings(settings, Mock())
    assert restored.style_combo.currentData() == style
    panel.close()
    restored.close()


@pytest.mark.parametrize("style", ["glass", "petals", "game"])
def test_style_save_failure_restores_choice_and_preview(qapp, monkeypatch, style):
    settings = AppSettings()
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    monkeypatch.setattr(settings, "save", Mock(side_effect=OSError("磁盘不可写")))
    panel.style_combo.setCurrentIndex(panel.style_combo.findData(style))
    assert settings.chat_wheel_style == "classic"
    assert panel.style_combo.currentData() == "classic"
    assert panel.wheel_picker.wheel_style == "classic"
    assert "磁盘不可写" in panel.status_label.text()
    changed.assert_not_called()
    panel.close()


def test_cleared_hotkey_restores_stored_sequence(qapp):
    """空快捷键不合法，清空后恢复已存值而不是写入空配置。"""
    changed = Mock()
    panel = ChatWheelSettings(AppSettings(), changed)
    panel.hotkey_input.clear()
    panel.hotkey_input.editingFinished.emit()
    changed.assert_not_called()
    assert panel.hotkey_input.keySequence().toString().lower() == "f8"


def test_wheel_name_can_be_customized_and_cleared(qapp):
    """轮盘名直接用组合框文本编辑：非空即自定义名，清空回退「轮盘 N」。"""
    settings = AppSettings()
    settings.add_wheel("f9")
    panel = ChatWheelSettings(settings, Mock())
    assert panel.wheel_combo.itemText(0) == "轮盘 1"

    edit = panel.wheel_combo.lineEdit()
    assert edit is not None
    panel.wheel_combo.setEditText("工作")
    edit.editingFinished.emit()
    assert settings.get_wheel(0)["name"] == "工作"
    assert panel.wheel_combo.itemText(0) == "工作"

    panel.wheel_combo.setCurrentIndex(1)
    panel.wheel_combo.setEditText("")
    edit.editingFinished.emit()
    assert settings.get_wheel(1)["name"] == ""
    assert panel.wheel_combo.itemText(1) == "轮盘 2"


def test_wheel_selection_edits_only_selected_wheel(qapp):
    settings = AppSettings()
    settings.add_wheel("f9")
    panel = ChatWheelSettings(settings, Mock())
    panel.wheel_combo.setCurrentIndex(1)
    panel.text_mode_button.click()  # 空槽切文字不弹确认
    panel.text_input.setText("第二轮盘")
    panel._save_text()
    assert settings.get_wheel(1)["texts"][0] == "第二轮盘"
    assert settings.chat_wheel_texts[0] == ""
    assert panel.hotkey_input.keySequence().toString().lower() == "f9"


def test_audio_volume_is_independent_for_each_slot_and_wheel(qapp):
    settings = AppSettings(chat_wheel_slots=["shared.wav"] * 8)
    settings.add_wheel("f9")
    settings.update_wheel(1, slots=["shared.wav"] * 8)
    panel = ChatWheelSettings(settings, Mock())
    assert panel.volume_slider.value() == 100
    panel.volume_slider.setValue(40)
    assert settings.get_wheel(0)["volumes"][:2] == [0.4, 1.0]
    panel._select_slot(1)
    assert panel.volume_slider.value() == 100
    panel.volume_slider.setValue(160)
    panel._select_wheel(1)
    assert panel.volume_slider.value() == 100
    panel.volume_slider.setValue(0)
    panel._select_wheel(0)
    assert panel.volume_slider.value() == 40
    assert settings.get_wheel(1)["volumes"][0] == 0
    assert panel.volume_value_label.text() == "40%"


def test_volume_save_failure_restores_slider_and_configuration(qapp, monkeypatch):
    settings = AppSettings(chat_wheel_slots=["audio.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())
    monkeypatch.setattr(AppSettings, "save", Mock(side_effect=OSError("磁盘已满")))
    panel.volume_slider.setValue(50)
    assert settings.get_wheel(0)["volumes"][0] == 1
    assert panel.volume_slider.value() == 100
    assert panel.volume_value_label.text() == "100%"


def test_dragging_volume_saves_once_on_release(qapp, monkeypatch):
    settings = AppSettings(chat_wheel_slots=["audio.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())
    save = Mock(wraps=settings.save)
    monkeypatch.setattr(settings, "save", save)
    panel.volume_slider.setSliderDown(True)
    panel.volume_slider.setValue(70)
    panel.volume_slider.setValue(30)
    assert panel.volume_value_label.text() == "30%"
    assert settings.get_wheel(0)["volumes"][0] == 1
    save.assert_not_called()
    panel.volume_slider.setSliderDown(False)
    assert settings.get_wheel(0)["volumes"][0] == .3
    save.assert_called_once()


def test_playback_finish_does_not_reset_volume_being_dragged(qapp):
    settings = AppSettings(chat_wheel_slots=["audio.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())
    panel._toggle_preview()
    panel.volume_slider.setSliderDown(True)
    panel.volume_slider.setValue(30)
    panel.set_preview_playing(False)
    assert panel.volume_slider.value() == 30
    assert panel.volume_value_label.text() == "30%"
    panel.volume_slider.setSliderDown(False)
    assert settings.get_wheel(0)["volumes"][0] == .3


def test_trim_preview_uses_slot_volume(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QDialog

    settings = AppSettings(chat_wheel_slots=["audio.wav"] + [""] * 7)
    settings.update_wheel(0, volumes=[.35] + [1.] * 7)
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    preview = tmp_path / "preview.wav"

    class FakeDialog:
        def __init__(self, source, parent, *, play_preview, stop_preview):
            self.play = play_preview

        def exec(self):
            self.play(preview)
            return QDialog.DialogCode.Rejected

        def deleteLater(self):
            pass

    monkeypatch.setattr("wordy.ui.chat_wheel_settings.AudioTrimDialog", FakeDialog)
    assert panel._open_trim_dialog(tmp_path / "original.wav") is None
    assert changed.call_args.args[0] == "chat_wheel_preview_file"
    assert changed.call_args.args[1][:2] == (str(preview), .35)
    assert callable(changed.call_args.args[1][2])


def test_volume_control_only_edits_available_audio(qapp):
    settings = AppSettings(chat_wheel_slots=["audio.wav"] + [""] * 7,
                           chat_wheel_texts=["", "文字"] + [""] * 6)
    panel = ChatWheelSettings(settings, Mock())
    assert panel.volume_slider.isEnabled()
    panel._select_slot(1)
    assert not panel.volume_slider.isEnabled()
    panel._select_slot(2)
    assert not panel.volume_slider.isEnabled()
    panel._select_slot(0)
    panel._recording = 0
    panel._refresh_all_slots()
    assert not panel.volume_slider.isEnabled()


def test_wheel_switch_is_locked_during_import(qapp):
    settings = AppSettings()
    settings.add_wheel("f9")
    panel = ChatWheelSettings(settings, Mock())
    panel._busy.add(0)
    panel._refresh_all_slots()
    assert not panel.wheel_combo.isEnabled()
    panel._select_wheel(1)
    assert panel.wheel_index == 0


def test_unused_audio_is_retained_when_another_wheel_references_it(qapp, monkeypatch):
    settings = AppSettings(chat_wheel_slots=["shared.wav"] + [""] * 7)
    settings.add_wheel("f9")
    settings.update_wheel(1, slots=["shared.wav"] + [""] * 7)
    cleanup = Mock()
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.remove_audio_clip", cleanup)
    panel = ChatWheelSettings(settings, Mock())
    assert panel._replace_clip(0, "", "")
    cleanup.assert_not_called()


def test_import_feedback_goes_to_host_queue(qapp, monkeypatch):
    """挂进设置窗口后，轮盘导入的反馈走浮动弹窗而不是原地标签。"""
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    from wordy.ui.status_toast import StatusToastQueue

    # 导入在工作线程里跑；让它立刻失败，避免测试依赖真实音频文件。
    monkeypatch.setattr(
        "wordy.ui.chat_wheel_settings.import_audio_clip",
        Mock(side_effect=OSError("无法读取")),
    )
    host = QWidget()
    host.resize(420, 400)
    queue = StatusToastQueue(host)
    container = QWidget(host)
    layout = QVBoxLayout(container)
    panel = ChatWheelSettings(AppSettings(), Mock())
    layout.addWidget(panel)
    host.show()
    qapp.processEvents()

    try:
        panel.import_file(0, Path("clip.mp3"))

        assert queue.texts() == ["正在导入音频 1…"]
        assert panel.status_label.isVisible() is False
    finally:
        host.close()


def test_import_updates_file_and_label_together(qapp):
    settings = AppSettings()
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    panel._finish_import(2, "managed.wav", "胜利", "")
    changed.assert_not_called()
    assert settings.chat_wheel_slots[2] == "managed.wav"
    assert settings.chat_wheel_labels[2] == "胜利"
    panel.slot_buttons[2].click()
    assert panel.label_input.text() == "胜利"
    assert panel.preview_button.isEnabled()


def test_wheel_selects_slot_and_shows_only_its_editor(qapp):
    settings = AppSettings(chat_wheel_slots=["up.wav", "right-up.wav"] + [""] * 6,
                           chat_wheel_labels=["上方", "右上方"] + [""] * 6)
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)

    assert len(panel.slot_buttons) == 8
    assert panel.selected_index == 0
    panel.slot_buttons[1].click()

    assert panel.selected_index == 1
    assert panel.editor_title.text() == "音频 2"
    assert panel.label_input.text() == "右上方"
    assert panel.slot_buttons[1].property("selected") is True
    assert panel.slot_buttons[1].isChecked()
    assert panel.slot_buttons[0].property("selected") is False
    panel.preview_button.click()
    changed.assert_called_once_with("chat_wheel_preview", 1)


def test_wheel_sector_hit_test_matches_runtime_directions(qapp):
    panel = ChatWheelSettings(AppSettings(), Mock())

    # 预览按 634×540 画布等比缩放，命中半径随缩放变小（内 50px / 外 146px）。
    assert panel.wheel_picker._slot_at(QPoint(200, 60)) == 0
    assert panel.wheel_picker._slot_at(QPoint(340, 200)) == 2
    assert panel.wheel_picker._slot_at(QPoint(200, 340)) == 4
    assert panel.wheel_picker._slot_at(QPoint(60, 200)) == 6
    assert panel.wheel_picker._slot_at(QPoint(200, 200)) is None
    assert panel.wheel_picker._slot_at(QPoint(200, 40)) is None


def test_selected_sector_survives_mouse_leaving(qapp):
    panel = ChatWheelSettings(AppSettings(), Mock())
    panel.show()
    qapp.processEvents()

    QTest.mouseClick(panel.wheel_picker, Qt.MouseButton.LeftButton, pos=QPoint(340, 200))
    QTest.mouseMove(panel.label_input, QPoint(4, 4))
    qapp.processEvents()

    assert panel.selected_index == 2
    assert panel.slot_buttons[2].isChecked()


def test_failed_import_preserves_existing_clip(qapp):
    settings = AppSettings(chat_wheel_slots=["old.wav"] + [""] * 7,
                           chat_wheel_labels=["旧音频"] + [""] * 7)
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    panel._finish_import(0, "", "新音频", "音频无法读取")
    changed.assert_not_called()
    assert panel.label_input.text() == "旧音频"
    assert panel.preview_button.isEnabled()
    assert "无法读取" in panel.status_label.text()


def test_clear_and_preview_target_only_chosen_slot(qapp):
    settings = AppSettings(chat_wheel_slots=["old.wav"] + [""] * 7,
                           chat_wheel_labels=["旧音频"] + [""] * 7)
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    panel.preview_button.click()
    changed.assert_called_with("chat_wheel_preview", 0)
    panel.clear_button.click()
    assert settings.chat_wheel_slots[0] == ""
    assert settings.chat_wheel_labels[0] == ""
    assert not panel.preview_button.isEnabled()
    assert panel.label_input.text() == ""


def test_label_edit_preserves_other_labels(qapp):
    settings = AppSettings(chat_wheel_slots=["old.wav"] + [""] * 7,
                           chat_wheel_labels=["旧音频", "第二项"] + [""] * 6)
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    panel.label_input.setText("新名称")
    panel.label_input.editingFinished.emit()
    changed.assert_not_called()
    assert settings.chat_wheel_labels == ["新名称", "第二项"] + [""] * 6


def test_background_import_keeps_source_stem(qapp, monkeypatch, tmp_path):
    settings = AppSettings()
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    worker = Mock()
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.threading.Thread", worker)
    panel.import_file(4, Path("测试音频.wav"))
    assert not panel.slot_buttons[4].isEnabled()
    worker.return_value.start.assert_called_once()


def test_save_failure_restores_slot_and_discards_new_copy(qapp, monkeypatch):
    settings = AppSettings(chat_wheel_slots=["old.wav"] + [""] * 7,
                           chat_wheel_labels=["旧音频"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())
    cleanup = Mock()
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.remove_audio_clip", cleanup)
    monkeypatch.setattr(AppSettings, "save", Mock(side_effect=OSError("磁盘已满")))
    panel._finish_import(0, "new.wav", "新音频", "")
    assert settings.chat_wheel_slots[0] == "old.wav"
    assert settings.chat_wheel_labels[0] == "旧音频"
    assert panel.label_input.text() == "旧音频"
    cleanup.assert_called_once_with("new.wav", None)


def test_label_save_failure_restores_visible_and_stored_label(qapp, monkeypatch):
    settings = AppSettings(chat_wheel_slots=["old.wav"] + [""] * 7,
                           chat_wheel_labels=["旧音频"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())
    monkeypatch.setattr(AppSettings, "save", Mock(side_effect=OSError("磁盘已满")))
    panel.label_input.setText("新名称")
    panel.label_input.editingFinished.emit()
    assert settings.chat_wheel_labels[0] == "旧音频"
    assert panel.label_input.text() == "旧音频"


def test_qt_page_key_name_is_converted_for_native_listener(qapp):
    changed = Mock()
    panel = ChatWheelSettings(AppSettings(), changed)
    panel.hotkey_input.setKeySequence("Ctrl+PgUp")
    panel.hotkey_input.editingFinished.emit()
    changed.assert_called_once_with("chat_wheel_hotkey", "ctrl+page up")


class _InlineThread:
    """就地执行 target 的假线程，让导入结果在断言前同步落地。"""

    def __init__(self, target=None, name=None, daemon=None):
        self._target = target

    def start(self):
        assert self._target is not None
        self._target()


class _FakeRecorder:
    def __init__(self, result=None, start_error=None):
        self.result = result
        self.start_error = start_error
        self.started = False
        self.cancelled = False
        self.elapsed_seconds = 0.0

    def start(self):
        if self.start_error is not None:
            raise self.start_error
        self.started = True

    def stop(self):
        assert self.result is not None
        return self.result

    def cancel(self):
        self.cancelled = True


def _recording_panel(monkeypatch, recorder):
    """装好假录音器与就地导入线程的面板，返回 (panel, settings)。"""
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.SystemAudioRecorder", lambda: recorder)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.threading.Thread", _InlineThread)
    monkeypatch.setattr(
        "wordy.ui.chat_wheel_settings.import_audio_clip",
        lambda source, config_file=None: Path("managed.wav"),
    )
    settings = AppSettings()
    return ChatWheelSettings(settings, Mock()), settings


def test_record_button_saves_recording_with_timestamp_label(qapp, monkeypatch, tmp_path):
    source = tmp_path / "recording.wav"
    source.write_bytes(b"pcm")
    recorder = _FakeRecorder(result=source)
    panel, settings = _recording_panel(monkeypatch, recorder)

    panel.record_button.click()
    assert recorder.started is True
    assert panel._recording == 0
    assert panel.record_button.text().startswith("停止")
    assert not panel.choose_button.isEnabled()
    assert not panel.preview_button.isEnabled()
    assert not panel.clear_button.isEnabled()

    panel.record_button.click()

    assert panel._recording is None
    assert panel.record_button.text() == "录制"
    assert settings.chat_wheel_slots[0] == "managed.wav"
    assert settings.chat_wheel_labels[0].startswith("录音 ")
    assert not source.exists(), "录音临时文件必须在导入后删除"
    assert panel.choose_button.isEnabled()


def test_recording_saves_whole_clip_without_trim_dialog(qapp, monkeypatch, tmp_path):
    """录制结束直接保存整段音频，截取改为按需操作。"""
    source = tmp_path / "recording.wav"
    source.write_bytes(b"pcm")
    panel, settings = _recording_panel(monkeypatch, _FakeRecorder(result=source))
    settings.update(chat_wheel_slots=["old.wav"] + [""] * 7)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.AudioTrimDialog",
                        Mock(side_effect=AssertionError("录制后不应打开截取窗口")))
    panel._toggle_record()
    panel._toggle_record()
    assert settings.chat_wheel_slots[0] == "managed.wav"
    assert not source.exists()


def test_trim_button_opens_dialog_for_saved_audio(qapp, monkeypatch, tmp_path):
    """「截取」随时对槽位已保存的音频重裁，结果替换该槽。"""
    from contextlib import contextmanager

    from PySide6.QtWidgets import QDialog

    settings = AppSettings(chat_wheel_slots=["saved.wav"] + [""] * 7)
    settings.update(chat_wheel_labels=["旧片段"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())
    source, clip = tmp_path / "saved.wav", tmp_path / "clip.wav"
    source.write_bytes(b"pcm")
    clip.write_bytes(b"segment")
    seen: list[Path] = []

    @contextmanager
    def fake_decoded(path, cancel_event=None):
        seen.append(path)
        yield path

    class FakeDialog:
        def __init__(self, source, parent, *, play_preview, stop_preview):
            pass

        def exec(self):
            return QDialog.DialogCode.Accepted

        def take_clip(self):
            return clip

        def deleteLater(self):
            pass

    monkeypatch.setattr("wordy.ui.chat_wheel_settings.resolve_audio_clip",
                        lambda filename, config_file=None: source)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.decoded_audio_clip", fake_decoded)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.AudioTrimDialog", FakeDialog)
    imports = Mock()
    panel.import_file = imports

    panel.trim_button.click()

    assert seen == [source]
    imports.assert_called_once_with(0, clip, label="旧片段", temporary=True)


def test_trim_cancel_leaves_saved_audio_untouched(qapp, monkeypatch, tmp_path):
    from contextlib import contextmanager

    from PySide6.QtWidgets import QDialog

    settings = AppSettings(chat_wheel_slots=["saved.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())
    source = tmp_path / "saved.wav"
    source.write_bytes(b"pcm")

    @contextmanager
    def fake_decoded(path, cancel_event=None):
        yield path

    class FakeDialog:
        def __init__(self, source, parent, *, play_preview, stop_preview):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

        def deleteLater(self):
            pass

    monkeypatch.setattr("wordy.ui.chat_wheel_settings.resolve_audio_clip",
                        lambda filename, config_file=None: source)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.decoded_audio_clip", fake_decoded)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.AudioTrimDialog", FakeDialog)
    imports = Mock()
    panel.import_file = imports

    panel.trim_button.click()

    imports.assert_not_called()
    assert settings.chat_wheel_slots[0] == "saved.wav"


def test_trim_decode_shows_progress_bar_and_locks_actions(qapp, monkeypatch, tmp_path):
    """解码压缩音频期间显示不确定进度条，并锁住动作行防止重入。"""
    from contextlib import contextmanager

    from PySide6.QtWidgets import QDialog

    settings = AppSettings(chat_wheel_slots=["saved.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())
    source = tmp_path / "saved.wav"
    source.write_bytes(b"pcm")
    observed: list[tuple[bool, bool]] = []

    @contextmanager
    def fake_decoded(path, cancel_event=None):
        observed.append((not panel.decode_bar.isHidden(), panel.trim_button.isEnabled()))
        yield path

    class FakeDialog:
        def __init__(self, source, parent, *, play_preview, stop_preview):
            pass

        def exec(self):
            return QDialog.DialogCode.Rejected

        def deleteLater(self):
            pass

    monkeypatch.setattr("wordy.ui.chat_wheel_settings.resolve_audio_clip",
                        lambda filename, config_file=None: source)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.decoded_audio_clip", fake_decoded)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.AudioTrimDialog", FakeDialog)

    panel.trim_button.click()

    assert observed == [(True, False)]
    assert panel.decode_bar.isHidden()
    assert panel.trim_button.isEnabled()


def test_trim_button_only_enabled_for_audio_slots(qapp):
    settings = AppSettings(chat_wheel_slots=["a.wav"] + [""] * 7,
                           chat_wheel_texts=["", "文字"] + [""] * 6)
    panel = ChatWheelSettings(settings, Mock())
    assert panel.trim_button.isEnabled()
    panel._select_slot(1)
    assert not panel.trim_button.isEnabled()
    panel._select_slot(2)
    assert not panel.trim_button.isEnabled()


def test_slot_names_are_numbers_not_directions(qapp):
    settings = AppSettings(chat_wheel_slots=["a.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())

    assert panel.editor_title.text() == "音频 1"
    assert panel.preview_button.accessibleName() == "试听音频 1"
    panel.slot_buttons[4].click()
    assert panel.editor_title.text() == "音频 5"
    assert panel.clear_button.accessibleName() == "清空音频 5"


def test_preview_button_toggles_to_stop_and_back(qapp):
    settings = AppSettings(chat_wheel_slots=["a.wav"] + [""] * 7)
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)

    panel.preview_button.click()

    assert panel.preview_button.text() == "停止"
    assert panel.preview_button.isEnabled()
    changed.assert_called_once_with("chat_wheel_preview", 0)

    panel.preview_button.click()

    assert panel.preview_button.text() == "试听"
    changed.assert_called_with("chat_wheel_preview_stop", 0)


def test_preview_button_resets_when_playback_ends(qapp):
    """播完（或被打断）后主程序回调复位按钮，否则「停止」会一直挂着。"""
    settings = AppSettings(chat_wheel_slots=["a.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())
    panel.preview_button.click()

    panel.set_preview_playing(False)

    assert panel.preview_button.text() == "试听"
    assert panel._previewing is False


def test_starting_a_recording_stops_the_preview(qapp, monkeypatch):
    """录音抓的是系统声音，正在试听的片段会被录进去，所以必须先停掉。"""
    recorder = _FakeRecorder(result=Path("unused.wav"))
    panel, settings = _recording_panel(monkeypatch, recorder)
    settings.update(chat_wheel_slots=["a.wav"] + [""] * 7)
    panel._select_slot(0)
    changed = cast(Mock, panel._on_field_changed)

    panel.preview_button.click()
    assert panel._previewing is True

    panel.record_button.click()

    assert panel._previewing is False
    changed.assert_any_call("chat_wheel_preview_stop", 0)


def test_text_input_shares_the_label_row(qapp):
    """轮盘页纵向余量为 0，文字框必须和名称同行，不能新增一行。"""
    panel = ChatWheelSettings(AppSettings(), Mock())

    assert panel.text_input.parent() is panel.label_input.parent()
    assert panel.text_input.maxLength() > 0


def test_slot_mode_shows_only_matching_editor(qapp):
    """类型选择器必须让槽内容一眼可辨：只显示该类型用得上的编辑控件。"""
    settings = AppSettings(chat_wheel_slots=["a.wav"] + [""] * 7,
                           chat_wheel_texts=["", "马上到"] + [""] * 6)
    panel = ChatWheelSettings(settings, Mock())

    assert panel.audio_mode_button.isChecked()
    assert panel.text_input.isHidden()
    assert not panel.choose_button.isHidden()

    panel.slot_buttons[1].click()

    assert panel.text_mode_button.isChecked()
    assert not panel.text_input.isHidden()
    assert panel.choose_button.isHidden()
    assert panel.record_button.isHidden()
    assert panel.trim_button.isHidden()
    assert panel.volume_slider.isHidden()


def test_switching_to_text_clears_audio_after_confirmation(qapp, monkeypatch):
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.QMessageBox.question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    settings = AppSettings(chat_wheel_slots=["a.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())

    panel.text_mode_button.click()

    assert settings.chat_wheel_slots[0] == ""
    assert panel.text_mode_button.isChecked()
    assert not panel.text_input.isHidden()


def test_cancelling_the_switch_keeps_audio(qapp, monkeypatch):
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.QMessageBox.question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.No)
    settings = AppSettings(chat_wheel_slots=["a.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())

    panel.text_mode_button.click()

    assert settings.chat_wheel_slots[0] == "a.wav"
    assert panel.audio_mode_button.isChecked()
    assert panel.text_input.isHidden()


def test_switching_to_audio_clears_text_and_restores_actions(qapp):
    settings = AppSettings(chat_wheel_texts=["马上到"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())

    panel.audio_mode_button.click()

    assert settings.chat_wheel_texts[0] == ""
    assert panel.audio_mode_button.isChecked()
    assert not panel.choose_button.isHidden()
    assert panel.text_input.isHidden()


def test_empty_slot_mode_is_a_local_choice_until_content(qapp):
    settings = AppSettings()
    panel = ChatWheelSettings(settings, Mock())

    panel.text_mode_button.click()

    assert panel.text_mode_button.isChecked()
    assert not panel.text_input.isHidden()
    # 空槽切换类型不写配置。
    assert settings.chat_wheel_texts == [""] * 8
    assert settings.chat_wheel_slots == [""] * 8

    panel.slot_buttons[1].click()  # 切槽回到默认音频
    assert panel.audio_mode_button.isChecked()


def test_saving_text_replaces_the_slot_clip(qapp, monkeypatch):
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.QMessageBox.question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    settings = AppSettings(chat_wheel_slots=["a.wav"] + [""] * 7,
                           chat_wheel_labels=["旧片段"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())

    panel.text_mode_button.click()
    panel.text_input.setText("马上到")
    panel.text_input.editingFinished.emit()

    assert settings.chat_wheel_texts[0] == "马上到"
    assert settings.chat_wheel_slots[0] == ""
    assert panel.status_label.text() == "文字已保存。"


def test_focusing_the_text_box_without_editing_keeps_the_clip(qapp):
    """失焦也会触发 editingFinished；没改动就不能把音频删掉。"""
    settings = AppSettings(chat_wheel_slots=["a.wav"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())

    panel.text_input.editingFinished.emit()

    assert settings.chat_wheel_slots[0] == "a.wav"
    assert settings.chat_wheel_texts[0] == ""


def test_importing_a_clip_clears_the_slot_text(qapp):
    settings = AppSettings(chat_wheel_texts=["马上到"] + [""] * 7)
    panel = ChatWheelSettings(settings, Mock())

    panel._finish_import(0, "managed.wav", "新片段", "")

    assert settings.chat_wheel_slots[0] == "managed.wav"
    assert settings.chat_wheel_texts[0] == ""


def test_text_slot_preview_can_be_stopped(qapp):
    """朗读可打断：按钮变成「停止」，点击发停止信号，完成回调才复位并解锁。"""
    settings = AppSettings(chat_wheel_texts=["马上到"] + [""] * 7)
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)

    panel.preview_button.click()

    assert panel.preview_button.text() == "停止"
    assert panel.preview_button.isEnabled()
    changed.assert_called_once_with("chat_wheel_preview", 0)

    panel.preview_button.click()

    changed.assert_called_with("chat_wheel_preview_stop", 0)
    # 回调到达前保持锁定：回调不带槽号，提前放开会误复位新开的试听。
    assert panel._preview_speech is True

    panel.set_preview_playing(False)
    assert panel.preview_button.text() == "试听"
    assert panel.preview_button.isEnabled()
    assert panel._preview_speech is False


def test_stop_button_on_another_slot_only_stops_the_speech(qapp):
    """朗读中切到别的槽，按钮仍是「停止」：它只能停当前朗读，不能另起新试听。"""
    settings = AppSettings(chat_wheel_slots=["", "b.wav"] + [""] * 6,
                           chat_wheel_texts=["马上到"] + [""] * 7)
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    panel.preview_button.click()
    changed.reset_mock()

    panel.slot_buttons[1].click()
    panel.preview_button.click()

    # 只发停止信号，没有再提交新的试听。
    assert [call.args[0] for call in changed.call_args_list] == ["chat_wheel_preview_stop"]
    assert panel._preview_speech is True
    assert panel.preview_button.text() == "停止"


def test_recording_is_refused_while_speaking(qapp, monkeypatch):
    """录音采的是系统声音，正在朗读的 TTS 会被录进去。"""
    recorder = _FakeRecorder(result=Path("unused.wav"))
    panel, settings = _recording_panel(monkeypatch, recorder)
    settings.update(chat_wheel_texts=["马上到"] + [""] * 7)
    panel._select_slot(0)
    panel.preview_button.click()

    panel.record_button.click()

    assert recorder.started is False
    assert panel._recording is None
    assert "朗读" in panel.status_label.text()


def test_recording_cancel_button_discards_without_saving(qapp, monkeypatch):
    recorder = _FakeRecorder()
    panel, settings = _recording_panel(monkeypatch, recorder)
    assert panel.cancel_record_button.isHidden()

    panel.record_button.click()
    assert not panel.cancel_record_button.isHidden()

    panel.cancel_record_button.click()

    assert recorder.cancelled is True
    assert panel._recording is None
    assert panel.cancel_record_button.isHidden()
    assert settings.chat_wheel_slots == [""] * 8
    assert "取消" in panel.status_label.text()


def test_recording_start_failure_reports_reason(qapp, monkeypatch):
    from wordy.audio.recorder import RecordingError

    recorder = _FakeRecorder(start_error=RecordingError("未安装 PyAudioWPatch，无法录制系统声音"))
    panel, settings = _recording_panel(monkeypatch, recorder)

    panel.record_button.click()

    assert panel._recording is None
    assert "PyAudioWPatch" in panel.status_label.text()
    assert settings.chat_wheel_slots == [""] * 8


def test_too_short_recording_keeps_previous_slot(qapp, monkeypatch):
    from wordy.audio.recorder import RecordingError

    recorder = _FakeRecorder(result=Path("unused.wav"))
    panel, settings = _recording_panel(monkeypatch, recorder)
    monkeypatch.setattr(
        recorder, "stop",
        Mock(side_effect=RecordingError("录音太短，请至少录制 0.5 秒")),
    )

    panel.record_button.click()
    panel.record_button.click()

    assert panel._recording is None
    assert "太短" in panel.status_label.text()
    assert settings.chat_wheel_slots == [""] * 8


def test_unexpected_recording_failure_reports_and_resets_state(qapp, monkeypatch):
    """非 RecordingError 的意外失败不能静默穿出 Qt 槽：要给提示且让按钮恢复。"""
    recorder = _FakeRecorder(result=Path("unused.wav"))
    panel, settings = _recording_panel(monkeypatch, recorder)
    monkeypatch.setattr(recorder, "stop", Mock(side_effect=MemoryError("out of memory")))

    panel.record_button.click()
    panel.record_button.click()

    assert panel._recording is None
    assert panel.record_button.text() == "录制"
    assert panel.record_button.isEnabled()
    assert "失败" in panel.status_label.text()
    assert settings.chat_wheel_slots == [""] * 8


def test_closing_window_cancels_recording(qapp, monkeypatch, tmp_path):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    recorder = _FakeRecorder(result=tmp_path / "unused.wav")
    panel, settings = _recording_panel(monkeypatch, recorder)
    parent = QWidget()
    QVBoxLayout(parent).addWidget(panel)
    parent.show()
    qapp.processEvents()

    panel.record_button.click()
    assert panel._recording == 0
    parent.close()
    # 真实窗口中由 SettingsWindow 在关闭成功后调用 mark_closed()。
    panel.mark_closed()

    assert recorder.cancelled is True
    assert panel._recording is None
    assert settings.chat_wheel_slots == [""] * 8


def test_import_failure_keeps_recording_temp_file(qapp, monkeypatch, tmp_path):
    """导入失败不能连录音一起销毁：临时文件此时是这份音频唯一的副本。"""
    source = tmp_path / "recording.wav"
    source.write_bytes(b"pcm")
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.threading.Thread", _InlineThread)
    monkeypatch.setattr(
        "wordy.ui.chat_wheel_settings.import_audio_clip",
        Mock(side_effect=OSError("磁盘已满")),
    )
    settings = AppSettings()
    panel = ChatWheelSettings(settings, Mock())

    panel.import_file(0, source, label="录音 093000", temporary=True)

    assert source.is_file(), "导入失败时录音临时文件必须保留"
    assert settings.chat_wheel_slots[0] == ""


def test_save_failure_keeps_recording_temp_file(qapp, monkeypatch, tmp_path):
    """槽位没落盘就不算存住：托管副本被回收时录音本体仍要在。"""
    source = tmp_path / "recording.wav"
    source.write_bytes(b"pcm")
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.threading.Thread", _InlineThread)
    monkeypatch.setattr(
        "wordy.ui.chat_wheel_settings.import_audio_clip",
        lambda path, config_file=None: Path("managed.wav"),
    )
    monkeypatch.setattr(AppSettings, "save", Mock(side_effect=OSError("磁盘已满")))
    settings = AppSettings()
    panel = ChatWheelSettings(settings, Mock())

    panel.import_file(0, source, label="录音 093000", temporary=True)

    assert source.is_file(), "槽位保存失败时录音临时文件必须保留"
    assert settings.chat_wheel_slots[0] == ""


def test_import_file_temporary_source_is_removed(qapp, monkeypatch, tmp_path):
    source = tmp_path / "recording.wav"
    source.write_bytes(b"pcm")
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.threading.Thread", _InlineThread)
    monkeypatch.setattr(
        "wordy.ui.chat_wheel_settings.import_audio_clip",
        lambda path, config_file=None: Path("managed.wav"),
    )
    settings = AppSettings()
    panel = ChatWheelSettings(settings, Mock())

    panel.import_file(3, source, label="录音 093000", temporary=True)

    assert not source.exists()
    assert settings.chat_wheel_slots[3] == "managed.wav"
    assert settings.chat_wheel_labels[3] == "录音 093000"


def test_real_settings_window_wheel_fits_default_width(qapp, fake_keyring, monkeypatch):
    from PySide6.QtWidgets import QScrollArea, QTabWidget
    from wordy.ui.settings import DIALOG_WIDTH, SettingsWindow
    from wordy.ui.settings_state import SettingsState

    # 该槽带音频，切到文字会弹确认框；离屏测试里必须直接回答，否则永久阻塞。
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.QMessageBox.question",
                        lambda *args, **kwargs: QMessageBox.StandardButton.Yes)
    settings = AppSettings.load()
    settings.update(chat_wheel_slots=["sample.wav"] * 8,
                    chat_wheel_labels=[f"轮盘音频 {index + 1}" for index in range(8)])
    window = SettingsWindow(None, SettingsState(), Mock(), Mock(), Mock(), Mock())
    try:
        assert window.window is not None
        assert window.window.size().width() == DIALOG_WIDTH
        tabs = window.window.findChild(QTabWidget, "settingsTabs")
        assert tabs is not None
        tabs.setCurrentIndex(3)
        qapp.processEvents()
        panel = window.chat_wheel_panel
        assert len(panel.slot_buttons) == 8
        assert panel.isVisible()
        panel.slot_buttons[-1].click()
        panel.label_input.setText("这是一个比较长的音频标题")
        scroll = tabs.widget(3)
        assert isinstance(scroll, QScrollArea)
        assert scroll.horizontalScrollBar().maximum() == 0
        assert panel.clear_button.geometry().right() < panel.width()
        assert scroll.verticalScrollBar().maximum() == 0
        # 名称与朗读文字同行的两个输入框不能把面板撑宽。
        panel.text_mode_button.click()
        panel.text_input.setText("这是一段很长的朗读文字" * 10)
        qapp.processEvents()
        assert panel.text_input.geometry().right() < panel.width()
        assert scroll.horizontalScrollBar().maximum() == 0
        assert scroll.verticalScrollBar().maximum() == 0
    finally:
        window.close()


def test_parent_close_during_import_completes_worker_and_discards_clip(qapp, monkeypatch):
    from PySide6.QtWidgets import QVBoxLayout, QWidget

    parent = QWidget()
    panel = ChatWheelSettings(AppSettings(), Mock())
    QVBoxLayout(parent).addWidget(panel)
    parent.show()
    qapp.processEvents()
    started, release = threading.Event(), threading.Event()
    workers: list[threading.Thread] = []
    native_thread = threading.Thread

    def capture_thread(**kwargs):
        thread = native_thread(**kwargs)
        workers.append(thread)
        return thread

    def import_blocked(source, config_file):
        started.set()
        assert release.wait(timeout=2)
        return Path("managed.wav")

    cleanup = Mock()
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.import_audio_clip", import_blocked)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.remove_audio_clip", cleanup)
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.threading.Thread", capture_thread)
    panel.import_file(0, Path("音频.wav"))
    assert started.wait(timeout=2)
    parent.close()
    # 真实窗口中由 SettingsWindow 在关闭成功后调用 mark_closed()。
    panel.mark_closed()
    release.set()
    workers[0].join(timeout=2)
    assert not workers[0].is_alive()
    qapp.processEvents()
    assert panel._settings.chat_wheel_slots[0] == ""
    cleanup.assert_called_once_with("managed.wav", None)


def test_picker_uses_shared_wheel_painter(qapp, monkeypatch):
    from wordy.ui import chat_wheel_settings
    import wordy.ui.wheel_picker as wheel_picker

    calls = []
    # 盘体绘制已抽到共享 wheel_picker，设置页与截取目标选择器共用同一实现。
    monkeypatch.setattr(wheel_picker, "paint_wheel",
                        lambda painter, labels, selected, **options: calls.append((labels, selected, options)))
    picker = chat_wheel_settings._WheelPicker(lambda _: None)
    for button in picker.buttons:
        button.setText("音频\n未设置")
    picker.buttons[2].setText("音频 3\nExample")
    picker.buttons[2].setChecked(True)
    picker.grab()
    assert calls[-1][:2] == (["", "", "Example", "", "", "", "", ""], 2)
    assert calls[-1][2]["editing"] is True
    picker.wheel_style = "petals"
    picker.grab()
    assert calls[-1][2]["style"] == "petals"
    picker._hovered_index = 0
    picker.grab()
    assert calls[-1][1] == 0
    assert picker.width() == picker.height() == 400
    assert picker._slot_at(picker.buttons[0].geometry().center()) == 0
    assert picker._slot_at(QPoint(200, 140)) == 0
    assert picker._slot_at(QPoint(200, 150)) is None
    picker.close()
