use std::mem;
use std::os::windows::ffi::OsStringExt;
use std::path::{Path, PathBuf};
use std::ptr;
use windows_sys::Win32::Foundation::{HWND, LPARAM, RECT, WPARAM};
use windows_sys::Win32::Graphics::Dwm::{
    DWMWA_BORDER_COLOR, DWMWA_CAPTION_COLOR, DWMWA_TEXT_COLOR, DWMWA_USE_IMMERSIVE_DARK_MODE,
    DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND,
};
use windows_sys::Win32::Graphics::Gdi::{
    CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS, CreateFontW, DC_BRUSH, DEFAULT_CHARSET, DEFAULT_PITCH,
    DT_CALCRECT, DT_CENTER, DT_LEFT, DT_NOPREFIX, DT_SINGLELINE, DT_VCENTER, DT_WORDBREAK,
    DeleteObject, DrawTextW, FW_NORMAL, FW_SEMIBOLD, GetDC, GetStockObject, HDC, HFONT,
    InvalidateRect, MapWindowPoints, OPAQUE, OUT_DEFAULT_PRECIS, ReleaseDC, SelectObject,
    SetBkColor, SetBkMode, SetDCBrushColor, SetTextColor,
};
use windows_sys::Win32::System::SystemServices::{SS_CENTER, SS_LEFT, SS_NOPREFIX};
use windows_sys::Win32::System::Threading::{AttachThreadInput, GetCurrentThreadId};
use windows_sys::Win32::UI::Controls::Dialogs::*;
use windows_sys::Win32::UI::Controls::{
    DRAWITEMSTRUCT, EM_SETMARGINS, EM_SETSEL, ODS_DISABLED, ODS_FOCUS, ODS_SELECTED, ODT_BUTTON,
    WM_MOUSELEAVE,
};
use windows_sys::Win32::UI::HiDpi::GetDpiForWindow;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    SetFocus, TME_LEAVE, TRACKMOUSEEVENT, TrackMouseEvent,
};
use windows_sys::Win32::UI::Shell::{DefSubclassProc, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

use crate::desktop::{set_dwm, wide, window_text};
use crate::hotkey::{display_hotkey, is_modifier, key_name, windows::is_down};
use crate::settings_backdrop::{fill, round};

// Win32 defines DWLP_USER as two pointer-sized dialog slots after DWLP_MSGRESULT.
const DWLP_USER: i32 = (2 * mem::size_of::<usize>()) as i32;

// Same dark palette as the settings window and the wheel editor panels.
const SURFACE: u32 = 0x001e_1c1c;
const FIELD: u32 = 0x002e_2c2c;
const BUTTON: u32 = 0x003b_3737;
const BUTTON_HOVER: u32 = 0x0042_423e;
const BUTTON_PRESSED: u32 = 0x0030_2d2d;
const BORDER: u32 = 0x003a_3838;
const HOVER_BORDER: u32 = 0x005f_5b5b;
const PRIMARY: u32 = 0x00f7_f5f5;
const MUTED: u32 = 0x009d_9898;
const ACCENT: u32 = 0x00ff_840a;

fn dialog_font_sized(hwnd: HWND, font_family: &str, points: i32, weight: i32) -> HFONT {
    let dpi = unsafe { GetDpiForWindow(hwnd) }.max(96) as i32;
    let family = wide(crate::config::ui_font_family(font_family));
    unsafe {
        CreateFontW(
            -((points * dpi) / 72),
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
    }
}

fn dialog_font(hwnd: HWND, font_family: &str) -> HFONT {
    dialog_font_sized(hwnd, font_family, 9, FW_NORMAL as i32)
}

fn set_text(hwnd: HWND, id: i32, text: &str) {
    let text = wide(text);
    unsafe { SetDlgItemTextW(hwnd, id, text.as_ptr()) };
}

fn solid(dc: HDC, color: u32) -> isize {
    unsafe {
        SetDCBrushColor(dc, color);
        GetStockObject(DC_BRUSH) as isize
    }
}

/// Shared dark surface, label colours and owner-drawn buttons for every modal
/// dialog, so the popups match the settings window instead of the light system
/// defaults. Returns `None` for messages the dialog procedure must handle.
fn paint_dialog(hwnd: HWND, message: u32, wp: WPARAM, lp: LPARAM) -> Option<isize> {
    match message {
        WM_ERASEBKGND => {
            let mut rect = RECT::default();
            unsafe { GetClientRect(hwnd, &mut rect) };
            fill(wp as HDC, &rect, SURFACE);
            Some(1)
        }
        WM_CTLCOLORDLG => Some(solid(wp as HDC, SURFACE)),
        WM_CTLCOLORSTATIC => {
            let dc = wp as HDC;
            let id = unsafe { GetDlgCtrlID(lp as HWND) };
            unsafe {
                SetTextColor(dc, if id == 102 { MUTED } else { PRIMARY });
                SetBkColor(dc, SURFACE);
                SetBkMode(dc, OPAQUE as i32);
            }
            Some(solid(dc, SURFACE))
        }
        WM_CTLCOLOREDIT => {
            let dc = wp as HDC;
            unsafe {
                SetTextColor(dc, PRIMARY);
                SetBkColor(dc, FIELD);
                SetBkMode(dc, OPAQUE as i32);
            }
            Some(solid(dc, FIELD))
        }
        WM_DRAWITEM if lp != 0 => {
            let item = unsafe { &*(lp as *const DRAWITEMSTRUCT) };
            (item.CtlType == ODT_BUTTON).then(|| {
                paint_button(item);
                1
            })
        }
        _ => None,
    }
}

fn paint_button(item: &DRAWITEMSTRUCT) {
    let disabled = item.itemState & ODS_DISABLED != 0;
    let pressed = item.itemState & ODS_SELECTED != 0;
    let focused = item.itemState & ODS_FOCUS != 0;
    let (background, border) = if disabled {
        (FIELD, BORDER)
    } else if pressed {
        (BUTTON_PRESSED, BORDER)
    } else if hovering(item.hwndItem) {
        (BUTTON_HOVER, HOVER_BORDER)
    } else {
        (BUTTON, if focused { ACCENT } else { BORDER })
    };
    // Buttons sit on the dialog surface; the fill under the arcs avoids dark squares.
    fill(item.hDC, &item.rcItem, SURFACE);
    round(item.hDC, &item.rcItem, 9, border, Some(background));
    let text = wide(&window_text(item.hwndItem));
    let mut area = item.rcItem;
    let font = unsafe { SendMessageW(item.hwndItem, WM_GETFONT, 0, 0) } as HFONT;
    unsafe {
        let previous = if font.is_null() {
            std::ptr::null_mut()
        } else {
            windows_sys::Win32::Graphics::Gdi::SelectObject(item.hDC, font)
        };
        SetBkMode(
            item.hDC,
            windows_sys::Win32::Graphics::Gdi::TRANSPARENT as i32,
        );
        SetTextColor(item.hDC, if disabled { MUTED } else { PRIMARY });
        DrawTextW(
            item.hDC,
            text.as_ptr(),
            (text.len() - 1) as i32,
            &mut area,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
        );
        if !previous.is_null() {
            windows_sys::Win32::Graphics::Gdi::SelectObject(item.hDC, previous);
        }
    }
}

fn hovering(hwnd: HWND) -> bool {
    let mut rect = RECT::default();
    let mut cursor = windows_sys::Win32::Foundation::POINT::default();
    unsafe {
        GetWindowRect(hwnd, &mut rect);
        GetCursorPos(&mut cursor);
    }
    (rect.left..rect.right).contains(&cursor.x) && (rect.top..rect.bottom).contains(&cursor.y)
}

const HOVER: windows_sys::core::PCWSTR = windows_sys::core::w!("Wordy.Dialog.Hover");

/// Owner-drawn buttons cover their whole face, so the class brush must not
/// erase them first; hover and focus repaint once per transition to avoid flicker.
unsafe extern "system" fn no_erase_proc(
    hwnd: HWND,
    message: u32,
    wp: WPARAM,
    lp: LPARAM,
    _id: usize,
    _data: usize,
) -> isize {
    unsafe {
        match message {
            WM_ERASEBKGND => 1,
            WM_MOUSEMOVE => {
                let mut tracking = TRACKMOUSEEVENT {
                    cbSize: mem::size_of::<TRACKMOUSEEVENT>() as u32,
                    dwFlags: TME_LEAVE,
                    hwndTrack: hwnd,
                    dwHoverTime: 0,
                };
                TrackMouseEvent(&mut tracking);
                if GetPropW(hwnd, HOVER).is_null() {
                    SetPropW(hwnd, HOVER, 1 as _);
                    InvalidateRect(hwnd, ptr::null(), 0);
                }
                DefSubclassProc(hwnd, message, wp, lp)
            }
            WM_MOUSELEAVE => {
                RemovePropW(hwnd, HOVER);
                InvalidateRect(hwnd, ptr::null(), 0);
                DefSubclassProc(hwnd, message, wp, lp)
            }
            WM_SETFOCUS | WM_KILLFOCUS | WM_ENABLE | WM_SETTEXT => {
                let result = DefSubclassProc(hwnd, message, wp, lp);
                InvalidateRect(hwnd, ptr::null(), 0);
                result
            }
            _ => DefSubclassProc(hwnd, message, wp, lp),
        }
    }
}

fn style_dialog(hwnd: HWND, buttons: &[i32]) {
    for (attribute, value) in [
        (DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
        (DWMWA_CAPTION_COLOR, SURFACE as i32),
        (DWMWA_TEXT_COLOR, PRIMARY as i32),
        (DWMWA_BORDER_COLOR, BORDER as i32),
        (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND),
    ] {
        set_dwm(hwnd, attribute, value);
    }
    for &id in buttons {
        let button = unsafe { GetDlgItem(hwnd, id) };
        if button.is_null() {
            continue;
        }
        unsafe {
            let style = GetWindowLongPtrW(button, GWL_STYLE) as u32;
            SetWindowLongPtrW(
                button,
                GWL_STYLE,
                ((style & !(BS_TYPEMASK as u32)) | BS_OWNERDRAW as u32) as isize,
            );
            SetWindowSubclass(button, Some(no_erase_proc), 1, 0);
        }
    }
}

/// Posted after WM_INITDIALOG, when the dialog is visible and can be activated.
const FORCE_FOREGROUND: u32 = WM_APP + 17;

/// Modal dialogs are owned by the settings window, which the app may have
/// opened while another process still holds the foreground. Windows then
/// refuses to activate the dialog, keystrokes keep going to the other app, and
/// the hotkey recorder hears nothing. Attach to the foreground thread once to
/// hand the dialog the foreground explicitly.
fn force_foreground(hwnd: HWND) {
    unsafe {
        let foreground = GetForegroundWindow();
        let foreground_thread = if foreground.is_null() {
            0
        } else {
            GetWindowThreadProcessId(foreground, ptr::null_mut())
        };
        let current = GetCurrentThreadId();
        let attached = foreground_thread != 0
            && foreground_thread != current
            && AttachThreadInput(current, foreground_thread, 1) != 0;
        SetForegroundWindow(hwnd);
        if attached {
            AttachThreadInput(current, foreground_thread, 0);
        }
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

pub fn confirm(owner: HWND, title: &str, message: &str, font_family: &str) -> bool {
    let mut template = Template::with_size(title, 300, 104);
    template.control(
        0x82,
        100,
        message,
        WS_VISIBLE | WS_CHILD | SS_LEFT | SS_NOPREFIX,
        [16, 16, 268, 44],
    );
    template.control(
        0x80,
        IDOK as u16,
        "确定",
        WS_VISIBLE | WS_CHILD | WS_TABSTOP | BS_DEFPUSHBUTTON as u32,
        [150, 72, 64, 22],
    );
    template.control(
        0x80,
        IDCANCEL as u16,
        "取消",
        WS_VISIBLE | WS_CHILD | WS_TABSTOP,
        [222, 72, 62, 22],
    );
    let mut data = Confirm {
        accepted: false,
        font_family: font_family.into(),
        font: ptr::null_mut(),
    };
    // The modal function keeps both the template and its callback state borrowed until it returns.
    unsafe {
        DialogBoxIndirectParamW(
            ptr::null_mut(),
            template.words.as_ptr().cast(),
            owner,
            Some(confirm_proc),
            ptr::from_mut(&mut data) as LPARAM,
        )
    };
    if !data.font.is_null() {
        unsafe { DeleteObject(data.font) };
    }
    data.accepted
}

struct Confirm {
    accepted: bool,
    font_family: String,
    font: HFONT,
}

/// Grows the dialog when a confirmation message needs more than one screenful
/// of its initial label box, keeping the buttons anchored below the text.
fn fit_message(hwnd: HWND) {
    let label = unsafe { GetDlgItem(hwnd, 100) };
    if label.is_null() {
        return;
    }
    let mut client = RECT::default();
    unsafe { GetClientRect(label, &mut client) };
    let text = wide(&window_text(label));
    let dc = unsafe { GetDC(hwnd) };
    if dc.is_null() {
        return;
    }
    let font = unsafe { SendMessageW(label, WM_GETFONT, 0, 0) } as HFONT;
    let previous = if font.is_null() {
        ptr::null_mut()
    } else {
        unsafe { SelectObject(dc, font) }
    };
    let mut measured = RECT {
        left: 0,
        top: 0,
        right: client.right,
        bottom: 0,
    };
    unsafe {
        DrawTextW(
            dc,
            text.as_ptr(),
            (text.len() - 1) as i32,
            &mut measured,
            DT_CALCRECT | DT_WORDBREAK | DT_NOPREFIX | DT_LEFT,
        );
    }
    if !previous.is_null() {
        unsafe { SelectObject(dc, previous) };
    }
    unsafe { ReleaseDC(hwnd, dc) };
    let delta = measured.bottom - (client.bottom - client.top);
    if delta <= 0 {
        return;
    }
    let mut window = RECT::default();
    unsafe { GetWindowRect(hwnd, &mut window) };
    let (width, height) = (window.right - window.left, window.bottom - window.top);
    unsafe {
        SetWindowPos(
            hwnd,
            ptr::null_mut(),
            window.left,
            window.top - delta / 2,
            width,
            height + delta,
            SWP_NOZORDER | SWP_NOACTIVATE,
        );
        SetWindowPos(
            label,
            ptr::null_mut(),
            0,
            0,
            client.right,
            client.bottom + delta,
            SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE,
        );
        for id in [IDOK, IDCANCEL] {
            let button = GetDlgItem(hwnd, id);
            let mut bounds = RECT::default();
            GetWindowRect(button, &mut bounds);
            MapWindowPoints(ptr::null_mut(), hwnd, ptr::from_mut(&mut bounds).cast(), 2);
            SetWindowPos(
                button,
                ptr::null_mut(),
                bounds.left,
                bounds.top + delta,
                bounds.right - bounds.left,
                bounds.bottom - bounds.top,
                SWP_NOZORDER | SWP_NOACTIVATE,
            );
        }
    }
}

unsafe extern "system" fn confirm_proc(hwnd: HWND, message: u32, wp: WPARAM, lp: LPARAM) -> isize {
    if let Some(result) = paint_dialog(hwnd, message, wp, lp) {
        return result;
    }
    unsafe {
        match message {
            WM_INITDIALOG => {
                SetWindowLongPtrW(hwnd, DWLP_USER, lp);
                let data = &mut *(lp as *mut Confirm);
                style_dialog(hwnd, &[IDOK, IDCANCEL]);
                data.font = dialog_font(hwnd, &data.font_family);
                apply_dialog_font(hwnd, data.font, &[100, IDOK, IDCANCEL]);
                fit_message(hwnd);
                PostMessageW(hwnd, FORCE_FOREGROUND, 0, 0);
                1
            }
            FORCE_FOREGROUND => {
                force_foreground(hwnd);
                // Like the old Yes/No box, the safe answer owns Enter and focus.
                SetFocus(GetDlgItem(hwnd, IDCANCEL));
                SendMessageW(hwnd, DM_SETDEFID, IDCANCEL as usize, 0);
                1
            }
            WM_COMMAND if matches!(wp as u16 as i32, IDOK | IDCANCEL) => {
                let data = &mut *(GetWindowLongPtrW(hwnd, DWLP_USER) as *mut Confirm);
                data.accepted = wp as u16 as i32 == IDOK;
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

pub fn record_key(owner: HWND, single: bool, font_family: &str) -> Option<String> {
    let mut template = Template::with_size("录制快捷键", 300, 124);
    template.control(
        0x82,
        100,
        if single {
            "请按下一个键"
        } else {
            "请按下要使用的快捷键组合"
        },
        WS_VISIBLE | WS_CHILD | SS_CENTER,
        [16, 14, 268, 18],
    );
    template.control(
        0x82,
        101,
        "…",
        WS_VISIBLE | WS_CHILD | SS_CENTER,
        [16, 38, 268, 30],
    );
    template.control(
        0x82,
        102,
        "松开即保存，按 Esc 取消",
        WS_VISIBLE | WS_CHILD | SS_CENTER,
        [16, 74, 268, 16],
    );
    template.control(
        0x80,
        IDCANCEL as u16,
        "取消",
        WS_VISIBLE | WS_CHILD | WS_TABSTOP,
        [208, 96, 76, 22],
    );
    let mut capture = Capture {
        previous: [false; 256],
        result: None,
        pressed: None,
        single,
        font_family: font_family.into(),
        font: ptr::null_mut(),
        value_font: ptr::null_mut(),
        shown: String::new(),
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
    for font in [capture.font, capture.value_font] {
        if !font.is_null() {
            unsafe { DeleteObject(font) };
        }
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
    value_font: HFONT,
    shown: String,
}

/// Preview of the modifiers held before the main key lands, mirroring the
/// order the recorded binding uses.
fn modifier_preview(down: &[bool; 256]) -> Option<String> {
    let parts: Vec<_> = [
        (down[0x11], "Ctrl"),
        (down[0x10], "Shift"),
        (down[0x12], "Alt"),
        (down[0x5b] || down[0x5c], "Win"),
    ]
    .into_iter()
    .filter_map(|(active, name)| active.then_some(name))
    .collect();
    (!parts.is_empty()).then(|| format!("{} + …", parts.join(" + ")))
}

unsafe extern "system" fn capture_proc(hwnd: HWND, message: u32, wp: WPARAM, lp: LPARAM) -> isize {
    if let Some(result) = paint_dialog(hwnd, message, wp, lp) {
        return result;
    }
    unsafe {
        match message {
            WM_INITDIALOG => {
                SetWindowLongPtrW(hwnd, DWLP_USER, lp);
                let data = &mut *(lp as *mut Capture);
                style_dialog(hwnd, &[IDCANCEL]);
                data.font = dialog_font(hwnd, &data.font_family);
                data.value_font =
                    dialog_font_sized(hwnd, &data.font_family, 12, FW_SEMIBOLD as i32);
                apply_dialog_font(hwnd, data.font, &[100, 102, IDCANCEL]);
                apply_dialog_font(hwnd, data.value_font, &[101]);
                // The dialog is still hidden here; activate it once shown.
                PostMessageW(hwnd, FORCE_FOREGROUND, 0, 0);
                data.previous = std::array::from_fn(|key| is_down(key as u16));
                SetTimer(hwnd, 1, 12, None);
                1
            }
            FORCE_FOREGROUND => {
                force_foreground(hwnd);
                SetFocus(hwnd);
                1
            }
            WM_TIMER if GetForegroundWindow() == hwnd => {
                let data = &mut *(GetWindowLongPtrW(hwnd, DWLP_USER) as *mut Capture);
                let down: [bool; 256] = std::array::from_fn(|key| is_down(key as u16));
                // Esc is the dialog's cancel affordance, never a recording.
                if down[0x1b] {
                    EndDialog(hwnd, 0);
                    return 1;
                }
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
                        let shown = display_hotkey(&binding);
                        if shown != data.shown {
                            set_text(hwnd, 101, &shown);
                            data.shown = shown;
                        }
                        data.pressed = Some((key as u16, binding));
                        break;
                    }
                }
                if data.pressed.is_none()
                    && let Some(preview) = modifier_preview(&down)
                    && preview != data.shown
                {
                    set_text(hwnd, 101, &preview);
                    data.shown = preview;
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
    template.control(
        0x0082,
        100,
        label,
        WS_VISIBLE | WS_CHILD | SS_LEFT | SS_NOPREFIX,
        [9, 9, 214, 16],
    );
    template.control(
        0x0081,
        101,
        "",
        WS_VISIBLE
            | WS_CHILD
            | WS_TABSTOP
            | ES_AUTOHSCROLL as u32
            | if password { ES_PASSWORD as u32 } else { 0 },
        [9, 28, 214, 20],
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
                style_dialog(hwnd, &[IDOK, IDCANCEL]);
                data.font = dialog_font(hwnd, &data.font_family);
                apply_dialog_font(hwnd, data.font, &[100, 101, IDOK, IDCANCEL]);
                // Enter submits the field instead of the owner-drawn button type losing the default.
                SendMessageW(hwnd, DM_SETDEFID, IDOK as usize, 0);
                // The prompt field matches the settings editors: rounded corners
                // over the dialog surface and vertically centred text.
                let field = GetDlgItem(hwnd, 101);
                crate::panels::style_card_edit(field, FIELD, SURFACE);
                let margin =
                    crate::settings_backdrop::scaled(10, GetDpiForWindow(hwnd).max(96) as i32);
                SendMessageW(
                    field,
                    EM_SETMARGINS,
                    (EC_LEFTMARGIN | EC_RIGHTMARGIN) as usize,
                    (margin | (margin << 16)) as isize,
                );
                PostMessageW(hwnd, FORCE_FOREGROUND, 0, 0);
                let value = wide(data.value.as_deref().unwrap_or_default());
                SetDlgItemTextW(hwnd, 101, value.as_ptr());
                1
            }
            FORCE_FOREGROUND => {
                force_foreground(hwnd);
                let field = GetDlgItem(hwnd, 101);
                SetFocus(field);
                // Replace the existing value directly so typing overwrites it.
                SendMessageW(field, EM_SETSEL, 0, -1);
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::desktop::host_window;
    use std::time::Duration;
    use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;

    #[test]
    fn hotkey_recorder_takes_the_foreground_and_returns_on_cancel() {
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        let owner = host_window(instance).unwrap();
        let title = wide("录制快捷键");
        // The probe runs while record_key owns the modal loop on the test thread.
        let took_foreground = std::thread::spawn(move || {
            // The dialog exists a moment before the posted activation runs.
            let deadline = std::time::Instant::now() + Duration::from_secs(2);
            let mut dialog: HWND = ptr::null_mut();
            while std::time::Instant::now() < deadline {
                if dialog.is_null() {
                    dialog = unsafe { FindWindowW(ptr::null(), title.as_ptr()) };
                }
                if !dialog.is_null() && unsafe { GetForegroundWindow() == dialog } {
                    break;
                }
                std::thread::sleep(Duration::from_millis(10));
            }
            let foreground = !dialog.is_null() && unsafe { GetForegroundWindow() == dialog };
            if !dialog.is_null() {
                unsafe { PostMessageW(dialog, WM_COMMAND, IDCANCEL as usize, 0) };
            }
            foreground
        });
        let binding = record_key(owner, false, crate::config::DEFAULT_UI_FONT);
        let took_foreground = took_foreground.join().unwrap();
        unsafe { DestroyWindow(owner) };
        assert!(binding.is_none(), "cancel must not record a binding");
        assert!(took_foreground, "the recorder must own the keyboard");
    }

    fn when_visible(title: &[u16]) -> HWND {
        let deadline = std::time::Instant::now() + Duration::from_secs(2);
        loop {
            let dialog = unsafe { FindWindowW(ptr::null(), title.as_ptr()) };
            if !dialog.is_null() && unsafe { IsWindowVisible(dialog) } != 0 {
                return dialog;
            }
            if std::time::Instant::now() >= deadline {
                return ptr::null_mut();
            }
            std::thread::sleep(Duration::from_millis(10));
        }
    }

    #[test]
    fn confirmation_reports_which_button_was_pressed() {
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        let owner = host_window(instance).unwrap();
        for (title, command, expected) in [
            ("确认框测试-确定", IDOK, true),
            ("确认框测试-取消", IDCANCEL, false),
        ] {
            let title_wide = wide(title);
            let probe = std::thread::spawn(move || {
                let dialog = when_visible(&title_wide);
                assert!(!dialog.is_null(), "the confirmation never appeared");
                unsafe { PostMessageW(dialog, WM_COMMAND, command as usize, 0) };
            });
            let result = confirm(owner, title, "是否继续？", crate::config::DEFAULT_UI_FONT);
            probe.join().unwrap();
            assert_eq!(
                result, expected,
                "confirmation button {command} mapped wrong"
            );
        }
        unsafe { DestroyWindow(owner) };
    }

    #[test]
    fn prompt_returns_the_edited_value() {
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        let owner = host_window(instance).unwrap();
        let title = wide("输入框测试");
        let probe = std::thread::spawn(move || {
            let dialog = when_visible(&title);
            assert!(!dialog.is_null(), "the prompt never appeared");
            let value = wide("新名称");
            unsafe {
                SetDlgItemTextW(dialog, 101, value.as_ptr());
                PostMessageW(dialog, WM_COMMAND, IDOK as usize, 0);
            }
        });
        let value = prompt(
            owner,
            "输入框测试",
            "轮盘名称",
            "旧名称",
            false,
            crate::config::DEFAULT_UI_FONT,
        );
        probe.join().unwrap();
        unsafe { DestroyWindow(owner) };
        assert_eq!(value.as_deref(), Some("新名称"));
    }
}
