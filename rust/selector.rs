use std::{mem::size_of, ptr};

use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM},
    Graphics::Gdi::*,
    UI::{
        Controls::{COMBOBOXINFO, GetComboBoxInfo},
        HiDpi::GetDpiForWindow,
        Shell::{DefSubclassProc, SetWindowSubclass},
        WindowsAndMessaging::*,
    },
};

use crate::desktop::{wide, window_text};
use crate::settings_backdrop::paint_embedded_backdrop;

const SUBCLASS: usize = 1;
const LIST_SUBCLASS: usize = 2;
const SURFACE: u32 = 0x002e2c2c;
const PANEL: u32 = 0x001e1c1c;
const BORDER: u32 = 0x003a3838;
const TEXT: u32 = 0x00f7f5f5;
const MUTED: u32 = 0x009d9898;
const ACCENT: u32 = 0x00ff840a;

/// Paints a closed dropdown as the app's rounded dark field. The native paint
/// (and its blue focus selection) is replaced; the popup list still belongs to
/// the combo, so keyboard and dropdown behaviour stay native. `glass` selects
/// the backdrop under the rounded corners: the settings glass or a solid panel.
pub fn style_selector(hwnd: HWND, glass: bool) {
    unsafe {
        SetWindowSubclass(hwnd, Some(selector_proc), SUBCLASS, usize::from(glass));
        let mut info = COMBOBOXINFO {
            cbSize: size_of::<COMBOBOXINFO>() as u32,
            ..Default::default()
        };
        if GetComboBoxInfo(hwnd, &mut info) != 0 && !info.hwndList.is_null() {
            // The border follows the dropped state, and the combo does not
            // repaint its face for it on its own. The parent's CBN_DROPDOWN and
            // CBN_CLOSEUP notifications cover user-driven transitions; the list
            // hide also sends WM_SHOWWINDOW, which covers programmatic closes.
            SetWindowSubclass(info.hwndList, Some(list_proc), LIST_SUBCLASS, hwnd as usize);
        }
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

unsafe extern "system" fn list_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    combo: usize,
) -> LRESULT {
    unsafe {
        let result = DefSubclassProc(hwnd, message, wparam, lparam);
        if message == WM_SHOWWINDOW {
            InvalidateRect(combo as HWND, ptr::null(), 0);
        }
        result
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
            WM_PRINTCLIENT => {
                let mut client = RECT::default();
                GetClientRect(hwnd, &mut client);
                paint_field(hwnd, wparam as HDC, client, glass != 0);
                0
            }
            // The selector supplies the whole face; the class brush would flash
            // light underneath it.
            WM_ERASEBKGND => 1,
            // A closed combo must not change its selection on a stray wheel.
            WM_MOUSEWHEEL if SendMessageW(hwnd, CB_GETDROPPEDSTATE, 0, 0) == 0 => 0,
            _ => DefSubclassProc(hwnd, message, wparam, lparam),
        }
    }
}

fn paint(hwnd: HWND, glass: bool) -> LRESULT {
    unsafe {
        let mut paint = PAINTSTRUCT::default();
        let dc = BeginPaint(hwnd, &mut paint);
        let mut client = RECT::default();
        GetClientRect(hwnd, &mut client);
        paint_field(hwnd, dc, client, glass);
        EndPaint(hwnd, &paint);
        0
    }
}

/// The accent marks the open dropdown; at rest the border stays neutral so no
/// focus ring outlives a selection.
fn border(dropped: bool) -> u32 {
    if dropped { ACCENT } else { BORDER }
}

fn paint_field(hwnd: HWND, dc: HDC, client: RECT, glass: bool) {
    unsafe {
        if glass {
            paint_embedded_backdrop(hwnd, dc);
        } else {
            fill(dc, client, PANEL);
        }
        // Only the combo's own WM_PAINT draws the field. The WM_DRAWITEM face
        // request arrives with a clip region inset from the field, which left
        // corner fragments of the border instead of one complete frame.
        let border = border(SendMessageW(hwnd, CB_GETDROPPEDSTATE, 0, 0) != 0);
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

#[cfg(test)]
mod tests {
    use super::{ACCENT, BORDER, border};

    #[test]
    fn accent_is_reserved_for_the_open_dropdown() {
        assert_eq!(border(true), ACCENT);
        assert_eq!(border(false), BORDER);
    }
}
