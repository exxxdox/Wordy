#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for native_hotkey.py parse_hotkey function."""

import ctypes
import importlib
import sys
import pytest

# Ensure a clean import (previous test runs may have cached the module).
sys.modules.pop("native_hotkey", None)

from native_hotkey import parse_hotkey, MOD_NOREPEAT, MOD_CONTROL, MOD_SHIFT, MOD_ALT


def test_parse_hotkey_single_function_key():
    """Test parsing single function key like f6."""
    modifiers, key_code = parse_hotkey("f6")
    # F6 is 0x70 + 5 = 0x75 = 117
    assert key_code == 117
    assert modifiers == MOD_NOREPEAT  # MOD_NOREPEAT is always added


def test_parse_hotkey_compound_modifiers():
    """Test parsing ctrl+shift+f6 with multiple modifiers."""
    modifiers, key_code = parse_hotkey("ctrl+shift+f6")
    assert key_code == 117
    assert modifiers == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT)
    assert (modifiers & MOD_CONTROL) != 0
    assert (modifiers & MOD_SHIFT) != 0
    assert (modifiers & MOD_NOREPEAT) != 0


def test_parse_hotkey_missing_main_key_raises():
    """Test that missing main key raises ValueError."""
    with pytest.raises(ValueError, match="缺少主按键"):
        parse_hotkey("ctrl+alt")
    with pytest.raises(ValueError, match="缺少主按键"):
        parse_hotkey("")
    with pytest.raises(ValueError, match="缺少主按键"):
        parse_hotkey("+++")


def test_parse_hotkey_multiple_main_keys_raises():
    """Test that multiple non-modifier keys raises ValueError."""
    with pytest.raises(ValueError, match="只能包含一个非修饰键"):
        parse_hotkey("a+b")
    with pytest.raises(ValueError, match="只能包含一个非修饰键"):
        parse_hotkey("ctrl+f1+f2")


def test_parse_hotkey_unknown_key_raises():
    """Test that unknown key raises ValueError."""
    with pytest.raises(ValueError, match="不支持的快捷键按键"):
        parse_hotkey("ctrl+invalidkey")
    with pytest.raises(ValueError, match="不支持的快捷键按键"):
        parse_hotkey("not a key")


def test_parse_hotkey_various_formats():
    """Test parsing various hotkey formats common from keyboard library."""
    # Single alpha
    modifiers, key_code = parse_hotkey("a")
    assert key_code == ord("A")
    assert modifiers == MOD_NOREPEAT

    # Single digit
    modifiers, key_code = parse_hotkey("1")
    assert key_code == ord("1")

    # With spaces in name
    modifiers, key_code = parse_hotkey("ctrl+alt+page down")
    assert modifiers == (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT)
    # page down is 0x22 = 34
    assert key_code == 34


def test_native_hotkey_module_importable_without_user32(monkeypatch):
    """Module should import cleanly even when ctypes.WinDLL is missing/raising.

    Simulates a non-Windows environment by removing ctypes.WinDLL and verifying
    that a fresh import of native_hotkey still succeeds, public constants are
    preserved, and parse_hotkey behaves identically.
    """
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)
    sys.modules.pop("native_hotkey", None)

    module = importlib.import_module("native_hotkey")

    assert module.MOD_NOREPEAT == MOD_NOREPEAT
    assert module.MOD_CONTROL == MOD_CONTROL
    assert module.MOD_SHIFT == MOD_SHIFT
    assert module.MOD_ALT == MOD_ALT

    modifiers, key_code = module.parse_hotkey("ctrl+shift+f6")
    assert key_code == 117
    assert modifiers == (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT)


def test_listener_start_rejects_non_windows(monkeypatch):
    """start() must raise a clear RuntimeError on non-Windows platforms."""
    sys.modules.pop("native_hotkey", None)
    monkeypatch.setattr(sys, "platform", "linux")

    module = importlib.import_module("native_hotkey")
    listener = module.NativeHotkeyListener("ctrl+f6", "test", lambda: None)

    with pytest.raises(RuntimeError, match="仅支持 Windows 平台"):
        listener.start()

    # No background thread should have been spawned.
    assert listener._thread is None


def test_stop_logs_warning_on_join_timeout():
    """Test that stop logs a warning when thread is still alive after join timeout."""
    import logging
    import threading
    from unittest.mock import patch
    from native_hotkey import NativeHotkeyListener

    # Create a listener but don't actually start Windows message loop
    def dummy_callback():
        pass

    listener = NativeHotkeyListener("ctrl+c", "test", dummy_callback)

    # Create a fake daemon thread that runs forever and doesn't exit
    running_event = threading.Event()
    running_event.set()  # Keep running indefinitely
    def fake_run():
        while running_event.is_set():
            threading.Event().wait(1)

    listener._thread = threading.Thread(target=fake_run, daemon=True)
    listener._thread.start()
    # Fake thread id; on non-Windows the stop() path skips PostThreadMessageW,
    # on Windows _get_user32() handles the call.
    listener._thread_id = 12345

    fake_thread = listener._thread

    # Check that we get a warning when stopping
    with patch.object(logging.getLogger('native_hotkey'), 'warning') as mock_warn:
        listener.stop()
        assert mock_warn.called
        warning_messages = [call.args[0] for call in mock_warn.call_args_list]
        assert any("did not exit within 2 seconds" in msg for msg in warning_messages)

    # Clean up
    running_event.clear()
    fake_thread.join(timeout=1)
