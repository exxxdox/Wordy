#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""快捷键解析和 Windows 全局热键监听。"""

from easy_tts.hotkey.parser import iter_hotkey_parts, normalize_key_part, split_hotkey
from easy_tts.hotkey.native import NativeHotkeyListener

__all__ = [
    "iter_hotkey_parts",
    "NativeHotkeyListener",
    "normalize_key_part",
    "split_hotkey",
]
