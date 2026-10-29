"""按 PCM 帧截取录音；源文件始终保留给调用方处理。"""

import math
import tempfile
import wave
from pathlib import Path


def recording_duration(source: Path) -> float:
    with wave.open(str(source), "rb") as audio:
        return audio.getnframes() / audio.getframerate()


def trim_recording(source: Path, start: float, end: float) -> Path:
    """创建独立临时 WAV，成功后由调用方接管其清理。"""
    with wave.open(str(source), "rb") as audio:
        rate = audio.getframerate()
        total = audio.getnframes()
        if not (math.isfinite(start) and math.isfinite(end) and 0 <= start < end <= total / rate):
            raise ValueError("请选择录音范围内的有效起止时间")
        first, last = round(start * rate), round(end * rate)
        if first >= last:
            raise ValueError("片段至少需要包含一帧音频")
        audio.setpos(first)
        data = audio.readframes(last - first)
        # 截断 WAV 不能被当作完整片段保存，避免悄悄丢掉用户选择的尾部。
        if len(data) != (last - first) * audio.getnchannels() * audio.getsampwidth():
            raise ValueError("录音文件不完整")
        params = audio.getparams()
    with tempfile.NamedTemporaryFile(prefix="wordy-trim-", suffix=".wav", delete=False) as temporary:
        destination = Path(temporary.name)
    try:
        with wave.open(str(destination), "wb") as output:
            output.setparams(params)
            output.writeframes(data)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return destination
