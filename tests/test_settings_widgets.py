"""设置窗口专用控件测试。"""

from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QWheelEvent

from wordy.ui.settings_widgets import (
    CheckmarkCheckBox,
    NoWheelComboBox,
    NoWheelSlider,
)


class TestNoWheelComboBox:
    """NoWheelComboBox 忽略滚轮事件。"""

    def test_wheel_event_ignored(self, qapp):
        combo = NoWheelComboBox()
        combo.addItem("test")
        event = QWheelEvent(
            QPoint(10, 10), QPoint(10, 20), QPoint(0, 120),
            QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollBegin, False,
        )
        combo.wheelEvent(event)
        assert combo.currentIndex() == 0

    def test_is_qcombobox_subclass(self):
        from PySide6.QtWidgets import QComboBox
        combo = NoWheelComboBox()
        assert isinstance(combo, QComboBox)


class TestNoWheelSlider:
    """NoWheelSlider 忽略滚轮事件。"""

    def test_wheel_event_ignored(self, qapp):
        slider = NoWheelSlider(Qt.Orientation.Horizontal)
        slider.setRange(0, 100)
        slider.setValue(50)
        event = QWheelEvent(
            QPoint(10, 10), QPoint(10, 20), QPoint(0, 120),
            QPoint(0, 120), Qt.MouseButton.NoButton, Qt.KeyboardModifier.NoModifier,
            Qt.ScrollPhase.ScrollBegin, False,
        )
        slider.wheelEvent(event)
        assert slider.value() == 50

    def test_is_qslider_subclass(self):
        from PySide6.QtWidgets import QSlider
        slider = NoWheelSlider(Qt.Orientation.Horizontal)
        assert isinstance(slider, QSlider)


class TestCheckmarkCheckBox:
    """CheckmarkCheckBox 自定义绘制复选框。"""

    def test_default_unchecked(self, qapp):
        cb = CheckmarkCheckBox("Test")
        assert cb.isChecked() is False

    def test_check_uncheck(self, qapp):
        cb = CheckmarkCheckBox("Test")
        cb.setChecked(True)
        assert cb.isChecked() is True
        cb.setChecked(False)
        assert cb.isChecked() is False

    def test_text_preserved(self, qapp):
        cb = CheckmarkCheckBox("My Label")
        assert cb.text() == "My Label"

    def test_indicator_size_constant(self):
        assert CheckmarkCheckBox.INDICATOR_SIZE == 14
        assert CheckmarkCheckBox.LABEL_GAP == 8

    def test_size_hint_is_valid(self, qapp):
        cb = CheckmarkCheckBox("Hello World")
        hint = cb.sizeHint()
        assert hint.width() > 0
        assert hint.height() > 0
