"""TTS Manager 生命周期测试。"""

from unittest.mock import MagicMock, patch

import pytest

from wordy.tts.engine import BackendTTSEngine
from wordy.tts_manager import TTSManager, _TTSWorker, _janitor_loop_inner
from concurrent.futures import ThreadPoolExecutor
import threading
from queue import SimpleQueue
from typing import Any, cast
from pathlib import Path


def test_audio_file_waits_for_worker_handoff_without_voice(manager, mock_player, monkeypatch, tmp_path):
    from contextlib import contextmanager
    ready = threading.Event()
    manager._worker_ready = ready
    manager._settings.volcengine_voice_id = None
    @contextmanager
    def decoded(path, cancel_event):
        yield path
    monkeypatch.setattr("wordy.tts_manager.decoded_audio_clip", decoded)
    file = tmp_path / "clip.wav"
    manager.play_audio_file(file)
    operation, *args = manager.executor.submit.call_args.args
    thread = threading.Thread(target=operation, args=args)
    thread.start()
    try:
        mock_player.play_wav.assert_not_called()
    finally:
        ready.set()
        thread.join(2)
    assert not thread.is_alive()
    # 取消句柄从全局停止标志换成单次播放的句柄，退出时另由 shutdown 一并触发。
    cancel_event = mock_player.play_wav.call_args.kwargs["cancel_event"]
    assert cancel_event is not manager._audio_files_stopped
    assert isinstance(cancel_event, threading.Event)
    manager.shutdown()


def test_audio_file_reports_failed_output(manager, mock_player, monkeypatch):
    from contextlib import contextmanager
    @contextmanager
    def decoded(path, cancel_event):
        yield path
    monkeypatch.setattr("wordy.tts_manager.decoded_audio_clip", decoded)
    mock_player.play_wav.return_value = False
    error = MagicMock()
    manager.play_audio_file(Path("clip.wav"), error)
    operation, *args = manager.executor.submit.call_args.args
    operation(*args)
    error.assert_called_once()
    manager.shutdown()


def test_stop_audio_file_interrupts_current_clip(manager, mock_player):
    """停止试听只打断这一次播放，不能连累后续播放（那是 shutdown 的事）。"""
    manager.play_audio_file(Path("clip.wav"))
    operation, *args = manager.executor.submit.call_args.args

    manager.stop_audio_file()
    operation(*args)

    mock_player.play_wav.assert_not_called()

    # 只打断这一次：下一次播放拿到的是全新的、未被触发的句柄。
    manager.play_audio_file(Path("next.wav"))
    _operation, *next_args = manager.executor.submit.call_args.args
    assert next_args[-2] is not args[-2]
    assert next_args[-2].is_set() is False
    manager.shutdown()


def test_audio_file_reports_finish_on_success_and_failure(manager, mock_player, mock_engine):
    """播完和播放失败都要回调，否则设置页的「停止」按钮会一直挂着。"""
    finished = MagicMock()
    manager.play_audio_file(Path("clip.wav"), None, finished)
    operation, *args = manager.executor.submit.call_args.args
    operation(*args)
    finished.assert_called_once_with()

    manager.play_audio_file(Path("missing.wav"), None, finished)
    operation, *args = manager.executor.submit.call_args.args
    mock_player.play_wav.return_value = False
    operation(*args)
    assert finished.call_count == 2


def test_shutdown_interrupts_playing_clip(manager, mock_player):
    """退出时全局标志已不再直接传给播放层，shutdown 必须显式打断当前片段。"""
    manager.play_audio_file(Path("clip.wav"))
    operation, *args = manager.executor.submit.call_args.args

    manager.shutdown()
    operation(*args)

    mock_player.play_wav.assert_not_called()


def test_shutdown_discards_queued_audio_files(manager, mock_player):
    manager.play_audio_file(Path("clip.wav"))
    operation, *args = manager.executor.submit.call_args.args
    manager.shutdown()
    operation(*args)
    mock_player.play_wav.assert_not_called()


def test_audio_file_closes_realtime_engine_before_file_stream(manager, mock_player, mock_engine, monkeypatch):
    from contextlib import contextmanager
    order = []
    mock_engine.close.side_effect = lambda: order.append("close")
    mock_player.play_wav.side_effect = lambda *_args, **_kwargs: order.append("play") or True
    @contextmanager
    def decoded(path, cancel_event):
        yield path
    monkeypatch.setattr("wordy.tts_manager.decoded_audio_clip", decoded)
    manager.play_audio_file(Path("clip.wav"))
    operation, *args = manager.executor.submit.call_args.args
    operation(*args)
    assert order == ["close", "play"]
    mock_engine.reset_audio_output.assert_not_called()
    manager.shutdown()


def test_queued_text_keeps_engine_selected_at_submission():
    manager = TTSManager.__new__(TTSManager)
    manager._settings = MagicMock(volcengine_voice_id="speaker")
    manager.tts_api_provider = "Volcengine"
    manager.tts_backend = "Volcengine Streaming"
    manager._worker_ready = None
    manager._executor = MagicMock()
    old, new = MagicMock(), MagicMock()
    manager.engine = old
    manager.speak("queued text")
    operation, *args = manager._executor.submit.call_args.args
    kwargs = manager._executor.submit.call_args.kwargs
    manager.engine = new
    operation(*args, **kwargs)
    old.speak.assert_called_once_with("queued text")
    new.speak.assert_not_called()


def test_new_worker_waits_for_retired_engine_close(manager, mock_engine, monkeypatch):
    closing, release, playback = threading.Event(), threading.Event(), threading.Event()
    def close_old():
        closing.set()
        assert release.wait(2)
    mock_engine.close.side_effect = close_old
    new_engine, executor = MagicMock(), MagicMock()
    new_engine.speak.side_effect = lambda _text: playback.set()
    monkeypatch.setattr(manager, "_create_worker", lambda: _TTSWorker(executor, new_engine))
    manager._rebuild_worker("test switch", lambda: None)
    assert closing.wait(2)
    manager.speak("new text")
    operation, *args = executor.submit.call_args.args
    kwargs = executor.submit.call_args.kwargs
    started = threading.Event()
    def run():
        started.set()
        operation(*args, **kwargs)
    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert started.wait(2)
        assert not playback.wait(0.1), "new playback must wait until old shared stream is closed"
    finally:
        release.set()
        thread.join(2)
        manager.shutdown()
    assert not thread.is_alive()
    assert playback.is_set()


def test_prewarm_runs_on_worker_executor(manager, mock_engine):
    # 预热也必须入队，退役时 executor.shutdown 才能等待它结束。
    manager.connect_async()
    operation, engine, ready = manager.executor.submit.call_args.args
    mock_engine.connect.assert_not_called()
    operation(engine, ready)
    mock_engine.connect.assert_called_once()


@pytest.fixture
def mock_engine() -> MagicMock:
    e = MagicMock()
    e.fetch_voices.return_value = []
    e.speak.return_value = True
    return e


@pytest.fixture
def mock_player() -> MagicMock:
    return MagicMock()


@pytest.fixture
def mock_settings() -> MagicMock:
    s = MagicMock()
    s.volume = 1.0
    s.cartesia_voice_id = None
    s.cartesia_voice_name = None
    s.volcengine_voice_id = "test-speaker"
    s.volcengine_voice_name = "Test Speaker"
    s.volcengine_voice_list = []
    return s


@pytest.fixture
def manager(mock_settings, mock_player, mock_engine) -> TTSManager:
    with (
        patch("wordy.tts_manager.create_tts_engine", return_value=mock_engine),
        patch("wordy.tts_manager.ThreadPoolExecutor") as mock_tpe_cls,
    ):
        mock_tpe = MagicMock()
        mock_tpe_cls.return_value = mock_tpe
        mgr = TTSManager(
            settings=mock_settings,
            audio_player=mock_player,
            cartesia_api_key=None,
            volcengine_access_key="test-key",
            tts_api_provider="Volcengine",
            tts_backend="Volcengine Streaming",
        )
        mgr._executor = mock_tpe  # 替换为 mock 以便验证调用
        return mgr


# 清理测试归属 manager；保留更强的 FIFO、异常后继续与哨兵检查，删除重复覆盖。
# ---------------------------------------------------------------------------
# Deferred janitor loop contract tests (TTS lifecycle cleanup).
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


def _worker(executor: _FakeJanitorExecutor, engine: _FakeJanitorEngine) -> _TTSWorker:
    """替身只实现 janitor 用到的成员，此处收窄为 _TTSWorker 声明的类型。"""
    return _TTSWorker(
        executor=cast(ThreadPoolExecutor, executor),
        engine=cast(BackendTTSEngine, engine),
    )


def test_janitor_loop_drains_workers_in_shutdown_then_close_order():
    """_janitor_loop_inner must drain queued workers and call shutdown BEFORE close for each."""
    log: list[str] = []
    q: SimpleQueue[Any] = SimpleQueue()

    executor_a = _FakeJanitorExecutor("A", log)
    engine_a = _FakeJanitorEngine("A", log)
    worker_a = _worker(executor_a, engine_a)

    executor_b = _FakeJanitorExecutor("B", log)
    engine_b = _FakeJanitorEngine("B", log)
    worker_b = _worker(executor_b, engine_b)

    q.put(worker_a)
    q.put(worker_b)
    q.put(None)

    _janitor_loop_inner(q)

    assert log == [
        "A.executor.shutdown",
        "A.engine.close",
        "B.executor.shutdown",
        "B.engine.close",
    ], f"janitor must process workers FIFO and shutdown-before-close, got {log!r}"

    assert executor_a.shutdown_calls and executor_a.shutdown_calls[0]["wait"] is True
    assert executor_b.shutdown_calls and executor_b.shutdown_calls[0]["wait"] is True
    assert engine_a.close_calls == 1
    assert engine_b.close_calls == 1


def test_janitor_loop_continues_after_executor_shutdown_exception():
    """If executor.shutdown raises, the janitor must still call engine.close AND process subsequent workers."""
    log: list[str] = []
    q: SimpleQueue[Any] = SimpleQueue()

    bad_executor = _FakeJanitorExecutor("bad", log, raise_on_shutdown=True)
    bad_engine = _FakeJanitorEngine("bad", log)
    good_executor = _FakeJanitorExecutor("good", log)
    good_engine = _FakeJanitorEngine("good", log)

    q.put(_worker(bad_executor, bad_engine))
    q.put(_worker(good_executor, good_engine))
    q.put(None)

    _janitor_loop_inner(q)

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
    log: list[str] = []
    q: SimpleQueue[Any] = SimpleQueue()

    bad_executor = _FakeJanitorExecutor("bad", log)
    bad_engine = _FakeJanitorEngine("bad", log, raise_on_close=True)
    good_executor = _FakeJanitorExecutor("good", log)
    good_engine = _FakeJanitorEngine("good", log)

    q.put(_worker(bad_executor, bad_engine))
    q.put(_worker(good_executor, good_engine))
    q.put(None)

    _janitor_loop_inner(q)

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
    log: list[str] = []
    q: SimpleQueue[Any] = SimpleQueue()

    early_executor = _FakeJanitorExecutor("early", log)
    early_engine = _FakeJanitorEngine("early", log)
    early_worker = _worker(early_executor, early_engine)

    late_executor = _FakeJanitorExecutor("late", log)
    late_engine = _FakeJanitorEngine("late", log)
    late_worker = _worker(late_executor, late_engine)

    q.put(early_worker)
    q.put(None)
    q.put(late_worker)

    _janitor_loop_inner(q)

    assert log == [
        "early.executor.shutdown",
        "early.engine.close",
    ], f"janitor must stop at the None sentinel and ignore later items, got {log!r}"
    assert late_executor.shutdown_calls == []
    assert late_engine.close_calls == 0


def test_retired_worker_holds_executor_and_engine_references():
    """_TTSWorker must expose .executor and .engine attributes matching constructor args."""
    executor = _FakeJanitorExecutor("x", [])
    engine = _FakeJanitorEngine("x", [])

    retired = _worker(executor, engine)

    assert retired.executor is executor
    assert retired.engine is engine



class TestTTSManagerInit:
    """TTSManager 构造。"""

    def test_engine_created_on_init(self, manager, mock_engine):
        assert manager.engine is mock_engine

    def test_executor_available(self, manager):
        assert manager.executor is not None

    def test_janitor_thread_alive(self, manager):
        janitor = manager._janitor_thread
        assert janitor.is_alive()


class TestTTSManagerSwitchBackend:
    """切换后端。"""

    def test_switch_to_same_backend_noop(self, manager):
        with patch("wordy.tts_manager.resolve_tts_backend", return_value="Volcengine Streaming"):
            manager.switch_backend("Volcengine Streaming")
        # engine 不变（同后端不重建）
        assert manager.engine is not None

    def test_switch_to_different_backend_rebuilds(self, manager):
        new_engine = MagicMock()
        with (
            patch("wordy.tts_manager.resolve_tts_backend", return_value="Cartesia Bytes"),
            patch("wordy.tts_manager.create_tts_engine", return_value=new_engine),
        ):
            manager.switch_backend("Cartesia Bytes")
        assert manager.engine is new_engine


class TestTTSManagerSwitchProvider:
    """切换服务商。"""

    def test_switch_to_same_provider_noop(self, manager):
        with patch("wordy.tts_manager.create_tts_engine") as create:
            manager.switch_provider("Volcengine")
        create.assert_not_called()
        assert manager.engine is not None

    def test_switch_to_different_provider_rebuilds(self, manager):
        new_engine = MagicMock()
        with patch("wordy.tts_manager.create_tts_engine", return_value=new_engine):
            manager.switch_provider("Cartesia")
        assert manager.engine is new_engine
        assert manager.tts_api_provider == "Cartesia"


class TestTTSManagerApiKey:
    """API Key 更新。"""

    def test_update_cartesia_key(self, manager):
        new_engine = MagicMock()
        with patch("wordy.tts_manager.create_tts_engine", return_value=new_engine):
            manager.update_cartesia_key("new-key")
        assert manager.cartesia_api_key == "new-key"
        assert manager.engine is new_engine

    def test_update_volcengine_credentials(self, manager):
        new_engine = MagicMock()
        with patch("wordy.tts_manager.create_tts_engine", return_value=new_engine):
            manager.update_volcengine_credentials("new-access-key")
        assert manager.volcengine_access_key == "new-access-key"
        assert manager.engine is new_engine


class TestTTSManagerVoice:
    """音色/音量操作。"""

    def test_set_active_voice_volcengine(self, manager, mock_engine, mock_settings):
        manager.set_active_voice("BV001", "Test Voice")
        # 火山音色只存列表：选中的条目排到首位。
        mock_settings.update.assert_called_with(
            volcengine_voice_list=[{"id": "BV001", "name": "Test Voice"}],
        )
        mock_engine.set_voice.assert_called_with("BV001")

    def test_set_active_voice_volcengine_skips_save_when_already_head(
        self, manager, mock_engine, mock_settings,
    ):
        """面板已把选中项排到首位时不再重复落盘。"""
        mock_settings.volcengine_voice_list = [{"id": "BV001", "name": "Test Voice"}]

        manager.set_active_voice("BV001", "Test Voice")

        mock_settings.update.assert_not_called()
        mock_engine.set_voice.assert_called_with("BV001")

    def test_set_active_voice_cartesia(self, mock_settings, mock_player):
        engine = MagicMock()
        mock_settings.cartesia_voice_id = None
        mock_settings.cartesia_voice_name = None
        with patch("wordy.tts_manager.create_tts_engine", return_value=engine):
            mgr = TTSManager(
                settings=mock_settings,
                audio_player=mock_player,
                cartesia_api_key="ck",
                volcengine_access_key=None,
                tts_api_provider="Cartesia",
                tts_backend="Cartesia Bytes",
            )
        mgr.set_active_voice("voice-123", "Cartesia Voice")
        mock_settings.update.assert_called_with(
            cartesia_voice_id="voice-123", cartesia_voice_name="Cartesia Voice",
        )
        engine.set_voice.assert_called_with("voice-123")

    def test_set_volume(self, manager, mock_engine, mock_settings):
        manager.set_volume(0.75)
        assert mock_settings.volume == 0.75
        mock_engine.set_volume.assert_called_with(0.75)

    def test_reset_audio_output(self, manager, mock_engine):
        manager.reset_audio_output()
        operation, *args = manager._executor.submit.call_args.args
        operation(*args)
        mock_engine.reset_audio_output.assert_called_once()

    def test_reset_audio_output_defers_to_worker_thread(self, manager, mock_engine):
        """调用方是 GUI 线程，而实时引擎的 reset 要抢与 speak() 同一把锁。

        同步执行会让「朗读中切换返听/输出设备」把界面卡到本段读完。
        """
        manager.reset_audio_output()
        mock_engine.reset_audio_output.assert_not_called()

    def test_reset_audio_output_skipped_while_worker_hands_over(self, manager, mock_engine):
        """交接期间新引擎还没打开音频流，不能去关旧 worker 正在用的播放器。"""
        manager._worker_ready = threading.Event()
        manager.reset_audio_output()
        manager._executor.submit.assert_not_called()

    def test_reset_audio_output_survives_closed_executor(self, manager, mock_engine):
        """退出后 executor 已关闭，reset 只记日志，不向 GUI 抛出。"""
        manager._executor.submit.side_effect = RuntimeError("cannot schedule new futures")
        manager.reset_audio_output()

    def test_reset_audio_output_failure_does_not_kill_worker(self, manager, mock_engine):
        mock_engine.reset_audio_output.side_effect = RuntimeError("device busy")
        manager.reset_audio_output()
        operation, *args = manager._executor.submit.call_args.args
        operation(*args)


class TestTTSManagerSpeak:
    """播放相关。"""

    def test_speak_empty_text_ignored(self, manager):
        manager.speak("   ")
        manager._executor.submit.assert_not_called()

    def test_speak_no_voice_id_logs_error(self, manager, mock_settings):
        mock_settings.volcengine_voice_id = None
        manager.speak("hello")
        manager._executor.submit.assert_not_called()

    def test_speak_submits_to_executor(self, manager, mock_settings):
        mock_settings.volcengine_voice_id = "BV001"
        manager.speak("hello")
        manager._executor.submit.assert_called_once()

    def test_speak_reports_finish(self, manager, mock_settings, mock_engine):
        finished = MagicMock()
        manager.speak("hello", finished)
        operation, *args = manager._executor.submit.call_args.args
        operation(*args, **manager._executor.submit.call_args.kwargs)
        finished.assert_called_once_with()
        mock_engine.speak.assert_called_once_with("hello")

    def test_speak_reports_finish_on_engine_error(self, manager, mock_settings, mock_engine):
        mock_engine.speak.side_effect = OSError("网络中断")
        finished = MagicMock()
        manager.speak("hello", finished)
        operation, *args = manager._executor.submit.call_args.args
        operation(*args, **manager._executor.submit.call_args.kwargs)
        finished.assert_called_once_with()

    def test_speak_reports_finish_when_it_bails(self, manager, mock_settings):
        """早退也必须回调：设置页的「朗读中…」靠它复位，否则永远挂着。"""
        finished = MagicMock()
        manager.speak("   ", finished)
        finished.assert_called_once_with()
        manager._executor.submit.assert_not_called()

        mock_settings.volcengine_voice_id = None
        manager.speak("hello", finished)
        assert finished.call_count == 2

    def test_speak_reports_finish_when_submit_fails(self, manager, mock_settings):
        manager._executor.submit.side_effect = RuntimeError("executor 已关闭")
        finished = MagicMock()
        manager.speak("hello", finished)
        finished.assert_called_once_with()

    def test_fetch_voices_delegates_to_engine(self, manager, mock_engine):
        mock_engine.fetch_voices.return_value = [{"id": "v1", "name": "Voice 1"}]
        result = manager.fetch_voices()
        assert result == [{"id": "v1", "name": "Voice 1"}]


class TestTTSManagerShutdown:
    """关闭。"""

    def test_shutdown_returns_true_when_janitor_exits(self, manager):
        result = manager.shutdown()
        assert result is True

    def test_shutdown_stops_janitor_thread(self, manager):
        manager.shutdown()
        janitor = manager._janitor_thread
        janitor.join(timeout=1.0)
        assert not janitor.is_alive()
