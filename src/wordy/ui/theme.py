#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""共享 UI 颜色/主题常量。

统一输入栏和设置页的深海蓝、冰蓝强调色，避免窗口间视觉割裂。
"""

WINDOW_BG = "#071321"
SURFACE_BG = "#0e2033"
ELEVATED_BG = "#152d45"

# 强调色（输入框文本、光标、按钮悬停、状态成功提示）
# 保留既有 token 名称，让所有现有状态提示同步采用新强调色。
GREEN_ACCENT = "#55d9f2"
ACCENT_HOVER = "#9beeff"
ACCENT_PRESSED = "#239bb8"
ACCENT_SECONDARY = "#8d9fff"

# 文本颜色
TEXT_PRIMARY = "#eaf5ff"
TEXT_MUTED = "#9bb3cb"
TEXT_WARNING = "#f5c451"
TEXT_ERROR = "#ff6b6b"

# 分隔线 / 次级按钮背景
SEPARATOR_COLOR = "#24405b"

# 次级按钮
BUTTON_BG = "#152d45"
BUTTON_ACTIVE_BG = "#203e5d"
BUTTON_GHOST_BORDER = "#34536f"

SCROLLBAR_HANDLE = "#34536f"
SCROLLBAR_HANDLE_HOVER = "#527594"

# 主窗口（输入框）背景与边框
INPUT_BACKGROUND = "#0b1b2d"
INPUT_BORDER = "#34536f"

# 设置图标默认色（未悬停）
CONFIG_BUTTON_IDLE = "#9bb3cb"

# 字体栈 —— 终端/极客风格用等宽字体，UI 标签用系统无衬线
MONO_FONT = '"Cascadia Code", "JetBrains Mono", "Consolas", "Courier New", monospace'
UI_FONT = '"Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI", sans-serif'
