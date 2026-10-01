#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口 UI。直接读写 AppSettings，每次控件变更即时持久化。"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSlider, QStyle, QTabWidget, QVBoxLayout, QWidget,
)

from wordy.config import AppSettings
from wordy.constants import (
    LOG_LEVELS, MAX_OVERLAY_OPACITY, MAX_VOLUME,
    MIN_OVERLAY_OPACITY, MIN_VOLUME, OVERLAY_OPACITY_STEP, VOLUME_STEP,
)
from wordy.tts.constants import (
    TTS_API_PROVIDER_CARTESIA, TTS_API_PROVIDER_VOLCENGINE, TTS_API_PROVIDERS,
    TTS_BACKENDS_BY_PROVIDER,
)
from wordy.identity import normalize_identity
from wordy.tts.labels import VoiceLabelMaps, build_voice_label_maps
from wordy.ui.theme import (
    GREEN_ACCENT, TEXT_ERROR, TEXT_MUTED, TEXT_PRIMARY, TEXT_WARNING,
)
from wordy.ui.tts_panels import PROVIDER_PANELS
from wordy.ui.settings_state import (
    AudioOutputDevice, AudioOutputIdentity, SettingsState, VoiceRecord,
)
from wordy.ui.settings_widgets import (
    CheckmarkCheckBox, NoWheelComboBox, NoWheelSlider, _SettingsDialog,
)
from wordy.ui.settings_style import build_settings_stylesheet
from wordy.ui.window import activate_window, center_window

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
    """应用设置窗口。控件变更直接写入 AppSettings 并即时持久化。"""

    def __init__(self, root, state: SettingsState, on_record_hotkey: Callable[[], None],
                 on_refresh_voices: Callable[[], None],
                 on_field_changed: Callable[[str, object], None],
                 on_close: Callable[[], None]):
        self.root = root
        self.on_record_hotkey = on_record_hotkey
        self.on_refresh_voices = on_refresh_voices
        self.on_field_changed = on_field_changed
        self.on_close = on_close
        # 直接读写 live config，不再维护 pending 状态
        self._settings = AppSettings.load()

        # 热键录制结果（仅在录制完成后赋值）
        self._recorded_hotkey: str | None = None
        self._recorded_hotkey_name: str | None = None

        self._audio_output_label_to_identity: dict[str, AudioOutputIdentity] = {}
        self._audio_output_devices_error: Exception | None = None
        # 路由锁定时仅改变下拉框显示，保留用户关闭路由后使用的本地输出。
        self._local_audio_output_label: str | None = None
        self._closed = False
        self._closing = False
        self.current_label: QLabel = QLabel()
        self.pending_label: QLabel = QLabel()
        self.record_status_label: QLabel = QLabel()
        self.record_button: QPushButton = QPushButton()
        # voice_combo / refresh_voices_button / voice_status_label 由 property 委托给 panel
        self.tts_backend_combo: QComboBox = NoWheelComboBox()
        self.tts_api_combo: QComboBox = NoWheelComboBox()
        self.audio_output_combo: QComboBox = NoWheelComboBox()
        self.audio_output_status_label: QLabel = QLabel()
        # api_key_input / clear_api_key_button / api_key_status_label 由 property 委托给 panel
        self.log_level_combo: QComboBox = NoWheelComboBox()
        self.status_label: QLabel = QLabel()
        self.volume_value_label: QLabel = QLabel()
        self.volume_slider: QSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.opacity_value_label: QLabel = QLabel()
        self.opacity_slider: QSlider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.fixed_center_check: QCheckBox = QCheckBox()
        # 音频侦听 UI
        self.audio_route_enabled_check: QCheckBox = QCheckBox()
        self.audio_route_status_label: QLabel = QLabel()
        self.vb_cable_install_button: QPushButton = QPushButton()
        # TTS 服务商专属设置容器
        self._tts_provider_container: QFrame | None = None

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
        """关闭设置窗口并 flush pending 写入。"""
        self._settings.flush()  # 确保 slider debounce 写入完成
        if self.exists() and self.window is not None:
            self.window.close()

    def set_recording_started(self) -> None:
        """更新为快捷键录制中状态。"""
        self._set_label(self.record_status_label, "请按下新的快捷键组合，Esc 取消录制", TEXT_WARNING)
        self.record_button.setText("录制中...")
        self.record_button.setEnabled(False)

    def set_record_result(self, hotkey: str | None, hotkey_name: str | None, error: Exception | None = None) -> None:
        """更新快捷键录制结果。录制成功后直接注册并保存。"""
        self.record_button.setEnabled(True)
        self.record_button.setText("重新录制")
        if error is not None:
            self._set_label(self.record_status_label, f"录制失败：{error}", TEXT_ERROR)
            return
        if hotkey is None or hotkey_name is None:
            self._set_label(self.record_status_label, "已取消录制", TEXT_MUTED)
            self.record_button.setText("录制快捷键")
            return
        # 存储录制结果，由 overlay 回调处理注册
        self._recorded_hotkey = hotkey
        self._recorded_hotkey_name = hotkey_name
        # overlay 回调负责 try_register_hotkey，成功则写入 config
        self.on_field_changed("hotkey", hotkey)
        self.pending_label.setText(f"当前：{hotkey_name}")
        self._set_label(self.record_status_label, "已录制，快捷键已更新", INPUT_TEXT_COLOR)

    def set_hotkey_warning(self, text: str) -> None:
        """提示当前快捷键不可用，并引导用户重新录制。"""
        self.record_button.setEnabled(True)
        self.record_button.setText("重新录制")
        self._set_label(self.record_status_label, text, TEXT_ERROR)

    def set_voices_loading(self) -> None:
        """更新为音色加载中状态（委托给活跃面板）。"""
        panel = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        if panel is not None:
            panel.set_voices_loading()

    def set_voices_error(self, error: Exception | str) -> None:
        """显示音色加载错误。"""
        panel = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        if panel is not None:
            panel.set_voices_error(error)

    def set_voices_loaded(self, voices: list[VoiceRecord], selected_voice_id: str | None) -> list[str]:
        """填充音色列表。"""
        panel = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        if panel is not None:
            return panel.set_voices_loaded(voices, selected_voice_id)
        return []

    def set_status(self, text: str, color: str = INPUT_TEXT_COLOR) -> None:
        """在底部状态栏显示临时消息。"""
        self._set_label(self.status_label, text, color)

    def _handle_dialog_close(self) -> None:
        if self._closing:
            return
        self._closing = True
        try:
            if not self._closed:
                try:
                    self._settings.flush()  # 确保 debounce 写入完成
                    self.on_close()
                finally:
                    self._closed = True
        finally:
            self._closing = False

    def _build(self, state: SettingsState) -> None:
        assert self.window is not None, "window must be created before _build"
        self._state = state
        s = self._settings  # 当前 live config
        outer_layout = QVBoxLayout(self.window)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)
        dialog_shell = QFrame()
        dialog_shell.setObjectName("dialogShell")
        outer_layout.addWidget(dialog_shell, 1)
        shell_layout = QVBoxLayout(dialog_shell)
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        # 标题保留拖动命中区；关闭入口独立，避免无边框窗口只能从底部退出。
        header = QWidget()
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(CONTENT_MARGIN, 14, CONTENT_MARGIN, 12)
        header_layout.setSpacing(12)
        title_label = QLabel("设置")
        title_label.setObjectName("dialogTitle")
        title_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        title_label.setCursor(Qt.CursorShape.OpenHandCursor)
        title_label.installEventFilter(self.window)
        header_layout.addWidget(title_label, 1)
        close_button = QPushButton("×")
        close_button.setObjectName("dialogCloseButton")
        # 关闭不是表单提交，不能让凭据输入框的 Enter 误触此按钮。
        close_button.setAutoDefault(False)
        close_button.setFixedSize(34, 34)
        close_button.setToolTip("关闭设置")
        close_button.setAccessibleName("关闭设置")
        close_button.clicked.connect(lambda _checked=False: self.close())
        header_layout.addWidget(close_button)
        shell_layout.addWidget(header)
        tab_widget = QTabWidget()
        tab_widget.setObjectName("settingsTabs")
        tab_widget.setDocumentMode(True)
        # 隐藏原生 tab 基线，避免系统亮色边线穿过深色导航区域。
        tab_widget.tabBar().setDrawBase(False)
        tab_widget.setUsesScrollButtons(False)
        tab_widget.setElideMode(Qt.TextElideMode.ElideNone)
        shell_layout.addWidget(tab_widget, 1)

        # 1. 本地设置
        local_layout = self._create_tab_page(tab_widget, "本地设置")
        self._build_hotkey_section(local_layout, s)
        self._add_inner_gap(local_layout)
        self._build_volume_section(local_layout, s)
        self._add_inner_gap(local_layout)
        self._build_opacity_section(local_layout, s)
        self._add_inner_gap(local_layout)
        self._build_position_section(local_layout, s)
        local_layout.addStretch(1)

        # 2. 音频侦听
        route_layout = self._create_tab_page(tab_widget, "音频侦听")
        self._build_audio_output_section(route_layout, state, s)
        self._add_inner_gap(route_layout)
        self._build_audio_route_section(route_layout, state, s)
        self._add_inner_gap(route_layout)
        self._build_sidetone_section(route_layout, s)
        route_layout.addStretch(1)

        # 3. TTS 设置
        tts_layout = self._create_tab_page(tab_widget, "TTS 设置")
        self._build_tts_api_section(tts_layout, s)
        self._add_inner_gap(tts_layout)
        self._tts_provider_container = QFrame()
        self._tts_provider_container.setObjectName("ttsProviderContainer")
        container_layout = QVBoxLayout(self._tts_provider_container)
        container_layout.setContentsMargins(16, 12, 16, 4)
        container_layout.setSpacing(0)

        # 生成模式
        self._build_backend_section(container_layout, s)

        # 各 provider 面板
        self._provider_panel_widgets: dict[str, QFrame] = {}
        for name, panel in PROVIDER_PANELS.items():
            wrapper = QFrame()
            layout = QVBoxLayout(wrapper)
            layout.setContentsMargins(0, 12, 0, 0)
            layout.setSpacing(0)
            panel.build(layout, state, self.on_refresh_voices, self.on_field_changed)  # type: ignore[arg-type]
            wrapper.setVisible(name == s.active_tts_provider)
            container_layout.addWidget(wrapper)
            self._provider_panel_widgets[name] = wrapper

        container_layout.addStretch(1)
        tts_layout.addWidget(self._tts_provider_container)
        tts_layout.addStretch(1)

        # 4. 日志
        log_layout = self._create_tab_page(tab_widget, "日志")
        self._build_log_level_section(log_layout, s)
        log_layout.addStretch(1)

        footer = QWidget()
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(CONTENT_MARGIN, 0, CONTENT_MARGIN, CONTENT_MARGIN)
        footer_layout.setSpacing(0)
        self._build_buttons(footer_layout)
        shell_layout.addWidget(footer, 0)

    def _build_hotkey_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("全局快捷键"))
        self.current_label = self._body_label(f"当前快捷键：{s.name}", TEXT_MUTED)
        section.addWidget(self.current_label)
        self.pending_label = self._body_label(f"当前：{s.name}", INPUT_TEXT_COLOR, True)
        section.addWidget(self.pending_label)
        self.record_status_label = self._hint_label("点击录制后按下新的快捷键组合", TEXT_MUTED)
        section.addWidget(self.record_status_label)
        self.record_button = QPushButton("录制快捷键")
        self.record_button.clicked.connect(lambda _checked=False: self.on_record_hotkey())
        section.addWidget(self.record_button, 0, Qt.AlignmentFlag.AlignLeft)

    def _build_backend_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("生成模式"))
        self.tts_backend_combo = NoWheelComboBox()
        self.tts_backend_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        self.tts_backend_combo.setMinimumContentsLength(24)
        self.tts_backend_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self._populate_backend_combo_for_provider(s.active_tts_provider, s.tts_backend)
        self.tts_backend_combo.currentTextChanged.connect(self._on_tts_backend_selected)
        section.addWidget(self.tts_backend_combo)
        section.addWidget(self._hint_label("Bytes：完整生成后播放。Streaming：边生成边播放，延迟更低。", TEXT_MUTED))

    def _populate_backend_combo_for_provider(self, provider: str, current_backend: str) -> None:
        """按 provider 填充生成模式下拉框。"""
        backends = TTS_BACKENDS_BY_PROVIDER.get(provider, ())
        self.tts_backend_combo.blockSignals(True)
        self.tts_backend_combo.clear()
        self.tts_backend_combo.addItems(list(backends))
        if current_backend in backends:
            self.tts_backend_combo.setCurrentText(current_backend)
        elif backends:
            self.tts_backend_combo.setCurrentText(backends[0])
        self.tts_backend_combo.blockSignals(False)

    def _build_tts_api_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("TTS 服务商"))
        self.tts_api_combo = NoWheelComboBox()
        self.tts_api_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        self.tts_api_combo.setMinimumContentsLength(24)
        self.tts_api_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.tts_api_combo.addItems(list(TTS_API_PROVIDERS))
        if s.active_tts_provider in TTS_API_PROVIDERS:
            self.tts_api_combo.setCurrentText(s.active_tts_provider)
        self.tts_api_combo.currentTextChanged.connect(self._on_tts_api_selected)
        section.addWidget(self.tts_api_combo)
        section.addWidget(self._hint_label("选择 TTS 服务商，下方设置区同步切换。", TEXT_MUTED))

    def _build_audio_output_section(self, parent_layout: QVBoxLayout, state: SettingsState, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("音频输出"))
        self.audio_output_combo = NoWheelComboBox()
        self.audio_output_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        self.audio_output_combo.setMinimumContentsLength(24)
        self.audio_output_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.audio_output_combo.currentTextChanged.connect(self._on_audio_output_selected)
        section.addWidget(self.audio_output_combo)
        self.audio_output_status_label.setObjectName("hintLabel")
        self._apply_audio_output_devices(state.audio_output_devices, s.audio_output_device,  # type: ignore[arg-type]
                                         s.audio_output_device_name, state.audio_output_devices_error)
        self._local_audio_output_label = self.audio_output_combo.currentText()
        self._sync_audio_output_control(s)
        section.addWidget(self.audio_output_status_label)

    def _build_volume_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._section_title("音量设置"))
        self.volume_value_label = self._body_label(f"{s.volume:.2f}x", INPUT_TEXT_COLOR)
        header.addWidget(self.volume_value_label, 0, Qt.AlignmentFlag.AlignRight)
        section.addLayout(header)
        self.volume_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, self._volume_to_slider(MAX_VOLUME))
        self.volume_slider.setSingleStep(1)
        self.volume_slider.setPageStep(max(1, int(0.25 / VOLUME_STEP)))
        self.volume_slider.setValue(self._volume_to_slider(s.volume))
        self.volume_slider.valueChanged.connect(self._on_volume_changed)
        section.addWidget(self.volume_slider)
        section.addWidget(self._hint_label("范围 0.50x - 2.00x，默认 1.00x", TEXT_MUTED))

    def _build_opacity_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._section_title("输入框透明度"))
        self.opacity_value_label = self._body_label(f"{round(s.overlay_opacity * 100)}%", INPUT_TEXT_COLOR)
        header.addWidget(self.opacity_value_label, 0, Qt.AlignmentFlag.AlignRight)
        section.addLayout(header)
        self.opacity_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, self._opacity_to_slider(MAX_OVERLAY_OPACITY))
        self.opacity_slider.setSingleStep(1)
        self.opacity_slider.setPageStep(max(1, int(0.10 / OVERLAY_OPACITY_STEP)))
        self.opacity_slider.setValue(self._opacity_to_slider(s.overlay_opacity))
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        section.addWidget(self.opacity_slider)
        section.addWidget(self._hint_label("范围 30% - 100%，默认 100%", TEXT_MUTED))

    def _build_position_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("显示位置"))
        self.fixed_center_check = CheckmarkCheckBox("固定出现在屏幕中心")
        self.fixed_center_check.setChecked(s.fixed_center)
        self.fixed_center_check.stateChanged.connect(self._on_fixed_center_changed)
        section.addWidget(self.fixed_center_check)
        section.addWidget(self._hint_label("取消勾选后，可拖动输入窗口；松开鼠标后自动记住位置。", TEXT_MUTED))

    def _build_audio_route_section(self, parent_layout: QVBoxLayout, state: SettingsState, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("音频侦听"))

        # VB-CABLE 状态
        if state.vb_cable_installed:
            self.audio_route_status_label = self._hint_label(
                "VB-CABLE 已安装。TTS + 麦克风 → CABLE Input，其他应用的输入设备选 CABLE Output 即可。",
                INPUT_TEXT_COLOR,
            )
        else:
            self.audio_route_status_label = self._hint_label(
                "需安装 VB-CABLE 驱动才能让其他应用听到 TTS 和麦克风。",
                TEXT_WARNING,
            )
        section.addWidget(self.audio_route_status_label)

        if not state.vb_cable_installed:
            self.vb_cable_install_button = QPushButton("下载并安装 VB-CABLE")
            self.vb_cable_install_button.clicked.connect(self._on_open_vb_cable_download)
            section.addWidget(self.vb_cable_install_button)

        # 启用开关
        self.audio_route_enabled_check = CheckmarkCheckBox("启用音频侦听")
        self.audio_route_enabled_check.setChecked(s.audio_routing_enabled)
        self.audio_route_enabled_check.stateChanged.connect(self._on_audio_route_enabled_changed)
        section.addWidget(self.audio_route_enabled_check)

        # 麦克风选择（音频侦听子项）
        section.addWidget(self._section_title("麦克风被侦听"))
        self.mic_input_combo = NoWheelComboBox()
        self.mic_input_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        self.mic_input_combo.setMinimumContentsLength(24)
        self._populate_input_devices(state.input_devices, s.mic_input_device)
        self.mic_input_combo.currentTextChanged.connect(self._on_mic_input_selected)
        section.addWidget(self.mic_input_combo)
        self.mic_listen_status_label = QLabel()
        self.mic_listen_status_label.setObjectName("hintLabel")
        self._update_mic_listen_status(s, state)
        section.addWidget(self.mic_listen_status_label)

    def _build_sidetone_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("返听"))
        self.sidetone_enabled_check = CheckmarkCheckBox("启用返听（TTS 同步输出到默认设备）")
        self.sidetone_enabled_check.setChecked(s.sidetone_enabled)
        self.sidetone_enabled_check.stateChanged.connect(self._on_sidetone_enabled_changed)
        section.addWidget(self.sidetone_enabled_check)
        section.addWidget(self._hint_label(
            "TTS 播放时同步在扬声器/耳机中播放，方便音频侦听启用后自己能听到声音。",
            TEXT_MUTED,
        ))

    def _on_open_vb_cable_download(self, _checked: bool = False) -> None:
        from wordy.audio.driver import VBCableDriverManager
        VBCableDriverManager.open_download_page()

    def _on_audio_route_enabled_changed(self, state: int) -> None:
        enabled = state == Qt.CheckState.Checked.value
        s = self._settings
        if enabled and not s.audio_routing_enabled:
            self._local_audio_output_label = self.audio_output_combo.currentText()
        s.audio_routing_enabled = enabled  # auto-save
        self._sync_audio_output_control(s)
        self._update_mic_listen_status(s, self._state)
        self.on_field_changed("audio_routing_enabled", enabled)

    def _on_sidetone_enabled_changed(self, state: int) -> None:
        enabled = state == Qt.CheckState.Checked.value
        self._settings.sidetone_enabled = enabled  # auto-save
        self.on_field_changed("sidetone_enabled", enabled)

    def _find_routing_output_label(self) -> str | None:
        """查找路由固定使用的 Windows WASAPI CABLE Input 标签。"""
        for label, identity in self._audio_output_label_to_identity.items():
            name = identity.get("name")
            host_api = identity.get("host_api_name")
            if isinstance(name, str) and "CABLE Input" in name and host_api == "Windows WASAPI":
                return label
        return None

    def _sync_audio_output_control(self, s: AppSettings) -> None:
        """路由启用时固定显示 CABLE Input，关闭后恢复本地输出选择。"""
        combo = self.audio_output_combo
        combo.blockSignals(True)
        if s.audio_routing_enabled:
            routing_label = self._find_routing_output_label()
            if routing_label is None:
                routing_label = "CABLE Input [Windows WASAPI]（未检测到）"
                if combo.findText(routing_label) < 0:
                    combo.addItem(routing_label)
            combo.setCurrentText(routing_label)
            combo.setEnabled(False)
            self._set_label(
                self.audio_output_status_label,
                "音频侦听已启用，输出固定为 VB-CABLE Input，不可更改。",
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
        if not s.audio_routing_enabled:
            self._on_audio_output_selected(combo.currentText())

    MIC_NONE_LABEL = "无（不侦听麦克风）"

    def _on_mic_input_selected(self, text: str) -> None:
        device = None if text == self.MIC_NONE_LABEL else (text if text else None)
        self._settings.mic_input_device = device  # auto-save
        self.on_field_changed("mic_input_device", device)
        self._update_mic_listen_status(self._settings, self._state)

    def _update_mic_listen_status(self, s: AppSettings, state: SettingsState | None) -> None:
        """更新麦克风侦听状态提示。VB-CABLE 安装检查在 section 顶部，此处只关注侦听结果。"""
        listen_ok = state.mic_listen_configured if state is not None else False

        if not s.audio_routing_enabled:
            self._set_label(
                self.mic_listen_status_label,
                "启用音频侦听后自动配置",
                TEXT_MUTED,
            )
        elif not s.mic_input_device:
            self._set_label(
                self.mic_listen_status_label,
                "选择麦克风后自动配置",
                TEXT_MUTED,
            )
        elif listen_ok:
            self._set_label(
                self.mic_listen_status_label,
                "麦克风侦听成功",
                INPUT_TEXT_COLOR,
            )
        else:
            self._set_label(
                self.mic_listen_status_label,
                "麦克风侦听失败——请检查设备是否被占用",
                TEXT_ERROR,
            )

    def _populate_input_devices(self, devices: list[dict[str, object]], selected: str | None) -> None:
        """填充麦克风被侦听设备下拉列表，首项为"无"。"""
        combo = self.mic_input_combo
        combo.blockSignals(True)
        combo.clear()
        combo.addItem(self.MIC_NONE_LABEL)
        names: list[str] = [self.MIC_NONE_LABEL]
        for dev in devices:
            name = dev.get("name") if isinstance(dev, dict) else str(dev)
            if isinstance(name, str) and name:
                names.append(name)
                combo.addItem(name)
        if selected is None:
            combo.setCurrentIndex(0)
        elif selected in names:
            combo.setCurrentText(selected)
        combo.blockSignals(False)

    def _build_log_level_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("显示等级"))
        self.log_level_combo = NoWheelComboBox()
        self.log_level_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContentsOnFirstShow)
        self.log_level_combo.setMinimumContentsLength(24)
        self.log_level_combo.view().setTextElideMode(Qt.TextElideMode.ElideRight)
        self.log_level_combo.addItems(list(LOG_LEVELS))
        current = s.log_level if s.log_level in LOG_LEVELS else "INFO"
        self.log_level_combo.setCurrentText(current)
        self.log_level_combo.currentTextChanged.connect(self._on_log_level_selected)
        section.addWidget(self.log_level_combo)
        section.addWidget(self._hint_label("选择日志窗口显示和收集的最低等级，修改后立即生效。", TEXT_MUTED))

    def _build_buttons(self, parent_layout: QVBoxLayout) -> None:
        self.status_label = self._hint_label("", INPUT_TEXT_COLOR)
        self.status_label.setObjectName("applyStatusLabel")
        parent_layout.addWidget(self.status_label)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 14, 0, 0)
        button_row.setSpacing(BUTTON_GAP)
        button_row.addStretch(1)
        exit_button = QPushButton("退出")
        exit_button.setObjectName("cancelButton")
        exit_button.setMinimumWidth(BUTTON_MIN_WIDTH)
        exit_button.clicked.connect(lambda _checked=False: self.close())
        button_row.addWidget(exit_button)
        parent_layout.addLayout(button_row)

    def _add_inner_gap(self, parent_layout: QVBoxLayout) -> None:
        parent_layout.addSpacing(SECTION_GAP)

    def _on_tts_backend_selected(self, backend: str) -> None:
        self._settings.tts_backend = backend  # auto-save via property setter
        # 用 provider 前缀传递，匹配 overlay 的 cartesia_tts_backend / volcengine_tts_backend 处理
        provider = self._settings.active_tts_provider.lower()
        self.on_field_changed(f"{provider}_tts_backend", backend)

    # 控件由活跃面板持有；只读委托避免误赋值被空 setter 静默丢弃。
    @property
    def api_key_input(self):
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "api_key_input", None) if p is not None else None

    @property
    def voice_combo(self):
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "voice_combo", None) if p is not None else None

    @property
    def refresh_voices_button(self):
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "refresh_btn", None) if p is not None else None

    @property
    def voice_status_label(self):
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "voice_status", None) if p is not None else None

    @property
    def voice_label_to_id(self) -> dict[str, str]:
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "voice_label_to_id", {}) if p is not None else {}

    @property
    def voice_label_to_name(self) -> dict[str, str]:
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "voice_label_to_name", {}) if p is not None else {}

    @property
    def clear_api_key_button(self):
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "clear_key_btn", None) if p is not None else None

    @property
    def api_key_status_label(self):
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "api_status", None) if p is not None else None

    def _on_tts_api_selected(self, provider: str) -> None:
        """TTS 服务商切换：切换面板可见性 + 刷新生成模式下拉。"""
        s = self._settings
        if provider == s.active_tts_provider:
            return
        previous = s.active_tts_provider
        s.active_tts_provider = provider  # 回调需要读取目标服务商配置。
        try:
            self.on_field_changed("active_tts_provider", provider)
        except Exception:
            # 引擎切换失败时恢复持久配置与下拉框；面板尚未变更，无需重建。
            s.active_tts_provider = previous
            self.tts_api_combo.blockSignals(True)
            self.tts_api_combo.setCurrentText(previous)
            self.tts_api_combo.blockSignals(False)
            self.set_status("服务商切换失败，请重试", TEXT_ERROR)
            return
        self._populate_backend_combo_for_provider(provider, s.tts_backend)
        for name, widget in self._provider_panel_widgets.items():
            widget.setVisible(name == provider)

    def _on_fixed_center_changed(self, state: int) -> None:
        checked = state == Qt.CheckState.Checked.value
        self._settings.fixed_center = checked  # auto-save
        self.on_field_changed("fixed_center", checked)

    def _on_log_level_selected(self, log_level: str) -> None:
        if log_level in LOG_LEVELS:
            self._settings.log_level = log_level  # auto-save
            self.on_field_changed("log_level", log_level)

    def _apply_audio_output_devices(self, devices: list[AudioOutputDevice] | list[str],
                                     selected_identity: AudioOutputIdentity | None,
                                     selected_device_name: str | None,
                                     error: Exception | None) -> None:
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
        else:
            self.audio_output_combo.setCurrentText(SYSTEM_DEFAULT_AUDIO_OUTPUT_LABEL)
        self.audio_output_combo.blockSignals(False)

        if error is not None:
            self._set_label(self.audio_output_status_label, f"输出设备枚举失败：{error}", TEXT_WARNING)
        elif labels:
            self._set_label(self.audio_output_status_label, f"已发现 {len(labels)} 个输出设备", TEXT_MUTED)
        else:
            self._set_label(self.audio_output_status_label, "未发现输出设备，将使用系统默认", TEXT_MUTED)

    def _on_audio_output_selected(self, device_label: str) -> None:
        s = self._settings
        if s.audio_routing_enabled:
            return
        self._local_audio_output_label = device_label
        if device_label == SYSTEM_DEFAULT_AUDIO_OUTPUT_LABEL:
            s.audio_output_device = None
            s.audio_output_device_name = None
            self.on_field_changed("audio_output_device", None)
            return
        identity = self._audio_output_label_to_identity.get(device_label)
        if identity is not None:
            s.audio_output_device = dict(identity)  # type: ignore[assignment]
            chosen_name = identity.get("name")
            s.audio_output_device_name = chosen_name if isinstance(chosen_name, str) else None
            self.on_field_changed("audio_output_device", dict(identity))
            return
        s.audio_output_device = dict(normalize_identity(device_label))  # type: ignore[assignment]
        s.audio_output_device_name = device_label
        self.on_field_changed("audio_output_device", dict(normalize_identity(device_label)))

    def _on_volume_changed(self, value: int) -> None:
        vol = round(MIN_VOLUME + value * VOLUME_STEP, 2)
        self.volume_value_label.setText(f"{vol:.2f}x")
        self._settings.volume = vol  # auto-save (debounced)
        self.on_field_changed("volume", vol)

    def _on_opacity_changed(self, value: int) -> None:
        op = round(MIN_OVERLAY_OPACITY + value * OVERLAY_OPACITY_STEP, 2)
        self.opacity_value_label.setText(f"{round(op * 100)}%")
        self._settings.overlay_opacity = op  # auto-save (debounced)
        self.on_field_changed("overlay_opacity", op)

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
