use std::{mem::size_of, ptr};

use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, WPARAM},
    Graphics::Gdi::*,
    UI::{
        Controls::{
            DRAWITEMSTRUCT, ODS_COMBOBOXEDIT, ODS_DISABLED, ODS_FOCUS, ODS_SELECTED, ODT_BUTTON,
            ODT_COMBOBOX, ODT_STATIC, WM_MOUSELEAVE,
        },
        Input::KeyboardAndMouse::{
            GetFocus, IsWindowEnabled, TME_LEAVE, TRACKMOUSEEVENT, TrackMouseEvent,
        },
        Shell::DefSubclassProc,
        WindowsAndMessaging::*,
    },
};

use super::{
    CONTROL_BG, CONTROL_KIND, Field, Kind, MUTED, PRIMARY, SELECTED_TAB, checked, choice, send,
    text, wide,
};
use crate::{
    desktop::paint_buffered,
    selector::paint_field,
    settings_backdrop::{fill, outline, paint_embedded_backdrop, rgb, round, stroke},
    slider,
};

const CONTROL_BORDER: u32 = rgb(72, 72, 74);
const HOVER_ACCENT: u32 = rgb(64, 156, 255);
const ACCENT: u32 = rgb(10, 132, 255);
// Marks a control whose pointer-entered repaint already ran until WM_MOUSELEAVE.
const HOVER: windows_sys::core::PCWSTR = windows_sys::core::w!("Wordy.Settings.Hover");
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

pub(super) fn paint_item(parent: HWND, item: &DRAWITEMSTRUCT) {
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
        } else {
            // The combo delegates its closed face to the parent. Drawing it here
            // covers selection changes and page switches, which otherwise leave
            // the field blank until a hover repaint. rcItem excludes the native
            // arrow button, so paint the whole client like the WM_PAINT path;
            // using rcItem shifts the field and leaves a second border/chevron.
            let mut client = RECT::default();
            unsafe { GetClientRect(hwnd, &mut client) };
            paint_field(hwnd, dc, client, true);
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

pub(super) fn combo_text(hwnd: HWND, index: usize) -> String {
    let len = send(hwnd, CB_GETLBTEXTLEN, index, 0);
    if len < 0 {
        return String::new();
    }
    let mut units = vec![0; len as usize + 1];
    let written = send(hwnd, CB_GETLBTEXT, index, units.as_mut_ptr() as LPARAM);
    String::from_utf16_lossy(&units[..written.max(0) as usize])
}

pub(super) unsafe extern "system" fn style_control(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    kind: usize,
) -> LRESULT {
    let combo = kind == Kind::Combo as usize;
    let slider = kind == Kind::Slider as usize;
    if slider && let Some(result) = slider::handle_pointer(hwnd, message, lparam) {
        return result;
    }
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
        // Combo faces never change on hover, and slider faces only on drag, so
        // those never repaint from a mouse move. Edits, checks and buttons do
        // show a hover state, but repainting on every move flickers; paint once
        // when the pointer enters and once when it leaves.
        WM_MOUSEMOVE if !combo && !slider => {
            let mut tracking = TRACKMOUSEEVENT {
                cbSize: size_of::<TRACKMOUSEEVENT>() as u32,
                dwFlags: TME_LEAVE,
                hwndTrack: hwnd,
                dwHoverTime: 0,
            };
            unsafe {
                TrackMouseEvent(&mut tracking);
                if GetPropW(hwnd, HOVER).is_null() {
                    SetPropW(hwnd, HOVER, 1 as _);
                    InvalidateRect(hwnd, ptr::null(), 0);
                }
            }
        }
        WM_MOUSEWHEEL if slider || (combo && send(hwnd, CB_GETDROPPEDSTATE, 0, 0) == 0) => {
            return 0;
        }
        WM_KEYDOWN if slider => unsafe {
            let result = DefSubclassProc(hwnd, message, wparam, lparam);
            InvalidateRect(hwnd, ptr::null(), 0);
            return result;
        },
        WM_PAINT if slider => {
            paint_buffered(hwnd, |dc, rect| paint_slider(hwnd, dc, rect));
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
            if !slider {
                paint_embedded_backdrop(hwnd, dc);
            }
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
    if message == WM_MOUSELEAVE {
        // Hover state ends here; the repaint below restores the resting border.
        unsafe { RemovePropW(hwnd, HOVER) };
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
    paint_embedded_backdrop(hwnd, dc);
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
