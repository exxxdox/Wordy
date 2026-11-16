#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""管理 Windows 麦克风侦听。"""

from __future__ import annotations

import logging

from wordy.audio.driver import VBCableDriverManager
from wordy.audio.listen_policy import ListenPolicyState, get_listen_policy, set_listen_policy
from wordy.audio.router import AudioRouter
from wordy.config import AppSettings

logger = logging.getLogger(__name__)


class RoutingController:
    """管理音频侦听引擎生命周期。"""

    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._router: AudioRouter | None = None

        self._init_from_config()

    # ── 初始化 ─────────────────────────────────────────────────────────────

    def _init_from_config(self) -> None:
        """根据配置初始化路由引擎（仅启动时调用）。"""
        s = self._settings
        listen_state = self.mic_listen_state
        # Windows 可读取时优先使用实际状态，读取不到时使用已保存的侦听偏好。
        enabled = listen_state.enabled if listen_state is not None else s.audio_routing_enabled
        if not enabled:
            return
        router = self._try_create_router(
            virtual_output=s.virtual_output_device,
            mic_device=s.mic_input_device,
            configure_listen=False,
        )
        if router is None:
            # 保存值必须反映真实运行态，避免启动失败后设置页仍显示已启用。
            s.audio_routing_enabled = False

    def _try_create_router(
        self, *, virtual_output: object, mic_device: object = None, configure_listen: bool = True
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
                configure_listen=configure_listen,
            ):
                self._router = router
                return router
            logger.warning("音频侦听引擎启动失败")
            return None
        except Exception as e:
            logger.exception("音频侦听引擎启动失败: %s", e)
            return None

    # ── 路由配置变更 ──────────────────────────────────────────────────────

    def apply_config(self, route_config: dict[str, object]) -> bool:
        """应用音频侦听配置，并返回运行态是否切换成功。"""
        enabled = (
            bool(route_config["audio_routing_enabled"])
            if "audio_routing_enabled" in route_config
            else self._router is not None and self._router.is_running()
        )
        if not enabled:
            if self._router is None and "audio_routing_enabled" in route_config:
                device = self._settings.mic_input_device
                # Windows 可能在 Wordy 未启用路由时被手动打开。
                if device:
                    return set_listen_policy(device, "", enabled=False)
            return self.stop()

        virtual_device = route_config.get("virtual_output_device")
        mic_device = route_config.get("mic_input_device")

        if self._router is None:
            if mic_device is None:
                mic_device = self._settings.mic_input_device
            return self._try_create_router(
                virtual_output=virtual_device,
                mic_device=mic_device,
            ) is not None
        else:
            if route_config.get("audio_routing_enabled") is True and self._settings.mic_input_device:
                cable = self._router.get_output_device()
                return cable is not None and set_listen_policy(
                    self._settings.mic_input_device, cable["name"], enabled=True,
                )
            if "mic_input_device" in route_config:
                ok = self._router.set_mic_device(
                    mic_device if isinstance(mic_device, str) else None
                )
                if not ok:
                    logger.error("麦克风侦听切换失败，请检查设备名称是否正确")
                    return False
            if "virtual_output_device" in route_config:
                if not self._router.set_virtual_output(
                    virtual_device if isinstance(virtual_device, str) else None
                ):
                    if not self._router.is_running():
                        self.stop(preserve_listen=True)
                    logger.error("虚拟输出切换失败")
                    return False
            logger.debug("音频侦听配置已更新")
            return True

    @property
    def mic_listen_state(self) -> ListenPolicyState | None:
        """读取 Windows 当前的麦克风侦听状态，供设置页回显真实结果。

        不读路由内部标志：设置页要显示系统里实际生效的状态，
        包括用户直接在 Windows 中修改的状态。"""
        device = self._settings.mic_input_device
        if not device:
            return None
        return get_listen_policy(device)

    @property
    def is_running(self) -> bool:
        return self._router is not None and self._router.is_running()

    # ── 停止 ───────────────────────────────────────────────────────────────

    def stop(self, *, preserve_listen: bool = False) -> bool:
        """侦听关闭失败时保留引擎，以便再次尝试。"""
        if self._router is None:
            return True
        if not self._router.stop(preserve_listen=preserve_listen):
            return False
        self._router = None
        logger.debug("音频侦听已禁用")
        return True
