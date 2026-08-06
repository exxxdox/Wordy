"""设置窗口 QSS 样式表测试。"""

from wordy.ui.settings_style import build_settings_stylesheet


class TestBuildSettingsStylesheet:
    """build_settings_stylesheet 输出验证。"""

    def test_returns_non_empty_string(self):
        css = build_settings_stylesheet()
        assert isinstance(css, str)
        assert len(css) > 100

    def test_contains_dialog_shell_selector(self):
        css = build_settings_stylesheet()
        assert "QFrame#dialogShell" in css

    def test_contains_tab_widget_selector(self):
        css = build_settings_stylesheet()
        assert "QTabWidget#settingsTabs" in css

    def test_contains_scrollbar_selectors(self):
        css = build_settings_stylesheet()
        assert "QScrollBar:vertical" in css

    def test_contains_button_selectors(self):
        css = build_settings_stylesheet()
        assert "QPushButton" in css

    def test_contains_apply_button_selector(self):
        css = build_settings_stylesheet()
        assert "QPushButton#applyButton" in css

    def test_contains_cancel_button_selector(self):
        css = build_settings_stylesheet()
        assert "QPushButton#cancelButton" in css

    def test_contains_combobox_selector(self):
        css = build_settings_stylesheet()
        assert "QComboBox" in css

    def test_contains_lineedit_selector(self):
        css = build_settings_stylesheet()
        assert "QLineEdit" in css

    def test_contains_slider_selectors(self):
        css = build_settings_stylesheet()
        assert "QSlider::groove:horizontal" in css
        assert "QSlider::handle:horizontal" in css

    def test_contains_checkbox_selector(self):
        css = build_settings_stylesheet()
        assert "QCheckBox" in css

    def test_contains_dialog_title_selector(self):
        css = build_settings_stylesheet()
        assert "QLabel#dialogTitle" in css

    def test_contains_section_title_selector(self):
        css = build_settings_stylesheet()
        assert "QLabel#sectionTitle" in css

    def test_contains_separator_selector(self):
        css = build_settings_stylesheet()
        assert "QFrame#separator" in css

    def test_contains_tts_provider_container_selector(self):
        css = build_settings_stylesheet()
        assert "QFrame#ttsProviderContainer" in css

    def test_is_idempotent(self):
        """多次调用返回相同样式表（无副作用累积）。"""
        css1 = build_settings_stylesheet()
        css2 = build_settings_stylesheet()
        assert css1 == css2

    def test_no_fstring_placeholders(self):
        """样式表不含 Python f-string 占位符（如 {VARNAME}）。"""
        import re

        css = build_settings_stylesheet()
        # QSS 选择器用 { … } 是正常的；检查是否有未替换的 Python 变量名
        # 合法的 theme 颜色值都是 hex 如 #e6edf6，不含 { 在非选择器位置
        # 所以只需确认 CSS 不以裸 { 开头（这些是 QSS 块）
        fstring_pattern = re.findall(r"\{[a-zA-Z_][a-zA-Z0-9_]*\}", css)
        assert fstring_pattern == [], f"f-string placeholders found: {fstring_pattern}"
