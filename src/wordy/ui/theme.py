#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""共享 UI 颜色/主题常量。

仅集中存放在多个窗口之间共享或语义相同的颜色值，
不改变任何视觉表现，便于统一维护。
"""

WINDOW_BG = "#0b1220"
SURFACE_BG = "#121b2e"
ELEVATED_BG = "#1a253d"

# 强调色（输入框文本、光标、按钮悬停、状态成功提示）
GREEN_ACCENT = "#22d3a0"
ACCENT_HOVER = "#34e7b6"
ACCENT_PRESSED = "#16a37a"

# 文本颜色
TEXT_PRIMARY = "#e6edf6"
TEXT_MUTED = "#8a9ab4"
TEXT_WARNING = "#f5c451"
TEXT_ERROR = "#ff6b6b"

# 分隔线 / 次级按钮背景
SEPARATOR_COLOR = "#1f2a44"

# 次级按钮
BUTTON_BG = "#1a253d"
BUTTON_ACTIVE_BG = "#24325a"
BUTTON_GHOST_BORDER = "#2b3a5c"

SCROLLBAR_HANDLE = "#2b3a5c"
SCROLLBAR_HANDLE_HOVER = "#3b4f7c"

# 主窗口（输入框）背景与边框
INPUT_BACKGROUND = "#0d1626"
INPUT_BORDER = "#070b15"

# 设置图标默认色（未悬停）
CONFIG_BUTTON_IDLE = "#4a6b66"

# 透明色键（用于无边框圆角窗口透明背景）
TRANSPARENT_COLOR = "#ff00ff"

# 字体栈 —— 终端/极客风格用等宽字体，UI 标签用系统无衬线
MONO_FONT = '"Cascadia Code", "JetBrains Mono", "Consolas", "Courier New", monospace'
UI_FONT = '"Segoe UI", system-ui, sans-serif'
