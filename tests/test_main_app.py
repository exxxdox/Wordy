#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Offline baseline tests for main.py app lifecycle without real UI, audio, or network."""

from __future__ import annotations

import queue
import sys
import threading
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

# Ensure repo root is importable
sys.path.insert(0, str(__file__).replace("/tests/test_main_app.py", ""))

from easy_tts.config import AppSettings
from easy_tts.tts.constants import TTS_BACKEND_CARTESIA_BYTES, TTS_BACKEND_CARTESIA_REALTIME
from easy_tts.tts.engine import BackendTTSEngine


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeInputOverlay:
    """Stub InputOverlay that never touches Tk."""

    def __init__(self, **kwargs: Any):
        self._callbacks = kwargs
        self.pre_stop_hook: Callable[[], None] | None = None

    def prepare_ui(self) -> None:
        pass

    def set_pre_stop_hook(self, hook: Callable[[], None]) -> None:
        self.pre_stop_hook = hook

    def run(self) -> None:
        pass

    def stop(self) -> None:
        pass


class FakeTTSEngine(BackendTTSEngine):
    """Stub TTS engine with recordable side effects."""

    def __init__(self, audio_player: Any = None, voice_id: str | None = None, volume: float = 1.0):
        super().__init__(audio_player=audio_player or MagicMock(), voice_id=voice_id, volume=volume)
        self.connect_calls = 0
        self.close_calls = 0
        self.speak_calls: list[str] = []
        self.fetch_voices_result: list[dict] = [{"id": "v1", "name": "Voice1"}]

    def connect(self) -> None:
        self.connect_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def fetch_voices(self) -> list[dict]:
        return self.fetch_voices_result

    def speak(self, text: str) -> bool:
        self.speak_calls.append(text)
        return True


class FakeExecutor:
    """Drop-in replacement for ThreadPoolExecutor that runs submitted callables synchronously.

    Records every submission and shutdown invocation so tests can assert that
    work flows through the executor rather than being executed inline through
    a raw daemon thread.
    """

    instances: list["FakeExecutor"] = []

    def __init__(self) -> None:
        self.submissions: list[tuple[Callable[..., Any], tuple[Any, ...], dict[str, Any]]] = []
        self.shutdown_calls: list[dict[str, Any]] = []
        self._shutdown = False
        FakeExecutor.instances.append(self)

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> MagicMock:
        if self._shutdown:
            raise RuntimeError("cannot schedule new futures after shutdown")
        self.submissions.append((fn, args, kwargs))
        future = MagicMock(name="FakeFuture")
        try:
            result = fn(*args, **kwargs)
            future.result.return_value = result
        except Exception as exc:  # pragma: no cover - defensive, tests assert no raise
            future.exception.return_value = exc
        return future

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self._shutdown = True
        self.shutdown_calls.append({"wait": wait, "cancel_futures": cancel_futures})


class FakeJanitorThread:
    def __init__(self, target: Callable[[], None], name: str, daemon: bool) -> None:
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        self.join_calls: list[float | None] = []
        self._alive = False

    def start(self) -> None:
        self.started = True
        self._alive = True

    def is_alive(self) -> bool:
        return self._alive

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)
        self._alive = False


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_dependencies(monkeypatch):
    """Monkeypatch external dependencies before importing main."""
    monkeypatch.setattr("easy_tts.secret.load_cartesia_api_key", lambda: "fake-api-key")
    # 用 AppSettings 替代已删除的独立 load 函数
    _default_settings = AppSettings()
    _default_settings.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    _default_settings.voice_id = "fake-voice"
    _default_settings.voice_name = "Fake"
    _default_settings.volume = 1.0
    monkeypatch.setattr(AppSettings, "load", lambda **kw: _default_settings)
    # Patch InputOverlay to the fake.
    monkeypatch.setattr("easy_tts.main.InputOverlay", FakeInputOverlay)
    monkeypatch.setattr("easy_tts.main.threading.Thread", FakeJanitorThread)
    # Reset shared executor instance tracking for each test.
    FakeExecutor.instances.clear()
    # Replace executor factory with synchronous fake; tests assert through it.
    monkeypatch.setattr("easy_tts.main.WavTransApp._create_tts_executor", staticmethod(FakeExecutor))


@pytest.fixture
def app(monkeypatch):
    """Return a WavTransApp instance wired with a FakeTTSEngine."""
    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    instance = main_mod.WavTransApp()
    # Reset engine because _create_tts_engine was already called during __init__.
    instance.tts_engine = engine
    try:
        yield instance
    finally:
        if instance._janitor_thread.is_alive():
            instance._janitor_thread.join(timeout=1)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class _FlushableStream:
    def __init__(self, events: list[str], name: str, *, fail: bool = False) -> None:
        self.events = events
        self.name = name
        self.fail = fail

    def flush(self) -> None:
        self.events.append(f"{self.name}.flush")
        if self.fail:
            raise RuntimeError(f"{self.name} flush failed")


class _HardExitCalled(RuntimeError):
    pass


@pytest.mark.parametrize("system_exit_code", [None, 0, False])
def test_run_as_main_hard_exits_after_successful_completion(monkeypatch, system_exit_code):
    """_run_as_main must flush then os._exit(0) after clean SystemExit codes."""
    import easy_tts.main as main_mod

    events: list[str] = []

    def fake_main() -> None:
        events.append("main")
        raise SystemExit(system_exit_code)

    def fake_exit(code: int) -> None:
        events.append(f"exit:{code}")
        raise _HardExitCalled

    monkeypatch.setattr(main_mod, "main", fake_main)
    monkeypatch.setattr(main_mod, "_flush_std_streams_and_logging", lambda: events.append("flush"))
    monkeypatch.setattr(main_mod.os, "_exit", fake_exit)

    with pytest.raises(_HardExitCalled):
        main_mod._run_as_main()

    assert events == ["main", "flush", "exit:0"]


def test_run_as_main_hard_exits_after_normal_main_return(monkeypatch):
    """Calling _run_as_main after main() returns normally must flush then os._exit(0)."""
    import easy_tts.main as main_mod

    events: list[str] = []

    def fake_exit(code: int) -> None:
        events.append(f"exit:{code}")
        raise _HardExitCalled

    monkeypatch.setattr(main_mod, "main", lambda: events.append("main"))
    monkeypatch.setattr(main_mod, "_flush_std_streams_and_logging", lambda: events.append("flush"))
    monkeypatch.setattr(main_mod.os, "_exit", fake_exit)

    with pytest.raises(_HardExitCalled):
        main_mod._run_as_main()

    assert events == ["main", "flush", "exit:0"]


@pytest.mark.parametrize("system_exit_code", [1, True, "bad"])
def test_run_as_main_reraises_non_zero_system_exit_without_hard_exit(monkeypatch, system_exit_code):
    """Non-zero SystemExit values are failure paths and must not call os._exit."""
    import easy_tts.main as main_mod

    hard_exit_calls: list[None] = []

    def fake_main() -> None:
        raise SystemExit(system_exit_code)

    monkeypatch.setattr(main_mod, "main", fake_main)
    monkeypatch.setattr(main_mod, "_hard_exit_success", lambda: hard_exit_calls.append(None))

    with pytest.raises(SystemExit) as exc_info:
        main_mod._run_as_main()

    assert exc_info.value.code == system_exit_code
    assert hard_exit_calls == []


@pytest.mark.parametrize("exception", [RuntimeError("boom"), KeyboardInterrupt()])
def test_run_as_main_reraises_base_exception_without_hard_exit(monkeypatch, exception):
    """Generic exceptions and KeyboardInterrupt must be re-raised without hard exit."""
    import easy_tts.main as main_mod

    events: list[str] = []

    def fake_main() -> None:
        events.append("main")
        raise exception

    monkeypatch.setattr(main_mod, "main", fake_main)
    monkeypatch.setattr(main_mod, "_hard_exit_success", lambda: events.append("hard_exit_success"))

    with pytest.raises(type(exception)):
        main_mod._run_as_main()

    assert events == ["main"]


def test_hard_exit_success_flushes_then_exits(monkeypatch):
    """_hard_exit_success must flush before os._exit(0), then exit."""
    import easy_tts.main as main_mod

    events: list[str] = []

    def fake_exit(code: int) -> None:
        events.append(f"exit:{code}")
        raise _HardExitCalled

    monkeypatch.setattr(main_mod, "_flush_std_streams_and_logging", lambda: events.append("flush"))
    monkeypatch.setattr(main_mod.os, "_exit", fake_exit)

    with pytest.raises(_HardExitCalled):
        main_mod._hard_exit_success()

    assert events == ["flush", "exit:0"]


def test_flush_std_streams_and_logging_is_best_effort(monkeypatch):
    """Flush helper must attempt stdout, stderr, and logging.shutdown even if earlier steps fail."""
    import easy_tts.main as main_mod

    events: list[str] = []

    monkeypatch.setattr(main_mod.sys, "stdout", _FlushableStream(events, "stdout", fail=True))
    monkeypatch.setattr(main_mod.sys, "stderr", _FlushableStream(events, "stderr", fail=True))

    def fake_logging_shutdown() -> None:
        events.append("logging.shutdown")
        raise RuntimeError("logging shutdown failed")

    monkeypatch.setattr(main_mod.logging, "shutdown", fake_logging_shutdown)

    main_mod._flush_std_streams_and_logging()

    assert events == ["stdout.flush", "stderr.flush", "logging.shutdown"]


def test_calling_main_directly_does_not_hard_exit(monkeypatch):
    """Importing and calling main.main() must preserve app behavior without invoking os._exit."""
    import easy_tts.main as main_mod

    events: list[str] = []

    class FakeApp:
        def run(self) -> None:
            events.append("app.run")

    monkeypatch.setattr(main_mod, "configure_logging", lambda: events.append("configure_logging"))
    monkeypatch.setattr(main_mod, "WavTransApp", lambda: FakeApp())
    monkeypatch.setattr(main_mod.os, "_exit", lambda code: events.append(f"exit:{code}"))

    main_mod.main()

    assert events == ["configure_logging", "app.run"]


def test_configure_logging_installs_log_stream_pipeline(monkeypatch):
    """configure_logging must install the in-memory/Qt log stream pipeline for tray UI."""
    import easy_tts.main as main_mod

    install_calls: list[None] = []

    def fake_install_log_stream() -> None:
        install_calls.append(None)

    monkeypatch.setattr(main_mod, "install_log_stream", fake_install_log_stream, raising=False)
    main_mod.configure_logging()

    assert install_calls == [None], (
        "configure_logging must call install_log_stream() so tray logs receive the "
        "RingBufferQtHandler-backed stream pipeline"
    )


def test_run_prepares_overlay_tray_hook_and_disposes_tray_before_overlay_run(app, monkeypatch):
    """run() must prepare overlay UI, wire TrayApp/TrayController, hook tray disposal, then enter overlay.run()."""
    import easy_tts.main as main_mod

    events: list[str] = []

    class RecordingOverlay(FakeInputOverlay):
        def __init__(self, **kwargs: Any) -> None:
            super().__init__(**kwargs)
            events.append("overlay.__init__")

        def prepare_ui(self) -> None:
            events.append("overlay.prepare_ui")

        def set_pre_stop_hook(self, hook: Callable[[], None]) -> None:
            events.append("overlay.set_pre_stop_hook")
            self.pre_stop_hook = hook

        def run(self) -> None:
            events.append("overlay.run")
            assert events.index("overlay.prepare_ui") < events.index("tray.app.__init__")
            assert events.index("tray.app.__init__") < events.index("tray.controller.__init__")
            assert events.index("tray.controller.__init__") < events.index("overlay.set_pre_stop_hook")
            assert events.index("overlay.set_pre_stop_hook") < events.index("overlay.run")
            assert self.pre_stop_hook is not None
            self.pre_stop_hook()
            events.append("overlay.run.after_pre_stop_hook")

    class RecordingTrayApp:
        def __init__(self, overlay: RecordingOverlay) -> None:
            events.append("tray.app.__init__")
            self.overlay = overlay

        def dispose(self) -> None:
            events.append("tray.app.dispose")

    class RecordingTrayController:
        def __init__(self, tray_app: RecordingTrayApp, overlay: RecordingOverlay) -> None:
            events.append("tray.controller.__init__")
            self.tray_app = tray_app
            self.overlay = overlay

    monkeypatch.setattr(main_mod, "InputOverlay", RecordingOverlay)
    assert hasattr(main_mod, "TrayApp"), "main must import TrayApp for tray lifecycle wiring"
    assert hasattr(main_mod, "TrayController"), "main must import TrayController for tray lifecycle wiring"
    monkeypatch.setattr(main_mod, "TrayApp", RecordingTrayApp)
    monkeypatch.setattr(main_mod, "TrayController", RecordingTrayController)

    instance = main_mod.WavTransApp()
    instance.tts_engine = app.tts_engine
    instance._tts_executor = app._tts_executor
    instance._janitor_queue = queue.Queue()

    class FakeJanitorThread:
        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            events.append("janitor.join")

    instance._janitor_thread = FakeJanitorThread()  # type: ignore[assignment]

    instance.run()

    assert events.count("tray.app.dispose") == 1, (
        f"tray.dispose must be registered as overlay pre-stop hook and called once, got {events!r}"
    )
    assert events.index("tray.app.dispose") < events.index("overlay.run.after_pre_stop_hook")
    assert events.index("overlay.run") < events.index("janitor.join")


def test_run_disposes_tray_in_finally_when_overlay_run_raises(app, monkeypatch):
    """run() finally path must dispose the tray even if overlay.run() raises before pre-stop hook fires."""
    import easy_tts.main as main_mod

    events: list[str] = []

    class RaisingOverlay(FakeInputOverlay):
        def prepare_ui(self) -> None:
            events.append("overlay.prepare_ui")

        def set_pre_stop_hook(self, hook: Callable[[], None]) -> None:
            events.append("overlay.set_pre_stop_hook")
            self.pre_stop_hook = hook

        def run(self) -> None:
            events.append("overlay.run")
            raise RuntimeError("overlay boom")

    class RecordingTrayApp:
        def __init__(self, overlay: RaisingOverlay) -> None:
            events.append("tray.app.__init__")
            self.overlay = overlay

        def dispose(self) -> None:
            events.append("tray.app.dispose")

    class RecordingTrayController:
        def __init__(self, tray_app: RecordingTrayApp, overlay: RaisingOverlay) -> None:
            events.append("tray.controller.__init__")
            self.tray_app = tray_app
            self.overlay = overlay

    monkeypatch.setattr(main_mod, "InputOverlay", RaisingOverlay)
    assert hasattr(main_mod, "TrayApp"), "main must import TrayApp for tray lifecycle wiring"
    assert hasattr(main_mod, "TrayController"), "main must import TrayController for tray lifecycle wiring"
    monkeypatch.setattr(main_mod, "TrayApp", RecordingTrayApp)
    monkeypatch.setattr(main_mod, "TrayController", RecordingTrayController)

    instance = main_mod.WavTransApp()
    instance.tts_engine = app.tts_engine
    instance._tts_executor = app._tts_executor
    instance._janitor_queue = queue.Queue()

    class FakeJanitorThread:
        def is_alive(self) -> bool:
            return False

        def join(self, timeout: float | None = None) -> None:
            events.append("janitor.join")

    instance._janitor_thread = FakeJanitorThread()  # type: ignore[assignment]

    with pytest.raises(RuntimeError, match="overlay boom"):
        instance.run()

    assert events.count("tray.app.dispose") == 1, (
        f"run() finally must dispose tray exactly once after overlay.run failure, got {events!r}"
    )
    assert events.index("overlay.run") < events.index("tray.app.dispose")
    assert events.index("tray.app.dispose") < events.index("janitor.join")


def test_app_uses_fake_executor_not_raw_thread(app):
    """WavTransApp must hold a FakeExecutor (i.e. went through factory), not a raw threading object."""
    assert isinstance(app._tts_executor, FakeExecutor)
    assert not isinstance(app._tts_executor, threading.Thread)


def test_on_submit_without_voice_does_not_submit_or_speak(app):
    """Missing voice_id should log error and never queue work on the executor."""
    app.voice_id = None
    app.tts_engine.speak_calls.clear()
    app._tts_executor.submissions.clear()

    app._on_submit("hello")

    assert app.tts_engine.speak_calls == []
    assert app._tts_executor.submissions == []


def test_on_submit_with_voice_queues_through_executor(app):
    """When voice_id is set, _on_submit must submit _generate_and_play through the executor."""
    app.voice_id = "fake-voice"
    app.tts_engine.speak_calls.clear()
    app._tts_executor.submissions.clear()

    app._on_submit("hello world")

    # Exactly one submission, captured engine/backend/text, target is _generate_and_play.
    assert len(app._tts_executor.submissions) == 1
    fn, args, kwargs = app._tts_executor.submissions[0]
    assert fn == app._generate_and_play
    assert args == (app.tts_engine, app.tts_backend, "hello world")
    assert kwargs == {}
    # FakeExecutor runs synchronously, so speak was invoked.
    assert app.tts_engine.speak_calls == ["hello world"]


def test_on_submit_does_not_spawn_raw_daemon_thread(app, monkeypatch):
    """Regression guard: _on_submit must never construct a threading.Thread directly."""
    sentinel = MagicMock(side_effect=AssertionError("threading.Thread must not be used"))
    monkeypatch.setattr("threading.Thread", sentinel)

    app.voice_id = "fake-voice"
    app._on_submit("hi")

    sentinel.assert_not_called()


def test_backend_switch_enqueues_old_worker_without_inline_cleanup(app, monkeypatch):
    """Backend switch must enqueue the old executor+engine for the janitor and NOT call shutdown/close inline."""
    import easy_tts.main as main_mod

    old_engine = app.tts_engine
    old_executor = app._tts_executor
    new_engine = FakeTTSEngine(voice_id="fake-voice")

    inline_calls: list[str] = []

    original_shutdown = old_executor.shutdown

    def tracked_shutdown(*a, **kw):
        inline_calls.append("executor.shutdown")
        return original_shutdown(*a, **kw)

    original_close = old_engine.close

    def tracked_close():
        inline_calls.append("engine.close")
        return original_close()

    old_executor.shutdown = tracked_shutdown  # type: ignore[assignment]
    old_engine.close = tracked_close  # type: ignore[assignment]

    monkeypatch.setattr("easy_tts.main.resolve_tts_backend", lambda name: TTS_BACKEND_CARTESIA_REALTIME)
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: new_engine)

    observed_queue: queue.Queue = queue.Queue()
    assert hasattr(app, "_janitor_queue"), (
        "WavTransApp must expose a _janitor_queue attribute for deferred TTS cleanup"
    )
    app._janitor_queue = observed_queue

    app._on_tts_backend_change(TTS_BACKEND_CARTESIA_REALTIME)

    assert inline_calls == [], (
        f"backend switch must defer cleanup; got inline calls {inline_calls!r}"
    )
    assert old_executor.shutdown_calls == [], (
        f"old executor.shutdown must not be called inline, got {old_executor.shutdown_calls!r}"
    )
    assert old_engine.close_calls == 0, (
        f"old engine.close must not be called inline, got close_calls={old_engine.close_calls}"
    )

    enqueued: list[Any] = []
    while True:
        try:
            enqueued.append(observed_queue.get_nowait())
        except queue.Empty:
            break
    assert len(enqueued) == 1, (
        f"backend switch must enqueue exactly one retired worker, got {enqueued!r}"
    )
    retired = enqueued[0]
    assert isinstance(retired, main_mod._RetiredWorker), (
        f"enqueued item must be a _RetiredWorker, got {type(retired).__name__}"
    )
    assert retired.executor is old_executor
    assert retired.engine is old_engine

    assert app.tts_backend == TTS_BACKEND_CARTESIA_REALTIME
    assert app.tts_engine is new_engine
    assert new_engine.connect_calls == 0
    assert app._tts_executor is not old_executor
    assert isinstance(app._tts_executor, FakeExecutor)


def test_backend_switch_submit_after_switch_uses_new_engine_and_executor(app, monkeypatch):
    """After a backend switch, _on_submit must route work to the NEW engine and the NEW executor."""
    new_engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.resolve_tts_backend", lambda name: TTS_BACKEND_CARTESIA_REALTIME)
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: new_engine)

    app._janitor_queue = queue.Queue()

    app._on_tts_backend_change(TTS_BACKEND_CARTESIA_REALTIME)

    new_executor = app._tts_executor
    assert isinstance(new_executor, FakeExecutor)
    new_executor.submissions.clear()
    new_engine.speak_calls.clear()

    app.voice_id = "fake-voice"
    app._on_submit("after-switch")

    assert len(new_executor.submissions) == 1
    fn, args, kwargs = new_executor.submissions[0]
    assert fn == app._generate_and_play
    assert args == (new_engine, app.tts_backend, "after-switch")
    assert kwargs == {}
    assert new_engine.speak_calls == ["after-switch"]


def test_backend_switch_no_op_when_same_backend(app, monkeypatch):
    """Switching to the same backend should be a no-op and not touch the executor or janitor queue."""
    old_engine = app.tts_engine
    old_executor = app._tts_executor
    monkeypatch.setattr("easy_tts.main.resolve_tts_backend", lambda name: TTS_BACKEND_CARTESIA_BYTES)

    observed_queue: queue.Queue = queue.Queue()
    assert hasattr(app, "_janitor_queue"), (
        "WavTransApp must expose a _janitor_queue attribute for deferred TTS cleanup"
    )
    app._janitor_queue = observed_queue

    app._on_tts_backend_change(TTS_BACKEND_CARTESIA_BYTES)

    assert old_engine.close_calls == 0
    assert app.tts_engine is old_engine
    assert app._tts_executor is old_executor
    assert old_executor.shutdown_calls == []
    assert observed_queue.empty(), (
        "same-backend switch must not enqueue any retired worker"
    )


def test_run_enqueues_current_worker_and_sentinel_then_joins_janitor(app, monkeypatch):
    """run()'s finally must enqueue the current worker + sentinel into the janitor queue and join the janitor thread.

    No inline executor.shutdown / engine.close calls are allowed during run cleanup.
    """
    import easy_tts.main as main_mod

    executor = app._tts_executor
    engine = app.tts_engine

    inline_calls: list[str] = []

    original_shutdown = executor.shutdown

    def tracked_shutdown(*a, **kw):
        inline_calls.append("executor.shutdown")
        return original_shutdown(*a, **kw)

    original_close = engine.close

    def tracked_close():
        inline_calls.append("engine.close")
        return original_close()

    executor.shutdown = tracked_shutdown  # type: ignore[assignment]
    engine.close = tracked_close  # type: ignore[assignment]

    observed_queue: queue.Queue = queue.Queue()
    assert hasattr(app, "_janitor_queue"), (
        "WavTransApp must expose a _janitor_queue attribute for deferred TTS cleanup"
    )
    app._janitor_queue = observed_queue

    join_calls: list[float | None] = []

    class FakeJanitorThread:
        def __init__(self) -> None:
            self._alive = True

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            join_calls.append(timeout)
            self._alive = False

    fake_janitor = FakeJanitorThread()
    assert hasattr(app, "_janitor_thread"), (
        "WavTransApp must expose a _janitor_thread attribute for deferred TTS cleanup"
    )
    app._janitor_thread = fake_janitor  # type: ignore[assignment]

    app.run()

    assert inline_calls == [], (
        f"run cleanup must defer worker shutdown/close to janitor; got inline calls {inline_calls!r}"
    )
    assert engine.connect_calls == 1

    drained: list[Any] = []
    while True:
        try:
            drained.append(observed_queue.get_nowait())
        except queue.Empty:
            break

    assert len(drained) >= 2, (
        f"run cleanup must enqueue at least the current worker and a shutdown sentinel, got {drained!r}"
    )
    retired_items = [item for item in drained if isinstance(item, main_mod._RetiredWorker)]
    assert len(retired_items) == 1, (
        f"run cleanup must enqueue exactly one _RetiredWorker for the current worker, got {drained!r}"
    )
    retired = retired_items[0]
    assert retired.executor is executor
    assert retired.engine is engine

    assert drained[-1] is None, (
        f"final enqueued item must be a None sentinel to stop the janitor loop, got {drained!r}"
    )

    assert join_calls, "run cleanup must join the janitor thread after enqueuing the sentinel"


def test_run_without_cartesia_api_key_does_not_eager_connect(monkeypatch):
    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.secret.load_cartesia_api_key", lambda: None)
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    instance = main_mod.WavTransApp()
    run_calls: list[None] = []
    instance.overlay.run = lambda: run_calls.append(None)
    observed_queue: queue.Queue = queue.Queue()
    instance._janitor_queue = observed_queue

    class FakeJanitorThread:
        def __init__(self) -> None:
            self._alive = True

        def is_alive(self) -> bool:
            return self._alive

        def join(self, timeout: float | None = None) -> None:
            self._alive = False

    instance._janitor_thread = FakeJanitorThread()  # type: ignore[assignment]

    instance.run()

    assert engine.connect_calls == 0
    assert run_calls == [None]


# ---------------------------------------------------------------------------
# RED contract tests for Cartesia API key secret-store wiring (S1/S3).
# ---------------------------------------------------------------------------


CARTESIA_KEY_SENTINEL = "sk_main_NEW_DO_NOT_LEAK"


def test_app_startup_loads_cartesia_api_key_from_secret_store(monkeypatch):
    import easy_tts.main as main_mod

    secret_load_calls: list[None] = []
    engine_calls: list[dict[str, Any]] = []

    def secret_loader() -> str:
        secret_load_calls.append(None)
        return CARTESIA_KEY_SENTINEL

    def factory(*args: Any, **kwargs: Any) -> FakeTTSEngine:
        engine_calls.append({"args": args, "kwargs": kwargs})
        return FakeTTSEngine(voice_id=kwargs.get("voice_id"))

    monkeypatch.setattr("easy_tts.secret.load_cartesia_api_key", secret_loader, raising=False)
    monkeypatch.setattr("easy_tts.main.create_tts_engine", factory)

    instance = main_mod.WavTransApp()

    assert secret_load_calls == [None], (
        "WavTransApp must source the Cartesia API key from secret_store.load_cartesia_api_key()"
    )
    assert engine_calls, "WavTransApp must construct a TTS engine during startup"
    assert instance.cartesia_api_key == CARTESIA_KEY_SENTINEL
    assert engine_calls[0]["kwargs"].get("api_key") == CARTESIA_KEY_SENTINEL


def test_app_startup_does_not_leak_cartesia_api_key_when_secret_loader_raises(monkeypatch):
    import easy_tts.main as main_mod

    def secret_loader() -> str:
        raise RuntimeError("secret-store unavailable")

    monkeypatch.setattr("easy_tts.secret.load_cartesia_api_key", secret_loader, raising=False)
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: FakeTTSEngine(voice_id="fake-voice"))

    with pytest.raises(RuntimeError) as exc_info:
        main_mod.WavTransApp()

    message = str(exc_info.value)
    assert CARTESIA_KEY_SENTINEL not in message


def test_cartesia_api_key_change_rebuilds_tts_engine_without_leaking_key(monkeypatch):
    import easy_tts.main as main_mod

    created_engines: list[FakeTTSEngine] = []
    engine_calls: list[dict[str, Any]] = []

    def factory(*args: Any, **kwargs: Any) -> FakeTTSEngine:
        engine = FakeTTSEngine(voice_id=kwargs.get("voice_id"))
        created_engines.append(engine)
        engine_calls.append({"args": args, "kwargs": kwargs})
        return engine

    monkeypatch.setattr("easy_tts.secret.load_cartesia_api_key", lambda: "initial-secret-key", raising=False)
    monkeypatch.setattr("easy_tts.main.create_tts_engine", factory)

    instance = main_mod.WavTransApp()
    old_engine = instance.tts_engine
    old_executor = instance._tts_executor
    instance._janitor_queue = queue.Queue()

    callback = getattr(instance, "_on_cartesia_api_key_change", None)
    if not callable(callback):
        overlay = getattr(instance, "overlay", None)
        callbacks = getattr(overlay, "_callbacks", {}) if overlay is not None else {}
        callback = callbacks.get("on_cartesia_api_key_change") or callbacks.get("on_api_key_change")
    assert callable(callback), (
        "WavTransApp must expose or wire a settings-apply callback for Cartesia API key changes"
    )

    callback(CARTESIA_KEY_SENTINEL)

    assert instance.cartesia_api_key == CARTESIA_KEY_SENTINEL
    assert instance.tts_engine is not old_engine
    assert instance._tts_executor is not old_executor
    assert engine_calls[-1]["kwargs"].get("api_key") == CARTESIA_KEY_SENTINEL

    enqueued: list[Any] = []
    while True:
        try:
            enqueued.append(instance._janitor_queue.get_nowait())
        except queue.Empty:
            break
    assert any(getattr(item, "engine", None) is old_engine for item in enqueued), (
        "API key changes must retire the old TTS engine through the janitor queue"
    )
    assert CARTESIA_KEY_SENTINEL not in repr(enqueued)


def test_on_cartesia_api_key_change_engine_build_failure_does_not_corrupt_state(monkeypatch):
    import easy_tts.main as main_mod

    initial_engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.secret.load_cartesia_api_key", lambda: "sk_OLD_STABLE", raising=False)
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: initial_engine)

    instance = main_mod.WavTransApp()
    old_engine = instance.tts_engine
    old_executor = instance._tts_executor
    observed_queue: queue.Queue = queue.Queue()
    instance._janitor_queue = observed_queue

    def failing_factory(*args: Any, **kwargs: Any) -> FakeTTSEngine:
        raise RuntimeError("build failed")

    monkeypatch.setattr("easy_tts.main.create_tts_engine", failing_factory)

    with pytest.raises(RuntimeError, match="build failed"):
        instance._on_cartesia_api_key_change("sk_NEW_FAILURE")

    assert instance.cartesia_api_key == "sk_OLD_STABLE"
    assert instance.tts_engine is old_engine
    assert instance._tts_executor is old_executor

    enqueued: list[Any] = []
    while True:
        try:
            enqueued.append(observed_queue.get_nowait())
        except queue.Empty:
            break
    assert not any(isinstance(item, main_mod._RetiredWorker) for item in enqueued), (
        f"failed API key rebuild must not enqueue retired workers, got {enqueued!r}"
    )


# ---------------------------------------------------------------------------
# RED contract tests for audio output device wiring (S3).
# ---------------------------------------------------------------------------


class _RecordingInputOverlay(FakeInputOverlay):
    """FakeInputOverlay variant that exposes captured constructor kwargs."""

    last_kwargs: dict[str, Any] = {}

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        type(self).last_kwargs = dict(kwargs)


def _patch_audio_player_factory(monkeypatch) -> list[dict[str, Any]]:
    """Patch main.AudioPlayer with a recording factory; return calls list."""
    calls: list[dict[str, Any]] = []

    def factory(*args: Any, **kwargs: Any) -> MagicMock:
        calls.append({"args": args, "kwargs": kwargs})
        player = MagicMock(name="AudioPlayer")
        player.set_output_device_name_calls = []

        def set_output_device_name(name: str | None) -> None:
            player.set_output_device_name_calls.append(name)
            player.output_device_name = name

        player.set_output_device_name.side_effect = set_output_device_name
        player.output_device = kwargs.get("output_device")
        player.output_device_name = kwargs.get("output_device_name")
        return player

    monkeypatch.setattr("easy_tts.main.AudioPlayer", factory)
    return calls


def test_app_constructs_audio_player_with_loaded_output_device_name(monkeypatch):
    """WavTransApp must call AudioPlayer with output_device_name from AppSettings."""
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    s.audio_output_device_name = "VB-Audio Virtual Cable"
    monkeypatch.setattr(AppSettings, "load", lambda **kw: s)
    audio_player_calls = _patch_audio_player_factory(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    _ = main_mod.WavTransApp()

    assert audio_player_calls, "AudioPlayer must be constructed exactly once at startup"
    init_kwargs = audio_player_calls[0]["kwargs"]
    assert init_kwargs.get("output_device_name") == "VB-Audio Virtual Cable", (
        f"AudioPlayer must receive output_device_name from load helper, got {init_kwargs!r}"
    )


def test_app_constructs_audio_player_with_none_when_no_stored_device(monkeypatch):
    """When AppSettings.audio_output_device_name is None, AudioPlayer must receive None."""
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    s.audio_output_device_name = None
    monkeypatch.setattr(AppSettings, "load", lambda **kw: s)
    audio_player_calls = _patch_audio_player_factory(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    _ = main_mod.WavTransApp()

    assert audio_player_calls
    init_kwargs = audio_player_calls[0]["kwargs"]
    assert "output_device_name" in init_kwargs, (
        f"AudioPlayer must receive output_device_name kwarg even when None, got {init_kwargs!r}"
    )
    assert init_kwargs["output_device_name"] is None


def test_on_audio_output_change_updates_player_device_name(monkeypatch):
    """_on_audio_output_change(name) must propagate the name to the audio player."""
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    s.audio_output_device_name = None
    monkeypatch.setattr(AppSettings, "load", lambda **kw: s)
    _ = _patch_audio_player_factory(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    instance = main_mod.WavTransApp()

    handler = getattr(instance, "_on_audio_output_change", None)
    assert callable(handler), "WavTransApp must expose _on_audio_output_change callable"

    handler("VB-Audio Virtual Cable")

    player = instance.player
    assert getattr(player, "output_device_name", None) == "VB-Audio Virtual Cable", (
        f"player.output_device_name must update to selected name, got {getattr(player, 'output_device_name', None)!r}"
    )


def test_on_audio_output_change_to_none_clears_player_device_name(monkeypatch):
    """_on_audio_output_change(None) must clear the player's output device name."""
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    s.audio_output_device_name = "Initial"
    monkeypatch.setattr(AppSettings, "load", lambda **kw: s)
    _ = _patch_audio_player_factory(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    instance = main_mod.WavTransApp()
    handler = getattr(instance, "_on_audio_output_change", None)
    assert callable(handler), "WavTransApp must expose _on_audio_output_change callable"

    handler(None)

    player = instance.player
    assert getattr(player, "output_device_name", "MISSING") is None, (
        f"player.output_device_name must be None after clear, got {getattr(player, 'output_device_name', 'MISSING')!r}"
    )


def test_app_wires_on_audio_output_change_callback_to_overlay(monkeypatch):
    """WavTransApp must pass _on_audio_output_change to InputOverlay constructor."""
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    s.audio_output_device_name = None
    monkeypatch.setattr(AppSettings, "load", lambda **kw: s)
    _ = _patch_audio_player_factory(monkeypatch)
    monkeypatch.setattr("easy_tts.main.InputOverlay", _RecordingInputOverlay)
    _RecordingInputOverlay.last_kwargs = {}

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    instance = main_mod.WavTransApp()

    overlay_kwargs = _RecordingInputOverlay.last_kwargs
    assert "on_audio_output_change" in overlay_kwargs, (
        f"InputOverlay must receive on_audio_output_change kwarg, got {overlay_kwargs!r}"
    )
    assert overlay_kwargs["on_audio_output_change"] == instance._on_audio_output_change, (
        "on_audio_output_change kwarg must be bound to WavTransApp._on_audio_output_change"
    )
    assert "audio_player" in overlay_kwargs, (
        f"InputOverlay must receive audio_player kwarg, got {overlay_kwargs!r}"
    )
    assert overlay_kwargs["audio_player"] is instance.player, (
        "audio_player kwarg must be the WavTransApp.player instance"
    )


class _FakeAudioRouter:
    instances: list["_FakeAudioRouter"] = []

    def __init__(
        self,
        virtual_output: str | None = None,
    ) -> None:
        self.virtual_output = virtual_output
        self.started = False
        self.stopped = False
        self.set_virtual_output_calls: list[str | None] = []
        type(self).instances.append(self)

    def start(self, *, mic_device: str | None = None) -> bool:  # noqa: ARG002
        self.started = True
        return True

    def stop(self) -> None:
        self.started = False
        self.stopped = True

    def is_running(self) -> bool:
        return self.started

    def get_output_device(self) -> dict | None:
        return {"name": "CABLE Input", "host_api_name": "Windows WASAPI"}

    def get_stats(self):
        # 测试替身显式暴露麦克风侦听状态，匹配 AudioRouter 的运行时契约。
        from easy_tts.audio.router import RouterStats

        return RouterStats(is_running=self.started, listen_configured=False)

    def set_mic_device(self, device_name: str | None) -> None:  # noqa: ARG002
        pass

    def set_virtual_output(self, device_name: str | None) -> bool:
        self.set_virtual_output_calls.append(device_name)
        self.virtual_output = device_name
        return True


def _patch_audio_router_for_runtime_change(monkeypatch) -> type[_FakeAudioRouter]:
    _FakeAudioRouter.instances.clear()
    _patch_audio_player_factory(monkeypatch)
    monkeypatch.setattr("easy_tts.main.AudioRouter", _FakeAudioRouter)
    monkeypatch.setattr("easy_tts.main.VBCableDriverManager.is_installed", lambda: True)
    # AppSettings.load() returns defaults for audio routing (all disabled/None)
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    monkeypatch.setattr(AppSettings, "load", lambda **kw: s)
    return _FakeAudioRouter


def test_enabling_audio_route_runtime_switches_player_output(monkeypatch):
    """Runtime audio-route enable must switch player output to CABLE Input."""
    router_cls = _patch_audio_router_for_runtime_change(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)
    app = main_mod.WavTransApp()

    app._on_audio_route_change(
        {
            "audio_routing_enabled": True,
            "virtual_output_device": "Cable",
        }
    )

    assert router_cls.instances
    router = router_cls.instances[-1]
    assert router.started is True
    app.player.set_output_device.assert_called_once()


def test_audio_route_keeps_cable_output_when_local_output_changes(monkeypatch):
    """路由运行时本地输出变更只能更新恢复目标，不能覆盖 CABLE Input。"""
    _patch_audio_router_for_runtime_change(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)
    app = main_mod.WavTransApp()
    app._on_audio_route_change({"audio_routing_enabled": True})
    app.player.set_output_device.reset_mock()

    local_output = {"name": "Speakers", "host_api_name": "Windows WASAPI"}
    app._on_audio_output_change(local_output)

    app.player.set_output_device.assert_not_called()
    assert app._saved_output_device == local_output
    assert app._saved_output_device_name == "Speakers"

    app._on_audio_route_change({"audio_routing_enabled": False})

    app.player.set_output_device.assert_called_once_with(local_output)


def test_disabling_audio_route_runtime_stops_router(monkeypatch):
    """Disabling audio routing must stop router and reset output device."""
    router_cls = _patch_audio_router_for_runtime_change(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)
    app = main_mod.WavTransApp()
    app._on_audio_route_change({"audio_routing_enabled": True})
    router = router_cls.instances[-1]

    app._on_audio_route_change({"audio_routing_enabled": False})

    assert router.stopped is True
    assert app._router is None


def test_audio_route_runtime_update_can_clear_optional_devices(monkeypatch):
    """Explicit None updates from settings must clear virtual device."""
    router_cls = _patch_audio_router_for_runtime_change(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)
    app = main_mod.WavTransApp()
    app._on_audio_route_change(
        {
            "audio_routing_enabled": True,
            "virtual_output_device": "Cable",
        }
    )
    router = router_cls.instances[-1]

    app._on_audio_route_change(
        {
            "virtual_output_device": None,
        }
    )

    assert router.set_virtual_output_calls[-1] is None


# ---------------------------------------------------------------------------
# RED contract tests for STRUCTURED audio output device identity (S3 follow-up).
#
# New requirement: WavTransApp consumes ``load_audio_output_device()`` (structured
# dict ``{"name": ..., "host_api_name": ...}`` or ``None``) and propagates the
# structured value to AudioPlayer construction and to ``player.set_output_device``
# on change. Legacy ``load_audio_output_device_name`` / ``set_output_device_name``
# remain available but are no longer the wiring surface.
# ---------------------------------------------------------------------------


def _patch_structured_audio_player_factory(monkeypatch) -> list[dict[str, Any]]:
    """Patch main.AudioPlayer with a factory that records all kwargs and tracks set_output_device."""
    calls: list[dict[str, Any]] = []

    def factory(*args: Any, **kwargs: Any) -> MagicMock:
        calls.append({"args": args, "kwargs": kwargs})
        player = MagicMock(name="AudioPlayer")
        player.set_output_device_calls = []
        player.set_output_device_name_calls = []

        def set_output_device(device: Any) -> None:
            player.set_output_device_calls.append(device)
            player.output_device = device

        def set_output_device_name(name: str | None) -> None:
            player.set_output_device_name_calls.append(name)
            player.output_device_name = name

        player.set_output_device.side_effect = set_output_device
        player.set_output_device_name.side_effect = set_output_device_name
        player.output_device = kwargs.get("output_device")
        player.output_device_name = kwargs.get("output_device_name")
        return player

    monkeypatch.setattr("easy_tts.main.AudioPlayer", factory)
    return calls


def test_app_constructs_audio_player_with_structured_load_audio_output_device(monkeypatch):
    """WavTransApp must construct AudioPlayer using AppSettings.audio_output_device (structured dict).

    Contract:
      * AppSettings.load() is called and the settings instance provides audio_output_device.
      * AudioPlayer receives the structured value via an ``output_device`` kwarg
        (not the legacy ``output_device_name`` flattened to a string).
    """
    structured = {"name": "Speakers (Realtek)", "host_api_name": "WASAPI"}
    load_calls: list[None] = []
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    s.audio_output_device = dict(structured)
    s.audio_output_device_name = None

    def fake_load(**kw: Any) -> AppSettings:
        load_calls.append(None)
        return s

    monkeypatch.setattr(AppSettings, "load", fake_load)

    audio_player_calls = _patch_structured_audio_player_factory(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    _ = main_mod.WavTransApp()

    assert load_calls, (
        "WavTransApp must call load_audio_output_device() during construction"
    )
    assert audio_player_calls, "AudioPlayer must be constructed exactly once at startup"
    init_kwargs = audio_player_calls[0]["kwargs"]
    assert "output_device" in init_kwargs, (
        f"AudioPlayer must receive the structured value via 'output_device' kwarg, "
        f"got kwargs={init_kwargs!r}"
    )
    received = init_kwargs["output_device"]
    assert isinstance(received, dict), (
        f"AudioPlayer.output_device must be a structured dict, got {received!r}"
    )
    assert received == {"name": "Speakers (Realtek)", "host_api_name": "WASAPI"}, (
        f"AudioPlayer.output_device must equal load_audio_output_device() result, got {received!r}"
    )


def test_app_constructs_audio_player_with_none_when_load_returns_none(monkeypatch):
    """When AppSettings.audio_output_device is None, AudioPlayer's output_device must be None."""
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    s.audio_output_device = None
    s.audio_output_device_name = None
    monkeypatch.setattr(AppSettings, "load", lambda **kw: s)

    audio_player_calls = _patch_structured_audio_player_factory(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    _ = main_mod.WavTransApp()

    assert audio_player_calls
    init_kwargs = audio_player_calls[0]["kwargs"]
    assert "output_device" in init_kwargs, (
        f"AudioPlayer must receive 'output_device' kwarg even when None, got {init_kwargs!r}"
    )
    assert init_kwargs["output_device"] is None, (
        f"AudioPlayer 'output_device' must be None when load returns None, got {init_kwargs!r}"
    )


def test_on_audio_output_change_with_structured_device_calls_set_output_device(monkeypatch):
    """_on_audio_output_change(structured_dict) must call player.set_output_device(structured_dict).

    Contract: the handler propagates the *structured* identity to the player via
    ``set_output_device`` (not the legacy ``set_output_device_name`` with just the
    bare ``name`` string).
    """
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    s.audio_output_device = None
    s.audio_output_device_name = None
    monkeypatch.setattr(AppSettings, "load", lambda **kw: s)
    _ = _patch_structured_audio_player_factory(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    instance = main_mod.WavTransApp()
    handler = getattr(instance, "_on_audio_output_change", None)
    assert callable(handler), "WavTransApp must expose _on_audio_output_change callable"

    structured = {"name": "VB-Audio Virtual Cable", "host_api_name": "WASAPI"}
    handler(structured)

    player = instance.player
    set_calls = getattr(player, "set_output_device_calls", None)
    assert set_calls, (
        f"player.set_output_device must be invoked with the structured device, "
        f"got set_output_device_calls={set_calls!r}, "
        f"set_output_device_name_calls={getattr(player, 'set_output_device_name_calls', None)!r}"
    )
    assert set_calls[-1] == structured, (
        f"player.set_output_device must receive the exact structured dict, got {set_calls[-1]!r}"
    )
    assert isinstance(set_calls[-1], dict), (
        f"player.set_output_device argument must be a dict, got {type(set_calls[-1]).__name__}"
    )


def test_on_audio_output_change_with_none_clears_via_set_output_device(monkeypatch):
    """_on_audio_output_change(None) must call player.set_output_device(None)."""
    initial_structured = {"name": "Initial Device", "host_api_name": "MME"}
    s = AppSettings()
    s.tts_backend = TTS_BACKEND_CARTESIA_BYTES
    s.voice_id = "fake-voice"
    s.voice_name = "Fake"
    s.volume = 1.0
    s.audio_output_device = dict(initial_structured)
    s.audio_output_device_name = "Initial Device"
    monkeypatch.setattr(AppSettings, "load", lambda **kw: s)
    _ = _patch_structured_audio_player_factory(monkeypatch)

    import easy_tts.main as main_mod

    engine = FakeTTSEngine(voice_id="fake-voice")
    monkeypatch.setattr("easy_tts.main.create_tts_engine", lambda *a, **kw: engine)

    instance = main_mod.WavTransApp()
    handler = getattr(instance, "_on_audio_output_change", None)
    assert callable(handler), "WavTransApp must expose _on_audio_output_change callable"

    handler(None)

    player = instance.player
    set_calls = getattr(player, "set_output_device_calls", None)
    assert set_calls, (
        f"player.set_output_device must be invoked when clearing, "
        f"got set_output_device_calls={set_calls!r}"
    )
    assert set_calls[-1] is None, (
        f"player.set_output_device must receive None to clear selection, got {set_calls[-1]!r}"
    )


# ---------------------------------------------------------------------------
# RED contract tests for the deferred janitor loop (TTS lifecycle cleanup).
# ---------------------------------------------------------------------------


class _FakeJanitorEngine:
    """Minimal stand-in for a TTS engine used by janitor loop tests."""

    def __init__(self, name: str, log: list[str], raise_on_close: bool = False) -> None:
        self.name = name
        self._log = log
        self._raise_on_close = raise_on_close
        self.close_calls = 0

    def close(self) -> None:
        self.close_calls += 1
        self._log.append(f"{self.name}.engine.close")
        if self._raise_on_close:
            raise RuntimeError(f"{self.name} engine close boom")


class _FakeJanitorExecutor:
    """Minimal stand-in for an executor used by janitor loop tests."""

    def __init__(self, name: str, log: list[str], raise_on_shutdown: bool = False) -> None:
        self.name = name
        self._log = log
        self._raise_on_shutdown = raise_on_shutdown
        self.shutdown_calls: list[dict[str, Any]] = []

    def shutdown(self, wait: bool = True, *, cancel_futures: bool = False) -> None:
        self.shutdown_calls.append({"wait": wait, "cancel_futures": cancel_futures})
        self._log.append(f"{self.name}.executor.shutdown")
        if self._raise_on_shutdown:
            raise RuntimeError(f"{self.name} executor shutdown boom")


def test_janitor_loop_drains_workers_in_shutdown_then_close_order():
    """_janitor_loop_inner must drain queued workers and call shutdown BEFORE close for each."""
    import easy_tts.main as main_mod

    log: list[str] = []
    q: queue.Queue = queue.Queue()

    worker_a = main_mod._RetiredWorker(
        executor=_FakeJanitorExecutor("A", log),
        engine=_FakeJanitorEngine("A", log),
    )
    worker_b = main_mod._RetiredWorker(
        executor=_FakeJanitorExecutor("B", log),
        engine=_FakeJanitorEngine("B", log),
    )

    q.put(worker_a)
    q.put(worker_b)
    q.put(None)

    main_mod._janitor_loop_inner(q)

    assert log == [
        "A.executor.shutdown",
        "A.engine.close",
        "B.executor.shutdown",
        "B.engine.close",
    ], f"janitor must process workers FIFO and shutdown-before-close, got {log!r}"

    assert worker_a.executor.shutdown_calls and worker_a.executor.shutdown_calls[0]["wait"] is True
    assert worker_b.executor.shutdown_calls and worker_b.executor.shutdown_calls[0]["wait"] is True
    assert worker_a.engine.close_calls == 1
    assert worker_b.engine.close_calls == 1


def test_janitor_loop_continues_after_executor_shutdown_exception():
    """If executor.shutdown raises, the janitor must still call engine.close AND process subsequent workers."""
    import easy_tts.main as main_mod

    log: list[str] = []
    q: queue.Queue = queue.Queue()

    bad_executor = _FakeJanitorExecutor("bad", log, raise_on_shutdown=True)
    bad_engine = _FakeJanitorEngine("bad", log)
    good_executor = _FakeJanitorExecutor("good", log)
    good_engine = _FakeJanitorEngine("good", log)

    q.put(main_mod._RetiredWorker(executor=bad_executor, engine=bad_engine))
    q.put(main_mod._RetiredWorker(executor=good_executor, engine=good_engine))
    q.put(None)

    main_mod._janitor_loop_inner(q)

    assert "bad.executor.shutdown" in log
    assert "bad.engine.close" in log, (
        f"engine.close must run even after executor.shutdown raised, got {log!r}"
    )
    assert "good.executor.shutdown" in log
    assert "good.engine.close" in log, (
        f"janitor must keep processing further workers after an exception, got {log!r}"
    )
    assert bad_engine.close_calls == 1
    assert good_engine.close_calls == 1
    assert log.index("bad.executor.shutdown") < log.index("bad.engine.close")
    assert log.index("bad.engine.close") < log.index("good.executor.shutdown")


def test_janitor_loop_continues_after_engine_close_exception():
    """If engine.close raises, the janitor must still process subsequent workers."""
    import easy_tts.main as main_mod

    log: list[str] = []
    q: queue.Queue = queue.Queue()

    bad_executor = _FakeJanitorExecutor("bad", log)
    bad_engine = _FakeJanitorEngine("bad", log, raise_on_close=True)
    good_executor = _FakeJanitorExecutor("good", log)
    good_engine = _FakeJanitorEngine("good", log)

    q.put(main_mod._RetiredWorker(executor=bad_executor, engine=bad_engine))
    q.put(main_mod._RetiredWorker(executor=good_executor, engine=good_engine))
    q.put(None)

    main_mod._janitor_loop_inner(q)

    assert log == [
        "bad.executor.shutdown",
        "bad.engine.close",
        "good.executor.shutdown",
        "good.engine.close",
    ], f"janitor must continue past engine.close exceptions in FIFO order, got {log!r}"
    assert bad_engine.close_calls == 1
    assert good_engine.close_calls == 1


def test_janitor_loop_stops_on_sentinel_without_processing_later_items():
    """A None sentinel must terminate the loop; any items enqueued after it must be ignored."""
    import easy_tts.main as main_mod

    log: list[str] = []
    q: queue.Queue = queue.Queue()

    early_worker = main_mod._RetiredWorker(
        executor=_FakeJanitorExecutor("early", log),
        engine=_FakeJanitorEngine("early", log),
    )
    late_worker = main_mod._RetiredWorker(
        executor=_FakeJanitorExecutor("late", log),
        engine=_FakeJanitorEngine("late", log),
    )

    q.put(early_worker)
    q.put(None)
    q.put(late_worker)

    main_mod._janitor_loop_inner(q)

    assert log == [
        "early.executor.shutdown",
        "early.engine.close",
    ], f"janitor must stop at the None sentinel and ignore later items, got {log!r}"
    assert late_worker.executor.shutdown_calls == []
    assert late_worker.engine.close_calls == 0


def test_retired_worker_holds_executor_and_engine_references():
    """_RetiredWorker must expose .executor and .engine attributes matching constructor args."""
    import easy_tts.main as main_mod

    executor = _FakeJanitorExecutor("x", [])
    engine = _FakeJanitorEngine("x", [])

    retired = main_mod._RetiredWorker(executor=executor, engine=engine)

    assert retired.executor is executor
    assert retired.engine is engine



def test_on_submit_rejects_blank_text(app, caplog):
    """RED characterization: _on_submit must NOT submit blank/whitespace-only text.

    Production currently lacks this guard, so this test is intentionally RED.
    """
    import logging
    app.voice_id = "fake-voice"
    app.tts_engine.speak_calls.clear()
    app._tts_executor.submissions.clear()

    with caplog.at_level(logging.WARNING):
        app._on_submit("")

    assert app._tts_executor.submissions == [], (
        f"_on_submit(\"\") must not submit work, got {app._tts_executor.submissions!r}"
    )
    assert app.tts_engine.speak_calls == []


def test_on_submit_rejects_whitespace_only_text(app, caplog):
    """RED characterization: whitespace-only text must be rejected too."""
    import logging
    app.voice_id = "fake-voice"
    app.tts_engine.speak_calls.clear()
    app._tts_executor.submissions.clear()

    with caplog.at_level(logging.WARNING):
        app._on_submit("   \t\n")

    assert app._tts_executor.submissions == []
    assert app.tts_engine.speak_calls == []


def test_backend_switch_engine_build_failure_does_not_double_retire(app, monkeypatch):
    """RED characterization: when _create_tts_worker raises during backend switch,
    the OLD worker must NOT be enqueued for retirement, because production still
    holds it as the active engine (self.tts_engine / self._tts_executor).

    Compare _on_cartesia_api_key_change which builds the new worker FIRST and
    only retires on success. _on_tts_backend_change retires UNCONDITIONALLY
    before building, so a build failure leaves the active worker queued for
    janitor close while it is still being used.
    """
    import queue as _queue
    old_engine = app.tts_engine
    old_executor = app._tts_executor

    observed_queue: _queue.Queue = _queue.Queue()
    app._janitor_queue = observed_queue

    def failing_create_worker():
        raise RuntimeError("worker build failed")

    monkeypatch.setattr("easy_tts.main.resolve_tts_backend", lambda name: TTS_BACKEND_CARTESIA_REALTIME)
    monkeypatch.setattr(app, "_create_tts_worker", failing_create_worker)

    with pytest.raises(RuntimeError, match="worker build failed"):
        app._on_tts_backend_change(TTS_BACKEND_CARTESIA_REALTIME)

    drained: list[Any] = []
    while True:
        try:
            drained.append(observed_queue.get_nowait())
        except _queue.Empty:
            break

    assert app.tts_engine is old_engine
    assert app._tts_executor is old_executor

    leaked = [
        item for item in drained
        if (hasattr(item, "engine") and item.engine is old_engine)
        or (hasattr(item, "executor") and item.executor is old_executor)
    ]
    assert leaked == [], (
        f"backend-switch build failure must not enqueue the still-active worker; "
        f"got {leaked!r}"
    )
