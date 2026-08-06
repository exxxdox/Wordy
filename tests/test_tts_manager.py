"""TTS Manager 生命周期测试。"""

from unittest.mock import MagicMock, patch

import pytest

from wordy.tts_manager import TTSManager, _janitor_loop_inner
from queue import SimpleQueue


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


class TestJanitorLoopInner:
    """_janitor_loop_inner 底层清理循环。"""

    def test_none_sentinel_exits(self):
        q: SimpleQueue = SimpleQueue()
        q.put(None)
        _janitor_loop_inner(q)

    def test_cleans_up_retired_worker(self):
        q: SimpleQueue = SimpleQueue()
        executor = MagicMock()
        engine = MagicMock()
        from wordy.tts_manager import _RetiredWorker
        q.put(_RetiredWorker(executor=executor, engine=engine))
        q.put(None)
        _janitor_loop_inner(q)
        executor.shutdown.assert_called_once_with(wait=True)
        engine.close.assert_called_once()

    def test_handles_executor_shutdown_error(self):
        """executor.shutdown 异常时仍继续清理 engine。"""
        q: SimpleQueue = SimpleQueue()
        executor = MagicMock()
        executor.shutdown.side_effect = RuntimeError("shutdown fail")
        engine = MagicMock()
        from wordy.tts_manager import _RetiredWorker
        q.put(_RetiredWorker(executor=executor, engine=engine))
        q.put(None)
        _janitor_loop_inner(q)
        engine.close.assert_called_once()

    def test_handles_engine_close_error(self):
        """engine.close 异常时不抛异常并继续处理。"""
        q: SimpleQueue = SimpleQueue()
        executor = MagicMock()
        engine = MagicMock()
        engine.close.side_effect = RuntimeError("close fail")
        from wordy.tts_manager import _RetiredWorker
        q.put(_RetiredWorker(executor=executor, engine=engine))
        q.put(None)
        _janitor_loop_inner(q)
        executor.shutdown.assert_called_once_with(wait=True)


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
        mock_settings.update.assert_called_with(
            volcengine_voice_id="BV001", volcengine_voice_name="Test Voice",
        )
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
        mock_engine.reset_audio_output.assert_called_once()


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
