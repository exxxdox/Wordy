#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频侦听控制器。

从 ``main.py:WordyApp`` 抽出，封装 AudioRouter 生命周期、
麦克风切换、输出设备保存/恢复等逻辑。
"""

from __future__ import annotations

import logging
from typing import cast

from wordy.audio.driver import VBCableDriverManager
from wordy.audio.player import AudioPlayer, OutputDeviceSelection
from wordy.audio.router import AudioRouter
from wordy.config import AppSettings

logger = logging.getLogger(__name__)


class RoutingController:
    """管理音频侦听引擎生命周期。

    不持有 TTS engine 引用——输出设备变更时通过回调通知调用方重建音频流。
    """

    def __init__(
        self,
        settings: AppSettings,
        player: AudioPlayer,
        on_output_device_changed: object | None = None,
    ) -> None:
        self._settings = settings
        self._player = player
        self._router: AudioRouter | None = None
        # 路由启用时保存本地输出，关闭后精确恢复
        self._saved_output_device: OutputDeviceSelection | None = None
        # 输出设备变更回调（通知 TTS engine 重建音频流）
        self._on_output_device_changed = on_output_device_changed

        self._init_from_config()

    # ── 初始化 ─────────────────────────────────────────────────────────────

    def _init_from_config(self) -> None:
        """根据配置初始化路由引擎（仅启动时调用）。"""
        s = self._settings
        if not s.audio_routing_enabled:
            return
        self._try_create_router(
            virtual_output=s.virtual_output_device,
            mic_device=s.mic_input_device,
        )

    def _try_create_router(
        self, *, virtual_output: object, mic_device: object = None
    ) -> AudioRouter | None:
        """创建并启动 AudioRouter，失败返回 None。"""
        if not VBCableDriverManager.is_installed():
            logger.warning("VB-CABLE 未安装，无法启动音频侦听")
            return None
        try:
            router = AudioRouter(
                virtual_output=virtual_output if isinstance(virtual_output, str) else None,
            )
            if router.start(
                mic_device=mic_device if isinstance(mic_device, str) else None,
            ):
                self._enable_routing(router)
                return router
            logger.warning("音频侦听引擎启动失败")
            return None
        except Exception as e:
            logger.exception("音频侦听引擎启动失败: %s", e)
            return None

    def _enable_routing(self, router: AudioRouter) -> None:
        """启用路由：保存当前输出设备，切换到 CABLE Input。"""
        self._router = router
        self._saved_output_device = self._player.output_device
        cable_device = router.get_output_device()
        if cable_device is not None:
            self._player.set_output_device(cast(OutputDeviceSelection, cable_device))
            logger.debug("音频输出已切换为 CABLE Input")
        self._notify_output_device_changed()
        if router.get_stats().listen_configured:
            logger.debug("麦克风侦听已自动配置并验证")
        else:
            logger.warning(
                "麦克风侦听未自动生效，请在 Windows 声音设置中手动配置到 CABLE Input"
            )

    def _disable_routing(self) -> None:
        """停用路由：恢复路由启用前保存的本地输出设备。"""
        saved_device = self._saved_output_device
        self._saved_output_device = None
        self._player.set_output_device(saved_device)
        self._notify_output_device_changed()

    def _notify_output_device_changed(self) -> None:
        if callable(self._on_output_device_changed):
            self._on_output_device_changed()

    # ── 路由配置变更 ──────────────────────────────────────────────────────

    def apply_config(self, route_config: dict[str, object]) -> None:
        """音频侦听配置变更回调。"""
        enabled = (
            bool(route_config["audio_routing_enabled"])
            if "audio_routing_enabled" in route_config
            else self._router is not None and self._router.is_running()
        )
        if not enabled:
            self.stop()
            return

        virtual_device = route_config.get("virtual_output_device")
        mic_device = route_config.get("mic_input_device")

        if self._router is None:
            if mic_device is None:
                mic_device = self._settings.mic_input_device
            self._try_create_router(
                virtual_output=virtual_device,
                mic_device=mic_device,
            )
        else:
            if "mic_input_device" in route_config:
                ok = self._router.set_mic_device(
                    mic_device if isinstance(mic_device, str) else None
                )
                if not ok:
                    logger.error("麦克风侦听切换失败，请检查设备名称是否正确")
            if "virtual_output_device" in route_config:
                self._router.set_virtual_output(
                    virtual_device if isinstance(virtual_device, str) else None
                )
                cable_device = self._router.get_output_device()
                if cable_device is not None:
                    self._player.set_output_device(
                        cast(OutputDeviceSelection, cable_device)
                    )
                    self._notify_output_device_changed()
            logger.debug("音频侦听配置已更新")

    # ── 输出设备变更（路由运行中只保存，不修改当前设备）──────────────────

    def on_output_device_change(self, device: object) -> None:
        """更新本地输出设备。

        路由运行时只保存待恢复设备，不改变 CABLE Input；
        路由停止时直接应用到 player。
        """
        if self._router is not None and self._router.is_running():
            if isinstance(device, dict):
                self._saved_output_device = cast(OutputDeviceSelection, device)
            else:
                self._saved_output_device = None
            logger.debug("音频侦听运行中，已保存本地输出设置，当前输出保持 CABLE Input")
            return

        if isinstance(device, dict):
            self._player.set_output_device(cast(OutputDeviceSelection, device))
        else:
            self._player.set_output_device(None)
        name = device.get("name") if isinstance(device, dict) else None
        logger.info("音频输出设备已切换为: %s", name if name else "系统默认")
        self._notify_output_device_changed()

    @property
    def is_mic_listen_configured(self) -> bool:
        """返回麦克风侦听是否已在运行时成功配置。"""
        if self._router is None:
            return False
        return self._router.get_stats().listen_configured

    @property
    def is_running(self) -> bool:
        return self._router is not None and self._router.is_running()

    # ── 停止 ───────────────────────────────────────────────────────────────

    def stop(self) -> None:
        """停止路由引擎并恢复原始输出设备。"""
        if self._router is not None:
            self._router.stop()
            self._router = None
            self._disable_routing()
            logger.debug("音频侦听已禁用")
