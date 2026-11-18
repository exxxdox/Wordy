use std::ptr;

use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM},
    Graphics::Gdi::*,
    UI::{
        HiDpi::GetDpiForWindow,
        Input::KeyboardAndMouse::GetFocus,
        Shell::{DefSubclassProc, SetWindowSubclass},
        WindowsAndMessaging::*,
    },
};

use crate::desktop::{wide, window_text};
use crate::settings_window::paint_embedded_backdrop;

const SUBCLASS: usize = 1;
const SURFACE: u32 = 0x002e_2c2c;
const PANEL: u32 = 0x001e_1c1c;
const BORDER: u32 = 0x003a_38_38;
const TEXT: u32 = 0x00f7_f5f5;
const MUTED: u32 = 0x009d_9898;
const ACCENT: u32 = 0x00ff_840a;

/// Paints a closed dropdown as the app's rounded dark field. The native paint
/// (and its blue focus selection) is replaced; the popup list still belongs to
/// the combo, so keyboard and dropdown behaviour stay native. `glass` selects
/// the backdrop under the rounded corners: the settings glass or a solid panel.
pub fn style_selector(hwnd: HWND, glass: bool) {
    unsafe {
        SetWindowSubclass(hwnd, Some(selector_proc), SUBCLASS, usize::from(glass));
        SetWindowPos(
            hwnd,
            ptr::null_mut(),
            0,
            0,
            0,
            0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
        );
    }
}

fn dpi(hwnd: HWND) -> i32 {
    unsafe { GetDpiForWindow(hwnd) }.max(96) as i32
}

fn scaled(hwnd: HWND, units: i32) -> i32 {
    (units * dpi(hwnd) + 48) / 96
}

unsafe extern "system" fn selector_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    glass: usize,
) -> LRESULT {
    unsafe {
        match message {
            // The native frame is light; fill it and let the client paint carry
            // the rounded field. Unlike a window region, this keeps the parent
            // from leaving stale black pixels in the corners.
            WM_NCPAINT => {
                let mut size = RECT::default();
                GetWindowRect(hwnd, &mut size);
                let dc = GetWindowDC(hwnd);
                if !dc.is_null() {
                    fill(
                        dc,
                        RECT {
                            left: 0,
                            top: 0,
                            right: size.right - size.left,
                            bottom: size.bottom - size.top,
                        },
                        SURFACE,
                    );
                    ReleaseDC(hwnd, dc);
                }
                0
            }
            WM_PAINT => paint(hwnd, glass != 0),
            WM_SETFOCUS | WM_KILLFOCUS => {
                let result = DefSubclassProc(hwnd, message, wparam, lparam);
                InvalidateRect(hwnd, ptr::null(), 0);
                result
            }
            _ => DefSubclassProc(hwnd, message, wparam, lparam),
        }
    }
}

unsafe fn paint(hwnd: HWND, glass: bool) -> LRESULT {
    unsafe {
        let mut paint = PAINTSTRUCT::default();
        let dc = BeginPaint(hwnd, &mut paint);
        let mut client = RECT::default();
        GetClientRect(hwnd, &mut client);
        if glass {
            paint_embedded_backdrop(hwnd, dc);
        } else {
            fill(dc, client, PANEL);
        }
        let border = if GetFocus() == hwnd { ACCENT } else { BORDER };
        rounded(dc, client, scaled(hwnd, 10), Some(SURFACE), border);
        let font = SendMessageW(hwnd, WM_GETFONT, 0, 0) as HFONT;
        let previous = if font.is_null() {
            ptr::null_mut()
        } else {
            SelectObject(dc, font)
        };
        SetBkMode(dc, TRANSPARENT as i32);
        SetTextColor(dc, TEXT);
        let mut text = wide(&window_text(hwnd));
        let mut area = RECT {
            left: scaled(hwnd, 10),
            top: client.top,
            right: client.right - scaled(hwnd, 28),
            bottom: client.bottom,
        };
        DrawTextW(
            dc,
            text.as_mut_ptr(),
            -1,
            &mut area,
            DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX | DT_END_ELLIPSIS,
        );
        let (x, y) = (
            client.right - scaled(hwnd, 15),
            (client.top + client.bottom) / 2,
        );
        let pen = CreatePen(PS_SOLID, scaled(hwnd, 1).max(1), MUTED);
        let old = SelectObject(dc, pen);
        MoveToEx(
            dc,
            x - scaled(hwnd, 4),
            y - scaled(hwnd, 2),
            ptr::null_mut(),
        );
        LineTo(dc, x, y + scaled(hwnd, 2));
        MoveToEx(dc, x, y + scaled(hwnd, 2), ptr::null_mut());
        LineTo(dc, x + scaled(hwnd, 5), y - scaled(hwnd, 3));
        SelectObject(dc, old);
        DeleteObject(pen);
        if !previous.is_null() {
            SelectObject(dc, previous);
        }
        EndPaint(hwnd, &paint);
        0
    }
}

fn rounded(dc: HDC, rect: RECT, radius: i32, background: Option<u32>, border: u32) {
    unsafe {
        let brush = background.map_or_else(
            || GetStockObject(NULL_BRUSH),
            |color| CreateSolidBrush(color),
        );
        let pen = CreatePen(PS_SOLID, 1, border);
        let old_brush = SelectObject(dc, brush);
        let old_pen = SelectObject(dc, pen);
        RoundRect(
            dc,
            rect.left,
            rect.top,
            rect.right,
            rect.bottom,
            radius * 2,
            radius * 2,
        );
        SelectObject(dc, old_pen);
        SelectObject(dc, old_brush);
        DeleteObject(pen);
        if background.is_some() {
            DeleteObject(brush);
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
