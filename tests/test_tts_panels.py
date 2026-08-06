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
