use std::{
    cell::{Cell, RefCell},
    io, mem, ptr,
};
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Dwm::{
    DWMWA_CAPTION_COLOR, DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_WINDOW_CORNER_PREFERENCE,
    DWMWCP_ROUND, DwmSetWindowAttribute,
};
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::System::SystemServices::SS_OWNERDRAW;
use windows_sys::Win32::UI::Controls::{
    DRAWITEMSTRUCT, EM_SETMARGINS, ODS_COMBOBOXEDIT, ODS_DISABLED, ODS_FOCUS, ODS_SELECTED,
    ODT_BUTTON, ODT_COMBOBOX, ODT_STATIC, TBM_SETPAGESIZE, TBM_SETRANGE, TBS_NOTICKS,
    TRACKBAR_CLASSW, WM_MOUSELEAVE,
};
use windows_sys::Win32::UI::HiDpi::GetDpiForWindow;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetFocus, IsWindowEnabled, TME_LEAVE, TRACKMOUSEEVENT, TrackMouseEvent,
};
use windows_sys::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

use crate::config::Settings;
use crate::desktop::{center_edit_text, paint_buffered, paint_buffered_rect, wide, window_text};
use crate::dialog::{Panel, Template};
use crate::selector::style_selector;
use crate::settings_backdrop::paint_embedded_backdrop;
use crate::slider::{self, TBM_GETPOS};

mod preview;
mod trim;

mod chrome;
pub(super) use chrome::Chrome;
pub(crate) use chrome::style_card_edit;
pub use preview::wheel_labels;
pub use trim::Trim;

pub const TRIM_MESSAGE: u32 = WM_APP + 12;
pub const PICK: u16 = 200;
pub const WHEEL: u16 = 201;
pub const ADD: u16 = 202;
pub const REMOVE: u16 = 203;
pub const NAME: u16 = 204;
pub const STYLE: u16 = 206;
pub const LABEL: u16 = 207;
pub const TEXT: u16 = 208;
pub const GAIN: u16 = 209;
pub const SAVE: u16 = 210;
pub const PLAY: u16 = 214;
pub const CLEAR: u16 = 215;
pub const INFO: u16 = 216;
pub const WAVE: u16 = 217;
pub const START: u16 = 218;
pub const END: u16 = 219;
pub const STOP: u16 = 220;
const PRECISE: u16 = 224;
const RANGE_SUMMARY: u16 = 225;
const LOADING: u16 = 226;
const START_LABEL: u16 = 301;
const END_LABEL: u16 = 302;
const GAIN_VALUE: u16 = 227;
// The real GAIN edit stays hidden behind the slider, so the slider carries its own id.
const GAIN_SLIDER: u16 = 228;
const GAIN_SUBCLASS: usize = 11;

// COLORREF stores red, green, blue in least-to-most-significant byte order.
const PANEL_SURFACE: u32 = 0x1e1c1c;
const ELEVATED: u32 = 0x2e2c2c;
const EDITOR_CARD: u32 = 0x3e3b3b;
const SEPARATOR: u32 = 0x3a3838;
const PRIMARY: u32 = 0xf7f5f5;
const MUTED: u32 = 0x9d9898;
const ACCENT: u32 = 0xff840a;
const ERROR: u32 = 0x3a45ff;

const TABS: u32 = WS_TABSTOP;
const BASE: u32 = WS_VISIBLE | WS_CHILD;

fn button(template: &mut Template, id: u16, label: &str, bounds: [u16; 4]) {
    template.control(0x80, id, label, BASE | TABS | BS_OWNERDRAW as u32, bounds);
}

fn label(template: &mut Template, id: u16, label: &str, bounds: [u16; 4]) {
    // Ownerdraw so the glass (or card) is repainted under the text every time,
    // including after the UI font changes.
    template.control(0x82, id, label, BASE | SS_OWNERDRAW, bounds);
}

fn edit(template: &mut Template, id: u16, bounds: [u16; 4]) {
    // No WS_BORDER: the shared edit helper paints a dark 1px frame instead.
    template.control(0x81, id, "", BASE | TABS | ES_AUTOHSCROLL as u32, bounds);
}

fn combo(template: &mut Template, id: u16, bounds: [u16; 4]) {
    // Ownerdraw is what makes CB_SETITEMHEIGHT size the closed field, so the
    // selector can match the 32px toolbar buttons.
    template.control(
        0x85,
        id,
        "",
        BASE | TABS
            | WS_VSCROLL
            | CBS_DROPDOWNLIST as u32
            | CBS_OWNERDRAWFIXED as u32
            | CBS_HASSTRINGS as u32,
        bounds,
    );
}

fn combo_item(combo: HWND, index: u32) -> String {
    let length = unsafe { SendMessageW(combo, CB_GETLBTEXTLEN, index as usize, 0) };
    if length < 0 {
        return String::new();
    }
    let mut buffer = vec![0u16; length as usize + 1];
    let written = unsafe {
        SendMessageW(
            combo,
            CB_GETLBTEXT,
            index as usize,
            buffer.as_mut_ptr() as LPARAM,
        )
    };
    String::from_utf16_lossy(&buffer[..written.max(0) as usize])
}

fn draw_label(hwnd: HWND, dc: HDC, mut area: RECT, trim: bool) {
    let id = unsafe { GetDlgCtrlID(hwnd) } as u16;
    let card = !trim && matches!(id, INFO | GAIN_VALUE | 302..=305);
    if card {
        fill(dc, area, EDITOR_CARD);
    } else if trim {
        fill(dc, area, PANEL_SURFACE);
    } else {
        paint_embedded_backdrop(hwnd, dc);
    }
    let text = wide(&window_text(hwnd));
    unsafe {
        let font = SendMessageW(hwnd, WM_GETFONT, 0, 0) as HFONT;
        let previous = if font.is_null() {
            ptr::null_mut()
        } else {
            SelectObject(dc, font)
        };
        SetBkMode(dc, TRANSPARENT as i32);
        SetTextColor(dc, if id == LOADING { MUTED } else { PRIMARY });
        DrawTextW(
            dc,
            text.as_ptr(),
            -1,
            &mut area,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS,
        );
        if !previous.is_null() {
            SelectObject(dc, previous);
        }
    }
}

fn draw_combo_item(item: &DRAWITEMSTRUCT) {
    let selected = item.itemState & ODS_SELECTED != 0;
    fill(
        item.hDC,
        item.rcItem,
        if selected { 0x764e25 } else { ELEVATED },
    );
    let mut area = item.rcItem;
    area.left += logical_px(item.hwndItem, 8);
    let text = wide(&combo_item(item.hwndItem, item.itemID));
    unsafe {
        let font = SendMessageW(item.hwndItem, WM_GETFONT, 0, 0) as HFONT;
        let previous = if font.is_null() {
            ptr::null_mut()
        } else {
            SelectObject(item.hDC, font)
        };
        SetBkMode(item.hDC, TRANSPARENT as i32);
        SetTextColor(item.hDC, PRIMARY);
        DrawTextW(
            item.hDC,
            text.as_ptr(),
            -1,
            &mut area,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
        );
        if !previous.is_null() {
            SelectObject(item.hDC, previous);
        }
    }
}

fn choices(panel: &Panel, id: u16, values: impl IntoIterator<Item = String>, index: usize) {
    let widget = panel.child(id);
    unsafe {
        SendMessageW(widget, CB_RESETCONTENT, 0, 0);
        for value in values {
            let value = wide(&value);
            SendMessageW(widget, CB_ADDSTRING, 0, value.as_ptr() as isize);
        }
        SendMessageW(widget, CB_SETCURSEL, index, 0);
    }
}

pub fn selected(panel: &Panel, id: u16) -> usize {
    usize::try_from(unsafe { SendMessageW(panel.child(id), CB_GETCURSEL, 0, 0) }).unwrap_or(0)
}

fn wheel_names(settings: &Settings) -> impl Iterator<Item = String> + '_ {
    (0..settings.wheel_count()).map(|i| {
        let name = settings.wheel(i).expect("wheel index in range").name;
        if name.is_empty() {
            format!("轮盘 {}", i + 1)
        } else {
            name
        }
    })
}

fn dimensions(hwnd: HWND) -> (i32, i32) {
    let mut rect = RECT::default();
    unsafe { GetClientRect(hwnd, &mut rect) };
    (rect.right, rect.bottom)
}

fn logical_px(hwnd: HWND, units: i32) -> i32 {
    // The embedded editor page shares the settings shell's 96-DPI coordinate
    // space; both it and standalone dialogs must scale to the window's DPI.
    let dpi = unsafe { GetDpiForWindow(hwnd) }.max(96) as i32;
    (units * dpi + 48) / 96
}

fn fill(dc: HDC, rect: RECT, color: u32) {
    unsafe {
        let brush = CreateSolidBrush(color);
        FillRect(dc, &rect, brush);
        DeleteObject(brush);
    }
}

fn with_pen(dc: HDC, width: i32, color: u32, draw: impl FnOnce()) {
    unsafe {
        let pen = CreatePen(PS_SOLID, width, color);
        let old = SelectObject(dc, pen);
        draw();
        SelectObject(dc, old);
        DeleteObject(pen);
    }
}

fn rounded(dc: HDC, rect: RECT, radius: i32, background: Option<u32>, border: u32) {
    unsafe {
        let brush = background.map_or_else(
            || GetStockObject(NULL_BRUSH),
            |color| CreateSolidBrush(color),
        );
        let old = SelectObject(dc, brush);
        with_pen(dc, 1, border, || {
            RoundRect(
                dc,
                rect.left,
                rect.top,
                rect.right,
                rect.bottom,
                radius,
                radius,
            );
        });
        SelectObject(dc, old);
        if background.is_some() {
            DeleteObject(brush);
        }
    }
}
