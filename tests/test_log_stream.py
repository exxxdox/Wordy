#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""RED contract tests for the application log streaming bridge."""

from __future__ import annotations

import logging
import os
import threading

import pytest

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATEFMT = "%Y-%m-%d %H:%M:%S"


@pytest.fixture
def formatter() -> logging.Formatter:
    return logging.Formatter(LOG_FORMAT, datefmt=LOG_DATEFMT)


def test_handler_appends_formatted_messages_in_order(formatter: logging.Formatter) -> None:
    from easy_tts.log import LogRingBuffer, RingBufferQtHandler

    buffer = LogRingBuffer(max_size=10)
    handler = RingBufferQtHandler(buffer=buffer)
    handler.setFormatter(formatter)
    logger = logging.getLogger("tests.log_stream.order")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    logger.info("first message")
    logger.warning("second message")

    messages = buffer.snapshot()
    assert len(messages) == 2, f"expected two buffered records, got {messages!r}"
    assert "[INFO] tests.log_stream.order: first message" in messages[0], messages[0]
    assert "[WARNING] tests.log_stream.order: second message" in messages[1], messages[1]
    assert messages[0] != messages[1], "buffer must preserve distinct formatted messages in append order"


def test_buffer_caps_at_max_size_and_drops_oldest(formatter: logging.Formatter) -> None:
    from easy_tts.log import LogRingBuffer, RingBufferQtHandler

    buffer = LogRingBuffer(max_size=3)
    handler = RingBufferQtHandler(buffer=buffer)
    handler.setFormatter(formatter)
    logger = logging.getLogger("tests.log_stream.capacity")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)

    for index in range(5):
        logger.info("message %s", index)

    messages = buffer.snapshot()
    assert len(messages) == 3, f"expected max_size=3 to cap buffer, got {messages!r}"
    assert all("message 0" not in message and "message 1" not in message for message in messages), (
        f"oldest messages must be dropped first, got {messages!r}"
    )
    assert ["message 2" in messages[0], "message 3" in messages[1], "message 4" in messages[2]] == [
        True,
        True,
        True,
    ], f"expected retained messages 2, 3, 4 in order, got {messages!r}"


def test_qt_broadcaster_signal_emits_formatted_messages(formatter: logging.Formatter) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _ = pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication

    from easy_tts.log import LogRingBuffer, RingBufferQtHandler, install_log_stream

    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app
    logger = logging.getLogger("tests.log_stream.qt_signal")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    buffer = LogRingBuffer(max_size=10)
    handler = RingBufferQtHandler(buffer=buffer)
    broadcaster = install_log_stream(logger=logger, buffer=buffer, handler=handler)
    handler.setFormatter(formatter)
    received: list[str] = []
    broadcaster.message_emitted.connect(received.append)

    logger.error("signal payload")
    QCoreApplication.processEvents()

    assert len(received) == 1, f"expected one Qt signal emission, got {received!r}"
    assert "[ERROR] tests.log_stream.qt_signal: signal payload" in received[0], received[0]
    assert received == buffer.snapshot(), "Qt signal payload must match the formatted ring-buffer entry"


def test_worker_thread_logging_is_marshalled_safely_after_process_events(formatter: logging.Formatter) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _ = pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication

    from easy_tts.log import LogRingBuffer, RingBufferQtHandler, install_log_stream

    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app
    logger = logging.getLogger("tests.log_stream.worker")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    buffer = LogRingBuffer(max_size=10)
    handler = RingBufferQtHandler(buffer=buffer)
    broadcaster = install_log_stream(logger=logger, buffer=buffer, handler=handler)
    handler.setFormatter(formatter)
    received: list[str] = []
    broadcaster.message_emitted.connect(received.append)

    worker = threading.Thread(target=lambda: logger.info("worker payload"), name="log-stream-worker")
    worker.start()
    worker.join(timeout=2)
    assert not worker.is_alive(), "worker logging thread must finish before event processing"

    QCoreApplication.processEvents()

    assert len(received) == 1, f"expected worker log to be delivered through Qt event processing, got {received!r}"
    assert "[INFO] tests.log_stream.worker: worker payload" in received[0], received[0]
    assert received == buffer.snapshot(), "worker-thread signal payload must match the formatted ring-buffer entry"


def test_shutdown_log_stream_removes_handler_and_clears_current_stream() -> None:
    """shutdown_log_stream must remove RingBufferQtHandler from the target logger
    and reset current_log_stream() to None so background threads cannot reach
    a destroyed Qt broadcaster after app.quit()."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _ = pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication

    from easy_tts.log import (
        LogRingBuffer,
        RingBufferQtHandler,
        current_log_stream,
        install_log_stream,
        shutdown_log_stream,
    )

    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app

    logger = logging.getLogger("tests.log_stream.shutdown")
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    buffer = LogRingBuffer(max_size=4)
    handler = RingBufferQtHandler(buffer=buffer)
    broadcaster = install_log_stream(logger=logger, buffer=buffer, handler=handler)

    assert any(isinstance(h, RingBufferQtHandler) for h in logger.handlers), (
        "precondition: install_log_stream must register a RingBufferQtHandler"
    )
    assert current_log_stream() is not None
    assert handler.broadcaster is broadcaster

    shutdown_log_stream(logger)

    assert not any(isinstance(h, RingBufferQtHandler) for h in logger.handlers), (
        f"shutdown_log_stream must remove RingBufferQtHandler from the target logger, "
        f"got handlers={logger.handlers!r}"
    )
    assert handler.broadcaster is None, (
        "shutdown_log_stream must detach the Qt broadcaster from the handler"
    )
    assert current_log_stream() is None, (
        "shutdown_log_stream must clear the cached process-wide LogStream"
    )


def test_shutdown_log_stream_removes_root_logger_handler() -> None:
    """When called without an explicit logger, shutdown_log_stream must still
    scrub RingBufferQtHandler instances from the root logger so the
    main.py / janitor pipeline (which installs onto root) is fully detached."""
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    _ = pytest.importorskip("PySide6")
    from PySide6.QtCore import QCoreApplication

    from easy_tts.log import (
        LogRingBuffer,
        RingBufferQtHandler,
        current_log_stream,
        install_log_stream,
        shutdown_log_stream,
    )

    app = QCoreApplication.instance() or QCoreApplication([])
    _ = app

    root = logging.getLogger()
    pre_existing = list(root.handlers)
    buffer = LogRingBuffer(max_size=4)
    handler = RingBufferQtHandler(buffer=buffer)
    try:
        _ = install_log_stream(buffer=buffer, handler=handler)
        assert handler in root.handlers
        assert current_log_stream() is not None

        shutdown_log_stream()

        assert handler not in root.handlers, (
            "shutdown_log_stream() must remove RingBufferQtHandler from the root logger"
        )
        assert handler.broadcaster is None
        assert current_log_stream() is None
    finally:
        for h in list(root.handlers):
            if isinstance(h, RingBufferQtHandler) and h not in pre_existing:
                root.removeHandler(h)


def test_shutdown_log_stream_is_safe_when_no_pipeline_installed() -> None:
    """shutdown_log_stream must be a safe no-op when no pipeline has been
    installed (e.g. tests that import log_stream but never call install)."""
    from easy_tts.log import current_log_stream, shutdown_log_stream
    import easy_tts.log as log_stream_mod

    log_stream_mod._current_log_stream = None
    shutdown_log_stream()
    assert current_log_stream() is None
