#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""快捷键字符串的共享分词与归一化工具。

`app_config.display_hotkey` 与 `native_hotkey.parse_hotkey` 都需要把
keyboard 库风格的快捷键字符串（如 ``"ctrl+shift+page_down"``）拆分为
小写、去下划线的按键名，本模块集中实现该逻辑以避免重复。
"""

from __future__ import annotations

from collections.abc import Iterator


def normalize_key_part(part: str) -> str:
    """归一化单个按键片段：strip + 转小写 + 下划线转空格。

    例如 ``"  Left_Windows "`` -> ``"left windows"``。空字符串原样返回。
    """
    return part.strip().lower().replace("_", " ")


def split_hotkey(hotkey: str) -> list[str]:
    """按 ``+`` 拆分 keyboard 库风格快捷键的原始片段。

    先把空格统一替换为下划线，使 ``"left windows+g"`` 与
    ``"left_windows+g"`` 等价，再按 ``+`` 切分。不做大小写或空白处理，
    用于需要保留原始 token 文本的场景（如错误消息）。
    """
    return hotkey.replace(" ", "_").split("+")


def iter_hotkey_parts(hotkey: str) -> Iterator[tuple[str, str]]:
    """逐个产出快捷键片段的 ``(raw_part, normalized_key)``。

    自动跳过归一化后为空的片段，调用方只需关心非空按键名；
    `raw_part` 便于在抛错时回显用户输入。
    """
    for part in split_hotkey(hotkey):
        key = normalize_key_part(part)
        if key:
            yield part, key
