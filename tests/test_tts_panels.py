"""TTS 设置面板测试。"""

from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from PySide6.QtWidgets import QVBoxLayout, QWidget

from wordy.ui.tts_panels import (
    PROVIDER_PANELS,
    CartesiaPanel,
    VolcenginePanel,
    _BasePanel,
)
from wordy.config import AppSettings
from wordy.tts.constants import TTS_API_PROVIDER_CARTESIA, TTS_API_PROVIDER_VOLCENGINE
from wordy.ui.settings import SettingsWindow


@pytest.mark.parametrize("attribute", [
    "api_key_input", "voice_combo", "voice_label_to_id", "voice_label_to_name",
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
    # 映射按 QWidget 声明，但测试明确注入 MagicMock 以检查调用。
    cast(MagicMock, window._provider_panel_widgets[TTS_API_PROVIDER_CARTESIA]).setVisible.assert_called_once_with(False)
    cast(MagicMock, window._provider_panel_widgets[TTS_API_PROVIDER_VOLCENGINE]).setVisible.assert_called_once_with(True)
    window.on_field_changed.assert_called_once_with("active_tts_provider", TTS_API_PROVIDER_VOLCENGINE)


@pytest.fixture
def mock_state() -> MagicMock:
    s = MagicMock()
    s.cartesia_api_key_saved = False
    s.volcengine_access_key_saved = False
    s.voices_cache = []
    s.voices_loading = False
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


# 保住父容器引用：容器一旦被 GC，其子控件的 C++ 对象会一起销毁，
# 后续断言就变成 "Internal C++ object already deleted"。
_PANEL_CONTAINERS: list[QWidget] = []


def _build_volcengine_panel(mock_state, on_field_changed=None) -> VolcenginePanel:
    panel = VolcenginePanel()
    parent = QVBoxLayout()
    container = QWidget()
    container.setLayout(parent)
    panel.build(parent, mock_state, lambda: None, on_field_changed)
    _PANEL_CONTAINERS.append(container)
    return panel


class TestVolcenginePanelBuild:
    """VolcenginePanel build 方法。"""

    def test_build_creates_widgets(self, qapp, mock_state):
        panel = _build_volcengine_panel(mock_state)

        assert panel.api_key_input is not None
        assert panel.save_key_btn is not None
        assert panel.clear_key_btn is not None
        assert panel.voice_combo is not None
        assert panel.new_voice_id is not None
        assert panel.new_voice_name is not None

    def test_voice_id_input_placeholder(self, qapp, mock_state):
        panel = _build_volcengine_panel(mock_state)
        assert "BV001_streaming" in panel.new_voice_id.placeholderText()

    def test_combo_starts_with_none_entry(self, qapp, mock_state):
        panel = _build_volcengine_panel(mock_state)
        assert panel.voice_combo.currentText() == panel.NONE_VOICE_LABEL

    def test_empty_list_makes_current_state_and_actions_explicit(self, qapp, mock_state):
        panel = _build_volcengine_panel(mock_state)

        assert panel.current_voice_id.text() == "尚未添加音色"
        assert not panel.remove_voice_btn.isEnabled()
        assert panel.add_voice_btn.text() == "添加并使用"


class TestVolcenginePanelVoiceList:
    """本地手动维护的音色列表：添加、快速切换、删除。不联网。"""

    def test_add_voice_persists_and_fills_combo(self, qapp, mock_state):
        panel = _build_volcengine_panel(mock_state)
        panel.new_voice_id.setText("S_abc")
        panel.new_voice_name.setText("我的复刻音色")

        panel._on_add_voice()

        assert AppSettings.load().volcengine_voice_list == [
            {"id": "S_abc", "name": "我的复刻音色"}
        ]
        assert panel.voice_combo.currentText() == "我的复刻音色"
        assert panel.voice_label_to_id["我的复刻音色"] == "S_abc"
        assert panel.current_voice_id.text() == "Speaker ID  S_abc"
        assert panel.remove_voice_btn.isEnabled()

    def test_existing_id_changes_editor_action_to_update(self, qapp, mock_state):
        panel = _build_volcengine_panel(mock_state)
        panel.new_voice_id.setText("S_abc")
        panel.new_voice_name.setText("我的复刻音色")
        panel._on_add_voice()

        panel.new_voice_id.setText("S_abc")

        assert panel.add_voice_btn.text() == "保存修改"

    def test_add_voice_with_same_id_updates_in_place(self, qapp, mock_state):
        """同 ID 重填名称是「改」，不是再插一条。"""
        panel = _build_volcengine_panel(mock_state)
        panel.new_voice_id.setText("S_abc")
        panel.new_voice_name.setText("旧名")
        panel._on_add_voice()
        panel.new_voice_id.setText("S_abc")
        panel.new_voice_name.setText("新名")

        panel._on_add_voice()

        assert AppSettings.load().volcengine_voice_list == [{"id": "S_abc", "name": "新名"}]

    def test_add_voice_without_name_falls_back_to_id(self, qapp, mock_state):
        panel = _build_volcengine_panel(mock_state)
        panel.new_voice_id.setText("S_abc")
        panel.new_voice_name.setText("   ")

        panel._on_add_voice()

        assert panel.voice_label_to_id == {"S_abc": "S_abc"}

    def test_add_voice_without_id_does_nothing(self, qapp, mock_state):
        panel = _build_volcengine_panel(mock_state)
        panel.new_voice_id.setText("   ")

        panel._on_add_voice()

        assert AppSettings.load().volcengine_voice_list == []

    def test_selecting_from_combo_moves_voice_to_front(self, qapp, mock_state):
        """下拉切换 = 把选中条目挪到首位；列表顺序就是当前音色。"""
        changes: list[tuple[str, object]] = []
        panel = _build_volcengine_panel(
            mock_state, lambda field, value: changes.append((field, value))
        )
        for voice_id, name in (("S_abc", "我的复刻音色"), ("S_def", "备用音色")):
            panel.new_voice_id.setText(voice_id)
            panel.new_voice_name.setText(name)
            panel._on_add_voice()
        changes.clear()

        panel.voice_combo.setCurrentText("我的复刻音色")

        settings = AppSettings.load()
        assert [entry["id"] for entry in settings.volcengine_voice_list] == ["S_abc", "S_def"]
        assert settings.volcengine_voice_id == "S_abc"
        assert settings.volcengine_voice_name == "我的复刻音色"
        assert changes == [("volcengine_voice_list", "S_abc")]
        assert panel.current_voice_id.text() == "Speaker ID  S_abc"
        assert panel.new_voice_id.text() == "S_abc"
        assert panel.new_voice_name.text() == "我的复刻音色"
        assert panel.add_voice_btn.text() == "保存修改"

    def test_remove_voice_drops_entry_and_clears_active(self, qapp, mock_state):
        panel = _build_volcengine_panel(mock_state)
        panel.new_voice_id.setText("S_abc")
        panel.new_voice_name.setText("我的复刻音色")
        panel._on_add_voice()
        panel.voice_combo.setCurrentText("我的复刻音色")

        panel._on_remove_voice()

        settings = AppSettings.load()
        assert settings.volcengine_voice_list == []
        # 删掉的正是生效音色时要一并清空，否则下拉会停在一个不存在的条目上。
        assert settings.volcengine_voice_id is None
        assert settings.volcengine_voice_name is None
        assert panel.voice_combo.currentText() == panel.NONE_VOICE_LABEL

    def test_remove_active_voice_falls_back_to_remaining_entry(self, qapp, mock_state):
        """删掉生效音色后落到剩下第一条，而不是留下一个空的下拉。"""
        panel = _build_volcengine_panel(mock_state)
        for voice_id, name in (("S_abc", "音色甲"), ("S_def", "音色乙")):
            panel.new_voice_id.setText(voice_id)
            panel.new_voice_name.setText(name)
            panel._on_add_voice()

        panel.voice_combo.setCurrentText("音色甲")
        panel._on_remove_voice()

        settings = AppSettings.load()
        assert [entry["id"] for entry in settings.volcengine_voice_list] == ["S_def"]
        assert settings.volcengine_voice_id == "S_def"
        assert panel.voice_combo.currentText() == "音色乙"
        assert panel.current_voice_id.text() == "Speaker ID  S_def"
        assert panel.remove_voice_btn.isEnabled()

    def test_add_voice_makes_it_active_and_notifies(self, qapp, mock_state):
        """新填的音色直接生效并通知重建引擎。"""
        changes: list[tuple[str, object]] = []
        panel = _build_volcengine_panel(
            mock_state, lambda field, value: changes.append((field, value))
        )

        panel.new_voice_id.setText("S_abc")
        panel.new_voice_name.setText("我的复刻音色")
        panel._on_add_voice()

        settings = AppSettings.load()
        assert settings.volcengine_voice_list == [{"id": "S_abc", "name": "我的复刻音色"}]
        assert settings.volcengine_voice_id == "S_abc"
        assert settings.volcengine_voice_name == "我的复刻音色"
        assert changes == [("volcengine_voice_list", "S_abc")]

    def test_remove_from_empty_list_does_nothing(self, qapp, mock_state):
        """列表为空时下拉只有「无」，删除不能误伤任何条目。"""
        panel = _build_volcengine_panel(mock_state)
        assert panel.voice_combo.currentText() == panel.NONE_VOICE_LABEL

        panel._on_remove_voice()

        assert AppSettings.load().volcengine_voice_list == []

    def test_combo_hides_none_option_when_list_has_entries(self, qapp, mock_state):
        """列表非空时没有「无」可选——首位条目就是当前音色。"""
        s = AppSettings.load()
        s.volcengine_voice_list = [{"id": "S_abc", "name": "我的复刻音色"}]

        panel = _build_volcengine_panel(mock_state)

        assert panel.voice_combo.count() == 1
        assert panel.voice_combo.itemText(0) == "我的复刻音色"

    def test_saved_list_repopulates_combo_on_build(self, qapp, mock_state):
        """重启后下拉必须直接可用，用户不必重新录入。"""
        s = AppSettings.load()
        s.volcengine_voice_list = [{"id": "S_abc", "name": "我的复刻音色"}]

        panel = _build_volcengine_panel(mock_state)

        assert panel.voice_combo.currentText() == "我的复刻音色"
        assert panel.voice_label_to_id["我的复刻音色"] == "S_abc"
        assert panel.new_voice_id.text() == "S_abc"
        assert panel.new_voice_name.text() == "我的复刻音色"

    def test_set_voices_loaded_uses_local_list(self, qapp, mock_state):
        """Volcengine 不联网拉取：忽略传入的网络结果，始终按本地列表重填。"""
        panel = _build_volcengine_panel(mock_state)
        panel.new_voice_id.setText("S_abc")
        panel.new_voice_name.setText("我的复刻音色")
        panel._on_add_voice()

        labels = panel.set_voices_loaded([], None)

        assert labels == ["我的复刻音色"]
        assert panel.voice_combo.currentText() == "我的复刻音色"


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



def test_voice_feedback_stacks_as_popups(qapp, mock_state):
    """音色设置的反馈全部走浮动弹窗，加载与结果两条上下并列。"""
    from PySide6.QtTest import QTest

    from wordy.ui.status_toast import SLIDE_MS, StatusToastQueue

    host = QWidget()
    host.resize(420, 400)
    queue = StatusToastQueue(host)
    container = QWidget(host)
    layout = QVBoxLayout(container)
    panel = CartesiaPanel()
    panel.build(layout, mock_state, lambda: None)
    host.show()
    qapp.processEvents()

    try:
        panel.set_voices_loading()
        panel.set_voices_loaded([{"id": "v1", "name": "Voice"}], "v1")
        # 队列重排是滑动动画，等它落位后再比可见卡片的位置。
        QTest.qWait(SLIDE_MS + 40)

        assert queue.texts() == ["正在加载音色列表...", "已加载 1 个音色"]
        older, newer = queue.cards()
        assert older.card_geometry().bottom() < newer.card_geometry().y(), "多条反馈应上下并列"
        assert panel.voice_status.isVisible() is False, "反馈不再占用原地标签"
    finally:
        host.close()


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
        cast(MagicMock, panel).setVisible.assert_not_called()
    assert "切换失败" in window.set_status.call_args.args[0]
    settings.save()
    assert AppSettings.load(config_file=settings._config_file).active_tts_provider == TTS_API_PROVIDER_CARTESIA
