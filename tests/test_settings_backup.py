"""备份入口的取消、失败和退出行为。"""

from pathlib import Path
from unittest.mock import Mock

import pytest

from wordy.ui import settings as ui
from wordy.ui.settings_state import SettingsState
from PySide6.QtWidgets import QPushButton, QWidget


@pytest.fixture
def window(qapp):
    view = ui.SettingsWindow.__new__(ui.SettingsWindow)
    view.window = None
    view._settings = Mock()
    view.set_status = Mock()
    view._on_backup_imported = Mock()
    view.on_field_changed = Mock(return_value=True)
    view.chat_wheel_panel = Mock(_busy=set(), _recording=None, _trimming=False)
    return view


@pytest.fixture(autouse=True)
def password_dialog(monkeypatch):
    dialog = Mock(return_value=("测试加密密码", True))
    monkeypatch.setattr(ui.QInputDialog, "getText", dialog)
    return dialog


def test_export_uses_default_directory_and_selected_path(window, monkeypatch, tmp_path):
    choose = Mock(return_value=(str(tmp_path / "chosen.zip"), ""))
    export = Mock()
    monkeypatch.setattr(ui, "default_backup_directory", lambda: tmp_path)
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", choose)
    monkeypatch.setattr(ui.QMessageBox, "warning", Mock(return_value=ui.QMessageBox.StandardButton.Ok))
    monkeypatch.setattr(ui, "export_backup", export)
    window._export_backup()
    assert Path(choose.call_args.args[2]).parent == tmp_path
    export.assert_called_once_with(window._settings, tmp_path / "chosen.zip", "测试加密密码")


@pytest.mark.parametrize("accept", [True, False])
def test_import_confirmation_controls_restore_and_exit(window, monkeypatch, tmp_path, accept):
    monkeypatch.setattr(ui.QFileDialog, "getOpenFileName", Mock(return_value=(str(tmp_path / "a.zip"), "")))
    monkeypatch.setattr(ui, "default_backup_directory", lambda: tmp_path)
    monkeypatch.setattr(ui.QMessageBox, "question", Mock(return_value=(
        ui.QMessageBox.StandardButton.Yes if accept else ui.QMessageBox.StandardButton.No)))
    restore = Mock()
    monkeypatch.setattr(ui, "import_backup", restore)
    window._import_backup()
    assert restore.called == accept
    assert window._on_backup_imported.called == accept
    if accept:
        restore.assert_called_once_with(window._settings, tmp_path / "a.zip", "测试加密密码")


def test_failed_import_stays_open(window, monkeypatch, tmp_path):
    monkeypatch.setattr(ui.QFileDialog, "getOpenFileName", Mock(return_value=(str(tmp_path / "a.zip"), "")))
    monkeypatch.setattr(ui, "default_backup_directory", lambda: tmp_path)
    monkeypatch.setattr(ui.QMessageBox, "question", Mock(return_value=ui.QMessageBox.StandardButton.Yes))
    monkeypatch.setattr(ui, "import_backup", Mock(side_effect=ValueError("备份损坏")))
    window._import_backup()
    window._on_backup_imported.assert_not_called()
    assert "备份损坏" in window.set_status.call_args.args[0]


def test_busy_import_does_not_restore(window, monkeypatch, tmp_path):
    monkeypatch.setattr(ui.QFileDialog, "getOpenFileName", Mock(return_value=(str(tmp_path / "a.zip"), "")))
    monkeypatch.setattr(ui, "default_backup_directory", lambda: tmp_path)
    monkeypatch.setattr(ui.QMessageBox, "question", Mock(return_value=ui.QMessageBox.StandardButton.Yes))
    restore = Mock()
    monkeypatch.setattr(ui, "import_backup", restore)
    window.on_field_changed.return_value = False
    window._import_backup()
    restore.assert_not_called()
    window._on_backup_imported.assert_not_called()


def test_footer_exposes_both_backup_actions(qapp):
    root = QWidget()
    view = ui.SettingsWindow(root, SettingsState(), lambda: None, lambda: None,
                             lambda *_: None, lambda: None)
    try:
        assert view.window is not None
        texts = {button.text() for button in view.window.findChildren(QPushButton)}
        assert {"导出备份", "导入备份"} <= texts
    finally:
        view.close()


@pytest.mark.parametrize("answers", [
    [("", False)], [("   ", True)], [("first password", True), ("different password", True)],
    [("first password", True), ("", False)],
])
def test_export_requires_confirmed_nonempty_password(window, monkeypatch, tmp_path, password_dialog, answers):
    password_dialog.side_effect = answers
    monkeypatch.setattr(ui, "default_backup_directory", lambda: tmp_path)
    monkeypatch.setattr(ui.QFileDialog, "getSaveFileName", Mock(return_value=(str(tmp_path / "a.wordybackup"), "")))
    export = Mock()
    monkeypatch.setattr(ui, "export_backup", export)
    window._export_backup()
    export.assert_not_called()
    assert password_dialog.call_args.args[3] == ui.QLineEdit.EchoMode.Password


@pytest.mark.parametrize("answer", [("", False), ("", True)])
def test_import_requires_password(window, monkeypatch, tmp_path, password_dialog, answer):
    password_dialog.return_value = answer
    monkeypatch.setattr(ui, "default_backup_directory", lambda: tmp_path)
    monkeypatch.setattr(ui.QFileDialog, "getOpenFileName", Mock(return_value=(str(tmp_path / "a.wordybackup"), "")))
    monkeypatch.setattr(ui.QMessageBox, "question", Mock(return_value=ui.QMessageBox.StandardButton.Yes))
    restore = Mock()
    monkeypatch.setattr(ui, "import_backup", restore)
    window._import_backup()
    restore.assert_not_called()
    window._on_backup_imported.assert_not_called()
