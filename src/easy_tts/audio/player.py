#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频设备查找和 WAV 播放。"""

import logging
import wave
from collections.abc import Mapping
from io import BytesIO
from typing import TypedDict, cast

import pyaudio


logger = logging.getLogger(__name__)

WASAPI_HOST_API_NAME = "Windows WASAPI"

WINDOWS_OUTPUT_MAPPER_ALIASES = frozenset(
    {
        "Microsoft Sound Mapper - Output",
        "Primary Sound Driver",
        "Microsoft 声音映射器 - Output",
        "主声音驱动程序",
    }
)


class OutputDeviceInfo(TypedDict):
    """枚举出的输出设备记录, 包含 Host API 元信息以便区分同名设备。"""

    index: int
    name: str
    host_api_index: int
    host_api_name: str
    display_name: str
    is_default: bool


class InputDeviceInfo(TypedDict):
    """枚举出的输入设备记录。"""

    index: int
    name: str
    host_api_index: int
    host_api_name: str
    display_name: str
    is_default: bool


class OutputDeviceSelection(TypedDict, total=False):
    """结构化输出设备选择: 通过 ``name`` + ``host_api_name`` 精确锁定 Host API 实例。"""

    name: str
    host_api_name: str | None


def _lookup_host_api(p, host_api_index: int) -> tuple[int, str]:
    """安全查询 Host API 名称, 失败时回退到 ``(host_api_index, 'Unknown')``。"""
    try:
        host_api_info = p.get_host_api_info_by_index(host_api_index)
    except Exception as e:  # noqa: BLE001 - host API 查询失败回退到 Unknown
        logger.warning("无法获取 host API %s 信息: %s", host_api_index, e)
        return host_api_index, "Unknown"

    resolved_index = host_api_info.get("index", host_api_index)
    resolved_name = host_api_info.get("name") or "Unknown"
    return cast(int, resolved_index), cast(str, resolved_name)


def _iter_output_devices(p):
    count = p.get_device_count()
    for i in range(count):
        try:
            info = p.get_device_info_by_index(i)
        except Exception as e:  # noqa: BLE001
            logger.warning("无法获取设备 %s 信息: %s", i, e)
            continue

        if not isinstance(info, Mapping):
            logger.warning("设备 %s 信息格式无效: %r", i, info)
            continue

        max_output_channels = info.get("maxOutputChannels", 0)
        if not isinstance(max_output_channels, (int, float)) or max_output_channels <= 0:
            continue

        if not isinstance(info.get("name"), str) or not isinstance(info.get("index"), int):
            logger.warning("设备 %s 缺少有效 name/index: %r", i, info)
            continue

        host_api_value = info.get("hostApi")
        if not isinstance(host_api_value, int):
            logger.warning("设备 %s 缺少有效 hostApi: %r", i, info)
            continue

        raw_host_api_index = host_api_value
        yield info, raw_host_api_index


def list_output_devices() -> list[OutputDeviceInfo]:
    """枚举系统可用的输出设备 (按 Host API 区分同名条目)。

    返回包含 ``index`` / ``name`` / ``host_api_index`` / ``host_api_name`` /
    ``display_name`` / ``is_default`` 字段的 dict 列表, 仅包含
    ``maxOutputChannels > 0`` 的设备。Windows 声音映射器等别名仍被过滤,
    但同名硬件在不同 Host API (MME / DirectSound / WASAPI) 下的副本会全部保留,
    便于用户精确选择。PyAudio 初始化或枚举失败时返回 ``[]``, 确保 PyAudio
    实例在创建成功时一定会被 ``terminate``。
    """
    p = None
    try:
        p = pyaudio.PyAudio()
    except Exception as e:  # noqa: BLE001 - 枚举失败需返回空列表
        logger.warning("初始化 PyAudio 失败: %s", e)
        return []

    try:
        try:
            default_info = p.get_default_output_device_info()
            default_index = default_info["index"]
        except Exception as e:  # noqa: BLE001
            logger.warning("无法获取默认输出设备: %s", e)
            default_index = None

        try:
            devices: list[OutputDeviceInfo] = []
            for info, raw_host_api_index in _iter_output_devices(p):
                name = cast(str, info["name"])
                if name in WINDOWS_OUTPUT_MAPPER_ALIASES:
                    continue

                host_api_index, host_api_name = _lookup_host_api(p, raw_host_api_index)
                if host_api_name != WASAPI_HOST_API_NAME:
                    continue

                display_name = f"{name} [{host_api_name}]"

                devices.append(
                    OutputDeviceInfo(
                        index=cast(int, info["index"]),
                        name=name,
                        host_api_index=host_api_index,
                        host_api_name=str(host_api_name),
                        display_name=display_name,
                        is_default=info["index"] == default_index,
                    )
                )

            return devices
        except Exception as e:  # noqa: BLE001 - 枚举过程异常视作不可用
            logger.warning("枚举输出设备失败: %s", e)
            return []
    finally:
        if p is not None:
            try:
                p.terminate()
            except Exception as e:  # noqa: BLE001
                logger.warning("pyaudio.terminate 失败: %s", e)

class AudioPlayer:
    """播放 WAV 到默认输出设备或按名称/Host API 选择的输出设备。

    支持可选的音频路由器注入，使 TTS 音频同时混入虚拟设备。
    """

    def __init__(
        self,
        output_device_name: str | None = None,
        *,
        output_device: OutputDeviceSelection | None = None,
        router: object | None = None,
    ):
        self.output_device: OutputDeviceSelection | None = output_device
        if output_device is not None and output_device_name is None:
            # 保持 ``output_device_name`` 兼容性: 结构化选择存在时同步 raw name。
            output_device_name = output_device.get("name")
        self.output_device_name = output_device_name
        self._router = router
        self._stream_p = None
        self._stream = None

    def set_router(self, router: object | None) -> None:
        """设置音频路由器，用于 TTS 音频注入。"""
        self._router = router

    def set_output_device_name(self, output_device_name: str | None) -> None:
        """更新目标输出设备名 (清空结构化选择)。传入 ``None`` 表示使用系统默认设备。"""
        self.output_device_name = output_device_name
        self.output_device = None

    def set_output_device(self, output_device: OutputDeviceSelection | None) -> None:
        """更新结构化输出设备选择, 同步 ``output_device_name`` 以保持向后兼容。"""
        self.output_device = output_device
        if output_device is None:
            self.output_device_name = None
        else:
            self.output_device_name = output_device.get("name")

    def list_output_devices(self) -> list[OutputDeviceInfo]:
        """实例方法委托到模块级 ``list_output_devices()``, 便于调用方直接通过 player 调用。"""
        return list_output_devices()

    def _lookup_named_output_device(
        self,
        p,
        name: str,
        host_api_name: str | None = None,
    ) -> int | None:
        """在所有 output-capable 设备中查找索引。

        若提供 ``host_api_name``, 优先匹配 ``(name, host_api_name)``; 否则按 raw name
        匹配第一个 output-capable 设备 (兼容旧版 legacy 行为)。
        """
        try:
            for info, raw_host_api_index in _iter_output_devices(p):
                if info.get("name") != name:
                    continue

                if host_api_name is None:
                    return cast(int, info["index"])

                _, resolved_host_api_name = _lookup_host_api(p, raw_host_api_index)
                if resolved_host_api_name == host_api_name:
                    return cast(int, info["index"])
        except Exception as e:  # noqa: BLE001 - get_device_count 失败回退
            logger.warning("无法获取设备数量: %s", e)
            return None

        return None

    def _default_output_device_index(self, p) -> int | None:
        """获取默认输出设备索引, 失败时返回 None 并记录警告。"""
        try:
            default_info = p.get_default_output_device_info()
        except Exception as e:  # noqa: BLE001
            logger.warning("无法获取默认输出设备: %s", e)
            return None

        return default_info["index"]

    def _resolve_output_device(self, p, device_index: int | None) -> int | None:
        """解析输出设备索引。

        优先级:
        1. 显式 ``device_index``
        2. 结构化 ``output_device`` 精确匹配 ``(name, host_api_name)``
        3. 旧版 ``output_device_name`` 按 raw name 精确匹配 (第一个命中胜出)
        4. 默认输出设备

        命名 / 结构化设备查找失败时记录警告后回退到默认设备索引; 默认设备查找失败时返回 ``None``。
        """
        if device_index is not None:
            return device_index

        if self.output_device is not None:
            name = self.output_device.get("name")
            host_api_name = self.output_device.get("host_api_name")
            if name:
                structured_index = self._lookup_named_output_device(
                    p, name, host_api_name=host_api_name
                )
                if structured_index is not None:
                    logger.info(
                        "找到设备 [%s]: %s @ %s",
                        structured_index,
                        name,
                        host_api_name or "<any>",
                    )
                    return structured_index

                logger.warning(
                    "未找到输出设备 %r @ host_api=%r, 回退到默认输出设备",
                    name,
                    host_api_name,
                )

        elif self.output_device_name:
            named_index = self._lookup_named_output_device(p, self.output_device_name)
            if named_index is not None:
                logger.info("找到设备 [%s]: %s", named_index, self.output_device_name)
                return named_index

            logger.warning(
                "未找到名为 %r 的输出设备, 回退到默认输出设备",
                self.output_device_name,
            )

        return self._default_output_device_index(p)

    def _print_open_stream_error(self, error: Exception) -> None:
        """输出打开音频流失败的提示。"""
        logger.error("无法打开音频流: %s", error)
        logger.error("请确保目标输出设备没有被其他程序独占")

    def _open_pyaudio_stream(
        self,
        device_index: int | None,
        *,
        format_from_width: int | None = None,
        **open_kwargs,
    ):
        """初始化 PyAudio、解析设备并打开输出流, 失败时已自行清理 PyAudio。

        成功时返回 ``(p, stream)``; 失败时返回 ``(None, None)``。
        ``open_kwargs`` 透传给 ``p.open`` (会自动追加 ``output=True`` 和
        ``output_device_index``)。若提供 ``format_from_width``, 则用
        ``p.get_format_from_width(...)`` 推导 ``format`` 字段, 避免在调用方
        额外创建 PyAudio 实例。
        """
        p = pyaudio.PyAudio()

        resolved_index = self._resolve_output_device(p, device_index)
        if resolved_index is None:
            self._terminate_pyaudio(p)
            return None, None

        if format_from_width is not None:
            open_kwargs["format"] = p.get_format_from_width(format_from_width)

        try:
            stream = p.open(
                output=True,
                output_device_index=resolved_index,
                **open_kwargs,
            )
        except Exception as e:
            self._print_open_stream_error(e)
            self._terminate_pyaudio(p)
            return None, None

        return p, stream

    def open_stream(
        self,
        audio_format,
        channels: int,
        rate: int,
        device_index: int | None = None,
        frames_per_buffer: int = 1024,
    ) -> bool:
        """打开原始音频流到指定或匹配到的输出设备。"""
        self.close_stream()

        p, stream = self._open_pyaudio_stream(
            device_index,
            format=audio_format,
            channels=channels,
            rate=rate,
            frames_per_buffer=frames_per_buffer,
        )
        if p is None or stream is None:
            return False

        self._stream = stream
        self._stream_p = p
        logger.info("已打开流式播放到 %s", self.output_device_name or "默认输出设备")
        return True

    def write_stream(self, data: bytes) -> None:
        """向已打开的原始音频流写入音频块。"""
        if self._stream is None:
            raise RuntimeError("音频流尚未打开")

        self._stream.write(data)

    @staticmethod
    def _close_stream(stream) -> None:
        """尽力关闭单个音频流: 先 stop_stream 再 close, 每步异常都记录但不抛出。"""
        if stream is None:
            return

        try:
            stream.stop_stream()
        except Exception as e:  # noqa: BLE001 - 清理路径需吞掉异常以便后续步骤执行
            logger.warning("stop_stream 失败: %s", e)

        try:
            stream.close()
        except Exception as e:  # noqa: BLE001
            logger.warning("stream.close 失败: %s", e)

    @staticmethod
    def _terminate_pyaudio(p) -> None:
        """尽力终止 PyAudio 实例, 异常记录但不抛出。"""
        if p is None:
            return

        try:
            p.terminate()
        except Exception as e:  # noqa: BLE001
            logger.warning("pyaudio.terminate 失败: %s", e)

    def close_stream(self) -> None:
        """关闭已打开的原始音频流, 各步骤独立执行确保 refs 一定被清空。"""
        stream, self._stream = self._stream, None
        p, self._stream_p = self._stream_p, None

        self._close_stream(stream)
        self._terminate_pyaudio(p)

    def play_wav(
        self,
        wav_path: str | BytesIO,
        device_index: int | None = None,
    ) -> bool:
        """播放 WAV 文件到指定或匹配到的输出设备。"""
        try:
            wf = wave.open(wav_path, "rb")
        except FileNotFoundError:
            logger.error("找不到文件 %s", wav_path)
            return False
        except wave.Error as e:
            logger.error("不是有效的 WAV 文件: %s", e)
            return False

        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        framerate = wf.getframerate()
        frames = wf.getnframes()
        duration = frames / float(framerate)

        logger.info(
            "WAV 文件信息: 声道数=%s, 采样位深=%s bit, 采样率=%s Hz, 总时长=%.2f 秒",
            channels,
            sample_width * 8,
            framerate,
            duration,
        )

        p, stream = self._open_pyaudio_stream(
            device_index,
            format_from_width=sample_width,
            channels=channels,
            rate=framerate,
            frames_per_buffer=1024,
        )
        if p is None or stream is None:
            wf.close()
            return False

        logger.info("开始播放到 %s...", self.output_device_name or "默认输出设备")
        logger.info("按 Ctrl+C 停止播放")

        # 提前解析路由器注入回调，避免每帧重复 getattr/callable
        _inject_fn = None
        _inject_needs_format = False
        if self._router is not None:
            fn = getattr(self._router, "inject_tts_from_wav", None)
            if callable(fn):
                _inject_fn = fn
                _inject_needs_format = True
            else:
                fn = getattr(self._router, "inject_tts", None)
                if callable(fn):
                    _inject_fn = fn

        try:
            data = wf.readframes(1024)
            while data:
                stream.write(data)
                if _inject_fn is not None:
                    try:
                        if _inject_needs_format:
                            _inject_fn(data, src_rate=framerate, src_channels=channels)
                        else:
                            _inject_fn(data)
                    except Exception:
                        logger.exception("TTS 音频注入路由器失败")
                data = wf.readframes(1024)

            logger.info("播放完成!")
        except KeyboardInterrupt:
            logger.warning("用户中断播放")
        except Exception as e:
            logger.error("播放错误: %s", e)
        finally:
            self._close_stream(stream)
            try:
                wf.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("wave 文件关闭失败: %s", e)
            self._terminate_pyaudio(p)

        return True
