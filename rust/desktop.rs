use std::{io, iter, mem, ptr};
use windows_sys::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::System::SystemServices::SS_CENTERIMAGE;
use windows_sys::Win32::UI::Controls::EM_SETCUEBANNER;
use windows_sys::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetFocus, ReleaseCapture, SetFocus, VK_ESCAPE, VK_RETURN, VK_TAB,
};
use windows_sys::Win32::UI::Shell::*;
use windows_sys::Win32::UI::WindowsAndMessaging::*;
use windows_sys::core::w;

use crate::config::{Settings, WindowPosition};

pub const TRAY_MESSAGE: u32 = WM_APP + 1;
pub const OVERLAY_MESSAGE: u32 = WM_APP + 2;
pub const OVERLAY_SUBMIT: usize = 1;
pub const OVERLAY_READ: usize = 2;
pub const OVERLAY_SETTINGS: usize = 3;
pub const OVERLAY_POSITION: usize = 4;
pub const OVERLAY_DISMISS: usize = 5;
pub const TRAY_OVERLAY: usize = 1;
pub const TRAY_SETTINGS: usize = 2;
pub const TRAY_EXIT: usize = 3;

const PROMPT: i32 = 10;
const ENTRY: i32 = 11;
const READ: i32 = 12;
const SETTINGS: i32 = 13;
const WIDTH: i32 = 540;
const HEIGHT: i32 = 58;
const BACKGROUND: u32 = 0x1d1614;
const TEXT: u32 = 0x9ae286;

pub(crate) fn host_window(instance: HINSTANCE) -> io::Result<HWND> {
    let class = WNDCLASSW {
        lpfnWndProc: Some(host_proc),
        hInstance: instance,
        lpszClassName: w!("WordyMessageWindow"),
        ..Default::default()
    };
    // The class and its callback have process lifetime, and no state is passed by pointer.
    unsafe {
        lookup_class(&class)?;
        let window = CreateWindowExW(
            WS_EX_TOOLWINDOW,
            class.lpszClassName,
            w!("Wordy"),
            WS_POPUP,
            0,
            0,
            0,
            0,
            ptr::null_mut(),
            ptr::null_mut(),
            instance,
            ptr::null(),
        );
        if window.is_null() {
            Err(io::Error::last_os_error())
        } else {
            Ok(window)
        }
    }
}

unsafe extern "system" fn host_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    if msg == WM_CLOSE {
        unsafe { PostQuitMessage(0) };
        return 0;
    }
    unsafe { DefWindowProcW(hwnd, msg, wp, lp) }
}

unsafe fn lookup_class(class: &WNDCLASSW) -> io::Result<()> {
    let mut registered = WNDCLASSW::default();
    if unsafe { GetClassInfoW(class.hInstance, class.lpszClassName, &mut registered) } != 0
        || unsafe { RegisterClassW(class) } != 0
    {
        Ok(())
    } else {
        Err(io::Error::last_os_error())
    }
}

pub struct Tray {
    host: HWND,
    icon: HICON,
    active_icon: HICON,
    recording: bool,
}

impl Tray {
    pub fn install(host: HWND) -> io::Result<Self> {
        let icon = wordy_icon()?;
        let tray = Self {
            host,
            icon,
            active_icon: recording_icon(icon),
            recording: false,
        };
        tray.add()?;
        Ok(tray)
    }

    pub fn add(&self) -> io::Result<()> {
        let mut data = self.data();
        data.uFlags = NIF_ICON | NIF_TIP | NIF_MESSAGE;
        data.uCallbackMessage = TRAY_MESSAGE;
        data.hIcon = self.current_icon();
        copy_wide(&mut data.szTip, "Wordy");
        // Shell_NotifyIcon copies the initialized structure while its icon is alive.
        if unsafe { Shell_NotifyIconW(NIM_ADD, &data) } != 0 {
            Ok(())
        } else {
            Err(io::Error::other("无法创建 Wordy 托盘图标"))
        }
    }

    pub fn set_recording(&mut self, active: bool) {
        self.recording = active;
        let mut data = self.data();
        data.uFlags = NIF_ICON;
        data.hIcon = self.current_icon();
        unsafe { Shell_NotifyIconW(NIM_MODIFY, &data) };
    }

    fn current_icon(&self) -> HICON {
        if self.recording && !self.active_icon.is_null() {
            self.active_icon
        } else {
            self.icon
        }
    }

    pub fn notify(&self, message: &str) {
        let mut data = self.data();
        data.uFlags = NIF_INFO;
        data.dwInfoFlags = NIIF_INFO;
        copy_wide(&mut data.szInfoTitle, "Wordy");
        copy_wide(&mut data.szInfo, message);
        unsafe { Shell_NotifyIconW(NIM_MODIFY, &data) };
    }

    pub fn show_menu(&self) -> usize {
        // A transient menu is owned here and destroyed immediately after selection.
        unsafe {
            let menu = CreatePopupMenu();
            if menu.is_null() {
                return 0;
            }
            AppendMenuW(menu, MF_STRING, TRAY_OVERLAY, w!("显示输入框"));
            AppendMenuW(menu, MF_STRING, TRAY_SETTINGS, w!("设置"));
            AppendMenuW(menu, MF_SEPARATOR, 0, ptr::null());
            AppendMenuW(menu, MF_STRING, TRAY_EXIT, w!("退出"));
            let mut position = POINT::default();
            GetCursorPos(&mut position);
            SetForegroundWindow(self.host);
            let action = TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                position.x,
                position.y,
                0,
                self.host,
                ptr::null(),
            );
            PostMessageW(self.host, WM_NULL, 0, 0);
            DestroyMenu(menu);
            action as usize
        }
    }

    fn data(&self) -> NOTIFYICONDATAW {
        NOTIFYICONDATAW {
            cbSize: mem::size_of::<NOTIFYICONDATAW>() as u32,
            hWnd: self.host,
            uID: 1,
            ..Default::default()
        }
    }
}

impl Drop for Tray {
    fn drop(&mut self) {
        unsafe {
            Shell_NotifyIconW(NIM_DELETE, &self.data());
            DestroyIcon(self.icon);
            if !self.active_icon.is_null() {
                DestroyIcon(self.active_icon);
            }
        }
    }
}

fn recording_icon(icon: HICON) -> HICON {
    const SIZE: usize = 32;
    // Draw into a private DIB so the original icon remains reusable after recording.
    unsafe {
        let mut info = BITMAPINFO::default();
        info.bmiHeader.biSize = mem::size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = SIZE as i32;
        info.bmiHeader.biHeight = -(SIZE as i32);
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        let mut pixels = ptr::null_mut();
        let color = CreateDIBSection(
            ptr::null_mut(),
            &info,
            DIB_RGB_COLORS,
            &mut pixels,
            ptr::null_mut(),
            0,
        );
        if color.is_null() {
            return ptr::null_mut();
        }
        let dc = CreateCompatibleDC(ptr::null_mut());
        let mask = CreateBitmap(
            SIZE as i32,
            SIZE as i32,
            1,
            1,
            [0u8; SIZE * 4].as_ptr().cast(),
        );
        let result = if !dc.is_null() && !mask.is_null() {
            let old = SelectObject(dc, color);
            DrawIconEx(
                dc,
                0,
                0,
                icon,
                SIZE as i32,
                SIZE as i32,
                0,
                ptr::null_mut(),
                DI_NORMAL,
            );
            let pixels = std::slice::from_raw_parts_mut(pixels.cast::<u32>(), SIZE * SIZE);
            for y in 20i32..32 {
                for x in 20i32..32 {
                    let distance = (x - 25).pow(2) + (y - 25).pow(2);
                    if distance <= 36 {
                        pixels[y as usize * SIZE + x as usize] = if distance <= 22 {
                            0xfff23b46
                        } else {
                            0xff221c20
                        };
                    }
                }
            }
            SelectObject(dc, old);
            CreateIconIndirect(&ICONINFO {
                fIcon: 1,
                hbmMask: mask,
                hbmColor: color,
                ..Default::default()
            })
        } else {
            ptr::null_mut()
        };
        if !mask.is_null() {
            DeleteObject(mask);
        }
        if !dc.is_null() {
            DeleteDC(dc);
        }
        DeleteObject(color);
        result
    }
}

fn wordy_icon() -> io::Result<HICON> {
    let bytes = include_bytes!("../src/wordy/ui/icons/wordy.ico");
    let count = u16::from_le_bytes([bytes[4], bytes[5]]) as usize;
    let icon = bytes[6..6 + count * 16]
        .as_chunks::<16>()
        .0
        .iter()
        .min_by_key(|entry| {
            let size = if entry[0] == 0 {
                256
            } else {
                i32::from(entry[0])
            };
            (size - 32).abs()
        })
        .ok_or_else(|| io::Error::other("Wordy 图标为空"))?;
    let length = u32::from_le_bytes(icon[8..12].try_into().unwrap()) as usize;
    let start = u32::from_le_bytes(icon[12..16].try_into().unwrap()) as usize;
    let image = bytes
        .get(start..start + length)
        .ok_or_else(|| io::Error::other("Wordy 图标无效"))?;
    // CreateIcon copies the embedded ICO frame, which remains available through the call.
    let handle = unsafe {
        CreateIconFromResourceEx(image.as_ptr(), length as u32, 1, 0x0003_0000, 32, 32, 0)
    };
    if handle.is_null() {
        Err(io::Error::last_os_error())
    } else {
        Ok(handle)
    }
}

pub struct Overlay {
    hwnd: HWND,
    entry: HWND,
    font: HFONT,
    previous_foreground: HWND,
}

impl Overlay {
    pub(crate) fn new(instance: HINSTANCE, host: HWND) -> io::Result<Self> {
        // Creation and the child HWNDs belong to the one GUI event loop.
        unsafe {
            let class = WNDCLASSW {
                lpfnWndProc: Some(overlay_proc),
                hInstance: instance,
                lpszClassName: w!("WordyInputBar"),
                hCursor: LoadCursorW(ptr::null_mut(), IDC_ARROW),
                ..Default::default()
            };
            lookup_class(&class)?;
            let hwnd = CreateWindowExW(
                WS_EX_LAYERED | WS_EX_TOPMOST | WS_EX_TOOLWINDOW,
                class.lpszClassName,
                w!("Wordy 朗读"),
                WS_POPUP,
                0,
                0,
                WIDTH,
                HEIGHT,
                ptr::null_mut(),
                ptr::null_mut(),
                instance,
                ptr::null(),
            );
            if hwnd.is_null() {
                return Err(io::Error::last_os_error());
            }
            let mut overlay = Self {
                hwnd,
                entry: ptr::null_mut(),
                font: ptr::null_mut(),
                previous_foreground: ptr::null_mut(),
            };
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, host as isize);
            overlay.control(instance, w!("STATIC"), w!(">"), PROMPT, SS_CENTERIMAGE)?;
            overlay.entry = overlay.control(
                instance,
                w!("EDIT"),
                w!(""),
                ENTRY,
                ES_AUTOHSCROLL as u32 | WS_TABSTOP,
            )?;
            overlay.control(
                instance,
                w!("BUTTON"),
                w!("朗读"),
                READ,
                BS_PUSHBUTTON as u32,
            )?;
            overlay.control(
                instance,
                w!("BUTTON"),
                w!("设置"),
                SETTINGS,
                BS_PUSHBUTTON as u32 | WS_TABSTOP,
            )?;
            if SetWindowSubclass(overlay.entry, Some(edit_proc), 1, hwnd as usize) == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(overlay)
        }
    }

    unsafe fn control(
        &self,
        instance: HINSTANCE,
        class: *const u16,
        title: *const u16,
        id: i32,
        style: u32,
    ) -> io::Result<HWND> {
        let child = unsafe {
            CreateWindowExW(
                0,
                class,
                title,
                style | WS_CHILD | WS_VISIBLE,
                0,
                0,
                0,
                0,
                self.hwnd,
                id as isize as HMENU,
                instance,
                ptr::null(),
            )
        };
        if child.is_null() {
            Err(io::Error::last_os_error())
        } else {
            Ok(child)
        }
    }

    pub fn show(&mut self, settings: &Settings) -> io::Result<()> {
        let mut area = MONITORINFO {
            cbSize: mem::size_of::<MONITORINFO>() as u32,
            ..Default::default()
        };
        let mut cursor = POINT::default();
        // Window is placed in physical coordinates for the monitor containing the saved position or cursor.
        unsafe {
            self.previous_foreground = GetForegroundWindow();
            GetCursorPos(&mut cursor);
            let position = settings.window_position.filter(|_| !settings.fixed_center);
            let screen_point = position.map_or(cursor, |pos| POINT { x: pos.x, y: pos.y });
            let monitor = MonitorFromPoint(screen_point, MONITOR_DEFAULTTONEAREST);
            if GetMonitorInfoW(monitor, &mut area) == 0 {
                return Err(io::Error::last_os_error());
            }
            let mut dpi = 96;
            let mut dpi_y = 96;
            GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut dpi, &mut dpi_y);
            let px = |value| (value * dpi as i32 + 48) / 96;
            let (width, height) = (px(WIDTH), px(HEIGHT));
            let rect = area.rcWork;
            let (x, y) = position.map_or(
                (
                    (rect.left + rect.right - width) / 2,
                    (rect.top + rect.bottom - height) / 2,
                ),
                |p| (p.x, p.y),
            );
            let x = x.clamp(rect.left, rect.left.max(rect.right - width));
            let y = y.clamp(rect.top, rect.top.max(rect.bottom - height));
            let alpha = (settings.overlay_opacity * 255.0).round() as u8;
            SetLayeredWindowAttributes(self.hwnd, 0, alpha, LWA_ALPHA);
            let region = CreateRoundRectRgn(0, 0, width + 1, height + 1, px(28), px(28));
            if SetWindowRgn(self.hwnd, region, 0) == 0 {
                DeleteObject(region);
            }
            let font_name = wide(if settings.ui_font.is_empty() {
                "Microsoft YaHei UI"
            } else {
                &settings.ui_font
            });
            let font = CreateFontW(
                -px(20),
                0,
                0,
                0,
                400,
                0,
                0,
                0,
                DEFAULT_CHARSET as u32,
                0,
                0,
                0,
                0,
                font_name.as_ptr(),
            );
            for (id, x, y, w, h) in [
                (PROMPT, 10, 8, 18, 42),
                (ENTRY, 32, 13, 397, 34),
                (READ, 430, 11, 50, 36),
                (SETTINGS, 484, 11, 48, 36),
            ] {
                let child = GetDlgItem(self.hwnd, id);
                MoveWindow(child, px(x), px(y), px(w), px(h), 1);
                if !font.is_null() {
                    SendMessageW(child, WM_SETFONT, font as usize, 0);
                }
            }
            if !font.is_null() {
                if !self.font.is_null() {
                    DeleteObject(self.font);
                }
                self.font = font;
            }
            let placeholder = wide(&settings.overlay_placeholder);
            SendMessageW(
                self.entry,
                EM_SETCUEBANNER,
                1,
                placeholder.as_ptr() as isize,
            );
            SetWindowTextW(self.entry, w!(""));
            enable_read(self.hwnd, false);
            SetPropW(
                self.hwnd,
                w!("WordyDrag"),
                if settings.fixed_center {
                    ptr::null_mut()
                } else {
                    ptr::without_provenance_mut(1)
                },
            );
            SetWindowPos(self.hwnd, HWND_TOPMOST, x, y, width, height, SWP_SHOWWINDOW);
            SetForegroundWindow(self.hwnd);
            SetFocus(self.entry);
            SetTimer(self.hwnd, 1, 30, None);
            SetTimer(self.hwnd, 2, 90, None);
            SetTimer(self.hwnd, 3, 180, None);
        }
        Ok(())
    }

    pub fn is_visible(&self) -> bool {
        unsafe { IsWindowVisible(self.hwnd) != 0 }
    }

    pub fn hide(&mut self, restore_focus: bool) {
        // Restore the previous foreground before the TTS worker injects push-to-talk.
        unsafe {
            ShowWindow(self.hwnd, SW_HIDE);
            if restore_focus
                && !self.previous_foreground.is_null()
                && IsWindow(self.previous_foreground) != 0
            {
                SetForegroundWindow(self.previous_foreground);
            }
        }
    }

    pub fn text(&self) -> String {
        window_text(self.entry).trim().to_owned()
    }

    pub fn position(&self) -> Option<WindowPosition> {
        let mut rect = RECT::default();
        if unsafe { GetWindowRect(self.hwnd, &mut rect) } == 0 {
            return None;
        }
        Some(WindowPosition {
            x: rect.left,
            y: rect.top,
        })
    }
}

impl Drop for Overlay {
    fn drop(&mut self) {
        unsafe {
            RemovePropW(self.hwnd, w!("WordyDrag"));
            DestroyWindow(self.hwnd);
            if !self.font.is_null() {
                DeleteObject(self.font);
            }
        }
    }
}

unsafe extern "system" fn overlay_proc(hwnd: HWND, msg: u32, wp: WPARAM, lp: LPARAM) -> LRESULT {
    // Never borrow app state inside a window procedure. Turn actions into queued messages.
    unsafe {
        let action = match msg {
            WM_COMMAND => match wp as u16 as i32 {
                READ => Some(OVERLAY_READ),
                SETTINGS => Some(OVERLAY_SETTINGS),
                ENTRY if wp >> 16 == EN_CHANGE as usize => {
                    enable_read(hwnd, !window_text(lp as HWND).trim().is_empty());
                    None
                }
                _ => None,
            },
            WM_ACTIVATE if wp as u16 == WA_INACTIVE as u16 => Some(OVERLAY_DISMISS),
            WM_EXITSIZEMOVE => Some(OVERLAY_POSITION),
            WM_CLOSE => Some(OVERLAY_DISMISS),
            WM_LBUTTONDOWN if !GetPropW(hwnd, w!("WordyDrag")).is_null() => {
                SetFocus(GetDlgItem(hwnd, ENTRY));
                ReleaseCapture();
                SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION as usize, 0);
                return 0;
            }
            WM_TIMER if (1..=3).contains(&wp) => {
                KillTimer(hwnd, wp);
                if GetForegroundWindow() == hwnd
                    && IsWindowVisible(hwnd) != 0
                    && GetFocus() != GetDlgItem(hwnd, SETTINGS)
                {
                    SetFocus(GetDlgItem(hwnd, ENTRY));
                }
                return 0;
            }
            WM_ERASEBKGND => {
                let mut bounds = RECT::default();
                let brush = CreateSolidBrush(BACKGROUND);
                GetClientRect(hwnd, &mut bounds);
                FillRect(wp as HDC, &bounds, brush);
                DeleteObject(brush);
                return 1;
            }
            WM_CTLCOLOREDIT | WM_CTLCOLORSTATIC => {
                let dc = wp as HDC;
                SetTextColor(dc, TEXT);
                SetBkColor(dc, BACKGROUND);
                let brush = GetStockObject(DC_BRUSH);
                SetDCBrushColor(dc, BACKGROUND);
                return brush as isize;
            }
            _ => None,
        };
        if let Some(action) = action {
            post_overlay(hwnd, action);
            0
        } else {
            DefWindowProcW(hwnd, msg, wp, lp)
        }
    }
}

unsafe extern "system" fn edit_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    id: usize,
    parent: usize,
) -> LRESULT {
    unsafe {
        if msg == WM_NCDESTROY {
            RemoveWindowSubclass(hwnd, Some(edit_proc), id);
        }
        if msg == WM_CHAR {
            match wp as u16 {
                VK_RETURN => {
                    post_overlay(parent as HWND, OVERLAY_SUBMIT);
                    return 0;
                }
                VK_ESCAPE => {
                    post_overlay(parent as HWND, OVERLAY_DISMISS);
                    return 0;
                }
                VK_TAB => {
                    SetFocus(GetDlgItem(parent as HWND, SETTINGS));
                    return 0;
                }
                _ => (),
            }
        }
        DefSubclassProc(hwnd, msg, wp, lp)
    }
}

unsafe fn post_overlay(overlay: HWND, action: usize) {
    unsafe {
        let host = GetWindowLongPtrW(overlay, GWLP_USERDATA) as HWND;
        if !host.is_null() {
            PostMessageW(host, OVERLAY_MESSAGE, action, 0);
        }
    }
}

unsafe fn enable_read(hwnd: HWND, enabled: bool) {
    unsafe {
        windows_sys::Win32::UI::Input::KeyboardAndMouse::EnableWindow(
            GetDlgItem(hwnd, READ),
            i32::from(enabled),
        )
    };
}

pub(crate) fn window_text(hwnd: HWND) -> String {
    // The child control belongs to this thread, and the buffer includes a terminator slot.
    unsafe {
        let mut buffer = vec![0; GetWindowTextLengthW(hwnd) as usize + 1];
        let length = GetWindowTextW(hwnd, buffer.as_mut_ptr(), buffer.len() as i32).max(0) as usize;
        String::from_utf16_lossy(&buffer[..length])
    }
}

pub fn wide(text: &str) -> Vec<u16> {
    text.encode_utf16().chain(iter::once(0)).collect()
}

fn copy_wide(target: &mut [u16], text: &str) {
    let mut offset = 0;
    for c in text.chars() {
        let units = c.len_utf16();
        if offset + units >= target.len() {
            break;
        }
        c.encode_utf16(&mut target[offset..offset + units]);
        offset += units;
    }
}
