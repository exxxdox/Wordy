use std::{cell::Cell, io, mem, ptr};
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Dwm::{
    DWMWA_CAPTION_COLOR, DWMWA_USE_IMMERSIVE_DARK_MODE, DWMWA_WINDOW_CORNER_PREFERENCE,
    DWMWCP_ROUND, DwmSetWindowAttribute,
};
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::System::SystemServices::SS_OWNERDRAW;
use windows_sys::Win32::UI::Controls::{
    DRAWITEMSTRUCT, EM_SETLIMITTEXT, EM_SETMARGINS, ODS_COMBOBOXEDIT, ODS_DISABLED, ODS_FOCUS,
    ODS_SELECTED, ODT_BUTTON, ODT_COMBOBOX, ODT_STATIC, TB_ENDTRACK, TBM_SETPAGESIZE, TBM_SETRANGE,
    TBS_NOTICKS, TRACKBAR_CLASSW, WM_MOUSELEAVE,
};
use windows_sys::Win32::UI::HiDpi::GetDpiForWindow;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    EnableWindow, GetFocus, GetKeyState, ReleaseCapture, SetCapture, SetFocus, TME_LEAVE,
    TRACKMOUSEEVENT, TrackMouseEvent, VK_DOWN, VK_LEFT, VK_RETURN, VK_RIGHT, VK_SHIFT, VK_SPACE,
    VK_UP,
};
use windows_sys::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

use crate::config::{Settings, Wheel};
use crate::desktop::{frame_edit_text, paint_buffered, wide, window_text};
use crate::dialog::{Panel, Template};
use crate::selector::style_selector;
use crate::settings_window::paint_embedded_backdrop;
use crate::slider::{self, TBM_GETPOS};
use crate::wheel::{CANVAS_HEIGHT, CANVAS_WIDTH, Point, WheelStyle, picker_slot_at};
use crate::wheel_paint::{WheelBitmap, WheelFrame, WheelRenderer};

pub const EDITOR_MESSAGE: u32 = WM_APP + 11;
pub const TRIM_MESSAGE: u32 = WM_APP + 12;
pub const PICK: u16 = 200;
pub const WHEEL: u16 = 201;
pub const ADD: u16 = 202;
pub const REMOVE: u16 = 203;
pub const NAME: u16 = 204;
pub const HOTKEY: u16 = 205;
pub const STYLE: u16 = 206;
pub const LABEL: u16 = 207;
pub const TEXT: u16 = 208;
pub const GAIN: u16 = 209;
pub const SAVE: u16 = 210;
pub const IMPORT: u16 = 211;
pub const CROP: u16 = 213;
pub const PLAY: u16 = 214;
pub const CLEAR: u16 = 215;
pub const INFO: u16 = 216;
pub const WAVE: u16 = 217;
pub const START: u16 = 218;
pub const END: u16 = 219;
pub const STOP: u16 = 220;
pub const AUDIO_MODE: u16 = 221;
pub const TEXT_MODE: u16 = 222;
const PRECISE: u16 = 224;
const RANGE_SUMMARY: u16 = 225;
const LOADING: u16 = 226;
const START_LABEL: u16 = 301;
const END_LABEL: u16 = 302;
const GAIN_VALUE: u16 = 227;
// The real GAIN edit stays hidden behind the slider, so the slider carries its own id.
const GAIN_SLIDER: u16 = 228;
const RENAME: u16 = 229;
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
    template.control(0x82, id, label, BASE | SS_OWNERDRAW as u32, bounds);
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

struct Chrome {
    hwnd: HWND,
    state: Box<ChromeState>,
}

struct ChromeState {
    surface: HBRUSH,
    elevated: HBRUSH,
    card: HBRUSH,
    gain: HWND,
    trim: bool,
    precise: Cell<bool>,
    previewing: Cell<bool>,
    text_mode: Cell<bool>,
}

impl Chrome {
    fn attach(panel: &Panel, trim: bool, rolling: bool) -> io::Result<Self> {
        let mut state = Box::new(ChromeState {
            surface: unsafe { CreateSolidBrush(PANEL_SURFACE) },
            elevated: unsafe { CreateSolidBrush(ELEVATED) },
            card: unsafe { CreateSolidBrush(EDITOR_CARD) },
            gain: ptr::null_mut(),
            trim,
            precise: Cell::new(false),
            previewing: Cell::new(false),
            text_mode: Cell::new(false),
        });
        if !trim || rolling {
            // Keep the numeric field as the existing app-message contract and display a slider instead.
            let edit = panel.child(GAIN);
            let mut rect = RECT::default();
            unsafe {
                GetWindowRect(edit, &mut rect);
                MapWindowPoints(ptr::null_mut(), panel.0, ptr::from_mut(&mut rect).cast(), 2);
                state.gain = CreateWindowExW(
                    0,
                    TRACKBAR_CLASSW,
                    ptr::null(),
                    BASE | TABS | TBS_NOTICKS,
                    rect.left,
                    rect.top,
                    rect.right - rect.left,
                    rect.bottom - rect.top,
                    panel.0,
                    GAIN_SLIDER as isize as HMENU,
                    ptr::null_mut(),
                    ptr::null_mut(),
                );
                if !state.gain.is_null() {
                    SendMessageW(state.gain, TBM_SETRANGE, 1, 300 << 16);
                    SendMessageW(state.gain, TBM_SETPAGESIZE, 0, 10);
                }
            }
        }
        if unsafe {
            SetWindowSubclass(
                panel.0,
                Some(chrome_proc),
                7,
                ptr::from_mut(state.as_mut()) as usize,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        if trim {
            unsafe {
                // A dialog redraw must leave the wave and other child surfaces intact.
                let style = GetWindowLongPtrW(panel.0, GWL_STYLE);
                SetWindowLongPtrW(panel.0, GWL_STYLE, style | WS_CLIPCHILDREN as isize);
                // Match the dark client while retaining the system frame and shadow.
                for (attribute, value) in [
                    (DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
                    (DWMWA_CAPTION_COLOR, PANEL_SURFACE),
                    (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND as u32),
                ] {
                    DwmSetWindowAttribute(
                        panel.0,
                        attribute as u32,
                        ptr::from_ref(&value).cast(),
                        mem::size_of_val(&value) as u32,
                    );
                }
                precise_controls(panel.0, false);
                if !rolling {
                    ShowWindow(panel.child(LABEL), SW_HIDE);
                }
            }
        }
        // Style the selectors as rounded dark fields instead of system chrome.
        let item_height = logical_px(panel.0, 28);
        for id in [WHEEL, STYLE] {
            let combo = panel.child(id);
            if !combo.is_null() {
                unsafe {
                    SendMessageW(combo, CB_SETITEMHEIGHT, usize::MAX, item_height as isize);
                    SendMessageW(combo, CB_SETITEMHEIGHT, 0, item_height as isize);
                }
                style_selector(combo, !trim);
            }
        }
        if trim || !state.gain.is_null() {
            unsafe { ShowWindow(panel.child(GAIN), SW_HIDE) };
            if !state.gain.is_null() {
                let background = if trim { PANEL_SURFACE } else { EDITOR_CARD };
                unsafe {
                    SetWindowSubclass(
                        state.gain,
                        Some(slider_proc),
                        GAIN_SUBCLASS,
                        background as usize,
                    )
                };
            }
        }
        // Single-line edits draw their text at the top; center it in the field.
        for id in [GAIN, NAME, LABEL, TEXT, START, END] {
            let child = panel.child(id);
            if !child.is_null() {
                frame_edit_text(child, ELEVATED, SEPARATOR);
                let margin = logical_px(panel.0, 8);
                unsafe {
                    SendMessageW(
                        child,
                        EM_SETMARGINS,
                        (EC_LEFTMARGIN | EC_RIGHTMARGIN) as usize,
                        (margin | (margin << 16)) as isize,
                    )
                };
            }
        }
        // Ownerdraw buttons paint their full face, so the class-brush erase only flashes.
        suppress_button_erase(panel.0);
        Ok(Self {
            hwnd: panel.0,
            state,
        })
    }

    fn gain(&self, gain: f64) {
        let percent = (gain * 100.0).round() as i32;
        set_control_text(self.hwnd, GAIN, &percent.to_string());
        set_control_text(self.hwnd, GAIN_VALUE, &format!("{percent}%"));
        slider::set_position(self.state.gain, percent);
    }
}

impl Drop for Chrome {
    fn drop(&mut self) {
        unsafe { RemoveWindowSubclass(self.hwnd, Some(chrome_proc), 7) };
    }
}

impl Drop for ChromeState {
    fn drop(&mut self) {
        unsafe {
            DeleteObject(self.surface);
            DeleteObject(self.elevated);
            DeleteObject(self.card);
        }
    }
}

fn set_control_text(hwnd: HWND, id: u16, value: &str) {
    let text = wide(value);
    unsafe { SetDlgItemTextW(hwnd, i32::from(id), text.as_ptr()) };
}

unsafe fn precise_controls(hwnd: HWND, visible: bool) {
    for id in [START_LABEL, END_LABEL, START, END] {
        unsafe {
            ShowWindow(
                GetDlgItem(hwnd, i32::from(id)),
                if visible { SW_SHOW } else { SW_HIDE },
            )
        };
    }
}

fn suppress_button_erase(panel: HWND) {
    unsafe {
        let mut child = GetWindow(panel, GW_CHILD);
        while !child.is_null() {
            let style = GetWindowLongPtrW(child, GWL_STYLE) as u32;
            if style & BS_TYPEMASK as u32 == BS_OWNERDRAW as u32 {
                SetWindowSubclass(child, Some(no_erase_proc), 8, 0);
            }
            child = GetWindow(child, GW_HWNDNEXT);
        }
    }
}

fn paint_gain(hwnd: HWND, dc: HDC, rect: RECT, background: u32) {
    fill(dc, rect, background);
    let (channel, x) = slider::thumb_geometry(hwnd);
    let y = (rect.top + rect.bottom) / 2;
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
    rounded(dc, track, 6, Some(SEPARATOR), SEPARATOR);
    rounded(dc, progress, 6, Some(ACCENT), ACCENT);
    let thumb = RECT {
        left: x - 8,
        top: y - 8,
        right: x + 8,
        bottom: y + 8,
    };
    let ring = if unsafe { GetFocus() } == hwnd {
        ACCENT
    } else {
        PRIMARY
    };
    rounded(dc, thumb, 16, Some(PRIMARY), ring);
}

unsafe extern "system" fn slider_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    background: usize,
) -> LRESULT {
    if let Some(result) = slider::handle_pointer(hwnd, message, lparam) {
        return result;
    }
    unsafe {
        match message {
            WM_ERASEBKGND => 1,
            WM_PAINT => {
                paint_buffered(hwnd, |dc, rect| {
                    paint_gain(hwnd, dc, rect, background as u32)
                });
                0
            }
            WM_SETFOCUS | WM_KILLFOCUS | WM_KEYDOWN | WM_MOUSEWHEEL => {
                let result = DefSubclassProc(hwnd, message, wparam, lparam);
                InvalidateRect(hwnd, ptr::null(), 0);
                result
            }
            _ => DefSubclassProc(hwnd, message, wparam, lparam),
        }
    }
}

unsafe extern "system" fn no_erase_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    _data: usize,
) -> LRESULT {
    if message == WM_ERASEBKGND {
        return 1;
    }
    if message == WM_MOUSEMOVE {
        // Ownerdraw buttons have no hover state of their own; repaint on entry
        // and exit so the hover fill shows.
        let mut track = TRACKMOUSEEVENT {
            cbSize: mem::size_of::<TRACKMOUSEEVENT>() as u32,
            dwFlags: TME_LEAVE,
            hwndTrack: hwnd,
            dwHoverTime: 0,
        };
        unsafe {
            TrackMouseEvent(&mut track);
            InvalidateRect(hwnd, ptr::null(), 0);
        }
    } else if message == WM_MOUSELEAVE {
        unsafe { InvalidateRect(hwnd, ptr::null(), 0) };
    }
    unsafe { DefSubclassProc(hwnd, message, wparam, lparam) }
}

fn hovering(hwnd: HWND) -> bool {
    let mut rect = RECT::default();
    let mut cursor = POINT::default();
    unsafe {
        GetWindowRect(hwnd, &mut rect);
        GetCursorPos(&mut cursor);
    }
    (rect.left..rect.right).contains(&cursor.x) && (rect.top..rect.bottom).contains(&cursor.y)
}

unsafe extern "system" fn chrome_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    _: usize,
    data: usize,
) -> LRESULT {
    unsafe {
        let state = &*(data as *const ChromeState);
        match msg {
            WM_SIZE if !state.trim => {
                layout_editor(hwnd, state);
                DefSubclassProc(hwnd, msg, wp, lp)
            }
            WM_ERASEBKGND if !state.trim => 1,
            WM_PAINT if !state.trim => {
                // Limit the background paint to the dialog's uncovered client area.
                let mut paint = PAINTSTRUCT::default();
                let dc = BeginPaint(hwnd, &mut paint);
                paint_embedded_backdrop(hwnd, dc);
                rounded(
                    dc,
                    editor_card(hwnd, state.text_mode.get()),
                    logical_px(hwnd, 24),
                    Some(EDITOR_CARD),
                    0x4b4848,
                );
                EndPaint(hwnd, &paint);
                0
            }
            WM_CTLCOLORDLG => state.surface as LRESULT,
            WM_CTLCOLORSTATIC | WM_CTLCOLORBTN | WM_CTLCOLOREDIT | WM_CTLCOLORLISTBOX => {
                let dc = wp as HDC;
                let control = GetDlgCtrlID(lp as HWND) as u16;
                let field = matches!(msg, WM_CTLCOLOREDIT | WM_CTLCOLORLISTBOX)
                    || matches!(control, WHEEL | STYLE);
                let card =
                    !state.trim && matches!(control, INFO | GAIN_SLIDER | GAIN_VALUE | 302..=305);
                // Values read bright; captions stay muted. Captions sit straight
                // on the glass, so they must not erase a solid surface rectangle.
                SetTextColor(dc, PRIMARY);
                SetBkMode(dc, if field { OPAQUE } else { TRANSPARENT } as i32);
                SetBkColor(
                    dc,
                    if field {
                        ELEVATED
                    } else if card {
                        EDITOR_CARD
                    } else {
                        PANEL_SURFACE
                    },
                );
                if field {
                    state.elevated as LRESULT
                } else if card {
                    state.card as LRESULT
                } else {
                    GetStockObject(NULL_BRUSH) as LRESULT
                }
            }
            WM_DRAWITEM if lp != 0 => {
                let item = &*(lp as *const DRAWITEMSTRUCT);
                match item.CtlType {
                    ODT_BUTTON => draw_button(item, state),
                    ODT_STATIC => draw_label(item, state),
                    ODT_COMBOBOX if item.itemState & ODS_COMBOBOXEDIT == 0 => draw_combo_item(item),
                    _ => return DefSubclassProc(hwnd, msg, wp, lp),
                }
                1
            }
            WM_HSCROLL if lp != 0 && lp as HWND == state.gain => {
                let value = SendMessageW(state.gain, TBM_GETPOS, 0, 0);
                set_control_text(hwnd, GAIN, &value.to_string());
                set_control_text(hwnd, GAIN_VALUE, &format!("{value}%"));
                if !state.trim && wp as u16 as u32 == TB_ENDTRACK {
                    PostMessageW(
                        hwnd,
                        WM_COMMAND,
                        usize::from(GAIN) | ((EN_KILLFOCUS as usize) << 16),
                        state.gain as LPARAM,
                    );
                }
                0
            }
            WM_COMMAND if state.trim && wp as u16 == PRECISE => {
                let precise = !state.precise.get();
                state.precise.set(precise);
                precise_controls(hwnd, precise);
                set_control_text(
                    hwnd,
                    PRECISE,
                    if precise {
                        "▾ 精确调整"
                    } else {
                        "▸ 精确调整"
                    },
                );
                1
            }
            WM_COMMAND if state.trim && state.previewing.get() && wp == usize::from(PLAY) => {
                PostMessageW(hwnd, WM_COMMAND, usize::from(STOP), 0);
                1
            }
            WM_COMMAND if !state.trim && wp as u16 == RENAME => {
                let current = window_text(GetDlgItem(hwnd, i32::from(NAME)));
                if let Some(name) =
                    crate::dialog::prompt(hwnd, "重命名轮盘", "轮盘名称", &current, false)
                {
                    let name = name.trim().to_owned();
                    if name != current {
                        // Reuse the NAME edit's commit path so the app owns persistence.
                        set_control_text(hwnd, NAME, &name);
                        PostMessageW(
                            hwnd,
                            WM_COMMAND,
                            usize::from(NAME) | ((EN_KILLFOCUS as usize) << 16),
                            0,
                        );
                    }
                }
                1
            }
            _ => DefSubclassProc(hwnd, msg, wp, lp),
        }
    }
}

fn draw_button(item: &DRAWITEMSTRUCT, state: &ChromeState) {
    let id = item.CtlID as u16;
    let selected = (matches!(id, AUDIO_MODE) && !state.text_mode.get())
        || (matches!(id, TEXT_MODE) && state.text_mode.get());
    let disabled = item.itemState & ODS_DISABLED != 0;
    let pressing = item.itemState & ODS_SELECTED != 0;
    let focus = item.itemState & ODS_FOCUS != 0;
    let hovering = !disabled && hovering(item.hwndItem);
    let danger = matches!(id, REMOVE | CLEAR);
    let (background, border) = if selected || state.trim && id == SAVE {
        (
            if hovering { 0x7b4a26 } else { 0x673c1c },
            if focus { PRIMARY } else { ACCENT },
        )
    } else if pressing {
        (0x242426, SEPARATOR)
    } else if id == PRECISE {
        (PANEL_SURFACE, PANEL_SURFACE)
    } else if hovering {
        (
            if danger { 0x32262a } else { 0x3a3a3e },
            if focus { ACCENT } else { 0x4a4a4e },
        )
    } else {
        (ELEVATED, if focus { ACCENT } else { SEPARATOR })
    };
    let card_button =
        !state.trim && matches!(id, AUDIO_MODE | TEXT_MODE | IMPORT | PLAY | CROP | CLEAR);
    // Toolbar buttons sit on the glass, so their rounded corners must show the
    // glass; a solid surface fill would leave dark corner squares.
    let toolbar = !state.trim && matches!(id, HOTKEY | ADD | REMOVE | RENAME);
    if toolbar {
        paint_embedded_backdrop(item.hwndItem, item.hDC);
    } else {
        fill(
            item.hDC,
            item.rcItem,
            if card_button {
                EDITOR_CARD
            } else {
                PANEL_SURFACE
            },
        );
    }
    rounded(item.hDC, item.rcItem, 10, Some(background), border);
    let text = wide(&window_text(item.hwndItem));
    let mut area = item.rcItem;
    unsafe {
        // Ownerdraw DCs do not guarantee the control font; select it so the
        // settings UI font applies here too.
        let font = SendMessageW(item.hwndItem, WM_GETFONT, 0, 0) as HFONT;
        let previous = if font.is_null() {
            ptr::null_mut()
        } else {
            SelectObject(item.hDC, font)
        };
        SetBkMode(item.hDC, TRANSPARENT as i32);
        SetTextColor(
            item.hDC,
            if disabled {
                MUTED
            } else if matches!(id, REMOVE | CLEAR) {
                ERROR
            } else {
                PRIMARY
            },
        );
        DrawTextW(
            item.hDC,
            text.as_ptr(),
            -1,
            &mut area,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
        );
        if !previous.is_null() {
            SelectObject(item.hDC, previous);
        }
    }
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

fn draw_label(item: &DRAWITEMSTRUCT, state: &ChromeState) {
    let id = item.CtlID as u16;
    let card = !state.trim && matches!(id, INFO | GAIN_VALUE | 302..=305);
    if card {
        fill(item.hDC, item.rcItem, EDITOR_CARD);
    } else if state.trim {
        fill(item.hDC, item.rcItem, PANEL_SURFACE);
    } else {
        paint_embedded_backdrop(item.hwndItem, item.hDC);
    }
    let text = wide(&window_text(item.hwndItem));
    let mut area = item.rcItem;
    unsafe {
        let font = SendMessageW(item.hwndItem, WM_GETFONT, 0, 0) as HFONT;
        let previous = if font.is_null() {
            ptr::null_mut()
        } else {
            SelectObject(item.hDC, font)
        };
        SetBkMode(item.hDC, TRANSPARENT as i32);
        SetTextColor(item.hDC, if id == LOADING { MUTED } else { PRIMARY });
        DrawTextW(
            item.hDC,
            text.as_ptr(),
            -1,
            &mut area,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS,
        );
        if !previous.is_null() {
            SelectObject(item.hDC, previous);
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

pub fn wheel_labels(wheel: &Wheel) -> [String; 8] {
    std::array::from_fn(|i| {
        if wheel.slots[i].is_empty() && wheel.texts[i].is_empty() {
            String::new()
        } else if !wheel.labels[i].is_empty() {
            wheel.labels[i].clone()
        } else if !wheel.texts[i].is_empty() {
            wheel.texts[i].clone()
        } else {
            wheel.slots[i].clone()
        }
    })
}

struct Preview {
    child: HWND,
    state: Box<PreviewState>,
}

struct PreviewState {
    bitmap: Option<WheelBitmap>,
    selected: Option<usize>,
    hover: Option<usize>,
    labels: [String; 8],
    style: WheelStyle,
    renderer: WheelRenderer,
    embedded: bool,
}

impl Preview {
    fn new(child: HWND, embedded: bool) -> io::Result<Self> {
        let renderer = WheelRenderer::new().map_err(io::Error::other)?;
        let mut state = Box::new(PreviewState {
            bitmap: None,
            selected: None,
            hover: None,
            labels: std::array::from_fn(|_| String::new()),
            style: WheelStyle::Classic,
            renderer,
            embedded,
        });
        if unsafe {
            SetWindowSubclass(
                child,
                Some(preview_proc),
                1,
                ptr::from_mut(state.as_mut()) as usize,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(Self { child, state })
    }

    fn draw(&mut self, labels: &[String; 8], style: WheelStyle, slot: usize) -> io::Result<()> {
        self.state.labels = labels.clone();
        self.state.style = style;
        self.state.selected = Some(slot);
        render_preview(self.child, &mut self.state);
        Ok(())
    }
}

fn render_preview(child: HWND, state: &mut PreviewState) {
    let labels = state.labels.clone();
    let mut frame = WheelFrame::new(&labels, state.style);
    frame.editing = true;
    if !state.embedded {
        frame.center_hint = Some("选择槽位");
    }
    frame.selected = state.selected;
    let mut highlights = [0.0; 8];
    if let Some(slot) = state.selected {
        highlights[slot] = 1.0;
    }
    if let Some(slot) = state.hover
        && Some(slot) != state.selected
    {
        highlights[slot] = 0.45;
    }
    frame.highlights = Some(highlights);
    let (width, height) = dimensions(child);
    let scale = (f64::from(width) / CANVAS_WIDTH).min(f64::from(height) / CANVAS_HEIGHT);
    if let Ok(bitmap) = state.renderer.render(frame, scale) {
        state.bitmap = Some(bitmap);
        unsafe { InvalidateRect(child, ptr::null(), 0) };
    }
}

impl Drop for Preview {
    fn drop(&mut self) {
        unsafe { RemoveWindowSubclass(self.child, Some(preview_proc), 1) };
    }
}

unsafe extern "system" fn preview_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    _: usize,
    data: usize,
) -> LRESULT {
    unsafe {
        let state = &mut *(data as *mut PreviewState);
        let (width, height) = dimensions(hwnd);
        match msg {
            WM_ERASEBKGND => 1,
            WM_PAINT => {
                paint_buffered(hwnd, |dc, rect| {
                    fill(dc, rect, PANEL_SURFACE);
                    if state.embedded {
                        paint_embedded_backdrop(hwnd, dc);
                    }
                    if let Some(bitmap) = &state.bitmap {
                        blend_preview(dc, bitmap, width, height);
                    }
                });
                0
            }
            WM_MOUSEMOVE => {
                let slot = preview_slot(width, height, lp);
                if slot != state.hover {
                    state.hover = slot;
                    render_preview(hwnd, state);
                }
                let mut track = TRACKMOUSEEVENT {
                    cbSize: mem::size_of::<TRACKMOUSEEVENT>() as u32,
                    dwFlags: TME_LEAVE,
                    hwndTrack: hwnd,
                    dwHoverTime: 0,
                };
                TrackMouseEvent(&mut track);
                0
            }
            WM_MOUSELEAVE => {
                if state.hover.take().is_some() {
                    render_preview(hwnd, state);
                }
                0
            }
            WM_GETDLGCODE => (DLGC_WANTARROWS | DLGC_WANTCHARS) as LRESULT,
            WM_KEYDOWN => {
                let step = match wp as u16 {
                    VK_LEFT | VK_UP => -1,
                    VK_RIGHT | VK_DOWN => 1,
                    VK_RETURN | VK_SPACE => {
                        if state.selected.is_some() {
                            PostMessageW(
                                GetParent(hwnd),
                                WM_COMMAND,
                                usize::from(PICK),
                                hwnd as isize,
                            );
                        }
                        return 0;
                    }
                    _ => return DefSubclassProc(hwnd, msg, wp, lp),
                };
                let current = state.selected.unwrap_or(0) as isize;
                let next = (current + step).rem_euclid(8) as usize;
                state.selected = Some(next);
                render_preview(hwnd, state);
                PostMessageW(
                    GetParent(hwnd),
                    WM_COMMAND,
                    usize::from(PICK),
                    hwnd as isize,
                );
                0
            }
            WM_LBUTTONDOWN => {
                SetFocus(hwnd);
                if let Some(slot) = preview_slot(width, height, lp) {
                    state.selected = Some(slot);
                    PostMessageW(
                        GetParent(hwnd),
                        WM_COMMAND,
                        usize::from(PICK),
                        hwnd as isize,
                    );
                }
                0
            }
            _ => DefSubclassProc(hwnd, msg, wp, lp),
        }
    }
}

fn preview_slot(width: i32, height: i32, lp: LPARAM) -> Option<usize> {
    let size =
        (f64::from(width) / CANVAS_WIDTH).min(f64::from(height) / CANVAS_HEIGHT) * CANVAS_WIDTH;
    let x = f64::from(lp as i16) - (f64::from(width) - size) / 2.0;
    let y = f64::from((lp >> 16) as i16) - (f64::from(height) - size) / 2.0;
    picker_slot_at(Point::new(x, y), size)
}

fn blend_preview(dc: HDC, bitmap: &WheelBitmap, width: i32, height: i32) {
    let (w, h) = (bitmap.width as i32, bitmap.height as i32);
    let mut info = BITMAPINFO::default();
    info.bmiHeader.biSize = mem::size_of::<BITMAPINFOHEADER>() as u32;
    info.bmiHeader.biWidth = w;
    info.bmiHeader.biHeight = -h;
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    let mut bits = ptr::null_mut();
    unsafe {
        let image = CreateDIBSection(dc, &info, DIB_RGB_COLORS, &mut bits, ptr::null_mut(), 0);
        if image.is_null() {
            return;
        }
        let source = CreateCompatibleDC(dc);
        if !source.is_null() {
            // The shared wheel renderer emits premultiplied BGRA, the layout expected by AlphaBlend.
            ptr::copy_nonoverlapping(
                bitmap.pixels.as_ptr(),
                bits.cast::<u8>(),
                bitmap.pixels.len(),
            );
            let old = SelectObject(source, image);
            AlphaBlend(
                dc,
                (width - w) / 2,
                (height - h) / 2,
                w,
                h,
                source,
                0,
                0,
                w,
                h,
                BLENDFUNCTION {
                    BlendOp: AC_SRC_OVER as u8,
                    BlendFlags: 0,
                    SourceConstantAlpha: 255,
                    AlphaFormat: AC_SRC_ALPHA as u8,
                },
            );
            SelectObject(source, old);
            DeleteDC(source);
        }
        DeleteObject(image);
    }
}

fn dimensions(hwnd: HWND) -> (i32, i32) {
    let mut rect = RECT::default();
    unsafe { GetClientRect(hwnd, &mut rect) };
    (rect.right, rect.bottom)
}

pub struct Editor {
    preview: Preview,
    chrome: Chrome,
    pub panel: Panel,
    pub wheel: usize,
    pub slot: usize,
    pub empty_text_mode: bool,
    busy: bool,
}

impl Editor {
    pub fn new_embedded(host: HWND, parent: HWND, settings: &Settings) -> io::Result<Self> {
        let mut template = Template::embedded(500, 264);
        label(&mut template, 300, "轮盘", [4, 11, 27, 14]);
        combo(&mut template, WHEEL, [31, 7, 108, 100]);
        label(&mut template, 301, "快捷键", [144, 11, 28, 14]);
        button(&mut template, HOTKEY, "录制快捷键", [172, 7, 80, 18]);
        button(&mut template, ADD, "新增", [256, 7, 34, 18]);
        button(&mut template, REMOVE, "删除", [294, 7, 34, 18]);
        button(&mut template, RENAME, "重命名", [0, 0, 1, 1]);
        label(&mut template, 307, "样式", [407, 11, 28, 14]);
        combo(&mut template, STYLE, [435, 7, 63, 100]);
        template.control(0x82, PICK, "", BASE | TABS | 0x0000_0100, [3, 33, 198, 212]);
        edit(&mut template, NAME, [0, 0, 1, 1]);
        label(&mut template, INFO, "", [217, 42, 252, 16]);
        label(&mut template, 302, "内容", [217, 64, 26, 15]);
        button(&mut template, AUDIO_MODE, "音频", [245, 60, 34, 18]);
        button(&mut template, TEXT_MODE, "文字", [283, 60, 34, 18]);
        label(&mut template, 304, "音量", [217, 88, 26, 15]);
        edit(&mut template, GAIN, [245, 83, 112, 18]);
        label(&mut template, GAIN_VALUE, "100%", [362, 87, 41, 15]);
        label(&mut template, 305, "名称", [217, 108, 52, 15]);
        edit(&mut template, LABEL, [217, 125, 272, 18]);
        label(&mut template, 303, "朗读文字", [217, 150, 60, 15]);
        edit(&mut template, TEXT, [217, 167, 272, 18]);
        button(&mut template, IMPORT, "导入", [217, 152, 33, 18]);
        button(&mut template, PLAY, "试听", [342, 152, 42, 18]);
        button(&mut template, CROP, "截取", [388, 152, 33, 18]);
        button(&mut template, CLEAR, "清空", [425, 152, 33, 18]);
        let panel = Panel::new(parent, &template, host, EDITOR_MESSAGE)?;
        let chrome = Chrome::attach(&panel, false, false)?;
        layout_editor(panel.0, &chrome.state);
        let preview = Preview::new(panel.child(PICK), true)?;
        let mut editor = Self {
            preview,
            chrome,
            panel,
            wheel: settings.chat_wheel_last_index,
            slot: 0,
            empty_text_mode: false,
            busy: false,
        };
        for (id, limit) in [(NAME, 20), (LABEL, 40), (TEXT, 200), (GAIN, 8)] {
            unsafe { SendMessageW(editor.panel.child(id), EM_SETLIMITTEXT, limit, 0) };
        }
        unsafe { ShowWindow(editor.panel.child(NAME), SW_HIDE) };
        editor.refresh(settings)?;
        Ok(editor)
    }

    pub fn take_slot(&mut self) -> Option<usize> {
        self.preview.state.selected.take()
    }

    pub fn refresh(&mut self, settings: &Settings) -> io::Result<()> {
        self.wheel = self.wheel.min(settings.wheel_count() - 1);
        let wheel = settings.wheel(self.wheel).expect("wheel index in range");
        choices(&self.panel, WHEEL, wheel_names(settings), self.wheel);
        let style_index = crate::config::CHAT_WHEEL_STYLES
            .iter()
            .position(|style| *style == settings.chat_wheel_style)
            .unwrap_or(0);
        choices(
            &self.panel,
            STYLE,
            ["经典玻璃", "轻薄玻璃", "悬浮花瓣", "竞技轮盘"]
                .into_iter()
                .map(str::to_owned),
            style_index,
        );
        self.panel.set_text(NAME, &wheel.name);
        self.panel
            .set_text(HOTKEY, &crate::hotkey::display_hotkey(&wheel.hotkey));
        self.panel.set_text(LABEL, &wheel.labels[self.slot]);
        self.panel.set_text(TEXT, &wheel.texts[self.slot]);
        self.chrome.gain(wheel.volumes[self.slot]);
        let text_mode = !wheel.texts[self.slot].is_empty()
            || (wheel.slots[self.slot].is_empty() && self.empty_text_mode);
        self.chrome.state.text_mode.set(text_mode);
        unsafe { InvalidateRect(self.panel.0, ptr::null(), 1) };
        for (id, visible) in [
            (TEXT, text_mode),
            (303, text_mode),
            (GAIN, !text_mode && self.chrome.state.gain.is_null()),
            (GAIN_VALUE, !text_mode && !self.chrome.state.gain.is_null()),
            (304, !text_mode),
            (IMPORT, !text_mode),
            (CROP, !text_mode),
        ] {
            unsafe {
                ShowWindow(
                    self.panel.child(id),
                    if visible { SW_SHOW } else { SW_HIDE },
                )
            };
        }
        let has_audio = !wheel.slots[self.slot].is_empty();
        let has_text = !wheel.texts[self.slot].is_empty();
        unsafe {
            ShowWindow(
                self.chrome.state.gain,
                if text_mode { SW_HIDE } else { SW_SHOW },
            );
            EnableWindow(self.chrome.state.gain, i32::from(has_audio));
            EnableWindow(
                self.panel.child(REMOVE),
                i32::from(settings.wheel_count() > 1),
            );
            EnableWindow(self.panel.child(PLAY), i32::from(has_audio || has_text));
            EnableWindow(self.panel.child(CROP), i32::from(has_audio));
            EnableWindow(
                self.panel.child(CLEAR),
                i32::from(has_audio || has_text || !wheel.labels[self.slot].is_empty()),
            );
            for id in [AUDIO_MODE, TEXT_MODE] {
                InvalidateRect(self.panel.child(id), ptr::null(), 0);
            }
        }
        layout_editor(self.panel.0, &self.chrome.state);
        self.panel
            .set_text(INFO, &format!("音频 {}", self.slot + 1));
        self.preview.draw(
            &wheel_labels(&wheel),
            WheelStyle::from_config(&settings.chat_wheel_style),
            self.slot,
        )
    }

    /// Disables editing while a sample is being imported, saved, or previewed,
    /// leaving the preview button available to stop playback.
    pub fn busy(&mut self, active: bool) {
        if self.busy == active {
            return;
        }
        self.busy = active;
        unsafe {
            for id in [
                WHEEL, PICK, ADD, REMOVE, HOTKEY, STYLE, LABEL, TEXT, AUDIO_MODE, TEXT_MODE,
                IMPORT, CROP, CLEAR,
            ] {
                EnableWindow(self.panel.child(id), i32::from(!active));
            }
            EnableWindow(self.chrome.state.gain, i32::from(!active));
        }
    }
}

fn logical_px(hwnd: HWND, units: i32) -> i32 {
    // The embedded editor page shares the settings shell's 96-DPI coordinate
    // space; both it and standalone dialogs must scale to the window's DPI.
    let dpi = unsafe { GetDpiForWindow(hwnd) }.max(96) as i32;
    (units * dpi + 48) / 96
}

fn place(control: HWND, x: i32, y: i32, width: i32, height: i32) {
    unsafe {
        SetWindowPos(
            control,
            ptr::null_mut(),
            x,
            y,
            width.max(1),
            height,
            SWP_NOZORDER | SWP_NOACTIVATE,
        );
    }
}

fn editor_card(hwnd: HWND, text_mode: bool) -> RECT {
    RECT {
        left: logical_px(hwnd, 424),
        top: logical_px(hwnd, 44),
        right: dimensions(hwnd).0,
        bottom: logical_px(hwnd, if text_mode { 290 } else { 273 }),
    }
}

fn layout_editor(hwnd: HWND, state: &ChromeState) {
    let width = dimensions(hwnd).0;
    let px = |value| logical_px(hwnd, value);
    let at = |id, x, y, w, h| place(unsafe { GetDlgItem(hwnd, id) }, x, px(y), w, px(h));
    let card = editor_card(hwnd, state.text_mode.get());
    let left = card.left + px(18);
    let field = card.right - left - px(18);
    for (id, x, y, w, h) in [
        (300, 0, 7, 40, 20),
        (WHEEL, 46, 0, 204, 200),
        (301, 256, 7, 56, 20),
        (HOTKEY, 318, 0, 145, 32),
        (ADD, 471, 0, 52, 32),
        (REMOVE, 531, 0, 52, 32),
        (RENAME, 591, 0, 58, 32),
        (PICK, 0, 44, 400, 400),
    ] {
        at(i32::from(id), px(x), y, px(w), h);
    }
    at(307, width - px(184), 7, px(40), 20);
    at(i32::from(STYLE), width - px(136), 0, px(136), 200);
    for (id, x, y, w, h) in [
        (INFO, 0, 60, 180, 23),
        (302, 0, 96, 44, 20),
        (AUDIO_MODE, 43, 86, 42, 32),
        (TEXT_MODE, 93, 86, 42, 32),
        (304, 0, 126, 44, 20),
        (GAIN_VALUE, 268, 126, 50, 20),
        (
            305,
            0,
            if state.text_mode.get() { 123 } else { 152 },
            50,
            20,
        ),
        (303, 0, 184, 140, 20),
    ] {
        at(i32::from(id), left + px(x), y, px(w), h);
    }
    let gain = if state.gain.is_null() {
        unsafe { GetDlgItem(hwnd, i32::from(GAIN)) }
    } else {
        state.gain
    };
    place(gain, left + px(35), px(118), px(220), px(30));
    at(
        i32::from(LABEL),
        left,
        if state.text_mode.get() { 145 } else { 174 },
        field,
        32,
    );
    at(i32::from(TEXT), left, 205, field, 32);
    let mode = state.text_mode.get();
    let action_y = if mode { 244 } else { 222 };
    let (mut x, gap) = (left, px(6));
    for (id, control_width, visible) in [
        (IMPORT, 42, !mode),
        (PLAY, 64, true),
        (CROP, 42, !mode),
        (CLEAR, 42, true),
    ] {
        if visible {
            at(i32::from(id), x, action_y, px(control_width), 32);
            x += px(control_width) + gap;
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Range {
    pub start: f64,
    pub end: f64,
    pub duration: f64,
}

impl Range {
    fn set(&mut self, start: f64, end: f64) {
        if start.is_finite() && end.is_finite() {
            self.start = start.min(end).clamp(0.0, self.duration);
            self.end = start.max(end).clamp(0.0, self.duration);
        }
    }

    fn dragged(self, drag: Drag, at: f64) -> Self {
        let mut next = self;
        let at = at.clamp(0.0, self.duration);
        let gap = self.duration.min(0.001);
        match drag {
            Drag::Start => next.start = at.min((self.end - gap).max(0.0)),
            Drag::End => next.end = at.max((self.start + gap).min(self.duration)),
            Drag::Range(anchor) if (at - anchor).abs() >= gap => next.set(anchor, at),
            Drag::Range(_) => (),
        }
        next
    }

    fn edited(self, start: f64, end: f64) -> Option<Self> {
        let preserve = |value: f64, current: f64| {
            if (value - current).abs() <= 0.0005 {
                current
            } else {
                value.clamp(0.0, self.duration)
            }
        };
        let range = Self {
            start: preserve(start, self.start),
            end: preserve(end, self.end),
            ..self
        };
        // The millisecond fields can round the actual last PCM frame's timestamp.
        let range = if (end - self.duration).abs() <= 0.0005 {
            Self {
                end: self.duration,
                ..range
            }
        } else {
            range
        };
        (range.end > range.start).then_some(range)
    }
}

#[derive(Clone, Copy)]
enum Drag {
    Start,
    End,
    Range(f64),
}

fn scaled_peaks(mut peaks: Vec<f32>) -> Vec<f32> {
    let maximum = peaks.iter().copied().fold(0.0_f32, f32::max);
    if maximum > 0.0 {
        for peak in &mut peaks {
            *peak /= maximum;
        }
    }
    peaks
}

struct WaveState {
    range: Range,
    peaks: Vec<f32>,
    playhead: Option<f64>,
    drag: Option<Drag>,
    active: Drag,
    previewing: bool,
}

pub struct Trim {
    preview: Option<Preview>,
    wave: Box<WaveState>,
    chrome: Chrome,
    pub panel: Panel,
    pub wheel: usize,
    pub slot: usize,
    pub rolling: bool,
}

impl Trim {
    pub fn new(
        host: HWND,
        owner: HWND,
        settings: &Settings,
        target: Option<(usize, usize)>,
        duration: f64,
        gain: f64,
    ) -> io::Result<Self> {
        let rolling = target.is_none();
        let (wheel, slot) = target.unwrap_or((
            settings.rolling_buffer_target[0],
            settings.rolling_buffer_target[1],
        ));
        let mut template = if rolling {
            Template::with_size("保存后台录制", 440, 367)
        } else {
            Template::with_size("截取录音", 440, 205)
        };
        label(
            &mut template,
            300,
            "拖动两端手柄调整范围，或在波形上拖选；空格试听 / 停止。",
            [12, 13, 414, 12],
        );
        template.control(0x82, WAVE, "", BASE | TABS, [6, 28, 428, 90]);
        label(&mut template, LOADING, "正在加载波形…", [321, 127, 107, 12]);
        label(&mut template, RANGE_SUMMARY, "", [12, 127, 306, 12]);
        button(&mut template, PRECISE, "▸ 精确调整", [8, 147, 61, 15]);
        label(&mut template, START_LABEL, "开始（秒）", [12, 168, 55, 16]);
        edit(&mut template, START, [69, 165, 73, 16]);
        label(&mut template, END_LABEL, "结束（秒）", [155, 168, 55, 16]);
        edit(&mut template, END, [212, 165, 73, 16]);
        let action_y = if rolling { 341 } else { 183 };
        button(&mut template, PLAY, "试听片段", [12, action_y, 72, 17]);
        if rolling {
            // Keep the wheel large enough to read its labels, with the save fields alongside it.
            label(&mut template, 303, "保存到", [225, 192, 198, 14]);
            combo(&mut template, WHEEL, [225, 207, 137, 80]);
            button(&mut template, ADD, "新增轮盘", [368, 207, 60, 17]);
            label(&mut template, 304, "名称", [225, 230, 198, 14]);
            edit(&mut template, LABEL, [225, 245, 203, 17]);
            label(&mut template, 305, "音量", [225, 268, 198, 14]);
            edit(&mut template, GAIN, [225, 282, 163, 17]);
            label(&mut template, GAIN_VALUE, "100%", [394, 283, 34, 14]);
            template.control(
                0x82,
                PICK,
                "",
                BASE | TABS | 0x0000_0100,
                [12, 189, 205, 128],
            );
            label(&mut template, INFO, "", [12, 322, 416, 14]);
        } else {
            // Regular trims keep their current slot label and volume via the shared action contract.
            edit(&mut template, LABEL, [0, 0, 1, 1]);
            edit(&mut template, GAIN, [0, 0, 1, 1]);
        }
        button(&mut template, SAVE, "保存片段", [302, action_y, 70, 17]);
        button(
            &mut template,
            IDCANCEL as u16,
            "取消",
            [379, action_y, 49, 17],
        );
        let panel = Panel::new(owner, &template, host, TRIM_MESSAGE)?;
        let chrome = Chrome::attach(&panel, true, rolling)?;
        let preview = if rolling {
            Some(Preview::new(panel.child(PICK), false)?)
        } else {
            None
        };
        let mut wave = Box::new(WaveState {
            range: Range {
                start: 0.0,
                end: duration,
                duration,
            },
            peaks: Vec::new(),
            playhead: None,
            drag: None,
            active: Drag::Start,
            previewing: false,
        });
        if unsafe {
            SetWindowSubclass(
                panel.child(WAVE),
                Some(wave_proc),
                1,
                ptr::from_mut(wave.as_mut()) as usize,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        let mut trim = Self {
            preview,
            wave,
            chrome,
            panel,
            wheel,
            slot,
            rolling,
        };
        trim.update_range_fields();
        trim.chrome.gain(gain);
        trim.refresh(settings)?;
        Ok(trim)
    }

    pub fn accepts(&self, message: &MSG) -> bool {
        unsafe {
            if message.message == WM_KEYDOWN
                && message.wParam as u16 == VK_SPACE
                && IsWindowVisible(self.panel.0) != 0
                && (message.hwnd == self.panel.0 || IsChild(self.panel.0, message.hwnd) != 0)
                && ![START, END, LABEL]
                    .iter()
                    .any(|id| message.hwnd == self.panel.child(*id))
                && message.hwnd != self.panel.child(WHEEL)
            {
                if message.lParam & (1 << 30) == 0 {
                    PostMessageW(self.panel.0, WM_COMMAND, usize::from(PLAY), 0);
                }
                return true;
            }
        }
        self.panel.accepts(message)
    }

    pub fn refresh(&mut self, settings: &Settings) -> io::Result<()> {
        if let Some(preview) = &mut self.preview {
            let wheel = settings.wheel(self.wheel).expect("wheel index in range");
            choices(&self.panel, WHEEL, wheel_names(settings), self.wheel);
            let detail = if !wheel.texts[self.slot].trim().is_empty() {
                let text = &wheel.texts[self.slot];
                let preview: String = text.chars().take(38).collect();
                let ellipsis = if text.chars().count() > 38 { "…" } else { "" };
                format!("原有文字：{preview}{ellipsis}（保存将替换）")
            } else if !wheel.slots[self.slot].is_empty() {
                let label = if wheel.labels[self.slot].is_empty() {
                    &wheel.slots[self.slot]
                } else {
                    &wheel.labels[self.slot]
                };
                format!("原有音频：{label}（保存将替换）")
            } else {
                "空槽".into()
            };
            self.panel
                .set_text(INFO, &format!("音频 {} · {detail}", self.slot + 1));
            preview.draw(
                &wheel_labels(&wheel),
                WheelStyle::from_config(&settings.chat_wheel_style),
                self.slot,
            )?;
        }
        Ok(())
    }

    pub fn take_slot(&mut self) -> Option<usize> {
        self.preview.as_mut()?.state.selected.take()
    }

    pub fn update_range_fields(&self) {
        self.panel
            .set_text(START, &format!("{:.3}", self.wave.range.start));
        self.panel
            .set_text(END, &format!("{:.3}", self.wave.range.end));
        self.update_summary();
    }

    fn update_summary(&self) {
        let range = self.wave.range;
        self.panel.set_text(
            RANGE_SUMMARY,
            &format!(
                "起点 {:.3} 秒    终点 {:.3} 秒    片段 {:.3} 秒",
                range.start,
                range.end,
                range.end - range.start
            ),
        );
    }

    pub fn range(&mut self) -> io::Result<Range> {
        let parse = |id| {
            self.panel
                .text(id)
                .trim()
                .parse::<f64>()
                .ok()
                .filter(|value| value.is_finite())
        };
        let (Some(start), Some(end)) = (parse(START), parse(END)) else {
            return Err(io::Error::other("请输入有效的起止时间"));
        };
        let range = self
            .wave
            .range
            .edited(start, end)
            .ok_or_else(|| io::Error::other("请先选择截取区间"))?;
        if self.wave.range != range {
            self.wave.range = range;
            self.update_summary();
            self.invalidate();
        }
        Ok(self.wave.range)
    }

    pub fn set_peaks(&mut self, peaks: Vec<f32>) {
        self.wave.peaks = scaled_peaks(peaks);
        unsafe { ShowWindow(self.panel.child(LOADING), SW_HIDE) };
        self.invalidate();
    }
    pub fn progress(&mut self, seconds: Option<f64>) {
        self.set_previewing(seconds.is_some());
        let next = seconds.filter(|value| value.is_finite()).map(|value| {
            (self.wave.range.start + value).clamp(self.wave.range.start, self.wave.range.end)
        });
        let previous = mem::replace(&mut self.wave.playhead, next);
        let wave = self.panel.child(WAVE);
        let (width, height) = dimensions(wave);
        let plot = Plot::new(width, height);
        let column = |time: f64| plot.x_at(time, self.wave.range.duration);
        let (before, after) = (previous.map(column), next.map(column));
        if before != after {
            // Repaint the old and new cursor strips, leaving the rest of the plot untouched.
            for x in [before, after].into_iter().flatten() {
                let dirty = RECT {
                    left: x - 1,
                    right: x + 2,
                    bottom: plot.bounds.bottom + 1,
                    ..plot.bounds
                };
                unsafe { InvalidateRect(wave, &dirty, 0) };
            }
        }
    }

    pub fn set_previewing(&mut self, active: bool) {
        self.chrome.state.previewing.set(active);
        if active != self.wave.previewing {
            self.wave.previewing = active;
            self.panel.set_text(
                PLAY,
                if active {
                    "停止试听"
                } else {
                    "试听片段"
                },
            );
        }
    }
    fn invalidate(&self) {
        unsafe { InvalidateRect(self.panel.child(WAVE), ptr::null(), 0) };
    }
}

impl Drop for Trim {
    fn drop(&mut self) {
        unsafe { RemoveWindowSubclass(self.panel.child(WAVE), Some(wave_proc), 1) };
    }
}

unsafe extern "system" fn wave_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    _: usize,
    data: usize,
) -> LRESULT {
    unsafe {
        let state = &mut *(data as *mut WaveState);
        let (width, height) = dimensions(hwnd);
        let plot = Plot::new(width, height);
        let x = i32::from(lp as i16);
        let point = POINT {
            x,
            y: i32::from((lp >> 16) as i16),
        };
        let duration = state.range.duration;
        match msg {
            WM_LBUTTONDOWN if plot.contains(point) => {
                let edge = plot.edge_at(state.range, x);
                if let Some(edge) = edge {
                    state.active = edge;
                }
                state.drag = Some(edge.unwrap_or(Drag::Range(plot.time_at(x, duration))));
                SetFocus(hwnd);
                SetCapture(hwnd);
                0
            }
            WM_MOUSEMOVE | WM_LBUTTONUP if state.drag.is_some() => {
                let drag = state.drag.unwrap();
                change_range(hwnd, state, drag, plot.time_at(x, duration));
                if msg == WM_LBUTTONUP {
                    state.drag = None;
                    ReleaseCapture();
                }
                0
            }
            WM_CAPTURECHANGED => {
                state.drag = None;
                0
            }
            WM_SETCURSOR => {
                let mut point = POINT::default();
                GetCursorPos(&mut point);
                ScreenToClient(hwnd, &mut point);
                if plot.contains(point) {
                    let cursor = if plot.edge_at(state.range, point.x).is_some() {
                        IDC_SIZEWE
                    } else {
                        IDC_CROSS
                    };
                    SetCursor(LoadCursorW(ptr::null_mut(), cursor));
                    return 1;
                }
                DefSubclassProc(hwnd, msg, wp, lp)
            }
            WM_GETDLGCODE => (DLGC_WANTARROWS | DLGC_WANTCHARS) as LRESULT,
            WM_KEYDOWN if matches!(wp as u16, VK_LEFT | VK_RIGHT) => {
                let drag = if GetKeyState(i32::from(VK_SHIFT)) < 0 {
                    Drag::End
                } else {
                    state.active
                };
                let at = if matches!(drag, Drag::Start) {
                    state.range.start
                } else {
                    state.range.end
                };
                let delta =
                    (duration / 100.0).min(0.01) * if wp as u16 == VK_LEFT { -1.0 } else { 1.0 };
                change_range(hwnd, state, drag, at + delta);
                0
            }
            WM_KEYDOWN if wp as u16 == VK_SPACE => {
                let action = if state.previewing { STOP } else { PLAY };
                PostMessageW(GetParent(hwnd), WM_COMMAND, usize::from(action), 0);
                0
            }
            WM_SETFOCUS | WM_KILLFOCUS => {
                InvalidateRect(hwnd, ptr::null(), 0);
                DefSubclassProc(hwnd, msg, wp, lp)
            }
            WM_ERASEBKGND => 1,
            WM_PAINT => {
                paint_buffered(hwnd, |dc, rect| {
                    paint_wave(dc, state, plot, rect, GetFocus() == hwnd);
                });
                0
            }
            _ => DefSubclassProc(hwnd, msg, wp, lp),
        }
    }
}

struct Plot {
    bounds: RECT,
}

impl Plot {
    fn new(width: i32, height: i32) -> Self {
        Self {
            bounds: RECT {
                left: 18,
                top: 18,
                right: (width - 18).max(19),
                bottom: (height - 34).max(19),
            },
        }
    }

    fn contains(&self, point: POINT) -> bool {
        let bounds = self.bounds;
        (bounds.left..=bounds.right).contains(&point.x)
            && (bounds.top..=bounds.bottom).contains(&point.y)
    }

    fn x_at(&self, time: f64, duration: f64) -> i32 {
        self.bounds.left
            + (time / duration * f64::from(self.bounds.right - self.bounds.left)).round() as i32
    }

    fn time_at(&self, x: i32, duration: f64) -> f64 {
        (f64::from(x - self.bounds.left) / f64::from(self.bounds.right - self.bounds.left))
            .clamp(0.0, 1.0)
            * duration
    }

    fn edge_at(&self, range: Range, x: i32) -> Option<Drag> {
        let start = x.abs_diff(self.x_at(range.start, range.duration));
        let end = x.abs_diff(self.x_at(range.end, range.duration));
        if start.min(end) > 12 {
            None
        } else if start <= end {
            Some(Drag::Start)
        } else {
            Some(Drag::End)
        }
    }
}

fn change_range(hwnd: HWND, state: &mut WaveState, drag: Drag, at: f64) {
    let range = state.range.dragged(drag, at);
    if state.range != range {
        state.range = range;
        unsafe {
            InvalidateRect(hwnd, ptr::null(), 0);
            PostMessageW(GetParent(hwnd), WM_COMMAND, usize::from(WAVE), 0);
        }
    }
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

fn line(dc: HDC, x: i32, y: i32, x2: i32, y2: i32) {
    unsafe {
        MoveToEx(dc, x, y, ptr::null_mut());
        LineTo(dc, x2, y2);
    }
}

fn paint_wave(dc: HDC, state: &WaveState, plot: Plot, rect: RECT, focused: bool) {
    let bounds = plot.bounds;
    let middle = (bounds.top + bounds.bottom) / 2;
    let duration = state.range.duration;
    let left = plot.x_at(state.range.start, duration);
    let right = plot.x_at(state.range.end, duration);
    fill(dc, rect, PANEL_SURFACE);
    rounded(dc, bounds, 16, Some(ELEVATED), SEPARATOR);
    let saved_dc = unsafe { SaveDC(dc) };
    let clip = unsafe {
        CreateRoundRectRgn(
            bounds.left + 1,
            bounds.top + 1,
            bounds.right,
            bounds.bottom,
            16,
            16,
        )
    };
    unsafe {
        ExtSelectClipRgn(dc, clip, RGN_AND);
        DeleteObject(clip);
    }
    fill(
        dc,
        RECT {
            left,
            right,
            ..bounds
        },
        0x4b3827,
    );
    fill(
        dc,
        RECT {
            right: left,
            ..bounds
        },
        0x201f1e,
    );
    fill(
        dc,
        RECT {
            left: right,
            ..bounds
        },
        0x201f1e,
    );
    with_pen(dc, 1, SEPARATOR, || {
        line(dc, bounds.left, middle, bounds.right, middle);
        for tick in 0..=5 {
            let x = plot.x_at(duration * f64::from(tick) / 5.0, duration);
            line(dc, x, bounds.top, x, bounds.bottom);
        }
    });
    for (inside, color) in [(false, 0x6c6969), (true, PRIMARY)] {
        with_pen(dc, 1, color, || {
            for (index, peak) in state.peaks.iter().enumerate() {
                let x = bounds.left
                    + ((index as f64 + 0.5) * f64::from(bounds.right - bounds.left)
                        / state.peaks.len() as f64) as i32;
                if inside == (left..=right).contains(&x) {
                    let amplitude =
                        (peak * ((bounds.bottom - bounds.top) / 2 - 12).max(0) as f32) as i32;
                    line(dc, x, middle - amplitude, x, middle + amplitude + 1);
                }
            }
        });
    }
    unsafe {
        RestoreDC(dc, saved_dc);
    }
    for tick in 0..=5 {
        let time = duration * f64::from(tick) / 5.0;
        let at = (plot.x_at(time, duration) - 35).clamp(0, (rect.right - 70).max(0));
        let mut label = RECT {
            left: at,
            top: bounds.bottom + 7,
            right: at + 70,
            bottom: bounds.bottom + 27,
        };
        let text = wide(&format!("{time:.2}s"));
        unsafe {
            SetBkMode(dc, TRANSPARENT as i32);
            SetTextColor(dc, MUTED);
            DrawTextW(
                dc,
                text.as_ptr(),
                -1,
                &mut label,
                DT_CENTER | DT_SINGLELINE | DT_NOPREFIX,
            );
        }
    }
    for x in [left, right] {
        with_pen(dc, 2, ACCENT, || line(dc, x, bounds.top, x, bounds.bottom));
        rounded(
            dc,
            RECT {
                left: x - 5,
                right: x + 5,
                top: middle - 14,
                bottom: middle + 14,
            },
            8,
            Some(ACCENT),
            ACCENT,
        );
    }
    if let Some(time) = state.playhead {
        let x = plot.x_at(time, duration);
        with_pen(dc, 2, ERROR, || line(dc, x, bounds.top, x, bounds.bottom));
    }
    if focused {
        rounded(
            dc,
            RECT {
                left: bounds.left - 4,
                top: bounds.top - 4,
                right: bounds.right + 4,
                bottom: bounds.bottom + 4,
            },
            20,
            None,
            0x8d5329,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn moving_gain_repaints_the_old_thumb_and_progress_track() {
        let trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            3.0,
            1.0,
        )
        .unwrap();
        let gain = trim.chrome.state.gain;
        let (width, height) = dimensions(gain);
        let pointer = |x: i32| (x as u16 as isize) | ((height as isize / 2) << 16);
        unsafe {
            SetWindowPos(
                trim.panel.0,
                ptr::null_mut(),
                -32000,
                -32000,
                0,
                0,
                SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW,
            );
            SendMessageW(gain, WM_LBUTTONDOWN, 1, pointer(width - 2));
            ValidateRect(gain, ptr::null());
            SendMessageW(gain, WM_MOUSEMOVE, 1, pointer(2));
        }
        let mut dirty = RECT::default();
        unsafe {
            GetUpdateRect(gain, &mut dirty, 0);
            SendMessageW(gain, WM_LBUTTONUP, 0, pointer(2));
        }
        assert_eq!(
            (dirty.left, dirty.top, dirty.right, dirty.bottom),
            (0, 0, width, height)
        );
    }

    #[test]
    fn trim_gain_click_and_drag_follow_the_visible_track() {
        let trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            3.0,
            1.0,
        )
        .unwrap();
        let gain = trim.chrome.state.gain;
        let (width, height) = dimensions(gain);
        let pointer = |x: i32| (x as u16 as isize) | ((height as isize / 2) << 16);
        unsafe {
            SendMessageW(gain, WM_LBUTTONDOWN, 1, pointer(width / 2));
        }
        assert_eq!(trim.panel.text(GAIN), "150");
        unsafe {
            SendMessageW(gain, WM_MOUSEMOVE, 1, pointer(width));
        }
        assert_eq!(trim.panel.text(GAIN), "300");
        unsafe {
            SendMessageW(gain, WM_MOUSEMOVE, 1, pointer(-1));
            SendMessageW(gain, WM_LBUTTONUP, 0, pointer(-1));
        }
        assert_eq!(trim.panel.text(GAIN), "0");
        assert_ne!(
            unsafe { windows_sys::Win32::UI::Input::KeyboardAndMouse::GetCapture() },
            gain
        );
    }

    #[test]
    fn preview_progress_invalidates_only_the_changed_playhead_pixels() {
        let mut trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            10.0,
            1.0,
        )
        .unwrap();
        let wave = trim.panel.child(WAVE);
        let (width, height) = dimensions(wave);
        let plot = Plot::new(width, height);
        let x = plot.x_at(1.0, 10.0);
        let mut dirty = RECT::default();
        unsafe {
            SetWindowPos(
                trim.panel.0,
                ptr::null_mut(),
                -32000,
                -32000,
                0,
                0,
                SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW,
            );
            ValidateRect(wave, ptr::null());
        }

        trim.progress(Some(1.0));
        assert_ne!(unsafe { GetUpdateRect(wave, &mut dirty, 0) }, 0);
        assert!(dirty.left <= x - 1 && dirty.right > x);
        assert!(
            dirty.right - dirty.left <= 3,
            "only the playhead should be dirty"
        );
        unsafe { ValidateRect(wave, ptr::null()) };

        trim.progress(Some(1.0 + f64::EPSILON));
        assert_eq!(unsafe { GetUpdateRect(wave, &mut dirty, 0) }, 0);

        trim.progress(None);
        assert_ne!(unsafe { GetUpdateRect(wave, &mut dirty, 0) }, 0);
        assert!(dirty.left <= x - 1 && dirty.right > x);
        assert!(dirty.right - dirty.left <= 3);
    }

    #[test]
    fn rolling_trim_shows_a_large_wheel_beside_its_save_fields() {
        let trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            3.0,
            1.0,
        )
        .unwrap();
        let bounds = |id| {
            let mut rect = RECT::default();
            assert_ne!(unsafe { GetWindowRect(trim.panel.child(id), &mut rect) }, 0);
            rect
        };
        let wheel = bounds(PICK);
        let name = bounds(LABEL);
        let info = bounds(INFO);
        assert_eq!(window_text(trim.panel.0), "保存后台录制");
        assert!(wheel.right <= name.left && (wheel.top..wheel.bottom).contains(&name.top));
        assert!(wheel.bottom <= info.top && info.bottom < bounds(SAVE).top);
        assert!(wheel.bottom - wheel.top >= logical_px(trim.panel.0, 220));
    }

    #[test]
    fn selection_orders_and_clamps_dragged_bounds() {
        let mut range = Range {
            start: 0.0,
            end: 3.0,
            duration: 3.0,
        };
        range.set(2.0, -4.0);
        assert_eq!((range.start, range.end), (0.0, 2.0));
        range.set(f64::NAN, 3.0);
        assert_eq!((range.start, range.end), (0.0, 2.0));
    }

    #[test]
    fn trim_handles_keep_a_gap_and_cannot_cross() {
        let range = Range {
            start: 0.25,
            end: 0.75,
            duration: 1.0,
        };
        let start = range.dragged(Drag::Start, 1.0);
        assert_eq!((start.start, start.end), (0.749, 0.75));
        let end = range.dragged(Drag::End, 0.0);
        assert_eq!((end.start, end.end), (0.25, 0.251));
    }

    #[test]
    fn trim_drag_selects_in_both_directions_and_ignores_a_click() {
        let range = Range {
            start: 0.0,
            end: 1.0,
            duration: 1.0,
        };
        assert_eq!(range.dragged(Drag::Range(0.5), 0.5), range);
        let selected = range.dragged(Drag::Range(0.6), 0.2);
        assert_eq!((selected.start, selected.end), (0.2, 0.6));
    }

    #[test]
    fn wave_preview_uses_its_own_peak_and_preserves_silence() {
        assert_eq!(scaled_peaks(vec![0.0, 0.1, 0.2]), [0.0, 0.5, 1.0]);
        assert_eq!(scaled_peaks(vec![0.0]), [0.0]);
    }
}
