#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TOML 读写辅助（零依赖手写序列化器，不引入 tomli-w）。

从 ``config.py`` 抽出，保持该文件聚焦配置对象本身。
"""

from __future__ import annotations

import json
import os
import tempfile
import tomllib
from pathlib import Path


def read_toml(path: Path) -> dict[str, object] | None:
    """读取并校验 TOML 文件，失败返回 None。"""
    if not path.exists():
        return None
    try:
        with path.open("rb") as f:
            data: object = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return {str(k): v for k, v in data.items()}


def write_toml_atomic(path: Path, data: dict[str, object]) -> None:
    """原子写入 TOML：temp → flush → fsync → replace。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".atomic_wordy_", suffix=".toml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(to_toml(data))
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def to_toml(data: dict[str, object]) -> str:
    """将扁平 + 一级嵌套 dict 序列化为 TOML 字符串。"""
    lines: list[str] = []
    nested: dict[str, dict[str, object]] = {}
    for key, value in data.items():
        if isinstance(value, dict) and not _is_inline_table(value):
            nested[key] = value
        elif value is not None:
            lines.append(f"{key} = {_toml_value(value)}")
    for table_name, table_data in nested.items():
        lines.append("")
        lines.append(f"[{table_name}]")
        for k, v in table_data.items():
            if v is not None:
                lines.append(f"{k} = {_toml_value(v)}")
    return "\n".join(lines) + "\n"


def _is_inline_table(value: dict[str, object]) -> bool:
    """判断 dict 是否应为 TOML 内联表（小、扁平、无嵌套 dict 值）。"""
    if len(value) > 3:
        return False
    for v in value.values():
        if isinstance(v, dict):
            return False
    return True


def _toml_value(value: object) -> str:
    """将单个 Python 值转为 TOML 字面量。"""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return str(value)
    if isinstance(value, str):
        # JSON 的基本字符串转义适用于 TOML；额外转义 TOML 禁止的 DEL，保留非 BMP Unicode。
        return json.dumps(value, ensure_ascii=False).replace("\x7f", "\\u007f")
    if isinstance(value, dict):
        items = ", ".join(
            f"{k} = {_toml_value(v)}" for k, v in value.items() if v is not None
        )
        return "{" + items + "}"
    if value is None:
        return '""'
    return _toml_value(str(value))
