"""截取弹窗的保存目标选择：复用轮盘预览直观显示槽位内容。"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QKeySequenceEdit, QLabel,
    QLineEdit, QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from wordy.config import AppSettings, ChatWheelConfig
from wordy.ui.settings_widgets import NoWheelComboBox, NoWheelSlider
from wordy.ui.wheel_picker import WheelPicker

SLOTS_PER_WHEEL = 8
_PICKER_SIZE = 280
_DETAIL_PREVIEW_CHARS = 40


class TargetSlotPicker(QWidget):
    """轮盘下拉 + 可点击轮盘 + 选中槽内容说明。

    与设置页共用 ``WheelPicker``，槽位名称、空槽和选中效果不会两处分叉。
    """

    def __init__(
        self,
        settings: AppSettings,
        parent: QWidget | None = None,
        *,
        on_add_wheel: Callable[[str], None] | None = None,
        default_label: str = "",
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._on_add_wheel = on_add_wheel
        self._selected_slot = 0
        self.target_index = 0

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        wheel_row = QHBoxLayout()
        wheel_row.setContentsMargins(0, 0, 0, 0)
        wheel_row.addWidget(QLabel("保存到"))
        self.wheel_combo: QComboBox = NoWheelComboBox()
        self.wheel_combo.setAccessibleName("选择保存到哪个轮盘")
        self.wheel_combo.setFixedWidth(220)
        wheel_row.addWidget(self.wheel_combo)
        self.add_wheel_button = QPushButton("新增轮盘")
        self.add_wheel_button.clicked.connect(self._add_wheel)
        # 没有轮盘控制器时无法注册快捷键，隐藏入口而不是创建喊不醒的轮盘。
        self.add_wheel_button.setEnabled(on_add_wheel is not None)
        wheel_row.addWidget(self.add_wheel_button)
        wheel_row.addStretch(1)
        layout.addLayout(wheel_row)

        label_row = QHBoxLayout()
        label_row.setContentsMargins(0, 0, 0, 0)
        label_row.addWidget(QLabel("名称"))
        self.label_input = QLineEdit(default_label)
        self.label_input.setPlaceholderText("保存后的槽位名称")
        self.label_input.setAccessibleName("保存后的槽位名称")
        label_row.addWidget(self.label_input, 1)
        layout.addLayout(label_row)

        # 音量属于待保存片段，切换目标时沿用这次编辑值。
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("音量"))
        self.volume_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setAccessibleName("片段音量")
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setValue(100)
        volume_row.addWidget(self.volume_slider, 1)
        self.volume_value_label = QLabel("100%")
        self.volume_value_label.setMinimumWidth(self.volume_value_label.fontMetrics().horizontalAdvance("200%"))
        self.volume_slider.valueChanged.connect(lambda value: self.volume_value_label.setText(f"{value}%"))
        volume_row.addWidget(self.volume_value_label)
        layout.addLayout(volume_row)

        self.picker = WheelPicker(self._select_slot, size=_PICKER_SIZE)
        self.picker.wheel_style = settings.chat_wheel_style
        layout.addWidget(self.picker, 0, Qt.AlignmentFlag.AlignHCenter)

        self.detail_label = QLabel()
        self.detail_label.setWordWrap(True)
        self.detail_label.setAccessibleName("选中槽位内容")
        layout.addWidget(self.detail_label)

        self.wheel_combo.currentIndexChanged.connect(self._wheel_changed)
        self.reload()

    # ── 对外接口 ──────────────────────────────────────────────────────

    def set_index(self, index: int) -> None:
        """按扁平下标（轮盘 * 8 + 槽位）恢复上次目标。"""
        wheel = max(0, min(index // SLOTS_PER_WHEEL, self._settings.wheel_count - 1))
        self._selected_slot = max(0, min(index % SLOTS_PER_WHEEL, SLOTS_PER_WHEEL - 1))
        self._sync_combo()
        self.wheel_combo.setCurrentIndex(wheel)
        self._refresh()

    def selected_target(self) -> tuple[int, int]:
        return self.wheel_combo.currentIndex(), self._selected_slot

    def label(self) -> str:
        """保存时使用的槽位名称；留空由调用方回退默认名称。"""
        return self.label_input.text().strip()

    def volume(self) -> float:
        return self.volume_slider.value() / 100

    def add_wheel_with_hotkey(self, hotkey: str) -> bool:
        """注册新轮盘并选中它；弹窗与测试共用，失败返回 False。"""
        if not callable(self._on_add_wheel):
            return False
        try:
            self._on_add_wheel(hotkey)
        except (ValueError, RuntimeError, OSError) as error:
            QMessageBox.warning(self, "新增轮盘", str(error))
            return False
        self._sync_combo()
        self.wheel_combo.setCurrentIndex(self._settings.wheel_count - 1)
        self._refresh()
        return True

    def reload(self) -> None:
        """轮盘/槽位配置变化后重建下拉与预览。"""
        self._sync_combo()
        self._refresh()

    # ── 内部 ──────────────────────────────────────────────────────────

    def _add_wheel(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("新增轮盘")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("新轮盘快捷键"))
        hotkey = QKeySequenceEdit()
        hotkey.setMaximumSequenceLength(1)
        hotkey.setAccessibleName("新轮盘快捷键")
        layout.addWidget(hotkey)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("新增")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            if dialog.exec() != QDialog.DialogCode.Accepted:
                return
            text = hotkey.keySequence().toString(
                QKeySequence.SequenceFormat.PortableText).lower()
            # Qt 使用缩写名称，Win32 解析器使用 keyboard 风格的完整名称。
            text = text.replace("meta+", "windows+").replace("pgup", "page up").replace(
                "pgdown", "page down")
            if not text:
                QMessageBox.warning(self, "新增轮盘", "请先输入新轮盘的快捷键。")
                return
            self.add_wheel_with_hotkey(text)
        finally:
            dialog.deleteLater()

    def _wheel_names(self) -> list[str]:
        return [self._settings.get_wheel(i)["name"] or f"轮盘 {i + 1}"
                for i in range(self._settings.wheel_count)]

    def _sync_combo(self) -> None:
        wheel = max(0, min(self.wheel_combo.currentIndex(), self._settings.wheel_count - 1))
        self.wheel_combo.blockSignals(True)
        self.wheel_combo.clear()
        self.wheel_combo.addItems(self._wheel_names())
        self.wheel_combo.setCurrentIndex(wheel)
        self.wheel_combo.blockSignals(False)

    def _wheel_changed(self, _index: int) -> None:
        self._refresh()

    def _select_slot(self, index: int) -> None:
        self._selected_slot = index
        self._refresh()

    def _slot_display(self, label: str, text: str, filename: str) -> str:
        text = text.strip()
        if text:
            return label or text
        if filename:
            return label or "音频"
        return "空"

    def _refresh(self) -> None:
        wheel_index = self.wheel_combo.currentIndex()
        self.target_index = wheel_index * SLOTS_PER_WHEEL + self._selected_slot
        config = self._settings.get_wheel(wheel_index)
        for slot, button in enumerate(self.picker.buttons):
            button.setText(self._slot_display(
                config["labels"][slot], config["texts"][slot], config["slots"][slot]))
            button.setToolTip(self._detail(config, slot))
        self.picker.set_selected(self._selected_slot)
        self.detail_label.setText(self._detail(config, self._selected_slot))

    def _detail(self, config: ChatWheelConfig, slot: int) -> str:
        number = f"音频 {slot + 1}"
        text = config["texts"][slot].strip()
        filename = config["slots"][slot]
        if text:
            preview = text[:_DETAIL_PREVIEW_CHARS] + ("…" if len(text) > _DETAIL_PREVIEW_CHARS else "")
            return f"{number} · 原有文字：{preview}（保存将替换）"
        if filename:
            label = config["labels"][slot] or filename
            return f"{number} · 原有音频：{label}（保存将替换）"
        return f"{number} · 空槽"
