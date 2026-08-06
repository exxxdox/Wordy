"""Overlay 控件测试。

_OverlaySignals 和 _OverlayWidget 的非视觉行为测试。
"""

from unittest.mock import MagicMock

from PySide6.QtCore import QEvent

from wordy.ui.overlay_widgets import _OverlaySignals, _SETTINGS_SVG_PATH


class TestOverlaySignals:
    """_OverlaySignals 信号桥测试。"""

    def test_signals_declared(self):
        owner = MagicMock()
        owner._event_filter = lambda w, e: False
        signals = _OverlaySignals(owner)
        assert hasattr(signals, "hotkey_triggered")
        assert hasattr(signals, "record_finished")
        assert hasattr(signals, "voices_loaded")
        assert hasattr(signals, "voices_error")

    def test_event_filter_delegates_to_owner(self):
        owner = MagicMock()
        owner._event_filter = lambda w, e: True
        signals = _OverlaySignals(owner)
        result = signals.eventFilter(MagicMock(), QEvent(QEvent.Type.Paint))
        assert result is True


class TestOverlayWidgetMakeIcon:
    """_make_settings_icon 静态方法（不依赖 QApplication 的测试）。"""

    def test_svg_template_exists_and_readable(self):
        """SVG 图标文件存在且可读。"""
        assert _SETTINGS_SVG_PATH.exists()
        content = _SETTINGS_SVG_PATH.read_text(encoding="utf-8")
        assert "<svg" in content
        assert 'fill="#e3e3e3"' in content

    def test_color_replacement_in_svg(self):
        """颜色替换逻辑：将 #e3e3e3 替换为目标色。"""
        template = _SETTINGS_SVG_PATH.read_text(encoding="utf-8")
        # 模拟 _make_settings_icon 中的颜色替换
        target = "#ff0000"
        replaced = template.replace("#e3e3e3", target)
        assert target in replaced
        assert "#e3e3e3" not in replaced
