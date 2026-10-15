#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""界面字体栈解析：用户首选族在前，内置回退链在后。"""

from __future__ import annotations

from wordy.ui.settings_style import build_settings_stylesheet
from wordy.ui.theme import UI_FONT, UI_FONT_FAMILIES, ui_font_stack


def test_default_stack_matches_the_builtin_constant():
    """没有用户选择时，渲染结果必须与既有常量逐字节一致。"""
    for empty in (None, "", "   "):
        assert ui_font_stack(empty) == UI_FONT


def test_preferred_family_leads_and_builtin_fallbacks_follow():
    stack = ui_font_stack("Consolas")

    assert stack.startswith('"Consolas", ')
    for family in UI_FONT_FAMILIES:
        assert f'"{family}"' in stack
    assert stack.endswith("sans-serif")


def test_choosing_a_family_already_in_the_stack_does_not_duplicate_it():
    assert ui_font_stack(UI_FONT_FAMILIES[0]) == UI_FONT


def test_missing_family_still_returns_the_full_fallback_chain():
    """用户存的族没装时不能抛异常，后面必须还接得住。"""
    stack = ui_font_stack("一个一定不存在的字体族")

    assert stack.startswith('"一个一定不存在的字体族", ')
    for family in UI_FONT_FAMILIES:
        assert f'"{family}"' in stack


def test_settings_stylesheet_uses_the_given_stack():
    assert f"font-family: {UI_FONT};" in build_settings_stylesheet()

    custom = build_settings_stylesheet(ui_font_stack("Consolas"))
    assert 'font-family: "Consolas", ' in custom


def test_settings_stylesheet_defaults_to_the_builtin_stack():
    """不传参时保持默认，既有调用方不用改。"""
    assert build_settings_stylesheet() == build_settings_stylesheet(None)
