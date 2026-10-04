"""八方向音频轮盘设置。文件导入放到后台，配置在 GUI 线程一次写入。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, Signal
from PySide6.QtGui import QKeySequence, QShowEvent
from PySide6.QtWidgets import (
    QFileDialog, QGridLayout, QHBoxLayout, QKeySequenceEdit,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from wordy.audio.clips import AUDIO_EXTENSIONS, import_audio_clip, remove_audio_clip
from wordy.config import AppSettings
from wordy.ui.theme import GREEN_ACCENT, TEXT_ERROR, TEXT_MUTED, TEXT_PRIMARY

logger = logging.getLogger(__name__)
DIRECTIONS = ("上", "右上", "右", "右下", "下", "左下", "左", "左上")


class _ImportSignals(QObject):
    finished = Signal(int, str, str, str)


class ChatWheelSettings(QWidget):
    def __init__(self, settings: AppSettings,
                 on_field_changed: Callable[[str, object], None]):
        super().__init__()
        self._settings = settings
        self._on_field_changed = on_field_changed
        self._busy: set[int] = set()
        self._closed = threading.Event()
        self._signals = _ImportSignals(self)
        self._signals.finished.connect(self._finish_import)
        self.label_inputs: list[QLineEdit] = []
        self.choose_buttons: list[QPushButton] = []
        self.preview_buttons: list[QPushButton] = []
        self.clear_buttons: list[QPushButton] = []

        layout = QVBoxLayout(self)
        # 父级页签已有标题和阅读边距，避免重复留白把第八槽挤出默认窗口。
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        hint = QLabel("按住快捷键，移向音频，松开播放；回到中心或按 Esc 取消。")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_MUTED};")
        layout.addWidget(hint)

        hotkey_row = QHBoxLayout()
        hotkey_label = QLabel("全局快捷键")
        hotkey_label.setStyleSheet(f"color: {TEXT_PRIMARY};")
        hotkey_row.addWidget(hotkey_label)
        self.hotkey_input = QKeySequenceEdit(QKeySequence(settings.chat_wheel_hotkey))
        self.hotkey_input.setMaximumSequenceLength(1)
        self.hotkey_input.setAccessibleName("轮盘全局快捷键")
        hotkey_row.addWidget(self.hotkey_input, 1)
        self.hotkey_apply = QPushButton("应用")
        self.hotkey_apply.clicked.connect(self._apply_hotkey)
        hotkey_row.addWidget(self.hotkey_apply)
        layout.addLayout(hotkey_row)

        grid = QGridLayout()
        grid.setVerticalSpacing(6)
        for index, direction in enumerate(DIRECTIONS):
            direction_label = QLabel(f"{index + 1} · {direction}")
            direction_label.setStyleSheet(f"color: {TEXT_MUTED};")
            grid.addWidget(direction_label, index, 0)
            label = QLineEdit(settings.chat_wheel_labels[index])
            label.setPlaceholderText("选择音频后命名")
            label.setAccessibleName(f"{direction}音频名称")
            label.setMaxLength(40)
            label.editingFinished.connect(lambda i=index: self._save_label(i))
            self.label_inputs.append(label)
            grid.addWidget(label, index, 1)
            choose = QPushButton("选择文件")
            choose.setAccessibleName(f"选择{direction}音频")
            choose.clicked.connect(lambda _checked=False, i=index: self._choose_file(i))
            self.choose_buttons.append(choose)
            grid.addWidget(choose, index, 2)
            preview = QPushButton("试听")
            preview.setAccessibleName(f"试听{direction}音频")
            preview.clicked.connect(lambda _checked=False, i=index: self._on_field_changed("chat_wheel_preview", i))
            self.preview_buttons.append(preview)
            grid.addWidget(preview, index, 3)
            clear = QPushButton("清空")
            clear.setAccessibleName(f"清空{direction}音频")
            clear.clicked.connect(lambda _checked=False, i=index: self._replace_clip(i, "", ""))
            self.clear_buttons.append(clear)
            grid.addWidget(clear, index, 4)
            self._refresh_row(index)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("轮盘设置状态")
        layout.addWidget(self.status_label)
        layout.addStretch()

    def showEvent(self, event: QShowEvent) -> None:
        # 页签切换只隐藏子控件；仅父设置窗口关闭时放弃尚未提交的导入。
        self.window().installEventFilter(self)
        super().showEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Close:
            self._closed.set()
        return super().eventFilter(watched, event)

    def _status(self, text: str, *, error: bool = False) -> None:
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {TEXT_ERROR if error else GREEN_ACCENT};")

    def _apply_hotkey(self) -> None:
        text = self.hotkey_input.keySequence().toString(QKeySequence.SequenceFormat.PortableText).lower()
        # Qt 使用缩写名称，Win32 解析器使用 keyboard 风格的完整名称。
        text = text.replace("meta+", "windows+").replace("pgup", "page up").replace("pgdown", "page down")
        if not text:
            self._status("请先按下快捷键组合。", error=True)
            return
        # 注册成功才保存，避免占用冲突导致下次启动也无法唤醒。
        self.hotkey_apply.setEnabled(False)
        try:
            self._on_field_changed("chat_wheel_hotkey", text)
        except Exception as error:
            self.set_hotkey_result(str(error))

    def set_hotkey_result(self, error: str | None) -> None:
        self.hotkey_apply.setEnabled(True)
        self._status(error or "快捷键已更新。", error=bool(error))

    def _choose_file(self, index: int) -> None:
        patterns = " ".join(f"*{extension}" for extension in sorted(AUDIO_EXTENSIONS))
        filename, _ = QFileDialog.getOpenFileName(self, "选择轮盘音频", "", f"音频文件 ({patterns})")
        if filename:
            self.import_file(index, Path(filename))

    def import_file(self, index: int, source: Path) -> None:
        if index in self._busy:
            return
        self._busy.add(index)
        self._refresh_row(index)
        self._status(f"正在导入{DIRECTIONS[index]}音频…")
        config_file = self._settings._config_file
        signals = self._signals

        def run() -> None:
            filename, error = "", ""
            try:
                filename = import_audio_clip(source, config_file).name
            except Exception as exc:
                error = str(exc)
            # 设置窗口关闭后 QObject 可能已销毁，丢弃结果并回收未使用文件。
            try:
                if not self._closed.is_set() and isValid(signals):
                    signals.finished.emit(index, filename, source.stem, error)
                    return
            except RuntimeError:
                pass
            if filename:
                self._remove_unused(filename, config_file)

        threading.Thread(target=run, name="ChatWheelImport", daemon=True).start()

    def _finish_import(self, index: int, filename: str, label: str, error: str) -> None:
        self._busy.discard(index)
        if self._closed.is_set():
            if filename:
                self._remove_unused(filename, self._settings._config_file)
            return
        if error:
            self._status(f"导入失败：{error}", error=True)
            self._refresh_row(index)
            return
        if not self._replace_clip(index, filename, label):
            self._remove_unused(filename, self._settings._config_file)

    def _replace_clip(self, index: int, filename: str, label: str) -> bool:
        old_slots = list(self._settings.chat_wheel_slots)
        old_labels = list(self._settings.chat_wheel_labels)
        slots, labels = list(old_slots), list(old_labels)
        slots[index], labels[index] = filename, label[:40]
        try:
            self._settings.update(chat_wheel_slots=slots, chat_wheel_labels=labels)
        except Exception as error:
            # update 会先更改内存；落盘失败必须恢复旧槽，才能安全删除导入副本。
            object.__setattr__(self._settings, "chat_wheel_slots", old_slots)
            object.__setattr__(self._settings, "chat_wheel_labels", old_labels)
            self._status(f"保存失败：{error}", error=True)
            self._refresh_row(index)
            return False
        self.label_inputs[index].setText(labels[index])
        self._refresh_row(index)
        self._status("音频已保存。" if filename else "音频已清空。")
        old = old_slots[index]
        if old and old not in slots:
            self._remove_unused(old, self._settings._config_file)
        return True

    @staticmethod
    def _remove_unused(filename: str, config_file: Path | None) -> None:
        try:
            remove_audio_clip(filename, config_file)
        except (OSError, ValueError):
            logger.warning("无法清理轮盘音频 %s", filename, exc_info=True)

    def _save_label(self, index: int) -> None:
        old_labels = list(self._settings.chat_wheel_labels)
        labels = list(old_labels)
        text = self.label_inputs[index].text().strip()
        if labels[index] == text:
            return
        labels[index] = text
        try:
            self._settings.update(chat_wheel_labels=labels)
        except Exception as error:
            object.__setattr__(self._settings, "chat_wheel_labels", old_labels)
            self.label_inputs[index].setText(old_labels[index])
            self._status(f"保存失败：{error}", error=True)

    def _refresh_row(self, index: int) -> None:
        busy = index in self._busy
        filled = bool(self._settings.chat_wheel_slots[index])
        self.choose_buttons[index].setEnabled(not busy)
        self.choose_buttons[index].setText("导入中…" if busy else "选择文件")
        self.preview_buttons[index].setEnabled(filled and not busy)
        self.clear_buttons[index].setEnabled(filled and not busy)
        self.label_inputs[index].setEnabled(filled and not busy)
