from unittest.mock import Mock

from PySide6.QtCore import QPoint

from wordy.config import AppSettings
from wordy.ui.chat_wheel_settings import ChatWheelSettings


def test_wide_wheel_places_editor_beside_preview(qapp):
    panel = ChatWheelSettings(AppSettings(), Mock())
    panel.resize(1000, 480)
    panel.show()
    qapp.processEvents()
    # 文字框只在文字模式下显示；空槽切换不弹确认，也不写配置。
    panel.text_mode_button.click()
    qapp.processEvents()
    preview_right = panel.wheel_picker.mapTo(panel, panel.wheel_picker.rect().topRight()).x()
    assert panel.label_input.mapTo(panel, QPoint()).x() > preview_right
    assert panel.text_input.mapTo(panel, QPoint()).y() > panel.label_input.mapTo(panel, QPoint()).y()
    assert panel.clear_button.mapTo(panel, QPoint()).y() > panel.text_input.mapTo(panel, QPoint()).y()
    assert panel.wheel_picker.width() == panel.wheel_picker.height() == 400
    assert panel.wheel_combo.width() == 220
    assert panel.hotkey_input.width() == 160
    assert panel.label_input.width() == panel.text_input.width()
    assert panel.volume_slider.width() <= 280
    assert panel.choose_button.width() < 120
    panel.close()
