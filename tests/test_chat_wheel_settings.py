"""聊天轮盘设置：导入结果、失败回滚与快捷键确认。"""

from pathlib import Path
import threading
from unittest.mock import Mock
import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from wordy.config import AppSettings
from wordy.ui.chat_wheel_settings import ChatWheelSettings


@pytest.fixture(autouse=True)
def isolated_audio_config(monkeypatch, tmp_path):
    monkeypatch.setattr("wordy.config.USER_CONFIG_FILE", tmp_path / "wordy.toml")
    monkeypatch.setattr("wordy.ui.chat_wheel_settings.remove_audio_clip", Mock())


def test_hotkey_is_committed_only_after_registration(qapp):
    settings = AppSettings()
    changed = Mock()
    panel = ChatWheelSettings(settings, changed)
    panel.hotkey_input.setKeySequence("Ctrl+F8")
    panel.hotkey_apply.click()
    changed.assert_called_once_with("chat_wheel_hotkey", "ctrl+f8")
    assert settings.chat_wheel_hotkey == "f8"
    panel.set_hotkey_result("快捷键已被占用")
    assert "占用" in panel.status_label.text()
    assert panel.hotkey_apply.isEnabled()


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

        assert queue.texts() == ["正在导入上音频…"]
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
    assert panel.editor_title.text() == "右上音频"
    assert panel.label_input.text() == "右上方"
    assert panel.slot_buttons[1].property("selected") is True
    assert panel.slot_buttons[1].isChecked()
    assert panel.slot_buttons[0].property("selected") is False
    panel.preview_button.click()
    changed.assert_called_once_with("chat_wheel_preview", 1)


def test_wheel_sector_hit_test_matches_runtime_directions(qapp):
    panel = ChatWheelSettings(AppSettings(), Mock())

    assert panel.wheel_picker._slot_at(QPoint(170, 20)) == 0
    assert panel.wheel_picker._slot_at(QPoint(270, 120)) == 2
    assert panel.wheel_picker._slot_at(QPoint(170, 220)) == 4
    assert panel.wheel_picker._slot_at(QPoint(70, 120)) == 6
    assert panel.wheel_picker._slot_at(QPoint(170, 120)) is None


def test_selected_sector_survives_mouse_leaving(qapp):
    panel = ChatWheelSettings(AppSettings(), Mock())
    panel.show()
    qapp.processEvents()

    QTest.mouseClick(panel.wheel_picker, Qt.MouseButton.LeftButton, pos=QPoint(270, 120))
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
    panel.hotkey_apply.click()
    changed.assert_called_once_with("chat_wheel_hotkey", "ctrl+page up")


def test_real_settings_window_wheel_fits_default_width(qapp, fake_keyring):
    from PySide6.QtWidgets import QScrollArea, QTabWidget
    from wordy.ui.settings import SettingsWindow
    from wordy.ui.settings_state import SettingsState

    settings = AppSettings.load()
    settings.update(chat_wheel_slots=["sample.wav"] * 8,
                    chat_wheel_labels=[f"轮盘音频 {index + 1}" for index in range(8)])
    window = SettingsWindow(None, SettingsState(), Mock(), Mock(), Mock(), Mock())
    try:
        assert window.window is not None
        assert window.window.size().width() == 480
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
    release.set()
    workers[0].join(timeout=2)
    assert not workers[0].is_alive()
    qapp.processEvents()
    assert panel._settings.chat_wheel_slots[0] == ""
    cleanup.assert_called_once_with("managed.wav", None)
