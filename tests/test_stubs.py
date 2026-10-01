"""Dependency stubs must not mutate the real Qt module used by GUI tests."""

import sys
from types import ModuleType, SimpleNamespace

from tests import _stubs
from tests._stubs import DummyModule, DummySignal, DummySlot, install_module_stubs, patch_module_stubs


def test_dependency_stubs_preserve_real_qt_signals(monkeypatch):
    from PySide6 import QtCore

    signal, slot = QtCore.Signal, QtCore.Slot
    # Register the synthetic module first so even the import-time helper is undone.
    monkeypatch.setitem(sys.modules, "wordy_test_dependency", ModuleType("wordy_test_dependency"))
    install_module_stubs(("wordy_test_dependency",))
    assert QtCore.Signal is signal
    assert QtCore.Slot is slot
    patch_module_stubs(monkeypatch, ("wordy_test_dependency",))
    assert QtCore.Signal is signal
    assert QtCore.Slot is slot


def test_qt_stubs_receive_dummy_signals(monkeypatch):
    # Exercise the stub registry without replacing Qt modules used by live widgets.
    registry = {}
    monkeypatch.setattr(_stubs, "sys", SimpleNamespace(modules=registry))
    patch_module_stubs(monkeypatch, ("PySide6.QtCore",))
    qtcore = registry["PySide6.QtCore"]
    assert isinstance(qtcore, DummyModule)
    assert qtcore.Signal is DummySignal
    assert qtcore.Slot is DummySlot
