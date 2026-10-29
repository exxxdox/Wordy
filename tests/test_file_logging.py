"""exe 日志同步写入、字节限额与轮转保留。"""

import logging
from unittest.mock import Mock

import pytest

import wordy.log as log_module


@pytest.fixture
def file_logger():
    logger = logging.Logger("tests.persistent_logging")
    logger.propagate = False
    logger.setLevel(logging.INFO)
    yield logger
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_source_run_does_not_create_log_file(monkeypatch, tmp_path, file_logger):
    monkeypatch.delattr(log_module.sys, "frozen", raising=False)
    monkeypatch.setattr(log_module.sys, "executable", str(tmp_path / "python.exe"))
    log_module.install_file_logging(file_logger)
    file_logger.info("source run")
    assert not list(tmp_path.iterdir())
    assert not file_logger.handlers


def test_frozen_log_is_next_to_exe_immediately_readable_and_installed_once(monkeypatch, tmp_path, file_logger):
    monkeypatch.setattr(log_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(log_module.sys, "executable", str(tmp_path / "Wordy.exe"))
    log_module.install_file_logging(file_logger)
    log_module.install_file_logging(file_logger)
    assert len(file_logger.handlers) == 1
    handler = file_logger.handlers[0]
    assert handler.maxBytes == 5 * 1024 * 1024
    assert handler.backupCount == 4
    file_logger.info("中文同步日志")
    assert "中文同步日志" in (tmp_path / "wordy.log").read_text(encoding="utf-8")


def test_rotation_caps_utf8_bytes_and_keeps_only_five_files(monkeypatch, tmp_path, file_logger):
    monkeypatch.setattr(log_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(log_module.sys, "executable", str(tmp_path / "Wordy.exe"))
    log_module.install_file_logging(file_logger)
    handler = file_logger.handlers[0]
    handler.maxBytes = 96
    handler.setFormatter(logging.Formatter("%(message)s"))
    for index in range(12):
        file_logger.info("%02d %s", index, "中文" * 12)
    files = list(tmp_path.glob("wordy.log*"))
    assert {file.name for file in files} == {"wordy.log", *(f"wordy.log.{i}" for i in range(1, 5))}
    assert all(file.stat().st_size <= 96 for file in files)
    assert (tmp_path / "wordy.log").read_text(encoding="utf-8").startswith("11 ")
    assert (tmp_path / "wordy.log.4").read_text(encoding="utf-8").startswith("07 ")


def test_restart_appends_existing_log(monkeypatch, tmp_path, file_logger):
    monkeypatch.setattr(log_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(log_module.sys, "executable", str(tmp_path / "Wordy.exe"))
    log_module.install_file_logging(file_logger)
    file_logger.info("before restart")
    handler = file_logger.handlers[0]
    file_logger.removeHandler(handler)
    handler.close()
    log_module.install_file_logging(file_logger)
    file_logger.info("after restart")
    content = (tmp_path / "wordy.log").read_text(encoding="utf-8")
    assert "before restart" in content and "after restart" in content


def test_oversized_record_is_split_without_losing_utf8_content(monkeypatch, tmp_path, file_logger):
    monkeypatch.setattr(log_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(log_module.sys, "executable", str(tmp_path / "Wordy.exe"))
    log_module.install_file_logging(file_logger)
    handler = file_logger.handlers[0]
    handler.maxBytes = 64
    handler.setFormatter(logging.Formatter("%(message)s"))
    message = "录音\n" * 25
    file_logger.info(message)
    files = sorted(tmp_path.glob("wordy.log*"), key=lambda path: int(path.suffix[1:]) if path.suffix[1:].isdigit() else 0, reverse=True)
    assert 1 < len(files) <= 5
    assert all(file.stat().st_size <= 64 for file in files)
    assert "".join(file.read_text(encoding="utf-8") for file in files) == message + "\n"


def test_unwritable_exe_directory_reports_failure_without_fallback(monkeypatch, tmp_path, file_logger):
    monkeypatch.setattr(log_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(log_module.sys, "executable", str(tmp_path / "Wordy.exe"))
    monkeypatch.setattr(log_module, "_RotatingFileHandler", Mock(side_effect=PermissionError("read only")))
    warning = Mock()
    monkeypatch.setattr(file_logger, "warning", warning)
    log_module.install_file_logging(file_logger)
    warning.assert_called_once()
    assert not file_logger.handlers
    assert not list(tmp_path.iterdir())
