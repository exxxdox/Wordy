#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""火山引擎豆包语音 TTS 后端（单向流式语音合成HTTP / SSE）。"""

from __future__ import annotations

import base64
import json
import logging
import threading
from typing import Any

import pyaudio
import requests

from .engine import BackendTTSEngine, TTSAudioPlayer, VoiceInfo

logger = logging.getLogger(__name__)

TTS_SSE_URL = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse"
DEFAULT_RESOURCE_ID = "seed-icl-2.0"
# 与 Cartesia 一致：48 kHz，避免 Windows SRC 失真
DEFAULT_SAMPLE_RATE = 48000


def _ensure_volcengine_config(
    api_key: str | None,
    app_id: str | None,
    access_key: str | None,
    voice_id: str | None,
) -> None:
    """校验 Volcengine TTS 必要配置。"""
    has_new_auth = bool(api_key)
    has_legacy_auth = bool(app_id) and bool(access_key)
    if not has_new_auth and not has_legacy_auth:
        raise RuntimeError(
            "缺少火山引擎 API Key（新控制台）或 App ID + Access Key（旧控制台），请先在设置中配置"
        )
    if not voice_id:
        raise RuntimeError("缺少音色 ID，请在设置中输入 Speaker ID")


def _build_auth_headers(
    api_key: str | None,
    app_id: str | None,
    access_key: str | None,
) -> dict[str, str]:
    """按优先级构造鉴权 header：新控制台 X-Api-Key > 旧控制台。"""
    if api_key:
        return {"X-Api-Key": api_key}
    if app_id and access_key:
        return {
            "X-Api-App-Id": app_id,
            "X-Api-Access-Key": access_key,
        }
    return {}


def _volume_to_loudness_rate(volume: float) -> int:
    """Cartesia 音量 (0.5-2.0) → Volcengine loudness_rate (-50~100, 必须 int32)。"""
    rate = round((volume - 1.0) * 100)
    return max(-50, min(100, rate))


def _build_request_body(
    text: str,
    speaker: str,
    volume: float = 1.0,
    audio_format: str = "pcm",
    sample_rate: int = DEFAULT_SAMPLE_RATE,
) -> dict[str, Any]:
    """构造 Volcengine TTS V3 请求体（与 Cartesia 对齐的音频参数）。"""
    return {
        "req_params": {
            "text": text,
            "speaker": speaker,
            "audio_params": {
                "format": audio_format,
                "sample_rate": sample_rate,
                "loudness_rate": int(_volume_to_loudness_rate(volume)),
                "speech_rate": 0,  # int，1.0x 语速，与 Cartesia speed=1 对齐
            },
        }
    }


def _decode_base64_audio(data: str) -> bytes:
    """解码 base64 音频数据，自动补全 padding。"""
    return base64.b64decode(data + "=" * (-len(data) % 4))


class VolcengineStreamingTTS(BackendTTSEngine):
    """SSE 流式 TTS（单向流式语音合成HTTP）：边生成边流式播放。

    使用 HTTP SSE 协议，一次 POST 请求后通过 text/event-stream
    持续接收 JSON 事件行，每行含 base64 编码的 PCM 音频块。
    """

    def __init__(
        self,
        audio_player: TTSAudioPlayer,
        api_key: str | None = None,
        access_key: str | None = None,
        voice_id: str | None = None,
        resource_id: str = DEFAULT_RESOURCE_ID,
        sample_rate: int = DEFAULT_SAMPLE_RATE,
        volume: float = 1.0,
    ):
        super().__init__(audio_player=audio_player, voice_id=voice_id, volume=volume)
        self.api_key = api_key
        self.access_key = access_key
        self.resource_id = resource_id
        self.sample_rate = sample_rate
        self._stream_lock = threading.RLock()

    # ── 音色列表 ──────────────────────────────────────────────────

    def fetch_voices(self) -> list[VoiceInfo]:
        """Volcengine 不支持在线拉取音色列表；用户在设置中手动填写 Speaker ID。"""
        return []

    # ── 语音合成 ──────────────────────────────────────────────────

    def _build_headers(self) -> dict[str, str]:
        """构造包含鉴权和资源 ID 的请求头。"""
        headers = _build_auth_headers(self.api_key, None, self.access_key)
        headers["X-Api-Resource-Id"] = self.resource_id
        headers["Content-Type"] = "application/json"
        headers["Accept"] = "text/event-stream"
        return headers

    def _open_audio_stream(self) -> None:
        """打开 PyAudio 流式输出。

        Volcengine SSE 返回 base64(pcm_s16le)，必须用 paInt16 打开，不能用 float32。
        仅对 CABLE 设备匹配原生采样率以避免 Windows SRC 失真。
        """
        device_name = (self.audio_player.output_device_name or "").lower()
        is_cable = any(kw in device_name for kw in ("cable", "vb-audio"))

        stream_rate = self.sample_rate
        if is_cable:
            device_rate = self.audio_player.query_output_device_default_rate()
            if device_rate is not None and device_rate != self.sample_rate:
                logger.warning("CABLE 设备默认采样率为 %s Hz，已调整为设备原生采样率", device_rate)
                stream_rate = device_rate

        buffer_size = 1024
        # Volcengine 始终返回 pcm_s16le → 必须用 paInt16
        if self.audio_player.open_stream(
            audio_format=pyaudio.paInt16, channels=1, rate=stream_rate, frames_per_buffer=buffer_size
        ):
            stream_config = self.audio_player.get_stream_config()
            actual_rate = stream_config.get("rate")
            if actual_rate is not None:
                self.sample_rate = actual_rate
            logger.info("Volcengine 流式音频输出已打开 (paInt16, %s Hz)", actual_rate)
            return

        # int16 失败 → 尝试 float32 兜底（此时 PCM 会被错当 float 播，但至少不崩溃）
        logger.warning("paInt16 流打开失败，尝试 paFloat32 兜底")
        if self.audio_player.open_stream(
            audio_format=pyaudio.paFloat32, channels=1, rate=stream_rate, frames_per_buffer=buffer_size
        ):
            return

        raise RuntimeError("无法打开 PyAudio 流式输出")

    def _parse_sse_line(self, line: str) -> tuple[bytes | None, bool]:
        """解析单行 SSE 数据。返回 (pcm_bytes | None, is_done)。"""
        if not line or line.startswith(":"):
            return None, False
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            logger.debug("Volcengine SSE 跳过非 JSON 行: %s", line[:80])
            return None, False

        code = event.get("code")
        if code in (20000000, 3000):
            logger.debug("Volcengine SSE 流结束 (code=%s)", code)
            return None, True
        if isinstance(code, int) and code != 0:
            message = event.get("message", "未知错误")
            raise RuntimeError(f"Volcengine 流式 TTS 失败（code={code}）: {message}")

        data = event.get("data")
        if isinstance(data, str) and data:
            return _decode_base64_audio(data), False
        return None, False

    def _send_and_play(self, text: str) -> bool:
        """发送 SSE 请求并流式播放返回的音频块。"""
        _ensure_volcengine_config(self.api_key, None, self.access_key, self.voice_id)

        self._open_audio_stream()
        headers = self._build_headers()
        payload = _build_request_body(
            text=text, speaker=self.voice_id,  # type: ignore[arg-type]
            volume=self.volume,
            audio_format="pcm", sample_rate=self.sample_rate,
        )

        logger.debug("发送 Volcengine SSE 流式 TTS，文本长度: %d 字符", len(text))
        try:
            response = requests.post(
                TTS_SSE_URL, json=payload, headers=headers,
                stream=True, timeout=(10, 60),
            )
            response.raise_for_status()

            chunk_count = 0
            total_bytes = 0

            for raw_line in response.iter_lines(decode_unicode=True):
                if raw_line is None:
                    continue
                line: str = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8")
                if line.startswith("data:"):
                    line = line[5:].lstrip()
                pcm_bytes, is_done = self._parse_sse_line(line)
                if is_done:
                    break
                if pcm_bytes:
                    chunk_count += 1
                    total_bytes += len(pcm_bytes)
                    logger.info("Volcengine chunk #%d: %d bytes → write_stream", chunk_count, len(pcm_bytes))
                    self.audio_player.write_stream(pcm_bytes)

            logger.info("Volcengine SSE 完成: %d 音频块, %d bytes", chunk_count, total_bytes)
            return chunk_count > 0
        finally:
            self.audio_player.close_stream()

    def speak(self, text: str) -> bool:
        """流式生成并播放输入文本。"""
        with self._stream_lock:
            return self._send_and_play(text)

    def reset_audio_output(self) -> None:
        """输出设备变更时无需特殊处理——每次 speak() 都会重开流。"""

    def close(self) -> None:
        """释放资源。"""
        with self._stream_lock:
            self.audio_player.close_stream()


# ── 测试入口 ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    import sys as _sys
    logging.basicConfig(level=logging.DEBUG, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    from wordy.config import AppSettings as _AppSettings
    import wordy.secret as _secret

    _settings = _AppSettings.load()
    _provider = _settings.tts_providers.get("Volcengine", {})
    _ak = _secret.load_volcengine_access_key()
    _vid = _provider.get("voice_id")

    _sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    print(f"API Key: {'set' if _ak else 'MISSING'}")
    print(f"Speaker ID: {_vid or 'MISSING'}")
    print(f"Endpoint: {TTS_SSE_URL}")
    print(f"Resource: {DEFAULT_RESOURCE_ID}")
    print(f"Sample Rate: {DEFAULT_SAMPLE_RATE}")

    if not _ak or not _vid:
        print("ERROR: configure API Key and Speaker ID in settings first")
        _sys.exit(1)

    class _DummyPlayer:  # pragma: no cover
        output_device_name = None
        _buf: bytearray

        def open_stream(self, audio_format, channels, rate, frames_per_buffer=1024):
            print(f"[Dummy] open_stream fmt={audio_format} ch={channels} rate={rate}")
            self._buf = bytearray()
            return True

        def write_stream(self, data: bytes) -> None:
            self._buf += data

        def close_stream(self) -> None:
            print(f"[Dummy] close_stream, received {len(self._buf)} bytes PCM")

        def get_stream_config(self):
            return type("Cfg", (), {"get": lambda s, k: {"format": 8, "rate": 48000}.get(k)})()

        def query_output_device_default_rate(self):
            return None

    _player = _DummyPlayer()
    engine = VolcengineStreamingTTS(audio_player=_player, api_key=_ak, voice_id=str(_vid))
    test_text = "你好，这是一段测试语音，用于检查音质是否正常。"
    print(f"\nText: {test_text}")
    try:
        ok = engine.speak(test_text)
        print(f"Result: {'OK' if ok else 'FAIL (no audio)'} | {len(_player._buf)} bytes PCM")
        # 保存为 WAV
        import wave
        from pathlib import Path
        _out_dir = Path(".tmp")
        _out_dir.mkdir(exist_ok=True)
        _wav_path = _out_dir / "test.wav"
        with wave.open(str(_wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)  # 16-bit
            wf.setframerate(DEFAULT_SAMPLE_RATE)
            wf.writeframes(bytes(_player._buf))
        print(f"Saved: {_wav_path} ({_wav_path.stat().st_size} bytes)")
    except Exception as exc:
        print(f"FAIL: {exc}")
