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
        Controls::{EM_REPLACESEL, EM_SCROLLCARET, EM_SETSEL},
        HiDpi::GetDpiForSystem,
        WindowsAndMessaging::{
            CREATESTRUCTW, CreateWindowExW, DefWindowProcW, DestroyWindow, ES_AUTOVSCROLL,
            ES_MULTILINE, ES_READONLY, GWLP_USERDATA, GetClientRect, GetCursorPos,
            GetWindowLongPtrW, MoveWindow, RegisterClassW, SW_HIDE, SW_SHOW, SW_SHOWNORMAL,
            SendMessageW, SetForegroundWindow, SetWindowLongPtrW, ShowWindow, WM_CLOSE,
            WM_CTLCOLOREDIT, WM_CTLCOLORSTATIC, WM_ERASEBKGND, WM_NCCREATE, WM_NCDESTROY,
            WM_SETFONT, WM_SIZE, WNDCLASSW, WS_CHILD, WS_CLIPCHILDREN, WS_OVERLAPPEDWINDOW,
            WS_VISIBLE, WS_VSCROLL,
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
}

pub struct LogWindow {
    hwnd: HWND,
}

impl LogWindow {
    pub fn open(owner: HWND) -> io::Result<Self> {
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
        let (width, height) = (px(800), px(500));
        let origin = centered_origin(width, height);
        let state = Box::new(State {
            edit: ptr::null_mut(),
            font: ptr::null_mut(),
            brush: ptr::null_mut(),
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
        let state = unsafe { &mut *(GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State) };
        let family = w!("Consolas");
        state.font = unsafe {
            CreateFontW(
                -px(14),
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
                family,
            )
        };
        state.brush = unsafe { CreateSolidBrush(BACKGROUND) };
        state.edit = unsafe {
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
            SendMessageW(state.edit, WM_SETFONT, state.font as usize, 1);
            // EM_SETBKGNDCOLOR prevents the default white edit background on first paint.
            SendMessageW(state.edit, 0x0433, 0, BACKGROUND as isize);
            ShowWindow(hwnd, SW_SHOWNORMAL);
            UpdateWindow(hwnd);
            SetForegroundWindow(hwnd);
        }
        Ok(Self { hwnd })
    }

    pub fn set_lines(&self, lines: &[String]) {
        self.write(&lines.join("\r\n"));
    }

    pub fn append(&self, line: &str) {
        self.write(&format!("\r\n{line}"));
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

fn centered_origin(width: i32, height: i32) -> POINT {
    let mut cursor = POINT::default();
    unsafe { GetCursorPos(&mut cursor) };
    let monitor = unsafe { MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST) };
    let mut info = MONITORINFO {
        cbSize: mem::size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    if unsafe { GetMonitorInfoW(monitor, &mut info) } != 0 {
        let work = info.rcWork;
        POINT {
            x: work.left + ((work.right - work.left - width) / 2).max(0),
            y: work.top + ((work.bottom - work.top - height) / 2).max(0),
        }
    } else {
        POINT::default()
    }
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
                1
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
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
            _ => DefWindowProcW(hwnd, message, wparam, lparam),
        }
    }
}
