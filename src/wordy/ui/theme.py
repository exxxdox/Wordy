#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""共享 UI 颜色/主题常量。

Apple 式玻璃主题：中性灰阶分层底 + 半透明白色叠层 + 细高光描边 + 系统蓝强调色。
设置页、悬浮输入框、状态反馈、轮盘、托盘共用这一组 token，避免窗口间视觉割裂。

token 值保持 6 位 hex，因为 QPainter / QColor / 颜色混合直接消费它们；需要透明度
的 QSS 场景改用 :func:`rgba`，手绘玻璃叠层用 :func:`tint`。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PySide6.QtGui import QColor

# 分层表面：越靠上层越亮，模拟玻璃堆叠而不是单一纯色块。
WINDOW_BG = "#131316"
SURFACE_BG = "#1c1c1e"
ELEVATED_BG = "#2c2c2e"
RAISED_BG = "#3a3a3c"

# 强调色（Apple systemBlue 暗色外观）
# 保留既有 token 名称，让所有现有状态提示同步采用新强调色。
GREEN_ACCENT = "#0a84ff"
ACCENT_HOVER = "#409cff"
ACCENT_PRESSED = "#0060df"
ACCENT_SECONDARY = "#64d2ff"

# 文本颜色
TEXT_PRIMARY = "#f5f5f7"
TEXT_MUTED = "#98989d"
TEXT_WARNING = "#ffd60a"
TEXT_ERROR = "#ff453a"

# 分隔线 / 次级按钮背景
SEPARATOR_COLOR = "#38383a"

# 次级按钮
BUTTON_BG = "#2c2c2e"
BUTTON_ACTIVE_BG = "#3a3a3c"
BUTTON_GHOST_BORDER = "#48484a"

SCROLLBAR_HANDLE = "#48484a"
SCROLLBAR_HANDLE_HOVER = "#6c6c70"

# 主窗口（输入框）背景与边框
INPUT_BACKGROUND = "#1c1c1e"
INPUT_BORDER = "#48484a"

# 设置图标默认色（未悬停）
CONFIG_BUTTON_IDLE = "#98989d"

# 玻璃叠层：半透明白压在表面色上形成材质感。只在 QSS 使用，透明度靠 alpha 表达。
GLASS_SHEEN = "rgba(255, 255, 255, 4)"
GLASS_FILL = "rgba(255, 255, 255, 10)"
GLASS_FILL_STRONG = "rgba(255, 255, 255, 24)"
GLASS_HAIRLINE = "rgba(255, 255, 255, 26)"
GLASS_HAIRLINE_STRONG = "rgba(255, 255, 255, 56)"

# 圆角阶梯：Apple 的连续圆角在 Qt 里用统一圆角近似，只区分层级不追求曲率。
RADIUS_WINDOW = 20
RADIUS_CARD = 14
RADIUS_CONTROL = 10
RADIUS_CHIP = 8

# 字体栈 —— 终端/极客风格用等宽字体，UI 标签用系统无衬线
MONO_FONT = '"Cascadia Code", "JetBrains Mono", "Consolas", "Courier New", monospace'
UI_FONT = '"Segoe UI Variable", "Microsoft YaHei UI", "Segoe UI", sans-serif'


def rgba(hex_color: str, alpha: int) -> str:
    """把 6 位 hex 颜色转成 Qt QSS 可用的 rgba() 表达式。

    QSS 支持 rgba()，但 QPainter 与颜色混合函数只吃 hex；主题 token 统一存 hex，
    透明度在这里换算，省得每个样式块各自拼字符串。
    """
    text = hex_color.lstrip("#")
    red, green, blue = (int(text[index:index + 2], 16) for index in (0, 2, 4))
    return f"rgba({red}, {green}, {blue}, {alpha})"


def tint(hex_color: str, alpha: int) -> "QColor":
    """把主题 hex 转成带透明度的 QColor，供手绘玻璃叠层使用。

    Qt 只在调用时导入：token 本身是纯字符串，调色板测试不需要拉起 PySide6。
    """
    from PySide6.QtGui import QColor

    color = QColor(hex_color)
    color.setAlpha(alpha)
    return color
