#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口 UI。直接读写 AppSettings，每次控件变更即时持久化。"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QFont, QFontDatabase, QIcon
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFontComboBox, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QScrollArea, QSlider, QStyle, QTabWidget, QVBoxLayout,
    QWidget,
)

from wordy.config import AppSettings
from wordy.constants import (
    LOG_LEVELS, MAX_OVERLAY_OPACITY, MAX_VOLUME,
    MIN_OVERLAY_OPACITY, MIN_VOLUME, OVERLAY_OPACITY_STEP, VOLUME_STEP,
)
from wordy.tts.constants import (
    TTS_API_PROVIDER_CARTESIA,
    TTS_API_PROVIDERS,
    TTS_BACKENDS_BY_PROVIDER,
)
from wordy.identity import normalize_identity
from wordy.ui.theme import (
    GREEN_ACCENT, TEXT_ERROR, TEXT_MUTED, TEXT_WARNING, UI_FONT_FAMILIES, ui_font_stack,
)
from wordy.ui.tts_panels import PROVIDER_PANELS
from wordy.ui.settings_state import (
    AudioOutputDevice, AudioOutputIdentity, SettingsState, VoiceRecord,
)
from wordy.ui.settings_widgets import (
    CheckmarkCheckBox, NoWheelComboBox, NoWheelFontComboBox, NoWheelSlider,
    WINDOW_EDGE_PAD, _SettingsDialog, _TitleBar,
)
from wordy.ui.settings_style import BASE_FONT_PX, build_settings_stylesheet
from wordy.ui.status_toast import StatusToastQueue
from wordy.ui.window import (
    center_window, disable_window_transitions, enable_system_resize, set_window_cloaked,
)
from wordy.ui.chat_wheel_settings import ChatWheelSettings

INPUT_TEXT_COLOR = GREEN_ACCENT
SYSTEM_DEFAULT_AUDIO_OUTPUT_LABEL = "系统默认"
# 双栏表单与轮盘编辑并列呈现；默认宽度为原560px的两倍。
# 尺寸包含透明边缘与标题栏；静态尺寸契约要求顶层整数字面量。
DIALOG_WIDTH = 1120
DIALOG_HEIGHT = 760
DIALOG_MIN_WIDTH = 960
DIALOG_MIN_HEIGHT = 636
CONTENT_MARGIN = 36
SECTION_GAP = 28
INLINE_GAP = 10
# 卡片内边距：带描边的容器里，内容要留出呼吸位，否则贴到框线上。
# 不能再大——窄窗口（DIALOG_MIN_WIDTH）下 TTS 页的最小宽度余量只剩 26px，
# 每加 2px 内边距就吃掉 4px，14px 就会溢出并触发横向裁切。数值同 INLINE_GAP。
CARD_PADDING = 10
BUTTON_GAP = 16
BUTTON_MIN_WIDTH = 88
# 录制结果改由浮动弹窗呈现，原地提示回到中性指引。
RECORD_HINT = "点击录制后按下新的快捷键组合"


def _font_is_usable(family: str) -> bool:
    """该族能不能真的拿来当界面字体。

    用 isSmoothlyScalable 而不是"在不在 QFontDatabase.families() 里"：
    点阵遗留字体（Small Fonts / MS Sans Serif / Terminal…）确实列在 families()
    中，但 DirectWrite 建不出字面，Qt 会一路失败并刷日志——选它等于没选。
    """
    return bool(family) and QFontDatabase.isSmoothlyScalable(family)


class SettingsWindow:
    """应用设置窗口。控件变更直接写入 AppSettings 并即时持久化。"""

    # 设备枚举会阻塞 GUI，延后到新页首帧之后再跑。
    AUDIO_SCAN_DELAY_MS = 150

    def __init__(self, root, state: SettingsState, on_record_hotkey: Callable[[], None],
                 on_refresh_voices: Callable[[], None],
                 on_field_changed: Callable[[str, object], bool | None],
                 on_close: Callable[[], None],
                 on_audio_outputs_needed: Callable[[], None] | None = None,
                 prewarm: bool = False):
        self.root = root
        self._on_audio_outputs_needed = on_audio_outputs_needed
        self._audio_outputs_requested = False
        # 预热实例不显示也不改配置，丢弃时不必落盘。
        self._prewarmed = prewarm
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
        self.pending_label: QLabel = QLabel()
        self.record_status_label: QLabel = QLabel()
        self.record_button: QPushButton = QPushButton()
        # 音色控件由活跃 provider panel 持有。
        self.tts_backend_combo: QComboBox = NoWheelComboBox()
        self.tts_api_combo: QComboBox = NoWheelComboBox()
        self.audio_output_combo: QComboBox = NoWheelComboBox()
        self.audio_output_status_label: QLabel = QLabel()
        self.log_level_combo: QComboBox = NoWheelComboBox()
        # 临时反馈走浮动弹窗队列，不再占用页脚布局位置。
        self.status_toasts: StatusToastQueue | None = None
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
        # 无边框玻璃窗口：标题栏、拖动、缩放和三个窗口按钮全部由内容自绘，
        # 窗口本体只负责透明合成与手绘玻璃底。
        self.window.setWindowFlags(
            Qt.WindowType.Window | Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
        )
        self.window.setModal(False)
        self.window.setMinimumSize(DIALOG_MIN_WIDTH, DIALOG_MIN_HEIGHT)
        self.window.resize(DIALOG_WIDTH, DIALOG_HEIGHT)
        self.window.setSizeGripEnabled(False)  # 缩放改由 startSystemResize 承担，不叠加尺寸手柄。
        # 玻璃底由 paintEvent 自绘，窗口本身必须透明才透得出背后。
        self.window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.window.setStyleSheet(build_settings_stylesheet(ui_font_stack(self._settings.ui_font)))
        window = self.window
        self._build(state)
        # 深色样式和布局在隐藏时完成，按最终尺寸居中，避免首帧白底或显示后跳位。
        window.ensurePolished()
        layout = window.layout()
        if layout is not None:
            layout.activate()
        center_window(window, window.width(), window.height(), parent)
        # Qt 尺寸已稳定，但 Windows 打开动画仍会视觉缩放；必须在 show 前禁用。
        disable_window_transitions(window)
        # 无边框窗口是 WS_POPUP，系统本来不给缩放；补上可缩放边框样式，
        # startSystemResize 才会真的改尺寸，否则只有光标变、窗口不动。
        enable_system_resize(window)
        # 遮蔽要在 show 前、且在所有窗口标志/属性改完之后：
        # show 期间若因改动标志重建 HWND，遮蔽会连窗口一起丢掉。
        set_window_cloaked(window, True)
        if prewarm:
            # 预热只建控件树：上面的 DWM/缩放调用已经通过 winId 建出原生窗口，
            # 那一步和 QTabWidget/QScrollArea 的首次解析才是首次点击卡顿的来源。
            return
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
        # 点击设置图标时恢复已最小化的窗口，同时保留原来的最大化状态。
        if window.isMinimized():
            window.setWindowState(window.windowState() & ~Qt.WindowState.WindowMinimized)
        window.raise_()
        window.activateWindow()
        window.setFocus(Qt.FocusReason.ActiveWindowFocusReason)
        # 设置由前台应用内打开，Qt 已完成激活；不再重复 ShowWindow/附加前台线程抢焦点。

    def close(self) -> None:
        """关闭设置窗口并 flush pending 写入。"""
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
        # 结果只在浮动弹窗中出现，原地提示回到中性指引。
        self._reset_record_hint()
        if error is not None:
            self.set_status(f"录制失败：{error}", TEXT_ERROR)
            return
        if hotkey is None or hotkey_name is None:
            self.set_status("已取消录制", TEXT_MUTED)
            self.record_button.setText("录制快捷键")
            return
        # 存储录制结果，由 overlay 回调处理注册
        self._recorded_hotkey = hotkey
        self._recorded_hotkey_name = hotkey_name
        # overlay 回调负责 try_register_hotkey，成功则写入 config
        if self.on_field_changed("hotkey", hotkey) is False:
            # 注册失败时 overlay 已回滚配置并显示原因，不能再覆盖成成功提示。
            return
        self.pending_label.setText(f"当前：{hotkey_name}")
        self.set_status("已录制，快捷键已更新", INPUT_TEXT_COLOR)

    def _reset_record_hint(self) -> None:
        """把录制提示还原成中性指引，避免上一次结果长期留在固定位置。"""
        self._set_label(self.record_status_label, RECORD_HINT, TEXT_MUTED)

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
        """以浮动弹窗显示临时反馈；空文本表示立刻收起全部反馈。"""
        queue = self.status_toasts
        if queue is None:  # 窗口未构建完成时无处安放反馈
            return
        if text:
            queue.push(text, color)
        else:
            queue.clear()

    def _handle_dialog_close(self) -> bool:
        if self._closing:
            return False
        self._closing = True
        try:
            if not self._closed:
                # 预热实例从头到尾没改过配置，丢弃它不值得再写一次盘。
                if not self._prewarmed:
                    if self.window is not None:
                        self.window.hide()
                    try:
                        self._settings.flush()  # 确保 debounce 写入完成
                    except Exception as error:
                        if self.window is not None:
                            self.window.show()
                        self.set_status(f"设置保存失败：{error}", TEXT_ERROR)
                        return False
                try:
                    if self.window is not None and self._prewarmed:
                        self.window.hide()
                    panel = getattr(self, "chat_wheel_panel", None)
                    if panel is not None:
                        # 关闭已生效：轮盘面板此刻才放弃未完成的录音与导入。
                        panel.mark_closed()
                    self.on_close()
                finally:
                    self._closed = True
            return True
        finally:
            self._closing = False

    def _build(self, state: SettingsState) -> None:
        assert self.window is not None, "window must be created before _build"
        self._state = state
        s = self._settings  # 当前 live config
        outer_layout = QVBoxLayout(self.window)
        # 四周留出透明余量：容投影、当缩放热区，并让子控件不贴边——
        # 热区必须落在没有子控件的区域，鼠标按下才会直达窗口自己。
        outer_layout.setContentsMargins(WINDOW_EDGE_PAD, WINDOW_EDGE_PAD, WINDOW_EDGE_PAD, WINDOW_EDGE_PAD)
        outer_layout.setSpacing(0)
        dialog_shell = QFrame()
        dialog_shell.setObjectName("dialogShell")
        # 内容区不继承窗口在边缘设的缩放光标。
        dialog_shell.setCursor(Qt.CursorShape.ArrowCursor)
        outer_layout.addWidget(dialog_shell, 1)
        shell_layout = QVBoxLayout(dialog_shell)
        # 顶部留白由自绘标题栏取代；标题栏高度已计入 DIALOG_HEIGHT。
        shell_layout.setContentsMargins(0, 0, 0, 0)
        shell_layout.setSpacing(0)
        title_bar = _TitleBar(self.window)
        self.title_bar = title_bar
        self.window.title_bar = title_bar
        shell_layout.addWidget(title_bar, 0)
        tab_widget = QTabWidget()
        tab_widget.setObjectName("settingsTabs")
        tab_widget.setDocumentMode(True)
        # 隐藏原生 tab 基线，避免系统亮色边线穿过深色导航区域。
        tab_widget.tabBar().setDrawBase(False)
        # 标签按文字与内边距取宽，不均分或拉伸占满导航栏。
        tab_widget.tabBar().setExpanding(False)
        tab_widget.setUsesScrollButtons(False)
        tab_widget.setElideMode(Qt.TextElideMode.ElideNone)
        shell_layout.addWidget(tab_widget, 1)

        # 宽屏按任务分栏，避免输入框和滑块被拉成长条；两栏共用对齐与留白。
        local_layout = self._create_tab_page(tab_widget, "本地设置")
        input_column, display_column = self._create_columns(local_layout)
        self._build_hotkey_section(input_column, s)
        self._add_inner_gap(input_column)
        self._build_placeholder_section(input_column, s)
        self._add_inner_gap(input_column)
        self._build_volume_section(input_column, s)
        self._build_ui_font_section(display_column, s)
        self._add_inner_gap(display_column)
        self._build_opacity_section(display_column, s)
        self._add_inner_gap(display_column)
        self._build_position_section(display_column, s)
        input_column.addStretch(1)
        display_column.addStretch(1)
        local_layout.addStretch(1)

        route_layout = self._create_tab_page(tab_widget, "音频侦听")
        output_column, route_column = self._create_columns(route_layout)
        self._build_audio_output_section(output_column, state, s)
        self._add_inner_gap(output_column)
        self._build_sidetone_section(output_column, s)
        self._build_audio_route_section(route_column, state, s)
        output_column.addStretch(1)
        route_column.addStretch(1)
        route_layout.addStretch(1)

        tts_layout = self._create_tab_page(tab_widget, "TTS 设置")
        provider_column, voice_column = self._create_columns(tts_layout)
        self._build_tts_api_section(provider_column, s)
        self._add_inner_gap(provider_column)
        self._build_backend_section(provider_column, s)
        provider_column.addStretch(1)
        self._tts_provider_container = QFrame()
        self._tts_provider_container.setObjectName("ttsProviderContainer")
        container_layout = QVBoxLayout(self._tts_provider_container)
        # 服务商配置保留内边距，避免输入控件压住原有玻璃边框。
        container_layout.setContentsMargins(CARD_PADDING, 16, CARD_PADDING, 16)
        container_layout.setSpacing(0)

        # 各 provider 面板
        self._provider_panel_widgets: dict[str, QFrame] = {}
        for name, panel in PROVIDER_PANELS.items():
            # 必须先归属设置页再切换可见性；无父级时 setVisible(True) 会短暂弹出独立窗口。
            wrapper = QFrame(self._tts_provider_container)
            layout = QVBoxLayout(wrapper)
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(0)
            panel.build(layout, state, self.on_refresh_voices, self.on_field_changed)  # type: ignore[arg-type]
            if (name == TTS_API_PROVIDER_CARTESIA and state.cartesia_voices_cache
                    and hasattr(panel, "set_voices_loaded")):
                # 非激活服务商的面板不会拿到 voices_cache，这里用独立缓存补种。
                panel.set_voices_loaded(  # type: ignore[attr-defined]
                    state.cartesia_voices_cache, s.cartesia_voice_id)
            wrapper.setVisible(name == s.active_tts_provider)
            container_layout.addWidget(wrapper)
            self._provider_panel_widgets[name] = wrapper

        container_layout.addStretch(1)
        voice_column.addWidget(self._tts_provider_container)
        tts_layout.addStretch(1)

        # 4. 日志
        # 独立页保留八槽完整编辑空间，不挤占原有音频路由设置。
        wheel_layout = self._create_tab_page(tab_widget, "聊天轮盘")
        self.chat_wheel_panel = ChatWheelSettings(s, self.on_field_changed)
        wheel_layout.addWidget(self.chat_wheel_panel)
        wheel_layout.addStretch(1)

        log_layout = self._create_tab_page(tab_widget, "日志")
        # 日志只有一个选项，限制阅读宽度，不为填满屏幕增加无用内容。
        log_form = QWidget()
        log_form.setMaximumWidth(520)
        log_form_layout = QVBoxLayout(log_form)
        log_form_layout.setContentsMargins(0, 0, 0, 0)
        self._build_log_level_section(log_form_layout, s)
        log_layout.addWidget(log_form)
        log_layout.addStretch(1)

        footer = QWidget()
        footer.setObjectName("settingsFooter")
        footer_layout = QVBoxLayout(footer)
        footer_layout.setContentsMargins(CONTENT_MARGIN, 10, CONTENT_MARGIN, 12)
        footer_layout.setSpacing(0)
        self._build_buttons(footer_layout)
        shell_layout.addWidget(footer, 0)
        # 反馈弹窗挂在窗口上而非页签页，避免被页签透明度动画一起淡化。
        self.status_toasts = StatusToastQueue(self.window)
        # 初始本地页不扫描硬件；只在首次查看音频页时读取最新设备。
        tab_widget.currentChanged.connect(self._on_settings_tab_changed)

    def _on_settings_tab_changed(self, index: int) -> None:
        if index == 1 and not self._audio_outputs_requested and self._on_audio_outputs_needed is not None:
            self._audio_outputs_requested = True
            # 先让新页面完成首帧，再执行可能阻塞 GUI 的 PortAudio 枚举。
            QTimer.singleShot(self.AUDIO_SCAN_DELAY_MS, self._load_audio_outputs)

    def _load_audio_outputs(self) -> None:
        if self.exists() and self._on_audio_outputs_needed is not None:
            self._on_audio_outputs_needed()

    def _build_ui_font_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("界面字体"))
        self.ui_font_combo = NoWheelFontComboBox()
        self.ui_font_combo.setAccessibleName("界面字体")
        # 只列可平滑缩放的族。点阵遗留字体（Small Fonts / MS Sans Serif / Terminal…）
        # 既不能当界面字体，又会因为 DirectWrite 建不出字面而在滚动列表时刷日志——
        # QFontComboBox 默认用每个族自己的字面渲染列表项，滚到哪就试建哪个。
        self.ui_font_combo.setFontFilters(QFontComboBox.FontFilter.ScalableFonts)
        # 这一条专门修 Qt 的告警：QFontComboBox 弹窗内部会拿当前字体的 pointSize
        # 重设字号，而全窗 QSS 用的是 px，pointSize() 是 -1，Qt 便打
        # "QFont::setPointSize: Point size <= 0 (-1)"。控件级规则比全窗 QWidget 规则
        # 更具体，能把字号换成 pt；按当前 DPI 从基准 px 折算，渲染尺寸不变。
        font_pt = BASE_FONT_PX * 72.0 / max(1.0, float(self.window.logicalDpiY()) if self.window else 96.0)
        self.ui_font_combo.setStyleSheet(f"QFontComboBox {{ font-size: {font_pt:.2f}pt; }}")
        # 存的族不可用时，下拉框停在内置首选上——回退说明由提示文字讲清楚，
        # 配置里保留用户的选择，字体装回来就能接着用。
        saved_font = s.ui_font.strip()
        self.ui_font_combo.setCurrentFont(
            QFont(saved_font if _font_is_usable(saved_font) else UI_FONT_FAMILIES[0])
        )
        section.addWidget(self.ui_font_combo)
        self.ui_font_hint = self._hint_label("", TEXT_MUTED)
        section.addWidget(self.ui_font_hint)
        self._refresh_ui_font_hint(saved_font)
        # 初始赋值完成后再连接，打开设置不会把默认值误写入配置。
        self.ui_font_combo.currentFontChanged.connect(self._on_ui_font_changed)

    def _refresh_ui_font_hint(self, family: str) -> None:
        """说明当前生效的字体；选的族用不了时点明已回退。"""
        name = family.strip()
        missing = bool(name) and not _font_is_usable(name)
        if not name:
            text = "使用内置字体栈。"
        elif missing:
            text = f"未安装「{name}」，已回退到内置字体。"
        else:
            text = f"界面使用「{name}」，找不到时回退到内置字体。"
        self._set_label(self.ui_font_hint, text, TEXT_WARNING if missing else TEXT_MUTED)

    def _on_ui_font_changed(self, font: QFont) -> None:
        family = font.family()
        self._settings.ui_font = family
        self.on_field_changed("ui_font", family)
        # 本窗口当场换字体；悬浮输入框与托盘菜单各自在自己的刷新路径上跟进。
        if self.window is not None:
            self.window.setStyleSheet(build_settings_stylesheet(ui_font_stack(family)))
        self._refresh_ui_font_hint(family)

    def _build_placeholder_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("输入框提示文字"))
        self.placeholder_input = QLineEdit(s.overlay_placeholder)
        self.placeholder_input.setAccessibleName("输入框提示文字")
        self.placeholder_input.setPlaceholderText("留空则不显示提示")
        section.addWidget(self.placeholder_input)
        # 初始赋值完成后再连接，打开设置不会把默认值误写入配置。
        self.placeholder_input.textChanged.connect(self._on_placeholder_changed)

    def _on_placeholder_changed(self, text: str) -> None:
        self._settings.overlay_placeholder = text
        self.on_field_changed("overlay_placeholder", text)

    def _build_hotkey_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._section_title("全局快捷键"), 1)
        # 当前值只显示一次，避免重复信息挤占表单空间。
        self.pending_label = self._body_label(f"当前：{s.name}", INPUT_TEXT_COLOR, True)
        self.record_status_label = self._hint_label(RECORD_HINT, TEXT_MUTED)
        self.record_button = QPushButton("录制快捷键")
        self.record_button.clicked.connect(lambda _checked=False: self.on_record_hotkey())
        header.addWidget(self.record_button)
        section.addLayout(header)
        section.addWidget(self.pending_label)
        section.addWidget(self.record_status_label)

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

    def set_audio_output_devices(self, devices: list[AudioOutputDevice] | list[str], error: Exception | None) -> None:
        # 按需扫描完成后仅刷新控件，保留当前配置且不重建音频流。
        s = self._settings
        self._apply_audio_output_devices(
            devices, dict(s.audio_output_device) if s.audio_output_device else None,
            s.audio_output_device_name, error,
        )
        self._local_audio_output_label = self.audio_output_combo.currentText()
        self._sync_audio_output_control(s)

    def _build_volume_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self._section_title("朗读音量"))
        self.volume_value_label = self._body_label(f"{s.volume:.2f}x", INPUT_TEXT_COLOR)
        self.volume_value_label.setObjectName("settingValue")
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
        # 此值是背景 alpha：百分比越低越透明，明确名称避免把 100% 理解为全透明。
        header.addWidget(self._section_title("输入框背景不透明度"))
        self.opacity_value_label = self._body_label(f"{round(s.overlay_opacity * 100)}%", INPUT_TEXT_COLOR)
        self.opacity_value_label.setObjectName("settingValue")
        header.addWidget(self.opacity_value_label, 0, Qt.AlignmentFlag.AlignRight)
        section.addLayout(header)
        self.opacity_slider = NoWheelSlider(Qt.Orientation.Horizontal)
        self.opacity_slider.setRange(0, self._opacity_to_slider(MAX_OVERLAY_OPACITY))
        self.opacity_slider.setSingleStep(1)
        self.opacity_slider.setPageStep(max(1, int(0.10 / OVERLAY_OPACITY_STEP)))
        self.opacity_slider.setValue(self._opacity_to_slider(s.overlay_opacity))
        self.opacity_slider.valueChanged.connect(self._on_opacity_changed)
        section.addWidget(self.opacity_slider)
        section.addWidget(self._hint_label("30% 更透明，100% 完全不透明；文字保持清晰。", TEXT_MUTED))

    def _build_position_section(self, parent_layout: QVBoxLayout, s: AppSettings) -> None:
        section = self._create_section(parent_layout)
        section.addWidget(self._section_title("输入栏位置"))
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
        section.addWidget(self._section_title("侦听麦克风"))
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
        local_output_label = self.audio_output_combo.currentText()
        # 运行态成功后再落盘；否则下次启动仍会显示一个未生效的开关。
        if self.on_field_changed("audio_routing_enabled", enabled) is False:
            was_blocked = self.audio_route_enabled_check.blockSignals(True)
            self.audio_route_enabled_check.setChecked(s.audio_routing_enabled)
            self.audio_route_enabled_check.blockSignals(was_blocked)
            return
        if enabled and not s.audio_routing_enabled:
            self._local_audio_output_label = local_output_label
        s.audio_routing_enabled = enabled  # auto-save
        self._sync_audio_output_control(s)
        self._update_mic_listen_status(s, self._state)

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
        # 同步显示不能触发设备切换；否则打开设置也会写配置并重建 TTS 音频流。

    MIC_NONE_LABEL = "无（不侦听麦克风）"

    def _on_mic_input_selected(self, text: str) -> None:
        device = None if text == self.MIC_NONE_LABEL else (text if text else None)
        old_device = self._settings.mic_input_device
        if self.on_field_changed("mic_input_device", device) is False:
            # 运行态拒绝切换时不能把失败选择写入配置，否则重启后仍会重试坏状态。
            self.mic_input_combo.blockSignals(True)
            prev_device = old_device if old_device else self.MIC_NONE_LABEL
            index = self.mic_input_combo.findText(prev_device)
            self.mic_input_combo.setCurrentIndex(index if index >= 0 else 0)
            self.mic_input_combo.blockSignals(False)
            self.set_status("麦克风侦听切换失败，已保留原设备。", TEXT_ERROR)
            return
        self._settings.mic_input_device = device  # auto-save
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
        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(BUTTON_GAP)
        auto_save_label = self._hint_label("设置自动保存", TEXT_MUTED)
        auto_save_label.setObjectName("autoSaveLabel")
        button_row.addWidget(auto_save_label)
        button_row.addStretch(1)
        exit_button = QPushButton("关闭")
        exit_button.setObjectName("cancelButton")
        # 原生标题栏启用后，底部退出仍不能成为表单 Enter 的默认动作。
        exit_button.setAutoDefault(False)
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
    def voice_label_to_id(self) -> dict[str, str]:
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "voice_label_to_id", {}) if p is not None else {}

    @property
    def voice_label_to_name(self) -> dict[str, str]:
        p = PROVIDER_PANELS.get(self._settings.active_tts_provider)
        return getattr(p, "voice_label_to_name", {}) if p is not None else {}

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

    @staticmethod
    def _create_columns(parent_layout: QVBoxLayout) -> tuple[QVBoxLayout, QVBoxLayout]:
        """两列采用等宽表单，共用上边线，长文案在各自列内换行。"""
        row = QHBoxLayout()
        row.setSpacing(40)
        columns = []
        for _ in range(2):
            column = QVBoxLayout()
            column.setContentsMargins(0, 0, 0, 0)
            column.setSpacing(0)
            row.addLayout(column, 1)
            columns.append(column)
        parent_layout.addLayout(row)
        return columns[0], columns[1]

    def _create_section(self, parent_layout: QVBoxLayout) -> QVBoxLayout:
        section = QVBoxLayout()
        section.setContentsMargins(0, 0, 0, 0)
        section.setSpacing(10)
        parent_layout.addLayout(section)
        return section

    def _create_tab_page(self, tab_widget: QTabWidget, title: str) -> QVBoxLayout:
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        content = QWidget()
        content_layout = QVBoxLayout(content)
        # 横向仍留足阅读边距，收紧上下留白以容纳页标题而无需初始滚动。
        content_layout.setContentsMargins(CONTENT_MARGIN, 26, CONTENT_MARGIN, 24)
        content_layout.setSpacing(0)
        # 页面标题说明当前任务，分组用留白区分，避免窄窗口被边框和卡片切碎。
        page_titles = {
            "本地设置": ("输入与显示", "调整快捷键、提示文字和输入栏外观。"),
            "音频侦听": ("声音与路由", "选择播放设备，控制侦听与返听。"),
            "TTS 设置": ("语音生成", "选择服务商，配置密钥与音色。"),
            "聊天轮盘": ("音频轮盘", "选择八个音频，用全局快捷键随时播放。"),
            "日志": ("运行日志", "调整日志详细程度，便于排查问题。"),
        }
        heading, description = page_titles[title]
        heading_label = QLabel(heading)
        heading_label.setObjectName("pageTitle")
        content_layout.addWidget(heading_label)
        description_label = self._hint_label(description, TEXT_MUTED)
        description_label.setObjectName("pageDescription")
        content_layout.addWidget(description_label)
        content_layout.addSpacing(28)
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
