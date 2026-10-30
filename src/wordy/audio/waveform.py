"""有界读取 PCM 波形峰值，供后台绘制使用。"""

import wave
from pathlib import Path
from threading import Event

import numpy as np


def waveform_peaks(source: Path, cancel_event: Event, bins: int = 1600) -> list[float]:
    """按时间均匀分箱，跨声道取绝对峰值，返回 0 到 1 的幅度。"""
    if bins < 1:
        raise ValueError("波形分箱数必须大于零")
    if cancel_event.is_set():
        return []
    with wave.open(str(source), "rb") as audio:
        if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE":
            raise ValueError("波形需要 16 位 PCM 音频")
        frames, channels = audio.getnframes(), audio.getnchannels()
        if frames == 0:
            raise ValueError("音频没有可显示的采样")
        count = min(bins, frames)
        peaks: list[float] = []
        for index in range(count):
            remaining = (index + 1) * frames // count - index * frames // count
            peak = 0
            while remaining:
                if cancel_event.is_set():
                    return []
                requested = min(remaining, 65536)
                data = audio.readframes(requested)
                if len(data) != requested * channels * 2:
                    raise ValueError("录音文件不完整")
                # 先扩到 int32，避免 abs(-32768) 溢出；不混合声道以免反相抵消。
                samples = np.frombuffer(data, dtype="<i2").astype(np.int32)
                peak = max(peak, int(np.abs(samples).max()))
                remaining -= requested
            peaks.append(peak / 32768.0)
        return [] if cancel_event.is_set() else peaks
