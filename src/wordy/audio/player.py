#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频设备查找和 WAV 播放。"""


from collections.abc import Callable
import logging
import math
import threading
import wave
from threading import Event
from collections.abc import Mapping
from io import BytesIO
from typing import TypedDict, cast

import numpy as np
import pyaudio

# PortAudio 的初始化计数是进程级且非原子的。并发跑 Pa_Initialize/Pa_Terminate 会让
# 一个线程把全局设备表释放掉，另一个线程还在读它：表现为 Pa_GetDeviceCount 返回
# 垃圾值、报告「没有默认输出设备」，严重时原生访问违例直接段错误（没有 Python 栈）。
# 返听会同时跑主输出与返听两路播放（SidetoneAudioPlayer.play_wav），所以必须串行化。
# 只锁生命周期调用，不锁中间的实际播放，否则一次播放会把另一路整个堵住。
_PORTAUDIO_LIFECYCLE_LOCK = threading.Lock()


def _new_pyaudio():
    """构造 PyAudio 实例（= Pa_Initialize），全程持有生命周期锁。"""
    with _PORTAUDIO_LIFECYCLE_LOCK:
        return pyaudio.PyAudio()


def _terminate_pyaudio_locked(instance) -> None:
    """终止 PyAudio 实例（= Pa_Terminate），全程持有生命周期锁。"""
    with _PORTAUDIO_LIFECYCLE_LOCK:
        instance.terminate()


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
        p = _new_pyaudio()
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
                _terminate_pyaudio_locked(p)
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


def _resample_int16(data: bytes, channels: int, src_rate: int, dst_rate: int) -> bytes:
    """整段线性重采样 16-bit PCM；仅用于设备拒绝源采样率后的回退。

    回退时流按设备默认采样率打开，若继续写入源采样率的数据会变速变调。
    片段都很短（TTS/轮盘/录音 ≤60s），整段插值比维护跨块重采样状态更简单。
    """
    if not data or src_rate == dst_rate:
        return data
    samples = np.frombuffer(data, dtype="<i2").reshape(-1, channels).astype(np.float64)
    source_frames = samples.shape[0]
    target_frames = max(1, round(source_frames * dst_rate / src_rate))
    positions = np.linspace(0.0, source_frames - 1, target_frames)
    resampled = np.empty((target_frames, channels), dtype=np.float64)
    for channel in range(channels):
        resampled[:, channel] = np.interp(
            positions, np.arange(source_frames), samples[:, channel])
    return np.clip(np.rint(resampled), -32768, 32767).astype("<i2").tobytes()


def _scale_pcm(data: bytes, sample_width: int, volume: float) -> bytes:
    """饱和缩放 PCM，防止放大后整数回绕；8-bit WAV 的零点是 128。"""
    if volume == 1.0:
        return data
    if sample_width == 1:
        samples = np.frombuffer(data, dtype=np.uint8).astype(np.float64) - 128
    elif sample_width == 3:
        packed = np.frombuffer(data, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        samples = packed[:, 0] | packed[:, 1] << 8 | packed[:, 2] << 16
        samples = (samples ^ 0x800000) - 0x800000
    elif sample_width in (2, 4):
        samples = np.frombuffer(data, dtype=f"<i{sample_width}")
    else:
        raise ValueError("不支持的 PCM 采样宽度")
    peak = 2 ** (8 * sample_width - 1)
    scaled = np.clip(samples.astype(np.float64) * volume, -peak, peak - 1)
    if sample_width == 1:
        return (np.trunc(scaled) + 128).astype(np.uint8).tobytes()
    if sample_width == 3:
        return scaled.astype("<i4").view(np.uint8).reshape(-1, 4)[:, :3].tobytes()
    return scaled.astype(f"<i{sample_width}").tobytes()


class AudioPlayer:
    """播放 WAV 到默认输出设备或按名称/Host API 选择的输出设备。

    支持可选的音频侦听器注入，使 TTS 音频同时混入虚拟设备。
    """

    def __init__(
        self,
        *,
        output_device: OutputDeviceSelection | None = None,
    ):
        self.output_device: OutputDeviceSelection | None = output_device
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
        # 当前播放的取消句柄（播放层最后一道闸）：引擎或调用方忘了自检时，
        # write_stream/play_wav 也会在这里停下，保证任何来源的播放都可被打断。
        self._playback_lock = threading.Lock()
        self._playback_cancel: Event | None = None

    @property
    def output_device_name(self) -> str | None:
        """从结构化 output_device 派生设备名，仅用于显示和诊断。"""
        if self.output_device is None:
            return None
        return self.output_device.get("name")

    def set_output_device(self, output_device: OutputDeviceSelection | None) -> None:
        """更新输出设备选择，传入 ``None`` 表示使用系统默认设备。"""
        self.output_device = output_device

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
        3. 默认输出设备
        """
        if device_index is not None:
            return device_index

        if self.output_device is not None:
            name = self.output_device.get("name")
            host_api_name = self.output_device.get("host_api_name")
            if name:
                idx = self._lookup_named_output_device(p, name, host_api_name=host_api_name)
                if idx is not None:
                    logger.debug("找到设备 [%s]: %s @ %s", idx, name, host_api_name or "<any>")
                    return idx
                logger.warning("未找到输出设备 %r @ host_api=%r, 回退到默认输出设备", name, host_api_name)

        default_idx = self._default_output_device_index(p)
        if default_idx is not None:
            try:
                info = p.get_device_info_by_index(default_idx)
                logger.debug("使用系统默认输出设备 [%s]: %s", default_idx, info.get("name", "?"))
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
        p = _new_pyaudio()

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
        logger.debug("已打开流式播放到 [%s] %s",
                    self._last_actual_rate,
                    self.output_device_name or "系统默认设备")
        return True

    def begin_playback(self, cancel_event: Event) -> None:
        """登记本次播放的取消句柄；之后的 write_stream/play_wav 都会检查它。

        播放是串行的，所以播放器只需记住当前那一个句柄。只做协作取消：
        置位后在下次写入前停下，不跨线程关流（会和 PortAudio 生命周期竞争）。
        """
        with self._playback_lock:
            self._playback_cancel = cancel_event

    def end_playback(self, cancel_event: Event) -> None:
        """注销本次播放句柄；已被后继播放替换时不覆盖新句柄。"""
        with self._playback_lock:
            if self._playback_cancel is cancel_event:
                self._playback_cancel = None

    def _playback_interrupted(self, cancel_event: Event | None = None) -> bool:
        """本次播放是否已取消：显式句柄或播放层登记的令牌任一置位即算。"""
        token = self._playback_cancel
        return bool(
            (cancel_event is not None and cancel_event.is_set())
            or (token is not None and token.is_set())
        )

    def write_stream(self, data: bytes) -> None:
        """向已打开的原始音频流写入音频块。

        流统一按 paInt16 打开，调用方（CartesiaRealtimeTTS / VolcengineStreamingTTS）
        也固定请求 pcm_s16le，此处直接透传。取消令牌置位时抛 InterruptedError，
        让引擎的写入循环连同网络读取一起收尾。
        """
        if self._stream is None:
            raise RuntimeError("音频流尚未打开")
        if self._playback_interrupted():
            raise InterruptedError("播放已取消")

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

    def resolved_device_is_default(self) -> bool:
        """当前结构化选择是否解析到系统默认输出设备。"""
        p = None
        try:
            p = _new_pyaudio()
            resolved_index = self._resolve_output_device(p, None)
            default_index = self._default_output_device_index(p)
            return (
                resolved_index is not None
                and default_index is not None
                and resolved_index == default_index
            )
        except Exception:
            return False
        finally:
            if p is not None:
                try:
                    _terminate_pyaudio_locked(p)
                except Exception:
                    pass

    def query_output_device_default_rate(self) -> int | None:
        """查询当前选定输出设备的默认采样率，失败返回 None。

        创建临时 PyAudio 实例完成查询后立即释放，不影响活跃流。
        """
        p = None
        try:
            p = _new_pyaudio()
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
                    _terminate_pyaudio_locked(p)
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
            _terminate_pyaudio_locked(p)
        except Exception as e:  # noqa: BLE001
            logger.warning("pyaudio.terminate 失败: %s", e)

    def close_stream(self) -> None:
        """关闭已打开的原始音频流, 各步骤独立执行确保 refs 一定被清空。"""
        stream, self._stream = self._stream, None
        p, self._stream_p = self._stream_p, None
        self._stream_mono_upmix = False
        self._stream_sample_width = None
        # get_stream_config 是「当前是否还占着设备」的唯一可见状态；不归零会让
        # 调用方以为流还开着，继续往已关闭的流写或跳过补开。
        self._stream_format = None
        self._stream_rate = None

        self._close_stream(stream)
        self._terminate_pyaudio(p)

    def play_wav(
        self,
        wav_path: str | BytesIO,
        device_index: int | None = None,
        cancel_event: Event | None = None,
        *, volume: float = 1.0,
        on_progress: Callable[[float], None] | None = None,
    ) -> bool:
        """播放 WAV 文件到指定或匹配到的输出设备。"""
        if not math.isfinite(volume) or not 0 <= volume <= 2:
            raise ValueError("音频音量必须在 0 到 2 之间")
        if self._playback_interrupted(cancel_event):
            return False
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

        actual_rate = self._last_actual_rate or framerate
        resample = actual_rate != framerate
        if resample and sample_width != 2:
            # 回退流固定 int16；其他位深写进去只会是噪声，宁可明确失败。
            logger.error(
                "设备采样率 %s Hz 与源 %s Hz 不一致，且源不是 16-bit PCM，已停止播放",
                actual_rate, framerate,
            )
            self._close_stream(stream)
            wf.close()
            self._terminate_pyaudio(p)
            return False

        # 中途失败必须让调用方看得见：调用方只把 False 当失败上报 UI，
        # 返回 True 会让「播到一半设备炸了」对用户完全无声。
        ok = True
        try:
            logger.info("开始播放到 %s...", self.output_device_name or "默认输出设备")

            pending = (
                _resample_int16(wf.readframes(frames), channels, framerate, actual_rate)
                if resample else b""
            )
            data = b""
            submitted = 0.0
            if on_progress is not None and not self._playback_interrupted(cancel_event):
                on_progress(0.0)
            while True:
                # 退出时在当前小块写入结束后停止，避免等待整个长音频播放完。
                if self._playback_interrupted(cancel_event):
                    return False
                if resample:
                    if not pending:
                        break
                    data, pending = pending[:4096], pending[4096:]
                else:
                    data = wf.readframes(1024)
                    if not data:
                        break
                output_frames = len(data) // (channels * sample_width)
                if self._last_mono_upmix:
                    data = _duplicate_mono_to_stereo(data, sample_width)
                data = _scale_pcm(data, sample_width, volume)
                stream.write(data)  # type: ignore
                # 已提交帧只估计主输出进度，不把设备缓冲当作精确 DAC 时钟。
                submitted += output_frames / actual_rate
                if on_progress is not None and not self._playback_interrupted(cancel_event):
                    on_progress(min(duration, submitted))

            logger.debug("播放完成!")
        except KeyboardInterrupt:
            logger.warning("用户中断播放")
            ok = False
        except Exception as e:
            logger.error("播放错误: %s", e)
            ok = False
        finally:
            self._close_stream(stream)
            try:
                wf.close()
            except Exception as e:  # noqa: BLE001
                logger.warning("wave 文件关闭失败: %s", e)
            self._terminate_pyaudio(p)

        return ok and not self._playback_interrupted(cancel_event)
