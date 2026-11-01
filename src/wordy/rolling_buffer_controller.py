"""后台持续录制：热键切换、截取弹窗与槽位保存。

采集在 :class:`RollingAudioRecorder` 的后台线程里完成；本模块只做 GUI 线程的
状态切换和保存事务，Qt 对象不会从采集线程直接访问。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Protocol

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import QDialog

from wordy.audio.clips import (
    decoded_audio_clip, import_audio_clip, remove_audio_clip,
)
from wordy.audio.recorder import RollingAudioRecorder
from wordy.config import AppSettings, USER_CONFIG_FILE
from wordy.hotkey.native import NativeHotkeyListener, parse_hotkey
from wordy.ui.audio_trim_dialog import AudioTrimDialog
from wordy.ui.target_slot_picker import SLOTS_PER_WHEEL, TargetSlotPicker

logger = logging.getLogger(__name__)


class _RecorderLike(Protocol):
    """控制器只依赖录制器的这几个方法，测试可注入轻量替身。"""

    @property
    def is_running(self) -> bool: ...

    def start(self, seconds: float) -> None: ...

    def stop(self) -> Path: ...

    def cancel(self) -> None: ...


def ensure_hotkey_available(settings: AppSettings, hotkey: str) -> None:
    """后台录制快捷键不能与输入框或任一轮盘重复，冲突时抛 ``ValueError``。"""
    binding = parse_hotkey(hotkey)
    if binding == parse_hotkey(settings.hotkey):
        raise ValueError("不能与输入框快捷键相同")
    for index in range(settings.wheel_count):
        if binding == parse_hotkey(settings.get_wheel(index)["hotkey"]):
            raise ValueError("不能与聊天轮盘快捷键相同")


def save_buffer_clip(settings: AppSettings, source: Path, wheel_index: int,
                     slot_index: int, label: str) -> str:
    """把截取片段写入指定槽位，返回托管文件名。

    成功后才允许调用方删除 ``source``；任一步失败都回滚托管副本并抛出，
    旧槽位内容和临时文件保持原样。
    """
    config_file = settings._config_file or USER_CONFIG_FILE
    imported = import_audio_clip(source, config_file)
    try:
        # 替换旧槽前先完整验证解码，失败时旧音频仍保持可用。
        with decoded_audio_clip(imported):
            pass
        wheel = settings.get_wheel(wheel_index)
        slots = list(wheel["slots"])
        labels = list(wheel["labels"])
        texts = list(wheel["texts"])
        replaced = slots[slot_index]
        slots[slot_index] = imported.name
        labels[slot_index] = label
        # 槽内容互斥：写入音频必须清掉同槽文字，三组数组一次事务落盘。
        texts[slot_index] = ""
        settings.update_wheel(wheel_index, slots=slots, labels=labels, texts=texts)
    except Exception:
        try:
            remove_audio_clip(imported.name, config_file)
        except (OSError, ValueError):
            logger.warning("无法清理未使用的托管音频 %s", imported.name, exc_info=True)
        raise

    if (replaced and replaced != imported.name
            and not any(replaced in settings.get_wheel(i)["slots"]
                        for i in range(settings.wheel_count))):
        try:
            remove_audio_clip(replaced, config_file)
        except (OSError, ValueError):
            logger.warning("无法清理被替换的轮盘音频 %s", replaced, exc_info=True)
    return imported.name


class RollingBufferController(QObject):
    """热键开始/停止后台录制，停止后打开截取弹窗并保存到轮盘槽位。"""

    triggered = Signal()
    # (是否成功, 提示文字)；保存线程只发信号，不直接碰通知控件。
    save_finished = Signal(bool, str)

    def __init__(
        self,
        settings: AppSettings,
        *,
        dialog_parent: Callable[[], object | None],
        # (片段路径, 播放结束回调, 播放进度回调)
        play_preview: Callable[
            [Path, Callable[[], None], Callable[[float], None]], None],
        stop_preview: Callable[[], None],
        on_state_changed: Callable[[bool], None] | None = None,
        on_notify: Callable[[str], None] | None = None,
        recorder_factory: Callable[[], _RecorderLike] = RollingAudioRecorder,
    ) -> None:
        super().__init__()
        self.settings = settings
        self._dialog_parent = dialog_parent
        self._play_preview = play_preview
        self._stop_preview = stop_preview
        self._on_state_changed = on_state_changed
        self._on_notify = on_notify
        self._recorder_factory = recorder_factory
        self._recorder: _RecorderLike | None = None
        self._listener: NativeHotkeyListener | None = None
        self._dialog_open = False
        self._stopped = False
        self.triggered.connect(self._toggle, Qt.ConnectionType.QueuedConnection)
        self.save_finished.connect(self._on_save_finished, Qt.ConnectionType.QueuedConnection)

    # ── 热键 ──────────────────────────────────────────────────────────

    def start(self) -> bool:
        """注册配置中的后台录制快捷键；失败只影响本功能，不阻断启动。"""
        try:
            self._register(self.settings.rolling_buffer_hotkey)
        except (ValueError, RuntimeError, OSError) as error:
            logger.warning("后台录制快捷键未启用: %s", error)
            return False
        return True

    def set_hotkey(self, hotkey: str) -> bool:
        old_hotkey = self.settings.rolling_buffer_hotkey
        if hotkey == old_hotkey:
            return True
        try:
            self._register(hotkey)
        except (ValueError, RuntimeError, OSError) as error:
            logger.warning("注册后台录制快捷键失败: %s", error)
            self._notify(f"后台录制快捷键未启用：{error}")
            return False
        try:
            self.settings.rolling_buffer_hotkey = hotkey
        except Exception as error:  # noqa: BLE001 - 落盘失败要恢复旧监听器
            logger.warning("保存后台录制快捷键失败: %s", error)
            listener = self._listener
            if listener is not None:
                listener.stop()
            try:
                self._register(old_hotkey)
            except Exception:  # noqa: BLE001
                logger.warning("恢复后台录制快捷键失败", exc_info=True)
            self._notify(f"后台录制快捷键保存失败：{error}")
            return False
        return True

    def _register(self, hotkey: str) -> None:
        ensure_hotkey_available(self.settings, hotkey)
        listener = NativeHotkeyListener(hotkey, "后台录制", self.triggered.emit)
        listener.start()
        old = self._listener
        self._listener = listener
        if old is not None:
            old.stop()

    def stop(self) -> None:
        self._stopped = True
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()
        recorder, self._recorder = self._recorder, None
        if recorder is not None and recorder.is_running:
            recorder.cancel()
            self._state(False)

    def suspend_hotkey(self) -> None:
        """设置页录制新组合期间停掉监听；否则按到旧键会同时触发录制与切换。"""
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.stop()

    def resume_hotkey(self) -> None:
        """恢复监听器；改键成功时控制器已注册新监听器，这里是空操作。"""
        if self._stopped or self._listener is not None:
            return
        try:
            self._register(self.settings.rolling_buffer_hotkey)
        except (ValueError, RuntimeError, OSError) as error:
            logger.warning("恢复后台录制快捷键失败: %s", error)

    # ── 状态切换 ──────────────────────────────────────────────────────

    def _toggle(self) -> None:
        if self._stopped or self._dialog_open:
            return
        recorder = self._recorder
        if recorder is not None and recorder.is_running:
            self._finish(recorder)
        else:
            self._begin()

    def _begin(self) -> None:
        seconds = max(0.1, float(self.settings.rolling_buffer_seconds))
        try:
            recorder = self._recorder_factory()
            recorder.start(seconds=seconds)
        except Exception as error:  # noqa: BLE001 - 设备异常统一转成提示
            self._recorder = None
            self._notify(f"后台录制未能开始：{error}")
            return
        self._recorder = recorder
        self._state(True)
        self._notify(f"后台录制已开始，保留最近 {seconds:g} 秒；再按一次结束并截取。")

    def _finish(self, recorder: _RecorderLike) -> None:
        try:
            snapshot = recorder.stop()
        except Exception as error:  # noqa: BLE001 - 采集中断也要回到空闲
            # 超时等失败时保留句柄，下一次按热键可以重试停止而不是永久卡住。
            self._recorder = recorder
            self._notify(f"后台录制结束失败：{error}")
            self._state(False)
            return
        self._recorder = None
        self._state(False)
        self._dialog_open = True
        try:
            self._open_dialog(snapshot)
        finally:
            self._dialog_open = False
            # 截取产物是独立文件；原始窗口只服务这一次弹窗。波形线程可能还
            # 握着句柄，Windows 上删除会失败——那是清理问题，不能穿出 GUI 槽。
            try:
                snapshot.unlink(missing_ok=True)
            except OSError as error:
                logger.warning("清理后台录制临时文件失败: %s", error)

    # ── 截取与保存 ────────────────────────────────────────────────────

    def _open_dialog(self, snapshot: Path) -> None:
        picker = TargetSlotPicker(self.settings, self._dialog_parent())  # type: ignore[arg-type]
        saved_wheel, saved_slot = self.settings.rolling_buffer_target
        saved_index = saved_wheel * SLOTS_PER_WHEEL + saved_slot
        dialog = AudioTrimDialog(
            snapshot,
            self._dialog_parent(),  # type: ignore[arg-type]
            play_preview=None,
            stop_preview=self._stop_preview,
            target_picker=picker,
            target_index=saved_index,
        )

        def play(clip: Path) -> None:
            # 弹窗的播放线与按钮复位依赖按片段路径匹配的回调，旧片段不能复位新试听。
            def finished() -> None:
                try:
                    dialog.preview_finished(str(clip))
                except RuntimeError:
                    pass  # 窗口已销毁，无需再复位试听控件。

            def progress(seconds: float) -> None:
                try:
                    dialog.preview_progress(str(clip), seconds)
                except RuntimeError:
                    pass  # 窗口销毁后忽略迟到的音频进度。

            self._play_preview(clip, finished, progress)

        dialog.set_play_preview(play)

        try:
            accepted = dialog.exec() == QDialog.DialogCode.Accepted
            clip = dialog.take_clip() if accepted else None
            wheel_index, slot_index = picker.selected_target()
        finally:
            dialog.deleteLater()
        if clip is None:
            return
        try:
            self.settings.rolling_buffer_target = [wheel_index, slot_index]
        except Exception:  # noqa: BLE001 - 记住目标失败不影响本次保存
            logger.warning("保存后台录制目标失败", exc_info=True)
        label = f"录音 {datetime.now():%H%M%S}"
        name = self.settings.get_wheel(wheel_index)["name"] or f"轮盘 {wheel_index + 1}"
        target_name = f"{name} / 音频 {slot_index + 1}"
        self._save_async(clip, wheel_index, slot_index, label, target_name)

    def _save_async(self, clip: Path, wheel_index: int, slot_index: int,
                    label: str, target_name: str) -> None:
        settings = self.settings
        signal = self.save_finished

        def run() -> None:
            try:
                save_buffer_clip(settings, clip, wheel_index, slot_index, label)
            except Exception as error:  # noqa: BLE001 - 临时片段保留在 %TEMP% 供重试
                logger.exception("保存后台录制片段失败")
                signal.emit(False, f"保存失败：{error}（临时文件保留在 {clip}）")
                return
            try:
                clip.unlink(missing_ok=True)
            except OSError as error:
                logger.warning("清理已保存的截取临时文件失败: %s", error)
            signal.emit(True, f"已保存到 {target_name}")

        threading.Thread(target=run, name="RollingBufferSave", daemon=True).start()

    def _on_save_finished(self, ok: bool, message: str) -> None:
        if not ok:
            logger.warning(message)
        self._notify(message)

    def _state(self, active: bool) -> None:
        if self._on_state_changed is not None:
            self._on_state_changed(active)

    def _notify(self, message: str) -> None:
        if self._on_notify is not None:
            self._on_notify(message)
