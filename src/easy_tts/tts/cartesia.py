#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Cartesia TTS 和 voices API 客户端。"""

import logging
import re
import threading
import uuid
from io import BytesIO
from typing import TYPE_CHECKING, Any, cast

import pyaudio
import requests
from cartesia import Cartesia
from websockets.sync.client import ClientConnection

# 向后兼容：从 cartesia_connect 模块重新导出 voice label 辅助函数
from easy_tts.tts.labels import VoiceLabelMaps, build_voice_label_maps  # noqa: F401

if TYPE_CHECKING:
    from cartesia.types.websocket_connection_options import WebsocketConnectionOptions

from .engine import BackendTTSEngine, TTSAudioPlayer, VoiceInfo


logger = logging.getLogger(__name__)

DEFAULT_CARTESIA_VERSION = "2026-03-01"
VOICES_URL = "https://api.cartesia.ai/voices?is_owner=true"
BYTES_TTS_URL = "https://api.cartesia.ai/tts/bytes"
REALTIME_PING_INTERVAL_SECONDS = 60
REALTIME_PING_TIMEOUT_SECONDS = 20
IDLE_TIMEOUT_CLOSE_CODE = 1000
IDLE_TIMEOUT_CLOSE_REASON = "connection idle timeout"


class LoggingClientConnection(ClientConnection):
    """记录 websockets 自动 keepalive ping 帧。"""

    def ping(self, data=None, ack_on_close: bool = False):
        logger.debug("发送 Cartesia realtime websocket 心跳 ping")
        return super().ping(data=data, ack_on_close=ack_on_close)


def _sanitize_secret_text(text: str) -> str:
    return re.sub(r"sk[-_][A-Za-z0-9_\-]+", "sk_***", text)


def _ensure_cartesia_config(api_key: str | None, voice_id: str | None) -> None:
    if not api_key:
        raise RuntimeError("缺少 Cartesia API Key，请先在设置中配置")
    if not voice_id:
        raise RuntimeError("缺少音色配置，请先在设置中刷新音色列表并选择一个音色")


def _voice_specifier(voice_id: str | None) -> dict[str, Any]:
    return {"mode": "id", "id": voice_id}


def _generation_config(volume: float) -> dict[str, Any]:
    return {"volume": volume, "speed": 1}


def _wav_output_format(sample_rate: int) -> dict[str, Any]:
    return {
        "container": "wav",
        "encoding": "pcm_s16le",
        "sample_rate": sample_rate,
    }


def _raw_float_output_format(sample_rate: int) -> dict[str, Any]:
    return {
        "container": "raw",
        "encoding": "pcm_f32le",
        "sample_rate": sample_rate,
    }


def _raise_for_status(response: requests.Response, error_prefix: str) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as e:
        response_text = _sanitize_secret_text(response.text[:300]).replace("\n", " ")
        raise RuntimeError(f"{error_prefix}：HTTP {response.status_code} {response_text}") from e


class CartesiaTTS(BackendTTSEngine):
    """Cartesia TTS 后端基类。"""

    def __init__(
        self,
        audio_player: TTSAudioPlayer,
        api_key: str | None = None,
        voice_id: str | None = None,
        model_id: str = "sonic-3.5",
        sample_rate: int = 44100,
        volume: float = 1.0,
    ):
        super().__init__(audio_player=audio_player, voice_id=voice_id, volume=volume)
        self.api_key = api_key
        self.model_id = model_id
        self.sample_rate = sample_rate

    def _build_request_args(self, output_format: dict[str, Any]) -> dict[str, Any]:
        """构造 bytes/realtime 共享的请求字段（model/voice/output_format/language/generation_config）。"""
        return {
            "model_id": self.model_id,
            "voice": _voice_specifier(self.voice_id),
            "output_format": output_format,
            "language": "zh",
            "generation_config": _generation_config(self.volume),
        }

    def _fetch_cartesia_voices(self, version: str = DEFAULT_CARTESIA_VERSION, timeout: int = 20) -> list[VoiceInfo]:
        """获取 Cartesia 音色列表。"""
        if not self.api_key:
            raise RuntimeError("缺少 Cartesia API Key，请先在设置中配置")

        headers = {
            "Cartesia-Version": version,
            "Authorization": f"Bearer {self.api_key}",
        }

        response = requests.get(VOICES_URL, headers=headers, timeout=timeout)
        _raise_for_status(response, "获取音色列表失败")

        try:
            data = response.json().get("data")
        except ValueError as e:
            raise RuntimeError("获取音色列表失败：Cartesia 返回的不是有效 JSON") from e

        if not isinstance(data, list):
            raise RuntimeError("获取音色列表失败：Cartesia 返回格式不是 Voice 数组")

        voices: list[VoiceInfo] = []
        for item in data:
            if not isinstance(item, dict):
                continue

            voice_id = item.get("id")
            name = item.get("name")
            if isinstance(voice_id, str) and isinstance(name, str) and voice_id and name:
                voices.append(item)

        return sorted(voices, key=lambda voice: str(voice["name"]).lower())

    def fetch_voices(self) -> list[VoiceInfo]:
        """获取 Cartesia 后端可用音色列表。"""
        return self._fetch_cartesia_voices()


class CartesiaBytesTTS(CartesiaTTS):
    """通过 Cartesia /tts/bytes 接口生成完整 WAV bytes 并播放。"""

    def __init__(
        self,
        audio_player: TTSAudioPlayer,
        api_key: str | None = None,
        voice_id: str | None = None,
        model_id: str = "sonic-3.5",
        version: str = DEFAULT_CARTESIA_VERSION,
        sample_rate: int = 44100,
        timeout: int = 60,
        volume: float = 1.0,
    ):
        super().__init__(
            audio_player=audio_player,
            api_key=api_key,
            voice_id=voice_id,
            model_id=model_id,
            sample_rate=sample_rate,
            volume=volume,
        )
        self.version = version
        self.timeout = timeout

    def generate(self, text: str) -> bytes:
        """调用 Cartesia bytes 接口生成 WAV bytes。"""
        _ensure_cartesia_config(self.api_key, self.voice_id)

        headers = {
            "Cartesia-Version": self.version,
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            **self._build_request_args(_wav_output_format(self.sample_rate)),
            "transcript": text,
            "speed": "normal",
        }

        logger.debug("发送 Cartesia bytes 文本，长度: %d 字符", len(text))
        response = requests.post(BYTES_TTS_URL, json=payload, headers=headers, timeout=self.timeout)
        _raise_for_status(response, "Cartesia bytes TTS 失败")
        logger.debug("收到 Cartesia bytes 音频: %s bytes", len(response.content))
        return response.content

    def speak(self, text: str) -> bool:
        """生成完整 WAV bytes 后播放。"""
        wav_bytes = self.generate(text)
        return self.audio_player.play_wav(BytesIO(wav_bytes))


class CartesiaRealtimeTTS(CartesiaTTS):
    """保持 Cartesia websocket 长连接并流式播放输入文本。"""

    def __init__(
        self,
        audio_player: TTSAudioPlayer,
        api_key: str | None = None,
        voice_id: str | None = None,
        model_id: str = "sonic-3.5",
        sample_rate: int = 44100,
        volume: float = 1.0,
    ):
        super().__init__(
            audio_player=audio_player,
            api_key=api_key,
            voice_id=voice_id,
            model_id=model_id,
            sample_rate=sample_rate,
            volume=volume,
        )
        self.client = None
        self._connection_manager = None
        self._connection = None
        # 使用 RLock 因为 speak() 持锁时会经由 _send_and_play_once 重入 connect()。
        # 同时确保 connect()/close()/speak() 共享同一把锁，避免生命周期竞态。
        self._lock = threading.RLock()

    def connect(self) -> None:
        """建立 websocket 长连接并打开流式音频输出。"""
        with self._lock:
            if self._connection is not None:
                return

            _ensure_cartesia_config(self.api_key, self.voice_id)
            self._open_audio_stream()
            self._open_websocket()

    def _get_client(self):
        if self.client is None:
            self.client = Cartesia(api_key=self.api_key)
        return self.client

    def _open_audio_stream(self) -> None:
        logger.info("正在打开 PyAudio 流式输出...")
        if not self.audio_player.open_stream(
            audio_format=pyaudio.paFloat32,
            channels=1,
            rate=self.sample_rate,
        ):
            raise RuntimeError("无法打开 PyAudio 流式输出")

    def _open_websocket(self) -> None:
        logger.info("正在连接 Cartesia realtime websocket...")
        client = self._get_client()
        websocket_options: dict[str, Any] = {
            "ping_interval": REALTIME_PING_INTERVAL_SECONDS,
            "ping_timeout": REALTIME_PING_TIMEOUT_SECONDS,
            "create_connection": LoggingClientConnection,
        }
        self._connection_manager = client.tts.websocket_connect(
            websocket_connection_options=cast("WebsocketConnectionOptions", websocket_options),
        )
        try:
            self._connection = self._connection_manager.__enter__()
        except Exception:
            self._connection_manager = None
            self.audio_player.close_stream()
            raise

        logger.info(
            "Cartesia realtime websocket 已连接 (ping_interval=%ss, sample_rate=%s)",
            REALTIME_PING_INTERVAL_SECONDS,
            self.sample_rate,
        )

    def _new_context_id(self) -> str:
        """生成便于日志追踪的 context_id。"""
        return f"cartesia-tts-{uuid.uuid4()}"

    def _log_close_info(self, prefix: str) -> None:
        """记录 websocket 关闭码和原因。"""
        raw_connection = getattr(self._connection, "_connection", None)
        close_code = getattr(raw_connection, "close_code", None)
        close_reason = getattr(raw_connection, "close_reason", "") or ""
        if close_code is not None or close_reason:
            logger.warning("%s: code=%s, reason=%r", prefix, close_code, close_reason)

    def _close_websocket(self) -> None:
        if self._connection_manager is not None:
            try:
                self._connection_manager.__exit__(None, None, None)
            except Exception as e:
                logger.warning("关闭 Cartesia realtime websocket 时出错: %s", e)

        self._connection_manager = None
        self._connection = None

    def _is_idle_timeout_close(self, close_code: int | None, close_reason: str) -> bool:
        return close_code == IDLE_TIMEOUT_CLOSE_CODE and close_reason == IDLE_TIMEOUT_CLOSE_REASON

    def _send_and_play_once(self, text: str) -> bool:
        if self._connection is None:
            self.connect()

        connection = self._connection
        if connection is None:
            raise RuntimeError("Cartesia realtime websocket 未建立连接")

        context_id = self._new_context_id()
        ctx = connection.context(
            context_id=context_id,
            **self._build_request_args(_raw_float_output_format(self.sample_rate)),
        )

        logger.debug("发送 Cartesia realtime 文本，长度: %d 字符 (context_id=%s)", len(text), context_id)
        ctx.push(text)
        ctx.no_more_inputs()

        for response in ctx.receive():
            if response.type == "chunk" and response.audio:
                logger.debug("Received audio chunk (%s bytes, context_id=%s)", len(response.audio), response.context_id)
                self.audio_player.write_stream(response.audio)
            elif response.type == "error":
                message = getattr(response, "message", "") or getattr(response, "title", "")
                raise RuntimeError(f"Cartesia realtime 返回错误: {message}")
            elif response.type == "done":
                break
        return True

    def speak(self, text: str) -> bool:
        """在已有 websocket 连接上发送单段文本并流式播放返回音频。"""
        from websockets.exceptions import ConnectionClosed

        with self._lock:
            try:
                return self._send_and_play_once(text)
            except ConnectionClosed as e:
                close_code = e.rcvd.code if e.rcvd is not None else None
                close_reason = e.rcvd.reason if e.rcvd is not None else ""
                logger.warning("Cartesia realtime websocket 已关闭: code=%s, reason=%r", close_code, close_reason)

                should_retry = self._is_idle_timeout_close(close_code, close_reason)
                self._close_websocket()
                if should_retry:
                    logger.warning("检测到 Cartesia idle timeout，重新连接后重发本次文本")
                    return self._send_and_play_once(text)

                raise
            except TimeoutError:
                self._log_close_info("Cartesia realtime websocket 接收超时")
                self._close_websocket()
                raise

    def close(self) -> None:
        """关闭 websocket 连接和流式音频输出。"""
        with self._lock:
            self._log_close_info("关闭 Cartesia realtime websocket")
            self._close_websocket()
            self.audio_player.close_stream()
