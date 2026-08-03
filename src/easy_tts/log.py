#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Application log streaming bridge.

Provides a thread-safe in-memory ring buffer of formatted log lines plus an
optional Qt :class:`Signal` broadcaster so GUI consumers (e.g. a tray log
viewer backed by a ``QPlainTextEdit``) can subscribe without coupling the
stdlib :mod:`logging` machinery to widgets.

The module is import-safe in non-Qt contexts: PySide6 is imported lazily and
only when :func:`install_log_stream` is called. Pure-buffer usage (handler +
ring buffer, no signal) works without PySide6 installed.
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Protocol, override

from easy_tts.qt_lifecycle import safe_qt_call



DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
DEFAULT_LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


def _default_formatter() -> logging.Formatter:
    return logging.Formatter(DEFAULT_LOG_FORMAT, datefmt=DEFAULT_LOG_DATEFMT)


class MessageSignal(Protocol):
    """Structural type for a Qt ``Signal(str)`` instance.

    PySide6 ``Signal`` objects expose ``emit(value)`` and ``connect(slot)`` at
    runtime; declaring them via :class:`typing.Protocol` lets the handler stay
    decoupled from PySide6 so the module remains importable without Qt.
    """

    def emit(self, message: str, /) -> None: ...
    def connect(self, slot: object, /) -> object: ...


class LogBroadcasterProtocol(Protocol):
    """Structural type for the log broadcaster object.

    The broadcaster owns a single ``message_emitted`` signal. The handler
    needs nothing else from it, so the protocol stays minimal — a real
    :class:`PySide6.QtCore.QObject` subclass satisfies it without inheriting
    from this class.
    """

    message_emitted: MessageSignal


class LogRingBuffer:
    """Thread-safe ring buffer of formatted log messages.

    Backed by :class:`collections.deque` with a fixed ``maxlen`` so that once
    the buffer is full, the oldest entries are dropped to make room for new
    ones. All mutating and snapshot operations are guarded by a lock so it is
    safe to share the buffer across worker threads.
    """

    def __init__(self, max_size: int) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be a positive integer")
        self._max_size: int = int(max_size)
        self._messages: deque[str] = deque(maxlen=self._max_size)
        self._lock: threading.Lock = threading.Lock()

    @property
    def max_size(self) -> int:
        return self._max_size

    def append(self, message: str) -> None:
        """Append a fully formatted log message to the buffer."""
        with self._lock:
            self._messages.append(message)

    def snapshot(self) -> list[str]:
        """Return a point-in-time copy of the buffered messages in order."""
        with self._lock:
            return list(self._messages)

    def clear(self) -> None:
        """Drop all buffered messages."""
        with self._lock:
            self._messages.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._messages)


class RingBufferQtHandler(logging.Handler):
    """:mod:`logging` handler that fans records out to a ring buffer and an
    optional Qt broadcaster.

    The handler never touches widgets directly. When a broadcaster is attached
    via :func:`install_log_stream`, the handler emits the broadcaster's
    ``message_emitted`` signal with the formatted message. Cross-thread signal
    delivery is handled by Qt's default :class:`Qt.AutoConnection`, which
    queues the call onto the broadcaster's owning thread (the main GUI
    thread).
    """

    def __init__(
        self,
        buffer: LogRingBuffer,
        level: int = logging.NOTSET,
    ) -> None:
        super().__init__(level=level)
        self._buffer: LogRingBuffer = buffer
        self._broadcaster: LogBroadcasterProtocol | None = None

    @property
    def buffer(self) -> LogRingBuffer:
        return self._buffer

    @property
    def broadcaster(self) -> LogBroadcasterProtocol | None:
        return self._broadcaster

    def attach_broadcaster(self, broadcaster: LogBroadcasterProtocol) -> None:
        """Bind a broadcaster whose ``message_emitted`` signal will be fired
        for every subsequent record."""
        self._broadcaster = broadcaster

    def detach_broadcaster(self) -> None:
        self._broadcaster = None

    @override
    def format(self, record: logging.LogRecord) -> str:
        if self.formatter is None:
            return _default_formatter().format(record)
        return super().format(record)

    @override
    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = self.format(record)
        except Exception:
            self.handleError(record)
            return

        # 1) Persist into the ring buffer first so snapshot() always reflects
        #    delivery order, even if the Qt signal is queued.
        try:
            self._buffer.append(message)
        except Exception:
            self.handleError(record)
            return

        # 2) Broadcast to Qt consumers (widgets attach via signal/slot).
        broadcaster = self._broadcaster
        if broadcaster is None:
            return
        try:
            broadcaster.message_emitted.emit(message)
        except RuntimeError:
            # Broadcaster QObject was destroyed; drop the reference so we do
            # not keep poking a dead C++ object.
            self._broadcaster = None
        except Exception:
            self.handleError(record)


def _resolve_qt_signal_and_qobject() -> tuple[type, type]:
    """Return real PySide6 ``QObject`` and ``Signal`` types.

    Some test harnesses monkey-patch ``PySide6.QtCore.Signal`` to a stub that
    lacks ``connect`` and breaks signal-based broadcasting. Detect such a
    replacement and recover the real C-extension types by popping the cached
    module entry and re-importing — PySide6.QtCore re-initializes its native
    bindings and restores ``Signal`` to the genuine ``MetaSignal``-instance
    class.
    """

    import sys

    from PySide6.QtCore import QObject, Signal

    signal_is_intact = isinstance(Signal, type) and Signal.__module__ == "PySide6.QtCore"
    if signal_is_intact:
        return QObject, Signal

    for cached_name in ("PySide6.QtCore",):
        sys.modules.pop(cached_name, None)
    from PySide6.QtCore import QObject as RealQObject, Signal as RealSignal

    return RealQObject, RealSignal


def _build_broadcaster() -> LogBroadcasterProtocol:
    """Construct a ``QObject`` exposing ``message_emitted = Signal(str)``.

    PySide6 is imported lazily so that the rest of the module remains usable
    in pure-stdlib contexts (e.g. unit tests that only exercise the ring
    buffer).
    """

    QObject, Signal = _resolve_qt_signal_and_qobject()

    class LogBroadcaster(QObject):
        message_emitted = Signal(str)

        def __init__(self) -> None:
            super().__init__()

    return LogBroadcaster()


def install_log_stream(
    logger: logging.Logger | None = None,
    buffer: LogRingBuffer | None = None,
    handler: RingBufferQtHandler | None = None,
    *,
    max_size: int = 500,
) -> LogBroadcasterProtocol:
    """Install the log-streaming pipeline on ``logger`` and return the Qt
    broadcaster.

    The returned object owns a ``message_emitted`` :class:`Signal` to which
    GUI consumers (such as a tray log viewer) can connect. Calling this
    function repeatedly is idempotent: when neither ``handler`` nor
    ``buffer`` is supplied, the previously installed handler on the target
    logger is reused so duplicate records are not emitted.

    Parameters
    ----------
    logger:
        Target logger. Defaults to the root logger.
    buffer:
        Optional pre-built ring buffer. A fresh one of size ``max_size`` is
        created if omitted.
    handler:
        Optional pre-built handler. A new :class:`RingBufferQtHandler` is
        created if omitted.
    max_size:
        Capacity for the auto-created ring buffer when ``buffer`` is not
        provided.
    """

    target_logger = logger if logger is not None else logging.getLogger()

    existing_handler: RingBufferQtHandler | None = None
    if handler is None and buffer is None:
        for candidate in target_logger.handlers:
            if isinstance(candidate, RingBufferQtHandler):
                existing_handler = candidate
                break

    if existing_handler is not None:
        log_handler = existing_handler
        ring_buffer = log_handler.buffer
    else:
        ring_buffer = buffer if buffer is not None else LogRingBuffer(max_size=max_size)
        log_handler = handler if handler is not None else RingBufferQtHandler(buffer=ring_buffer)

    if log_handler.formatter is None:
        log_handler.setFormatter(_default_formatter())

    broadcaster = log_handler.broadcaster
    if broadcaster is None:
        broadcaster = _build_broadcaster()
        log_handler.attach_broadcaster(broadcaster)

    if log_handler not in target_logger.handlers:
        target_logger.addHandler(log_handler)

    _register_current_log_stream(ring_buffer, broadcaster)

    return broadcaster


class LogStream:
    """GUI-facing log stream adapter.

    Owns (or shares) a :class:`LogRingBuffer` plus a Qt broadcaster so the
    tray log window can subscribe via :meth:`attach` and receive both
    buffered snapshots and live updates. Direct GUI-thread writes go
    through :meth:`write`, which fans the message out to the buffer and
    the broadcaster signal.

    When ``buffer`` and ``broadcaster`` are supplied, the stream shares
    them with an external :class:`RingBufferQtHandler` so stdlib logging
    records routed through that handler appear in attached views without
    creating a second pipeline.
    """

    def __init__(
        self,
        max_size: int = 5000,
        *,
        buffer: LogRingBuffer | None = None,
        broadcaster: LogBroadcasterProtocol | None = None,
    ) -> None:
        self._buffer: LogRingBuffer = buffer if buffer is not None else LogRingBuffer(max_size=max_size)
        self._broadcaster: LogBroadcasterProtocol = (
            broadcaster if broadcaster is not None else _build_broadcaster()
        )
        self._attached_views: list[object] = []

    @property
    def buffer(self) -> LogRingBuffer:
        return self._buffer

    @property
    def broadcaster(self) -> LogBroadcasterProtocol:
        return self._broadcaster

    def snapshot(self) -> list[str]:
        return self._buffer.snapshot()

    def write(self, record: str) -> None:
        """Append ``record`` to the buffer and broadcast it to listeners."""
        message = record if record.endswith("\n") is False else record.rstrip("\n")
        self._buffer.append(message)
        try:
            self._broadcaster.message_emitted.emit(message)
        except RuntimeError:
            # Broadcaster was destroyed; rebuild on next write.
            self._broadcaster = _build_broadcaster()

    def attach(self, view: object) -> None:
        """Attach a Qt view exposing an ``append(str)`` slot.

        The current buffer is drained into the view first, then the
        broadcaster signal is connected so subsequent writes stream in.
        """
        append = getattr(view, "append_log", None)
        if append is None:
            append = getattr(view, "appendPlainText", None)
        if append is None:
            append = getattr(view, "append", None)
        if append is None:
            return

        for entry in self._buffer.snapshot():
            append(entry)

        self._broadcaster.message_emitted.connect(append)
        self._attached_views.append(view)


__all__ = [
    "DEFAULT_LOG_FORMAT",
    "DEFAULT_LOG_DATEFMT",
    "LogBroadcasterProtocol",
    "LogRingBuffer",
    "LogStream",
    "MessageSignal",
    "RingBufferQtHandler",
    "current_log_stream",
    "install_log_stream",
    "shutdown_log_stream",
]


_current_log_stream: LogStream | None = None


def _register_current_log_stream(
    buffer: LogRingBuffer,
    broadcaster: LogBroadcasterProtocol,
) -> LogStream:
    """Cache the latest installed pipeline as a process-wide ``LogStream``.

    GUI consumers (such as the tray log window) can call
    :func:`current_log_stream` to share the same buffer and broadcaster the
    root logger feeds, so stdlib :mod:`logging` records and direct
    ``stream.write`` calls converge on one in-memory pipeline.
    """

    global _current_log_stream
    cached = _current_log_stream
    if (
        cached is not None
        and cached.buffer is buffer
        and cached.broadcaster is broadcaster
    ):
        return cached
    stream = LogStream(buffer=buffer, broadcaster=broadcaster)
    _current_log_stream = stream
    return stream


def current_log_stream() -> LogStream | None:
    """Return the ``LogStream`` registered by the latest ``install_log_stream``
    call, or ``None`` if no pipeline has been installed yet."""

    return _current_log_stream


def shutdown_log_stream(logger: logging.Logger | None = None) -> None:
    """Detach the Qt log broadcaster and remove ring-buffer handlers.

    Called during application shutdown so background-thread log records
    (e.g. from the TTS janitor) cannot reach a Qt ``LogBroadcaster`` whose
    underlying C++ object is about to be destroyed by ``QApplication.quit``.
    """

    global _current_log_stream

    target_loggers: list[logging.Logger] = []
    if logger is not None:
        target_loggers.append(logger)
    root_logger = logging.getLogger()
    if root_logger not in target_loggers:
        target_loggers.append(root_logger)

    removed_handlers: list[RingBufferQtHandler] = []
    for target in target_loggers:
        for handler in list(target.handlers):
            if isinstance(handler, RingBufferQtHandler):
                safe_qt_call(handler.detach_broadcaster)
                target.removeHandler(handler)
                removed_handlers.append(handler)

    stream = _current_log_stream
    if stream is not None:
        broadcaster = stream.broadcaster
        delete_later = getattr(broadcaster, "deleteLater", None)
        if callable(delete_later):
            safe_qt_call(delete_later)
    _current_log_stream = None

    for handler in removed_handlers:
        try:
            handler.close()
        except Exception:  # noqa: BLE001 - best-effort close during shutdown
            pass
