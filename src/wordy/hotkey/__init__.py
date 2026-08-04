#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""快捷键解析和 Windows 全局热键监听。"""

from wordy.hotkey.parser import iter_hotkey_parts, normalize_key_part, split_hotkey
from wordy.hotkey.native import NativeHotkeyListener

__all__ = [
    "iter_hotkey_parts",
    "NativeHotkeyListener",
    "normalize_key_part",
    "split_hotkey",
]
