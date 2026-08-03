#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for safe_qt_call — a Qt-safe callable wrapper that guards against
RuntimeError and TypeError from destroyed/deleted Qt objects."""

import pytest

from easy_tts.qt_lifecycle import safe_qt_call


class TestSafeQtCall:
    """Tests for safe_qt_call(op)."""

    def test_success_returns_value(self) -> None:
        """When the callable succeeds, its return value is passed through."""
        result = safe_qt_call(lambda: 42)
        assert result == 42

    def test_success_returns_none_value(self) -> None:
        """A callable that legitimately returns None should pass through."""
        result = safe_qt_call(lambda: None)
        assert result is None

    def test_runtime_error_returns_none(self) -> None:
        """RuntimeError (e.g. wrapped C++ object deleted) → None."""
        def _raise_runtime() -> int:
            raise RuntimeError("wrapped C/C++ object has been deleted")
        result = safe_qt_call(_raise_runtime)
        assert result is None

    def test_type_error_returns_none(self) -> None:
        """TypeError (e.g. calling a method on a destroyed object) → None."""
        def _raise_type() -> str:
            raise TypeError("'NoneType' object is not callable")
        result = safe_qt_call(_raise_type)
        assert result is None

    def test_value_error_propagates(self) -> None:
        """Exceptions other than RuntimeError/TypeError are not swallowed."""
        def _raise_value() -> bool:
            raise ValueError("unexpected value")

        with pytest.raises(ValueError, match="unexpected value"):
            safe_qt_call(_raise_value)

    def test_custom_exception_propagates(self) -> None:
        """Custom exception classes are not caught."""

        class CustomError(Exception):
            pass

        def _raise_custom() -> None:
            raise CustomError("custom failure")

        with pytest.raises(CustomError, match="custom failure"):
            safe_qt_call(_raise_custom)

    def test_callable_with_closure(self) -> None:
        """The callable may capture variables from an enclosing scope."""
        captured = "hello"
        result = safe_qt_call(lambda: captured.upper())
        assert result == "HELLO"
