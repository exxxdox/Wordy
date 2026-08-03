#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Windows 原生 RegisterHotKey 全局快捷键监听。"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
from ctypes import wintypes
from typing import Any, Callable

from easy_tts.hotkey.parser import iter_hotkey_parts


logger = logging.getLogger(__name__)

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
ERROR_HOTKEY_ALREADY_REGISTERED = 1409
HOTKEY_ID = 1


def _is_windows() -> bool:
    return sys.platform.startswith("win")


_user32: Any = None
_kernel32: Any = None


def _load_winlib(name: str) -> Any:
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        raise RuntimeError(
            f"无法加载 Windows 动态库 {name}: 当前平台不支持 ctypes.WinDLL"
        )
    return win_dll(name, use_last_error=True)


def _get_user32() -> Any:
    global _user32
    if _user32 is None:
        _user32 = _load_winlib("user32")
    return _user32


def _get_kernel32() -> Any:
    global _kernel32
    if _kernel32 is None:
        _kernel32 = _load_winlib("kernel32")
    return _kernel32


VK_CODES = {
    "backspace": 0x08,
    "tab": 0x09,
    "clear": 0x0C,
    "enter": 0x0D,
    "return": 0x0D,
    "pause": 0x13,
    "caps lock": 0x14,
    "capslock": 0x14,
    "esc": 0x1B,
    "escape": 0x1B,
    "space": 0x20,
    "spacebar": 0x20,
    "page up": 0x21,
    "page down": 0x22,
    "end": 0x23,
    "home": 0x24,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    "print screen": 0x2C,
    "insert": 0x2D,
    "ins": 0x2D,
    "delete": 0x2E,
    "del": 0x2E,
    "num lock": 0x90,
    "numlock": 0x90,
    "scroll lock": 0x91,
    "scrolllock": 0x91,
    "menu": 0x5D,
    "apps": 0x5D,
    ";": 0xBA,
    ":": 0xBA,
    "=": 0xBB,
    "+": 0xBB,
    ",": 0xBC,
    "<": 0xBC,
    "-": 0xBD,
    "_": 0xBD,
    ".": 0xBE,
    ">": 0xBE,
    "/": 0xBF,
    "?": 0xBF,
    "`": 0xC0,
    "~": 0xC0,
    "[": 0xDB,
    "{": 0xDB,
    "\\": 0xDC,
    "|": 0xDC,
    "]": 0xDD,
    "}": 0xDD,
    "'": 0xDE,
    '"': 0xDE,
}

MODIFIER_KEYS = {
    "alt": MOD_ALT,
    "left alt": MOD_ALT,
    "right alt": MOD_ALT,
    "ctrl": MOD_CONTROL,
    "control": MOD_CONTROL,
    "left ctrl": MOD_CONTROL,
    "right ctrl": MOD_CONTROL,
    "left control": MOD_CONTROL,
    "right control": MOD_CONTROL,
    "shift": MOD_SHIFT,
    "left shift": MOD_SHIFT,
    "right shift": MOD_SHIFT,
    "win": MOD_WIN,
    "windows": MOD_WIN,
    "left windows": MOD_WIN,
    "right windows": MOD_WIN,
    "left win": MOD_WIN,
    "right win": MOD_WIN,
}


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


def _virtual_key_from_name(key: str) -> int | None:
    if key in VK_CODES:
        return VK_CODES[key]
    if len(key) == 1 and key.isalpha():
        return ord(key.upper())
    if len(key) == 1 and key.isdigit():
        return ord(key)
    if key.startswith("f") and key[1:].isdigit():
        function_index = int(key[1:])
        if 1 <= function_index <= 24:
            return 0x70 + function_index - 1
    if key.startswith("num ") and key[4:].isdigit():
        num_index = int(key[4:])
        if 0 <= num_index <= 9:
            return 0x60 + num_index
    return None


def parse_hotkey(hotkey: str) -> tuple[int, int]:
    """将 keyboard 包格式的快捷键解析为 RegisterHotKey 参数。"""
    modifiers = 0
    key_code = None
    for part, key in iter_hotkey_parts(hotkey):
        if key in MODIFIER_KEYS:
            modifiers |= MODIFIER_KEYS[key]
            continue
        if key_code is not None:
            raise ValueError(f"快捷键只能包含一个非修饰键: {hotkey}")
        key_code = _virtual_key_from_name(key)
        if key_code is None:
            raise ValueError(f"不支持的快捷键按键: {part}")

    if key_code is None:
        raise ValueError(f"快捷键缺少主按键: {hotkey}")
    return modifiers | MOD_NOREPEAT, key_code


class NativeHotkeyListener:
    """在独立消息线程中使用 RegisterHotKey 监听全局快捷键。"""

    def __init__(self, hotkey: str, name: str, on_trigger: Callable[[], None]):
        self.hotkey = hotkey
        self.name = name
        self.on_trigger = on_trigger
        self._thread: threading.Thread | None = None
        self._thread_id = 0
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._start_error: Exception | None = None

    def start(self) -> None:
        """启动热键消息线程并等待注册完成。"""
        if not _is_windows():
            raise RuntimeError(
                "NativeHotkeyListener 仅支持 Windows 平台"
            )
        if self._thread is not None and self._thread.is_alive():
            return

        self._ready.clear()
        self._stopped.clear()
        self._start_error = None
        self._thread = threading.Thread(target=self._run, name="NativeHotkeyListener", daemon=True)
        self._thread.start()
        self._ready.wait(timeout=3)
        if self._start_error is not None:
            self.stop()
            raise self._start_error
        if not self._ready.is_set():
            self.stop()
            raise RuntimeError(f"注册全局快捷键 {self.name} 超时")

    def stop(self) -> None:
        """停止热键消息线程。"""
        if self._thread_id and _is_windows():
            try:
                _get_user32().PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except OSError:
                logger.warning("PostThreadMessageW 失败,无法通知热键线程退出: %s", self.name)
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2)
            alive = self._thread.is_alive()
            if alive:
                logger.warning(
                    "Hotkey listener thread for '%s' did not exit within 2 seconds "
                    "after posting WM_QUIT. The thread is still running and will "
                    "be left as a daemon thread.", self.name
                )
        self._thread = None
        self._thread_id = 0
        self._stopped.set()

    def _raise_last_error(self, prefix: str) -> None:
        error_code = ctypes.get_last_error()
        if error_code == ERROR_HOTKEY_ALREADY_REGISTERED:
            raise RuntimeError(f"{prefix}: 快捷键已被其他程序占用")
        raise OSError(error_code, f"{prefix}: Windows 错误 {error_code}")

    def _run(self) -> None:
        try:
            modifiers, key_code = parse_hotkey(self.hotkey)
        except Exception as e:
            self._start_error = e
            self._ready.set()
            return

        user32 = _get_user32()
        kernel32 = _get_kernel32()

        self._thread_id = kernel32.GetCurrentThreadId()
        if not user32.RegisterHotKey(None, HOTKEY_ID, modifiers, key_code):
            self._start_error = RuntimeError(f"注册全局快捷键 {self.name} 失败")
            try:
                self._raise_last_error(str(self._start_error))
            except Exception as e:
                self._start_error = e
            self._ready.set()
            return

        logger.info("已注册 Windows 原生全局快捷键: %s", self.name)
        self._ready.set()

        msg = MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam == HOTKEY_ID:
                    try:
                        self.on_trigger()
                    except Exception:
                        logger.exception("处理 Windows 原生全局快捷键回调失败: %s", self.name)
        finally:
            if not user32.UnregisterHotKey(None, HOTKEY_ID):
                logger.warning("注销 Windows 原生全局快捷键 %s 失败: %s", self.name, ctypes.get_last_error())
            logger.info("已停止 Windows 原生全局快捷键: %s", self.name)
