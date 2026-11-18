use std::mem;
use std::os::windows::ffi::OsStringExt;
use std::path::{Path, PathBuf};
use std::ptr;
use windows_sys::Win32::Foundation::{HWND, LPARAM, WPARAM};
use windows_sys::Win32::Graphics::Gdi::{
    CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS, COLOR_WINDOW, CreateFontW, DEFAULT_CHARSET,
    DEFAULT_PITCH, DeleteObject, FW_NORMAL, GetSysColor, HFONT, OUT_DEFAULT_PRECIS,
};
use windows_sys::Win32::UI::Controls::Dialogs::*;
use windows_sys::Win32::UI::HiDpi::GetDpiForWindow;
use windows_sys::Win32::UI::WindowsAndMessaging::*;

use crate::desktop::{wide, window_text};
use crate::hotkey::{is_modifier, key_name, windows::is_down};

// Win32 defines DWLP_USER as two pointer-sized dialog slots after DWLP_MSGRESULT.
const DWLP_USER: i32 = (2 * mem::size_of::<usize>()) as i32;

fn dialog_font(hwnd: HWND, font_family: &str) -> HFONT {
    let dpi = unsafe { GetDpiForWindow(hwnd) }.max(96) as i32;
    let family = wide(crate::config::ui_font_family(font_family));
    unsafe {
        CreateFontW(
            -((9 * dpi) / 72),
            0,
            0,
            0,
            FW_NORMAL as i32,
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
    }
}

fn apply_dialog_font(hwnd: HWND, font: HFONT, ids: &[i32]) {
    if font.is_null() {
        return;
    }
    for &id in ids {
        let child = unsafe { GetDlgItem(hwnd, id) };
        if !child.is_null() {
            unsafe { SendMessageW(child, WM_SETFONT, font as usize, 1) };
        }
    }
}

pub fn file(
    owner: HWND,
    title: &str,
    filter: &str,
    directory: &Path,
    extension: Option<&str>,
    save: bool,
) -> Option<PathBuf> {
    let title = wide(title);
    let filter = wide(filter);
    let directory = wide(&directory.to_string_lossy());
    let extension = extension.map(wide);
    let mut name = vec![0; 32_768];
    let mut info = OPENFILENAMEW {
        lStructSize: mem::size_of::<OPENFILENAMEW>() as u32,
        hwndOwner: owner,
        lpstrFilter: filter.as_ptr(),
        nFilterIndex: 1,
        lpstrFile: name.as_mut_ptr(),
        nMaxFile: name.len() as u32,
        lpstrInitialDir: directory.as_ptr(),
        lpstrTitle: title.as_ptr(),
        Flags: OFN_EXPLORER
            | OFN_NOCHANGEDIR
            | OFN_PATHMUSTEXIST
            | if save {
                OFN_OVERWRITEPROMPT
            } else {
                OFN_FILEMUSTEXIST
            },
        lpstrDefExt: extension.as_ref().map_or(ptr::null(), |text| text.as_ptr()),
        ..Default::default()
    };
    let accepted = unsafe {
        if save {
            GetSaveFileNameW(&mut info)
        } else {
            GetOpenFileNameW(&mut info)
        }
    } != 0;
    let end = name
        .iter()
        .position(|&unit| unit == 0)
        .unwrap_or(name.len());
    accepted.then(|| std::ffi::OsString::from_wide(&name[..end]).into())
}

pub fn confirm(owner: HWND, title: &str, message: &str) -> bool {
    let (title, message) = (wide(title), wide(message));
    unsafe {
        MessageBoxW(
            owner,
            message.as_ptr(),
            title.as_ptr(),
            MB_YESNO | MB_DEFBUTTON2 | MB_ICONQUESTION,
        ) == IDYES
    }
}

pub fn record_key(owner: HWND, single: bool, font_family: &str) -> Option<String> {
    let mut template = Template::with_size("录制快捷键", 244, 68);
    template.control(
        0x82,
        100,
        if single {
            "请按下一个键"
        } else {
            "请按下要使用的快捷键组合"
        },
        WS_VISIBLE | WS_CHILD,
        [12, 12, 212, 16],
    );
    template.control(
        0x80,
        IDCANCEL as u16,
        "取消",
        WS_VISIBLE | WS_CHILD | WS_TABSTOP,
        [170, 36, 60, 18],
    );
    let mut capture = Capture {
        previous: [false; 256],
        result: None,
        pressed: None,
        single,
        font_family: font_family.into(),
        font: ptr::null_mut(),
    };
    // The dialog procedure accesses this state only during the modal call.
    unsafe {
        DialogBoxIndirectParamW(
            ptr::null_mut(),
            template.words.as_ptr().cast(),
            owner,
            Some(capture_proc),
            ptr::from_mut(&mut capture) as isize,
        )
    };
    if !capture.font.is_null() {
        unsafe { DeleteObject(capture.font) };
    }
    capture.result
}

struct Capture {
    previous: [bool; 256],
    result: Option<String>,
    pressed: Option<(u16, String)>,
    single: bool,
    font_family: String,
    font: HFONT,
}

unsafe extern "system" fn capture_proc(hwnd: HWND, message: u32, wp: WPARAM, lp: LPARAM) -> isize {
    unsafe {
        match message {
            WM_INITDIALOG => {
                SetWindowLongPtrW(hwnd, DWLP_USER, lp);
                let data = &mut *(lp as *mut Capture);
                data.font = dialog_font(hwnd, &data.font_family);
                apply_dialog_font(hwnd, data.font, &[100, IDCANCEL]);
                data.previous = std::array::from_fn(|key| is_down(key as u16));
                SetTimer(hwnd, 1, 12, None);
                1
            }
            WM_TIMER if GetForegroundWindow() == hwnd => {
                let data = &mut *(GetWindowLongPtrW(hwnd, DWLP_USER) as *mut Capture);
                let down: [bool; 256] = std::array::from_fn(|key| is_down(key as u16));
                if let Some((key, _)) = &data.pressed
                    && !down[usize::from(*key)]
                {
                    data.result = data.pressed.take().map(|(_, binding)| binding);
                    EndDialog(hwnd, 0);
                    return 1;
                }
                if data.pressed.is_none() {
                    for key in 8..down.len() {
                        if !down[key] || data.previous[key] || matches!(key, 0x10..=0x12) {
                            continue;
                        }
                        let Some(key_name) = key_name(key as u32) else {
                            continue;
                        };
                        if !data.single && is_modifier(&key_name) {
                            continue;
                        }
                        let binding = if data.single {
                            key_name
                        } else {
                            let mut parts = Vec::new();
                            for (active, name) in [
                                (down[0x11], "ctrl"),
                                (down[0x10], "shift"),
                                (down[0x12], "alt"),
                                (down[0x5b] || down[0x5c], "windows"),
                            ] {
                                if active {
                                    parts.push(name);
                                }
                            }
                            parts.push(&key_name);
                            parts.join("+")
                        };
                        data.pressed = Some((key as u16, binding));
                        break;
                    }
                }
                data.previous = down;
                1
            }
            WM_COMMAND if wp as u16 == IDCANCEL as u16 => {
                EndDialog(hwnd, 0);
                1
            }
            WM_CLOSE => {
                EndDialog(hwnd, 0);
                1
            }
            WM_DESTROY => {
                KillTimer(hwnd, 1);
                0
            }
            _ => 0,
        }
    }
}

pub fn prompt(
    owner: HWND,
    title: &str,
    label: &str,
    value: &str,
    password: bool,
    font_family: &str,
) -> Option<String> {
    let mut data = Prompt {
        value: Some(value.to_owned()),
        accepted: false,
        font_family: font_family.into(),
        font: ptr::null_mut(),
    };
    let mut template = Template::new(title);
    template.control(0x0082, 100, label, WS_VISIBLE | WS_CHILD, [9, 9, 214, 16]);
    template.control(
        0x0081,
        101,
        "",
        WS_VISIBLE
            | WS_CHILD
            | WS_TABSTOP
            | WS_BORDER
            | ES_AUTOHSCROLL as u32
            | if password { ES_PASSWORD as u32 } else { 0 },
        [9, 28, 214, 16],
    );
    template.control(
        0x0080,
        IDOK as u16,
        "确定",
        WS_VISIBLE | WS_CHILD | WS_TABSTOP | BS_DEFPUSHBUTTON as u32,
        [106, 52, 55, 17],
    );
    template.control(
        0x0080,
        IDCANCEL as u16,
        "取消",
        WS_VISIBLE | WS_CHILD | WS_TABSTOP,
        [168, 52, 55, 17],
    );
    // The modal function keeps both the template and its callback state borrowed until it returns.
    unsafe {
        DialogBoxIndirectParamW(
            ptr::null_mut(),
            template.words.as_ptr().cast(),
            owner,
            Some(prompt_proc),
            ptr::from_mut(&mut data) as LPARAM,
        )
    };
    if !data.font.is_null() {
        unsafe { DeleteObject(data.font) };
    }
    if data.accepted { data.value } else { None }
}

struct Prompt {
    value: Option<String>,
    accepted: bool,
    font_family: String,
    font: HFONT,
}

unsafe extern "system" fn prompt_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> isize {
    unsafe {
        match msg {
            WM_INITDIALOG => {
                let data = &mut *(lp as *mut Prompt);
                SetWindowLongPtrW(hwnd, DWLP_USER, lp);
                data.font = dialog_font(hwnd, &data.font_family);
                apply_dialog_font(hwnd, data.font, &[100, 101, IDOK, IDCANCEL]);
                let value = wide(data.value.as_deref().unwrap_or_default());
                SetDlgItemTextW(hwnd, 101, value.as_ptr());
                // Native single-line edits draw at the top; centre the text in
                // the prompt field the same way the settings editors do.
                crate::desktop::center_edit_text(GetDlgItem(hwnd, 101), GetSysColor(COLOR_WINDOW));
                1
            }
            WM_COMMAND if matches!(wp as u16 as i32, IDOK | IDCANCEL) => {
                if wp as u16 as i32 == IDOK {
                    let data = &mut *(GetWindowLongPtrW(hwnd, DWLP_USER) as *mut Prompt);
                    data.value = Some(window_text(GetDlgItem(hwnd, 101)));
                    data.accepted = true;
                }
                EndDialog(hwnd, 0);
                1
            }
            WM_CLOSE => {
                EndDialog(hwnd, 0);
                1
            }
            _ => 0,
        }
    }
}

pub struct Template {
    words: Vec<u16>,
}

impl Template {
    fn new(title: &str) -> Self {
        Self::with_size(title, 232, 80)
    }

    pub fn with_size(title: &str, width: u16, height: u16) -> Self {
        Self::with_style(
            title,
            width,
            height,
            WS_CAPTION | WS_SYSMENU | DS_MODALFRAME as u32 | DS_SETFONT as u32 | DS_CENTER as u32,
            0,
        )
    }

    pub fn embedded(width: u16, height: u16) -> Self {
        Self::with_style(
            "",
            width,
            height,
            WS_CHILD | WS_CLIPCHILDREN | DS_CONTROL as u32 | DS_SETFONT as u32,
            WS_EX_CONTROLPARENT,
        )
    }

    fn with_style(title: &str, width: u16, height: u16, style: u32, ex_style: u32) -> Self {
        let mut template = Self {
            words: Vec::with_capacity(128),
        };
        template.dword(style);
        template.dword(ex_style);
        template.words.extend([0, 0, 0, width, height, 0, 0]);
        template.words.extend(wide(title));
        template.words.push(9);
        template.words.extend(wide(crate::config::DEFAULT_UI_FONT));
        template
    }

    fn dword(&mut self, value: u32) {
        self.words.extend([value as u16, (value >> 16) as u16]);
    }

    pub fn control(&mut self, class: u16, id: u16, text: &str, style: u32, bounds: [u16; 4]) {
        self.words[4] += 1;
        if !self.words.len().is_multiple_of(2) {
            self.words.push(0);
        }
        self.dword(style);
        self.dword(0);
        self.words.extend(bounds);
        self.words.extend([id, u16::MAX, class]);
        self.words.extend(wide(text));
        self.words.push(0);
    }
}

pub struct Panel(pub HWND);

impl Panel {
    pub fn new(owner: HWND, template: &Template, host: HWND, event: u32) -> std::io::Result<Self> {
        let mut routing = [host as usize, event as usize];
        let hwnd = unsafe {
            CreateDialogIndirectParamW(
                ptr::null_mut(),
                template.words.as_ptr().cast(),
                owner,
                Some(panel_proc),
                routing.as_mut_ptr() as isize,
            )
        };
        if hwnd.is_null() {
            Err(std::io::Error::last_os_error())
        } else {
            Ok(Self(hwnd))
        }
    }

    pub fn child(&self, id: u16) -> HWND {
        unsafe { GetDlgItem(self.0, i32::from(id)) }
    }
    pub fn text(&self, id: u16) -> String {
        window_text(self.child(id))
    }
    pub fn set_text(&self, id: u16, text: &str) {
        let text = wide(text);
        unsafe { SetDlgItemTextW(self.0, i32::from(id), text.as_ptr()) };
    }
    pub fn show(&self) {
        unsafe {
            ShowWindow(self.0, SW_SHOW);
            SetForegroundWindow(self.0);
        }
    }
    pub fn accepts(&self, message: &MSG) -> bool {
        // The wheel page can remain alive while a different settings tab is visible.
        unsafe { IsWindowVisible(self.0) != 0 && IsDialogMessageW(self.0, message) != 0 }
    }
}

impl Drop for Panel {
    fn drop(&mut self) {
        unsafe { DestroyWindow(self.0) };
    }
}

unsafe extern "system" fn panel_proc(hwnd: HWND, message: u32, wp: WPARAM, lp: LPARAM) -> isize {
    unsafe {
        let message_id = match message {
            WM_INITDIALOG => {
                let routing = &*(lp as *const [usize; 2]);
                SetWindowLongPtrW(hwnd, DWLP_USER, routing[0] as isize);
                SetPropW(
                    hwnd,
                    windows_sys::core::w!("WordyPanelEvent"),
                    ptr::without_provenance_mut(routing[1]),
                );
                return 1;
            }
            WM_COMMAND => wp,
            WM_CLOSE => IDCANCEL as usize,
            WM_NCDESTROY => {
                RemovePropW(hwnd, windows_sys::core::w!("WordyPanelEvent"));
                return 0;
            }
            _ => return 0,
        };
        let host = GetWindowLongPtrW(hwnd, DWLP_USER) as HWND;
        let event = GetPropW(hwnd, windows_sys::core::w!("WordyPanelEvent")) as usize;
        if !host.is_null() && event != 0 {
            PostMessageW(host, event as u32, message_id, hwnd as isize);
        }
        1
    }
}
