#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""持久化目录解析。

不放在 ``config`` 里是有意的：``config`` 有一条契约禁止访问环境变量（防止
密钥重新从环境读回来），而路径根目录的解析本质上是环境相关的。分开之后
守卫可以保持原样，路径策略也只有一个出处。
"""

from __future__ import annotations

import os
from pathlib import Path

APP_DIR_NAME = "wordy"


def config_directory() -> Path:
    """所有持久化文件的根目录：``$XDG_CONFIG_HOME/wordy``。

    未设置、空串、全空白或给的是相对路径时退回 ``~/.config/wordy``：XDG
    规范规定相对路径无效，照用会把文件写到随当前工作目录漂移的位置。
    """
    root = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(root) if root and Path(root).is_absolute() else Path.home() / ".config"
    return base / APP_DIR_NAME


__all__ = ["APP_DIR_NAME", "config_directory"]
