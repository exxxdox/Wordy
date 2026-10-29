"""录音片段选择；确认前不修改槽位或源录音。"""

import logging
from collections.abc import Callable
from pathlib import Path

from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QLabel,
    QPushButton, QVBoxLayout, QWidget,
)

from wordy.audio.trim import recording_duration, trim_recording
from wordy.ui.theme import ELEVATED_BG, SEPARATOR_COLOR, SURFACE_BG, TEXT_ERROR, TEXT_PRIMARY

logger = logging.getLogger(__name__)


class AudioTrimDialog(QDialog):
    def __init__(self, source: Path, parent: QWidget | None = None, *,
                 play_preview: Callable[[Path], None] | None = None,
                 stop_preview: Callable[[], None] | None = None) -> None:
        super().__init__(parent)
        self._source = source
        self._play_preview = play_preview
        self._stop_preview = stop_preview
        self._preview_paths: list[Path] = []
        self._clip: Path | None = None
        self.setWindowTitle("截取录音")
        self.setMinimumWidth(330)
        # 设置窗口的 QDialog 规则透明，独立编辑窗口需要自己的可读底色。
        self.setObjectName("audioTrimDialog")
        self.setStyleSheet(f"""
            QDialog#audioTrimDialog {{ background: {SURFACE_BG}; color: {TEXT_PRIMARY}; }}
            QDoubleSpinBox {{ background: {ELEVATED_BG}; color: {TEXT_PRIMARY};
                border: 1px solid {SEPARATOR_COLOR}; border-radius: 6px; padding: 6px; }}
        """)
        duration = self._duration = recording_duration(source)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel(f"录音时长：{duration:.3f} 秒"))
        form = QFormLayout()
        self.start_input = QDoubleSpinBox()
        self.end_input = QDoubleSpinBox()
        for control, name in ((self.start_input, "开始时间"), (self.end_input, "结束时间")):
            control.setDecimals(3)
            control.setRange(0, duration)
            control.setSingleStep(.1)
            control.setSuffix(" 秒")
            control.setAccessibleName(name)
        self.end_input.setValue(duration)
        form.addRow("开始", self.start_input)
        form.addRow("结束", self.end_input)
        layout.addLayout(form)
        self.preview_button = QPushButton("试听片段")
        self.preview_button.setEnabled(play_preview is not None)
        self.preview_button.clicked.connect(self._preview)
        layout.addWidget(self.preview_button)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {TEXT_ERROR};")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存片段")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _selected_clip(self) -> Path:
        end = self.end_input.value()
        # 毫秒显示不能精确表示所有 PCM 帧；选到末尾时保留原录音的最后一帧。
        if end == self.end_input.maximum():
            end = self._duration
        return trim_recording(self._source, self.start_input.value(), end)

    def _preview(self) -> None:
        try:
            if self._stop_preview is not None:
                self._stop_preview()
            clip = self._selected_clip()
            # worker 可能尚未打开文件，所有试听副本保留到窗口关闭后再清理。
            self._preview_paths.append(clip)
            if self._play_preview is not None:
                self._play_preview(clip)
            self.error_label.clear()
        except Exception as error:
            self.error_label.setText(str(error))

    def accept(self) -> None:
        try:
            self._clip = self._selected_clip()
        except Exception as error:
            self.error_label.setText(str(error))
            return
        super().accept()

    def take_clip(self) -> Path:
        """仅确认成功后调用；将临时片段所有权交给导入链路。"""
        if self._clip is None:
            raise ValueError("尚未保存录音片段")
        clip, self._clip = self._clip, None
        return clip

    def done(self, result: int) -> None:
        if self._stop_preview is not None:
            self._stop_preview()
        for path in self._preview_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("无法删除试听临时文件 %s", path)
        self._preview_paths.clear()
        super().done(result)
