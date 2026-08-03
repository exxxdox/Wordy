#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for easy_tts.window.py.

These tests focus on two things:

1. Non-Windows safe defaults — the module must be importable and the
   Win32-bound helpers must return inert values without touching
   ``ctypes.windll.user32``/``kernel32`` on platforms where those do
   not exist (CI sandboxes, Linux contributors).
2. Geometry regressions for the pure-Python helpers
   (``clamp_window_position``, ``is_point_in_widget``,
   ``center_window``) that must work identically on every platform.
"""

from __future__ import annotations

import sys
from typing import cast

import pytest

import easy_tts.window


# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------


class _FakeGeometry:
    def __init__(self, x: int, y: int, width: int, height: int) -> None:
        self._x = x
        self._y = y
        self._width = width
        self._height = height

    def x(self) -> int:
        return self._x

    def y(self) -> int:
        return self._y

    def width(self) -> int:
        return self._width

    def height(self) -> int:
        return self._height

    def left(self) -> int:
        return self._x

    def right(self) -> int:
        return self._x + self._width

    def top(self) -> int:
        return self._y

    def bottom(self) -> int:
        return self._y + self._height


class _FakeScreen:
    def __init__(self, geometry: _FakeGeometry) -> None:
        self._geometry = geometry

    def availableGeometry(self) -> _FakeGeometry:
        return self._geometry


class _FakeWidget:
    def __init__(self, frame: _FakeGeometry, screen: _FakeScreen | None) -> None:
        self._frame = frame
        self._screen = screen
        self.set_geometry_calls: list[tuple[int, int, int, int]] = []

    def frameGeometry(self) -> _FakeGeometry:
        return self._frame

    def screen(self) -> _FakeScreen | None:
        return self._screen

    def setGeometry(self, x: int, y: int, width: int, height: int) -> None:
        self.set_geometry_calls.append((x, y, width, height))

    def winId(self) -> int:
        return 0


class _DeletedQtWidget:
    """Mimics a wrapped Qt widget whose C++ side is gone."""

    def frameGeometry(self) -> _FakeGeometry:
        raise RuntimeError("Internal C++ object already deleted.")


# ---------------------------------------------------------------------------
# Non-Windows safe defaults
# ---------------------------------------------------------------------------


def test_get_cursor_position_returns_none_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(easy_tts.window, "_IS_WINDOWS", False)

    assert easy_tts.window.get_cursor_position() is None


def test_is_left_button_down_returns_false_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(easy_tts.window, "_IS_WINDOWS", False)

    assert easy_tts.window.is_left_button_down() is False


def test_activate_window_is_noop_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(easy_tts.window, "_IS_WINDOWS", False)

    # Pass a widget that would explode if any Win32 call ran (winId raises),
    # to prove the guard short-circuits before touching ctypes.windll.
    class _ExplodingWidget:
        def winId(self) -> int:
            raise AssertionError("activate_window must not touch widget on non-Windows")

    easy_tts.window.activate_window(cast(easy_tts.window._Widget, _ExplodingWidget()))


def test_is_windows_flag_matches_sys_platform() -> None:
    assert easy_tts.window._IS_WINDOWS == sys.platform.startswith("win")


# ---------------------------------------------------------------------------
# Pure-Python geometry helpers (platform-independent)
# ---------------------------------------------------------------------------


def test_is_point_in_widget_inside() -> None:
    widget = _FakeWidget(_FakeGeometry(10, 20, 100, 50), screen=None)
    assert easy_tts.window.is_point_in_widget(cast(easy_tts.window._Widget, widget), 50, 40) is True


def test_is_point_in_widget_outside() -> None:
    widget = _FakeWidget(_FakeGeometry(10, 20, 100, 50), screen=None)
    assert easy_tts.window.is_point_in_widget(cast(easy_tts.window._Widget, widget), 9, 40) is False
    assert easy_tts.window.is_point_in_widget(cast(easy_tts.window._Widget, widget), 50, 200) is False


def test_is_point_in_widget_handles_deleted_qt_object() -> None:
    widget = _DeletedQtWidget()
    assert easy_tts.window.is_point_in_widget(cast(easy_tts.window._Widget, widget), 0, 0) is False


def test_clamp_window_position_within_bounds_unchanged() -> None:
    screen = _FakeScreen(_FakeGeometry(0, 0, 1920, 1080))
    x, y = easy_tts.window.clamp_window_position(
        cast(easy_tts.window._Screen, screen), 400, 300, 100, 200
    )
    assert (x, y) == (100, 200)


def test_clamp_window_position_clamps_to_right_and_bottom() -> None:
    screen = _FakeScreen(_FakeGeometry(0, 0, 1920, 1080))
    x, y = easy_tts.window.clamp_window_position(
        cast(easy_tts.window._Screen, screen), 400, 300, 5000, 5000
    )
    assert x == 1920 - 400
    assert y == 1080 - 300


def test_clamp_window_position_clamps_to_left_and_top() -> None:
    screen = _FakeScreen(_FakeGeometry(100, 50, 1920, 1080))
    x, y = easy_tts.window.clamp_window_position(
        cast(easy_tts.window._Screen, screen), 400, 300, -1000, -1000
    )
    assert (x, y) == (100, 50)


def test_clamp_window_position_with_widget_screen_source() -> None:
    screen = _FakeScreen(_FakeGeometry(0, 0, 800, 600))
    widget = _FakeWidget(_FakeGeometry(0, 0, 100, 100), screen=screen)
    x, y = easy_tts.window.clamp_window_position(
        cast(easy_tts.window._Widget, widget), 200, 200, 10_000, 10_000
    )
    assert x == 800 - 200
    assert y == 600 - 200


def test_clamp_window_position_none_source_returns_zero_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    no_primary_screen = lambda: None
    monkeypatch.setattr(easy_tts.window, "_primary_screen", no_primary_screen)

    x, y = easy_tts.window.clamp_window_position(None, 100, 100, 500, 500)
    assert (x, y) == (0, 0)


def test_center_window_sets_centered_geometry() -> None:
    screen = _FakeScreen(_FakeGeometry(0, 0, 1000, 800))
    widget = _FakeWidget(_FakeGeometry(0, 0, 1, 1), screen=screen)
    easy_tts.window.center_window(cast(easy_tts.window._Widget, widget), 400, 200)
    assert widget.set_geometry_calls == [((1000 - 400) // 2, (800 - 200) // 2, 400, 200)]


def test_center_window_uses_explicit_screen_source() -> None:
    inner_screen = _FakeScreen(_FakeGeometry(0, 0, 100, 100))
    outer_screen = _FakeScreen(_FakeGeometry(0, 0, 2000, 1500))
    widget = _FakeWidget(_FakeGeometry(0, 0, 1, 1), screen=inner_screen)
    screen_source = _FakeWidget(_FakeGeometry(0, 0, 1, 1), screen=outer_screen)
    easy_tts.window.center_window(
        cast(easy_tts.window._Widget, widget),
        500,
        300,
        screen_source=cast(easy_tts.window._Widget, screen_source),
    )
    assert widget.set_geometry_calls == [((2000 - 500) // 2, (1500 - 300) // 2, 500, 300)]
