#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

_T = TypeVar("_T")


def safe_qt_call(op: Callable[[], _T]) -> _T | None:
    try:
        return op()
    except (RuntimeError, TypeError):
        return None
