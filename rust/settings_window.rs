use std::{collections::HashSet, io, mem::size_of, ptr};

use windows_sys::{
    Win32::{
        Foundation::{
            ERROR_CLASS_ALREADY_EXISTS, GetLastError, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM,
        },
        Graphics::{
            Dwm::{DWMWA_CLOAK, DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_DONOTROUND},
            Gdi::*,
        },
        System::{LibraryLoader::GetModuleHandleW, SystemServices::SS_OWNERDRAW},
        UI::{
            Controls::{
                DRAWITEMSTRUCT, EM_SETLIMITTEXT, EM_SETMARGINS, ICC_BAR_CLASSES,
                INITCOMMONCONTROLSEX, InitCommonControlsEx, TBM_SETRANGEMAX, TRACKBAR_CLASSW,
            },
            HiDpi::{GetDpiForMonitor, GetDpiForWindow, MDT_EFFECTIVE_DPI},
            Input::KeyboardAndMouse::{EnableWindow, GetFocus, VK_ESCAPE},
            Shell::SetWindowSubclass,
            WindowsAndMessaging::*,
        },
    },
    core::{PCWSTR, w},
};

use crate::{
    audio::endpoint_index,
    config::{
        AudioIdentity, CARTESIA_BYTES, CARTESIA_REALTIME, LOG_LEVELS, Settings,
        VOLCENGINE_STREAMING, Voice,
    },
    credentials::{Provider, normalize_api_key_input},
    desktop::{set_dwm, wide},
    settings_backdrop::{COLOR_KEY, EDGE_PAD, FOOTER_HEIGHT, paint_shell, rgb, scaled},
    slider::{self, TBM_GETPOS},
};

mod actions;
mod build;
mod controls;
mod refresh;
mod shell;

use controls::{paint_item, style_control};
use shell::*;

pub const SETTINGS_ACTION_MESSAGE: u32 = WM_APP + 10;
const CLASS: PCWSTR = w!("Wordy.Rust.Settings");
const CLIENT_WIDTH: i32 = 1120;
const TITLE_HEIGHT: i32 = 44;
const SELECTED_TAB: PCWSTR = w!("Wordy.Settings.Tab");
const CONTROL_KIND: PCWSTR = w!("Wordy.Settings.Kind");
const PRIMARY: u32 = rgb(245, 245, 247);
const MUTED: u32 = rgb(152, 152, 157);
const CONTROL_BG: u32 = rgb(44, 44, 46);
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
        "聊天轮盘",
        "按住快捷键，移向槽位后松开播放；回到中心或按 Esc 取消。",
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
    #[allow(clippy::not_unsafe_ptr_arg_deref)]
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
}
impl Drop for SettingsWindow {
    fn drop(&mut self) {
        unsafe { DestroyWindow(self.hwnd) };
        free_fonts(self.fonts);
    }
}

#[cfg(test)]
mod tests {
    use super::{controls::combo_text, *};

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
    fn tts_voice_fields_share_aligned_edges() {
        let settings = SettingsWindow::new(ptr::null_mut(), &Settings::default()).unwrap();
        let bounds = |field| {
            let mut rect = RECT::default();
            unsafe { GetWindowRect(settings.control(field), &mut rect) };
            rect
        };
        let voice_id = bounds(Field::VoiceId);
        let name = bounds(Field::VoiceName);
        assert_eq!((voice_id.left, voice_id.right), (name.left, name.right));
        assert!(voice_id.bottom < name.top);
    }

    #[test]
    fn clicking_the_settings_sliders_jumps_to_the_clicked_value() {
        let mut settings = SettingsWindow::new(ptr::null_mut(), &Settings::default()).unwrap();
        for (field, position) in [(Field::Volume, 15), (Field::Opacity, 7)] {
            let slider = settings.control(field);
            let mut rect = RECT::default();
            unsafe { GetClientRect(slider, &mut rect) };
            let point = (rect.right as isize / 2) | ((rect.bottom as isize / 2) << 16);
            slider::set_position(slider, 0);
            send(slider, WM_LBUTTONDOWN, 1, point);
            send(slider, WM_LBUTTONUP, 0, point);
            assert_eq!(send(slider, TBM_GETPOS, 0, 0), position);
        }
        match settings.action(Field::Opacity as u16) {
            Some(SettingsAction::SetOpacity(value)) => assert_eq!(value, 0.65),
            _ => panic!("the opacity slider should emit its new value"),
        }
    }

    #[test]
    fn dragging_a_settings_slider_leaves_unrelated_controls_untouched() {
        let mut committed = Settings::default();
        let mut window = SettingsWindow::new(ptr::null_mut(), &committed).unwrap();
        let fonts = window.control(Field::UiFont);
        fill_choices(fonts, ["临时字体选项"], 0);
        slider::set_position(window.control(Field::Opacity), 5);
        let Some(SettingsAction::SetOpacity(value)) = window.action(Field::Opacity as u16) else {
            panic!("the opacity slider should report its value");
        };
        committed.overlay_opacity = value;
        window.sync_debounced_inputs(&committed);
        assert_eq!(text(window.control(Field::OpacityValue)), "55%");
        assert_eq!(combo_text(fonts, 0), "临时字体选项");
        assert!(window.action(Field::Opacity as u16).is_none());
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
