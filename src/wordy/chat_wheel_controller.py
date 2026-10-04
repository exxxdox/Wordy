"""轮盘全局快捷键、GUI 线程桥接与托管音频选择。"""

from collections.abc import Callable
from pathlib import Path

from PySide6.QtCore import QObject, Signal, Qt

from wordy.config import AppSettings, USER_CONFIG_FILE
from wordy.hotkey.native import NativeHotkeyListener, parse_hotkey
from wordy.ui.chat_wheel import ChatWheel
from wordy.audio.clips import resolve_audio_clip


class ChatWheelController(QObject):
    triggered = Signal()
    playback_error = Signal(str)

    def __init__(self, settings: AppSettings,
                 play_file: Callable[[Path, Callable[[str], None]], None],
                 on_error: Callable[[str], None]):
        super().__init__()
        self.settings = settings
        self._play_file = play_file
        self._listener: NativeHotkeyListener | None = None
        self._key_code = parse_hotkey(settings.chat_wheel_hotkey)[1]
        self._binding: tuple[int, int] | None = None
        self._stopped = False
        self.wheel = ChatWheel(self.play_slot)
        # 原生监听器运行在后台；显式排队保证窗口只由 GUI 线程操作。
        self.triggered.connect(self._open, Qt.ConnectionType.QueuedConnection)
        self.playback_error.connect(on_error, Qt.ConnectionType.QueuedConnection)

    def start(self) -> None:
        self.set_hotkey(self.settings.chat_wheel_hotkey)

    def set_hotkey(self, hotkey: str) -> None:
        binding = parse_hotkey(hotkey)
        if binding == parse_hotkey(self.settings.hotkey):
            raise ValueError("轮盘快捷键不能与输入框快捷键相同")
        if self._listener is not None and binding == self._binding:
            return
        listener = NativeHotkeyListener(hotkey, "聊天轮盘", self.triggered.emit)
        try:
            listener.start()
        except Exception:
            listener.stop()
            raise
        # 新注册成功后再注销旧键，冲突时保留可用绑定和持久化配置。
        previous = self._listener
        previous_hotkey = self.settings.chat_wheel_hotkey
        try:
            self.settings.chat_wheel_hotkey = hotkey
        except Exception:
            # 配置赋值先修改内存再保存，写盘失败也必须恢复内存中的旧键。
            object.__setattr__(self.settings, "chat_wheel_hotkey", previous_hotkey)
            listener.stop()
            raise
        self._listener = listener
        self._binding = binding
        self._key_code = binding[1]
        self.wheel.cancel()
        if previous is not None:
            previous.stop()

    def _open(self) -> None:
        if self._stopped:
            return
        labels = [self.settings.chat_wheel_labels[i] or "音频"
                  if filename else "" for i, filename in enumerate(self.settings.chat_wheel_slots)]
        self.wheel.open_wheel(labels, self._key_code)

    def play_slot(self, index: int) -> None:
        if self._stopped or not 0 <= index < 8:
            return
        filename = self.settings.chat_wheel_slots[index]
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
        self._play_file(file, self.playback_error.emit)

    def stop(self) -> None:
        self._stopped = True
        self.wheel.cancel()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
