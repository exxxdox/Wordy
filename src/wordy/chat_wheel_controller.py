"""轮盘全局快捷键、GUI 线程桥接与托管音频选择。"""

from collections.abc import Callable
from pathlib import Path
import logging

from PySide6.QtCore import QObject, Signal, Qt

from wordy.config import AppSettings, ChatWheelConfig, USER_CONFIG_FILE, new_chat_wheel
from wordy.hotkey.native import NativeHotkeyListener, parse_hotkey
from wordy.ui.chat_wheel import ChatWheel
from wordy.audio.clips import resolve_audio_clip, remove_audio_clip

logger = logging.getLogger(__name__)


class ChatWheelController(QObject):
    triggered = Signal(str)
    playback_error = Signal(str)
    # 播放结束（播完、被停止或失败）后发出，供设置页复位「停止」按钮。
    playback_finished = Signal()

    def __init__(self, settings: AppSettings,
                 play_file: Callable[
                     [Path, Callable[[str], None], Callable[[], None], float], None],
                 on_error: Callable[[str], None],
                 on_finished: Callable[[], None] | None = None,
                 speak_text: Callable[[str, Callable[[], None]], None] | None = None):
        super().__init__()
        self.settings = settings
        self._play_file = play_file
        self._speak_text = speak_text
        self._listeners: dict[tuple[int, int], NativeHotkeyListener] = {}
        self._active_wheel = 0
        self._stopped = False
        self.wheel = ChatWheel(self.play_slot)
        # 原生监听器运行在后台；显式排队保证窗口只由 GUI 线程操作。
        self.triggered.connect(self._open, Qt.ConnectionType.QueuedConnection)
        self.playback_error.connect(on_error, Qt.ConnectionType.QueuedConnection)
        if on_finished is not None:
            # 完成回调来自播放 worker 线程，必须排队回到 GUI 线程。
            self.playback_finished.connect(on_finished, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        self._apply_wheels([self.settings.get_wheel(i) for i in range(self.settings.wheel_count)])

    def _apply_wheels(self, wheels: list[ChatWheelConfig]) -> None:
        bindings = [parse_hotkey(wheel["hotkey"]) for wheel in wheels]
        if len(set(bindings)) != len(bindings):
            raise ValueError("轮盘快捷键不能重复")
        if parse_hotkey(self.settings.hotkey) in bindings:
            raise ValueError("轮盘快捷键不能与输入框快捷键相同")
        previous_wheels = [self.settings.get_wheel(i) for i in range(self.settings.wheel_count)]
        added: dict[tuple[int, int], NativeHotkeyListener] = {}
        try:
            for wheel, binding in zip(wheels, bindings):
                if binding in self._listeners:
                    continue
                hotkey = wheel["hotkey"]
                # 回调按快捷键定位，删除前面的轮盘后不会指向旧下标。
                listener = NativeHotkeyListener(hotkey, "聊天轮盘",
                                               lambda key=hotkey: self.triggered.emit(key))
                added[binding] = listener
                listener.start()
            self.settings.replace_wheels(wheels)
        except Exception:
            for listener in added.values():
                listener.stop()
            raise
        for binding, listener in list(self._listeners.items()):
            if binding not in bindings:
                if not listener.stop():
                    # 保留引用，退出时仍可重试清理未退出的原生线程。
                    for new_listener in added.values():
                        new_listener.stop()
                    self.settings.replace_wheels(previous_wheels)
                    raise RuntimeError("旧聊天轮盘快捷键线程未能停止")
                del self._listeners[binding]
        self._listeners.update(added)
        self.wheel.cancel()
        self._active_wheel = 0

    def set_hotkey(self, hotkey: str, wheel_index: int = 0) -> None:
        self.settings.get_wheel(wheel_index)
        wheels = [self.settings.get_wheel(i).copy() for i in range(self.settings.wheel_count)]
        wheels[wheel_index]["hotkey"] = hotkey
        self._apply_wheels(wheels)

    def add_wheel(self, hotkey: str) -> None:
        self._apply_wheels([self.settings.get_wheel(i) for i in range(self.settings.wheel_count)]
                           + [new_chat_wheel(hotkey)])

    def remove_wheel(self, index: int) -> None:
        removed = self.settings.get_wheel(index)
        if self.settings.wheel_count == 1:
            raise ValueError("至少保留一个轮盘")
        self._apply_wheels([self.settings.get_wheel(i) for i in range(self.settings.wheel_count)
                            if i != index])
        referenced = {filename for i in range(self.settings.wheel_count)
                      for filename in self.settings.get_wheel(i)["slots"]}
        for filename in set(removed["slots"]) - referenced - {""}:
            try:
                remove_audio_clip(filename, self.settings._config_file or USER_CONFIG_FILE)
            except (OSError, ValueError):
                logger.warning("无法清理轮盘音频 %s", filename, exc_info=True)

    def _open(self, hotkey: str = "") -> None:
        # 当前显示内容与松键播放目标必须一致，按住期间忽略另一轮盘热键。
        if self._stopped or self.wheel.is_active():
            return
        index = 0
        if hotkey:
            binding = parse_hotkey(hotkey)
            index = next((i for i in range(self.settings.wheel_count)
                          if parse_hotkey(self.settings.get_wheel(i)["hotkey"]) == binding), -1)
            if index < 0:
                return
        self._active_wheel = index
        config = self.settings.get_wheel(index)
        # 标签非空同时代表「可选中」和「显示什么」，所以空槽必须留空串；
        # 文字槽没有文件名，得把文字本身当作显示文本传进去。
        labels = [
            label or (text if text else "音频") if (text or filename) else ""
            for filename, label, text in zip(
                config["slots"], config["labels"], config["texts"],
            )
        ]
        self.wheel.open_wheel(labels, parse_hotkey(config["hotkey"])[1])

    def play_slot(self, index: int, wheel_index: int | None = None) -> None:
        if self._stopped or not 0 <= index < 8:
            return
        selected = self._active_wheel if wheel_index is None else wheel_index
        if not 0 <= selected < self.settings.wheel_count:
            return
        config = self.settings.get_wheel(selected)
        # 一个槽一份内容：有文字就朗读文字，否则才去找音频文件。
        text = config["texts"][index].strip()
        if text:
            if self._speak_text is not None:
                self._speak_text(text, self.playback_finished.emit)
            return
        filename = config["slots"][index]
        if not filename:
            return
        # 即使调用方直接修改配置，也不能让槽引用托管目录以外的文件。
        if Path(filename).name != filename or any(c in filename for c in ("/", "\\", ":")):
            self.playback_error.emit("音频路径无效，请重新选择文件")
            return
        try:
            file = resolve_audio_clip(filename, self.settings._config_file or USER_CONFIG_FILE)
        except ValueError:
            self.playback_error.emit("音频路径无效，请重新选择文件")
            return
        if not file.is_file():
            self.playback_error.emit("音频文件已丢失，请重新选择文件")
            return
        # 每次提交绑定该槽的增益，切换槽位不会改变已经排队的音频。
        self._play_file(file, self.playback_error.emit, self.playback_finished.emit,
                        config["volumes"][index])

    def stop(self) -> None:
        self._stopped = True
        self.wheel.cancel()
        for binding, listener in list(self._listeners.items()):
            if listener.stop():
                del self._listeners[binding]
