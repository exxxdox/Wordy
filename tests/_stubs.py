#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Shared test dependency stubs.

Provides Dummy* classes that stand in for heavy/native-only modules
(pyaudio, cartesia, websockets, tkinter, ...) during unit tests on
environments where those dependencies are absent.

This module is intentionally side-effect free at import time: it only
defines classes and helpers. Tests must explicitly opt in by calling
``install_module_stubs(...)`` (top-level, for import-time stubbing) or
``patch_module_stubs(monkeypatch, ...)`` (inside a fixture, for
test-scoped stubbing).
"""

from __future__ import annotations

import sys
from collections.abc import Iterable
from types import ModuleType
from typing import TYPE_CHECKING, Protocol, TypeVar

_F = TypeVar("_F", bound=object)

if TYPE_CHECKING:
    from typing import override
else:
    def override(func: _F) -> _F:
        return func


class MonkeyPatchProtocol(Protocol):
    def setitem(self, dic: dict[str, ModuleType], name: str, value: ModuleType) -> None: ...


CARTESIA_NATIVE_DEPS = (
    "pyaudio",
    "cartesia",
    "websockets",
    "websockets.sync",
    "websockets.sync.client",
)


class DummyClassMeta(type):
    """Metaclass whose attribute access yields fresh subclassable dummy classes."""

    def __getattr__(cls, name: str) -> type[object]:
        # When accessing a class attribute from a dummy module,
        # or a nested class, create a new dummy class that can be inherited.
        return DummyClassMeta(name, (object,), {})


class DummyModule(ModuleType):
    """Stand-in for a missing module: attribute / call access returns dummies."""

    def __init__(self, name: str = "dummy") -> None:
        super().__init__(name)

    @override
    def __getattr__(self, name: str) -> type[object]:
        # When importing from a dummy module,
        # give out a dummy class that can be inherited.
        return DummyClassMeta(name, (object,), {})

    def __call__(self, *args: object, **kwargs: object) -> "DummyModule":
        # When called as a function/constructor, return a new dummy instance.
        return DummyModule()


PYside6_STUBS: tuple[str, ...] = (
    "PySide6",
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
)


class DummySignal:
    def __init__(self, *types: type[object]) -> None:
        pass

    def __get__(
        self, obj: object | None, objtype: type[object] | None = None
    ) -> "DummySignal":
        return self

    def __call__(self, *args: object, **kwargs: object) -> DummyModule:
        return DummyModule()


_TFunc = TypeVar("_TFunc", bound=object)


class DummySlot:
    def __init__(
        self,
        *types: type[object],
        name: str | None = None,
        result: type[object] | None = None,
    ) -> None:
        pass

    def __call__(self, func: _TFunc) -> _TFunc:
        return func


def _apply_pyside6_qtcore_patches() -> None:
    qtcore = sys.modules.get("PySide6.QtCore")
    # Only patch our dummy module; mutating real Qt leaks into later GUI tests.
    if isinstance(qtcore, DummyModule):
        setattr(qtcore, "Signal", DummySignal)
        setattr(qtcore, "Slot", DummySlot)


def install_module_stubs(module_names: Iterable[str]) -> None:
    for name in module_names:
        sys.modules[name] = DummyModule()
    _apply_pyside6_qtcore_patches()


def patch_module_stubs(
    monkeypatch: MonkeyPatchProtocol, module_names: Iterable[str]
) -> None:
    for name in module_names:
        monkeypatch.setitem(sys.modules, name, DummyModule())
    _apply_pyside6_qtcore_patches()
