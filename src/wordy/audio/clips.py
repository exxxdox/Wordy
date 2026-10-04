"""轮盘音频的托管存储和 WAV 解码；播放仍使用现有音频路由。"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import os
from pathlib import Path
import shutil
from threading import Event
from tempfile import TemporaryDirectory
from uuid import uuid4
import wave


AUDIO_EXTENSIONS = frozenset({
    ".wav", ".mp3", ".ogg", ".flac", ".m4a", ".aac", ".wma", ".aiff", ".aif", ".opus",
})


def _clips_directory(config_file: Path | None = None) -> Path:
    if config_file is None:
        from wordy import config
        config_file = config.USER_CONFIG_FILE
    return Path(config_file).parent / "audio_clips"


def import_audio_clip(source: str | Path, config_file: Path | None = None) -> Path:
    """托管原文件，Windows 同卷优先硬链接，其他情况复制。"""
    source = Path(source)
    if not source.is_file():
        raise FileNotFoundError(f"找不到音频文件：{source.name}")
    suffix = source.suffix.lower()
    if suffix not in AUDIO_EXTENSIONS:
        raise ValueError("不支持此音频格式")
    directory = _clips_directory(config_file)
    directory.mkdir(parents=True, exist_ok=True)
    # 不保留外部文件名，避免覆盖同名文件及将路径内容写入托管目录。
    destination = directory / f"{uuid4().hex}{suffix}"
    try:
        if os.name == "nt":
            try:
                os.link(source, destination)
                return destination
            except OSError:
                # 跨卷、文件系统不支持或权限不足时，复制仍能完成用户导入。
                pass
        shutil.copy2(source, destination)
    except OSError:
        destination.unlink(missing_ok=True)
        raise
    return destination


def resolve_audio_clip(filename: str, config_file: Path | None = None) -> Path:
    """配置仅保存托管文件名，拒绝路径穿越和外部路径。"""
    if not filename or Path(filename).name != filename or "/" in filename or "\\" in filename:
        raise ValueError("无效的音频文件名")
    if Path(filename).suffix.lower() not in AUDIO_EXTENSIONS:
        raise ValueError("不支持此音频格式")
    directory = _clips_directory(config_file)
    path = directory / filename
    # 不允许被篡改的配置或目录符号链接把播放指向托管目录之外。
    if path.resolve().parent != directory.resolve():
        raise ValueError("音频文件不在托管目录中")
    return path


def remove_audio_clip(filename: str, config_file: Path | None = None) -> None:
    resolve_audio_clip(filename, config_file).unlink(missing_ok=True)


def _decode_to_wav(source: Path, destination: Path, cancel_event: Event | None = None) -> None:
    from PySide6.QtCore import QCoreApplication, QEventLoop, QTimer, QUrl
    from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat

    if QCoreApplication.instance() is None:
        raise RuntimeError("音频解码需要运行中的 Qt 应用")
    decoder = QAudioDecoder()
    loop = QEventLoop()
    timeout = QTimer()
    timeout.setSingleShot(True)
    cancellation_poll = QTimer()
    cancellation_poll.setInterval(100)
    errors: list[str] = []
    written_frames = 0
    cancelled = False
    # 固定解码为 PCM16，避免压缩/浮点 WAV 超出现有 wave 播放器的支持范围。
    requested = QAudioFormat()
    requested.setSampleRate(48000)
    requested.setChannelCount(2)
    requested.setSampleFormat(QAudioFormat.SampleFormat.Int16)
    decoder.setAudioFormat(requested)

    with wave.open(str(destination), "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)

        def fail(message: str) -> None:
            errors.append(message)
            loop.quit()

        def check_cancelled() -> bool:
            nonlocal cancelled
            if cancel_event is not None and cancel_event.is_set():
                cancelled = True
                loop.quit()
            return cancelled

        def read_buffer() -> None:
            nonlocal written_frames
            if check_cancelled():
                return
            try:
                buffer = decoder.read()
                if not buffer.isValid():
                    return
                if buffer.format() != requested:
                    fail("解码器不支持 PCM16 输出")
                    return
                output.writeframesraw(bytes(buffer.data()))
                written_frames += buffer.frameCount()
                # 按无进展超时，而非文件总时长，长音频正常解码不会被提前截断。
                timeout.start(30000)
            except Exception as error:
                fail(str(error))

        decoder.bufferReady.connect(read_buffer)
        decoder.finished.connect(loop.quit)
        decoder.error.connect(lambda _error: fail(decoder.errorString()))
        timeout.timeout.connect(lambda: fail("音频解码超时"))
        # 解码器没有产出时也允许退出，避免关机等待完整解码或30秒无进展超时。
        cancellation_poll.timeout.connect(check_cancelled)
        decoder.setSource(QUrl.fromLocalFile(str(source.resolve())))
        timeout.start(30000)
        if cancel_event is not None:
            cancellation_poll.start()
        try:
            # 从事件循环启动解码，避免 start 同步失败时 quit 发生在 exec 之前。
            QTimer.singleShot(0, decoder.start)
            loop.exec()
        finally:
            timeout.stop()
            cancellation_poll.stop()
            decoder.stop()
        if cancelled:
            raise InterruptedError("音频播放已取消")
        if errors or not written_frames:
            raise ValueError(f"音频解码失败：{errors[0] if errors else '文件没有音频内容'}")


@contextmanager
def decoded_audio_clip(source: str | Path, cancel_event: Event | None = None) -> Iterator[Path]:
    """提供可供 play_wav 使用的路径，退出播放范围后清理临时 PCM。"""
    source = Path(source)
    if cancel_event is not None and cancel_event.is_set():
        raise InterruptedError("音频播放已取消")
    if not source.is_file():
        raise FileNotFoundError(f"找不到音频文件：{source.name}")
    if source.suffix.lower() == ".wav":
        is_pcm = False
        try:
            with wave.open(str(source), "rb") as audio:
                is_pcm = audio.getcomptype() == "NONE" and audio.getnframes() > 0
        except (wave.Error, EOFError):
            pass
        if is_pcm:
            yield source
            return
    # 临时磁盘文件让长音频按块解码，避免把整个音频装入 GUI/worker 内存。
    with TemporaryDirectory(prefix="wordy-clip-") as directory:
        destination = Path(directory) / "decoded.wav"
        _decode_to_wav(source, destination, cancel_event)
        yield destination
