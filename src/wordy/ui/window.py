#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""PySide6/Windows 窗口焦点和位置工具。"""

from __future__ import annotations

import ctypes
import importlib
import sys
from ctypes import wintypes
from typing import Any, Protocol, SupportsInt, cast

SW_SHOW = 5
_IS_WINDOWS = sys.platform.startswith("win")


class _Geometry(Protocol):
    def x(self) -> int: ...
    def y(self) -> int: ...
    def width(self) -> int: ...
    def height(self) -> int: ...
    def left(self) -> int: ...
    def right(self) -> int: ...
    def top(self) -> int: ...
    def bottom(self) -> int: ...


class _Screen(Protocol):
    def availableGeometry(self) -> _Geometry: ...


# 用 Any 而非 _Widget Protocol——PySide6 QWidget.setGeometry 有 QRect 重载，
# pyright 无法将子类匹配到协议声明的单一重载，统一用 Any 接受所有 QWidget 子类。
_Widget = Any
"""PySide6 QWidget 或其子类的实例。"""


class _QGuiApplication(Protocol):
    @staticmethod
    def primaryScreen() -> _Screen | None: ...


def _primary_screen() -> _Screen | None:
    try:
        qt_gui = importlib.import_module("PySide6.QtGui")
        q_gui_application = cast(_QGuiApplication, getattr(qt_gui, "QGuiApplication"))
        return q_gui_application.primaryScreen()
    except (ImportError, AttributeError, RuntimeError):
        return None


def get_cursor_position() -> tuple[int, int] | None:
    """获取当前鼠标指针屏幕坐标。"""
    if not _IS_WINDOWS:
        return None
    point = wintypes.POINT()
    if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
        return None
    return point.x, point.y


def is_left_button_down() -> bool:
    """判断鼠标左键当前是否按下。"""
    if not _IS_WINDOWS:
        return False
    return cast(int, ctypes.windll.user32.GetAsyncKeyState(0x01)) & 0x8000 != 0


def _available_geometry(widget: Any = None) -> tuple[int, int, int, int]:
    """返回目标窗口所在屏幕的可用几何范围。"""
    screen = widget.screen() if widget is not None else None  # type: ignore[union-attr]
    if screen is None:
        screen = _primary_screen()
    if screen is None:
        return 0, 0, 0, 0

    geometry = screen.availableGeometry()
    return geometry.x(), geometry.y(), geometry.width(), geometry.height()


def is_point_in_widget(widget: Any, x: int, y: int) -> bool:
    """判断屏幕坐标是否落在 QWidget 窗口边框范围内。"""
    try:
        geometry = widget.frameGeometry()  # type: ignore[union-attr]
    except RuntimeError:
        return False

    return geometry.left() <= x <= geometry.right() and geometry.top() <= y <= geometry.bottom()


def activate_window(widget: Any) -> None:
    """尽量将 QWidget 对应的 Win32 窗口激活到前台。"""
    if not _IS_WINDOWS:
        return
    current_thread_id = 0
    foreground_thread_id = 0
    user32 = None
    try:
        hwnd = int(widget.winId())  # type: ignore[union-attr]
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        current_thread_id = cast(int, kernel32.GetCurrentThreadId())
        foreground_hwnd = cast(int, user32.GetForegroundWindow())
        foreground_thread_id = user32.GetWindowThreadProcessId(foreground_hwnd, None) if foreground_hwnd else 0

        if foreground_thread_id:
            user32.AttachThreadInput(current_thread_id, foreground_thread_id, True)

        user32.ShowWindow(hwnd, SW_SHOW)
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
        user32.SetFocus(hwnd)
    except (AttributeError, OSError, RuntimeError):
        return
    finally:
        if user32 is not None and foreground_thread_id:
            user32.AttachThreadInput(current_thread_id, foreground_thread_id, False)


def center_window(widget: Any, width: int, height: int, screen_source: Any = None) -> None:
    """按目标屏幕可用范围居中 QWidget 窗口。"""
    screen_x, screen_y, screen_width, screen_height = _available_geometry(screen_source or widget)
    x = screen_x + max(0, (screen_width - width) // 2)
    y = screen_y + max(0, (screen_height - height) // 2)
    widget.setGeometry(x, y, width, height)  # type: ignore[union-attr]


def clamp_window_position(screen_source: Any, width: int, height: int, x: int, y: int) -> tuple[int, int]:
    """将窗口左上角限制在屏幕可用范围内。"""
    if screen_source is not None and hasattr(screen_source, "availableGeometry"):
        geometry = cast(_Screen, screen_source).availableGeometry()
        screen_x, screen_y, screen_width, screen_height = geometry.x(), geometry.y(), geometry.width(), geometry.height()
    else:
        screen_x, screen_y, screen_width, screen_height = _available_geometry(screen_source)

    max_x = screen_x + max(0, screen_width - width)
    max_y = screen_y + max(0, screen_height - height)
    return min(max(screen_x, x), max_x), min(max(screen_y, y), max_y)
