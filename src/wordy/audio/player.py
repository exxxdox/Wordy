#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频设备查找和 WAV 播放。"""

import logging
import wave
from collections.abc import Mapping
from io import BytesIO
from typing import TypedDict, cast

import numpy as np
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


def _is_device_invalid_error(error: Exception) -> bool:
    """PortAudio -9996 (paInvalidDevice) 表示设备索引无法打开。"""
    return "-9996" in str(error) or "Invalid device" in str(error)


def _is_vb_cable_playback_device(p, device_index: int) -> bool:
    """判断已解析的输出端点是否属于 VB-CABLE 播放侧。"""
    try:
        info = p.get_device_info_by_index(device_index)
    except Exception as e:  # noqa: BLE001 - 诊断失败时保持原始声道配置
        logger.warning("无法读取输出设备 %s 信息，跳过 VB-CABLE 声道适配: %s", device_index, e)
        return False

    name = info.get("name", "") if isinstance(info, Mapping) else ""
    if not isinstance(name, str):
        return False
    normalized = name.casefold()
    return (
        "cable input" in normalized
        or "cable in " in normalized
        or "vb-cable" in normalized
        or "vb-audio virtual cable" in normalized
    )


def _duplicate_mono_to_stereo(data: bytes, sample_width: int) -> bytes:
    """逐样本复制单声道 PCM，避免双声道 VB-CABLE 将相邻样本拆成 L/R。"""
    if sample_width <= 0 or len(data) % sample_width != 0:
        raise ValueError("单声道 PCM 数据未按采样宽度对齐")
    samples = np.frombuffer(data, dtype=np.dtype((np.void, sample_width)))
    return np.repeat(samples, 2).tobytes()


class AudioPlayer:
    """播放 WAV 到默认输出设备或按名称/Host API 选择的输出设备。

    支持可选的音频路由器注入，使 TTS 音频同时混入虚拟设备。
    """

    def __init__(
        self,
        output_device_name: str | None = None,
        *,
        output_device: OutputDeviceSelection | None = None,
    ):
        self.output_device: OutputDeviceSelection | None = output_device
        if output_device is not None and output_device_name is None:
            # 保持 ``output_device_name`` 兼容性: 结构化选择存在时同步 raw name。
            output_device_name = output_device.get("name")
        self.output_device_name = output_device_name
        self._stream_p = None
        self._stream = None
        # 记录最近一次 open_stream 的实际格式/采样率（回退后可能与请求值不同）
        self._stream_format: int | None = None
        self._stream_rate: int | None = None
        self._stream_mono_upmix = False
        self._stream_sample_width: int | None = None
        # _open_pyaudio_stream 内部回退时写入，供调用方读取
        self._last_actual_format: int | None = None
        self._last_actual_rate: int | None = None
        self._last_mono_upmix = False
        self._last_sample_width: int | None = None

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

        default_idx = self._default_output_device_index(p)
        if default_idx is not None:
            try:
                info = p.get_device_info_by_index(default_idx)
                logger.info("使用系统默认输出设备 [%s]: %s", default_idx, info.get("name", "?"))
            except Exception:
                pass
        return default_idx

    def _try_fallback_stream_for_device(
        self,
        p,
        resolved_index: int,
        original_error: Exception,
        **open_kwargs,
    ) -> tuple[object | None, int | None, int | None]:
        """格式/采样率不兼容时尝试 int16 回退，避免 WASAPI 虚拟设备静默失败。

        回退顺序：
        1. paInt16 + 请求采样率
        2. paInt16 + 设备默认采样率

        返回 ``(stream, actual_format, actual_rate)``；全部为 None 表示回退失败。
        """
        rate = open_kwargs.get("rate", 48000)

        # Fallback 1: int16 at requested rate (float32→int16 是最常见修复)
        try:
            fb_kwargs = {**open_kwargs, "format": pyaudio.paInt16}
            stream = p.open(output=True, output_device_index=resolved_index, **fb_kwargs)
            logger.warning(
                "目标设备不支持请求的采样格式，已回退为 int16 / %s Hz（原始错误: %s）",
                rate,
                original_error,
            )
            return stream, pyaudio.paInt16, rate
        except Exception:
            pass

        # Fallback 2: int16 at device default rate
        try:
            device_info = p.get_device_info_by_index(resolved_index)
            default_rate = int(device_info.get("defaultSampleRate", 48000))
        except Exception:
            default_rate = 48000

        if default_rate != rate:
            try:
                fb_kwargs = {**open_kwargs, "format": pyaudio.paInt16, "rate": default_rate}
                stream = p.open(output=True, output_device_index=resolved_index, **fb_kwargs)
                logger.warning(
                    "目标设备不支持 %s Hz 采样率，已回退为 int16 / %s Hz（原始错误: %s）",
                    rate,
                    default_rate,
                    original_error,
                )
                return stream, pyaudio.paInt16, default_rate
            except Exception:
                pass

        return None, None, None

    def _try_open_default_device(
        self,
        p,
        default_index: int,
        original_error: Exception,
        **open_kwargs,
    ) -> object | None:
        """选定的输出设备无法打开时，回退到系统默认输出设备作为最后兜底。

        仅当默认设备与已选设备不同时才尝试，避免无限重试。
        """
        try:
            stream = p.open(
                output=True,
                output_device_index=default_index,
                **open_kwargs,
            )
            logger.warning(
                "选定输出设备不可用，已回退到系统默认设备 [%s]（原始错误: %s）",
                default_index,
                original_error,
            )
            return stream
        except Exception as fallback_error:
            logger.error(
                "回退到默认设备 [%s] 也失败: %s", default_index, fallback_error,
            )
            return None

    def _print_open_stream_error(self, error: Exception, *, rate: int | None = None) -> None:
        """输出打开音频流失败的提示。"""
        logger.error("无法打开音频流: %s", error)
        # PortAudio -9997 表示设备不接受请求采样率，并非设备被独占。
        if "-9997" in str(error) or "Invalid sample rate" in str(error):
            logger.error(
                "目标输出设备不支持 %s Hz 采样率，请让音源采样率与设备默认采样率保持一致",
                rate if rate is not None else "当前",
            )
            return
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

        格式/采样率不兼容时自动回退为 int16，并通过 ``_last_actual_format`` /
        ``_last_actual_rate`` 暴露实际协商结果（调用方在返回后读取）。
        """
        p = pyaudio.PyAudio()

        self._last_actual_format = None
        self._last_actual_rate = None
        self._last_mono_upmix = False
        self._last_sample_width = None

        resolved_index = self._resolve_output_device(p, device_index)
        if resolved_index is None:
            self._terminate_pyaudio(p)
            return None, None

        if format_from_width is not None:
            open_kwargs["format"] = p.get_format_from_width(format_from_width)

        # VB-CABLE 的 Windows 端点按双声道工作。实机回录验证表明，直接以
        # 单声道打开会把相邻 PCM 样本错误拆到 L/R，造成频率翻倍和失真。
        # 因此仅对 VB-CABLE 将设备流改成双声道，写入前再逐样本复制。
        if open_kwargs.get("channels") == 1 and _is_vb_cable_playback_device(p, resolved_index):
            open_kwargs["channels"] = 2
            self._last_mono_upmix = True

        try:
            stream = p.open(
                output=True,
                output_device_index=resolved_index,
                **open_kwargs,
            )
        except Exception as e:
            stream, fb_format, fb_rate = self._try_fallback_stream_for_device(
                p, resolved_index, e, **open_kwargs,
            )
            if stream is None:
                # 设备无效（-9996 / paInvalidDevice）：已选择设备无法打开，
                # 回退到系统默认输出后重试，避免因无效配置导致完全无声。
                if _is_device_invalid_error(e) and device_index is None:
                    default_index = self._default_output_device_index(p)
                    if default_index is not None and default_index != resolved_index:
                        stream = self._try_open_default_device(
                            p, default_index, e, **open_kwargs,
                        )
                if stream is None:
                    self._print_open_stream_error(e, rate=open_kwargs.get("rate"))
                    self._terminate_pyaudio(p)
                    return None, None
            # 更新 kwargs 以反映回退后的实际参数
            if fb_format is not None:
                open_kwargs["format"] = fb_format
            if fb_rate is not None:
                open_kwargs["rate"] = fb_rate

        self._last_actual_format = open_kwargs.get("format")
        self._last_actual_rate = open_kwargs.get("rate")
        self._last_sample_width = p.get_sample_size(open_kwargs["format"])
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
        # 记录实际协商的格式/采样率（回退后可能与请求值不同）
        self._stream_format = self._last_actual_format
        self._stream_rate = self._last_actual_rate
        self._stream_mono_upmix = self._last_mono_upmix
        self._stream_sample_width = self._last_sample_width
        logger.info("已打开流式播放到 [%s] %s",
                    self._last_actual_rate,
                    self.output_device_name or "系统默认设备")
        return True

    def write_stream(self, data: bytes) -> None:
        """向已打开的原始音频流写入音频块。

        调用方（CartesiaRealtimeTTS）已根据流格式选择匹配的 PCM 编码
        （int16 流 → pcm_s16le，float32 流 → pcm_f32le），此处直接透传。
        """
        if self._stream is None:
            raise RuntimeError("音频流尚未打开")

        if self._stream_mono_upmix:
            if self._stream_sample_width is None:
                raise RuntimeError("缺少流采样宽度，无法执行 VB-CABLE 双声道转换")
            data = _duplicate_mono_to_stereo(data, self._stream_sample_width)

        self._stream.write(data)  # type: ignore[union-attr]

    def get_stream_config(self) -> dict[str, int | None]:
        """返回当前已打开流的实际格式与采样率，用于诊断格式兼容性问题。

        未打开流时返回 ``{"format": None, "rate": None}``。
        """
        return {"format": self._stream_format, "rate": self._stream_rate}

    def query_output_device_default_rate(self) -> int | None:
        """查询当前选定输出设备的默认采样率，失败返回 None。

        创建临时 PyAudio 实例完成查询后立即释放，不影响活跃流。
        """
        p = None
        try:
            p = pyaudio.PyAudio()
            resolved_index = self._resolve_output_device(p, None)
            if resolved_index is None:
                return None
            info = p.get_device_info_by_index(resolved_index)
            rate = info.get("defaultSampleRate")
            return int(rate) if rate is not None else None
        except Exception:
            return None
        finally:
            if p is not None:
                try:
                    p.terminate()
                except Exception:
                    pass

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
        self._stream_mono_upmix = False
        self._stream_sample_width = None

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
        if isinstance(wav_path, BytesIO) and frames >= 0x7FFFFFFF:
            # 仅在流式 WAV 使用未知长度占位值时读取实际 PCM，避免普通内存 WAV 被额外复制。
            actual_pcm = wf.readframes(frames)
            bytes_per_frame = channels * sample_width
            frames = len(actual_pcm) // bytes_per_frame
            wf.rewind()
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

        try:
            logger.info("开始播放到 %s...", self.output_device_name or "默认输出设备")

            data = wf.readframes(1024)
            while data:
                if self._last_mono_upmix:
                    data = _duplicate_mono_to_stereo(data, sample_width)
                stream.write(data)  # type: ignore[union-attr]
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
