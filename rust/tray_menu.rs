use std::{io, mem, ptr};

use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, WPARAM},
    Graphics::Gdi::{
        BLACK_BRUSH, BeginPaint, CLEARTYPE_QUALITY, CLIP_DEFAULT_PRECIS, CreateFontW,
        CreateRoundRectRgn, CreateSolidBrush, DEFAULT_CHARSET, DEFAULT_PITCH, DT_CALCRECT, DT_LEFT,
        DT_NOPREFIX, DT_SINGLELINE, DT_VCENTER, DeleteObject, DrawTextW, EndPaint, FW_NORMAL,
        FillRect, FrameRect, GetDC, GetMonitorInfoW, GetStockObject, GetTextMetricsW, HBRUSH,
        HFONT, HGDIOBJ, InvalidateRect, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromPoint,
        OUT_DEFAULT_PRECIS, PAINTSTRUCT, ReleaseDC, SelectObject, SetBkMode, SetTextColor,
        SetWindowRgn, TEXTMETRICW, TRANSPARENT,
    },
    System::LibraryLoader::GetModuleHandleW,
    UI::{
        Controls::WM_MOUSELEAVE,
        HiDpi::GetDpiForSystem,
        Input::KeyboardAndMouse::{
            ReleaseCapture, SetCapture, TME_LEAVE, TRACKMOUSEEVENT, TrackMouseEvent,
        },
        WindowsAndMessaging::*,
    },
};
use windows_sys::core::w;

use crate::desktop::{TRAY_COMMAND, TRAY_EXIT, TRAY_LOGS, TRAY_OVERLAY, TRAY_SETTINGS, wide};

const CLASS: windows_sys::core::PCWSTR = w!("Wordy.Rust.TrayMenu");
const BACKGROUND: u32 = 0x001c_1d1e;
const HOVER: u32 = 0x002e_2f31;
const BORDER: u32 = 0x003a_3b3e;
const FOREGROUND: u32 = 0x00f5_f5f7;

struct State {
    host: HWND,
    font: HFONT,
    hover: Option<usize>,
    items: Vec<(usize, &'static str)>,
}

impl State {
    fn row_height(&self) -> i32 {
        let mut metrics = TEXTMETRICW::default();
        let dc = unsafe { GetDC(ptr::null_mut()) };
        let old = unsafe { SelectObject(dc, self.font) };
        unsafe { GetTextMetricsW(dc, &mut metrics) };
        unsafe {
            SelectObject(dc, old);
            ReleaseDC(ptr::null_mut(), dc);
        }
        metrics.tmHeight + 16
    }

    fn at_point(&self, y: i32) -> Option<usize> {
        let row = self.row_height();
        let index = (y - 8) / row;
        (index >= 0 && (index as usize) < self.items.len()).then_some(index as usize)
    }
}

pub fn open(host: HWND) -> io::Result<()> {
    unsafe {
        let instance = GetModuleHandleW(ptr::null());
        let class = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: instance,
            hCursor: LoadCursorW(ptr::null_mut(), IDC_ARROW),
            lpszClassName: CLASS,
            hbrBackground: GetStockObject(BLACK_BRUSH) as HBRUSH,
            ..Default::default()
        };
        RegisterClassW(&class);

        let items = vec![
            (TRAY_OVERLAY, "显示输入框"),
            (TRAY_SETTINGS, "设置"),
            (TRAY_LOGS, "打开日志窗口"),
            (TRAY_EXIT, "退出"),
        ];
        let font = menu_font();
        let state = Box::new(State {
            host,
            font,
            hover: None,
            items,
        });

        let mut cursor = POINT::default();
        GetCursorPos(&mut cursor);
        let monitor = MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST);
        let mut info = MONITORINFO {
            cbSize: mem::size_of::<MONITORINFO>() as u32,
            ..Default::default()
        };
        GetMonitorInfoW(monitor, &mut info);
        let work = info.rcWork;
        let row = state.row_height();
        let scale = GetDpiForSystem().max(96) as i32 / 96;
        let width = state
            .items
            .iter()
            .map(|(_, text)| measure(&state, text))
            .max()
            .unwrap_or(120)
            + 56 * scale;
        let height = row * state.items.len() as i32 + 16;
        let x = cursor
            .x
            .clamp(work.left, (work.right - width).max(work.left));
        let y = (cursor.y - height).clamp(work.top, (work.bottom - height).max(work.top));

        let hwnd = CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
            CLASS,
            w!("Wordy 菜单"),
            WS_POPUP,
            x,
            y,
            width,
            height,
            ptr::null_mut(),
            ptr::null_mut(),
            instance,
            Box::into_raw(state).cast(),
        );
        if hwnd.is_null() {
            return Err(io::Error::last_os_error());
        }
        let region = CreateRoundRectRgn(0, 0, width + 1, height + 1, 10, 10);
        SetWindowRgn(hwnd, region, 0);
        ShowWindow(hwnd, SW_SHOWNOACTIVATE);
        SetCapture(hwnd);
        Ok(())
    }
}

fn measure(state: &State, text: &str) -> i32 {
    let value = wide(text);
    let dc = unsafe { GetDC(ptr::null_mut()) };
    let old = unsafe { SelectObject(dc, state.font) };
    let mut bounds = RECT::default();
    unsafe {
        DrawTextW(
            dc,
            value.as_ptr(),
            -1,
            &mut bounds,
            DT_CALCRECT | DT_SINGLELINE | DT_NOPREFIX,
        );
        SelectObject(dc, old);
        ReleaseDC(ptr::null_mut(), dc);
    }
    bounds.right
}

fn menu_font() -> HFONT {
    let dpi = unsafe { GetDpiForSystem() }.max(96) as i32;
    let height = (14 * dpi + 48) / 96;
    unsafe {
        CreateFontW(
            -height,
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
            w!("Segoe UI Variable"),
        )
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
            WM_PAINT => {
                let state = &*(GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const State);
                let mut paint = PAINTSTRUCT::default();
                let dc = BeginPaint(hwnd, &mut paint);
                let mut client = RECT::default();
                GetClientRect(hwnd, &mut client);
                let background = CreateSolidBrush(BACKGROUND);
                FillRect(dc, &client, background);
                DeleteObject(background);
                let row = state.row_height();
                let old = SelectObject(dc, state.font);
                SetBkMode(dc, TRANSPARENT as i32);
                SetTextColor(dc, FOREGROUND);
                for (index, (_, text)) in state.items.iter().enumerate() {
                    let mut bounds = RECT {
                        left: 18,
                        top: 8 + index as i32 * row,
                        right: client.right - 12,
                        bottom: 8 + (index as i32 + 1) * row,
                    };
                    if state.hover == Some(index) {
                        let hover = CreateSolidBrush(HOVER);
                        let mut hit = bounds;
                        hit.left = 6;
                        hit.right = client.right - 6;
                        FillRect(dc, &hit, hover);
                        DeleteObject(hover);
                    }
                    let value = wide(text);
                    DrawTextW(
                        dc,
                        value.as_ptr(),
                        -1,
                        &mut bounds,
                        DT_LEFT | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
                    );
                }
                SelectObject(dc, old);
                let border = CreateSolidBrush(BORDER);
                FrameRect(dc, &client, border);
                DeleteObject(border);
                EndPaint(hwnd, &paint);
                0
            }
            WM_MOUSEMOVE => {
                let state = &mut *(GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State);
                let hover = state.at_point((lparam >> 16) as i16 as i32);
                if state.hover != hover {
                    state.hover = hover;
                    InvalidateRect(hwnd, ptr::null(), 0);
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
                let state = &mut *(GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State);
                if state.hover.take().is_some() {
                    InvalidateRect(hwnd, ptr::null(), 0);
                }
                0
            }
            WM_LBUTTONUP => {
                let state = &*(GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *const State);
                let mut client = RECT::default();
                GetClientRect(hwnd, &mut client);
                let x = lparam as i16 as i32;
                let y = (lparam >> 16) as i16 as i32;
                if x >= 0 && y >= 0 && x < client.right && y < client.bottom {
                    if let Some(index) = state.at_point(y) {
                        PostMessageW(state.host, TRAY_COMMAND, state.items[index].0, 0);
                    }
                }
                DestroyWindow(hwnd);
                0
            }
            WM_RBUTTONUP | WM_MBUTTONDOWN | WM_KILLFOCUS => {
                DestroyWindow(hwnd);
                0
            }
            WM_DESTROY => {
                ReleaseCapture();
                0
            }
            WM_NCDESTROY => {
                let state = Box::from_raw(GetWindowLongPtrW(hwnd, GWLP_USERDATA) as *mut State);
                if !state.font.is_null() {
                    DeleteObject(state.font as HGDIOBJ);
                }
                SetWindowLongPtrW(hwnd, GWLP_USERDATA, 0);
                DefWindowProcW(hwnd, message, wparam, lparam)
            }
            _ => DefWindowProcW(hwnd, message, wparam, lparam),
        }
    }
}
