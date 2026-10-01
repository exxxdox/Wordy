"""TTS 设置面板测试。"""

from unittest.mock import MagicMock, patch

import pytest

from PySide6.QtWidgets import QVBoxLayout, QWidget

from wordy.ui.tts_panels import (
    PROVIDER_PANELS,
    CartesiaPanel,
    VolcenginePanel,
    _BasePanel,
)
from wordy.tts.constants import TTS_API_PROVIDER_CARTESIA, TTS_API_PROVIDER_VOLCENGINE
from wordy.ui.settings import SettingsWindow


@pytest.mark.parametrize("attribute", [
    "api_key_input", "voice_combo", "refresh_voices_button", "voice_status_label",
    "voice_label_to_id", "voice_label_to_name", "clear_api_key_button", "api_key_status_label",
])
def test_panel_delegation_rejects_assignment(attribute):
    # 面板持有控件，窗口属性不应静默吞掉误赋值。
    window = SettingsWindow.__new__(SettingsWindow)
    with pytest.raises(AttributeError):
        setattr(window, attribute, object())


def test_provider_switch_only_updates_visible_panel_and_notifies(monkeypatch):
    window = SettingsWindow.__new__(SettingsWindow)
    window._settings = MagicMock(active_tts_provider=TTS_API_PROVIDER_CARTESIA)
    window._populate_backend_combo_for_provider = MagicMock()
    window.on_field_changed = MagicMock()
    window._provider_panel_widgets = {
        TTS_API_PROVIDER_CARTESIA: MagicMock(),
        TTS_API_PROVIDER_VOLCENGINE: MagicMock(),
    }
    # 服务商切换只依赖真实行为，不要求每个面板实现空钩子。
    monkeypatch.setattr("wordy.ui.settings.PROVIDER_PANELS", {
        TTS_API_PROVIDER_CARTESIA: object(), TTS_API_PROVIDER_VOLCENGINE: object(),
    })
    window._on_tts_api_selected(TTS_API_PROVIDER_VOLCENGINE)
    assert window._settings.active_tts_provider == TTS_API_PROVIDER_VOLCENGINE
    window._provider_panel_widgets[TTS_API_PROVIDER_CARTESIA].setVisible.assert_called_once_with(False)
    window._provider_panel_widgets[TTS_API_PROVIDER_VOLCENGINE].setVisible.assert_called_once_with(True)
    window.on_field_changed.assert_called_once_with("active_tts_provider", TTS_API_PROVIDER_VOLCENGINE)


@pytest.fixture
def mock_state() -> MagicMock:
    s = MagicMock()
    s.cartesia_api_key_saved = False
    s.volcengine_access_key_saved = False
    s.voices_cache = []
    s.voices_loading = False
    s.voice_fetch_error = None
    return s


class TestBasePanel:
    """_BasePanel 静态工具方法。"""

    def test_section_title_has_correct_object_name(self, qapp):
        label = _BasePanel._section_title("Test Title")
        assert label.objectName() == "sectionTitle"
        assert label.text() == "Test Title"

    def test_body_label_default(self, qapp):
        label = _BasePanel._body_label("Body", "#fff")
        assert label.objectName() == "bodyLabel"
        assert "color: #fff" in label.styleSheet()

    def test_body_label_emphasis(self, qapp):
        label = _BasePanel._body_label("Emphasis", "#000", emphasis=True)
        assert label.objectName() == "bodyLabelEmphasis"

    def test_hint_label(self, qapp):
        label = _BasePanel._hint_label("Hint", "#999")
        assert label.objectName() == "hintLabel"
        assert label.wordWrap() is True

    def test_set_label(self, qapp):
        from PySide6.QtWidgets import QLabel
        w = QLabel("old")
        _BasePanel._set_label(w, "new", "red")
        assert w.text() == "new"
        assert "color: red" in w.styleSheet()

    def test_create_section(self, qapp):
        parent = QVBoxLayout()
        section = _BasePanel._create_section(parent)
        assert isinstance(section, QVBoxLayout)


class TestCartesiaPanelBuild:
    """CartesiaPanel build 方法。"""

    def test_build_creates_widgets(self, qapp, mock_state):
        panel = CartesiaPanel()
        parent = QVBoxLayout()
        container = QWidget()
        container.setLayout(parent)

        panel.build(parent, mock_state, lambda: None)

        assert panel.api_key_input is not None
        assert panel.voice_combo is not None
        assert panel.refresh_btn is not None

    def test_none_voice_label_in_combo(self, qapp, mock_state):
        panel = CartesiaPanel()
        parent = QVBoxLayout()
        container = QWidget()
        container.setLayout(parent)

        panel.build(parent, mock_state, lambda: None)
        assert panel.voice_combo.count() >= 1
        assert panel.voice_combo.itemText(0) == panel.NONE_VOICE_LABEL


class TestVolcenginePanelBuild:
    """VolcenginePanel build 方法。"""

    def test_build_creates_widgets(self, qapp, mock_state):
        panel = VolcenginePanel()
        parent = QVBoxLayout()
        container = QWidget()
        container.setLayout(parent)

        panel.build(parent, mock_state, lambda: None)

        assert panel.api_key_input is not None
        assert panel.speaker_input is not None
        assert panel.save_key_btn is not None
        assert panel.clear_key_btn is not None

    def test_speaker_input_placeholder(self, qapp, mock_state):
        panel = VolcenginePanel()
        parent = QVBoxLayout()
        container = QWidget()
        container.setLayout(parent)

        panel.build(parent, mock_state, lambda: None)
        assert "Speaker ID" in panel.speaker_input.placeholderText()


class TestProviderPanelsRegistry:
    """PROVIDER_PANELS 注册表。"""

    def test_cartesia_panel_registered(self):
        assert TTS_API_PROVIDER_CARTESIA in PROVIDER_PANELS
        assert isinstance(PROVIDER_PANELS[TTS_API_PROVIDER_CARTESIA], CartesiaPanel)

    def test_volcengine_panel_registered(self):
        assert TTS_API_PROVIDER_VOLCENGINE in PROVIDER_PANELS
        assert isinstance(PROVIDER_PANELS[TTS_API_PROVIDER_VOLCENGINE], VolcenginePanel)


class TestCartesiaPanelApiKey:
    """CartesiaPanel API key 保存/清除。"""

    def test_save_key_with_empty_input_shows_warning(self, qapp, mock_state):
        panel = CartesiaPanel()
        parent = QVBoxLayout()
        container = QWidget()
        container.setLayout(parent)

        panel.build(parent, mock_state, lambda: None)
        panel.api_key_input.clear()
        with patch("wordy.ui.tts_panels.wordy.secret.save_cartesia_api_key") as save:
            panel._on_save_key()
        save.assert_not_called()

    def test_clear_key_deletes_from_keyring(self, qapp, mock_state):
        panel = CartesiaPanel()
        parent = QVBoxLayout()
        container = QWidget()
        container.setLayout(parent)

        panel.build(parent, mock_state, lambda: None)
        with patch("wordy.ui.tts_panels.wordy.secret.delete_cartesia_api_key") as delete:
            panel._on_clear_key()
        delete.assert_called_once()


class TestVolcenginePanelApiKey:
    """VolcenginePanel API key 保存/清除。"""

    def test_save_key_with_empty_input_shows_warning(self, qapp, mock_state):
        panel = VolcenginePanel()
        parent = QVBoxLayout()
        container = QWidget()
        container.setLayout(parent)

        panel.build(parent, mock_state, lambda: None)
        panel.api_key_input.clear()
        with patch("wordy.ui.tts_panels.wordy.secret.save_volcengine_access_key") as save:
            panel._on_save_key()
        save.assert_not_called()



def test_provider_switch_failure_restores_config_and_controls(qapp, tmp_path):
    from wordy.config import AppSettings
    from PySide6.QtWidgets import QComboBox
    settings = AppSettings()
    settings.active_tts_provider = TTS_API_PROVIDER_CARTESIA
    settings._config_file = tmp_path / "provider.toml"
    settings._loaded = True
    window = SettingsWindow.__new__(SettingsWindow)
    window._settings = settings
    window.tts_api_combo = QComboBox()
    window.tts_api_combo.addItems([TTS_API_PROVIDER_CARTESIA, TTS_API_PROVIDER_VOLCENGINE])
    window.tts_api_combo.setCurrentText(TTS_API_PROVIDER_VOLCENGINE)
    window._populate_backend_combo_for_provider = MagicMock()
    window._provider_panel_widgets = {TTS_API_PROVIDER_CARTESIA: MagicMock(), TTS_API_PROVIDER_VOLCENGINE: MagicMock()}
    window.on_field_changed = MagicMock(side_effect=RuntimeError("engine build failed"))
    window.set_status = MagicMock()
    window._on_tts_api_selected(TTS_API_PROVIDER_VOLCENGINE)
    assert settings.active_tts_provider == TTS_API_PROVIDER_CARTESIA
    assert window.tts_api_combo.currentText() == TTS_API_PROVIDER_CARTESIA
    window._populate_backend_combo_for_provider.assert_not_called()
    for panel in window._provider_panel_widgets.values():
        panel.setVisible.assert_not_called()
    assert "切换失败" in window.set_status.call_args.args[0]
    settings.save()
    assert AppSettings.load(config_file=settings._config_file).active_tts_provider == TTS_API_PROVIDER_CARTESIA
