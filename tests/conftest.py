#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Shared pytest fixtures.

Critical: ``secret_store`` imports ``keyring`` (and references
``keyring.errors.PasswordDeleteError``) at module import time. The test
environment does not have the real ``keyring`` package installed, and
we never want tests to touch the real OS credential store anyway. So
this conftest installs a minimal in-memory stub for ``keyring`` and
``keyring.errors`` in ``sys.modules`` *before* any test module imports
``secret_store``.

The ``fake_keyring`` fixture then exposes the in-memory backend so
tests can pre-seed values and assert against the recorded state. The
``keyring_unavailable`` fixture flips a flag that makes the production
code's ``_keyring_available()`` probe return False.
"""

from __future__ import annotations

import sys
from types import ModuleType
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator


# Sentinel API key used by tests that need a non-empty fake secret. The
# real key from the user's .env must NEVER appear in this repo or in
# test output.
SENTINEL_KEY_DO_NOT_LEAK = "sk-fake-SENTINEL_KEY_DO_NOT_LEAK-xyz"


class _PasswordDeleteError(Exception):
    """Stand-in for ``keyring.errors.PasswordDeleteError``."""


class FakeKeyringBackend:
    """In-memory keyring substitute, controllable per-test.

    Tracks (service, username) -> password and exposes flags so tests
    can simulate backend unavailability or per-operation failures.
    """

    def __init__(self) -> None:
        self.store: dict[tuple[str, str], str] = {}
        self.available: bool = True
        self.set_calls: list[tuple[str, str, str]] = []
        self.delete_calls: list[tuple[str, str]] = []
        self.get_calls: list[tuple[str, str]] = []
        self.fail_on_set: bool = False
        self.fail_on_get: bool = False
        self.fail_on_delete: bool = False

    def get_keyring(self) -> object:
        if not self.available:
            raise RuntimeError("Fake keyring backend marked unavailable")
        return self

    def get_password(self, service: str, username: str) -> str | None:
        self.get_calls.append((service, username))
        if not self.available:
            raise RuntimeError("Fake keyring backend marked unavailable")
        if self.fail_on_get:
            raise RuntimeError("Fake keyring get_password failure")
        return self.store.get((service, username))

    def set_password(self, service: str, username: str, password: str) -> None:
        self.set_calls.append((service, username, password))
        if not self.available:
            raise RuntimeError("Fake keyring backend marked unavailable")
        if self.fail_on_set:
            raise RuntimeError("Fake keyring set_password failure")
        self.store[(service, username)] = password

    def delete_password(self, service: str, username: str) -> None:
        self.delete_calls.append((service, username))
        if not self.available:
            raise RuntimeError("Fake keyring backend marked unavailable")
        if self.fail_on_delete:
            raise RuntimeError("Fake keyring delete_password failure")
        if (service, username) not in self.store:
            raise _PasswordDeleteError("no such password")
        del self.store[(service, username)]


def _install_keyring_stub_module() -> None:
    """Install a minimal ``keyring`` module in ``sys.modules``.

    This runs at conftest import time so that any later
    ``import secret_store`` succeeds even without the real ``keyring``
    package installed. The functions installed here are placeholders;
    fixtures rebind them via monkeypatch on a per-test basis.
    """
    errors_mod = ModuleType("keyring.errors")
    setattr(errors_mod, "PasswordDeleteError", _PasswordDeleteError)

    keyring_mod = ModuleType("keyring")
    setattr(keyring_mod, "errors", errors_mod)

    # Default no-op implementations: tests that need behavior must use
    # the ``fake_keyring`` or ``keyring_unavailable`` fixtures.
    def _noop_get_keyring() -> object:
        raise RuntimeError("keyring stub: not configured for this test")

    def _noop_get_password(service: str, username: str) -> str | None:
        raise RuntimeError("keyring stub: not configured for this test")

    def _noop_set_password(service: str, username: str, password: str) -> None:
        raise RuntimeError("keyring stub: not configured for this test")

    def _noop_delete_password(service: str, username: str) -> None:
        raise RuntimeError("keyring stub: not configured for this test")

    setattr(keyring_mod, "get_keyring", _noop_get_keyring)
    setattr(keyring_mod, "get_password", _noop_get_password)
    setattr(keyring_mod, "set_password", _noop_set_password)
    setattr(keyring_mod, "delete_password", _noop_delete_password)

    sys.modules.setdefault("keyring", keyring_mod)
    sys.modules.setdefault("keyring.errors", errors_mod)


_install_keyring_stub_module()


@pytest.fixture(autouse=True)
def cleanup_qt_application() -> "Iterator[None]":
    """Tear down leaked Qt widgets/apps so PySide tests do not crash at exit."""
    yield

    qt_widgets = sys.modules.get("PySide6.QtWidgets")
    qt_core = sys.modules.get("PySide6.QtCore")
    if qt_widgets is None:
        return
    qapplication = getattr(qt_widgets, "QApplication", None)
    qevent = getattr(qt_core, "QEvent", None) if qt_core is not None else None
    if qapplication is None:
        return
    try:
        app = qapplication.instance()
    except RuntimeError:
        return
    if app is None:
        return

    try:
        for widget in qapplication.topLevelWidgets():
            try:
                widget.close()
            except (RuntimeError, TypeError):
                pass
        qapplication.processEvents()
        if qevent is not None:
            qapplication.sendPostedEvents(None, qevent.Type.DeferredDelete)
        qapplication.processEvents()
        set_quit_on_last_window_closed = getattr(app, "setQuitOnLastWindowClosed", None)
        if callable(set_quit_on_last_window_closed):
            set_quit_on_last_window_closed(False)
    except (RuntimeError, TypeError):
        pass


@pytest.fixture
def fake_keyring(monkeypatch: pytest.MonkeyPatch) -> "Iterator[FakeKeyringBackend]":
    """Provide an in-memory keyring backend wired into ``secret_store``.

    Patches the ``keyring`` module functions that ``secret_store`` calls,
    so any production code path under test hits the in-memory store.
    Never touches the real OS credential store.
    """
    import keyring as keyring_mod  # the stub or real module
    backend = FakeKeyringBackend()

    monkeypatch.setattr(keyring_mod, "get_keyring", backend.get_keyring, raising=False)
    monkeypatch.setattr(keyring_mod, "get_password", backend.get_password, raising=False)
    monkeypatch.setattr(keyring_mod, "set_password", backend.set_password, raising=False)
    monkeypatch.setattr(
        keyring_mod, "delete_password", backend.delete_password, raising=False
    )

    yield backend


@pytest.fixture
def keyring_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> "Iterator[FakeKeyringBackend]":
    """Provide a keyring backend that reports as unavailable.

    Simulates a system with no usable OS credential store backend, so
    ``secret_store._keyring_available()`` returns False.
    """
    import keyring as keyring_mod
    backend = FakeKeyringBackend()
    backend.available = False

    monkeypatch.setattr(keyring_mod, "get_keyring", backend.get_keyring, raising=False)
    monkeypatch.setattr(keyring_mod, "get_password", backend.get_password, raising=False)
    monkeypatch.setattr(keyring_mod, "set_password", backend.set_password, raising=False)
    monkeypatch.setattr(
        keyring_mod, "delete_password", backend.delete_password, raising=False
    )

    yield backend
