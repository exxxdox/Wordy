#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""快捷键解析、Windows 全局热键监听和显示名格式化。"""

from wordy.hotkey.parser import iter_hotkey_parts, normalize_key_part, split_hotkey
from wordy.hotkey.native import NativeHotkeyListener


def display_hotkey(hotkey: str) -> str:
    """将 keyboard 包快捷键字符串格式化为用户可读名称。"""
    display_parts: list[str] = []
    for _, key in iter_hotkey_parts(hotkey):
        if key in {"ctrl", "control"}:
            display_parts.append("Ctrl")
        elif key == "alt":
            display_parts.append("Alt")
        elif key == "shift":
            display_parts.append("Shift")
        elif key in {"windows", "win", "left windows", "right windows"}:
            display_parts.append("Win")
        elif key.startswith("f") and key[1:].isdigit():
            display_parts.append(key.upper())
        elif len(key) == 1:
            display_parts.append(key.upper())
        else:
            display_parts.append(" ".join(word.capitalize() for word in key.split()))
    return "+".join(display_parts) if display_parts else hotkey


__all__ = [
    "display_hotkey",
    "iter_hotkey_parts",
    "NativeHotkeyListener",
    "normalize_key_part",
    "split_hotkey",
]
