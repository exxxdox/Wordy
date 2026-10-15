#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Apple 式玻璃主题契约测试。

主题从「深海蓝 + 青色科技感」改为中性灰阶玻璃：表面靠半透明白叠层与细高光
描边分层，强调色换成系统蓝。这些断言锁住玻璃主题的结构不变量，避免后续换色
时退回纯色卡片、实色粗边或旧的青色系。
"""

from __future__ import annotations

import importlib
import os
import re

import pytest

_ = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_RGBA_RE = re.compile(r"^rgba\((\d{1,3}), (\d{1,3}), (\d{1,3}), (\d{1,3})\)$")

_NEUTRAL_SPREAD = 6
_GLASS_ALPHA_RANGE = (1, 80)

_UI_DIR = os.path.join(os.path.dirname(__file__), "..", "src", "wordy", "ui")


def _rgb(hex_color: str) -> tuple[int, int, int]:
    return (int(hex_color[1:3], 16), int(hex_color[3:5], 16), int(hex_color[5:7], 16))


def test_surface_tokens_are_neutral_glass_layers() -> None:
    """表面色必须是低饱和中性灰阶，且由暗到亮递增，模拟玻璃堆叠。"""
    theme = importlib.import_module("wordy.ui.theme")

    layers: list[int] = []
    for name in ("WINDOW_BG", "SURFACE_BG", "ELEVATED_BG", "RAISED_BG"):
        value = getattr(theme, name, None)
        assert isinstance(value, str), f"缺少分层表面 token: {name}"
        assert _HEX_RE.match(value), f"{name}={value!r} 不是 6 位 hex"
        channels = _rgb(value)
        assert max(channels) - min(channels) <= _NEUTRAL_SPREAD, (
            f"{name}={value!r} 不够中性；玻璃底应为灰阶，不饱和"
        )
        layers.append(sum(channels))

    assert layers == sorted(layers), f"表面层级必须由暗到亮递增，实际亮度 {layers}"


def test_accent_tokens_are_system_blue() -> None:
    """强调色换成系统蓝：蓝通道不低于绿通道，且高于红通道。"""
    theme = importlib.import_module("wordy.ui.theme")

    for name in ("GREEN_ACCENT", "ACCENT_HOVER", "ACCENT_PRESSED"):
        value = getattr(theme, name)
        red, green, blue = _rgb(value)
        assert blue > red, f"{name}={value!r} 不是蓝色系（蓝通道必须高于红通道）"
        assert blue >= green, f"{name}={value!r} 不是蓝色系（蓝通道必须不低于绿通道）"


def test_glass_overlay_tokens_are_translucent_white() -> None:
    """玻璃叠层 token 必须是半透明白，透明度落在可见但不过曝的区间。"""
    theme = importlib.import_module("wordy.ui.theme")

    alphas: dict[str, int] = {}
    for name in ("GLASS_SHEEN", "GLASS_FILL", "GLASS_FILL_STRONG", "GLASS_HAIRLINE", "GLASS_HAIRLINE_STRONG"):
        value = getattr(theme, name, None)
        assert isinstance(value, str), f"缺少玻璃叠层 token: {name}"
        match = _RGBA_RE.match(value)
        assert match is not None, f"{name}={value!r} 不是 rgba(r, g, b, a) 表达式"
        red, green, blue, alpha = (int(part) for part in match.groups())
        assert (red, green, blue) == (255, 255, 255), f"{name}={value!r} 必须是白色叠层"
        low, high = _GLASS_ALPHA_RANGE
        assert low <= alpha <= high, f"{name} 透明度 {alpha} 超出玻璃叠层区间 {_GLASS_ALPHA_RANGE}"
        alphas[name] = alpha

    assert alphas["GLASS_FILL_STRONG"] > alphas["GLASS_FILL"], "强调叠层必须比常规叠层更亮"
    assert alphas["GLASS_HAIRLINE_STRONG"] > alphas["GLASS_HAIRLINE"], "高光描边必须比常规描边更亮"


def test_radius_scale_is_monotonic() -> None:
    """圆角阶梯必须分层：窗口 > 卡片 > 控件 > 小胶囊，且最小圆角仍可见。"""
    theme = importlib.import_module("wordy.ui.theme")

    scale = [theme.RADIUS_WINDOW, theme.RADIUS_CARD, theme.RADIUS_CONTROL, theme.RADIUS_CHIP]
    assert scale == sorted(scale, reverse=True), f"圆角阶梯必须递减，实际 {scale}"
    assert theme.RADIUS_CHIP >= 6, "最小圆角也要保留可辨认的 Apple 圆角感"


def test_rgba_helper_emits_qss_notation() -> None:
    """rgba() 把 hex token 换算成 QSS 表达式，避免各样式块各自拼字符串。"""
    theme = importlib.import_module("wordy.ui.theme")

    assert theme.rgba("#0a84ff", 64) == "rgba(10, 132, 255, 64)"
    assert theme.rgba("#ffffff", 0) == "rgba(255, 255, 255, 0)"


def test_rgba_helper_rejects_non_hex() -> None:
    """无效输入立刻报错，而不是生成一个 Qt 会静默忽略的样式值。"""
    theme = importlib.import_module("wordy.ui.theme")

    with pytest.raises(ValueError):
        theme.rgba("rgba(1, 2, 3, 4)", 10)


def test_tint_returns_qt_color_with_alpha() -> None:
    """tint() 给手绘玻璃叠层提供带透明度的 QColor。"""
    pytest.importorskip("PySide6.QtGui")
    theme = importlib.import_module("wordy.ui.theme")

    color = theme.tint("#0a84ff", 128)
    assert color.name() == "#0a84ff"
    assert color.alpha() == 128


# ---------------------------------------------------------------------------
# 设置页样式表：玻璃结构而不是实色粗边
# ---------------------------------------------------------------------------


def test_settings_stylesheet_uses_glass_layers_and_hairlines() -> None:
    from wordy.ui.settings_style import build_settings_stylesheet

    css = build_settings_stylesheet()
    assert "border: 1px solid rgba(255, 255, 255," in css, "控件必须用 1px 细高光描边"
    assert "border-bottom: 2px solid" not in css, "底边加粗是终端风格，玻璃主题改用 1px 描边"
    assert "qlineargradient" in css, "窗口背景用渐变分层，避免整块纯色"


def test_settings_stylesheet_drops_dead_apply_button_style() -> None:
    """应用按钮已改为即时保存，样式表不应保留没有控件使用的死样式。"""
    from wordy.ui.settings_style import build_settings_stylesheet

    assert "QPushButton#applyButton" not in build_settings_stylesheet()


def test_settings_stylesheet_radius_follows_theme_scale() -> None:
    from wordy.ui.settings_style import build_settings_stylesheet
    from wordy.ui.theme import RADIUS_CONTROL

    css = build_settings_stylesheet()
    assert f"border-radius: {RADIUS_CONTROL}px" in css, "控件圆角必须取自主题阶梯"


# ---------------------------------------------------------------------------
# 手绘界面：轮盘、悬浮框与托盘也必须走主题 token，避免视觉断层
# ---------------------------------------------------------------------------

_PAINT_SOURCES = (
    "chat_wheel.py",
    "chat_wheel_settings.py",
    "overlay_widgets.py",
    "tray.py",
)

# 旧主题的硬编码色；留在手绘代码里说明还没接上主题。
# 不含应用图标品牌色：那是标识，故意不跟随界面换色，由下面单独的用例钉住。
_LEGACY_HARDCODED_COLORS = (
    '"#55d9f2"',
    '"#8d9fff"',
    '"#9bb3cb"',
    '"#6b839b"',
    '"#f0fbff"',
    '"#071321"',
    '"#0e2033"',
    '"#152d45"',
    '"#0b1b2d"',
    '"#34536f"',
)


@pytest.mark.parametrize("file_name", _PAINT_SOURCES)
def test_painted_widgets_use_theme_tokens(file_name: str) -> None:
    """手绘控件不得保留旧主题硬编码色，否则换主题会留下视觉断层。"""
    with open(os.path.join(_UI_DIR, file_name), encoding="utf-8") as handle:
        text = handle.read()

    leftovers = [color for color in _LEGACY_HARDCODED_COLORS if color in text]
    assert leftovers == [], f"{file_name} 仍硬编码旧主题色: {leftovers}"


def test_tray_icon_keeps_the_application_brand_palette() -> None:
    """托盘图标是应用标识，必须保持原来的绿白配色，不跟随界面主题。

    这个配色和打包用的 wavtrans.ico 是同一套；换成强调色会让托盘里的应用
    标识和 exe 图标对不上。
    """
    from wordy.ui.tray import ICON_ACCENT, ICON_BACKGROUND

    assert ICON_BACKGROUND == "#0b1220"
    assert ICON_ACCENT == "#22d3a0"

    with open(os.path.join(_UI_DIR, "tray.py"), encoding="utf-8") as handle:
        text = handle.read()

    assert "ICON_ACCENT" in text and "ICON_BACKGROUND" in text
    assert "GREEN_ACCENT" not in text, "托盘图标不应使用界面强调色"


def test_overlay_paint_uses_specular_hairline_instead_of_terminal_dots() -> None:
    """悬浮输入框去掉终端点阵，改用上缘细高光描边表达玻璃边缘。"""
    with open(os.path.join(_UI_DIR, "overlay_widgets.py"), encoding="utf-8") as handle:
        text = handle.read()

    assert "drawPoint(" not in text, "终端点阵已移除"
    assert "QBrush(edge)" in text, "玻璃边缘必须用带渐变的 1px 高光描边绘制"
