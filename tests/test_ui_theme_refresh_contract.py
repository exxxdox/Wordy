#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""RED contract tests for the modern dark UI refresh.

These tests pin the palette tokens that ``easy_tts.ui.theme.py`` must expose after
the refresh, while preserving the existing tokens used by current code.
They also assert structural invariants of the settings stylesheet and the
input overlay capsule so the visual refresh cannot regress behavior.
"""

from __future__ import annotations

import importlib
import os
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

_ = os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")


# ---------------------------------------------------------------------------
# Pure palette tests (no PySide6 required).
# ---------------------------------------------------------------------------


def test_palette_tokens_present() -> None:
    """New modern-dark palette tokens must exist and be 6-digit hex."""
    theme = importlib.import_module("easy_tts.ui.theme")

    required = (
        "SURFACE_BG",
        "ELEVATED_BG",
        "ACCENT_HOVER",
        "ACCENT_PRESSED",
        "BUTTON_GHOST_BORDER",
        "SCROLLBAR_HANDLE",
        "SCROLLBAR_HANDLE_HOVER",
    )
    for name in required:
        assert hasattr(theme, name), f"easy_tts.ui.theme missing new token: {name}"
        value = getattr(theme, name)
        assert isinstance(value, str), f"{name} must be a string, got {type(value)!r}"
        assert _HEX_RE.match(value), f"{name}={value!r} is not a 6-digit hex color"


def test_palette_backwards_compat_aliases_preserved() -> None:
    """Existing palette tokens must remain available and valid hex values."""
    theme = importlib.import_module("easy_tts.ui.theme")

    legacy = (
        "GREEN_ACCENT",
        "INPUT_BACKGROUND",
        "INPUT_BORDER",
        "WINDOW_BG",
        "TEXT_PRIMARY",
        "TEXT_MUTED",
        "TEXT_WARNING",
        "TEXT_ERROR",
        "SEPARATOR_COLOR",
        "BUTTON_BG",
        "BUTTON_ACTIVE_BG",
        "CONFIG_BUTTON_IDLE",
        "TRANSPARENT_COLOR",
    )
    for name in legacy:
        assert hasattr(theme, name), f"easy_tts.ui.theme dropped legacy token: {name}"
        value = getattr(theme, name)
        assert isinstance(value, str), f"{name} must be a string, got {type(value)!r}"
        assert _HEX_RE.match(value), f"{name}={value!r} is not a 6-digit hex color"

    assert theme.TRANSPARENT_COLOR == "#ff00ff"


# ---------------------------------------------------------------------------
# Qt-backed structural tests.
# ---------------------------------------------------------------------------


def _install_native_dependency_stubs(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("pyaudio", "cartesia", "websockets.sync.client"):
        monkeypatch.setitem(sys.modules, name, ModuleType(name))

    cartesia_module = sys.modules["cartesia"]
    setattr(cartesia_module, "Cartesia", object)

    websocket_client_module = sys.modules["websockets.sync.client"]
    setattr(websocket_client_module, "ClientConnection", object)


def _new_settings_window(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    _install_native_dependency_stubs(monkeypatch)

    _ = pytest.importorskip("PySide6.QtWidgets")
    try:
        from PySide6.QtWidgets import QApplication
    except Exception as exc:  # pragma: no cover - dependency-specific import failures
        pytest.skip(f"QApplication cannot be imported: {exc}")
        raise
    try:
        from easy_tts.ui.settings import SettingsState, SettingsWindow
    except Exception as exc:  # pragma: no cover - dependency-specific import failures
        pytest.skip(f"SettingsWindow dependencies cannot be imported: {exc}")
        raise

    app = QApplication.instance() or QApplication(sys.argv[:1])
    state = SettingsState(
        hotkey="f6",
        hotkey_name="F6",
        voice_id="voice-a",
        voice_name="Old Voice",
        volume=1.0,
        overlay_opacity=1.0,
        tts_backend="cartesia-bytes",
        fixed_center=True,
        voices_cache=[{"id": "voice-a", "name": "Old Voice"}],
        voices_loading=False,
        voice_fetch_error=None,
    )
    window: Any | None = None
    try:
        window = SettingsWindow(
            None,
            state,
            on_record_hotkey=lambda: None,
            on_refresh_voices=lambda: None,
            on_apply=lambda _window: None,
            on_close=lambda: None,
        )
    except Exception as exc:  # pragma: no cover - environment-specific Qt failures
        pytest.skip(f"SettingsWindow cannot be instantiated on this platform: {exc}")
    assert window is not None
    return app, window


def test_settings_stylesheet_contains_modern_selectors(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = pytest.importorskip("PySide6")
    _app, window = _new_settings_window(monkeypatch)
    try:
        stylesheet = window.window.styleSheet() or ""

        required_substrings = (
            "QFrame#dialogShell",
            "QPushButton#applyButton",
            "QPushButton#cancelButton",
            "QScrollBar::handle:vertical",
            "QScrollBar::handle:vertical:hover",
            "QSlider::handle:horizontal",
            "QComboBox QAbstractItemView",
            "border-radius:",
        )
        for needle in required_substrings:
            assert needle in stylesheet, (
                f"settings stylesheet missing required selector/property: {needle!r}"
            )

        forbidden_substrings = (
            "box-shadow",
            "caret-color",
            "transition",
            "linear-gradient(",
            "var(--",
            "data:image",
        )
        for needle in forbidden_substrings:
            assert needle not in stylesheet, (
                f"settings stylesheet must not contain unsupported Qt CSS: {needle!r}"
            )
    finally:
        window.close()


# ---------------------------------------------------------------------------
# Input overlay invariants.
# ---------------------------------------------------------------------------


_OVERLAY_CONFIG = {
    "hotkey": "f6",
    "name": "F6",
    "voice_id": "voice-current",
    "voice_name": "Current Voice",
    "volume": 1.0,
    "overlay_opacity": 1.0,
    "tts_backend": "cartesia-bytes",
    "fixed_center": True,
    "window_position": None,
}


def _install_native_hotkey_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    module = ModuleType("easy_tts.native_hotkey")

    class NativeHotkeyListener:
        started: bool

        def __init__(self, *args: object, **kwargs: object) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

        def stop(self) -> None:
            self.started = False

    setattr(module, "NativeHotkeyListener", NativeHotkeyListener)
    monkeypatch.setitem(sys.modules, "easy_tts.native_hotkey", module)


def _import_input_overlay(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    monkeypatch.setattr(sys, "platform", "win32")
    _install_native_hotkey_stub(monkeypatch)

    # 用 AppSettings 替代已删除的 load_initial_config / save_app_config
    from easy_tts.config import AppSettings
    _test_settings = AppSettings()
    _test_settings.hotkey = _OVERLAY_CONFIG["hotkey"]
    _test_settings.name = _OVERLAY_CONFIG["name"]
    _test_settings.voice_id = _OVERLAY_CONFIG["voice_id"]
    _test_settings.voice_name = _OVERLAY_CONFIG["voice_name"]
    _test_settings.volume = _OVERLAY_CONFIG["volume"]
    _test_settings.overlay_opacity = _OVERLAY_CONFIG["overlay_opacity"]
    _test_settings.tts_backend = _OVERLAY_CONFIG["tts_backend"]
    _test_settings.fixed_center = _OVERLAY_CONFIG["fixed_center"]
    monkeypatch.setattr(AppSettings, "load", lambda **kw: _test_settings)
    monkeypatch.setattr(AppSettings, "update", lambda self, **kw: Path("/tmp/wavtrans-test-config.json"))
    monkeypatch.setattr(
        "easy_tts.config.get_active_config_file", lambda: Path("/tmp/wavtrans-test-config.json")
    )

    original_module = sys.modules.pop("easy_tts.ui.overlay", None)
    module = importlib.import_module("easy_tts.ui.overlay")

    def skip_voice_loading(_self: object, show_status: bool = True) -> None:
        _ = show_status

    monkeypatch.setattr(module.InputOverlay, "_start_load_voices", skip_voice_loading)
    if original_module is not None:
        monkeypatch.setitem(sys.modules, "input_overlay_original_for_contract", original_module)
    return module


def test_input_overlay_capsule_invariants_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    _ = pytest.importorskip("PySide6")
    module = _import_input_overlay(monkeypatch)
    submitted: list[str] = []
    try:
        overlay: Any = module.InputOverlay(on_submit=submitted.append)
        overlay._ensure_ui()
    except Exception as exc:  # pragma: no cover - environment-specific Qt failures
        pytest.skip(f"InputOverlay cannot be instantiated on this platform: {exc}")
        raise
    if overlay.root is None or overlay.entry is None:
        pytest.skip("InputOverlay did not create Qt widgets in this environment")
    root = overlay.root
    entry = overlay.entry
    assert root is not None
    assert entry is not None

    try:
        entry_style = entry.styleSheet() or ""

        required_entry = ("font-size: 24px", "border: none", "padding: 0")
        for needle in required_entry:
            assert needle in entry_style, (
                f"overlay entry stylesheet missing required token: {needle!r}"
            )

        forbidden_entry = ("font-weight", "caret-color", "setCursorWidth")
        for needle in forbidden_entry:
            assert needle not in entry_style, (
                f"overlay entry stylesheet must not contain: {needle!r}"
            )

        settings_button = getattr(root, "settings_button", None)
        assert settings_button is not None, "overlay root must expose settings_button attribute"
        settings_style = settings_button.styleSheet() or ""
        assert "font-size: 18px" in settings_style, (
            "settingsButton stylesheet must include 'font-size: 18px'"
        )

        size = root.size()
        assert size.width() == 540, f"overlay root width must be 540, got {size.width()}"
        assert size.height() == 58, f"overlay root height must be 58, got {size.height()}"
    finally:
        overlay.stop()
