"""八方向音频轮盘设置。文件导入放到后台，配置在 GUI 线程一次写入。"""

from __future__ import annotations

import logging
import math
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QEvent, QObject, QPoint, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QColor, QFont, QKeySequence, QMouseEvent, QPaintEvent, QPainter,
    QPainterPath, QPen, QShowEvent,
)
from PySide6.QtWidgets import (
    QFileDialog, QHBoxLayout, QKeySequenceEdit,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)
from shiboken6 import isValid

from wordy.audio.clips import AUDIO_EXTENSIONS, import_audio_clip, remove_audio_clip
from wordy.audio.recorder import MAX_RECORD_SECONDS, RecordingError, SystemAudioRecorder
from wordy.config import AppSettings
from wordy.ui.status_toast import show_status_toast
from wordy.ui import glass
from wordy.ui.theme import (
    ACCENT_SECONDARY, ELEVATED_BG, GREEN_ACCENT, TEXT_ERROR, TEXT_MUTED, TEXT_PRIMARY,
    WINDOW_BG, tint,
)

logger = logging.getLogger(__name__)
DIRECTIONS = ("上", "右上", "右", "右下", "下", "左下", "左", "左上")
RECORD_TICK_MS = 200


class _ImportSignals(QObject):
    # index, filename, label, error, 录音临时文件路径（非录音导入为空串）
    finished = Signal(int, str, str, str, str)


class _WheelPicker(QWidget):
    """用真实方位呈现八个可键盘聚焦的轮盘槽。"""

    POSITIONS = (
        (126, 20), (181, 43), (204, 98), (181, 153),
        (126, 176), (71, 153), (48, 98), (71, 43),
    )

    def __init__(self, on_select: Callable[[int], None]) -> None:
        super().__init__()
        self._on_select = on_select
        self._hovered_index: int | None = None
        self.setFixedSize(340, 240)
        self.setMouseTracking(True)
        self.buttons: list[QPushButton] = []
        for index, direction in enumerate(DIRECTIONS):
            button = QPushButton(direction, self)
            button.setObjectName("wheelSlot")
            button.setAccessibleName(f"编辑{direction}音频")
            button.setCheckable(True)
            # 鼠标统一由轮盘处理，避免透明按钮的 hover/pressed 状态覆盖扇区选中色。
            button.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            button.setFixedSize(88, 44)
            button.move(*self.POSITIONS[index])
            button.clicked.connect(lambda _checked=False, i=index: on_select(i))
            button.installEventFilter(self)
            self.buttons.append(button)
        self.setStyleSheet(f"""
            QPushButton#wheelSlot {{
                background: transparent;
                color: transparent;
                border: none;
            }}
            QPushButton#wheelSlot:disabled {{
                background: transparent;
                color: transparent;
            }}
        """)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched in self.buttons and event.type() in (QEvent.Type.FocusIn, QEvent.Type.FocusOut):
            self.update()
        return super().eventFilter(watched, event)

    @staticmethod
    def _slot_at(point: QPoint) -> int | None:
        dx, dy = point.x() - 170, point.y() - 120
        distance = math.hypot(dx, dy)
        if distance < 42 or distance > 116:
            return None
        angle = math.degrees(math.atan2(dx, -dy)) % 360
        return int((angle + 22.5) % 360 // 45)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            index = self._slot_at(event.position().toPoint())
            if index is not None and self.buttons[index].isEnabled():
                self._on_select(index)
                self.update()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        hovered_index = self._slot_at(event.position().toPoint())
        if hovered_index != self._hovered_index:
            self._hovered_index = hovered_index
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event: QEvent) -> None:
        self._hovered_index = None
        self.update()
        super().leaveEvent(event)

    def paintEvent(self, _event: QPaintEvent) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center = QPointF(170, 120)
        outer = QRectF(58, 8, 224, 224)
        inner = QRectF(129, 79, 82, 82)
        accent = QColor(GREEN_ACCENT)
        secondary = QColor(ACCENT_SECONDARY)
        # 与运行轮盘共用同一套玻璃叠层，两处视觉不漂移；扇区同样保持足够不透明度。
        disc = QPainterPath()
        disc.addEllipse(QRectF(48, -2, 244, 244))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tint(WINDOW_BG, 190))
        painter.drawPath(disc)
        glass.paint_noise(painter, disc)
        glass.paint_edge_glow(painter, disc, QRectF(48, -2, 244, 244), alpha=48)

        for index, button in enumerate(self.buttons):
            active = button.isChecked()
            highlighted = active or index == self._hovered_index or button.hasFocus()
            angle = 112.5 - index * 45
            path = QPainterPath()
            path.arcMoveTo(outer, angle - 1)
            path.arcTo(outer, angle - 1, -43)
            end = math.radians(angle - 44)
            path.lineTo(center + QPointF(41 * math.cos(end), -41 * math.sin(end)))
            path.arcTo(inner, angle - 44, 43)
            path.closeSubpath()
            if active:
                painter.setPen(QPen(tint(GREEN_ACCENT, 50), 7))
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(tint(GREEN_ACCENT, 95) if active else
                             tint(ELEVATED_BG, 250) if highlighted else tint(ELEVATED_BG, 215))
            painter.drawPath(path)
            # 与运行轮盘一致：分隔用居中 1px 细线，避免相邻扇区各自内缩叠出深缝。
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(accent if highlighted else tint(TEXT_PRIMARY, 45), 1.0))
            painter.drawPath(path)

            radians = math.radians(90 - index * 45)
            text_center = center + QPointF(78 * math.cos(radians), -78 * math.sin(radians))
            label = button.text().split("\n", 1)[-1]
            painter.setFont(QFont("Segoe UI", 7, QFont.Weight.DemiBold))
            glass.paint_lifted_text(
                painter,
                QRectF(text_center.x() - 38, text_center.y() - 18, 76, 14),
                Qt.AlignmentFlag.AlignCenter,
                f"0{index + 1}",
                accent if highlighted else secondary,
            )
            painter.setFont(QFont("Microsoft YaHei UI", 8, QFont.Weight.DemiBold))
            text = painter.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, 82)
            glass.paint_lifted_text(
                painter,
                QRectF(text_center.x() - 42, text_center.y() - 2, 84, 24),
                Qt.AlignmentFlag.AlignCenter,
                text,
                QColor(TEXT_PRIMARY) if label != "未设置" else QColor(TEXT_MUTED),
            )

        for tick in range(40):
            angle = math.radians(tick * 9)
            major = tick % 5 == 0
            painter.setPen(QPen(secondary if major else tint(TEXT_PRIMARY, 45),
                                1.4 if major else 0.8))
            painter.drawLine(center + QPointF(116 * math.cos(angle), 116 * math.sin(angle)),
                             center + QPointF((121 if major else 119) * math.cos(angle),
                                              (121 if major else 119) * math.sin(angle)))

        painter.setBrush(tint(WINDOW_BG, 250))
        painter.setPen(QPen(tint(GREEN_ACCENT, 110), 1))
        painter.drawEllipse(QRectF(133, 83, 74, 74))
        # 静态双弧代替运行轮盘的入场扫描：设置页不应有持续动画。
        painter.setPen(QPen(accent, 2))
        painter.drawArc(QRectF(138, 88, 64, 64), 16 * 18, 52 * 16)
        painter.setPen(QPen(secondary, 2))
        painter.drawArc(QRectF(138, 88, 64, 64), 16 * 198, 52 * 16)
        painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
        painter.setPen(accent)
        painter.drawText(QRectF(137, 101, 66, 17), Qt.AlignmentFlag.AlignCenter, "WORDY")
        painter.setFont(QFont("Microsoft YaHei UI", 7))
        painter.setPen(QColor(TEXT_MUTED))
        painter.drawText(QRectF(137, 121, 66, 17), Qt.AlignmentFlag.AlignCenter, "点击编辑")
        painter.end()


class ChatWheelSettings(QWidget):
    def __init__(self, settings: AppSettings,
                 on_field_changed: Callable[[str, object], None]):
        super().__init__()
        self._settings = settings
        self._on_field_changed = on_field_changed
        self._busy: set[int] = set()
        self._closed = threading.Event()
        # 录制独占面板：开始后锁住槽切换与其他编辑动作，录音只落在开始时的槽。
        self._recording: int | None = None
        self._recorder: SystemAudioRecorder | None = None
        self._record_timer = QTimer(self)
        self._record_timer.setInterval(RECORD_TICK_MS)
        self._record_timer.timeout.connect(self._tick_recording)
        self._signals = _ImportSignals(self)
        self._signals.finished.connect(self._finish_import)
        self.selected_index = 0

        layout = QVBoxLayout(self)
        # 父级页签已有标题和阅读边距，避免重复留白把第八槽挤出默认窗口。
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
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

        self.wheel_picker = _WheelPicker(self._select_slot)
        self.slot_buttons = self.wheel_picker.buttons
        layout.addWidget(self.wheel_picker, 0, Qt.AlignmentFlag.AlignHCenter)

        self.editor_title = QLabel()
        self.editor_title.setObjectName("sectionTitle")
        layout.addWidget(self.editor_title)
        self.label_input = QLineEdit()
        self.label_input.setPlaceholderText("选择音频后命名")
        self.label_input.setMaxLength(40)
        self.label_input.editingFinished.connect(self._save_label)
        layout.addWidget(self.label_input)
        action_row = QHBoxLayout()
        self.choose_button = QPushButton("选择文件")
        self.choose_button.clicked.connect(lambda: self._choose_file(self.selected_index))
        action_row.addWidget(self.choose_button, 1)
        # 文案保持两字，动作行必须留在同一行里：加行高会顶掉滚动区契约。
        self.record_button = QPushButton("录制")
        self.record_button.clicked.connect(self._toggle_record)
        action_row.addWidget(self.record_button)
        self.preview_button = QPushButton("试听")
        self.preview_button.clicked.connect(
            lambda: self._on_field_changed("chat_wheel_preview", self.selected_index))
        action_row.addWidget(self.preview_button)
        self.clear_button = QPushButton("清空")
        self.clear_button.clicked.connect(
            lambda: self._replace_clip(self.selected_index, "", ""))
        action_row.addWidget(self.clear_button)
        layout.addLayout(action_row)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setAccessibleName("轮盘设置状态")
        # 反馈优先走设置窗口的浮动弹窗，原地标签仅在面板独立使用时出现。
        self.status_label.setVisible(False)
        layout.addWidget(self.status_label)
        layout.addStretch()
        for index in range(8):
            self._refresh_slot(index)
        self._select_slot(0)

    def showEvent(self, event: QShowEvent) -> None:
        # 页签切换只隐藏子控件；仅父设置窗口关闭时放弃尚未提交的导入。
        self.window().installEventFilter(self)
        super().showEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if event.type() == QEvent.Type.Close:
            self._closed.set()
            # 采集线程独立于窗口，窗口销毁前不收掉会留下采集流和临时文件。
            self._cancel_recording()
        return super().eventFilter(watched, event)

    def _hint(self, text: str) -> None:
        """长期停留的状态说明写原地标签，不走浮动队列。

        浮动弹窗锚在页脚上沿、向上生长，而本页的动作行就在页脚正上方；
        弹窗不仅遮住按钮，自身还会吃掉点击，录制中就没法再点「停止」。
        """
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {GREEN_ACCENT};")
        self.status_label.setVisible(True)

    def _clear_hint(self) -> None:
        self.status_label.clear()
        self.status_label.setVisible(False)

    def _status(self, text: str, *, error: bool = False) -> None:
        color = TEXT_ERROR if error else GREEN_ACCENT
        # 宿主窗口存在时用浮动弹窗，反馈不再长期停在页面固定位置。
        if show_status_toast(self, text, color):
            return
        self.status_label.setText(text)
        self.status_label.setStyleSheet(f"color: {color};")
        self.status_label.setVisible(True)

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

    def import_file(self, index: int, source: Path, label: str | None = None,
                    temporary: bool = False) -> None:
        """把一个音频文件导入槽位；``label`` 缺省沿用文件名，``temporary`` 用于录音副本。"""
        if index in self._busy:
            return
        self._busy.add(index)
        self._refresh_slot(index)
        self._status(f"正在导入{DIRECTIONS[index]}音频…")
        config_file = self._settings._config_file
        signals = self._signals
        slot_label = label or source.stem

        def run() -> None:
            filename, error = "", ""
            try:
                filename = import_audio_clip(source, config_file).name
            except Exception as exc:
                error = str(exc)
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
                self._discard_temporary(str(source))

        threading.Thread(target=run, name="ChatWheelImport", daemon=True).start()

    def _toggle_record(self) -> None:
        if self._recording is not None:
            self._finish_recording()
            return
        index = self.selected_index
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
        self._hint(f"正在录制{DIRECTIONS[index]}音频，再次点击「停止」即可结束。")

    def _finish_recording(self) -> None:
        index, recorder = self._recording, self._recorder
        self._record_timer.stop()
        self._recording = None
        self._recorder = None
        self._clear_hint()
        self._refresh_all_slots()
        if index is None or recorder is None:
            return
        try:
            path = recorder.stop()
        except RecordingError as error:
            self._status(str(error), error=True)
            return
        # 时间戳让八个槽在轮盘上互相分得清，用户仍可随时改名。
        self.import_file(index, path, label=f"录音 {datetime.now():%H%M%S}", temporary=True)

    def _cancel_recording(self) -> None:
        """丢弃进行中的录音；采集线程不碰 Qt，可以在这里安全收尾。"""
        self._record_timer.stop()
        recorder, self._recorder = self._recorder, None
        self._recording = None
        self._clear_hint()
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
        direction = DIRECTIONS[recording if recording is not None else self.selected_index]
        self.record_button.setAccessibleName(
            f"停止录制{direction}音频" if recording else f"录制{direction}音频"
        )

    def _refresh_all_slots(self) -> None:
        for slot in range(8):
            self._refresh_slot(slot)

    def _finish_import(self, index: int, filename: str, label: str, error: str,
                       temporary_source: str = "") -> None:
        self._busy.discard(index)
        if self._closed.is_set():
            if filename:
                self._remove_unused(filename, self._settings._config_file)
            # 窗口已关，这份录音不会再被导入，可以连同临时文件一起回收。
            self._discard_temporary(temporary_source)
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
            self._refresh_slot(index)
            return False
        self._refresh_slot(index)
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

    def _save_label(self) -> None:
        index = self.selected_index
        old_labels = list(self._settings.chat_wheel_labels)
        labels = list(old_labels)
        text = self.label_input.text().strip()
        if labels[index] == text:
            return
        labels[index] = text
        try:
            self._settings.update(chat_wheel_labels=labels)
        except Exception as error:
            object.__setattr__(self._settings, "chat_wheel_labels", old_labels)
            self.label_input.setText(old_labels[index])
            self._status(f"保存失败：{error}", error=True)
            return
        self._refresh_slot(index)

    def _select_slot(self, index: int) -> None:
        self.selected_index = index
        for button_index, button in enumerate(self.slot_buttons):
            selected = button_index == index
            button.setChecked(selected)
            button.setProperty("selected", selected)
            button.style().unpolish(button)
            button.style().polish(button)
        self.wheel_picker.update()
        self.editor_title.setText(f"{DIRECTIONS[index]}音频")
        self.label_input.setAccessibleName(f"{DIRECTIONS[index]}音频名称")
        self.choose_button.setAccessibleName(f"选择{DIRECTIONS[index]}音频")
        self.preview_button.setAccessibleName(f"试听{DIRECTIONS[index]}音频")
        self.clear_button.setAccessibleName(f"清空{DIRECTIONS[index]}音频")
        self._refresh_slot(index)

    def _refresh_slot(self, index: int) -> None:
        recording = index == self._recording
        # 录制期间锁住全部槽位：录音只能落在开始时的那个槽。
        busy = index in self._busy or self._recording is not None
        filled = bool(self._settings.chat_wheel_slots[index])
        label = self._settings.chat_wheel_labels[index].strip()
        self.slot_buttons[index].setText(f"{DIRECTIONS[index]}\n{label or '未设置'}")
        self.slot_buttons[index].setEnabled(not busy)
        if index != self.selected_index:
            return
        self.label_input.setText(label)
        self.choose_button.setEnabled(not busy)
        self.choose_button.setText(
            "录制中…" if recording else "导入中…" if busy else "选择文件"
        )
        self.record_button.setEnabled(recording or index not in self._busy)
        self._refresh_record_button()
        self.preview_button.setEnabled(filled and not busy)
        self.clear_button.setEnabled(filled and not busy)
        self.label_input.setEnabled(filled and not busy)
