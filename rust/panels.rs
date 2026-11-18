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
    ODT_BUTTON, ODT_COMBOBOX, ODT_STATIC, TB_ENDTRACK, TBM_SETPAGESIZE, TBM_SETRANGE, TBS_NOTICKS,
    TRACKBAR_CLASSW, WM_MOUSELEAVE,
};
use windows_sys::Win32::UI::HiDpi::GetDpiForWindow;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetFocus, IsWindowEnabled, TME_LEAVE, TRACKMOUSEEVENT, TrackMouseEvent,
};
use windows_sys::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

use crate::config::Settings;
use crate::desktop::{frame_edit_text, paint_buffered, paint_buffered_rect, wide, window_text};
use crate::dialog::{Panel, Template};
use crate::selector::style_selector;
use crate::settings_backdrop::paint_embedded_backdrop;
use crate::slider::{self, TBM_GETPOS};

mod editor;
mod preview;
mod trim;

pub use editor::Editor;
pub use preview::wheel_labels;
pub use trim::Trim;

use editor::{editor_card, layout_editor};

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
    ui_font: RefCell<String>,
}

impl Chrome {
    pub(super) fn set_ui_font(&self, font: &str) {
        self.state.ui_font.replace(font.to_owned());
    }

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
            ui_font: RefCell::new(String::new()),
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
        set_gain_readout(self.hwnd, percent);
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

fn set_gain_readout(hwnd: HWND, value: i32) {
    let percent = value.to_string();
    let edit = unsafe { GetDlgItem(hwnd, i32::from(GAIN)) };
    // A drag's final notification repeats the last value and needs no visual update.
    if window_text(edit) != percent {
        set_control_text(hwnd, GAIN, &percent);
        set_control_text(hwnd, GAIN_VALUE, &format!("{percent}%"));
    }
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
    let enabled = unsafe { IsWindowEnabled(hwnd) != 0 };
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
    if enabled {
        rounded(dc, progress, 6, Some(ACCENT), ACCENT);
    }
    let thumb = RECT {
        left: x - 8,
        top: y - 8,
        right: x + 8,
        bottom: y + 8,
    };
    let thumb_color = if enabled { PRIMARY } else { MUTED };
    let ring = if enabled && unsafe { GetFocus() } == hwnd {
        ACCENT
    } else {
        thumb_color
    };
    rounded(dc, thumb, 16, Some(thumb_color), ring);
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
            WM_SETFOCUS | WM_KILLFOCUS | WM_KEYDOWN | WM_MOUSEWHEEL | WM_ENABLE => {
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
                // The gain readout supplies its own buffered background in WM_DRAWITEM.
                let card = !state.trim && matches!(control, INFO | GAIN_SLIDER | 302..=305);
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
                    ODT_STATIC if item.CtlID == u32::from(GAIN_VALUE) => {
                        paint_buffered_rect(item.hDC, item.rcItem, |dc, rect| {
                            draw_label(item.hwndItem, dc, rect, state.trim);
                        });
                    }
                    ODT_STATIC => draw_label(item.hwndItem, item.hDC, item.rcItem, state.trim),
                    ODT_COMBOBOX if item.itemState & ODS_COMBOBOXEDIT == 0 => draw_combo_item(item),
                    _ => return DefSubclassProc(hwnd, msg, wp, lp),
                }
                1
            }
            WM_HSCROLL if lp != 0 && lp as HWND == state.gain => {
                let value = SendMessageW(state.gain, TBM_GETPOS, 0, 0) as i32;
                set_gain_readout(hwnd, value);
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
                let ui_font = state.ui_font.borrow().clone();
                if let Some(name) =
                    crate::dialog::prompt(hwnd, "重命名轮盘", "轮盘名称", &current, false, &ui_font)
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
        // Keep the collapsed control quiet while retaining visible mouse and keyboard feedback.
        (
            if hovering { ELEVATED } else { PANEL_SURFACE },
            if focus { ACCENT } else { PANEL_SURFACE },
        )
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
