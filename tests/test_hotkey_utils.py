#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for hotkey_utils shared tokenization/normalization."""

from wordy.hotkey import iter_hotkey_parts, normalize_key_part, split_hotkey


def test_normalize_key_part_basic():
    """strip + lowercase + underscore -> space."""
    assert normalize_key_part("Ctrl") == "ctrl"
    assert normalize_key_part("  F6  ") == "f6"
    assert normalize_key_part("Left_Windows") == "left windows"
    assert normalize_key_part("PAGE_DOWN") == "page down"
    assert normalize_key_part("") == ""
    assert normalize_key_part("   ") == ""


def test_split_hotkey_handles_spaces_as_underscores():
    """Space and underscore tokens are treated equivalently."""
    assert split_hotkey("ctrl+shift+f6") == ["ctrl", "shift", "f6"]
    assert split_hotkey("left windows+g") == ["left_windows", "g"]
    assert split_hotkey("ctrl+page down") == ["ctrl", "page_down"]
    # Empty splits preserved for caller filtering
    assert split_hotkey("+++") == ["", "", "", ""]
    assert split_hotkey("") == [""]


def test_iter_hotkey_parts_skips_empty_and_yields_pairs():
    """Yields (raw_part, normalized_key); skips empty after normalize."""
    parts = list(iter_hotkey_parts("Ctrl+Shift+F6"))
    assert parts == [("Ctrl", "ctrl"), ("Shift", "shift"), ("F6", "f6")]

    # Empty segments filtered
    assert list(iter_hotkey_parts("+++")) == []
    assert list(iter_hotkey_parts("")) == []
    assert list(iter_hotkey_parts("ctrl++a")) == [("ctrl", "ctrl"), ("a", "a")]


def test_iter_hotkey_parts_space_underscore_equivalence():
    """'left windows+g' and 'left_windows+g' produce the same normalized keys."""
    spaced = [key for _, key in iter_hotkey_parts("left windows+g")]
    underscored = [key for _, key in iter_hotkey_parts("left_windows+g")]
    assert spaced == underscored == ["left windows", "g"]


def test_iter_hotkey_parts_preserves_raw_for_error_reporting():
    """raw_part keeps original (pre-normalize) text, useful for error messages."""
    parts = list(iter_hotkey_parts("Ctrl+InvalidKey"))
    # raw_part still has original casing per token (after space->underscore split)
    assert parts == [("Ctrl", "ctrl"), ("InvalidKey", "invalidkey")]
