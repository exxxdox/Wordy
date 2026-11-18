use std::{io, mem, ptr};

use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, WPARAM},
    Graphics::Gdi::{
        BLACK_BRUSH, CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS, CreateFontW, CreateSolidBrush,
        DEFAULT_CHARSET, DEFAULT_PITCH, DeleteObject, FW_NORMAL, GetMonitorInfoW, GetStockObject,
        HBRUSH, HFONT, HGDIOBJ, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromPoint,
        OUT_DEFAULT_PRECIS, SetBkColor, SetTextColor, UpdateWindow,
    },
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        Controls::{EM_LINEINDEX, EM_REPLACESEL, EM_SCROLLCARET, EM_SETSEL},
        HiDpi::{GetDpiForSystem, GetDpiForWindow},
        WindowsAndMessaging::{
            CREATESTRUCTW, CreateWindowExW, DefWindowProcW, DestroyIcon, DestroyWindow,
            ES_AUTOVSCROLL, ES_MULTILINE, ES_READONLY, GWLP_USERDATA, GetClientRect, GetCursorPos,
            GetWindowLongPtrW, HICON, ICON_BIG, ICON_SMALL, MoveWindow, RegisterClassW, SW_HIDE,
            SW_SHOW, SW_SHOWNORMAL, SendMessageW, SetForegroundWindow, SetWindowLongPtrW,
            ShowWindow, WM_CLOSE, WM_CTLCOLOREDIT, WM_CTLCOLORSTATIC, WM_ERASEBKGND, WM_NCCREATE,
            WM_NCDESTROY, WM_SETFONT, WM_SETICON, WM_SIZE, WNDCLASSW, WS_CHILD, WS_CLIPCHILDREN,
            WS_OVERLAPPEDWINDOW, WS_VISIBLE, WS_VSCROLL,
        },
    },
};
use windows_sys::core::w;

const CLASS: windows_sys::core::PCWSTR = w!("Wordy.Rust.Log");
const TITLE: windows_sys::core::PCWSTR = w!("日志窗口");
const BACKGROUND: u32 = 0x0012_1212;
const FOREGROUND: u32 = 0x00e8_e8e8;

struct State {
    edit: HWND,
    font: HFONT,
    brush: HBRUSH,
    icon: HICON,
}

pub struct LogWindow {
    hwnd: HWND,
}

impl LogWindow {
    pub fn open(owner: HWND, font_family: &str) -> io::Result<Self> {
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        let class = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: instance,
            lpszClassName: CLASS,
            // Painted before the list exists, so the window never flashes white.
            hbrBackground: unsafe { GetStockObject(BLACK_BRUSH) as HBRUSH },
            ..Default::default()
        };
        // The class lives for the process, so a duplicate registration is harmless.
        unsafe { RegisterClassW(&class) };

        let scale = unsafe { GetDpiForSystem() }.max(96) as i32;
        let px = |units: i32| (units * scale + 48) / 96;
        let (origin, width, height) = centered_frame(px(1080), px(720));
        let state = Box::new(State {
            edit: ptr::null_mut(),
            font: ptr::null_mut(),
            brush: ptr::null_mut(),
            icon: ptr::null_mut(),
        });
        let hwnd = unsafe {
            CreateWindowExW(
                0,
                CLASS,
                TITLE,
                WS_OVERLAPPEDWINDOW | WS_CLIPCHILDREN,
                origin.x,
                origin.y,
                width,
                height,
                owner,
                ptr::null_mut(),
                instance,
                Box::into_raw(state).cast(),
            )
        };
        if hwnd.is_null() {
            return Err(io::Error::last_os_error());
        }
        // Keep only a raw pointer: the state is owned by the window and the
        // message handler may re-derive references while these calls run.
        let state = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State };
        let font = log_font(font_family, -px(14));
        let brush = unsafe { CreateSolidBrush(BACKGROUND) };
        let edit = unsafe {
            CreateWindowExW(
                0,
                w!("EDIT"),
                w!(""),
                WS_CHILD
                    | WS_VISIBLE
                    | WS_VSCROLL
                    | ES_MULTILINE as u32
                    | ES_READONLY as u32
                    | ES_AUTOVSCROLL as u32,
                0,
                0,
                width,
                height,
                hwnd,
                ptr::null_mut(),
                instance,
                ptr::null(),
            )
        };
        unsafe {
            (*state).font = font;
            (*state).brush = brush;
            (*state).edit = edit;
        }
        unsafe {
            SendMessageW(edit, WM_SETFONT, font as usize, 1);
            // EM_SETBKGNDCOLOR prevents the default white edit background on first paint.
            SendMessageW(edit, 0x0433, 0, BACKGROUND as isize);
            if let Ok(icon) = crate::desktop::wordy_icon() {
                (*state).icon = icon;
                SendMessageW(hwnd, WM_SETICON, ICON_BIG as usize, icon as isize);
                SendMessageW(hwnd, WM_SETICON, ICON_SMALL as usize, icon as isize);
            }
            ShowWindow(hwnd, SW_SHOWNORMAL);
            UpdateWindow(hwnd);
            SetForegroundWindow(hwnd);
        }
        Ok(Self { hwnd })
    }

    pub fn set_font(&self, font_family: &str) {
        let dpi = unsafe { GetDpiForWindow(self.hwnd) }.max(96) as i32;
        let height = -((14 * dpi + 48) / 96);
        let font = log_font(font_family, height);
        if font.is_null() {
            return;
        }
        let state = unsafe { GetWindowLongPtrW(self.hwnd, GWLP_USERDATA) as *mut State };
        let edit = unsafe { (*state).edit };
        if edit.is_null() {
            unsafe { DeleteObject(font as HGDIOBJ) };
            return;
        }
        unsafe {
            SendMessageW(edit, WM_SETFONT, font as usize, 1);
            // Re-derive only after the message returns: the handler may have
            // created another reference to the same state during the call.
            let old = std::mem::replace(&mut (*state).font, font);
            if !old.is_null() {
                DeleteObject(old as HGDIOBJ);
            }
        }
    }

    pub fn set_lines(&self, lines: &[String]) {
        self.write(&lines.join("\r\n"));
    }

    pub fn append(&self, line: &str) {
        self.write(&format!("\r\n{line}"));
    }

    pub fn trim_front(&self) {
        let state = unsafe { &*(GetWindowLongPtrW(self.hwnd, GWLP_USERDATA) as *const State) };
        if state.edit.is_null() {
            return;
        }
        let next = unsafe { SendMessageW(state.edit, EM_LINEINDEX, 1, 0) };
        if next <= 0 {
            return;
        }
        let empty = crate::desktop::wide("");
        unsafe {
            SendMessageW(state.edit, EM_SETSEL, 0, next as isize);
            SendMessageW(state.edit, EM_REPLACESEL, 0, empty.as_ptr() as isize);
        }
    }

    fn write(&self, text: &str) {
        let state = unsafe { &*(GetWindowLongPtrW(self.hwnd, GWLP_USERDATA) as *const State) };
        if state.edit.is_null() {
            return;
        }
        let wide = crate::desktop::wide(text);
        unsafe {
            SendMessageW(state.edit, EM_SETSEL, usize::MAX, -1);
            SendMessageW(state.edit, EM_REPLACESEL, 0, wide.as_ptr() as isize);
            // Keep the newest line visible; the buffer covers the whole session.
            SendMessageW(state.edit, EM_SETSEL, usize::MAX, -1);
            SendMessageW(state.edit, EM_SCROLLCARET, 0, 0);
        }
    }

    pub fn show_existing(&self) {
        unsafe {
            ShowWindow(self.hwnd, SW_SHOW);
            SetForegroundWindow(self.hwnd);
        }
    }
}

impl Drop for LogWindow {
    fn drop(&mut self) {
        unsafe { DestroyWindow(self.hwnd) };
    }
}

fn log_font(font_family: &str, height: i32) -> HFONT {
    let family = crate::desktop::wide(if font_family.is_empty() {
        "Consolas"
    } else {
        font_family
    });
    unsafe {
        CreateFontW(
            height,
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

/// Centres the window on the cursor monitor and never lets it exceed the work
/// area, so the larger default still fits small laptop screens.
fn centered_frame(width: i32, height: i32) -> (POINT, i32, i32) {
    let mut cursor = POINT::default();
    unsafe { GetCursorPos(&mut cursor) };
    let monitor = unsafe { MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST) };
    let mut info = MONITORINFO {
        cbSize: mem::size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    if unsafe { GetMonitorInfoW(monitor, &mut info) } == 0 {
        return (POINT::default(), width, height);
    }
    let work = info.rcWork;
    let width = width.min(work.right - work.left);
    let height = height.min(work.bottom - work.top);
    (
        POINT {
            x: work.left + ((work.right - work.left - width) / 2).max(0),
            y: work.top + ((work.bottom - work.top - height) / 2).max(0),
        },
        width,
        height,
    )
}

unsafe extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    unsafe {
        match message {
            WM_NCCREATE => {
                let create = &*(lparam as *const CREATESTRUCTW);
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, create.lpCreateParams as isize);
                // Let DefWindowProc store the creation title; returning 1 here
                // without it left the title bar empty.
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
            WM_SIZE => {
                let mut client = RECT::default();
                GetClientRect(hwnd, &mut client);
                let state = &*(GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const State);
                if !state.edit.is_null() {
                    MoveWindow(state.edit, 0, 0, client.right, client.bottom, 1);
                }
                0
            }
            WM_CTLCOLOREDIT | WM_CTLCOLORSTATIC => {
                let state = &*(GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const State);
                SetTextColor(wparam as _, FOREGROUND);
                SetBkColor(wparam as _, BACKGROUND);
                state.brush as isize
            }
            WM_ERASEBKGND => 1,
            WM_CLOSE => {
                // Closing the log window keeps the session buffer alive for reopening.
                ShowWindow(hwnd, SW_HIDE);
                0
            }
            WM_NCDESTROY => {
                let state = Box::from_raw(GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State);
                if !state.font.is_null() {
                    DeleteObject(state.font as HGDIOBJ);
                }
                if !state.brush.is_null() {
                    DeleteObject(state.brush as HGDIOBJ);
                }
                if !state.icon.is_null() {
                    DestroyIcon(state.icon);
                }
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
            _ => DefWindowProcW(hwnd, message, wparam, lparam),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows_sys::Win32::UI::Controls::EM_GETLINECOUNT;

    #[test]
    fn changing_the_log_font_replaces_the_handle() {
        let window = LogWindow::open(ptr::null_mut(), "").unwrap();
        let before =
            unsafe { (*(GetWindowLongPtrW(window.hwnd, GWLP_USERDATA) as *const State)).font };
        window.set_font("Consolas");
        let after =
            unsafe { (*(GetWindowLongPtrW(window.hwnd, GWLP_USERDATA) as *const State)).font };
        assert!(!before.is_null());
        assert!(!after.is_null());
        assert_ne!(before, after);
    }

    #[test]
    fn trimming_removes_only_the_oldest_line() {
        let window = LogWindow::open(ptr::null_mut(), "").unwrap();
        window.set_lines(&["one".into(), "two".into(), "three".into()]);
        let state = unsafe { &*(GetWindowLongPtrW(window.hwnd, GWLP_USERDATA) as *const State) };
        assert_eq!(
            unsafe { SendMessageW(state.edit, EM_GETLINECOUNT, 0, 0) },
            3
        );
        window.trim_front();
        assert_eq!(
            unsafe { SendMessageW(state.edit, EM_GETLINECOUNT, 0, 0) },
            2
        );
    }
}
