#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频侦听引擎。

不捕获麦克风——由 Windows "侦听此设备" 功能将 mic 直通到 VB-CABLE Input。
本模块只负责：启用时将 TTS 音频输出重定向到 VB-CABLE Input，
让 TTS 语音和 mic 在系统层叠加后从 CABLE Output 输出。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TypedDict

import sounddevice as sd

from wordy.audio.driver import VBCableDriverManager
from wordy.paths import config_directory

logger = logging.getLogger(__name__)
_LISTEN_JOURNAL = "listen_policy_recovery.json"


def _journal_path() -> Path:
    return config_directory() / _LISTEN_JOURNAL


def _write_listen_journal(device: str, state: Any) -> None:
    """修改系统策略前落盘，保证进程异常退出后仍能恢复。"""
    path = _journal_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".listen_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            json.dump({"device": device, "enabled": state.enabled, "output_id": state.output_id}, file)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


def _clear_listen_journal() -> None:
    _journal_path().unlink(missing_ok=True)


def _recover_listen_journal() -> bool:
    path = _journal_path()
    if not path.is_file():
        return True
    try:
        from wordy.audio.listen_policy import ListenPolicyState, restore_listen_policy

        raw = json.loads(path.read_text(encoding="utf-8"))
        device = raw["device"]
        enabled = raw["enabled"]
        output_id = raw.get("output_id")
        if (not isinstance(device, str) or not isinstance(enabled, bool)
                or output_id is not None and not isinstance(output_id, str)):
            raise ValueError("invalid listen recovery journal")
        state = ListenPolicyState(enabled=enabled, output_id=output_id)
        if restore_listen_policy(device, state):
            _clear_listen_journal()
            return True
    except Exception:
        logger.exception("恢复上次异常退出遗留的麦克风侦听状态失败")
    return False


class RouterOutputDevice(TypedDict):
    """VB-CABLE Input 输出设备的结构化选择（兼容 AudioPlayer 接口）。"""
    name: str
    host_api_name: str


@dataclass
class RouterStats:
    """音频侦听状态，区分 TTS 路由与麦克风侦听是否均已就绪。"""

    is_running: bool = False
    listen_configured: bool = False


class AudioRouter:
    """音频侦听引擎。

    启用时 TTS 输出重定向到 VB-CABLE Input。
    麦克风直通由 Windows 系统"侦听此设备"功能处理——零延迟、零 CPU。

    使用方式：
        router = AudioRouter()
        if router.start():
            player.set_output_device(router.get_output_device())
    """

    def __init__(self, virtual_output: str | None = None):
        _recover_listen_journal()
        self.virtual_output = virtual_output or VBCableDriverManager.get_status().get("output_device_name")
        self._active = False
        self._mic_device: str | None = None
        self._listen_configured = False
        self._listen_original_state: Any | None = None
        self._listen_original_device: str | None = None

    # ── 生命周期 ─────────────────────────────────────────────────────────

    def start(self, *, mic_device: str | None = None) -> bool:
        """启用 TTS 路由，并尽力配置 Windows 麦克风侦听。

        mic_device 为 None 时不配置侦听——用户需手动选择麦克风。"""
        if self._active:
            return True
        # 恢复失败后的快照必须先还原，不能被重新启动的设备状态覆盖。
        if self._listen_configured and not self.stop():
            return False
        # Windows 侦听策略与播放器都依赖 WASAPI 端点，不能用其他 Host API 假启动。
        if self.get_output_device() is None:
            logger.error("无法找到 Windows WASAPI 的 VB-CABLE 输出设备")
            return False
        if self._resolve_output_device() is None:
            logger.error("无法解析 VB-CABLE 输出设备")
            return False

        self._active = True
        self._mic_device = mic_device
        self._listen_configured = False
        if mic_device is not None:
            self._listen_configured = self._enable_mic_listen(mic_device)

        logger.info("TTS 音频侦听已启用（TTS → CABLE Input）")
        return True

    def stop(self) -> bool:
        """停用路由；返回原侦听状态是否恢复成功，失败可重试。"""
        restored = True
        if self._listen_configured and self._mic_device is not None:
            restored = self._disable_mic_listen(self._mic_device)
            if not restored:
                logger.error("音频侦听已停止，但麦克风原侦听状态恢复失败: %s", self._mic_device)
        # 恢复失败时保留状态和快照，允许调用方再次 stop 重试。
        self._listen_configured = self._listen_configured and not restored
        self._active = False
        logger.info("音频侦听已停用")
        return restored

    def set_mic_device(self, device_name: str | None) -> bool:
        """切换麦克风时先清理旧设备，再启用新设备。返回是否成功。

        device_name 为 None 时停用侦听。新设备启用失败不回滚旧侦听，
        仅记录错误——用户可手动重试或切回旧设备。"""
        # 已停用但尚未恢复时，先完成旧快照恢复再允许修改麦克风。
        if not self._active and self._listen_configured and not self.stop():
            return False
        old = self._mic_device
        if old == device_name:
            return True

        # 先禁用旧设备侦听
        if self._active and self._listen_configured and old is not None:
            if not self._disable_mic_listen(old):
                logger.error("无法恢复旧麦克风状态，已取消切换: %s → %s", old, device_name)
                return False

        # 启用新设备侦听
        self._mic_device = device_name
        self._listen_configured = False
        if self._active and device_name is not None:
            self._listen_configured = self._enable_mic_listen(device_name)
            if not self._listen_configured:
                logger.error("麦克风被侦听失败，新设备未生效: %s", device_name)
                if self._listen_original_device == device_name:
                    # 新设备恢复仍失败时保留其快照，stop() 才能继续重试。
                    self._listen_configured = True
                    return False
                self._mic_device = old
                if old is not None:
                    self._listen_configured = self._enable_mic_listen(old)
                return False

        if device_name is not None:
            logger.debug("麦克风侦听已切换: %s → %s", old, device_name)
        else:
            logger.debug("麦克风侦听已停用（原: %s）", old)
        return True

    def _enable_mic_listen(self, mic_name: str) -> bool:
        """使用 WASAPI 的 VB-CABLE 名称配置 Windows Core Audio 侦听。"""
        try:
            from wordy.audio.listen_policy import get_listen_policy, restore_listen_policy, set_listen_policy

            # 上次异常退出遗留的恢复记录必须先消费完，否则覆盖它会永久丢掉
            # 麦克风原本的侦听状态（恢复记录里只有一份原始值）。
            if _journal_path().is_file() and not _recover_listen_journal():
                logger.error("上一次麦克风侦听恢复尚未完成，暂不接管新设备: %s", mic_name)
                return False

            original_state = get_listen_policy(mic_name)
            if original_state is None:
                logger.warning("无法安全保存麦克风原侦听状态，已跳过自动接管: %s", mic_name)
                return False

            cable_device = self.get_output_device()
            if cable_device is None:
                logger.warning("未找到 Windows WASAPI 的 CABLE Input，无法自动配置麦克风侦听")
                return False

            _write_listen_journal(mic_name, original_state)
            self._listen_original_state = original_state
            self._listen_original_device = mic_name
            # 日志已落盘即视为「已接管」：即使下面设置或回滚失败，stop() 也要能重试恢复。
            self._listen_configured = True
            target_name = cable_device["name"]
            if set_listen_policy(mic_name, target_name, enabled=True):
                logger.debug("已启用并验证麦克风侦听: %s → %s", mic_name, target_name)
                return True

            logger.warning(
                "无法自动配置麦克风侦听。请手动设置 Windows 声音 → %s → 属性 → 侦听 → 侦听此设备 → %s",
                mic_name,
                target_name,
            )
            if restore_listen_policy(mic_name, original_state):
                _clear_listen_journal()
                self._listen_original_state = None
                self._listen_original_device = None
                self._listen_configured = False
            return False
        except Exception:
            logger.exception("配置麦克风侦听失败")
            logger.warning("请手动在 Windows 声音设置中启用'侦听此设备'")
            return False

    def _disable_mic_listen(self, mic_name: str) -> bool:
        """禁用指定旧设备的侦听，避免切换时错误操作新设备。"""
        try:
            from wordy.audio.listen_policy import restore_listen_policy, set_listen_policy

            original_state = self._listen_original_state
            original_device = self._listen_original_device
            if original_state is not None and original_device == mic_name:
                ok = restore_listen_policy(mic_name, original_state)
                if not ok:
                    logger.warning("恢复麦克风原侦听状态失败: %s", mic_name)
            else:
                # 兼容没有快照的旧调用路径；仅关闭侦听，不修改历史目标端点。
                ok = set_listen_policy(
                    mic_name,
                    self.virtual_output or "CABLE Input",
                    enabled=False,
                )

            if ok:
                _clear_listen_journal()
                self._listen_original_state = None
                self._listen_original_device = None
            return ok
        except Exception:
            logger.exception("恢复麦克风侦听状态失败: %s", mic_name)
            # 保留快照，允许 stop 再次尝试恢复。
            return False

    def is_running(self) -> bool:
        """返回是否正在运行。"""
        return self._active

    # ── 设备查询 ─────────────────────────────────────────────────────────

    def get_output_device(self) -> RouterOutputDevice | None:
        """返回与 MMDevice 命名域一致的 WASAPI CABLE Input。"""
        status = VBCableDriverManager.get_status()
        index = status.get("output_device_index")
        name = status.get("output_device_name")
        if index is None or not isinstance(name, str):
            return None

        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
            device = devices[index]
            host_api_index = device.get("hostapi", -1)
            host_api_name = hostapis[host_api_index].get("name", "")
        except Exception as e:  # noqa: BLE001
            logger.warning("无法解析 VB-CABLE Host API: %s", e)
            return None

        if host_api_name != "Windows WASAPI":
            logger.warning("VB-CABLE 缺少 Windows WASAPI 输出端点，当前仅找到: %s", host_api_name)
            return None
        return RouterOutputDevice(name=name, host_api_name=host_api_name)

    def _resolve_output_device(self) -> int | None:
        """优先解析 WASAPI VB-CABLE 索引，兼容显式旧配置。"""
        idx = VBCableDriverManager.get_virtual_output_index()
        if idx is not None:
            return idx

        if self.virtual_output is not None:
            try:
                devices = sd.query_devices()
                for index, dev in enumerate(devices):
                    name = dev.get("name", "")
                    if isinstance(name, str) and self.virtual_output in name:
                        if dev.get("max_output_channels", 0) > 0:
                            return index
            except Exception as e:  # noqa: BLE001
                logger.warning("查询输出设备失败: %s", e)

        logger.error("无法解析虚拟输出设备")
        return None

    # ── 兼容接口 ─────────────────────────────────────────────────────────

    def get_stats(self) -> RouterStats:
        return RouterStats(
            is_running=self._active,
            listen_configured=self._listen_configured,
        )

    # 兼容旧调用方；生产 UI 不提供虚拟输出切换。
    def set_virtual_output(self, device_name: str | None) -> bool:
        was_running = self._active
        mic_device = self._mic_device
        if not self.stop():
            return False
        self.virtual_output = device_name
        if was_running:
            return self.start(mic_device=mic_device)
        return True
