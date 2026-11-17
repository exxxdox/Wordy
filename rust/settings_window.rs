use std::{collections::HashSet, io, mem::size_of, ptr};

use windows_sys::{
    Win32::{
        Foundation::{
            ERROR_CLASS_ALREADY_EXISTS, GetLastError, HWND, LPARAM, LRESULT, RECT, WPARAM,
        },
        Graphics::Gdi::{COLOR_WINDOW, DEFAULT_GUI_FONT, GetStockObject},
        System::LibraryLoader::GetModuleHandleW,
        UI::{
            Controls::{
                EM_SETLIMITTEXT, ICC_BAR_CLASSES, ICC_TAB_CLASSES, INITCOMMONCONTROLSEX,
                InitCommonControlsEx, NMHDR, TBM_SETPOS, TBM_SETRANGEMAX, TCIF_TEXT, TCITEMW,
                TCM_GETCURSEL, TCM_INSERTITEMW, TCN_SELCHANGE, TRACKBAR_CLASSW, WC_TABCONTROLW,
            },
            Input::KeyboardAndMouse::{EnableWindow, GetFocus},
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
const CLIENT_WIDTH: i32 = 1088;
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
}

#[derive(Clone, Copy, PartialEq, Eq)]
#[repr(u16)]
enum Field {
    Tab = 100,
    Resize,
    Close,
    Hotkey,
    HoldKey,
    HoldKeyEnabled,
    Volume,
    VolumeValue,
    Placeholder,
    UiFont,
    Opacity,
    OpacityValue,
    FixedCenter,
    LogLevel,
    MainPlayback,
    Sidetone,
    Output,
    Mic,
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
    SaveVoice,
    DeleteVoice,
    Wheels,
    RollingHotkey,
    RollingSeconds,
    OpenLogs,
    Import,
    Export,
    Status,
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

const PAGES: [(Page, &str); 5] = [
    (Page::Local, "本地设置"),
    (Page::Audio, "音频设置"),
    (Page::Tts, "TTS 设置"),
    (Page::Wheels, "聊天轮盘"),
    (Page::Rolling, "后台录制"),
];

type Bounds = [i32; 4];

#[derive(Clone, Copy)]
enum Kind {
    Label,
    Button,
    Check,
    Edit,
    Password,
    Number,
    Combo,
    Slider,
    Tabs,
}

impl Kind {
    fn window(self) -> (PCWSTR, u32, u32) {
        match self {
            Self::Label => (w!("STATIC"), 0, 0),
            Self::Button => (w!("BUTTON"), WS_TABSTOP | BS_PUSHBUTTON as u32, 0),
            Self::Check => (w!("BUTTON"), WS_TABSTOP | BS_AUTOCHECKBOX as u32, 0),
            Self::Edit => (
                w!("EDIT"),
                WS_TABSTOP | ES_AUTOHSCROLL as u32,
                WS_EX_CLIENTEDGE,
            ),
            Self::Password => (
                w!("EDIT"),
                WS_TABSTOP | ES_AUTOHSCROLL as u32 | ES_PASSWORD as u32,
                WS_EX_CLIENTEDGE,
            ),
            Self::Number => (w!("EDIT"), WS_TABSTOP | ES_NUMBER as u32, WS_EX_CLIENTEDGE),
            Self::Combo => (
                w!("COMBOBOX"),
                WS_TABSTOP | WS_VSCROLL | CBS_DROPDOWNLIST as u32,
                0,
            ),
            Self::Slider => (TRACKBAR_CLASSW, WS_TABSTOP, 0),
            Self::Tabs => (WC_TABCONTROLW, WS_TABSTOP, 0),
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
    settings: Settings,
    outputs: Vec<AudioIdentity>,
    microphones: Vec<String>,
    cartesia_voices: Vec<Voice>,
    listen_state: Option<bool>,
    cable_available: bool,
    audio_devices_requested: bool,
    page: Page,
}

impl SettingsWindow {
    pub(crate) fn new(host: HWND, settings: &Settings) -> io::Result<Self> {
        let classes = INITCOMMONCONTROLSEX {
            dwSize: size_of::<INITCOMMONCONTROLSEX>() as u32,
            dwICC: ICC_TAB_CLASSES | ICC_BAR_CLASSES,
        };
        if unsafe { InitCommonControlsEx(&classes) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        let class = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: instance,
            hCursor: unsafe { LoadCursorW(ptr::null_mut(), IDC_ARROW) },
            hbrBackground: (COLOR_WINDOW as usize + 1) as _,
            lpszClassName: CLASS,
            ..Default::default()
        };
        if unsafe { RegisterClassW(&class) } == 0
            && unsafe { GetLastError() } != ERROR_CLASS_ALREADY_EXISTS
        {
            return Err(io::Error::last_os_error());
        }
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_CONTROLPARENT,
                CLASS,
                w!("Wordy 设置"),
                WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
                CW_USEDEFAULT,
                CW_USEDEFAULT,
                1120,
                820,
                host,
                ptr::null_mut(),
                instance,
                host.cast(),
            )
        };
        if hwnd.is_null() {
            return Err(io::Error::last_os_error());
        }
        let mut window = Self {
            hwnd,
            instance,
            widgets: Vec::new(),
            settings: settings.clone(),
            outputs: Vec::new(),
            microphones: Vec::new(),
            cartesia_voices: Vec::new(),
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
            ShowWindow(
                self.hwnd,
                if IsIconic(self.hwnd) != 0 {
                    SW_RESTORE
                } else {
                    SW_SHOW
                },
            );
            SetForegroundWindow(self.hwnd);
        }
    }

    pub fn handle_message(&self, message: &MSG) -> bool {
        unsafe { IsWindowVisible(self.hwnd) != 0 && IsDialogMessageW(self.hwnd, message) != 0 }
    }

    pub fn refresh(&mut self, settings: &Settings) {
        if settings.active_tts_provider != self.settings.active_tts_provider {
            set_text(self.control(Field::Key), "");
        }
        self.settings = settings.clone();
        for (field, binding) in [
            (Field::Hotkey, &settings.hotkey),
            (Field::HoldKey, &settings.hold_key),
            (Field::RollingHotkey, &settings.rolling_buffer_hotkey),
        ] {
            set_text(
                self.control(field),
                &format!("录制 · {}", crate::hotkey::display_hotkey(binding)),
            );
        }
        self.set_edit(
            Field::RollingSeconds,
            &format!("{}", settings.rolling_buffer_seconds.round()),
        );
        self.set_edit(Field::Placeholder, &settings.overlay_placeholder);
        self.set_edit(Field::UiFont, &settings.ui_font);
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

    pub fn set_listen_state(&mut self, state: Option<bool>) {
        self.listen_state = state;
        self.refresh_audio_status();
    }

    pub fn set_vb_cable_available(&mut self, available: bool) {
        self.cable_available = available;
        self.refresh_audio_status();
        self.show_page();
    }

    pub fn set_status(&self, status: &str) {
        set_text(self.control(Field::Status), status);
    }

    pub fn action(&mut self, action_id: u16) -> Option<SettingsAction> {
        use SettingsAction::*;
        if action_id == Field::Resize as u16 {
            self.layout();
            return None;
        }
        if action_id == Field::Close as u16 {
            set_text(self.control(Field::Key), "");
            return Some(Closed);
        }
        let field = self
            .widgets
            .iter()
            .filter_map(|widget| widget.field)
            .find(|&field| field as u16 == action_id)?;
        let control = self.control(field);
        let active = self.settings.active_tts_provider;
        match field {
            Field::Tab => {
                let current = send(control, TCM_GETCURSEL, 0, 0) as usize;
                self.page = PAGES.get(current).map_or(Page::Local, |(page, _)| *page);
                self.show_page();
                if self.page == Page::Audio && !self.audio_devices_requested {
                    self.audio_devices_requested = true;
                    return Some(RequestAudioDevices);
                }
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
            Field::UiFont => changed(text(control), &self.settings.ui_font).map(SetUiFont),
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
                    self.set_status("请输入 API Key。");
                    return None;
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
                    self.set_status("请输入 Speaker ID。");
                    return None;
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
        self.add(Some(Tab), None, Tabs, "", [14, 12, 1060, -72])?;
        let tabs = self.control(Tab);
        for (index, (_, caption)) in PAGES.iter().enumerate() {
            let mut caption = wide(caption);
            let item = TCITEMW {
                mask: TCIF_TEXT,
                pszText: caption.as_mut_ptr(),
                ..Default::default()
            };
            send(tabs, TCM_INSERTITEMW, index, ptr::from_ref(&item) as LPARAM);
        }

        // Every native control remains on the same top-level owner so focus
        // and keyboard navigation work across both columns of the active page.
        for (page, field, kind, caption, bounds) in [
            (Local, None, Label, "快捷键与播放", [36, 73, 470, 24]),
            (Local, None, Label, "输入框快捷键", [36, 113, 150, 24]),
            (Local, Some(Hotkey), Button, "", [202, 110, 280, 28]),
            (
                Local,
                Some(HoldKeyEnabled),
                Check,
                "播放时自动按住某键",
                [36, 156, 195, 26],
            ),
            (Local, Some(HoldKey), Button, "", [238, 154, 244, 28]),
            (Local, None, Label, "朗读音量", [36, 207, 160, 24]),
            (Local, Some(VolumeValue), Label, "", [410, 207, 80, 24]),
            (Local, Some(Volume), Slider, "", [36, 235, 446, 32]),
            (Local, None, Label, "界面与显示", [558, 73, 467, 24]),
            (Local, None, Label, "界面字体", [558, 113, 130, 24]),
            (Local, Some(UiFont), Edit, "", [710, 110, 312, 28]),
            (Local, None, Label, "输入框提示文字", [558, 156, 150, 24]),
            (Local, Some(Placeholder), Edit, "", [710, 153, 312, 28]),
            (
                Local,
                None,
                Label,
                "输入框背景不透明度",
                [558, 207, 180, 24],
            ),
            (Local, Some(OpacityValue), Label, "", [960, 207, 60, 24]),
            (Local, Some(Opacity), Slider, "", [558, 235, 464, 32]),
            (
                Local,
                Some(FixedCenter),
                Check,
                "输入框固定居中",
                [558, 283, 250, 29],
            ),
            (Local, None, Label, "日志等级", [558, 329, 150, 24]),
            (Local, Some(LogLevel), Combo, "", [710, 326, 312, 175]),
            (Audio, None, Label, "播放与返听", [36, 73, 470, 24]),
            (
                Audio,
                Some(MainPlayback),
                Check,
                "启用主播放输出",
                [36, 111, 300, 28],
            ),
            (Audio, Some(CableStatus), Label, "", [36, 152, 446, 24]),
            (
                Audio,
                Some(Download),
                Button,
                "下载 VB-CABLE",
                [36, 182, 160, 30],
            ),
            (Audio, Some(Sidetone), Check, "启用返听", [36, 232, 300, 30]),
            (Audio, None, Label, "返听播放设备", [36, 277, 150, 24]),
            (Audio, Some(Output), Combo, "", [36, 309, 446, 230]),
            (Audio, None, Label, "麦克风与侦听", [558, 73, 465, 24]),
            (
                Audio,
                Some(Listen),
                Check,
                "启用音频侦听",
                [558, 111, 350, 30],
            ),
            (Audio, None, Label, "侦听麦克风", [558, 152, 465, 24]),
            (Audio, Some(Mic), Combo, "", [558, 185, 466, 230]),
            (Audio, Some(MicStatus), Label, "", [558, 231, 466, 48]),
            (Tts, None, Label, "生成方式", [36, 73, 365, 24]),
            (Tts, None, Label, "TTS 服务商", [36, 113, 150, 24]),
            (Tts, Some(Provider), Combo, "", [36, 146, 400, 160]),
            (Tts, None, Label, "生成模式", [36, 195, 150, 24]),
            (Tts, Some(Backend), Combo, "", [36, 226, 400, 160]),
            (Tts, None, Label, "音色与密钥", [497, 73, 523, 24]),
            (Tts, None, Label, "API Key", [497, 113, 120, 24]),
            (Tts, Some(Key), Password, "", [618, 110, 404, 29]),
            (Tts, Some(KeyStatus), Label, "", [497, 157, 244, 24]),
            (Tts, Some(SaveKey), Button, "保存密钥", [767, 151, 120, 31]),
            (
                Tts,
                Some(DeleteKey),
                Button,
                "删除密钥",
                [902, 151, 120, 31],
            ),
            (Tts, None, Label, "音色", [497, 209, 120, 24]),
            (Tts, Some(Voices), Combo, "", [497, 243, 525, 230]),
            (
                Tts,
                Some(RefreshVoices),
                Button,
                "刷新音色",
                [497, 281, 140, 30],
            ),
            (Tts, None, Label, "Speaker ID", [497, 325, 120, 24]),
            (Tts, Some(VoiceId), Edit, "", [618, 322, 404, 28]),
            (Tts, None, Label, "名称", [497, 367, 120, 24]),
            (Tts, Some(VoiceName), Edit, "", [618, 364, 404, 28]),
            (
                Tts,
                Some(SaveVoice),
                Button,
                "添加音色",
                [767, 410, 120, 31],
            ),
            (
                Tts,
                Some(DeleteVoice),
                Button,
                "删除音色",
                [902, 410, 120, 31],
            ),
            (
                Page::Wheels,
                Some(Wheels),
                Button,
                "编辑聊天轮盘",
                [36, 73, 200, 36],
            ),
            (Rolling, None, Label, "后台录制快捷键", [36, 73, 180, 24]),
            (Rolling, Some(RollingHotkey), Button, "", [237, 70, 245, 28]),
            (Rolling, None, Label, "保留时长（秒）", [36, 121, 180, 24]),
            (
                Rolling,
                Some(RollingSeconds),
                Number,
                "",
                [237, 118, 120, 28],
            ),
        ] {
            self.add(field, Some(page), kind, caption, bounds)?;
        }
        for (field, kind, caption, bounds) in [
            (Status, Label, "", [20, -46, 558, 28]),
            (OpenLogs, Button, "查看日志", [620, -49, 127, 34]),
            (Export, Button, "导出备份", [766, -49, 144, 34]),
            (Import, Button, "导入备份", [929, -49, 144, 34]),
        ] {
            self.add(Some(field), None, kind, caption, bounds)?;
        }
        send(self.control(Volume), TBM_SETRANGEMAX, 1, 30);
        send(self.control(Opacity), TBM_SETRANGEMAX, 1, 14);
        send(self.control(RollingSeconds), EM_SETLIMITTEXT, 3, 0);
        send(self.control(Key), EM_SETLIMITTEXT, 1280, 0);
        unsafe {
            SetWindowPos(
                tabs,
                HWND_BOTTOM,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            );
        }
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
                bounds[0],
                bounds[1],
                bounds[2],
                bounds[3],
                self.hwnd,
                id as HMENU,
                self.instance,
                ptr::null(),
            )
        };
        if hwnd.is_null() {
            return Err(io::Error::last_os_error());
        }
        send(
            hwnd,
            WM_SETFONT,
            unsafe { GetStockObject(DEFAULT_GUI_FONT) } as WPARAM,
            0,
        );
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
            let top = if y < 0 { client.bottom + y } else { y };
            let height = if h < 0 { client.bottom + h } else { h };
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
    }

    fn show_page(&self) {
        let provider = self.settings.active_tts_provider;
        for widget in &self.widgets {
            let correct_page = widget.page.is_none_or(|page| page == self.page);
            let visible = match widget.field {
                Some(Field::Download) => !self.cable_available,
                Some(Field::RefreshVoices) => provider == Provider::Cartesia,
                Some(Field::VoiceId | Field::VoiceName | Field::SaveVoice | Field::DeleteVoice) => {
                    provider == Provider::Volcengine
                }
                // Speaker ID and its name labels sit beside Volcengine's two edits.
                None if widget.page == Some(Page::Tts)
                    && matches!(widget.kind, Kind::Label)
                    && widget.bounds[1] >= 325 =>
                {
                    provider == Provider::Volcengine
                }
                _ => true,
            };
            unsafe {
                ShowWindow(
                    widget.hwnd,
                    if correct_page && visible {
                        SW_SHOW
                    } else {
                        SW_HIDE
                    },
                )
            };
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
                "主输出：CABLE Input"
            } else {
                "未检测到 VB-CABLE"
            },
        );
        let listen = self
            .listen_state
            .unwrap_or(self.settings.audio_routing_enabled);
        self.check(Field::Listen, listen);
        unsafe { EnableWindow(self.control(Field::Listen), i32::from(self.cable_available)) };
        let status = match self.listen_state {
            Some(true) => "Windows 麦克风侦听已开启",
            Some(false) => "Windows 麦克风侦听未开启",
            None if self.settings.mic_input_device.is_some() => "无法读取 Windows 麦克风侦听状态",
            None => "未选择侦听麦克风",
        };
        set_text(self.control(Field::MicStatus), status);
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
        WM_CLOSE => {
            unsafe { ShowWindow(hwnd, SW_HIDE) };
            post(Field::Close as u16);
            return 0;
        }
        WM_SIZE => post(Field::Resize as u16),
        WM_GETMINMAXINFO => {
            let limits = unsafe { &mut *(lparam as *mut MINMAXINFO) };
            limits.ptMinTrackSize.x = 960;
            limits.ptMinTrackSize.y = 560;
        }
        WM_NOTIFY if lparam != 0 => {
            let notification = unsafe { &*(lparam as *const NMHDR) };
            if notification.idFrom == Field::Tab as usize && notification.code == TCN_SELCHANGE {
                post(Field::Tab as u16);
                return 0;
            }
        }
        WM_COMMAND if lparam != 0 => {
            let notification = ((wparam >> 16) & 0xffff) as u32;
            if [BN_CLICKED, CBN_SELCHANGE, EN_KILLFOCUS].contains(&notification) {
                post(wparam as u16);
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
}
