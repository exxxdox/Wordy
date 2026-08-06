"""设置窗口运行时状态测试。"""

from wordy.ui.settings_state import SettingsState


class TestSettingsState:
    """SettingsState dataclass 默认值与字段赋值。"""

    def test_default_audio_output_devices_is_empty_list(self):
        s = SettingsState()
        assert s.audio_output_devices == []

    def test_default_audio_output_devices_error_is_none(self):
        s = SettingsState()
        assert s.audio_output_devices_error is None

    def test_default_input_devices_is_empty_list(self):
        s = SettingsState()
        assert s.input_devices == []

    def test_default_voices_cache_is_empty_list(self):
        s = SettingsState()
        assert s.voices_cache == []

    def test_default_voices_loading_is_false(self):
        s = SettingsState()
        assert s.voices_loading is False

    def test_default_voice_fetch_error_is_none(self):
        s = SettingsState()
        assert s.voice_fetch_error is None

    def test_default_cartesia_api_key_saved_is_false(self):
        s = SettingsState()
        assert s.cartesia_api_key_saved is False

    def test_default_volcengine_access_key_saved_is_false(self):
        s = SettingsState()
        assert s.volcengine_access_key_saved is False

    def test_default_vb_cable_installed_is_false(self):
        s = SettingsState()
        assert s.vb_cable_installed is False

    def test_default_mic_listen_configured_is_false(self):
        s = SettingsState()
        assert s.mic_listen_configured is False

    def test_fields_are_independent(self):
        """修改一个字段不影响其他字段默认值。"""
        s = SettingsState(
            audio_output_devices=[{"name": "Speakers", "host_api_name": "MME"}],
            vb_cable_installed=True,
        )
        assert len(s.audio_output_devices) == 1
        assert s.vb_cable_installed is True
        # 其他字段仍为默认
        assert s.voices_cache == []
        assert s.voices_loading is False
        assert s.cartesia_api_key_saved is False

    def test_voice_fetch_error_stores_exception(self):
        err = RuntimeError("network timeout")
        s = SettingsState(voice_fetch_error=err)
        assert s.voice_fetch_error is err

    def test_audio_output_devices_error_stores_exception(self):
        err = OSError("no devices")
        s = SettingsState(audio_output_devices_error=err)
        assert s.audio_output_devices_error is err
