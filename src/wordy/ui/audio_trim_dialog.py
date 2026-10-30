"""录音片段选择；确认前不修改槽位或源录音。"""

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QHBoxLayout, QLabel,
    QPushButton, QToolButton, QVBoxLayout, QWidget,
)

from wordy.audio.waveform import waveform_peaks
from wordy.ui.audio_waveform import WaveformSelection
from wordy.audio.trim import recording_duration, trim_recording
from wordy.ui.settings_style import build_settings_stylesheet
from wordy.ui.theme import ELEVATED_BG, SEPARATOR_COLOR, SURFACE_BG, TEXT_ERROR, TEXT_PRIMARY

logger = logging.getLogger(__name__)


class _WaveformSignals(QObject):
    loaded = Signal(object, str)


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
        self.setMinimumWidth(640)
        self.resize(860, 400)
        self._closed = threading.Event()
        self._previewing = False
        self._waveform_signals = _WaveformSignals(self)
        self._waveform_signals.loaded.connect(self._waveform_loaded)
        # 设置窗口的 QDialog 规则透明，独立编辑窗口需要自己的可读底色。
        self.setObjectName("audioTrimDialog")
        self.setStyleSheet(build_settings_stylesheet() + f"""
            QDialog#audioTrimDialog {{ background: {SURFACE_BG}; color: {TEXT_PRIMARY}; }}
            QToolButton {{ background: transparent; border: none; padding: 2px; }}
            QDoubleSpinBox {{ background: {ELEVATED_BG}; color: {TEXT_PRIMARY};
                border: 1px solid {SEPARATOR_COLOR}; border-radius: 6px; padding: 6px; }}
        """)
        duration = self._duration = recording_duration(source)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(14)
        layout.addWidget(QLabel("拖动两端手柄调整范围，或在波形上拖选；空格试听 / 停止。"))
        self.waveform = WaveformSelection(duration)
        layout.addWidget(self.waveform)
        self.loading_label = QLabel("正在加载波形…")
        layout.addWidget(self.loading_label)
        self.range_label = QLabel()
        self.range_label.setAccessibleName("已选片段范围")
        layout.addWidget(self.range_label)
        self.precise_button = QToolButton()
        self.precise_button.setText("精确调整")
        self.precise_button.setCheckable(True)
        self.precise_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.precise_button.setArrowType(Qt.ArrowType.RightArrow)
        layout.addWidget(self.precise_button)
        precise_panel = QWidget()
        self.precise_panel = precise_panel
        precise_panel.setVisible(False)
        form = QFormLayout(precise_panel)
        form.setContentsMargins(0, 0, 0, 0)
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
        layout.addWidget(precise_panel)
        self.precise_button.toggled.connect(precise_panel.setVisible)
        self.precise_button.toggled.connect(lambda shown: self.precise_button.setArrowType(
            Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow))
        self.waveform.selectionChanged.connect(self._waveform_selected)
        self.start_input.valueChanged.connect(self._range_changed)
        self.end_input.valueChanged.connect(self._range_changed)
        self._range_changed()
        self.preview_button = QPushButton("试听片段")
        self.preview_button.setEnabled(play_preview is not None)
        self.preview_button.clicked.connect(self._preview)
        self._space_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self._space_shortcut.activated.connect(self._preview)
        actions = QHBoxLayout()
        actions.addWidget(self.preview_button)
        actions.addStretch(1)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet(f"color: {TEXT_ERROR};")
        layout.addWidget(self.error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("保存片段")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        actions.addWidget(buttons)
        layout.addLayout(actions)
        signals, cancelled = self._waveform_signals, self._closed

        def load() -> None:
            try:
                peaks, error = waveform_peaks(source, cancelled), ""
            except Exception as exc:
                peaks, error = [], f"波形加载失败：{exc}"
            if not cancelled.is_set():
                try:
                    signals.loaded.emit(peaks, error)
                except RuntimeError:
                    pass  # 窗口已销毁，后台结果无需投递。

        threading.Thread(target=load, name="AudioWaveform", daemon=True).start()

    def _waveform_loaded(self, peaks: list[float], error: str) -> None:
        if self._closed.is_set():
            return
        self.waveform.set_peaks(peaks)
        self.loading_label.hide()
        if error:
            self.error_label.setText(error)

    def _waveform_selected(self, start: float, end: float) -> None:
        # 一次更新两个端点，避免拖选时中间状态触发试听或错误区间。
        self.start_input.blockSignals(True)
        self.end_input.blockSignals(True)
        self.start_input.setValue(start)
        self.end_input.setValue(end)
        self.start_input.blockSignals(False)
        self.end_input.blockSignals(False)
        self._range_changed()

    def _range_changed(self) -> None:
        self._stop_selected_preview()
        start, end = self.start_input.value(), self.end_input.value()
        self.waveform.set_selection(start, end)
        self.range_label.setText(f"起点 {start:.3f} 秒    终点 {end:.3f} 秒    片段 {max(0, end - start):.3f} 秒")

    def preview_progress(self, filename: str, seconds: float) -> None:
        if (not self._closed.is_set() and self._previewing and self._preview_paths
                and str(self._preview_paths[-1]) == filename):
            self.waveform.set_playhead(min(self.end_input.value(), self.start_input.value() + seconds))

    def preview_finished(self, filename: str) -> None:
        # 旧片段的结束回调不能复位新片段的试听按钮。
        if self._preview_paths and str(self._preview_paths[-1]) == filename:
            self.set_preview_playing(False)

    def _stop_selected_preview(self) -> None:
        if self._previewing and self._stop_preview is not None:
            self._stop_preview()
        self.set_preview_playing(False)

    def set_preview_playing(self, playing: bool) -> None:
        self._previewing = playing
        if not playing:
            self.waveform.set_playhead(None)
        if hasattr(self, "preview_button"):
            self.preview_button.setText("停止试听" if playing else "试听片段")


    def _selected_clip(self) -> Path:
        end = self.end_input.value()
        # 毫秒显示不能精确表示所有 PCM 帧；选到末尾时保留原录音的最后一帧。
        if end == self.end_input.maximum():
            end = self._duration
        return trim_recording(self._source, self.start_input.value(), end)

    def _preview(self) -> None:
        if self._play_preview is None:
            return
        if self._previewing:
            self._stop_selected_preview()
            return
        try:
            if self._stop_preview is not None:
                self._stop_preview()
            clip = self._selected_clip()
            # worker 可能尚未打开文件，所有试听副本保留到窗口关闭后再清理。
            self._preview_paths.append(clip)
            if self._play_preview is not None:
                self._previewing = True
                self.preview_button.setText("停止试听")
                self._play_preview(clip)
            self.error_label.clear()
        except Exception as error:
            self.set_preview_playing(False)
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
        self._closed.set()
        if self._stop_preview is not None:
            self._stop_preview()
        for path in self._preview_paths:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning("无法删除试听临时文件 %s", path)
        self._preview_paths.clear()
        super().done(result)
