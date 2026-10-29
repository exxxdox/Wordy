"""单实例命名互斥体：拒绝重复启动，持有首实例句柄直到进程退出。"""

import ctypes
import subprocess
import sys
import time
import uuid
from unittest.mock import Mock

import pytest

import wordy.single_instance as instance_module


@pytest.fixture
def kernel32(monkeypatch):
    api = Mock()
    api.CreateMutexW.return_value = 123
    monkeypatch.setattr(instance_module, "_instance_handle", None)
    monkeypatch.setattr(ctypes, "WinDLL", Mock(return_value=api), raising=False)
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 0, raising=False)
    monkeypatch.setattr(ctypes, "set_last_error", Mock(), raising=False)
    return api


def test_first_instance_keeps_handle_and_repeated_check_is_idempotent(kernel32):
    assert instance_module.acquire_single_instance()
    assert instance_module.acquire_single_instance()
    kernel32.CreateMutexW.assert_called_once_with(None, False, r"Local\Wordy.SingleInstance")
    assert instance_module._instance_handle == 123
    kernel32.CloseHandle.assert_not_called()


def test_duplicate_instance_closes_only_its_new_handle(kernel32, monkeypatch):
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 183)
    assert not instance_module.acquire_single_instance()
    assert instance_module._instance_handle is None
    kernel32.CloseHandle.assert_called_once_with(123)


def test_mutex_failure_does_not_allow_startup(kernel32, monkeypatch):
    kernel32.CreateMutexW.return_value = None
    monkeypatch.setattr(ctypes, "get_last_error", lambda: 5)
    with pytest.raises(OSError):
        instance_module.acquire_single_instance()
    assert instance_module._instance_handle is None


def test_duplicate_notice_uses_native_information_dialog(kernel32):
    instance_module.show_duplicate_instance_message()
    kernel32.MessageBoxW.assert_called_once_with(
        None, "Wordy 已在运行，请勿重复启动。", "Wordy", 0x00010040,
    )
    kernel32.CreateMutexW.assert_not_called()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex")
def test_real_process_rejects_duplicate_and_releases_after_forced_exit(tmp_path):
    # 使用独立名字，不占用用户正在运行的 Wordy 的真实互斥体。
    name = rf"Local\Wordy.test.{uuid.uuid4().hex}"
    ready = tmp_path / "ready"
    code = """
import sys
from pathlib import Path
import wordy.single_instance as instance
instance._MUTEX_NAME = sys.argv[1]
acquired = instance.acquire_single_instance()
print(acquired, flush=True)
if len(sys.argv) > 2:
    Path(sys.argv[2]).write_text(str(acquired))
    sys.stdin.read(1)
"""
    first = subprocess.Popen([sys.executable, "-u", "-c", code, name, str(ready)],
                             stdin=subprocess.PIPE, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(.01)
        assert ready.read_text() == "True"
        second = subprocess.run([sys.executable, "-c", code, name],
                                capture_output=True, text=True, timeout=5, check=True)
        assert second.stdout.strip() == "False"
    finally:
        if first.poll() is None:
            first.kill()
        first.communicate(timeout=5)
    third = subprocess.run([sys.executable, "-c", code, name],
                           capture_output=True, text=True, timeout=5, check=True)
    assert third.stdout.strip() == "True"
