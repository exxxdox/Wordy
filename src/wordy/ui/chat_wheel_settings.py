"""八方向音频轮盘设置。文件导入放到后台，配置在 GUI 线程一次写入。"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QIcon, QKeySequence, QPainter,
    QPixmap, QPolygonF,
)
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QKeySequenceEdit,
    QLabel, QLineEdit, QMessageBox, QProgressBar, QPushButton, QSlider, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from wordy.audio.clips import (
    AUDIO_EXTENSIONS, decoded_audio_clip, import_audio_clip, remove_audio_clip, resolve_audio_clip,
)
from wordy.audio.recorder import MAX_RECORD_SECONDS, RecordingError, SystemAudioRecorder
from wordy.config import AppSettings, CHAT_WHEEL_STYLES, WHEEL_NAME_MAX_LENGTH
from wordy.ui.audio_trim_dialog import AudioTrimDialog
from wordy.ui.status_toast import show_status_toast
from wordy.ui.settings_widgets import NoWheelComboBox
from wordy.ui.wheel_picker import WheelPicker as _WheelPicker
from wordy.ui.theme import (
    GREEN_ACCENT, TEXT_ERROR, TEXT_PRIMARY,
    SURFACE_BG,
)

logger = logging.getLogger(__name__)
# 槽位用数字编号标识：方向名（上/右上…）既指不明确，又和运行时轮盘的方向概念重名。
SLOT_NAMES = tuple(f"音频 {index}" for index in range(1, 9))
RECORD_TICK_MS = 200


def _action_icon(shape: str, color: str) -> QIcon:
    """动作按钮的小图标：QSS 的 color 不会给 QIcon 着色，只能自绘定色。"""
    pixmap = QPixmap(14, 14)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(color))
    if shape == "play":
        painter.drawPolygon(QPolygonF([QPointF(2.5, 1.5), QPointF(12, 7), QPointF(2.5, 12.5)]))
    elif shape == "stop":
        painter.drawRoundedRect(QRectF(2.5, 2.5, 9, 9), 1.5, 1.5)
    else:
        painter.drawEllipse(QPointF(7, 7), 5, 5)
    painter.end()
    return QIcon(pixmap)


class _ImportSignals(QObject):
    # index, filename, label, error, 录音临时文件路径（非录音导入为空串）
    finished = Signal(int, str, str, str, str)
    preview_finished = Signal(str)
    preview_progress = Signal(str, float)


class ChatWheelSettings(QWidget):
    def __init__(self, settings: AppSettings,
                 on_field_changed: Callable[[str, object], bool | None]):
        super().__init__()
        self._settings = settings
        self._on_field_changed = on_field_changed
        self._busy: set[int] = set()
        self._closed = threading.Event()
        # 录制独占面板：开始后锁住槽切换与其他编辑动作，录音只落在开始时的槽。
        self._recording: int | None = None
        self._recorder: SystemAudioRecorder | None = None
        # 试听是否正在播放：由主程序在播放结束时回调复位。
        self._previewing = False
        # 正在朗读文字（而非播放音频）。朗读可打断，按钮显示「停止」。
        self._preview_speech = False
        # 空槽没有内容可推断类型，用用户最后一次选择的模式；有内容时类型由内容决定。
        self._empty_mode = "audio"
        self._record_timer = QTimer(self)
        self._record_timer.setInterval(RECORD_TICK_MS)
        self._record_timer.timeout.connect(self._tick_recording)
        self._signals = _ImportSignals(self)
        self._signals.finished.connect(self._finish_import)
        self._signals.preview_finished.connect(self._trim_preview_finished)
        self._signals.preview_progress.connect(self._trim_preview_progress)
        self.selected_index = 0
        self.wheel_index = 0
        # 截取前的解码会走嵌套事件循环，期间用它锁住动作行防止重入。
        self._trim_dialog: AudioTrimDialog | None = None
        self._trimming = False

        layout = QVBoxLayout(self)
        # 父级页签已有标题和阅读边距，面板不重复增加外边距。
        layout.setContentsMargins(0, 0, 0, 0)
        # 操作指引已放在页标题下，避免重复占高挤出纵向滚动条。
        layout.setSpacing(12)

        hotkey_row = QHBoxLayout()
        # 轮盘管理独立一行，预览与槽位编辑在下方并列。
        hotkey_row.setSpacing(12)
        hotkey_row.addWidget(QLabel("轮盘"))
        self.wheel_combo = QComboBox()
        self.wheel_combo.setAccessibleName("选择或重命名轮盘")
        # 可编辑下拉框即重命名入口；NoInsert 防止回车把临时输入变成新条目。
        self.wheel_combo.setEditable(True)
        self.wheel_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.wheel_combo.setSizeAdjustPolicy(
            QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.wheel_combo.setMinimumContentsLength(10)
        edit = self.wheel_combo.lineEdit()
        if edit is not None:
            edit.setMaxLength(WHEEL_NAME_MAX_LENGTH)
            edit.editingFinished.connect(self._rename_wheel)
        self.wheel_combo.currentIndexChanged.connect(self._select_wheel)
        self.wheel_combo.setFixedWidth(220)
        hotkey_row.addWidget(self.wheel_combo)
        self.hotkey_input = QKeySequenceEdit(QKeySequence(settings.chat_wheel_hotkey))
        self.hotkey_input.setMaximumSequenceLength(1)
        self.hotkey_input.setAccessibleName("轮盘全局快捷键")
        # 与其他设置页一样自动保存：编辑结束（按键组合完成或失焦）即提交。
        self.hotkey_input.editingFinished.connect(self._apply_hotkey)
        hotkey_row.addWidget(QLabel("快捷键"))
        self.hotkey_input.setFixedWidth(160)
        hotkey_row.addWidget(self.hotkey_input)
        self.add_wheel_button = QPushButton("新增")
        self.add_wheel_button.clicked.connect(self._add_wheel)
        hotkey_row.addWidget(self.add_wheel_button)
        self.remove_wheel_button = QPushButton("删除")
        self.remove_wheel_button.clicked.connect(self._remove_wheel)
        hotkey_row.addWidget(self.remove_wheel_button)
        hotkey_row.addStretch(1)
        hotkey_row.addWidget(QLabel("样式"))
        self.style_combo = NoWheelComboBox()
        self.style_combo.setAccessibleName("轮盘样式")
        self.style_combo.setToolTip("所有轮盘共用此样式")
        # 新外观追加到末尾，保留既有样式顺序和用户已保存的选择。
        for style, title in zip(CHAT_WHEEL_STYLES, ("经典玻璃", "轻薄玻璃", "悬浮花瓣", "竞技轮盘")):
            self.style_combo.addItem(title, style)
        self.style_combo.setFixedWidth(136)
        self.style_combo.setCurrentIndex(self.style_combo.findData(settings.chat_wheel_style))
        hotkey_row.addWidget(self.style_combo)
        layout.addLayout(hotkey_row)

        self.wheel_picker = _WheelPicker(self._select_slot)
        self.wheel_picker.wheel_style = settings.chat_wheel_style
        # 初始赋值后再监听，打开设置不会落盘；切换只改外观，不重建槽位控件。
        self.style_combo.currentIndexChanged.connect(self._select_style)
        self.slot_buttons = self.wheel_picker.buttons
        # 预览保持运行轮盘的材质与比例，编辑区利用新增宽度独立呈现字段。
        body = QHBoxLayout()
        body.setSpacing(32)
        body.addWidget(self.wheel_picker, 0, Qt.AlignmentFlag.AlignTop)
        editor = QWidget()
        editor.setObjectName("wheelEditor")
        editor_layout = QVBoxLayout(editor)
        editor_layout.setContentsMargins(0, 12, 0, 0)
        editor_layout.setSpacing(12)
        body.addWidget(editor, 1)
        layout.addLayout(body)

        self.editor_title = QLabel()
        self.editor_title.setObjectName("sectionTitle")
        editor_layout.addWidget(self.editor_title)
        # 槽内容二选一：类型由内容决定，选择器把它显式化，切换即换内容类型。
        mode_row = QHBoxLayout()
        mode_row.setSpacing(6)
        self.audio_mode_button = QPushButton("音频")
        self.text_mode_button = QPushButton("文字")
        for mode_button in (self.audio_mode_button, self.text_mode_button):
            mode_button.setCheckable(True)
            mode_button.setObjectName("slotModeButton")
        self.audio_mode_button.clicked.connect(lambda: self._switch_slot_mode("audio"))
        self.text_mode_button.clicked.connect(lambda: self._switch_slot_mode("text"))
        mode_row.addWidget(QLabel("内容"))
        mode_row.addWidget(self.audio_mode_button)
        mode_row.addWidget(self.text_mode_button)
        mode_row.addStretch(1)
        editor_layout.addLayout(mode_row)
        # 音量独立成行，名称与朗读文字各占一行，避免宽屏下字段挤在一起。
        editor_row = QHBoxLayout()
        self.volume_label = QLabel("音量")
        editor_row.addWidget(self.volume_label)
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setMinimumWidth(120)
        self.volume_slider.setMaximumWidth(280)
        self.volume_slider.setToolTip("音量（下次播放生效）")
        self.volume_slider.valueChanged.connect(self._volume_changed)
        self.volume_slider.sliderReleased.connect(self._save_volume)
        editor_row.addWidget(self.volume_slider, 1)
        self.volume_value_label = QLabel("100%")
        self.volume_value_label.setMinimumWidth(42)
        editor_row.addWidget(self.volume_value_label)
        editor_row.addStretch(1)
        editor_layout.addSpacing(4)
        editor_layout.addLayout(editor_row)
        editor_layout.addWidget(QLabel("名称"))
        self.label_input = QLineEdit()
        # 名称与朗读文字共用右边线，让编辑区保持完整的表单轮廓。
        self.label_input.setMaximumWidth(520)
        self.label_input.setPlaceholderText("名称")
        self.label_input.setMaxLength(40)
        self.label_input.editingFinished.connect(self._save_label)
        editor_layout.addWidget(self.label_input)
        self.text_label = QLabel("朗读文字")
        editor_layout.addWidget(self.text_label)
        self.text_input = QLineEdit()
        self.text_input.setMaximumWidth(520)
        self.text_input.setPlaceholderText("输入要朗读的文字")
        # 朗读走单 worker 执行器，太长会拖住后续播放与轮盘（可打断但会占住队列）。
        self.text_input.setMaxLength(200)
        self.text_input.editingFinished.connect(self._save_text)
        editor_layout.addWidget(self.text_input)
        editor_layout.addSpacing(8)
        action_row = QHBoxLayout()
        action_row.setSpacing(8)
        self.choose_button = QPushButton("导入")
        self.choose_button.clicked.connect(lambda: self._choose_file(self.selected_index))
        action_row.addWidget(self.choose_button)
        # 动作按钮保持同一行，便于在选槽后直接操作。
        self.record_button = QPushButton("录制")
        self.record_button.setIconSize(QSize(14, 14))
        self.record_button.clicked.connect(self._toggle_record)
        action_row.addWidget(self.record_button)
        self.preview_button = QPushButton("试听")
        self.preview_button.setIconSize(QSize(14, 14))
        self.preview_button.clicked.connect(self._toggle_preview)
        action_row.addWidget(self.preview_button)
        # 截取作用于已保存的音频，不再和录制流程绑定，随时可重裁。
        self.trim_button = QPushButton("截取")
        self.trim_button.setIconSize(QSize(14, 14))
        self.trim_button.clicked.connect(self._trim_slot)
        action_row.addWidget(self.trim_button)
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(
            lambda: self._replace_clip(self.selected_index, "", ""))
        action_row.addWidget(self.clear_button)
        action_row.addStretch(1)
        editor_layout.addLayout(action_row)
        # 压缩音频解码期间用不确定进度条表态；平时隐藏，不占纵向空间。
        self.decode_bar = QProgressBar()
        self.decode_bar.setRange(0, 0)
        self.decode_bar.setTextVisible(False)
        self.decode_bar.setFixedHeight(4)
        self.decode_bar.setAccessibleName("正在准备截取")
        self.decode_bar.setVisible(False)
        editor_layout.addWidget(self.decode_bar)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("轮盘设置状态")
        # 反馈优先走设置窗口的浮动弹窗，原地标签仅在面板独立使用时出现。
        self.status_label.setVisible(False)
        editor_layout.addWidget(self.status_label)
        editor_layout.addStretch()
        layout.addStretch()
        self._reload_wheels()
        for index in range(8):
            self._refresh_slot(index)
        self._select_slot(0)

    def mark_closed(self) -> None:
        """设置窗口真正关闭后调用；被拒绝的关闭不能提前丢弃后续导入。"""
        self._closed.set()
        # 采集线程独立于窗口，窗口销毁前不收掉会留下采集流和临时文件。
        self._cancel_recording()

    def _status(self, text: str, *, error: bool = False) -> None:
        color = TEXT_ERROR if error else GREEN_ACCENT
        # 宿主窗口存在时用浮动弹窗，反馈不再长期停在页面固定位置。
        if show_status_toast(self, text, color):
            return
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")
        self.status_label.setVisible(True)

    def _hotkey_text(self, control: QKeySequenceEdit | None = None) -> str:
        text = (control or self.hotkey_input).keySequence().toString(QKeySequence.SequenceFormat.PortableText).lower()
        # Qt 使用缩写名称，Win32 解析器使用 keyboard 风格的完整名称。
        text = text.replace("meta+", "windows+").replace("pgup", "page up").replace("pgdown", "page down")
        return text

    def _wheel_title(self, index: int) -> str:
        """空名称回退「轮盘 N」，保证下拉框始终有可读标题。"""
        return self._settings.get_wheel(index)["name"] or f"轮盘 {index + 1}"

    def _select_style(self, index: int) -> None:
        style = self.style_combo.itemData(index)
        if style not in CHAT_WHEEL_STYLES:
            return
        try:
            # 先原子保存再换预览，失败时不能让显示与下次唤出的样式分叉。
            self._settings.update(chat_wheel_style=style)
        except Exception as error:
            self.style_combo.blockSignals(True)
            self.style_combo.setCurrentIndex(self.style_combo.findData(self._settings.chat_wheel_style))
            self.style_combo.blockSignals(False)
            self._status(f"保存失败：{error}", error=True)
            return
        self.wheel_picker.wheel_style = self._settings.chat_wheel_style
        self.wheel_picker.update()
        self._on_field_changed("chat_wheel_style", self._settings.chat_wheel_style)

    def _reload_wheels(self) -> None:
        self.wheel_index = min(self.wheel_index, self._settings.wheel_count - 1)
        self.wheel_combo.blockSignals(True)
        self.wheel_combo.clear()
        self.wheel_combo.addItems([self._wheel_title(i) for i in range(self._settings.wheel_count)])
        self.wheel_combo.setCurrentIndex(self.wheel_index)
        self.wheel_combo.blockSignals(False)
        self.hotkey_input.setKeySequence(QKeySequence(self._settings.get_wheel(self.wheel_index)["hotkey"]))
        self.remove_wheel_button.setEnabled(self._settings.wheel_count > 1)

    def _rename_wheel(self) -> None:
        """下拉框文本即自定义名称；只在编辑结束时落盘，空名回退默认标题。"""
        edit = self.wheel_combo.lineEdit()
        if edit is None or self._busy or self._recording is not None or self._preview_speech:
            return
        name = edit.text().strip()[:WHEEL_NAME_MAX_LENGTH]
        if name != self._settings.get_wheel(self.wheel_index)["name"]:
            try:
                self._settings.update_wheel(self.wheel_index, name=name)
            except Exception as error:
                self._status(f"保存失败：{error}", error=True)
        title = self._wheel_title(self.wheel_index)
        # 逐项刷新而不是重建列表：避免重命名时冲掉用户正要点击的轮盘选择。
        self.wheel_combo.setItemText(self.wheel_index, title)
        self.wheel_combo.setEditText(title)

    def _select_wheel(self, index: int) -> None:
        # 导入回调只携带槽号；处理完成前锁住轮盘，防止结果写进另一盘。
        if index < 0 or self._busy or self._recording is not None or self._preview_speech:
            return
        self._stop_preview()
        self.wheel_index = index
        self._reload_wheels()
        self._refresh_all_slots()
        self._select_slot(0)

    def _add_wheel(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("新增轮盘")
        dialog.setObjectName("newWheelDialog")
        dialog.setStyleSheet(f"QDialog#newWheelDialog {{ background: {SURFACE_BG}; }}")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("新轮盘快捷键"))
        hotkey = QKeySequenceEdit()
        hotkey.setMaximumSequenceLength(1)
        hotkey.setAccessibleName("新轮盘快捷键")
        layout.addWidget(hotkey)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setText("新增")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                text = self._hotkey_text(hotkey)
                if text:
                    self._on_field_changed("chat_wheel_add", text)
                else:
                    self._status("请先输入新轮盘的快捷键。", error=True)
        finally:
            dialog.deleteLater()

    def _remove_wheel(self) -> None:
        if QMessageBox.question(self, "删除轮盘", "删除此轮盘及其八个槽位？") != QMessageBox.StandardButton.Yes:
            return
        self._stop_preview()
        self._on_field_changed("chat_wheel_remove", self.wheel_index)

    def set_wheel_result(self, error: str | None) -> None:
        if not error:
            self._reload_wheels()
            self._refresh_all_slots()
            self._select_slot(0)
        self._status(error or "轮盘已更新。", error=bool(error))

    def _apply_hotkey(self) -> None:
        text = self._hotkey_text()
        stored = str(self._settings.get_wheel(self.wheel_index)["hotkey"]).lower()
        if not text:
            # 空序列不是合法快捷键：恢复已存值，别把空值写进配置后唤不醒轮盘。
            self.hotkey_input.setKeySequence(self._settings.get_wheel(self.wheel_index)["hotkey"])
            return
        # 失焦也会触发 editingFinished，值没变就不再重复注册。
        if text == stored:
            return
        # 注册成功才保存，避免占用冲突导致下次启动也无法唤醒。
        try:
            self._on_field_changed("chat_wheel_hotkey", (self.wheel_index, text) if self.wheel_index else text)
        except Exception as error:
            self.set_hotkey_result(str(error))

    def set_hotkey_result(self, error: str | None) -> None:
        if error:
            # 注册失败保留旧配置，输入框同步回退，避免显示的组合并未生效。
            self.hotkey_input.setKeySequence(self._settings.get_wheel(self.wheel_index)["hotkey"])
        self._status(error or "快捷键已更新。", error=bool(error))

    def _choose_file(self, index: int) -> None:
        patterns = " ".join(f"*{extension}" for extension in sorted(AUDIO_EXTENSIONS))
        filename, _ = QFileDialog.getOpenFileName(self, "选择轮盘音频", "", f"音频文件 ({patterns})")
        if filename:
            self.import_file(index, Path(filename))

    def import_file(self, index: int, source: Path, label: str | None = None,
                    temporary: bool = False) -> None:
        """把一个音频文件导入槽位；``label`` 缺省沿用文件名，``temporary`` 用于录音副本。"""
        if index in self._busy:
            return
        self._busy.add(index)
        self._refresh_all_slots()
        self._status(f"正在导入{SLOT_NAMES[index]}…")
        config_file = self._settings._config_file
        signals = self._signals
        slot_label = label or source.stem

        def run() -> None:
            filename, error = "", ""
            imported: Path | None = None
            try:
                imported = import_audio_clip(source, config_file)
                # 替换旧槽前先完整验证解码，失败时旧音频仍保持可用。
                if imported.is_file():
                    with decoded_audio_clip(imported):
                        pass
                filename = imported.name
            except Exception as exc:
                error = str(exc)
                if imported is not None:
                    self._remove_unused(imported.name, config_file)
            # 录音临时文件的删除时机由 _finish_import 决定：只有内容既进了托管
            # 目录、槽位又落了盘，才谈得上安全回收。
            try:
                if not self._closed.is_set() and isValid(signals):
                    signals.finished.emit(index, filename, slot_label, error,
                                          str(source) if temporary else "")
                    return
            except RuntimeError:
                pass
            # 设置窗口关闭后 QObject 可能已销毁，丢弃结果并回收未使用文件。
            if filename:
                self._remove_unused(filename, config_file)
            if temporary:
                self._keep_temporary(str(source), "设置窗口已关闭，录音未保存")

        threading.Thread(target=run, name="ChatWheelImport", daemon=True).start()

    def _toggle_preview(self) -> None:
        if self._previewing:
            self._stop_preview()
            return
        index = self.selected_index
        text = self._settings.get_wheel(self.wheel_index)["texts"][index].strip()
        if not (text or self._settings.get_wheel(self.wheel_index)["slots"][index]):
            return
        self._previewing = True
        self._preview_speech = bool(text)
        self._refresh_all_slots()
        self._on_field_changed("chat_wheel_preview", (self.wheel_index, index) if self.wheel_index else index)

    def _stop_preview(self) -> None:
        if not self._previewing:
            return
        if self._preview_speech:
            # 朗读已可打断：发同一个停止信号，由主程序置取消位。
            # 面板状态仍锁定到完成回调——回调不带槽号，提前放开会把新试听误复位。
            self._on_field_changed("chat_wheel_preview_stop", self.selected_index)
            return
        self._previewing = False
        self._refresh_all_slots()
        self._on_field_changed("chat_wheel_preview_stop", self.selected_index)

    def set_preview_playing(self, playing: bool) -> None:
        """播放结束时由主程序回调复位按钮；重复通知是幂等的。"""
        if self._previewing == playing and not self._preview_speech:
            return
        self._previewing = playing
        self._preview_speech = False
        self._refresh_all_slots()

    def _toggle_record(self) -> None:
        if self._recording is not None:
            self._finish_recording()
            return
        index = self.selected_index
        if self._preview_speech:
            # 朗读停不下来，硬录会把 TTS 一起录进片段。
            self._status("朗读中，请等朗读结束再录制。", error=True)
            return
        # 录音采的是系统声音，正在试听的片段会被一起录进去，先停掉。
        self._stop_preview()
        # 首次点击才构造录音器：面板构造期不加载 pyaudiowpatch，预热窗口也不受影响。
        recorder = SystemAudioRecorder()
        try:
            recorder.start()
        except RecordingError as error:
            self._status(str(error), error=True)
            return
        self._recorder = recorder
        self._recording = index
        self._record_timer.start()
        self._refresh_all_slots()

    def _finish_recording(self) -> None:
        index, recorder = self._recording, self._recorder
        self._record_timer.stop()
        self._recording = None
        self._recorder = None
        self._refresh_all_slots()
        if index is None or recorder is None:
            return
        try:
            path = recorder.stop()
        except Exception as error:  # noqa: BLE001 - 兜底，异常不能静默穿出 Qt 槽
            logger.exception("结束录音失败")
            self._status(f"录音失败：{error}", error=True)
            return
        # 录完先整段落盘；需要裁剪时再用「截取」按钮，不再强制中断录制流程。
        self.import_file(index, path, label=f"录音 {datetime.now():%H%M%S}", temporary=True)

    def _trim_slot(self) -> None:
        """截取当前槽位已保存的音频；结果替换该槽，取消则保持原样。"""
        index = self.selected_index
        filename = self._settings.get_wheel(self.wheel_index)["slots"][index]
        if not filename or self._trimming:
            return
        # 截取窗口会试听片段，先停掉正在播放的试听。
        self._stop_preview()
        # 压缩音频解码会短暂占用 GUI 线程（嵌套事件循环），期间显示进度条并锁住动作行。
        self._trimming = True
        self.decode_bar.setVisible(True)
        self._refresh_all_slots()
        try:
            source = resolve_audio_clip(filename, self._settings._config_file)
            with decoded_audio_clip(source) as decoded:
                self.decode_bar.setVisible(False)
                clip = self._open_trim_dialog(decoded)
        except Exception as error:
            self._status(f"截取失败：{error}", error=True)
            return
        finally:
            self.decode_bar.setVisible(False)
            self._trimming = False
            self._refresh_all_slots()
        if clip is None:
            return
        label = self._settings.get_wheel(self.wheel_index)["labels"][index]
        self.import_file(index, clip, label=label or filename, temporary=True)

    def _trim_preview_progress(self, filename: str, seconds: float) -> None:
        if self._trim_dialog is not None:
            self._trim_dialog.preview_progress(filename, seconds)

    def _trim_preview_finished(self, filename: str) -> None:
        if self._trim_dialog is not None:
            self._trim_dialog.preview_finished(filename)

    def _open_trim_dialog(self, path: Path) -> Path | None:
        volume = self._settings.get_wheel(self.wheel_index)["volumes"][self.selected_index]

        def play(clip: Path) -> None:
            signals = self._signals

            def finished() -> None:
                try:
                    signals.preview_finished.emit(str(clip))
                except RuntimeError:
                    pass  # 设置窗口已销毁，无需再复位试听控件。

            def progress(seconds: float) -> None:
                try:
                    signals.preview_progress.emit(str(clip), seconds)
                except RuntimeError:
                    pass  # 窗口销毁后忽略迟到的音频进度。

            self._on_field_changed("chat_wheel_preview_file", (str(clip), volume, finished, progress))

        def stop() -> None:
            self._on_field_changed("chat_wheel_preview_stop", 0)

        dialog = AudioTrimDialog(
            path, self, play_preview=play, stop_preview=stop,
        )
        self._trim_dialog = dialog
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                return dialog.take_clip()
            return None
        finally:
            self._trim_dialog = None
            dialog.deleteLater()

    def _cancel_recording(self) -> None:
        """丢弃进行中的录音；采集线程不碰 Qt，可以在这里安全收尾。"""
        self._record_timer.stop()
        recorder, self._recorder = self._recorder, None
        self._recording = None
        if recorder is not None:
            recorder.cancel()

    def _tick_recording(self) -> None:
        recorder = self._recorder
        if self._recording is None or recorder is None:
            self._record_timer.stop()
            return
        # 到达上限时走与手动停止同一条收尾路径，避免两套结束逻辑。
        if recorder.elapsed_seconds >= MAX_RECORD_SECONDS:
            self._finish_recording()
            return
        self._refresh_record_button()

    def _refresh_record_button(self) -> None:
        recording = self._recording
        recorder = self._recorder
        elapsed = int(recorder.elapsed_seconds) if recording is not None and recorder else 0
        text = f"停止 {elapsed}s" if recording is not None else "录制"
        self.record_button.setText(text)
        self.record_button.setIcon(
            _action_icon("stop" if recording is not None else "record", TEXT_ERROR))
        slot_name = SLOT_NAMES[recording if recording is not None else self.selected_index]
        self.record_button.setAccessibleName(
            f"停止录制{slot_name}" if recording else f"录制{slot_name}"
        )

    def _refresh_all_slots(self) -> None:
        locked = bool(self._busy) or self._recording is not None or self._preview_speech
        self.wheel_combo.setEnabled(not locked)
        self.add_wheel_button.setEnabled(not locked)
        self.remove_wheel_button.setEnabled(not locked and self._settings.wheel_count > 1)
        self.hotkey_input.setEnabled(not locked)
        for slot in range(8):
            self._refresh_slot(slot)

    def _finish_import(self, index: int, filename: str, label: str, error: str,
                       temporary_source: str = "") -> None:
        self._busy.discard(index)
        self._refresh_all_slots()
        if self._closed.is_set():
            if filename:
                self._remove_unused(filename, self._settings._config_file)
            # 槽位尚未落盘时，临时录音仍是唯一副本，关窗也不能销毁。
            self._keep_temporary(temporary_source, "设置窗口已关闭，录音未保存")
            return
        if error:
            self._status(f"导入失败：{error}", error=True)
            self._refresh_slot(index)
            self._keep_temporary(temporary_source, "导入失败")
            return
        if not self._replace_clip(index, filename, label):
            self._remove_unused(filename, self._settings._config_file)
            self._keep_temporary(temporary_source, "槽位保存失败")
            return
        self._discard_temporary(temporary_source)

    @staticmethod
    def _discard_temporary(path: str) -> None:
        """只在录音已安全落入托管目录、槽位也已落盘时调用。"""
        if not path:
            return
        try:
            Path(path).unlink(missing_ok=True)
        except OSError:
            logger.warning("无法删除录音临时文件 %s", path)

    @staticmethod
    def _keep_temporary(path: str, reason: str) -> None:
        """失败路径下录音临时文件是唯一副本：保留并记下路径，不静默销毁。"""
        if path:
            logger.warning("%s，录音临时文件保留在 %s", reason, path)

    def _replace_clip(self, index: int, filename: str, label: str, text: str = "") -> bool:
        """写入一个槽的全部内容：音频文件名、名称、朗读文字互斥共存。"""
        # 被替换/清空的片段可能正在试听，先停掉再动文件。
        self._stop_preview()
        old_slots = list(self._settings.get_wheel(self.wheel_index)["slots"])
        old_labels = list(self._settings.get_wheel(self.wheel_index)["labels"])
        old_texts = list(self._settings.get_wheel(self.wheel_index)["texts"])
        slots, labels, texts = list(old_slots), list(old_labels), list(old_texts)
        slots[index], labels[index], texts[index] = filename, label[:40], text[:200]
        try:
            self._settings.update_wheel(self.wheel_index, slots=slots, labels=labels, texts=texts)
        except Exception as error:
            self._status(f"保存失败：{error}", error=True)
            self._refresh_slot(index)
            return False
        self._refresh_slot(index)
        # 清空时两种内容都没了，说「音频已清空」在文字槽切换类型时会误导。
        self._status("文字已保存。" if text else "音频已保存。" if filename else "槽位已清空。")
        old = old_slots[index]
        if old and not any(old in self._settings.get_wheel(i)["slots"]
                           for i in range(self._settings.wheel_count)):
            self._remove_unused(old, self._settings._config_file)
        return True

    @staticmethod
    def _remove_unused(filename: str, config_file: Path | None) -> None:
        try:
            remove_audio_clip(filename, config_file)
        except (OSError, ValueError):
            logger.warning("无法清理轮盘音频 %s", filename, exc_info=True)

    def _save_label(self) -> None:
        index = self.selected_index
        old_labels = list(self._settings.get_wheel(self.wheel_index)["labels"])
        labels = list(old_labels)
        text = self.label_input.text().strip()
        if labels[index] == text:
            return
        labels[index] = text
        try:
            self._settings.update_wheel(self.wheel_index, labels=labels)
        except Exception as error:
            self.label_input.setText(old_labels[index])
            self._status(f"保存失败：{error}", error=True)
            return
        self._refresh_slot(index)

    def _save_text(self) -> None:
        """把该槽内容换成一段朗读文字（文件名一并清空，与「清空」同一语义）。"""
        index = self.selected_index
        # 切到音频模式会隐藏文字框，隐藏引发的 editingFinished 不能把刚换上的音频又清掉。
        if self.text_input.isHidden():
            return
        text = self.text_input.text().strip()[:200]
        # 失焦也会触发本回调；内容没变就直接返回，否则点一下输入框再点走就会删掉音频。
        if self._settings.get_wheel(self.wheel_index)["texts"][index] == text:
            return
        # 名称沿用已存的值：输入框里可能还有没提交的编辑。
        self._replace_clip(index, "", self._settings.get_wheel(self.wheel_index)["labels"][index], text)

    def _volume_changed(self, value: int) -> None:
        self.volume_value_label.setText(f"{value}%")
        # 拖动只更新显示，释放后保存一次；键盘和点击轨道的调整立即保存。
        if not self.volume_slider.isSliderDown():
            self._save_volume()

    def _save_volume(self) -> None:
        config = self._settings.get_wheel(self.wheel_index)
        index = self.selected_index
        if not config["slots"][index] or config["texts"][index] or self._recording is not None or index in self._busy:
            return
        volumes = list(config["volumes"])
        volume = self.volume_slider.value() / 100
        if volumes[index] == volume:
            return
        volumes[index] = volume
        try:
            self._settings.update_wheel(self.wheel_index, volumes=volumes)
        except Exception as error:
            self._refresh_slot(index)
            self._status(f"保存失败：{error}", error=True)

    def _slot_mode(self, index: int) -> str:
        """槽内容类型：有内容时由内容决定，空槽跟随用户最后选择的模式。"""
        config = self._settings.get_wheel(self.wheel_index)
        if config["texts"][index].strip():
            return "text"
        if config["slots"][index]:
            return "audio"
        return self._empty_mode

    def _switch_slot_mode(self, mode: str) -> None:
        """切换槽的内容类型；一个槽只存一种内容，换类型必须清掉另一类。

        音频是录制/导入的产物，删了找不回，有音频时先确认；文字短且可重打，
        按「导入音频清空文字」的既有语义直接清掉。
        """
        index = self.selected_index
        if self._slot_mode(index) == mode:
            self._refresh_slot(index)  # 点已选中的可勾选按钮会取消勾选，刷回来
            return
        config = self._settings.get_wheel(self.wheel_index)
        if mode == "text" and config["slots"][index]:
            confirmed = QMessageBox.question(self, "切换为文字", "切换为文字会删除该槽音频，继续？")
            if confirmed != QMessageBox.StandardButton.Yes:
                self._refresh_slot(index)
                return
        has_content = bool(config["slots"][index] or config["texts"][index].strip())
        self._empty_mode = mode
        if has_content:
            if not self._replace_clip(index, "", config["labels"][index], ""):
                return
        else:
            self._refresh_slot(index)
        if mode == "text":
            self.text_input.setFocus()

    def _select_slot(self, index: int) -> None:
        self.selected_index = index
        # 空槽的编辑意图跟着槽走，切槽后回到默认的音频模式。
        self._empty_mode = "audio"
        for button_index, button in enumerate(self.slot_buttons):
            selected = button_index == index
            button.setChecked(selected)
            button.setProperty("selected", selected)
            button.style().unpolish(button)
            button.style().polish(button)
        self.wheel_picker.update()
        self.editor_title.setText(SLOT_NAMES[index])
        self.label_input.setAccessibleName(f"{SLOT_NAMES[index]}名称")
        self.text_input.setAccessibleName(f"{SLOT_NAMES[index]}朗读文字")
        self.choose_button.setAccessibleName(f"选择{SLOT_NAMES[index]}")
        self.clear_button.setAccessibleName(f"清空{SLOT_NAMES[index]}")
        self.volume_slider.setAccessibleName(f"{SLOT_NAMES[index]}音量")
        self._refresh_slot(index)

    def _refresh_slot(self, index: int) -> None:
        recording = index == self._recording
        # 录制或截取解码期间锁住全部槽位：录音只能落在开始时的那个槽。
        busy = index in self._busy or self._recording is not None or self._trimming
        text = self._settings.get_wheel(self.wheel_index)["texts"][index].strip()
        filled = bool(self._settings.get_wheel(self.wheel_index)["slots"][index] or text)
        label = self._settings.get_wheel(self.wheel_index)["labels"][index].strip()
        self.slot_buttons[index].setText(label or text or "未设置")
        self.slot_buttons[index].setEnabled(not busy)
        if index != self.selected_index:
            return
        self.label_input.setText(label)
        self.text_input.setText(text)
        # 内容类型决定显示哪一套编辑控件；两种入口同时出现正是「分不清槽类型」的来源。
        is_text = self._slot_mode(index) == "text"
        self.volume_label.setVisible(not is_text)
        self.volume_slider.setVisible(not is_text)
        self.volume_value_label.setVisible(not is_text)
        self.text_label.setVisible(is_text)
        self.text_input.setVisible(is_text)
        # 切换类型会清空一类内容，忙碌或朗读中都不允许。
        for mode_button in (self.audio_mode_button, self.text_mode_button):
            mode_button.setEnabled(not busy and not self._preview_speech)
        self.audio_mode_button.setChecked(not is_text)
        self.text_mode_button.setChecked(is_text)
        # 播放结束也会刷新面板，不能覆盖尚未释放滑块的待保存选择。
        if not self.volume_slider.isSliderDown():
            self.volume_slider.blockSignals(True)
            volume_percent = round(self._settings.get_wheel(self.wheel_index)["volumes"][index] * 100)
            self.volume_slider.setValue(volume_percent)
            self.volume_slider.blockSignals(False)
            self.volume_value_label.setText(f"{volume_percent}%")
        # 只有音频槽能调音量、截取；文字槽走 TTS 音量。
        audio_available = filled and not text and not busy and not self._preview_speech
        self.volume_slider.setEnabled(audio_available)
        # 截取窗口会试听片段，进入时先停掉正在播放的试听，所以播放中也可点。
        self.trim_button.setEnabled(audio_available)
        self.trim_button.setAccessibleName(f"截取{SLOT_NAMES[index]}")
        self.choose_button.setEnabled(not busy)
        self.choose_button.setText(
            "截取中…" if self._trimming else
            "录制中…" if recording else
            "导入中…" if busy else "导入"
        )
        # 导入/录制/截取只属于音频槽；文字槽只保留试听与清空。
        for audio_button in (self.choose_button, self.record_button, self.trim_button):
            audio_button.setVisible(not is_text)
        self.record_button.setEnabled(recording or not busy)
        self._refresh_record_button()
        if self._preview_speech:
            # 朗读可打断，按钮就是「停止」；其余槽位仍锁到完成回调，
            # 否则回调会把之后新开的试听误复位。
            self.preview_button.setEnabled(True)
            self.preview_button.setText("停止")
            self.preview_button.setIcon(_action_icon("stop", TEXT_PRIMARY))
            self.preview_button.setAccessibleName(f"停止朗读{SLOT_NAMES[index]}")
        else:
            # 试听中按钮必须始终可点，否则一旦别的槽忙起来「停止」就点不动了。
            self.preview_button.setEnabled((filled and not busy) or self._previewing)
            self.preview_button.setText("停止" if self._previewing else "试听")
            # 播放中换成停止方块，和「停止」文案一起表意。
            self.preview_button.setIcon(
                _action_icon("stop" if self._previewing else "play",
                             TEXT_PRIMARY if self._previewing else GREEN_ACCENT))
            self.preview_button.setAccessibleName(
                f"停止试听{SLOT_NAMES[index]}" if self._previewing else f"试听{SLOT_NAMES[index]}"
            )
        self.clear_button.setEnabled(filled and not busy)
        self.label_input.setEnabled(filled and not busy)
        self.text_input.setEnabled(not busy)
