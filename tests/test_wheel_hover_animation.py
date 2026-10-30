from PySide6.QtWidgets import QWidget
from wordy.ui.wheel_paint import WheelHover


def test_hover_retargets_from_current_frame(qapp):
    widget = QWidget()
    animation = WheelHover(widget)
    animation.select(0)
    animation.setCurrentTime(45)
    first = animation.values.copy()
    assert 0 < first[0] < 1
    animation.select(2)
    assert animation.values == first
    animation.setCurrentTime(90)
    assert animation.values[0] == 0
    assert animation.values[2] == 1
    animation.select(None)
    animation.setCurrentTime(90)
    assert animation.values == [0.0] * 8
