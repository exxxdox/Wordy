use std::{cell::RefCell, collections::HashSet, io, mem::size_of, ptr};

use windows_sys::{
    Win32::{
        Foundation::{
            ERROR_CLASS_ALREADY_EXISTS, GetLastError, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM,
        },
        Graphics::{
            Dwm::{
                DWMWA_CLOAK, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND,
                DwmSetWindowAttribute,
            },
            Gdi::*,
        },
        System::{LibraryLoader::GetModuleHandleW, SystemServices::SS_OWNERDRAW},
        UI::{
            Controls::{
                DRAWITEMSTRUCT, EM_SETLIMITTEXT, EM_SETMARGINS, ICC_BAR_CLASSES,
                INITCOMMONCONTROLSEX, InitCommonControlsEx, ODS_COMBOBOXEDIT, ODS_DISABLED,
                ODS_FOCUS, ODS_SELECTED, ODT_BUTTON, ODT_COMBOBOX, ODT_STATIC, TBM_GETCHANNELRECT,
                TBM_GETTHUMBRECT, TBM_SETPOS, TBM_SETRANGEMAX, TRACKBAR_CLASSW, WM_MOUSELEAVE,
            },
            HiDpi::{GetDpiForMonitor, GetDpiForWindow, MDT_EFFECTIVE_DPI},
            Input::KeyboardAndMouse::{
                EnableWindow, GetFocus, IsWindowEnabled, TME_LEAVE, TRACKMOUSEEVENT,
                TrackMouseEvent, VK_ESCAPE,
            },
            Shell::{DefSubclassProc, SetWindowSubclass},
            WindowsAndMessaging::*,
        },
    },
    core::{PCWSTR, w},
};

use crate::{
    config::{
        AudioIdentity, CARTESIA_BYTES, CARTESIA_REALTIME, LOG_LEVELS, Settings,
        VOLCENGINE_STREAMING, Voice,
    },
    credentials::{Provider, normalize_api_key_input},
};

pub const SETTINGS_ACTION_MESSAGE: u32 = WM_APP + 10;
const TBM_GETPOS: u32 = WM_USER;
const CLASS: PCWSTR = w!("Wordy.Rust.Settings");
const CLIENT_WIDTH: i32 = 1120;
const EDGE_PAD: i32 = 12;
const RADIUS: i32 = 20;
const TITLE_HEIGHT: i32 = 44;
const FOOTER_HEIGHT: i32 = 54;
const SELECTED_TAB: PCWSTR = w!("Wordy.Settings.Tab");
const CONTROL_KIND: PCWSTR = w!("Wordy.Settings.Kind");
const COLOR_KEY: u32 = rgb(255, 0, 255);
const PRIMARY: u32 = rgb(245, 245, 247);
const MUTED: u32 = rgb(152, 152, 157);
const ACCENT: u32 = rgb(10, 132, 255);
const HOVER_ACCENT: u32 = rgb(64, 156, 255);
const SEPARATOR: u32 = rgb(56, 56, 58);
const CONTROL_BG: u32 = rgb(44, 44, 46);
const CONTROL_BORDER: u32 = rgb(72, 72, 74);
const EMPTY_VOICE: &str = "无";

pub enum SettingsAction {
    SetProvider(Provider),
    SetBackend(String),
    SetVolume(f64),
    SetOpacity(f64),
    SetPlaceholder(String),
    SetUiFont(String),
    SetFixedCenter(bool),
    SetLogLevel(String),
    SetHoldKeyEnabled(bool),
    RecordHoldKey,
    RecordHotkey,
    RecordRollingHotkey,
    SetRollingSeconds(f64),
    SetOutputDevice(Option<AudioIdentity>),
    SetMic(Option<String>),
    SetMainPlayback(bool),
    SetSidetone(bool),
    SetListen(bool),
    SaveKey(Provider, String),
    DeleteKey(Provider),
    RefreshVoices,
    SelectVoice(Provider, Voice),
    SaveVolcengineVoice(Voice),
    DeleteVolcengineVoice(String),
    ExportBackup,
    ImportBackup,
    EditWheels,
    DownloadVbCable,
    OpenLogs,
    RequestAudioDevices,
    Closed,
    Feedback(String),
}

#[derive(Clone, Copy, PartialEq, Eq)]
#[repr(u16)]
enum Field {
    TabLocal = 100,
    TabAudio,
    TabTts,
    TabWheels,
    TabRolling,
    Minimize,
    Maximize,
    TitleClose,
    Resize,
    Close,
    Hotkey,
    HotkeyValue,
    HoldKey,
    HoldKeyValue,
    HoldKeyEnabled,
    Volume,
    VolumeValue,
    Placeholder,
    UiFont,
    FontHint,
    Opacity,
    OpacityValue,
    FixedCenter,
    LogLevel,
    MainPlayback,
    Sidetone,
    Output,
    OutputStatus,
    Mic,
    VirtualMic,
    Listen,
    CableStatus,
    MicStatus,
    Download,
    Provider,
    Backend,
    Key,
    KeyStatus,
    SaveKey,
    DeleteKey,
    Voices,
    RefreshVoices,
    VoiceId,
    VoiceName,
    VoiceHint,
    SaveVoice,
    DeleteVoice,
    Wheels,
    RollingHotkey,
    RollingHotkeyValue,
    RollingSeconds,
    OpenLogs,
    Import,
    Export,
}

#[derive(Clone, Copy, PartialEq, Eq)]
#[repr(usize)]
enum Page {
    Local,
    Audio,
    Tts,
    Wheels,
    Rolling,
}

const PAGES: [(Page, Field, &str, &str, &str); 5] = [
    (
        Page::Local,
        Field::TabLocal,
        "本地设置",
        "输入与显示",
        "调整快捷键、提示文字和输入栏外观。",
    ),
    (
        Page::Audio,
        Field::TabAudio,
        "音频设置",
        "声音与路由",
        "管理主播放、返听与麦克风侦听。",
    ),
    (
        Page::Tts,
        Field::TabTts,
        "TTS 设置",
        "语音生成",
        "选择服务商，配置密钥与音色。",
    ),
    (
        Page::Wheels,
        Field::TabWheels,
        "聊天轮盘",
        "音频轮盘",
        "按住快捷键，移向音频，松开播放；回到中心或按 Esc 取消。",
    ),
    (
        Page::Rolling,
        Field::TabRolling,
        "后台录制",
        "后台录制",
        "持续保留最近的系统声音，停止后用片段填入轮盘槽位。",
    ),
];

type Bounds = [i32; 4];

#[derive(Clone, Copy)]
#[repr(usize)]
enum Kind {
    Label,
    Muted,
    Heading,
    Column,
    Current,
    Value,
    DisabledField,
    Button,
    Check,
    Edit,
    Password,
    Number,
    Combo,
    Slider,
}

impl Kind {
    fn window(self) -> (PCWSTR, u32, u32) {
        match self {
            Self::Label
            | Self::Muted
            | Self::Heading
            | Self::Column
            | Self::Current
            | Self::Value
            | Self::DisabledField => (w!("STATIC"), SS_OWNERDRAW, 0),
            Self::Button => (w!("BUTTON"), WS_TABSTOP | BS_OWNERDRAW as u32, 0),
            Self::Check => (w!("BUTTON"), WS_TABSTOP | BS_AUTOCHECKBOX as u32, 0),
            Self::Edit => (w!("EDIT"), WS_TABSTOP | ES_AUTOHSCROLL as u32, 0),
            Self::Password => (
                w!("EDIT"),
                WS_TABSTOP | ES_AUTOHSCROLL as u32 | ES_PASSWORD as u32,
                0,
            ),
            Self::Number => (w!("EDIT"), WS_TABSTOP | ES_NUMBER as u32, 0),
            Self::Combo => (
                w!("COMBOBOX"),
                WS_TABSTOP
                    | WS_VSCROLL
                    | CBS_DROPDOWNLIST as u32
                    | CBS_OWNERDRAWFIXED as u32
                    | CBS_HASSTRINGS as u32,
                0,
            ),
            Self::Slider => (TRACKBAR_CLASSW, WS_TABSTOP, 0),
        }
    }
}

struct Widget {
    hwnd: HWND,
    field: Option<Field>,
    page: Option<Page>,
    bounds: Bounds,
    kind: Kind,
}

pub struct SettingsWindow {
    hwnd: HWND,
    instance: windows_sys::Win32::Foundation::HINSTANCE,
    widgets: Vec<Widget>,
    fonts: [HFONT; 4],
    // The shell is laid out in 96-DPI units; this factor also feeds per-monitor windows.
    scale: i32,
    wheel_panel: HWND,
    settings: Settings,
    outputs: Vec<AudioIdentity>,
    microphones: Vec<String>,
    cartesia_voices: Vec<Voice>,
    font_families: Vec<String>,
    listen_state: Option<(bool, Option<String>, bool)>,
    cable_available: bool,
    audio_devices_requested: bool,
    page: Page,
}

impl SettingsWindow {
    pub(crate) fn new(host: HWND, settings: &Settings) -> io::Result<Self> {
        let classes = INITCOMMONCONTROLSEX {
            dwSize: size_of::<INITCOMMONCONTROLSEX>() as u32,
            dwICC: ICC_BAR_CLASSES,
        };
        if unsafe { InitCommonControlsEx(&classes) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        let class = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: instance,
            hCursor: unsafe { LoadCursorW(ptr::null_mut(), IDC_ARROW) },
            lpszClassName: CLASS,
            ..Default::default()
        };
        if unsafe { RegisterClassW(&class) } == 0
            && unsafe { GetLastError() } != ERROR_CLASS_ALREADY_EXISTS
        {
            return Err(io::Error::last_os_error());
        }
        let font_families = installed_fonts();
        let current_font = usable_font(&font_families, &settings.ui_font);
        let scale = scale_at_cursor();
        let (width, height) = (scaled(1120, scale), scaled(820, scale));
        let top_left = centered_position(width, height);
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_CONTROLPARENT | WS_EX_LAYERED,
                CLASS,
                w!("Wordy 设置"),
                WS_POPUP
                    | WS_THICKFRAME
                    | WS_SYSMENU
                    | WS_MAXIMIZEBOX
                    | WS_MINIMIZEBOX
                    | WS_CLIPCHILDREN,
                top_left.x,
                top_left.y,
                width,
                height,
                host,
                ptr::null_mut(),
                instance,
                host.cast(),
            )
        };
        if hwnd.is_null() {
            return Err(io::Error::last_os_error());
        }
        unsafe {
            SetLayeredWindowAttributes(hwnd, COLOR_KEY, 247, LWA_COLORKEY | LWA_ALPHA);
            set_dwm(hwnd, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND);
            SetPropW(hwnd, SELECTED_TAB, Field::TabLocal as usize as _);
        }
        let mut window = Self {
            hwnd,
            instance,
            widgets: Vec::new(),
            fonts: create_fonts(current_font, scale),
            scale,
            wheel_panel: ptr::null_mut(),
            settings: settings.clone(),
            outputs: Vec::new(),
            microphones: Vec::new(),
            cartesia_voices: Vec::new(),
            font_families,
            listen_state: None,
            cable_available: false,
            audio_devices_requested: false,
            page: Page::Local,
        };
        window.build()?;
        window.refresh(settings);
        window.layout();
        Ok(window)
    }

    pub fn hwnd(&self) -> HWND {
        self.hwnd
    }

    pub fn show(&self) {
        unsafe {
            // DWM must see the finished surface before presenting a layered HWND.
            let first_frame = IsWindowVisible(self.hwnd) == 0;
            if first_frame {
                set_dwm(self.hwnd, DWMWA_CLOAK, 1);
            }
            ShowWindow(
                self.hwnd,
                if IsIconic(self.hwnd) != 0 {
                    SW_RESTORE
                } else {
                    SW_SHOW
                },
            );
            if first_frame {
                RedrawWindow(
                    self.hwnd,
                    ptr::null(),
                    ptr::null_mut(),
                    RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_UPDATENOW,
                );
                set_dwm(self.hwnd, DWMWA_CLOAK, 0);
            }
            SetForegroundWindow(self.hwnd);
            if first_frame && self.page == Page::Audio {
                let host = GetWindowLongPtrW(self.hwnd, GWLP_USERDATA) as HWND;
                if !host.is_null() {
                    PostMessageW(host, SETTINGS_ACTION_MESSAGE, Field::TabAudio as WPARAM, 0);
                }
            }
        }
    }

    pub fn set_wheel_panel(&mut self, hwnd: HWND) {
        self.wheel_panel = hwnd;
        self.apply_panel_font(hwnd);
        self.layout();
        self.show_page();
    }

    /// Dialog templates carry their own DS_SETFONT, so embedded editors would
    /// otherwise render smaller text than the settings pages around them.
    pub fn apply_panel_font(&self, root: HWND) {
        let body = self.fonts[0];
        if body.is_null() {
            return;
        }
        let emphasis = if self.fonts[3].is_null() {
            body
        } else {
            self.fonts[3]
        };
        unsafe {
            send(root, WM_SETFONT, body as usize, 1);
            let mut child = GetWindow(root, GW_CHILD);
            while !child.is_null() {
                let mut name = [0u16; 32];
                let length = GetClassNameW(child, name.as_mut_ptr(), name.len() as i32);
                let button =
                    length > 0 && String::from_utf16_lossy(&name[..length as usize]) == "Button";
                send(
                    child,
                    WM_SETFONT,
                    if button { emphasis } else { body } as usize,
                    1,
                );
                child = GetWindow(child, GW_HWNDNEXT);
            }
        }
    }

    pub fn toast_anchor(&self) -> Option<RECT> {
        let mut client = RECT::default();
        let mut origin = POINT::default();
        if unsafe { IsWindowVisible(self.hwnd) } == 0
            || unsafe { GetClientRect(self.hwnd, &mut client) } == 0
            || unsafe { ClientToScreen(self.hwnd, &mut origin) } == 0
        {
            return None;
        }
        let pad = scaled(EDGE_PAD, self.scale);
        Some(RECT {
            left: origin.x + pad,
            top: origin.y + pad,
            right: origin.x + client.right - pad,
            bottom: origin.y + client.bottom - pad - scaled(FOOTER_HEIGHT, self.scale),
        })
    }

    pub fn handle_message(&self, message: &MSG) -> bool {
        unsafe { IsWindowVisible(self.hwnd) != 0 && IsDialogMessageW(self.hwnd, message) != 0 }
    }

    pub fn refresh(&mut self, settings: &Settings) {
        if self.settings.ui_font != settings.ui_font {
            let family = usable_font(&self.font_families, &settings.ui_font);
            let old = std::mem::replace(&mut self.fonts, create_fonts(family, self.scale));
            for widget in &self.widgets {
                apply_font(widget.hwnd, widget.kind, &self.fonts);
            }
            free_fonts(old);
        }
        if settings.active_tts_provider != self.settings.active_tts_provider {
            set_text(self.control(Field::Key), "");
        }
        self.settings = settings.clone();
        for (field, prefix, binding) in [
            (Field::HotkeyValue, "当前：", &settings.hotkey),
            (Field::HoldKeyValue, "当前：", &settings.hold_key),
            (
                Field::RollingHotkeyValue,
                "快捷键：",
                &settings.rolling_buffer_hotkey,
            ),
        ] {
            set_text(
                self.control(field),
                &format!("{prefix}{}", crate::hotkey::display_hotkey(binding)),
            );
        }
        self.set_edit(
            Field::RollingSeconds,
            &format!("{}", settings.rolling_buffer_seconds.round()),
        );
        self.set_edit(Field::Placeholder, &settings.overlay_placeholder);
        let family = usable_font(&self.font_families, &settings.ui_font);
        let index = self
            .font_families
            .iter()
            .position(|font| font == family)
            .unwrap_or(0);
        fill_choices(self.control(Field::UiFont), &self.font_families, index);
        let font_hint = if settings.ui_font.is_empty() {
            "使用内置字体栈。".into()
        } else if family != settings.ui_font {
            format!("未安装「{}」，已回退到内置字体。", settings.ui_font)
        } else {
            format!("界面使用「{family}」，找不到时回退到内置字体。")
        };
        set_text(self.control(Field::FontHint), &font_hint);
        self.check(Field::HoldKeyEnabled, settings.hold_key_enabled);
        self.check(Field::FixedCenter, settings.fixed_center);
        self.check(Field::MainPlayback, settings.main_playback_enabled);
        self.check(Field::Sidetone, settings.sidetone_enabled);
        set_slider(
            self.control(Field::Volume),
            slider_position(settings.volume, 0.5),
        );
        set_slider(
            self.control(Field::Opacity),
            slider_position(settings.overlay_opacity, 0.30),
        );
        self.refresh_slider_labels();
        fill_choices(
            self.control(Field::LogLevel),
            LOG_LEVELS,
            LOG_LEVELS
                .iter()
                .position(|&level| level == settings.log_level)
                .unwrap_or(1),
        );
        self.refresh_audio_choices();
        self.refresh_audio_status();
        self.refresh_provider();
    }

    pub fn set_output_devices(&mut self, outputs: Vec<AudioIdentity>) {
        self.outputs = outputs;
        self.refresh_audio_choices();
    }

    pub fn set_input_devices(&mut self, microphones: Vec<String>) {
        self.microphones = microphones;
        self.refresh_audio_choices();
    }

    pub fn set_cartesia_voices(&mut self, voices: Vec<Voice>) {
        self.cartesia_voices = voices;
        if self.settings.active_tts_provider == Provider::Cartesia {
            self.refresh_provider();
        }
    }

    pub fn set_listen_state(&mut self, state: Option<(bool, Option<String>, bool)>) {
        self.listen_state = state;
        self.refresh_audio_status();
    }

    pub fn set_vb_cable_available(&mut self, available: bool) {
        self.cable_available = available;
        self.refresh_audio_status();
        self.show_page();
    }

    pub fn action(&mut self, action_id: u16) -> Option<SettingsAction> {
        use SettingsAction::*;
        if action_id == Field::Resize as u16 {
            self.layout();
            return None;
        }
        if action_id == Field::Close as u16 || action_id == Field::TitleClose as u16 {
            unsafe { ShowWindow(self.hwnd, SW_HIDE) };
            set_text(self.control(Field::Key), "");
            self.audio_devices_requested = false;
            return Some(Closed);
        }
        if let Some(&(page, field, ..)) = PAGES
            .iter()
            .find(|(_, field, ..)| *field as u16 == action_id)
        {
            self.page = page;
            unsafe { SetPropW(self.hwnd, SELECTED_TAB, field as usize as _) };
            self.show_page();
            for (_, field, ..) in PAGES {
                unsafe { InvalidateRect(self.control(field), ptr::null(), 0) };
            }
            if page == Page::Audio && !self.audio_devices_requested {
                self.audio_devices_requested = true;
                return Some(RequestAudioDevices);
            }
            if page == Page::Wheels && self.wheel_panel.is_null() {
                return Some(EditWheels);
            }
            return None;
        }
        let field = self
            .widgets
            .iter()
            .filter_map(|widget| widget.field)
            .find(|&field| field as u16 == action_id)?;
        let control = self.control(field);
        let active = self.settings.active_tts_provider;
        match field {
            Field::Minimize => {
                unsafe { ShowWindow(self.hwnd, SW_MINIMIZE) };
                None
            }
            Field::Maximize => {
                toggle_zoom(self.hwnd);
                None
            }
            Field::Hotkey => Some(RecordHotkey),
            Field::HoldKey => Some(RecordHoldKey),
            Field::RollingHotkey => Some(RecordRollingHotkey),
            Field::RollingSeconds => {
                let parsed = text(control).parse::<u16>().ok();
                if let Some(value) = parsed
                    .map(f64::from)
                    .filter(|&value| value != self.settings.rolling_buffer_seconds)
                {
                    return Some(SetRollingSeconds(value));
                }
                if parsed.is_none() {
                    set_text(
                        control,
                        &format!("{}", self.settings.rolling_buffer_seconds.round()),
                    );
                }
                None
            }
            Field::HoldKeyEnabled => Some(SetHoldKeyEnabled(checked(control))),
            Field::FixedCenter => Some(SetFixedCenter(checked(control))),
            Field::MainPlayback => Some(SetMainPlayback(checked(control))),
            Field::Sidetone => Some(SetSidetone(checked(control))),
            Field::Listen => Some(SetListen(checked(control))),
            Field::Volume => {
                let value = slider_value(send(control, TBM_GETPOS, 0, 0), 0.5);
                set_text(self.control(Field::VolumeValue), &format!("{value:.2}x"));
                Some(SetVolume(value))
            }
            Field::Opacity => {
                let value = slider_value(send(control, TBM_GETPOS, 0, 0), 0.30);
                set_text(
                    self.control(Field::OpacityValue),
                    &format!("{:.0}%", value * 100.0),
                );
                Some(SetOpacity(value))
            }
            Field::Placeholder => {
                changed(text(control), &self.settings.overlay_placeholder).map(SetPlaceholder)
            }
            Field::UiFont => choice(control)
                .and_then(|index| self.font_families.get(index))
                .cloned()
                .map(SetUiFont),
            Field::LogLevel => choice(control)
                .and_then(|index| LOG_LEVELS.get(index))
                .map(|&level| SetLogLevel(level.into())),
            Field::Mic => choice(control).map(|index| {
                SetMic(
                    index
                        .checked_sub(1)
                        .and_then(|index| self.microphones.get(index).cloned()),
                )
            }),
            Field::Output => choice(control).map(|index| {
                SetOutputDevice(
                    index
                        .checked_sub(1)
                        .and_then(|index| self.outputs.get(index).cloned()),
                )
            }),
            Field::Provider => choice(control).map(|index| {
                SetProvider(if index == 0 {
                    Provider::Volcengine
                } else {
                    Provider::Cartesia
                })
            }),
            Field::Backend => choice(control)
                .and_then(|index| backends(active).get(index))
                .map(|&backend| SetBackend(backend.into())),
            Field::SaveKey => {
                let field = self.control(Field::Key);
                let raw = text(field);
                let key = if active == Provider::Cartesia {
                    normalize_api_key_input(&raw)
                } else {
                    raw.trim()
                };
                if key.is_empty() {
                    return Some(Feedback("请输入 API Key。".into()));
                }
                set_text(field, "");
                Some(SaveKey(active, key.to_owned()))
            }
            Field::DeleteKey => self
                .confirm("确定删除当前服务商的 API Key？")
                .then_some(DeleteKey(active)),
            Field::Voices => self
                .voice_selected()
                .map(|voice| SelectVoice(active, voice)),
            Field::RefreshVoices => Some(RefreshVoices),
            Field::SaveVoice => {
                let id = text(self.control(Field::VoiceId)).trim().to_owned();
                if id.is_empty() {
                    return Some(Feedback("请输入 Speaker ID。".into()));
                }
                let name = text(self.control(Field::VoiceName)).trim().to_owned();
                set_text(self.control(Field::VoiceId), "");
                set_text(self.control(Field::VoiceName), "");
                Some(SaveVolcengineVoice(Voice {
                    name: if name.is_empty() { id.clone() } else { name },
                    id,
                }))
            }
            Field::DeleteVoice => self
                .voice_selected()
                .filter(|_| self.confirm("确定删除当前音色？"))
                .map(|voice| DeleteVolcengineVoice(voice.id)),
            Field::Download => Some(DownloadVbCable),
            Field::Wheels => Some(EditWheels),
            Field::OpenLogs => Some(OpenLogs),
            Field::Export => Some(ExportBackup),
            Field::Import => Some(ImportBackup),
            _ => None,
        }
    }

    fn build(&mut self) -> io::Result<()> {
        use Field::*;
        use Kind::*;
        use Page::{Audio, Local, Rolling, Tts};

        self.add(None, None, Column, "设置", [48, 19, 300, 25])?;
        for (field, caption, x) in [
            (Minimize, "最小化", 1000),
            (Maximize, "最大化", 1034),
            (TitleClose, "关闭", 1068),
        ] {
            self.add(Some(field), None, Button, caption, [x, 20, 28, 28])?;
        }
        let tab_geometry = [(48, 86), (138, 86), (228, 112), (344, 86), (434, 86)];
        for ((page, field, caption, heading, description), (x, width)) in
            PAGES.into_iter().zip(tab_geometry)
        {
            self.add(Some(field), None, Button, caption, [x, 56, width, 30])?;
            self.add(None, Some(page), Heading, heading, [48, 99, 972, 43])?;
            self.add(None, Some(page), Muted, description, [48, 137, 972, 24])?;
        }

        for (page, field, kind, caption, bounds) in [
            (Local, None, Column, "快捷键与播放", [48, 171, 440, 38]),
            (Local, None, Label, "全局快捷键", [48, 222, 300, 22]),
            (Local, Some(HotkeyValue), Current, "", [48, 249, 335, 32]),
            (
                Local,
                Some(Hotkey),
                Button,
                "录制快捷键",
                [397, 250, 91, 32],
            ),
            (
                Local,
                None,
                Muted,
                "点击录制后按下新的快捷键组合",
                [48, 282, 440, 24],
            ),
            (
                Local,
                Some(HoldKeyEnabled),
                Check,
                "播放时自动按住某键",
                [48, 310, 154, 32],
            ),
            (Local, Some(HoldKeyValue), Current, "", [201, 310, 194, 32]),
            (Local, Some(HoldKey), Button, "录制按键", [410, 310, 78, 32]),
            (
                Local,
                None,
                Muted,
                "开启后，播放语音前自动按下该键，结束后松开。",
                [48, 342, 440, 24],
            ),
            (Local, None, Label, "朗读音量", [48, 391, 160, 24]),
            (Local, Some(VolumeValue), Value, "", [395, 396, 93, 17]),
            (Local, Some(Volume), Slider, "", [40, 414, 456, 28]),
            (
                Local,
                None,
                Muted,
                "范围 0.50x - 2.00x，默认 1.00x",
                [48, 438, 440, 24],
            ),
            (Local, None, Column, "界面与显示", [578, 171, 440, 38]),
            (Local, None, Label, "界面字体", [578, 222, 440, 22]),
            (Local, Some(UiFont), Combo, "", [578, 250, 440, 242]),
            (Local, Some(FontHint), Muted, "", [578, 282, 440, 24]),
            (Local, None, Label, "输入框提示文字", [578, 314, 440, 24]),
            (Local, Some(Placeholder), Edit, "", [578, 343, 440, 32]),
            (
                Local,
                None,
                Label,
                "输入框背景不透明度",
                [578, 391, 240, 24],
            ),
            (Local, Some(OpacityValue), Value, "", [937, 396, 81, 17]),
            (Local, Some(Opacity), Slider, "", [570, 414, 456, 28]),
            (
                Local,
                None,
                Muted,
                "30% 更透明，100% 完全不透明；文字保持清晰。",
                [578, 438, 440, 24],
            ),
            (Local, None, Label, "输入栏位置", [578, 469, 440, 22]),
            (
                Local,
                Some(FixedCenter),
                Check,
                "固定出现在屏幕中心",
                [578, 491, 440, 25],
            ),
            (
                Local,
                None,
                Muted,
                "取消勾选后，可拖动输入窗口；松开鼠标后自动记住位置。",
                [578, 515, 440, 24],
            ),
            (Local, None, Label, "日志等级", [578, 549, 440, 22]),
            (Local, Some(LogLevel), Combo, "", [578, 575, 440, 180]),
            (
                Local,
                None,
                Muted,
                "设置日志记录的详细程度。",
                [578, 607, 440, 24],
            ),
            (Audio, None, Column, "播放与返听", [48, 171, 440, 38]),
            (Audio, None, Label, "主播放输出", [48, 222, 440, 22]),
            (
                Audio,
                Some(MainPlayback),
                Check,
                "启用主播放输出",
                [48, 246, 440, 24],
            ),
            (Audio, Some(CableStatus), Muted, "", [48, 272, 440, 28]),
            (
                Audio,
                Some(Download),
                Button,
                "下载并安装 VB-CABLE",
                [48, 304, 169, 30],
            ),
            (Audio, None, Label, "返听", [48, 340, 440, 20]),
            (Audio, Some(Sidetone), Check, "启用返听", [48, 362, 440, 24]),
            (Audio, None, Label, "返听播放设备", [48, 388, 440, 20]),
            (Audio, Some(Output), Combo, "", [48, 412, 440, 230]),
            (Audio, Some(OutputStatus), Muted, "", [48, 445, 440, 24]),
            (Audio, None, Column, "麦克风与侦听", [578, 171, 440, 38]),
            (Audio, None, Label, "音频侦听", [578, 222, 440, 22]),
            (
                Audio,
                None,
                Muted,
                "通过 Windows 将所选麦克风送至 CABLE Input。",
                [578, 244, 440, 22],
            ),
            (Audio, None, Label, "麦克风输入", [578, 278, 440, 22]),
            (
                Audio,
                Some(VirtualMic),
                DisabledField,
                "",
                [578, 305, 440, 32],
            ),
            (
                Audio,
                Some(Listen),
                Check,
                "启用音频侦听",
                [578, 341, 440, 25],
            ),
            (Audio, None, Label, "侦听麦克风", [578, 366, 440, 22]),
            (Audio, Some(Mic), Combo, "", [578, 392, 440, 230]),
            (Audio, Some(MicStatus), Muted, "", [578, 424, 440, 24]),
            (Tts, None, Column, "生成方式", [48, 171, 395, 38]),
            (Tts, None, Label, "TTS 服务商", [48, 222, 395, 22]),
            (Tts, Some(Provider), Combo, "", [48, 250, 395, 160]),
            (
                Tts,
                None,
                Muted,
                "选择 TTS 服务商，下方设置区同步切换。",
                [48, 281, 395, 24],
            ),
            (Tts, None, Label, "生成模式", [48, 316, 395, 22]),
            (Tts, Some(Backend), Combo, "", [48, 343, 395, 160]),
            (
                Tts,
                None,
                Muted,
                "Bytes：完整生成后播放。Streaming：边生成边播放。",
                [48, 375, 395, 24],
            ),
            (Tts, None, Column, "音色与密钥", [479, 171, 440, 38]),
            (Tts, None, Label, "API Key", [495, 222, 577, 22]),
            (Tts, Some(KeyStatus), Muted, "", [495, 244, 576, 22]),
            (Tts, Some(Key), Password, "", [495, 271, 385, 32]),
            (Tts, Some(SaveKey), Button, "保存", [888, 271, 88, 32]),
            (Tts, Some(DeleteKey), Button, "删除", [984, 271, 88, 32]),
            (
                Tts,
                None,
                Muted,
                "密钥保存在 Windows 凭据管理器中。",
                [495, 304, 577, 24],
            ),
            (Tts, None, Label, "音色选择", [495, 330, 577, 22]),
            (
                Tts,
                None,
                Muted,
                "选择当前用于朗读的音色。",
                [495, 352, 577, 22],
            ),
            (Tts, Some(Voices), Combo, "", [495, 376, 481, 230]),
            (
                Tts,
                Some(RefreshVoices),
                Button,
                "刷新音色",
                [984, 376, 88, 32],
            ),
            (
                Tts,
                Some(DeleteVoice),
                Button,
                "删除音色",
                [984, 376, 88, 32],
            ),
            (Tts, Some(VoiceHint), Muted, "", [495, 437, 577, 22]),
            (Tts, None, Muted, "Speaker ID", [495, 463, 124, 32]),
            (Tts, Some(VoiceId), Edit, "", [624, 465, 448, 32]),
            (Tts, None, Muted, "名称", [495, 504, 76, 32]),
            (Tts, Some(VoiceName), Edit, "", [576, 505, 496, 32]),
            (
                Tts,
                Some(SaveVoice),
                Button,
                "添加 / 保存音色",
                [892, 545, 180, 32],
            ),
            (
                Page::Wheels,
                Some(Wheels),
                Button,
                "编辑聊天轮盘",
                [48, 181, 200, 32],
            ),
            (Rolling, None, Label, "录制设置", [48, 174, 440, 22]),
            (
                Rolling,
                Some(RollingHotkeyValue),
                Current,
                "",
                [48, 201, 333, 32],
            ),
            (
                Rolling,
                Some(RollingHotkey),
                Button,
                "录制快捷键",
                [397, 201, 91, 32],
            ),
            (
                Rolling,
                None,
                Muted,
                "点击录制后按下新的快捷键组合",
                [48, 234, 440, 24],
            ),
            (Rolling, None, Label, "保留时长", [48, 261, 84, 34]),
            (
                Rolling,
                Some(RollingSeconds),
                Number,
                "",
                [132, 261, 116, 34],
            ),
            (Rolling, None, Muted, "秒", [252, 261, 35, 34]),
        ] {
            self.add(field, Some(page), kind, caption, bounds)?;
        }
        for (field, kind, caption, bounds) in [
            (None, Muted, "设置自动保存", [48, -56, 105, 32]),
            (Some(OpenLogs), Button, "查看日志", [157, -56, 92, 32]),
            (Some(Export), Button, "导出备份", [812, -56, 78, 32]),
            (Some(Import), Button, "导入备份", [898, -56, 78, 32]),
            (Some(Close), Button, "关闭", [983, -56, 88, 32]),
        ] {
            self.add(field, None, kind, caption, bounds)?;
        }
        send(self.control(Volume), TBM_SETRANGEMAX, 1, 30);
        send(self.control(Opacity), TBM_SETRANGEMAX, 1, 14);
        send(self.control(RollingSeconds), EM_SETLIMITTEXT, 3, 0);
        send(self.control(Key), EM_SETLIMITTEXT, 1280, 0);
        self.show_page();
        Ok(())
    }

    fn add(
        &mut self,
        field: Option<Field>,
        page: Option<Page>,
        kind: Kind,
        caption: &str,
        bounds: Bounds,
    ) -> io::Result<()> {
        let (class, style, ex) = kind.window();
        let caption = wide(caption);
        let id = field.map_or(0, |field| field as usize);
        let hwnd = unsafe {
            CreateWindowExW(
                ex,
                class,
                caption.as_ptr(),
                WS_CHILD | WS_CLIPSIBLINGS | style,
                scaled(bounds[0], self.scale),
                scaled(bounds[1], self.scale),
                scaled(bounds[2], self.scale),
                scaled(bounds[3], self.scale),
                self.hwnd,
                id as HMENU,
                self.instance,
                ptr::null(),
            )
        };
        if hwnd.is_null() {
            return Err(io::Error::last_os_error());
        }
        apply_font(hwnd, kind, &self.fonts);
        unsafe {
            SetPropW(hwnd, CONTROL_KIND, (kind as usize + 1) as _);
            SetWindowSubclass(hwnd, Some(style_control), 1, kind as usize);
        }
        if matches!(kind, Kind::Edit | Kind::Password | Kind::Number) {
            send(
                hwnd,
                EM_SETMARGINS,
                (EC_LEFTMARGIN | EC_RIGHTMARGIN) as usize,
                (scaled(10, self.scale) | (scaled(10, self.scale) << 16)) as isize,
            );
            crate::desktop::center_edit_text(hwnd, CONTROL_BG);
        }
        if matches!(kind, Kind::Combo) {
            send(
                hwnd,
                CB_SETITEMHEIGHT,
                usize::MAX,
                scaled(28, self.scale) as isize,
            );
            send(hwnd, CB_SETITEMHEIGHT, 0, scaled(28, self.scale) as isize);
            // Same rounded dark field as the wheel editor's selectors.
            crate::selector::style_selector(hwnd, true);
        }
        self.widgets.push(Widget {
            hwnd,
            field,
            page,
            bounds,
            kind,
        });
        Ok(())
    }

    fn layout(&self) {
        let mut client = RECT::default();
        unsafe { GetClientRect(self.hwnd, &mut client) };
        let width = client.right.max(1);
        for widget in &self.widgets {
            let [x, y, w, h] = widget.bounds;
            // Horizontal coordinates stretch with the client; vertical rhythm follows DPI.
            let top = if y < 0 {
                client.bottom + scaled(y, self.scale)
            } else {
                scaled(y, self.scale)
            };
            let height = if h < 0 {
                client.bottom + scaled(h, self.scale)
            } else {
                scaled(h, self.scale)
            };
            unsafe {
                SetWindowPos(
                    widget.hwnd,
                    ptr::null_mut(),
                    x * width / CLIENT_WIDTH,
                    top,
                    w * width / CLIENT_WIDTH,
                    height.max(10),
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
            }
        }
        if !self.wheel_panel.is_null() {
            let x = 48 * width / CLIENT_WIDTH;
            let top = scaled(187, self.scale);
            unsafe {
                SetWindowPos(
                    self.wheel_panel,
                    ptr::null_mut(),
                    x,
                    top,
                    width - 2 * x,
                    (client.bottom - top - scaled(EDGE_PAD + FOOTER_HEIGHT + 8, self.scale)).max(1),
                    SWP_NOZORDER | SWP_NOACTIVATE,
                );
            }
        }
        unsafe { InvalidateRect(self.hwnd, ptr::null(), 1) };
    }

    fn show_page(&self) {
        let provider = self.settings.active_tts_provider;
        for widget in &self.widgets {
            let correct_page = widget.page.is_none_or(|page| page == self.page);
            let visible = match widget.field {
                Some(Field::Wheels) => self.wheel_panel.is_null(),
                Some(Field::Download) => !self.cable_available,
                Some(Field::RefreshVoices) => provider == Provider::Cartesia,
                Some(
                    Field::VoiceId
                    | Field::VoiceName
                    | Field::VoiceHint
                    | Field::SaveVoice
                    | Field::DeleteVoice,
                ) => provider == Provider::Volcengine,
                // Speaker ID and its name labels sit beside Volcengine's two edits.
                None if widget.page == Some(Page::Tts) && widget.bounds[1] >= 437 => {
                    provider == Provider::Volcengine
                }
                _ => true,
            };
            let desired = correct_page && visible;
            if (unsafe { IsWindowVisible(widget.hwnd) } != 0) != desired {
                unsafe { ShowWindow(widget.hwnd, if desired { SW_SHOW } else { SW_HIDE }) };
            }
        }
        // Repaint the glass under controls that were hidden by the page switch.
        unsafe { InvalidateRect(self.hwnd, ptr::null(), 0) };
        if !self.wheel_panel.is_null() {
            unsafe {
                if self.page == Page::Wheels {
                    SetWindowPos(
                        self.wheel_panel,
                        HWND_TOP,
                        0,
                        0,
                        0,
                        0,
                        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
                    );
                    RedrawWindow(
                        self.wheel_panel,
                        ptr::null(),
                        ptr::null_mut(),
                        RDW_INVALIDATE | RDW_ALLCHILDREN,
                    );
                } else {
                    ShowWindow(self.wheel_panel, SW_HIDE);
                }
            };
        }
        unsafe {
            // Repaint the uncovered shell; shown controls invalidate themselves.
            RedrawWindow(self.hwnd, ptr::null(), ptr::null_mut(), RDW_INVALIDATE);
        }
    }

    fn control(&self, field: Field) -> HWND {
        self.widgets
            .iter()
            .find(|widget| widget.field == Some(field))
            .expect("the settings form declares all controls")
            .hwnd
    }

    fn set_edit(&self, field: Field, value: &str) {
        let control = self.control(field);
        if unsafe { GetFocus() } != control {
            set_text(control, value);
        }
    }

    fn check(&self, field: Field, check: bool) {
        send(self.control(field), BM_SETCHECK, usize::from(check), 0);
    }

    fn refresh_slider_labels(&self) {
        set_text(
            self.control(Field::VolumeValue),
            &format!("{:.2}x", self.settings.volume),
        );
        set_text(
            self.control(Field::OpacityValue),
            &format!("{:.0}%", self.settings.overlay_opacity * 100.0),
        );
    }

    fn refresh_audio_choices(&self) {
        let chosen = self.settings.audio_output_device.as_ref();
        let legacy = self.settings.audio_output_device_name.as_deref();
        let selected = self.outputs.iter().position(|output| {
            chosen.map_or_else(
                || legacy == Some(output.name.as_str()),
                |chosen| chosen == output,
            )
        });
        let outputs =
            std::iter::once("默认（跟随系统）".into()).chain(self.outputs.iter().map(|output| {
                output.host_api_name.as_ref().map_or_else(
                    || output.name.clone(),
                    |api| format!("{} ({api})", output.name),
                )
            }));
        fill_choices(
            self.control(Field::Output),
            outputs,
            selected.map_or(0, |index| index + 1),
        );
        set_text(
            self.control(Field::OutputStatus),
            &format!("已找到 {} 个输出设备。", self.outputs.len()),
        );
        let selected = self
            .microphones
            .iter()
            .position(|mic| Some(mic) == self.settings.mic_input_device.as_ref());
        let microphones = std::iter::once("无（不侦听麦克风）")
            .chain(self.microphones.iter().map(String::as_str));
        fill_choices(
            self.control(Field::Mic),
            microphones,
            selected.map_or(0, |index| index + 1),
        );
    }

    fn refresh_audio_status(&self) {
        set_text(
            self.control(Field::CableStatus),
            if self.cable_available {
                "输出至 CABLE Input。语音软件的输入设备选 CABLE Output。"
            } else {
                "未检测到 VB-CABLE。安装后即可输出至 CABLE Input。"
            },
        );
        set_text(
            self.control(Field::VirtualMic),
            if self.cable_available {
                "CABLE Input"
            } else {
                "未检测到 VB-CABLE"
            },
        );
        let listen = self
            .listen_state
            .as_ref()
            .map_or(self.settings.audio_routing_enabled, |state| state.0);
        self.check(Field::Listen, listen);
        unsafe { EnableWindow(self.control(Field::Listen), i32::from(self.cable_available)) };
        let status = match &self.listen_state {
            Some((enabled, output, has_target)) => {
                let mut text = if *enabled {
                    "Windows 侦听：已开启".to_string()
                } else {
                    "Windows 侦听：未开启".to_string()
                };
                if let Some(output) = output {
                    text.push_str(&format!(" · 输出目标：{}", short(output, 28)));
                } else if *has_target {
                    text.push_str(" · 输出目标：设备不可用");
                } else if *enabled {
                    text.push_str(" · 输出目标：默认播放设备");
                }
                text
            }
            None if self.settings.mic_input_device.is_some() => {
                "无法读取侦听状态——请确认该设备支持系统侦听".into()
            }
            None => "选择麦克风后自动配置".into(),
        };
        set_text(self.control(Field::MicStatus), &status);
    }

    fn refresh_provider(&self) {
        let provider = self.settings.active_tts_provider;
        fill_choices(
            self.control(Field::Provider),
            [Provider::Volcengine.name(), Provider::Cartesia.name()],
            usize::from(provider == Provider::Cartesia),
        );
        let backends = backends(provider);
        fill_choices(
            self.control(Field::Backend),
            backends.iter().copied(),
            backends
                .iter()
                .position(|&mode| mode == self.settings.active_backend())
                .unwrap_or(0),
        );
        let has_key = match provider {
            Provider::Cartesia => self.settings.tts_providers.cartesia.api_key_set,
            Provider::Volcengine => self.settings.tts_providers.volcengine.api_key_set,
        };
        set_text(
            self.control(Field::KeyStatus),
            if has_key {
                "API Key 已保存"
            } else {
                "API Key 未保存"
            },
        );
        unsafe { EnableWindow(self.control(Field::DeleteKey), i32::from(has_key)) };
        let voices = self.voices();
        fill_choices(
            self.control(Field::Voices),
            voice_labels(voices),
            selected_voice(voices, self.settings.active_voice_id()),
        );
        let active_volc = self
            .settings
            .tts_providers
            .volcengine
            .voice_list
            .first()
            .map_or("无", |voice| voice.id.as_str());
        set_text(
            self.control(Field::VoiceHint),
            &format!("当前 Speaker ID：{active_volc}"),
        );
        self.show_page();
    }

    fn voices(&self) -> &[Voice] {
        match self.settings.active_tts_provider {
            Provider::Cartesia => &self.cartesia_voices,
            Provider::Volcengine => &self.settings.tts_providers.volcengine.voice_list,
        }
    }

    fn voice_selected(&self) -> Option<Voice> {
        choice(self.control(Field::Voices))
            .and_then(|index| index.checked_sub(1))
            .and_then(|index| self.voices().get(index).cloned())
    }

    fn confirm(&self, message: &str) -> bool {
        let message = wide(message);
        unsafe {
            MessageBoxW(
                self.hwnd,
                message.as_ptr(),
                w!("Wordy"),
                MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2,
            ) == IDYES
        }
    }
}

impl Drop for SettingsWindow {
    fn drop(&mut self) {
        unsafe { DestroyWindow(self.hwnd) };
        free_fonts(self.fonts);
    }
}

unsafe extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // Store only the owner's HWND. The callback posts actions and never borrows
    // SettingsWindow, including during CreateWindowExW or programmatic refresh.
    if message == WM_NCCREATE {
        let creation = unsafe { &*(lparam as *const CREATESTRUCTW) };
        unsafe { SetWindowLongPtrW(hwnd, GWLP_USERDATA, creation.lpCreateParams as isize) };
    }
    let host = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } as HWND;
    let post = |id: u16| {
        if !host.is_null() {
            unsafe { PostMessageW(host, SETTINGS_ACTION_MESSAGE, id as WPARAM, 0) };
        }
    };
    match message {
        WM_NCCALCSIZE | WM_NCPAINT | WM_ERASEBKGND => return 0,
        WM_NCACTIVATE => return 1,
        WM_NCHITTEST => {
            let x = lparam as i16 as i32;
            let y = (lparam >> 16) as i16 as i32;
            let mut point = POINT { x, y };
            let mut client = RECT::default();
            unsafe {
                ScreenToClient(hwnd, &mut point);
                GetClientRect(hwnd, &mut client);
            }
            return shell_hit(
                point.x,
                point.y,
                client.right,
                client.bottom,
                unsafe { IsZoomed(hwnd) != 0 },
                unsafe { GetDpiForWindow(hwnd) }.max(96) as i32,
            );
        }
        WM_NCLBUTTONDBLCLK if wparam == HTCAPTION as usize => {
            toggle_zoom(hwnd);
            return 0;
        }
        WM_KEYDOWN if wparam as u16 == VK_ESCAPE => {
            unsafe { ShowWindow(hwnd, SW_HIDE) };
            post(Field::Close as u16);
            return 0;
        }
        WM_CLOSE => {
            unsafe { ShowWindow(hwnd, SW_HIDE) };
            post(Field::Close as u16);
            return 0;
        }
        WM_SIZE => post(Field::Resize as u16),
        WM_GETMINMAXINFO => {
            let limits = unsafe { &mut *(lparam as *mut MINMAXINFO) };
            let monitor = unsafe { MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) };
            let mut info = MONITORINFO {
                cbSize: size_of::<MONITORINFO>() as u32,
                ..Default::default()
            };
            if unsafe { GetMonitorInfoW(monitor, &mut info) } != 0 {
                limits.ptMaxPosition.x = info.rcWork.left - info.rcMonitor.left;
                limits.ptMaxPosition.y = info.rcWork.top - info.rcMonitor.top;
                limits.ptMaxSize.x = info.rcWork.right - info.rcWork.left;
                limits.ptMaxSize.y = info.rcWork.bottom - info.rcWork.top;
            }
            let scale = scale_at_cursor();
            limits.ptMinTrackSize.x = scaled(960, scale);
            // The fixed two-column layout has no scrollbars; keep the full height usable.
            limits.ptMinTrackSize.y = scaled(820, scale);
        }
        WM_PAINT => {
            paint_shell(hwnd);
            return 0;
        }
        WM_CTLCOLORSTATIC | WM_CTLCOLOREDIT | WM_CTLCOLORLISTBOX if wparam != 0 => {
            let dc = wparam as HDC;
            let kind = unsafe { GetPropW(lparam as HWND, CONTROL_KIND) } as usize;
            let color = if kind == Kind::Muted as usize + 1 {
                MUTED
            } else {
                PRIMARY
            };
            unsafe {
                SetTextColor(dc, color);
                if message == WM_CTLCOLORSTATIC {
                    SetBkMode(dc, TRANSPARENT as i32);
                    return GetStockObject(NULL_BRUSH) as LRESULT;
                }
                SetBkColor(dc, CONTROL_BG);
                SetDCBrushColor(dc, CONTROL_BG);
                return GetStockObject(DC_BRUSH) as LRESULT;
            }
        }
        WM_DRAWITEM if lparam != 0 => {
            let item = unsafe { &*(lparam as *const DRAWITEMSTRUCT) };
            paint_item(hwnd, item);
            return 1;
        }
        WM_COMMAND if lparam != 0 => {
            let id = wparam as u16;
            let notification = ((wparam >> 16) & 0xffff) as u32;
            if notification == BN_CLICKED as u32
                && (Field::TabLocal as u16..=Field::TabRolling as u16).contains(&id)
            {
                // The page swap runs after the posted action; paint the selected
                // tab now so the click has immediate feedback.
                unsafe { SetPropW(hwnd, SELECTED_TAB, id as usize as _) };
                for tab in Field::TabLocal as u16..=Field::TabRolling as u16 {
                    unsafe { InvalidateRect(GetDlgItem(hwnd, i32::from(tab)), ptr::null(), 0) };
                }
            }
            if [BN_CLICKED, CBN_SELCHANGE, EN_KILLFOCUS].contains(&notification) {
                post(id);
                return 0;
            }
        }
        WM_HSCROLL if lparam != 0 => {
            let field = unsafe { GetDlgCtrlID(lparam as HWND) };
            if field == Field::Volume as i32 || field == Field::Opacity as i32 {
                post(field as u16);
                return 0;
            }
        }
        _ => {}
    }
    unsafe { DefWindowProcW(hwnd, message, wparam, lparam) }
}

fn shell_hit(x: i32, y: i32, width: i32, height: i32, maximized: bool, scale: i32) -> LRESULT {
    let pad = if maximized {
        0
    } else {
        scaled(EDGE_PAD, scale)
    };
    if !maximized {
        // The first opaque pixels inside the transparent margin also resize.
        let hot_zone = scaled(EDGE_PAD + 4, scale);
        let column = if x < hot_zone {
            -1
        } else if x >= width - hot_zone {
            1
        } else {
            0
        };
        let row = if y < hot_zone {
            -1
        } else if y >= height - hot_zone {
            1
        } else {
            0
        };
        let hit = match (column, row) {
            (-1, -1) => HTTOPLEFT,
            (1, -1) => HTTOPRIGHT,
            (-1, 1) => HTBOTTOMLEFT,
            (1, 1) => HTBOTTOMRIGHT,
            (-1, _) => HTLEFT,
            (1, _) => HTRIGHT,
            (_, -1) => HTTOP,
            (_, 1) => HTBOTTOM,
            _ => HTCLIENT,
        };
        if hit != HTCLIENT {
            return hit as LRESULT;
        }
    }
    if y >= pad && y < pad + scaled(TITLE_HEIGHT, scale) {
        HTCAPTION as LRESULT
    } else {
        HTCLIENT as LRESULT
    }
}

fn toggle_zoom(hwnd: HWND) {
    unsafe {
        ShowWindow(
            hwnd,
            if IsZoomed(hwnd) != 0 {
                SW_RESTORE
            } else {
                SW_MAXIMIZE
            },
        )
    };
}

fn centered_position(width: i32, height: i32) -> POINT {
    let mut cursor = POINT::default();
    unsafe { GetCursorPos(&mut cursor) };
    let monitor = unsafe { MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST) };
    let mut info = MONITORINFO {
        cbSize: size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    if unsafe { GetMonitorInfoW(monitor, &mut info) } != 0 {
        let work = info.rcWork;
        POINT {
            x: work.left + ((work.right - work.left - width) / 2).max(0),
            y: work.top + ((work.bottom - work.top - height) / 2).max(0),
        }
    } else {
        POINT::default()
    }
}

/// Settings runs per-monitor DPI aware, so every 96-DPI unit must be converted
/// before reaching Win32 controls. The cursor monitor decides the initial size.
fn scale_at_cursor() -> i32 {
    let mut cursor = POINT::default();
    unsafe { GetCursorPos(&mut cursor) };
    let monitor = unsafe { MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST) };
    let (mut x, mut y) = (96, 96);
    if unsafe { GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut x, &mut y) } >= 0 && x >= 96 {
        x as i32
    } else {
        96
    }
}

fn scaled(units: i32, dpi: i32) -> i32 {
    (units * dpi.max(96) + 48) / 96
}

unsafe fn set_dwm(hwnd: HWND, attribute: i32, value: i32) {
    unsafe {
        DwmSetWindowAttribute(
            hwnd,
            attribute as u32,
            ptr::from_ref(&value).cast(),
            size_of::<i32>() as u32,
        )
    };
}

const fn rgb(red: u8, green: u8, blue: u8) -> u32 {
    red as u32 | ((green as u32) << 8) | ((blue as u32) << 16)
}

fn paint_shell(hwnd: HWND) {
    let mut paint = PAINTSTRUCT::default();
    let dc = unsafe { BeginPaint(hwnd, &mut paint) };
    let mut window = RECT::default();
    unsafe { GetClientRect(hwnd, &mut window) };
    let maximized = unsafe { IsZoomed(hwnd) != 0 };
    let scale = unsafe { GetDpiForWindow(hwnd) }.max(96) as i32;
    SHELL_CACHE.with_borrow_mut(|cache| {
        let stale = cache.as_ref().is_none_or(|shell| {
            shell.width != window.right
                || shell.height != window.bottom
                || shell.scale != scale
                || shell.maximized != maximized
        });
        if stale {
            *cache = Some(build_shell(window.right, window.bottom, scale, maximized));
        }
        if let Some(shell) = cache.as_ref() {
            // The whole frame is composed once per size; a page switch only blits it.
            unsafe {
                BitBlt(
                    dc,
                    0,
                    0,
                    window.right,
                    window.bottom,
                    shell.dc,
                    0,
                    0,
                    SRCCOPY,
                )
            };
        }
    });
    unsafe { EndPaint(hwnd, &paint) };
}

struct ShellCache {
    width: i32,
    height: i32,
    scale: i32,
    maximized: bool,
    dc: HDC,
    bitmap: HBITMAP,
    previous: HGDIOBJ,
}

impl Drop for ShellCache {
    fn drop(&mut self) {
        unsafe {
            SelectObject(self.dc, self.previous);
            DeleteObject(self.bitmap);
            DeleteDC(self.dc);
        }
    }
}

thread_local! {
    static SHELL_CACHE: RefCell<Option<ShellCache>> = const { RefCell::new(None) };
}

fn build_shell(width: i32, height: i32, scale: i32, maximized: bool) -> ShellCache {
    unsafe {
        let screen = GetDC(ptr::null_mut());
        let dc = CreateCompatibleDC(screen);
        let bitmap = CreateCompatibleBitmap(screen, width.max(1), height.max(1));
        ReleaseDC(ptr::null_mut(), screen);
        let previous = SelectObject(dc, bitmap);
        draw_shell(
            dc,
            RECT {
                left: 0,
                top: 0,
                right: width,
                bottom: height,
            },
            maximized,
            scale,
        );
        ShellCache {
            width,
            height,
            scale,
            maximized,
            dc,
            bitmap,
            previous,
        }
    }
}

fn draw_shell(dc: HDC, window: RECT, maximized: bool, scale: i32) {
    let pad = if maximized {
        0
    } else {
        scaled(EDGE_PAD, scale)
    };
    let radius = if maximized { 0 } else { scaled(RADIUS, scale) };
    let body = RECT {
        left: pad,
        top: pad,
        right: window.right - pad,
        bottom: window.bottom - pad,
    };
    if body.right > body.left && body.bottom > body.top {
        // Clear only the transparent margin. Flooding the whole client with the
        // color key would composite a fully transparent frame before the glass
        // lands, which flashes on every page switch; margin pixels are already
        // transparent, so repainting them is invisible.
        for margin in [
            RECT {
                left: 0,
                top: 0,
                right: window.right,
                bottom: body.top,
            },
            RECT {
                left: 0,
                top: body.bottom,
                right: window.right,
                bottom: window.bottom,
            },
            RECT {
                left: 0,
                top: body.top,
                right: body.left,
                bottom: body.bottom,
            },
            RECT {
                left: body.right,
                top: body.top,
                right: window.right,
                bottom: body.bottom,
            },
        ] {
            fill(dc, &margin, COLOR_KEY);
        }
        // The glass is clipped to a rounded region, so the four corner squares
        // inside the body stay untouched unless they are cleared to the key;
        // otherwise they keep stale/black pixels.
        let corner = radius.min((body.right - body.left) / 2);
        for square in [
            RECT {
                left: body.left,
                top: body.top,
                right: body.left + corner,
                bottom: body.top + corner,
            },
            RECT {
                left: body.right - corner,
                top: body.top,
                right: body.right,
                bottom: body.top + corner,
            },
            RECT {
                left: body.left,
                top: body.bottom - corner,
                right: body.left + corner,
                bottom: body.bottom,
            },
            RECT {
                left: body.right - corner,
                top: body.bottom - corner,
                right: body.right,
                bottom: body.bottom,
            },
        ] {
            fill(dc, &square, COLOR_KEY);
        }
        let clip = unsafe {
            CreateRoundRectRgn(
                body.left,
                body.top,
                body.right + 1,
                body.bottom + 1,
                radius * 2,
                radius * 2,
            )
        };
        let saved = unsafe { SaveDC(dc) };
        unsafe {
            ExtSelectClipRgn(dc, clip, RGN_AND);
            DeleteObject(clip);
        }
        paint_glass(dc, body);
        unsafe { RestoreDC(dc, saved) };
        // The upper edge catches more light than the sides and bottom of the glass.
        for band in 0..16 {
            let top = body.top + (body.bottom - body.top) * band / 16;
            let bottom = body.top + (body.bottom - body.top) * (band + 1) / 16;
            let level = if band == 0 {
                249
            } else {
                (204 - band * 10).max(91) as u8
            };
            let saved = unsafe { SaveDC(dc) };
            unsafe { IntersectClipRect(dc, body.left, top, body.right + 1, bottom + 1) };
            outline(dc, &body, radius, rgb(level, level, level));
            unsafe { RestoreDC(dc, saved) };
        }
        let inset = RECT {
            left: body.left + 1,
            top: body.top + 1,
            right: body.right - 1,
            bottom: body.bottom - 1,
        };
        outline(dc, &inset, (radius - 1).max(0), rgb(56, 59, 64));
        let footer = body.bottom - scaled(FOOTER_HEIGHT, scale);
        stroke(
            dc,
            &[[body.left + 1, footer], [body.right - 1, footer]],
            SEPARATOR,
        );
    } else {
        fill(dc, &window, COLOR_KEY);
    }
}

fn paint_glass(dc: HDC, body: RECT) {
    let (width, height) = (body.right - body.left, body.bottom - body.top);
    if width <= 0 || height <= 0 {
        return;
    }
    GLASS_PIXELS.with_borrow_mut(|cache| {
        if cache.0 != width || cache.1 != height {
            cache.0 = width;
            cache.1 = height;
            cache.2.clear();
            cache.2.reserve(width as usize * height as usize);
            for y in 0..height {
                for x in 0..width {
                    cache.2.push(glass_pixel(x, y, width, height));
                }
            }
        }
        let mut info = BITMAPINFO::default();
        info.bmiHeader.biSize = size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = width;
        info.bmiHeader.biHeight = -height;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        // Controls use the same cached surface translated into their client coordinates.
        unsafe {
            StretchDIBits(
                dc,
                body.left,
                body.top,
                width,
                height,
                0,
                0,
                width,
                height,
                cache.2.as_ptr().cast(),
                &info,
                DIB_RGB_COLORS,
                SRCCOPY,
            )
        };
    });
}

thread_local! {
    static GLASS_PIXELS: RefCell<(i32, i32, Vec<u32>)> = const { RefCell::new((0, 0, Vec::new())) };
}

fn glass_pixel(x: i32, y: i32, width: i32, height: i32) -> u32 {
    let f = y as f32 / height as f32;
    let radius = width as f32 * 0.62;
    let light = (1.0 - (x as f32 - width as f32 * 0.18).hypot(y as f32) / radius).max(0.0);
    let hash =
        (x as u32).wrapping_mul(0x045d_9f3b).rotate_right(9) ^ (y as u32).wrapping_mul(0x9e37_79b9);
    let grain = ((hash ^ (hash >> 16)) % 11) as f32 - 5.0;
    let channel =
        |offset: f32, tint: f32| (53.0 - 26.0 * f + offset + light * tint + grain).round() as u8;
    u32::from_le_bytes([channel(2.0, 29.0), channel(0.0, 16.0), channel(0.0, 5.0), 0])
}

const DEFAULT_FAMILY: &str = "Segoe UI Variable";

fn installed_fonts() -> Vec<String> {
    let mut family_names: Vec<String> = Vec::new();
    let query = LOGFONTW {
        lfCharSet: DEFAULT_CHARSET,
        ..Default::default()
    };
    let dc = unsafe { GetDC(ptr::null_mut()) };
    if !dc.is_null() {
        unsafe {
            EnumFontFamiliesExW(
                dc,
                &query,
                Some(add_font),
                ptr::from_mut(&mut family_names) as LPARAM,
                0,
            );
            ReleaseDC(ptr::null_mut(), dc);
        }
    }
    if !family_names.iter().any(|name| name == DEFAULT_FAMILY) {
        family_names.push(DEFAULT_FAMILY.into());
    }
    family_names.sort_by_cached_key(|name| name.to_lowercase());
    family_names.dedup();
    family_names
}

unsafe extern "system" fn add_font(
    font: *const LOGFONTW,
    _: *const TEXTMETRICW,
    font_type: u32,
    data: LPARAM,
) -> i32 {
    if font_type & RASTER_FONTTYPE == 0 {
        let units = &unsafe { &*font }.lfFaceName;
        let length = units
            .iter()
            .position(|&unit| unit == 0)
            .unwrap_or(units.len());
        let name = String::from_utf16_lossy(&units[..length]);
        if !name.is_empty() && !name.starts_with('@') {
            unsafe { &mut *(data as *mut Vec<String>) }.push(name);
        }
    }
    1
}

fn short(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_owned();
    }
    text.chars()
        .take(limit.saturating_sub(1))
        .chain(['…'])
        .collect()
}

fn usable_font<'a>(families: &[String], configured: &'a str) -> &'a str {
    if families.iter().any(|family| family == configured) {
        configured
    } else {
        DEFAULT_FAMILY
    }
}

fn create_fonts(family: &str, scale: i32) -> [HFONT; 4] {
    let family = wide(if family.is_empty() {
        "Segoe UI Variable"
    } else {
        family
    });
    [(13, 400), (16, 600), (30, 700), (13, 600)].map(|(size, weight)| unsafe {
        CreateFontW(
            -scaled(size, scale),
            0,
            0,
            0,
            weight,
            0,
            0,
            0,
            DEFAULT_CHARSET as u32,
            OUT_DEFAULT_PRECIS as u32,
            CLIP_DEFAULT_PRECIS as u32,
            CLEARTYPE_QUALITY as u32,
            DEFAULT_PITCH as u32,
            family.as_ptr(),
        )
    })
}

fn free_fonts(fonts: [HFONT; 4]) {
    for font in fonts {
        if !font.is_null() {
            unsafe { DeleteObject(font) };
        }
    }
}

fn apply_font(hwnd: HWND, kind: Kind, fonts: &[HFONT; 4]) {
    let font = fonts[match kind {
        Kind::Column => 1,
        Kind::Heading => 2,
        Kind::Button | Kind::Current | Kind::Value => 3,
        _ => 0,
    }];
    let font = if font.is_null() {
        unsafe { GetStockObject(DEFAULT_GUI_FONT) }
    } else {
        font
    };
    send(hwnd, WM_SETFONT, font as WPARAM, 1);
}

fn fill(dc: HDC, rect: &RECT, color: u32) {
    unsafe {
        SetDCBrushColor(dc, color);
        FillRect(dc, rect, GetStockObject(DC_BRUSH));
    }
}

fn round(dc: HDC, rect: &RECT, radius: i32, border: u32, background: Option<u32>) {
    let diameter = radius * 2;
    unsafe {
        let pen = SelectObject(dc, GetStockObject(DC_PEN));
        let brush = SelectObject(
            dc,
            GetStockObject(if background.is_some() {
                DC_BRUSH
            } else {
                NULL_BRUSH
            }),
        );
        SetDCPenColor(dc, border);
        if let Some(color) = background {
            SetDCBrushColor(dc, color);
        }
        RoundRect(
            dc,
            rect.left,
            rect.top,
            rect.right,
            rect.bottom,
            diameter,
            diameter,
        );
        SelectObject(dc, brush);
        SelectObject(dc, pen);
    }
}

fn outline(dc: HDC, rect: &RECT, radius: i32, color: u32) {
    round(dc, rect, radius, color, None);
}

fn stroke(dc: HDC, points: &[[i32; 2]], color: u32) {
    let native: Vec<_> = points.iter().map(|&[x, y]| POINT { x, y }).collect();
    unsafe {
        let pen = SelectObject(dc, GetStockObject(DC_PEN));
        SetDCPenColor(dc, color);
        Polyline(dc, native.as_ptr(), native.len() as i32);
        SelectObject(dc, pen);
    }
}

fn draw_text(hwnd: HWND, dc: HDC, mut rect: RECT, value: &str, color: u32, format: u32) {
    let value = wide(value);
    let font = send(hwnd, WM_GETFONT, 0, 0) as HFONT;
    unsafe {
        let previous = if font.is_null() {
            ptr::null_mut()
        } else {
            SelectObject(dc, font)
        };
        SetBkMode(dc, TRANSPARENT as i32);
        SetTextColor(dc, color);
        DrawTextW(
            dc,
            value.as_ptr(),
            (value.len() - 1) as i32,
            &mut rect,
            format | DT_SINGLELINE | DT_VCENTER | DT_NOPREFIX | DT_END_ELLIPSIS,
        );
        if !previous.is_null() {
            SelectObject(dc, previous);
        }
    }
}

fn cursor_over(hwnd: HWND) -> bool {
    let mut cursor = POINT::default();
    unsafe { GetCursorPos(&mut cursor) != 0 && WindowFromPoint(cursor) == hwnd }
}

pub(crate) fn paint_embedded_backdrop(hwnd: HWND, dc: HDC) {
    let parent = unsafe { GetAncestor(hwnd, GA_ROOT) };
    let mut client = RECT::default();
    let mut child = RECT::default();
    let mut origin = POINT::default();
    unsafe {
        GetClientRect(hwnd, &mut child);
        GetClientRect(parent, &mut client);
        ClientToScreen(hwnd, &mut origin);
        ScreenToClient(parent, &mut origin);
    }
    let pad = if unsafe { IsZoomed(parent) } != 0 {
        0
    } else {
        EDGE_PAD
    };
    // Match the parent's light direction across every child HWND.
    let saved = unsafe { SaveDC(dc) };
    unsafe { IntersectClipRect(dc, child.left, child.top, child.right, child.bottom) };
    paint_glass(
        dc,
        RECT {
            left: pad - origin.x,
            top: pad - origin.y,
            right: client.right - pad - origin.x,
            bottom: client.bottom - pad - origin.y,
        },
    );
    unsafe { RestoreDC(dc, saved) };
}

fn paint_item(parent: HWND, item: &DRAWITEMSTRUCT) {
    let dc = item.hDC;
    let hwnd = item.hwndItem;
    let rect = item.rcItem;
    if item.CtlType == ODT_STATIC {
        let kind = (unsafe { GetPropW(hwnd, CONTROL_KIND) } as usize).saturating_sub(1);
        paint_embedded_backdrop(hwnd, dc);
        paint_label(hwnd, dc, rect, kind);
        return;
    }
    if item.CtlType == ODT_COMBOBOX {
        if item.itemState & ODS_COMBOBOXEDIT == 0 {
            let selected = item.itemState & ODS_SELECTED != 0;
            fill(
                dc,
                &rect,
                if selected {
                    rgb(37, 78, 118)
                } else {
                    CONTROL_BG
                },
            );
            let mut label = rect;
            label.left += 9;
            label.right -= 7;
            draw_text(
                hwnd,
                dc,
                label,
                &combo_text(hwnd, item.itemID as usize),
                PRIMARY,
                DT_LEFT,
            );
        }
        return;
    }
    if item.CtlType != ODT_BUTTON {
        return;
    }
    paint_embedded_backdrop(hwnd, dc);
    let pressed = item.itemState & ODS_SELECTED != 0;
    let disabled = item.itemState & ODS_DISABLED != 0;
    let focused = item.itemState & ODS_FOCUS != 0;
    let hovering = !disabled && cursor_over(hwnd);
    let id = item.CtlID as u16;
    let chrome = [Field::Minimize, Field::Maximize, Field::TitleClose]
        .into_iter()
        .find(|&field| field as u16 == id);
    if let Some(chrome) = chrome {
        if hovering || pressed {
            let color = if chrome == Field::TitleClose {
                rgb(219, 58, 53)
            } else {
                rgb(69, 71, 77)
            };
            round(dc, &rect, 10, color, Some(color));
        } else if focused {
            outline(dc, &rect, 10, HOVER_ACCENT);
        }
        paint_chrome(parent, dc, rect, chrome);
        return;
    }
    let is_tab = (Field::TabLocal as u16..=Field::TabRolling as u16).contains(&id);
    let active_tab =
        is_tab && unsafe { GetPropW(parent, SELECTED_TAB) } as usize == usize::from(id);
    let foreground = if disabled || (is_tab && !active_tab && !hovering) {
        MUTED
    } else if active_tab {
        rgb(220, 238, 255)
    } else {
        PRIMARY
    };
    if active_tab {
        let border = if focused || hovering {
            HOVER_ACCENT
        } else {
            rgb(68, 103, 151)
        };
        round(dc, &rect, 10, border, Some(rgb(43, 69, 101)));
    } else if !is_tab || hovering || focused {
        let border = if focused {
            HOVER_ACCENT
        } else if hovering {
            rgb(91, 91, 95)
        } else {
            CONTROL_BORDER
        };
        let fill = if pressed || disabled {
            rgb(45, 45, 48)
        } else if hovering {
            rgb(62, 62, 66)
        } else {
            rgb(55, 55, 59)
        };
        round(dc, &rect, 10, border, Some(fill));
    }
    draw_text(hwnd, dc, rect, &text(hwnd), foreground, DT_CENTER);
}

fn paint_label(hwnd: HWND, dc: HDC, mut rect: RECT, kind: usize) {
    if kind == Kind::Value as usize {
        round(dc, &rect, 8, rgb(36, 67, 96), Some(rgb(36, 67, 96)));
        draw_text(hwnd, dc, rect, &text(hwnd), rgb(185, 220, 255), DT_CENTER);
    } else if kind == Kind::DisabledField as usize {
        round(dc, &rect, 9, rgb(57, 57, 61), Some(rgb(46, 46, 48)));
        rect.left += 10;
        draw_text(hwnd, dc, rect, &text(hwnd), MUTED, DT_LEFT);
    } else {
        let color = if kind == Kind::Muted as usize {
            MUTED
        } else if kind == Kind::Current as usize {
            ACCENT
        } else {
            PRIMARY
        };
        draw_text(hwnd, dc, rect, &text(hwnd), color, DT_LEFT);
        if kind == Kind::Column as usize {
            let mut origin = POINT::default();
            unsafe {
                ClientToScreen(hwnd, &mut origin);
                ScreenToClient(GetParent(hwnd), &mut origin);
            }
            if origin.y >= 170 {
                stroke(
                    dc,
                    &[[0, rect.bottom - 1], [rect.right, rect.bottom - 1]],
                    rgb(57, 83, 111),
                );
            }
        }
    }
}

fn paint_chrome(parent: HWND, dc: HDC, rect: RECT, button: Field) {
    let x = (rect.left + rect.right) / 2;
    let y = (rect.top + rect.bottom) / 2;
    match button {
        Field::Minimize => stroke(dc, &[[x - 4, y + 4], [x + 5, y + 4]], PRIMARY),
        Field::Maximize => {
            let offset = unsafe { IsZoomed(parent) };
            if offset != 0 {
                stroke(
                    dc,
                    &[[x - 1, y - 5], [x + 5, y - 5], [x + 5, y + 1]],
                    PRIMARY,
                );
            }
            outline(
                dc,
                &RECT {
                    left: x - 4,
                    right: x + 5 - offset,
                    top: y - 4 + offset,
                    bottom: y + 5,
                },
                0,
                PRIMARY,
            );
        }
        Field::TitleClose => {
            stroke(dc, &[[x - 4, y - 4], [x + 5, y + 5]], PRIMARY);
            stroke(dc, &[[x + 4, y - 4], [x - 5, y + 5]], PRIMARY);
        }
        _ => {}
    }
}

fn combo_text(hwnd: HWND, index: usize) -> String {
    let len = send(hwnd, CB_GETLBTEXTLEN, index, 0);
    if len < 0 {
        return String::new();
    }
    let mut units = vec![0; len as usize + 1];
    let written = send(hwnd, CB_GETLBTEXT, index, units.as_mut_ptr() as LPARAM);
    String::from_utf16_lossy(&units[..written.max(0) as usize])
}

unsafe extern "system" fn style_control(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    kind: usize,
) -> LRESULT {
    let combo = kind == Kind::Combo as usize;
    let slider = kind == Kind::Slider as usize;
    let check = kind == Kind::Check as usize;
    let edit = [Kind::Edit, Kind::Password, Kind::Number]
        .into_iter()
        .any(|control| kind == control as usize);
    match message {
        // Every custom-painted control supplies its own backdrop, so the class
        // brush (light in the system colours) must never erase it first. The
        // flash showed on slider drags and button hover because those repaints
        // arrive with the erase flag set by the control itself. Edits keep the
        // default erase: their brush is already the dark WM_CTLCOLOREDIT one.
        WM_ERASEBKGND if !edit => return 1,
        WM_MOUSEMOVE => {
            let mut tracking = TRACKMOUSEEVENT {
                cbSize: size_of::<TRACKMOUSEEVENT>() as u32,
                dwFlags: TME_LEAVE,
                hwndTrack: hwnd,
                dwHoverTime: 0,
            };
            unsafe {
                TrackMouseEvent(&mut tracking);
                InvalidateRect(hwnd, ptr::null(), 0);
            }
        }
        WM_MOUSEWHEEL if slider || (combo && send(hwnd, CB_GETDROPPEDSTATE, 0, 0) == 0) => {
            return 0;
        }
        WM_PAINT | WM_PRINTCLIENT if slider || combo || check => {
            let mut paint = PAINTSTRUCT::default();
            let painting = message == WM_PAINT;
            let dc = if painting {
                unsafe { BeginPaint(hwnd, &mut paint) }
            } else {
                wparam as HDC
            };
            let mut rect = RECT::default();
            unsafe { GetClientRect(hwnd, &mut rect) };
            paint_embedded_backdrop(hwnd, dc);
            if combo {
                paint_combo(hwnd, dc, rect);
            } else if check {
                paint_check(hwnd, dc, rect);
            } else {
                paint_slider(hwnd, dc, rect);
            }
            if painting {
                unsafe { EndPaint(hwnd, &paint) };
            }
            return 0;
        }
        _ => {}
    }
    let result = unsafe { DefSubclassProc(hwnd, message, wparam, lparam) };
    if message == WM_PAINT && edit {
        // The centered text lives in a shrunken client, so outline the whole
        // field window; GetClientRect would only cover the text band.
        let dc = unsafe { GetWindowDC(hwnd) };
        let mut size = RECT::default();
        unsafe { GetWindowRect(hwnd, &mut size) };
        let rect = RECT {
            left: 0,
            top: 0,
            right: size.right - size.left,
            bottom: size.bottom - size.top,
        };
        let border = if unsafe { GetFocus() } == hwnd {
            HOVER_ACCENT
        } else if cursor_over(hwnd) {
            rgb(89, 89, 93)
        } else {
            CONTROL_BORDER
        };
        outline(dc, &rect, 9, border);
        unsafe { ReleaseDC(hwnd, dc) };
    }
    if matches!(
        message,
        WM_SETFOCUS
            | WM_KILLFOCUS
            | WM_ENABLE
            | WM_LBUTTONDOWN
            | WM_LBUTTONUP
            | BM_SETCHECK
            | WM_MOUSELEAVE
            | WM_SETTEXT
    ) {
        // Skip the class-brush erase: it flashes light before the custom paint runs.
        unsafe { InvalidateRect(hwnd, ptr::null(), 0) };
    }
    result
}

fn paint_combo(hwnd: HWND, dc: HDC, rect: RECT) {
    let focused = unsafe { GetFocus() } == hwnd;
    let expanded = send(hwnd, CB_GETDROPPEDSTATE, 0, 0) != 0;
    let border = if focused || expanded {
        HOVER_ACCENT
    } else if cursor_over(hwnd) {
        rgb(89, 89, 93)
    } else {
        CONTROL_BORDER
    };
    round(dc, &rect, 9, border, Some(CONTROL_BG));
    if let Some(index) = choice(hwnd) {
        let mut label = rect;
        label.left += 10;
        label.right -= 28;
        draw_text(hwnd, dc, label, &combo_text(hwnd, index), PRIMARY, DT_LEFT);
    }
    let x = rect.right - 15;
    let y = (rect.top + rect.bottom) / 2;
    stroke(dc, &[[x - 4, y - 2], [x, y + 2], [x + 5, y - 3]], MUTED);
}

fn paint_check(hwnd: HWND, dc: HDC, rect: RECT) {
    let selected = checked(hwnd);
    let disabled = unsafe { IsWindowEnabled(hwnd) } == 0;
    let focus = !disabled && unsafe { GetFocus() } == hwnd;
    let top = (rect.bottom - 14) / 2;
    let check = RECT {
        left: 2,
        top,
        right: 16,
        bottom: top + 14,
    };
    let color = if selected && !disabled {
        if cursor_over(hwnd) {
            HOVER_ACCENT
        } else {
            ACCENT
        }
    } else {
        CONTROL_BG
    };
    round(
        dc,
        &check,
        4,
        if selected && !disabled {
            color
        } else {
            CONTROL_BORDER
        },
        Some(color),
    );
    if selected {
        let color = if disabled { MUTED } else { PRIMARY };
        stroke(dc, &[[5, top + 7], [8, top + 10], [13, top + 4]], color);
        stroke(dc, &[[5, top + 8], [8, top + 11], [13, top + 5]], color);
    }
    if focus {
        outline(
            dc,
            &RECT {
                left: 1,
                top: top - 1,
                right: 17,
                bottom: top + 15,
            },
            5,
            HOVER_ACCENT,
        );
    }
    let mut label = rect;
    label.left += 24;
    draw_text(
        hwnd,
        dc,
        label,
        &text(hwnd),
        if disabled { MUTED } else { PRIMARY },
        DT_LEFT,
    );
}

fn paint_slider(hwnd: HWND, dc: HDC, rect: RECT) {
    let mut channel = RECT::default();
    let mut thumb = RECT::default();
    send(
        hwnd,
        TBM_GETCHANNELRECT,
        0,
        ptr::from_mut(&mut channel) as LPARAM,
    );
    send(
        hwnd,
        TBM_GETTHUMBRECT,
        0,
        ptr::from_mut(&mut thumb) as LPARAM,
    );
    let y = (rect.top + rect.bottom) / 2;
    let x = (thumb.left + thumb.right) / 2;
    let track = RECT {
        left: channel.left,
        top: y - 3,
        right: channel.right,
        bottom: y + 3,
    };
    let progress = RECT {
        right: x.max(channel.left + 1),
        ..track
    };
    round(dc, &track, 3, CONTROL_BORDER, Some(CONTROL_BORDER));
    round(dc, &progress, 3, ACCENT, Some(ACCENT));
    let thumb = RECT {
        left: x - 8,
        top: y - 8,
        right: x + 8,
        bottom: y + 8,
    };
    round(
        dc,
        &thumb,
        8,
        if unsafe { GetFocus() } == hwnd {
            HOVER_ACCENT
        } else {
            rgb(194, 211, 229)
        },
        Some(PRIMARY),
    );
}

fn wide(value: &str) -> Vec<u16> {
    value.encode_utf16().chain([0]).collect()
}

fn text(hwnd: HWND) -> String {
    let length = unsafe { GetWindowTextLengthW(hwnd) };
    let mut units = vec![0; (length + 1) as usize];
    let length = unsafe { GetWindowTextW(hwnd, units.as_mut_ptr(), units.len() as i32) };
    String::from_utf16_lossy(&units[..length as usize])
}

fn set_text(hwnd: HWND, value: &str) {
    let units = wide(value);
    unsafe { SetWindowTextW(hwnd, units.as_ptr()) };
}

fn send(hwnd: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe { SendMessageW(hwnd, message, wparam, lparam) }
}

fn checked(hwnd: HWND) -> bool {
    send(hwnd, BM_GETCHECK, 0, 0) != 0
}

fn set_slider(hwnd: HWND, position: i32) {
    send(hwnd, TBM_SETPOS, 1, position as LPARAM);
}

fn slider_position(value: f64, min: f64) -> i32 {
    ((value - min) * 20.0).round() as i32
}

fn slider_value(position: LRESULT, min: f64) -> f64 {
    (position as f64 * 5.0 + min * 100.0) / 100.0
}

fn choice(hwnd: HWND) -> Option<usize> {
    usize::try_from(send(hwnd, CB_GETCURSEL, 0, 0)).ok()
}

fn fill_choices(hwnd: HWND, choices: impl IntoIterator<Item = impl AsRef<str>>, selected: usize) {
    send(hwnd, CB_RESETCONTENT, 0, 0);
    for label in choices {
        let label = wide(label.as_ref());
        send(hwnd, CB_ADDSTRING, 0, label.as_ptr() as LPARAM);
    }
    send(hwnd, CB_SETCURSEL, selected, 0);
}

fn changed(text: String, saved: &str) -> Option<String> {
    (text != saved).then_some(text)
}

fn backends(provider: Provider) -> &'static [&'static str] {
    match provider {
        Provider::Cartesia => &[CARTESIA_BYTES, CARTESIA_REALTIME],
        Provider::Volcengine => &[VOLCENGINE_STREAMING],
    }
}

fn selected_voice(voices: &[Voice], selected: Option<&str>) -> usize {
    voices
        .iter()
        .position(|voice| Some(voice.id.as_str()) == selected)
        .map_or(0, |index| index + 1)
}

fn voice_labels(voices: &[Voice]) -> Vec<String> {
    let mut names = HashSet::new();
    let labels = voices.iter().map(|voice| {
        if names.insert(&voice.name) {
            voice.name.clone()
        } else {
            format!(
                "{} ({})",
                voice.name,
                voice.id.chars().take(8).collect::<String>()
            )
        }
    });
    std::iter::once(EMPTY_VOICE.to_owned())
        .chain(labels)
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn voice_choices_keep_configured_identity_with_duplicate_labels() {
        let voices = vec![
            Voice {
                id: "first".into(),
                name: "说话".into(),
            },
            Voice {
                id: "second-002".into(),
                name: "说话".into(),
            },
        ];
        assert_eq!(voice_labels(&voices), ["无", "说话", "说话 (second-0)"]);
        assert_eq!(selected_voice(&voices, Some("second-002")), 2);
        assert_eq!(selected_voice(&voices, Some("missing")), 0);
    }

    #[test]
    fn slider_positions_preserve_the_configured_ranges() {
        assert_eq!(slider_position(0.5, 0.5), 0);
        assert_eq!(slider_position(2.0, 0.5), 30);
        assert_eq!(slider_position(1.0, 0.30), 14);
        assert_eq!(slider_value(2, 0.30), 0.4);
    }

    #[test]
    fn glass_shell_keeps_eight_way_resize_and_title_drag() {
        assert_eq!(
            shell_hit(12, 12, 1120, 820, false, 96),
            HTTOPLEFT as LRESULT
        );
        assert_eq!(
            shell_hit(1110, 420, 1120, 820, false, 96),
            HTRIGHT as LRESULT
        );
        assert_eq!(
            shell_hit(600, 810, 1120, 820, false, 96),
            HTBOTTOM as LRESULT
        );
        assert_eq!(
            shell_hit(600, 35, 1120, 820, false, 96),
            HTCAPTION as LRESULT
        );
        assert_eq!(
            shell_hit(600, 160, 1120, 820, false, 96),
            HTCLIENT as LRESULT
        );
        assert_eq!(shell_hit(0, 200, 1120, 820, true, 96), HTCLIENT as LRESULT);
    }

    #[test]
    fn dpi_scale_matches_logical_units() {
        assert_eq!(scaled(1120, 96), 1120);
        assert_eq!(scaled(1120, 120), 1400);
        assert_eq!(scaled(13, 120), 16);
        // Resize hot zones follow the same factor as the rendered shell.
        assert_eq!(
            shell_hit(19, 420, 1400, 1025, false, 120),
            HTLEFT as LRESULT
        );
        assert_eq!(
            shell_hit(200, 60, 1400, 1025, false, 120),
            HTCAPTION as LRESULT
        );
    }
}
