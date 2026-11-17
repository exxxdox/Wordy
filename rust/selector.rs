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

const SUBCLASS: usize = 1;
const BACKGROUND: u32 = 0x002e_2c2c;
const BORDER: u32 = 0x003a_38_38;
const TEXT: u32 = 0x00f7_f5f5;
const MUTED: u32 = 0x009d_9898;
const ACCENT: u32 = 0x00ff_840a;

/// Paints a closed dropdown as the app's rounded dark field. The native paint
/// (and its blue focus selection) is replaced; the popup list still belongs to
/// the combo, so keyboard and dropdown behaviour stay native.
pub fn style_selector(hwnd: HWND) {
    unsafe {
        SetWindowSubclass(hwnd, Some(selector_proc), SUBCLASS, 0);
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
    _data: usize,
) -> LRESULT {
    unsafe {
        match message {
            WM_SIZE => {
                let mut size = RECT::default();
                GetWindowRect(hwnd, &mut size);
                let radius = scaled(hwnd, 10) * 2;
                let region = CreateRoundRectRgn(
                    0,
                    0,
                    size.right - size.left + 1,
                    size.bottom - size.top + 1,
                    radius,
                    radius,
                );
                SetWindowRgn(hwnd, region, 1);
                DefSubclassProc(hwnd, message, wparam, lparam)
            }
            WM_NCPAINT => {
                // The theme frame is light; fill the frame area and suppress it.
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
                        BACKGROUND,
                    );
                    ReleaseDC(hwnd, dc);
                }
                0
            }
            WM_PAINT => paint(hwnd),
            WM_SETFOCUS | WM_KILLFOCUS => {
                let result = DefSubclassProc(hwnd, message, wparam, lparam);
                InvalidateRect(hwnd, ptr::null(), 0);
                result
            }
            _ => DefSubclassProc(hwnd, message, wparam, lparam),
        }
    }
}

unsafe fn paint(hwnd: HWND) -> LRESULT {
    unsafe {
        let mut paint = PAINTSTRUCT::default();
        let dc = BeginPaint(hwnd, &mut paint);
        let mut client = RECT::default();
        GetClientRect(hwnd, &mut client);
        fill(dc, client, BACKGROUND);
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
        border(hwnd);
        0
    }
}

unsafe fn border(hwnd: HWND) {
    unsafe {
        let mut size = RECT::default();
        GetWindowRect(hwnd, &mut size);
        let dc = GetWindowDC(hwnd);
        if dc.is_null() {
            return;
        }
        let color = if GetFocus() == hwnd { ACCENT } else { BORDER };
        let (width, height) = (size.right - size.left, size.bottom - size.top);
        let radius = scaled(hwnd, 10) * 2;
        let pen = CreatePen(PS_SOLID, scaled(hwnd, 1).max(1), color);
        let old_pen = SelectObject(dc, pen);
        let old_brush = SelectObject(dc, GetStockObject(NULL_BRUSH));
        RoundRect(dc, 0, 0, width, height, radius, radius);
        SelectObject(dc, old_brush);
        SelectObject(dc, old_pen);
        DeleteObject(pen);
        ReleaseDC(hwnd, dc);
    }
}

fn fill(dc: HDC, rect: RECT, color: u32) {
    unsafe {
        let brush = CreateSolidBrush(color);
        FillRect(dc, &rect, brush);
        DeleteObject(brush);
    }
}
