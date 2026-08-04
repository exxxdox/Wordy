#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口 UI。"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSlider, QStyle, QTabWidget, QVBoxLayout, QWidget,
)

import easy_tts.secret
from easy_tts.config import (
    LOG_LEVELS, MAX_OVERLAY_OPACITY, MAX_VOLUME, MIN_OVERLAY_OPACITY, MIN_VOLUME,
    OVERLAY_OPACITY_STEP, TTS_BACKENDS, VOLUME_STEP,
)
from easy_tts.identity import normalize_identity
from easy_tts.tts.labels import VoiceLabelMaps, build_voice_label_maps
from easy_tts.ui.theme import (
    GREEN_ACCENT, TEXT_ERROR, TEXT_MUTED, TEXT_PRIMARY, TEXT_WARNING,
)
from easy_tts.ui.settings_state import (
    AudioOutputDevice, AudioOutputIdentity, PendingSettings, SettingsState, VoiceRecord,
)
from easy_tts.ui.settings_widgets import (
    CheckmarkCheckBox, NoWheelComboBox, NoWheelSlider, _SettingsDialog,
)
from easy_tts.ui.settings_style import build_settings_stylesheet
from easy_tts.ui.window import activate_window, center_window

INPUT_TEXT_COLOR = GREEN_ACCENT
SYSTEM_DEFAULT_AUDIO_OUTPUT_LABEL = "系统默认"
DIALOG_WIDTH = 720
DIALOG_HEIGHT = 580
DIALOG_MIN_WIDTH = 680
DIALOG_MIN_HEIGHT = 580
CONTENT_MARGIN = 20
SECTION_GAP = 12
INLINE_GAP = 10
BUTTON_GAP = 16
BUTTON_MIN_WIDTH = 88


class SettingsWindow:
    """应用设置窗口。"""

    def __init__(self, root, state: SettingsState, on_record_hotkey: Callable[[], None], on_refresh_voices: Callable[[], None], on_apply: Callable[["SettingsWindow"], None], on_close: Callable[[], None]):
        self.root = root
        self.on_record_hotkey = on_record_hotkey
        self.on_refresh_voices = on_refresh_voices
        self.on_apply = on_apply
        self.on_close = on_close
        self.pending_hotkey = (state.hotkey, state.hotkey_name)
        self.pending_voice_id = state.voice_id
        self.pending_voice_name = state.voice_name
        self.pending_volume = state.volume
        self.pending_overlay_opacity = state.overlay_opacity
        self.pending_tts_backend = state.tts_backend
        self.pending_fixed_center = state.fixed_center
        self.pending_audio_output_device_name = state.audio_output_device_name
        self.pending_audio_output_device_identity: AudioOutputIdentity | None = state.audio_output_device_identity
        self.pending_cartesia_api_key_action = "unchanged"
        self.pending_cartesia_api_key_value: str | None = None
        self.pending_log_level = state.log_level
        # 音频路由待应用配置
        self.pending_audio_routing_enabled = state.audio_routing_enabled
        self.pending_mic_input_device = state.mic_input_device
        self.pending_virtual_output_device = state.virtual_output_device
        self.cartesia_api_key_saved = state.cartesia_api_key_saved
        self._audio_output_label_to_identity: dict[str, AudioOutputIdentity] = {}
        self._audio_output_devices_error: Exception | None = None
        # 路由锁定时仅改变下拉框显示，保留用户关闭路由后使用的本地输出。
        self._local_audio_output_label: str | None = None
        self.voice_label_to_id: dict[str, str] = {}
        self.voice_label_to_name: dict[str, str] = {}
        self._closed = False
        self._closing = False
        self.current_label: QLabel = QLabel()
        self.pending_label: QLabel = QLabel()
        self.record_status_label: QLabel = QLabel()
        self.record_button: QPushButton = QPushButton()
        self.voice_combo: QComboBox = NoWheelComboBox()
        self.refresh_voices_button: QPushButton = QPushButton()
        self.voice_status_label: QLabel = QLabel()
        self.tts_backend_combo: QComboBox = NoWheelComboBox()
        self.audio_output_combo: QComboBox = NoWheelComboBox()
        self.audio_output_status_label: QLabel = QLabel()
        self.api_key_input: QLineEdit = QLineEdit()
        self.clear_api_key_button: QPushButton = QPushButton()
        self.api_key_status_label: QLabel = QLabel()
        self.log_level_combo: QComboBox = NoWheelComboBox()
        self.apply_status_label: QLabel = QLabel()
        self.volume_value_label: QLabel = QLabel()
        self.volume_slider: QSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.opacity_value_label: QLabel = QLabel()
        self.opacity_slider: QSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.fixed_center_check: QCheckBox = QCheckBox()
        # 音频路由 UI
        self.audio_route_enabled_check: QCheckBox = QCheckBox()
        self.virtual_output_combo: QComboBox = NoWheelComboBox()
        self.audio_route_status_label: QLabel = QLabel()
        self.vb_cable_install_button: QPushButton = QPushButton()
        self._input_device_names: list[str] = []
        self._output_device_names: list[str] = []

        parent = root if isinstance(root, QWidget) else None
        self.window: _SettingsDialog | None = _SettingsDialog(self, parent)
        self.window.setWindowTitle("设置")
        app_style = QApplication.style()
        settings_icon = app_style.standardIcon(QStyle.StandardPixmap.SP_ComputerIcon) if app_style is not None else QIcon()
        self.window.setWindowIcon(settings_icon)
        self.window.setWindowFlag(Qt.WindowType.FramelessWindowHint, True)
        self.window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
        self.window.setModal(False)
        self.window.setMinimumSize(DIALOG_MIN_WIDTH, DIALOG_MIN_HEIGHT)
        self.window.resize(DIALOG_WIDTH, DIALOG_HEIGHT)
        self.window.setSizeGripEnabled(True)
        self.window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.window.setStyleSheet(build_settings_stylesheet())
        window = self.window
        center_window(window, DIALOG_WIDTH, DIALOG_HEIGHT, parent)
        self._build(state)
        window.show()
        self.lift_and_focus()

    def exists(self) -> bool:
        """设置窗口是否仍存在。"""
        if self._closed or self.window is None:
            return False
        try:
            self.window.isVisible()
        except RuntimeError:
            self._closed = True
            self.window = None
            return False
        return True

    def lift_and_focus(self) -> None:
        """置顶并聚焦设置窗口。"""
        if not self.exists():
            return
        window = self.window
        if window is None:
            return
        window.raise_()
        window.activateWindow()
        window.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        activate_window(window)

    def close(self) -> None:
        """关闭设置窗口并清理绑定。"""
        if self.exists() and self.window is not None:
            self.window.close()

    def get_pending_settings(self) -> PendingSettings:
        """读取待应用设置。"""
        hotkey, hotkey_name = self.pending_hotkey
        self.pending_fixed_center = self.fixed_center_check.isChecked()
        # 路由启用时下拉框显示的是固定 CABLE Input，不能覆盖已保存的本地输出。
        if not self.pending_audio_routing_enabled:
            self._on_audio_output_selected(self.audio_output_combo.currentText())
        self._sync_pending_cartesia_api_key()
        return PendingSettings(
            hotkey, hotkey_name,
            self.pending_voice_id, self.pending_voice_name,
            self.pending_volume, self.pending_overlay_opacity,
            self.pending_tts_backend, self.pending_fixed_center,
            self.pending_audio_output_device_name,
            self.pending_audio_output_device_identity,
            self.pending_cartesia_api_key_action,
            self.pending_cartesia_api_key_value,
            self.pending_log_level,
            audio_routing_enabled=self.pending_audio_routing_enabled,
            mic_input_device=self.pending_mic_input_device,
            virtual_output_device=self.pending_virtual_output_device,
        )

    def set_recording_started(self) -> None:
        """更新为快捷键录制中状态。"""
        self._set_label(self.record_status_label, "请按下新的快捷键组合，Esc 取消录制", TEXT_WARNING)
        self.record_button.setText("录制中...")
        self.record_button.setEnabled(False)

    def set_record_result(self, hotkey: str | None, hotkey_name: str | None, error: Exception | None = None) -> None:
        """更新快捷键录制结果。"""
        self.record_button.setEnabled(True)
        self.record_button.setText("重新录制")
        if error is not None:
            self._set_label(self.record_status_label, f"录制失败：{error}", TEXT_ERROR)
            return
        if hotkey is None or hotkey_name is None:
            self._set_label(self.record_status_label, "已取消录制", TEXT_MUTED)
            self.record_button.setText("录制快捷键")
            return
        self.pending_hotkey = (hotkey, hotkey_name)
        self.pending_label.setText(f"待应用：{hotkey_name}")
        self._set_label(self.record_status_label, "已录制，点击应用后生效", INPUT_TEXT_COLOR)

    def set_hotkey_warning(self, text: str) -> None:
        """提示当前快捷键不可用，并引导用户重新录制。"""
        self.record_button.setEnabled(True)
        self.record_button.setText("重新录制")
        self._set_label(self.record_status_label, text, TEXT_ERROR)

    def set_voices_loading(self) -> None:
        """更新为音色加载中状态。"""
        self.refresh_voices_button.setEnabled(False)
        self.refresh_voices_button.setText("加载中...")
        self._set_label(self.voice_status_label, "正在加载音色列表...", TEXT_WARNING)

    def set_voices_error(self, error: Exception | str) -> None:
        """显示音色加载错误。"""
        self.refresh_voices_button.setEnabled(True)
        self.refresh_voices_button.setText("刷新音色列表")
        self._set_label(self.voice_status_label, f"加载失败：{error}", TEXT_ERROR)

    def set_voices_loaded(self, voices: list[VoiceRecord], selected_voice_id: str | None) -> list[str]:
        """填充音色列表。"""
        self.refresh_voices_button.setEnabled(True)
        self.refresh_voices_button.setText("刷新音色列表")
        label_maps = build_voice_label_maps(voices, selected_voice_id)
        self._apply_voice_label_maps(label_maps)
        self._set_label(self.voice_status_label, f"已加载 {len(label_maps.labels)} 个音色", INPUT_TEXT_COLOR)
        return label_maps.labels

    def set_apply_status(self, text: str, color: str = INPUT_TEXT_COLOR) -> None:
        self._set_label(self.apply_status_label, text, color)

    def clear_apply_status(self) -> None:
        self._set_label(self.apply_status_label, "", INPUT_TEXT_COLOR)

    def _handle_dialog_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            if not self._closed:
                try:
                    self.on_close()
                finally:
                    self._closed = True
        finally:
            self._closing = False

    def _build(self, state: SettingsState) -> None:
        outer_layout = QVBoxLayout(self.window)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        dialog_shell = QFrame()
        dialog_shell.setObjectName("dialogShell")
        outer_layout.addWidget(dialog_shell, 1)
        shell_layout = QVBoxLayout(dialog_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        title_label = QLabel("设置")
        title_label.setObjectName("dialogTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_label.setCursor(Qt.CursorShape.OpenHandCursor)
        title_label.installEventFilter(self.window)
        shell_layout.addWidget(title_label)
        tab_widget = QTabWidget()
        tab_widget.setObjectName("settingsTabs")
        tab_widget.setDocumentMode(True)
        tab_widget.setUsesScrollButtons(False)
        tab_widget.setElideMode(Qt.TextElideMode.ElideNone)
        shell_layout.addWidget(tab_widget, 1)

        hotkey_layout = self._create_tab_page(tab_widget, "全局快捷键")
        self._build_hotkey_section(hotkey_layout, state)
        hotkey_layout.addStretch(1)

        cartesia_layout = self._create_tab_page(tab_widget, "Cartesia 设置")
        self._build_api_key_section(cartesia_layout, state)
        self._add_inner_gap(cartesia_layout)
        self._build_backend_section(cartesia_layout)
        self._add_inner_gap(cartesia_layout)
        self._build_voice_section(cartesia_layout, state)
        cartesia_layout.addStretch(1)

        local_layout = self._create_tab_page(tab_widget, "本地设置")
        self._build_volume_section(local_layout)
        self._add_inner_gap(local_layout)
        self._build_opacity_section(local_layout)
        self._add_inner_gap(local_layout)
        self._build_position_section(local_layout)
        local_layout.addStretch(1)

        route_layout = self._create_tab_page(tab_widget, "音频路由")
        self._build_audio_route_section(route_layout, state)
        self._add_inner_gap(route_layout)
        self._build_audio_output_section(route_layout, state)
        route_layout.addStretch(1)

        log_layout = self._create_tab_page(tab_widget, "日志显示等级")
        self._build_log_level_section(log_layout)
        log_layout.addStretch(1)

        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(CONTENT_MARGIN, 0, CONTENT_MARGIN, CONTENT_MARGIN)
        footer_layout.setSpacing(0)
        self._build_buttons(footer_layout)
        shell_layout.addWidget(footer, 0)

    def _build_hotkey_section(self, parent_layout: QVBoxLayout, state: SettingsState) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("全局快捷键"))
        self.current_label = self._body_label(f"当前快捷键：{state.hotkey_name}", TEXT_MUTED)
        section.addWidget(self.current_label)
        self.pending_label = self._body_label(f"待应用：{state.hotkey_name}", INPUT_TEXT_COLOR, True)
        section.addWidget(self.pending_label)
        self.record_status_label = self._hint_label("点击录制后按下新的快捷键组合", TEXT_MUTED)
        section.addWidget(self.record_status_label)
        self.record_button = QPushButton("录制快捷键")
        self.record_button.clicked.connect(lambda _checked=False: self.on_record_hotkey())
        section.addWidget(self.record_button, 0, Qt.AlignmentFlag.AlignLeft)

    def _build_voice_section(self, parent_layout: QVBoxLayout, state: SettingsState) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("音色设置"))
        voice_display = state.voice_name or state.voice_id or "未设置"
        section.addWidget(self._body_label(f"当前音色：{voice_display}", TEXT_MUTED))
        self.voice_combo = NoWheelComboBox()
        self.voice_combo.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        self.voice_combo.setMinimumContentsLength(24)
        self.voice_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.voice_combo.currentTextChanged.connect(self._on_voice_selected)
        section.addWidget(self.voice_combo)
        self._apply_voice_label_maps(build_voice_label_maps(state.voices_cache, state.voice_id))
        voice_footer = QHBoxLayout()
        voice_footer.setContentsMargins(0, 8, 0, 0)
        voice_footer.setSpacing(INLINE_GAP)
        self.refresh_voices_button = QPushButton("刷新音色列表")
        self.refresh_voices_button.clicked.connect(lambda _checked=False: self.on_refresh_voices())
        voice_footer.addWidget(self.refresh_voices_button, 0, Qt.AlignmentFlag.AlignLeft)
        if state.voices_loading:
            self.refresh_voices_button.setEnabled(False)
            self.refresh_voices_button.setText("加载中...")
        status_text = "点击刷新后加载 Cartesia 音色"
        status_color = TEXT_MUTED
        if state.voices_cache:
            status_text = f"已缓存 {len(state.voices_cache)} 个音色"
            status_color = INPUT_TEXT_COLOR
        elif state.voices_loading:
            status_text = "正在后台加载音色列表..."
            status_color = TEXT_WARNING
        elif state.voice_fetch_error is not None:
            status_text = f"上次加载失败：{state.voice_fetch_error}"
            status_color = TEXT_ERROR
        self.voice_status_label = self._hint_label(status_text, status_color)
        voice_footer.addWidget(self.voice_status_label, 1)
        section.addLayout(voice_footer)

    def _build_api_key_section(self, parent_layout: QVBoxLayout, state: SettingsState) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("Cartesia API Key"))
        saved_status = "已保存" if state.cartesia_api_key_saved else "未保存"
        section.addWidget(self._body_label(f"当前密钥：{saved_status}", TEXT_MUTED))

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        placeholder = "已保存；留空保持不变" if state.cartesia_api_key_saved else "未保存；粘贴新密钥后应用"
        self.api_key_input.setPlaceholderText(placeholder)
        self.api_key_input.textChanged.connect(self._on_api_key_text_changed)
        section.addWidget(self.api_key_input)

        api_key_footer = QHBoxLayout()
        api_key_footer.setContentsMargins(0, 8, 0, 0)
        api_key_footer.setSpacing(INLINE_GAP)
        self.clear_api_key_button = QPushButton("清除已保存密钥")
        self.clear_api_key_button.clicked.connect(self._on_clear_api_key_clicked)
        api_key_footer.addWidget(self.clear_api_key_button, 0, Qt.AlignmentFlag.AlignLeft)
        self.api_key_status_label = self._hint_label("支持粘贴 CARTESIA_API_KEY=...；输入内容只在应用时提交。", TEXT_MUTED)
        api_key_footer.addWidget(self.api_key_status_label, 1)
        section.addLayout(api_key_footer)

    def _build_backend_section(self, parent_layout: QVBoxLayout) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("模式切换"))
        self.tts_backend_combo = NoWheelComboBox()
        self.tts_backend_combo.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        self.tts_backend_combo.setMinimumContentsLength(24)
        self.tts_backend_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tts_backend_combo.addItems(list(TTS_BACKENDS))
        self.tts_backend_combo.setCurrentText(self.pending_tts_backend)
        self.tts_backend_combo.currentTextChanged.connect(self._on_tts_backend_selected)
        section.addWidget(self.tts_backend_combo)
        section.addWidget(self._hint_label("选择 Cartesia bytes 或 realtime 模式，应用后立即生效。", TEXT_MUTED))

    def _build_audio_output_section(self, parent_layout: QVBoxLayout, state: SettingsState) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("音频输出"))
        self.audio_output_combo = NoWheelComboBox()
        self.audio_output_combo.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        self.audio_output_combo.setMinimumContentsLength(24)
        self.audio_output_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.audio_output_combo.currentTextChanged.connect(self._on_audio_output_selected)
        section.addWidget(self.audio_output_combo)
        self.audio_output_status_label.setObjectName("hintLabel")
        self._apply_audio_output_devices(state.audio_output_devices, state.audio_output_device_identity, state.audio_output_device_name, state.audio_output_devices_error)
        self._local_audio_output_label = self.audio_output_combo.currentText()
        self._sync_audio_output_control()
        section.addWidget(self.audio_output_status_label)

    def _build_volume_section(self, parent_layout: QVBoxLayout) -> None:
        section = self._create_section(parent_layout)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._section_title("音量设置"))
        self.volume_value_label = self._body_label(f"{self.pending_volume:.2f}x", INPUT_TEXT_COLOR)
        header.addWidget(self.volume_value_label, 0, Qt.AlignmentFlag.AlignRight)
        section.addLayout(header)
        self.volume_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, self._volume_to_slider(MAX_VOLUME))
        self.volume_slider.setSingleStep(1)
        self.volume_slider.setPageStep(max(1, int(0.25 / VOLUME_STEP)))
        self.volume_slider.setValue(self._volume_to_slider(self.pending_volume))
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        section.addWidget(self.volume_slider)
        section.addWidget(self._hint_label("范围 0.50x - 2.00x，默认 1.00x", TEXT_MUTED))

    def _build_opacity_section(self, parent_layout: QVBoxLayout) -> None:
        section = self._create_section(parent_layout)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._section_title("输入框透明度"))
        self.opacity_value_label = self._body_label(f"{round(self.pending_overlay_opacity * 100)}%", INPUT_TEXT_COLOR)
        header.addWidget(self.opacity_value_label, 0, Qt.AlignmentFlag.AlignRight)
        section.addLayout(header)
        self.opacity_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, self._opacity_to_slider(MAX_OVERLAY_OPACITY))
        self.opacity_slider.setSingleStep(1)
        self.opacity_slider.setPageStep(max(1, int(0.10 / OVERLAY_OPACITY_STEP)))
        self.opacity_slider.setValue(self._opacity_to_slider(self.pending_overlay_opacity))
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        section.addWidget(self.opacity_slider)
        section.addWidget(self._hint_label("范围 30% - 100%，默认 100%", TEXT_MUTED))

    def _build_position_section(self, parent_layout: QVBoxLayout) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("显示位置"))
        self.fixed_center_check = CheckmarkCheckBox("固定出现在屏幕中心")
        self.fixed_center_check.setChecked(self.pending_fixed_center)
        section.addWidget(self.fixed_center_check)
        section.addWidget(self._hint_label("取消勾选后，可拖动输入窗口；松开鼠标后自动记住位置。", TEXT_MUTED))

    def _build_audio_route_section(self, parent_layout: QVBoxLayout, state: SettingsState) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("音频路由"))

        # VB-CABLE 状态
        if state.vb_cable_installed:
            self.audio_route_status_label = self._hint_label(
                "VB-CABLE 虚拟驱动已安装。可将你的麦克风声音和 TTS 语音一起输出给 Discord、游戏等应用。",
                INPUT_TEXT_COLOR,
            )
        else:
            self.audio_route_status_label = self._hint_label(
                "需要先安装 VB-CABLE 虚拟音频驱动，才能让其他应用听到你的麦克风声音和 TTS 语音。",
                TEXT_WARNING,
            )
        section.addWidget(self.audio_route_status_label)

        if not state.vb_cable_installed:
            self.vb_cable_install_button = QPushButton("下载并安装 VB-CABLE")
            self.vb_cable_install_button.clicked.connect(self._on_open_vb_cable_download)
            section.addWidget(self.vb_cable_install_button)
            self._add_inner_gap(parent_layout)

        # 启用开关
        self.audio_route_enabled_check = CheckmarkCheckBox("启用音频路由")
        self.audio_route_enabled_check.setChecked(state.audio_routing_enabled)
        self.audio_route_enabled_check.stateChanged.connect(self._on_audio_route_enabled_changed)
        section.addWidget(self.audio_route_enabled_check)
        section.addWidget(self._hint_label(
            "启用后 TTS 语音将输出到 CABLE Input，与其他应用通过 CABLE Output 输入的声音叠加。\n"
            "请确保在 Windows 声音设置中将麦克风设为'侦听此设备'→ 播放设备选 CABLE Input。\n"
            '在 Discord / 游戏 / Zoom 里选择"CABLE Output"作为麦克风即可。',
            TEXT_MUTED,
        ))

        # 麦克风选择（配置 Windows 侦听）
        section.addWidget(self._section_title("麦克风输入"))
        section.addWidget(self._hint_label(
            "选择要侦听到 CABLE Input 的麦克风。"
            "开启路由后自动配置 Windows 侦听，无需手动设置。",
            TEXT_MUTED,
        ))
        self.mic_input_combo = NoWheelComboBox()
        self.mic_input_combo.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        self.mic_input_combo.setMinimumContentsLength(24)
        self._populate_input_devices(state.input_devices, state.mic_input_device)
        self.mic_input_combo.currentTextChanged.connect(self._on_mic_input_selected)
        section.addWidget(self.mic_input_combo)

    def _on_open_vb_cable_download(self, _checked: bool = False) -> None:
        from easy_tts.audio.driver import VBCableDriverManager
        VBCableDriverManager.open_download_page()

    def _on_audio_route_enabled_changed(self, state: int) -> None:
        enabled = state == Qt.CheckState.Checked.value
        if enabled and not self.pending_audio_routing_enabled:
            self._local_audio_output_label = self.audio_output_combo.currentText()
        self.pending_audio_routing_enabled = enabled
        self._sync_audio_output_control()

    def _find_routing_output_label(self) -> str | None:
        """查找路由固定使用的 Windows WASAPI CABLE Input 标签。"""
        for label, identity in self._audio_output_label_to_identity.items():
            name = identity.get("name")
            host_api = identity.get("host_api_name")
            if isinstance(name, str) and "CABLE Input" in name and host_api == "Windows WASAPI":
                return label
        return None

    def _sync_audio_output_control(self) -> None:
        """路由启用时固定显示 CABLE Input，关闭后恢复本地输出选择。"""
        combo = self.audio_output_combo
        combo.blockSignals(True)
        if self.pending_audio_routing_enabled:
            routing_label = self._find_routing_output_label()
            if routing_label is None:
                routing_label = "CABLE Input [Windows WASAPI]（未检测到）"
                if combo.findText(routing_label) < 0:
                    combo.addItem(routing_label)
            combo.setCurrentText(routing_label)
            combo.setEnabled(False)
            self._set_label(
                self.audio_output_status_label,
                "音频路由已启用，输出固定为 VB-CABLE Input，不可更改。",
                INPUT_TEXT_COLOR,
            )
        else:
            combo.setEnabled(True)
            local_label = self._local_audio_output_label or SYSTEM_DEFAULT_AUDIO_OUTPUT_LABEL
            if combo.findText(local_label) >= 0:
                combo.setCurrentText(local_label)
            else:
                combo.setCurrentText(SYSTEM_DEFAULT_AUDIO_OUTPUT_LABEL)
            if self._audio_output_devices_error is not None:
                self._set_label(
                    self.audio_output_status_label,
                    f"输出设备枚举失败：{self._audio_output_devices_error}",
                    TEXT_WARNING,
                )
            elif self._audio_output_label_to_identity:
                self._set_label(
                    self.audio_output_status_label,
                    f"已发现 {len(self._audio_output_label_to_identity)} 个输出设备",
                    TEXT_MUTED,
                )
            else:
                self._set_label(
                    self.audio_output_status_label,
                    "未发现输出设备，将使用系统默认",
                    TEXT_MUTED,
                )
        combo.blockSignals(False)
        if not self.pending_audio_routing_enabled:
            self._on_audio_output_selected(combo.currentText())

    def _on_mic_input_selected(self, text: str) -> None:
        self.pending_mic_input_device = text if text else None

    def _populate_input_devices(self, devices: list[dict[str, object]], selected: str | None) -> None:
        """填充麦克风输入设备下拉列表。"""
        combo = self.mic_input_combo
        combo.blockSignals(True)
        combo.clear()
        names: list[str] = []
        for dev in devices:
            name = dev.get("name") if isinstance(dev, dict) else str(dev)
            if isinstance(name, str) and name:
                names.append(name)
                combo.addItem(name)
        if selected and selected in names:
            combo.setCurrentText(selected)
        combo.blockSignals(False)

    def _build_log_level_section(self, parent_layout: QVBoxLayout) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("显示等级"))
        self.log_level_combo = NoWheelComboBox()
        self.log_level_combo.setSizeAdjustPolicy(QComboBox.AdjustToContentsOnFirstShow)
        self.log_level_combo.setMinimumContentsLength(24)
        self.log_level_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.log_level_combo.addItems(list(LOG_LEVELS))
        if self.pending_log_level not in LOG_LEVELS:
            self.pending_log_level = "INFO"
        self.log_level_combo.setCurrentText(self.pending_log_level)
        self.log_level_combo.currentTextChanged.connect(self._on_log_level_selected)
        section.addWidget(self.log_level_combo)
        section.addWidget(self._hint_label("选择日志窗口显示和收集的最低等级，应用后立即生效。", TEXT_MUTED))

    def _build_buttons(self, parent_layout: QVBoxLayout) -> None:
        self.apply_status_label = self._hint_label("", INPUT_TEXT_COLOR)
        self.apply_status_label.setObjectName("applyStatusLabel")
        parent_layout.addWidget(self.apply_status_label)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 14, 0, 0)
        button_row.setSpacing(BUTTON_GAP)
        button_row.addStretch(1)
        cancel_button = QPushButton("取消")
        cancel_button.setObjectName("cancelButton")
        cancel_button.setMinimumWidth(BUTTON_MIN_WIDTH)
        cancel_button.clicked.connect(lambda _checked=False: self.close())
        button_row.addWidget(cancel_button)
        apply_button = QPushButton("应用")
        apply_button.setObjectName("applyButton")
        apply_button.setMinimumWidth(BUTTON_MIN_WIDTH)
        apply_button.clicked.connect(lambda _checked=False: self.on_apply(self))
        button_row.addWidget(apply_button)
        parent_layout.addLayout(button_row)

    def _add_inner_gap(self, parent_layout: QVBoxLayout) -> None:
        parent_layout.addSpacing(SECTION_GAP)

    def _apply_voice_label_maps(self, label_maps: VoiceLabelMaps) -> None:
        self.voice_label_to_id = label_maps.label_to_id
        self.voice_label_to_name = label_maps.label_to_name
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        self.voice_combo.addItems(label_maps.labels)
        if label_maps.selected_label is not None:
            self.voice_combo.setCurrentText(label_maps.selected_label)
            self._set_pending_voice_from_label(label_maps.selected_label)
        elif label_maps.labels:
            self.voice_combo.setCurrentText(label_maps.labels[0])
            self._set_pending_voice_from_label(label_maps.labels[0])
        self.voice_combo.blockSignals(False)

    def _on_voice_selected(self, label: str) -> None:
        self._set_pending_voice_from_label(label)

    def _on_tts_backend_selected(self, backend: str) -> None:
        self.pending_tts_backend = backend

    def _on_log_level_selected(self, log_level: str) -> None:
        self.pending_log_level = log_level if log_level in LOG_LEVELS else "INFO"

    def _apply_audio_output_devices(self, devices: list[AudioOutputDevice] | list[str], selected_identity: AudioOutputIdentity | None, selected_device_name: str | None, error: Exception | None) -> None:
        self._audio_output_label_to_identity = {}
        self._audio_output_devices_error = error
        labels: list[str] = []
        identities: list[AudioOutputIdentity] = []
        if error is None:
            for device in devices:
                if isinstance(device, dict):
                    name_value = device.get("name")
                    if not isinstance(name_value, str) or not name_value:
                        continue
                    host_api_value = device.get("host_api_name")
                    host_api_name = host_api_value if isinstance(host_api_value, str) and host_api_value else None
                    display_value = device.get("display_name")
                    if isinstance(display_value, str) and display_value:
                        label = display_value
                    elif host_api_name is not None:
                        label = f"{name_value} [{host_api_name}]"
                    else:
                        label = name_value
                    identity: AudioOutputIdentity = dict(normalize_identity(name_value, host_api_name))
                elif isinstance(device, str):
                    if not device:
                        continue
                    label = device
                    identity = dict(normalize_identity(device))
                else:
                    continue
                labels.append(label)
                identities.append(identity)
                self._audio_output_label_to_identity[label] = identity

        self.audio_output_combo.blockSignals(True)
        self.audio_output_combo.clear()
        self.audio_output_combo.addItem(SYSTEM_DEFAULT_AUDIO_OUTPUT_LABEL)
        self.audio_output_combo.addItems(labels)

        selected_label: str | None = None
        if selected_identity is not None:
            target_name = selected_identity.get("name") if isinstance(selected_identity, dict) else None
            target_host = selected_identity.get("host_api_name") if isinstance(selected_identity, dict) else None
            for label, identity in zip(labels, identities):
                if identity.get("name") == target_name and identity.get("host_api_name") == target_host:
                    selected_label = label
                    break
        if selected_label is None and selected_device_name is not None:
            for label, identity in zip(labels, identities):
                if identity.get("name") == selected_device_name:
                    selected_label = label
                    break

        if selected_label is not None:
            self.audio_output_combo.setCurrentText(selected_label)
            chosen_identity = self._audio_output_label_to_identity[selected_label]
            self.pending_audio_output_device_identity = dict(chosen_identity)
            chosen_name = chosen_identity.get("name")
            self.pending_audio_output_device_name = chosen_name if isinstance(chosen_name, str) else None
        else:
            self.audio_output_combo.setCurrentText(SYSTEM_DEFAULT_AUDIO_OUTPUT_LABEL)
            self.pending_audio_output_device_identity = None
            self.pending_audio_output_device_name = None
        self.audio_output_combo.blockSignals(False)

        if error is not None:
            self._set_label(self.audio_output_status_label, f"输出设备枚举失败：{error}", TEXT_WARNING)
        elif labels:
            self._set_label(self.audio_output_status_label, f"已发现 {len(labels)} 个输出设备", TEXT_MUTED)
        else:
            self._set_label(self.audio_output_status_label, "未发现输出设备，将使用系统默认", TEXT_MUTED)

    def _on_api_key_text_changed(self, _text: str) -> None:
        self.pending_cartesia_api_key_action = "unchanged"
        self.pending_cartesia_api_key_value = None
        self._set_label(self.api_key_status_label, "输入新密钥后点击应用；留空保持不变。", TEXT_MUTED)

    def _on_clear_api_key_clicked(self, _checked: bool = False) -> None:
        self.api_key_input.blockSignals(True)
        self.api_key_input.clear()
        self.api_key_input.blockSignals(False)
        self.pending_cartesia_api_key_action = "clear"
        self.pending_cartesia_api_key_value = None
        self._set_label(self.api_key_status_label, "点击应用后清除已保存密钥。", TEXT_WARNING)

    def _sync_pending_cartesia_api_key(self) -> None:
        if self.pending_cartesia_api_key_action == "clear":
            self.pending_cartesia_api_key_value = None
            return
        normalized = easy_tts.secret.normalize_api_key_input(self.api_key_input.text())
        if normalized:
            self.pending_cartesia_api_key_action = "set"
            self.pending_cartesia_api_key_value = normalized
            return
        self.pending_cartesia_api_key_action = "unchanged"
        self.pending_cartesia_api_key_value = None

    def _on_audio_output_selected(self, device_label: str) -> None:
        if self.pending_audio_routing_enabled:
            return
        self._local_audio_output_label = device_label
        if device_label == SYSTEM_DEFAULT_AUDIO_OUTPUT_LABEL:
            self.pending_audio_output_device_identity = None
            self.pending_audio_output_device_name = None
            return
        identity = self._audio_output_label_to_identity.get(device_label)
        if identity is not None:
            self.pending_audio_output_device_identity = dict(identity)
            chosen_name = identity.get("name")
            self.pending_audio_output_device_name = chosen_name if isinstance(chosen_name, str) else None
            return
        self.pending_audio_output_device_identity = dict(normalize_identity(device_label))
        self.pending_audio_output_device_name = device_label

    def _on_volume_changed(self, value: int) -> None:
        self.pending_volume = round(MIN_VOLUME + value * VOLUME_STEP, 2)
        self.volume_value_label.setText(f"{self.pending_volume:.2f}x")

    def _on_opacity_changed(self, value: int) -> None:
        self.pending_overlay_opacity = round(MIN_OVERLAY_OPACITY + value * OVERLAY_OPACITY_STEP, 2)
        self.opacity_value_label.setText(f"{round(self.pending_overlay_opacity * 100)}%")

    def _set_pending_voice_from_label(self, label: str) -> None:
        voice_name = self.voice_label_to_name.get(label, label)
        voice_id = self.voice_label_to_id.get(label)
        if voice_id is None:
            return
        self.pending_voice_id = voice_id
        self.pending_voice_name = voice_name
        if hasattr(self, "voice_status_label"):
            self._set_label(self.voice_status_label, f"待应用音色：{voice_name}", INPUT_TEXT_COLOR)

    def _create_section(self, parent_layout: QVBoxLayout) -> QVBoxLayout:
        section = QVBoxLayout()
        section.setContentsMargins(0, 0, 0, 0)
        section.setSpacing(6)
        parent_layout.addLayout(section)
        return section

    def _create_tab_page(self, tab_widget: QTabWidget, title: str) -> QVBoxLayout:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        content_layout.setContentsMargins(CONTENT_MARGIN, CONTENT_MARGIN, CONTENT_MARGIN, CONTENT_MARGIN)
        content_layout.setSpacing(0)
        scroll_area.setWidget(content)
        tab_widget.addTab(scroll_area, title)
        return content_layout

    def _section_title(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    def _body_label(self, text: str, color: str, emphasized: bool = False) -> QLabel:
        label = QLabel(text)
        label.setObjectName("bodyLabelEmphasis" if emphasized else "bodyLabel")
        label.setProperty("textColor", color)
        label.setStyleSheet(f"color: {color};")
        label.setWordWrap(True)
        return label

    def _hint_label(self, text: str, color: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hintLabel")
        label.setProperty("textColor", color)
        label.setStyleSheet(f"color: {color};")
        label.setWordWrap(True)
        return label

    def _set_label(self, label: QLabel, text: str, color: str) -> None:
        label.setText(text)
        label.setProperty("textColor", color)
        label.setStyleSheet(f"color: {color};")

    def _volume_to_slider(self, volume: float) -> int:
        clamped = min(max(volume, MIN_VOLUME), MAX_VOLUME)
        return round((clamped - MIN_VOLUME) / VOLUME_STEP)

    def _opacity_to_slider(self, opacity: float) -> int:
        clamped = min(max(opacity, MIN_OVERLAY_OPACITY), MAX_OVERLAY_OPACITY)
        return round((clamped - MIN_OVERLAY_OPACITY) / OVERLAY_OPACITY_STEP)
