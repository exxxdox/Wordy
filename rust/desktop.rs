use std::{io, iter, mem, ptr};
use windows_sys::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::System::Registry::{
    HKEY_CURRENT_USER, KEY_WRITE, REG_OPTION_NON_VOLATILE, REG_SZ, RegCloseKey, RegCreateKeyExW,
    RegSetValueExW,
};
use windows_sys::Win32::System::SystemServices::SS_CENTERIMAGE;
use windows_sys::Win32::UI::Controls::{
    DRAWITEMSTRUCT, TOOLTIPS_CLASSW, TTF_IDISHWND, TTF_SUBCLASS, TTM_ADDTOOLW, TTS_ALWAYSTIP,
    TTTOOLINFOW,
};
use windows_sys::Win32::UI::HiDpi::{GetDpiForMonitor, GetDpiForWindow, MDT_EFFECTIVE_DPI};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    GetCapture, GetFocus, IsWindowEnabled, ReleaseCapture, SetCapture, SetFocus, VK_ESCAPE,
    VK_RETURN, VK_TAB,
};
use windows_sys::Win32::UI::Shell::*;
use windows_sys::Win32::UI::WindowsAndMessaging::*;
use windows_sys::core::w;

use crate::config::{Settings, WindowPosition};
use crate::wheel::Point;
use crate::wheel_paint::WheelBitmap;
use crate::wheel_window::LayeredSurface;

pub const TRAY_MESSAGE: u32 = WM_APP + 1;
pub const OVERLAY_MESSAGE: u32 = WM_APP + 2;
pub const OVERLAY_SUBMIT: usize = 1;
pub const OVERLAY_READ: usize = 2;
pub const OVERLAY_SETTINGS: usize = 3;
pub const OVERLAY_POSITION: usize = 4;
pub const OVERLAY_DISMISS: usize = 5;
pub const OVERLAY_DEACTIVATED: usize = 6;
pub const TRAY_OVERLAY: usize = 1;
pub const TRAY_SETTINGS: usize = 2;
pub const TRAY_EXIT: usize = 3;
pub const TRAY_LOGS: usize = 4;

const PROMPT: i32 = 10;
const ENTRY: i32 = 11;
const READ: i32 = 12;
const SETTINGS: i32 = 13;
const BODY_WIDTH: i32 = 540;
const BODY_HEIGHT: i32 = 58;
const SHADOW_PAD: i32 = 8;
const WIDTH: i32 = BODY_WIDTH + SHADOW_PAD * 2;
const HEIGHT: i32 = BODY_HEIGHT + SHADOW_PAD * 2;
const GLASS_TIMER: usize = 4;
const GLOW_STEPS: usize = 10;

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
        data.uFlags = NIF_ICON | NIF_TIP;
        data.hIcon = self.current_icon();
        copy_wide(
            &mut data.szTip,
            if active {
                "Wordy · 后台录制中"
            } else {
                "Wordy"
            },
        );
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

    pub fn notify_balloon(&self, message: &str) {
        self.notify(message);
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
            AppendMenuW(menu, MF_STRING, TRAY_LOGS, w!("打开日志窗口"));
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

const APP_ID: &str = "Wordy.Desktop";
const SHOW_TOAST: &str = r#"
$ErrorActionPreference = 'Stop'
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$xml = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]::new()
$xml.LoadXml($payload.xml)
$toast = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]::CreateToastNotifier($payload.app_id).Show($toast)
"#;

/// Register the notification sender and show a toast through the Windows
/// notification center. Falls back to the tray balloon in the caller.
pub fn notify_toast(message: &str) -> io::Result<()> {
    use std::io::Write;
    use std::os::windows::process::CommandExt;
    use std::process::{Command, Stdio};

    let directory = crate::config::config_directory();
    std::fs::create_dir_all(&directory)?;
    let icon = directory.join("notification-icon.ico");
    let bytes = include_bytes!("assets/wordy.ico");
    if std::fs::read(&icon).as_deref().ok() != Some(bytes) {
        let temporary = icon.with_extension("tmp");
        std::fs::write(&temporary, bytes)?;
        std::fs::rename(temporary, &icon)?;
    }
    let display = wide("Wordy");
    let icon_uri = wide(&icon.to_string_lossy());
    let subkey = wide(r"Software\Classes\AppUserModelId\Wordy.Desktop");
    let name = wide("DisplayName");
    let uri = wide("IconUri");
    unsafe {
        let mut key = ptr::null_mut();
        let mut disposition = 0;
        let result = RegCreateKeyExW(
            HKEY_CURRENT_USER,
            subkey.as_ptr(),
            0,
            ptr::null(),
            REG_OPTION_NON_VOLATILE,
            KEY_WRITE,
            ptr::null(),
            &mut key,
            &mut disposition,
        );
        if result != 0 {
            return Err(io::Error::from_raw_os_error(result as i32));
        }
        for (value_name, value) in [(&name, &display), (&uri, &icon_uri)] {
            let result = RegSetValueExW(
                key,
                value_name.as_ptr(),
                0,
                REG_SZ,
                value.as_ptr().cast(),
                (value.len() * 2) as u32,
            );
            if result != 0 {
                RegCloseKey(key);
                return Err(io::Error::from_raw_os_error(result as i32));
            }
        }
        RegCloseKey(key);
    }
    let xml = format!(
        "<toast><visual><binding template=\"ToastGeneric\"><text>{}</text></binding></visual><audio silent=\"true\"/></toast>",
        xml_text(message)
    );
    let payload = format!(
        "{{\"app_id\":\"{}\",\"xml\":\"{}\"}}",
        APP_ID,
        json_text(&xml)
    );
    let powershell = format!(
        r"{}\System32\WindowsPowerShell\v1.0\powershell.exe",
        std::env::var("SystemRoot").unwrap_or_else(|_| r"C:\Windows".into())
    );
    let mut child = Command::new(powershell)
        .args(["-NoProfile", "-NonInteractive", "-Command", SHOW_TOAST])
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .creation_flags(0x0800_0000)
        .spawn()?;
    if let Some(mut stdin) = child.stdin.take() {
        stdin.write_all(payload.as_bytes())?;
    }
    if child.wait()?.success() {
        Ok(())
    } else {
        Err(io::Error::other("Windows 通知发送失败"))
    }
}

fn xml_text(text: &str) -> String {
    let mut escaped = String::with_capacity(text.len());
    for unit in text.chars() {
        match unit {
            '&' => escaped.push_str("&amp;"),
            '<' => escaped.push_str("&lt;"),
            '>' => escaped.push_str("&gt;"),
            '"' => escaped.push_str("&quot;"),
            '\'' => escaped.push_str("&apos;"),
            // Keep the PowerShell stdin payload ASCII; XML accepts numeric refs.
            unit if !unit.is_ascii() => escaped.push_str(&format!("&#x{:X};", unit as u32)),
            unit => escaped.push(unit),
        }
    }
    escaped
}

fn json_text(text: &str) -> String {
    text.replace('\\', "\\\\")
        .replace('"', "\\\"")
        .replace('\n', "\\n")
        .replace('\r', "\\r")
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
    let bytes = include_bytes!("assets/wordy.ico");
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
    backdrop: HWND,
    shadow: Option<(u32, u32, LayeredSurface)>,
    entry: HWND,
    font: HFONT,
    prompt_font: HFONT,
    placeholder: Vec<u16>,
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
                backdrop: ptr::null_mut(),
                shadow: None,
                entry: ptr::null_mut(),
                font: ptr::null_mut(),
                prompt_font: ptr::null_mut(),
                placeholder: vec![0],
                previous_foreground: ptr::null_mut(),
            };
            // The noninteractive shadow has its own alpha so the standard EDIT can keep IME and caret support.
            overlay.backdrop = CreateWindowExW(
                WS_EX_LAYERED
                    | WS_EX_TRANSPARENT
                    | WS_EX_NOACTIVATE
                    | WS_EX_TOPMOST
                    | WS_EX_TOOLWINDOW,
                w!("STATIC"),
                w!(""),
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
            if overlay.backdrop.is_null() {
                return Err(io::Error::last_os_error());
            }
            if SetPropW(hwnd, w!("WordyShadow"), overlay.backdrop) == 0 {
                return Err(io::Error::last_os_error());
            }
            SetWindowLongPtrW(hwnd, GWLP_USERDATA, host as isize);
            overlay.control(instance, w!("STATIC"), w!(">"), PROMPT, SS_CENTERIMAGE)?;
            overlay.entry = overlay.control(
                instance,
                w!("EDIT"),
                w!(""),
                ENTRY,
                ES_AUTOHSCROLL as u32 | WS_TABSTOP,
            )?;
            let read = overlay.control(
                instance,
                w!("BUTTON"),
                w!("朗读"),
                READ,
                BS_OWNERDRAW as u32,
            )?;
            let settings = overlay.control(
                instance,
                w!("BUTTON"),
                w!("设置"),
                SETTINGS,
                BS_OWNERDRAW as u32 | WS_TABSTOP,
            )?;
            if SetWindowSubclass(overlay.entry, Some(edit_proc), 1, hwnd as usize) == 0 {
                return Err(io::Error::last_os_error());
            }
            for button in [read, settings] {
                if SetWindowSubclass(button, Some(overlay_button_proc), 1, hwnd as usize) == 0 {
                    return Err(io::Error::last_os_error());
                }
            }
            let tip = CreateWindowExW(
                WS_EX_TOPMOST,
                TOOLTIPS_CLASSW,
                ptr::null(),
                WS_POPUP | TTS_ALWAYSTIP,
                0,
                0,
                0,
                0,
                hwnd,
                ptr::null_mut(),
                instance,
                ptr::null(),
            );
            if !tip.is_null() {
                for (child, text) in [
                    (overlay.entry, w!("Enter 朗读 · Esc 收起")),
                    (read, w!("朗读并保留输入框")),
                    (settings, w!("打开设置")),
                ] {
                    let mut tool = TTTOOLINFOW {
                        cbSize: mem::size_of::<TTTOOLINFOW>() as u32,
                        uFlags: TTF_IDISHWND | TTF_SUBCLASS,
                        hwnd,
                        uId: child as usize,
                        lpszText: text.cast_mut(),
                        ..Default::default()
                    };
                    SendMessageW(tip, TTM_ADDTOOLW, 0, ptr::from_mut(&mut tool) as isize);
                }
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
            self.paint_shadow(x, y, width, height, settings.overlay_opacity)?;
            let region = CreateRoundRectRgn(px(9), px(9), px(547) + 1, px(65) + 1, px(40), px(40));
            if SetWindowRgn(self.hwnd, region, 0) == 0 {
                DeleteObject(region);
            }
            for (id, x, y, w, h) in [
                (PROMPT, 18, 25, 18, 24),
                (ENTRY, 40, 22, 396, 31),
                (READ, 442, 19, 36, 36),
                (SETTINGS, 496, 19, 36, 36),
            ] {
                let child = GetDlgItem(self.hwnd, id);
                MoveWindow(child, px(x), px(y), px(w), px(h), 1);
            }
            self.set_visual_settings(settings, dpi)?;
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
            SetTimer(self.hwnd, GLASS_TIMER, 16, None);
        }
        Ok(())
    }

    pub fn apply_visual_settings(&mut self, settings: &Settings) -> io::Result<()> {
        if !self.is_visible() {
            return Ok(());
        }
        let mut bounds = RECT::default();
        if unsafe { GetWindowRect(self.hwnd, &mut bounds) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let dpi = unsafe { GetDpiForWindow(self.hwnd) }.max(96);
        self.set_visual_settings(settings, dpi)?;
        self.paint_shadow(
            bounds.left,
            bounds.top,
            bounds.right - bounds.left,
            bounds.bottom - bounds.top,
            settings.overlay_opacity,
        )
    }

    fn set_visual_settings(&mut self, settings: &Settings, dpi: u32) -> io::Result<()> {
        let pixel = |value| (value * dpi as i32 + 48) / 96;
        let font_name = wide(if settings.ui_font.is_empty() {
            "Segoe UI Variable"
        } else {
            &settings.ui_font
        });
        let alpha = (settings.overlay_opacity * 255.0).round() as u8;
        let placeholder = wide(&settings.overlay_placeholder);
        unsafe {
            if SetLayeredWindowAttributes(self.hwnd, 0, alpha, LWA_ALPHA) == 0
                || SetPropW(
                    self.entry,
                    w!("WordyPlaceholder"),
                    placeholder.as_ptr().cast_mut().cast(),
                ) == 0
            {
                return Err(io::Error::last_os_error());
            }
            self.placeholder = placeholder;
            for (child, size, weight, slot) in [
                (self.entry, 24, FW_NORMAL, &mut self.font),
                (
                    GetDlgItem(self.hwnd, PROMPT),
                    20,
                    FW_MEDIUM,
                    &mut self.prompt_font,
                ),
            ] {
                let font = overlay_font(-pixel(size), weight as i32, &font_name);
                if !font.is_null() {
                    SendMessageW(child, WM_SETFONT, font as usize, 0);
                    let old = mem::replace(slot, font);
                    if !old.is_null() {
                        DeleteObject(old);
                    }
                }
            }
            reset_overlay_brush(self.hwnd);
            RedrawWindow(
                self.hwnd,
                ptr::null(),
                ptr::null_mut(),
                RDW_INVALIDATE | RDW_ALLCHILDREN,
            );
        }
        Ok(())
    }

    fn paint_shadow(
        &mut self,
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        opacity: f64,
    ) -> io::Result<()> {
        let bitmap = shadow_bitmap(width as u32, height as u32, opacity as f32);
        if self
            .shadow
            .as_ref()
            .is_none_or(|&(w, h, _)| (w, h) != (bitmap.width, bitmap.height))
        {
            let surface =
                LayeredSurface::new(bitmap.width, bitmap.height).map_err(io::Error::other)?;
            self.shadow = Some((bitmap.width, bitmap.height, surface));
        }
        self.shadow
            .as_mut()
            .unwrap()
            .2
            .present(
                self.backdrop,
                Point::new(f64::from(x), f64::from(y)),
                &bitmap,
            )
            .map_err(io::Error::other)
    }

    pub fn is_visible(&self) -> bool {
        unsafe { IsWindowVisible(self.hwnd) != 0 }
    }

    pub fn hide(&mut self, restore_focus: bool) {
        // Restore the previous foreground before the TTS worker injects push-to-talk.
        unsafe {
            KillTimer(self.hwnd, GLASS_TIMER);
            RemovePropW(self.hwnd, w!("WordyFocusGlow"));
            RemovePropW(self.hwnd, w!("WordyHoverIcon"));
            ShowWindow(self.hwnd, SW_HIDE);
            ShowWindow(self.backdrop, SW_HIDE);
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
            RemovePropW(self.hwnd, w!("WordyShadow"));
            DestroyWindow(self.hwnd);
            if !self.backdrop.is_null() {
                DestroyWindow(self.backdrop);
            }
            if !self.font.is_null() {
                DeleteObject(self.font);
            }
            if !self.prompt_font.is_null() {
                DeleteObject(self.prompt_font);
            }
        }
    }
}

fn overlay_font(height: i32, weight: i32, name: &[u16]) -> HFONT {
    unsafe {
        CreateFontW(
            height,
            0,
            0,
            0,
            weight,
            0,
            0,
            0,
            DEFAULT_CHARSET as u32,
            0,
            0,
            ANTIALIASED_QUALITY as u32,
            0,
            name.as_ptr(),
        )
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
            WM_ACTIVATE if wp as u16 == WA_INACTIVE as u16 => Some(OVERLAY_DEACTIVATED),
            WM_WINDOWPOSCHANGED => {
                move_overlay_shadow(hwnd);
                None
            }
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
            WM_TIMER if wp == GLASS_TIMER => {
                sync_overlay_glass(hwnd);
                return 0;
            }
            WM_ERASEBKGND => return 1,
            WM_PAINT => {
                let mut paint = PAINTSTRUCT::default();
                let dc = BeginPaint(hwnd, &mut paint);
                let mut bounds = RECT::default();
                GetClientRect(hwnd, &mut bounds);
                paint_capsule(hwnd, dc, bounds, 0, 0, None, false);
                EndPaint(hwnd, &paint);
                return 0;
            }
            WM_DRAWITEM if wp == READ as usize || wp == SETTINGS as usize => {
                let item = &*(lp as *const DRAWITEMSTRUCT);
                let mut origin = POINT::default();
                ClientToScreen(item.hwndItem, &mut origin);
                ScreenToClient(hwnd, &mut origin);
                let hovered = GetPropW(hwnd, w!("WordyHoverIcon")) as usize == wp
                    || (wp == SETTINGS as usize && GetFocus() == item.hwndItem);
                let enabled = IsWindowEnabled(item.hwndItem) != 0;
                let color = capsule_icon_color(wp == SETTINGS as usize, hovered, enabled);
                paint_capsule(
                    hwnd,
                    item.hDC,
                    item.rcItem,
                    origin.x,
                    origin.y,
                    Some(color),
                    wp == SETTINGS as usize,
                );
                return 1;
            }
            WM_CTLCOLOREDIT | WM_CTLCOLORSTATIC => {
                let dc = wp as HDC;
                let text = if lp as HWND == GetDlgItem(hwnd, PROMPT) {
                    [152, 152, 157]
                } else {
                    [245, 245, 247]
                };
                SetTextColor(dc, colorref(text));
                SetBkMode(dc, TRANSPARENT as i32);
                let brush = overlay_background_brush(hwnd, lp as HWND, dc);
                return brush as isize;
            }
            WM_NCDESTROY => {
                let brush = RemovePropW(hwnd, w!("WordyGlassBrush"));
                if !brush.is_null() {
                    DeleteObject(brush);
                }
                None
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
            RemovePropW(hwnd, w!("WordyPlaceholder"));
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
        let result = DefSubclassProc(hwnd, msg, wp, lp);
        if msg == WM_PAINT || msg == WM_PRINTCLIENT {
            paint_overlay_placeholder(hwnd, (msg == WM_PRINTCLIENT).then_some(wp as HDC));
        }
        result
    }
}

unsafe fn paint_overlay_placeholder(hwnd: HWND, print_dc: Option<HDC>) {
    unsafe {
        let placeholder = GetPropW(hwnd, w!("WordyPlaceholder")).cast::<u16>();
        if placeholder.is_null() || *placeholder == 0 || GetWindowTextLengthW(hwnd) != 0 {
            return;
        }
        let dc = print_dc.unwrap_or_else(|| GetDC(hwnd));
        if dc.is_null() {
            return;
        }
        // The Win32 cue banner depends on a manifest. Drawing over EDIT keeps native IME and selection.
        let mut rect = RECT::default();
        GetClientRect(hwnd, &mut rect);
        let saved = SaveDC(dc);
        SetTextColor(dc, colorref([152, 152, 157]));
        SetBkMode(dc, TRANSPARENT as i32);
        let font = SendMessageW(hwnd, WM_GETFONT, 0, 0) as HFONT;
        let previous = if font.is_null() {
            ptr::null_mut()
        } else {
            SelectObject(dc, font)
        };
        let hidden = HideCaret(hwnd) != 0;
        DrawTextW(
            dc,
            placeholder,
            -1,
            &mut rect,
            DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX,
        );
        if hidden {
            ShowCaret(hwnd);
        }
        if saved != 0 {
            RestoreDC(dc, saved);
        } else if !previous.is_null() {
            SelectObject(dc, previous);
        }
        if print_dc.is_none() {
            ReleaseDC(hwnd, dc);
        }
    }
}

unsafe extern "system" fn overlay_button_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    id: usize,
    parent: usize,
) -> LRESULT {
    unsafe {
        let parent = parent as HWND;
        let command = if GetDlgCtrlID(hwnd) == SETTINGS {
            OVERLAY_SETTINGS
        } else {
            OVERLAY_READ
        };
        match msg {
            WM_NCDESTROY => {
                RemoveWindowSubclass(hwnd, Some(overlay_button_proc), id);
            }
            WM_LBUTTONDOWN if IsWindowEnabled(hwnd) != 0 => {
                // Clicking an icon leaves the native text editor and its caret focused.
                SetCapture(hwnd);
                return 0;
            }
            WM_LBUTTONUP if GetCapture() == hwnd => {
                ReleaseCapture();
                let mut rect = RECT::default();
                GetClientRect(hwnd, &mut rect);
                let x = i32::from(lp as i16);
                let y = i32::from((lp >> 16) as i16);
                if (rect.left..rect.right).contains(&x)
                    && (rect.top..rect.bottom).contains(&y)
                    && IsWindowEnabled(hwnd) != 0
                {
                    post_overlay(parent, command);
                }
                return 0;
            }
            WM_SETCURSOR => {
                let icon = if IsWindowEnabled(hwnd) != 0 {
                    IDC_HAND
                } else {
                    IDC_ARROW
                };
                SetCursor(LoadCursorW(ptr::null_mut(), icon));
                return 1;
            }
            WM_CHAR if wp == VK_RETURN as usize || wp == usize::from(b' ') => {
                if IsWindowEnabled(hwnd) != 0 {
                    post_overlay(parent, command);
                }
                return 0;
            }
            WM_CHAR if wp == VK_TAB as usize => {
                SetFocus(GetDlgItem(parent, ENTRY));
                return 0;
            }
            WM_CHAR if wp == VK_ESCAPE as usize => {
                post_overlay(parent, OVERLAY_DISMISS);
                return 0;
            }
            _ => {}
        }
        DefSubclassProc(hwnd, msg, wp, lp)
    }
}

unsafe fn move_overlay_shadow(hwnd: HWND) {
    unsafe {
        let shadow = GetPropW(hwnd, w!("WordyShadow"));
        let mut pos = RECT::default();
        if !shadow.is_null() && IsWindowVisible(hwnd) != 0 && GetWindowRect(hwnd, &mut pos) != 0 {
            SetWindowPos(
                shadow,
                hwnd,
                pos.left,
                pos.top,
                0,
                0,
                SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            );
        }
    }
}

unsafe fn sync_overlay_glass(hwnd: HWND) {
    unsafe {
        let previous = GetPropW(hwnd, w!("WordyFocusGlow")) as usize;
        let focused = GetFocus();
        let active = focused == hwnd || IsChild(hwnd, focused) != 0;
        let step = if active {
            (previous + 1).min(GLOW_STEPS)
        } else {
            previous.saturating_sub(1)
        };
        let read = GetDlgItem(hwnd, READ);
        let settings = GetDlgItem(hwnd, SETTINGS);
        let mut cursor = POINT::default();
        GetCursorPos(&mut cursor);
        let is_over = |child| {
            let mut bounds = RECT::default();
            GetWindowRect(child, &mut bounds) != 0
                && (bounds.left..bounds.right).contains(&cursor.x)
                && (bounds.top..bounds.bottom).contains(&cursor.y)
        };
        let hovered = if is_over(settings) {
            SETTINGS
        } else if IsWindowEnabled(read) != 0 && is_over(read) {
            READ
        } else {
            0
        };
        let previous_hover = GetPropW(hwnd, w!("WordyHoverIcon")) as i32;
        if previous_hover != hovered {
            if hovered == 0 {
                RemovePropW(hwnd, w!("WordyHoverIcon"));
            } else {
                SetPropW(
                    hwnd,
                    w!("WordyHoverIcon"),
                    ptr::without_provenance_mut(hovered as usize),
                );
            }
            for button in [read, settings] {
                InvalidateRect(button, ptr::null(), 0);
            }
        }
        if previous != step {
            if step == 0 {
                RemovePropW(hwnd, w!("WordyFocusGlow"));
            } else {
                SetPropW(
                    hwnd,
                    w!("WordyFocusGlow"),
                    ptr::without_provenance_mut(step),
                );
            }
            reset_overlay_brush(hwnd);
            RedrawWindow(
                hwnd,
                ptr::null(),
                ptr::null_mut(),
                RDW_INVALIDATE | RDW_ALLCHILDREN,
            );
        }
    }
}

unsafe fn reset_overlay_brush(hwnd: HWND) {
    unsafe {
        let brush = RemovePropW(hwnd, w!("WordyGlassBrush"));
        if !brush.is_null() {
            DeleteObject(brush);
        }
    }
}

unsafe fn capsule_glow(hwnd: HWND) -> f32 {
    let step = unsafe { GetPropW(hwnd, w!("WordyFocusGlow")) as usize };
    1.0 - (1.0 - step as f32 / GLOW_STEPS as f32).powi(3)
}

unsafe fn overlay_background_brush(hwnd: HWND, child: HWND, dc: HDC) -> HBRUSH {
    unsafe {
        let mut origin = POINT::default();
        ClientToScreen(child, &mut origin);
        ScreenToClient(hwnd, &mut origin);
        SetBrushOrgEx(dc, 0, -origin.y, ptr::null_mut());
        let cached = GetPropW(hwnd, w!("WordyGlassBrush"));
        if !cached.is_null() {
            return cached;
        }
        let mut bounds = RECT::default();
        GetClientRect(hwnd, &mut bounds);
        let height = bounds.bottom.max(1);
        let scale = height as f32 / HEIGHT as f32;
        let glow = capsule_glow(hwnd);
        let rows: Vec<_> = (0..height)
            .map(|y| dib_rgb(capsule_color(270.0, (y as f32 + 0.5) / scale, glow)))
            .collect();
        let bitmap = CreateCompatibleBitmap(dc, 1, height);
        let memory = CreateCompatibleDC(dc);
        if !bitmap.is_null() && !memory.is_null() {
            let previous = SelectObject(memory, bitmap);
            blit_pixels(
                memory,
                RECT {
                    left: 0,
                    top: 0,
                    right: 1,
                    bottom: height,
                },
                &rows,
            );
            SelectObject(memory, previous);
        }
        if !memory.is_null() {
            DeleteDC(memory);
        }
        let brush = if bitmap.is_null() {
            ptr::null_mut()
        } else {
            CreatePatternBrush(bitmap)
        };
        if !bitmap.is_null() {
            DeleteObject(bitmap);
        }
        if !brush.is_null() {
            if SetPropW(hwnd, w!("WordyGlassBrush"), brush) != 0 {
                return brush;
            }
            DeleteObject(brush);
        }
        SetDCBrushColor(dc, colorref(capsule_color(270.0, 37.0, glow)));
        GetStockObject(DC_BRUSH)
    }
}

unsafe fn paint_capsule(
    hwnd: HWND,
    dc: HDC,
    dest: RECT,
    origin_x: i32,
    origin_y: i32,
    icon: Option<[u8; 3]>,
    gear: bool,
) {
    let mut bounds = RECT::default();
    unsafe { GetClientRect(hwnd, &mut bounds) };
    let scale = bounds.bottom as f32 / HEIGHT as f32;
    let (width, height) = (dest.right - dest.left, dest.bottom - dest.top);
    if scale <= 0.0 || width <= 0 || height <= 0 {
        return;
    }
    let glow = unsafe { capsule_glow(hwnd) };
    let center = if gear { 514.0 } else { 460.0 };
    let mut pixels = Vec::with_capacity((width * height) as usize);
    for row in 0..height {
        let y = (origin_y as f32 + row as f32 + 0.5) / scale;
        for col in 0..width {
            let x = (origin_x as f32 + col as f32 + 0.5) / scale;
            let mut color = capsule_color(x, y, glow);
            if let Some(icon) = icon {
                let x = x - center;
                let y = y - 37.0;
                let subpixel = 0.25 / scale;
                let mut coverage = 0.0;
                for (dx, dy) in [
                    (-subpixel, -subpixel),
                    (subpixel, -subpixel),
                    (-subpixel, subpixel),
                    (subpixel, subpixel),
                ] {
                    if capsule_icon_at(x + dx, y + dy, gear) {
                        coverage += 0.25;
                    }
                }
                color = blend(color, icon, coverage);
            }
            pixels.push(dib_rgb(color));
        }
    }
    unsafe { blit_pixels(dc, dest, &pixels) };
}

unsafe fn blit_pixels(dc: HDC, dest: RECT, pixels: &[u32]) {
    let (width, height) = (dest.right - dest.left, dest.bottom - dest.top);
    let info = BITMAPINFO {
        bmiHeader: BITMAPINFOHEADER {
            biSize: mem::size_of::<BITMAPINFOHEADER>() as u32,
            biWidth: width,
            biHeight: -height,
            biPlanes: 1,
            biBitCount: 32,
            biCompression: BI_RGB,
            ..Default::default()
        },
        ..Default::default()
    };
    unsafe {
        StretchDIBits(
            dc,
            dest.left,
            dest.top,
            width,
            height,
            0,
            0,
            width,
            height,
            pixels.as_ptr().cast(),
            &info,
            DIB_RGB_COLORS,
            SRCCOPY,
        )
    };
}

fn colorref([r, g, b]: [u8; 3]) -> u32 {
    u32::from_le_bytes([r, g, b, 0])
}
fn dib_rgb([r, g, b]: [u8; 3]) -> u32 {
    u32::from_le_bytes([b, g, r, 0])
}

fn blend(base: [u8; 3], light: [u8; 3], amount: f32) -> [u8; 3] {
    std::array::from_fn(|i| {
        (f32::from(base[i]) * (1.0 - amount) + f32::from(light[i]) * amount).round() as u8
    })
}

fn capsule_distance(x: f32, y: f32) -> f32 {
    let qx = (x - WIDTH as f32 / 2.0).abs() - (BODY_WIDTH as f32 / 2.0 - 20.0);
    let qy = (y - HEIGHT as f32 / 2.0).abs() - (BODY_HEIGHT as f32 / 2.0 - 20.0);
    qx.max(0.0).hypot(qy.max(0.0)) + qx.max(qy).min(0.0) - 20.0
}

fn capsule_color(x: f32, y: f32, glow: f32) -> [u8; 3] {
    let tone = ((y - SHADOW_PAD as f32) / BODY_HEIGHT as f32).clamp(0.0, 1.0);
    let distance = capsule_distance(x, y);
    let mut color = blend([28, 28, 30], [255; 3], (26.0 - 18.0 * tone) / 255.0);
    color = blend(color, [100, 210, 255], 0.1 * glow * tone);
    let noise =
        (x as u32).wrapping_mul(0x045d_9f3b).rotate_right(9) ^ (y as u32).wrapping_mul(0x9e37_79b9);
    let grain = if noise.count_ones() & 1 == 0 { 255 } else { 0 };
    color = blend(color, [grain; 3], 0.009);
    // The hairline sits inside a darker transition so it follows the capsule instead of reading as an outline.
    if distance >= -4.0 {
        color = blend(
            color,
            [0; 3],
            (distance + 4.0).clamp(0.0, 1.0) * (72.0 / 255.0),
        );
    }
    if distance >= -2.0 {
        let edge = if tone < 0.35 {
            1.0 - tone / 0.35 * (137.0 / 255.0)
        } else {
            (118.0 - (tone - 0.35) / 0.65 * 30.0) / 255.0
        };
        color = blend(color, [255; 3], (distance + 2.5).clamp(0.0, 1.0) * edge);
    }
    color
}

fn capsule_shadow(x: f32, y: f32, opacity: f32) -> u8 {
    let distance = capsule_distance(x, y);
    if !(0.0..4.5).contains(&distance) {
        return 0;
    }
    let mut alpha = 0.0;
    for (expansion, shade) in [(4.0, 9.0), (3.0, 9.0), (2.0, 18.0), (1.0, 18.0)] {
        let cover = (expansion + 0.5 - distance).clamp(0.0, 1.0);
        let layer = shade / 255.0 * opacity * cover;
        alpha += (1.0 - alpha) * layer;
    }
    (alpha * 255.0).round() as u8
}

fn shadow_bitmap(width: u32, height: u32, opacity: f32) -> WheelBitmap {
    let mut pixels = vec![0; (width * height * 4) as usize];
    let scale = width as f32 / WIDTH as f32;
    for (index, pixel) in pixels.as_chunks_mut::<4>().0.iter_mut().enumerate() {
        let x = index % width as usize;
        let y = index / width as usize;
        pixel[3] = capsule_shadow((x as f32 + 0.5) / scale, (y as f32 + 0.5) / scale, opacity);
    }
    WheelBitmap {
        width,
        height,
        pixels,
    }
}

fn capsule_icon_color(gear: bool, hovered: bool, enabled: bool) -> [u8; 3] {
    if hovered && (gear || enabled) {
        [64, 156, 255]
    } else if gear || enabled {
        [152, 152, 157]
    } else {
        [188, 188, 188]
    }
}

fn capsule_icon_at(x: f32, y: f32, gear: bool) -> bool {
    if !gear {
        // The play glyph's three vertices match the original 20px icon canvas.
        return (-5.0..=8.0).contains(&x) && y.abs() <= 8.0 * (8.0 - x) / 13.0;
    }
    if x.abs() > 12.0 || y.abs() > 12.0 {
        return false;
    }
    // Contours follow the checked-in Material Symbols SVG's 960-unit viewbox.
    const OUTER: &[(i16, i16)] = &[
        (370, 880),
        (354, 752),
        (330, 740),
        (307, 725),
        (188, 775),
        (78, 585),
        (181, 507),
        (180, 480),
        (181, 453),
        (78, 375),
        (188, 185),
        (307, 235),
        (330, 220),
        (354, 208),
        (370, 80),
        (590, 80),
        (606, 208),
        (631, 220),
        (653, 235),
        (772, 185),
        (882, 375),
        (779, 453),
        (780, 480),
        (778, 507),
        (881, 585),
        (771, 775),
        (653, 725),
        (630, 740),
        (606, 752),
        (590, 880),
    ];
    const INNER: &[(i16, i16)] = &[
        (440, 800),
        (519, 800),
        (533, 694),
        (590, 671),
        (639, 633),
        (738, 674),
        (777, 606),
        (691, 541),
        (698, 512),
        (700, 480),
        (698, 449),
        (691, 419),
        (777, 354),
        (738, 286),
        (639, 328),
        (591, 290),
        (533, 266),
        (520, 160),
        (441, 160),
        (427, 266),
        (370, 290),
        (321, 327),
        (222, 286),
        (183, 354),
        (269, 418),
        (262, 448),
        (260, 480),
        (262, 511),
        (269, 541),
        (183, 606),
        (222, 674),
        (321, 632),
        (370, 671),
        (427, 694),
    ];
    let x = 480.0 + x * 40.0;
    let y = 480.0 + y * 40.0;
    (x - 482.0).hypot(y - 480.0) <= 140.0 || (polygon_at(OUTER, x, y) && !polygon_at(INNER, x, y))
}

fn polygon_at(points: &[(i16, i16)], x: f32, y: f32) -> bool {
    points.iter().zip(points.iter().cycle().skip(1)).fold(
        false,
        |inside, (&(x1, y1), &(x2, y2))| {
            let (x1, y1, x2, y2) = (f32::from(x1), f32::from(y1), f32::from(x2), f32::from(y2));
            inside ^ ((y1 > y) != (y2 > y) && x < (x2 - x1) * (y - y1) / (y2 - y1) + x1)
        },
    )
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

#[cfg(test)]
mod overlay_visual_tests {
    use super::*;

    #[test]
    fn glass_panel_is_brighter_at_the_top_and_gets_focus_glow_at_the_bottom() {
        let top = capsule_color(200.0, 17.5, 0.0);
        let bottom = capsule_color(200.0, 55.5, 0.0);
        let focused = capsule_color(200.0, 55.5, 1.0);
        assert!(top[0] > bottom[0]);
        assert!(focused[2] > bottom[2]);
    }

    #[test]
    fn shadow_is_transparent_in_the_panel_and_fades_in_the_outer_padding() {
        assert_eq!(capsule_shadow(200.0, 1.0, 1.0), 0);
        assert_eq!(capsule_shadow(200.0, 13.0, 1.0), 0);
        assert!(capsule_shadow(200.0, 7.75, 1.0) > capsule_shadow(200.0, 4.5, 1.0));
    }

    #[test]
    fn read_only_highlights_for_actionable_text_and_gear_always_can_highlight() {
        assert_eq!(capsule_icon_color(false, true, false), [188, 188, 188]);
        assert_eq!(capsule_icon_color(false, false, true), [152, 152, 157]);
        assert_eq!(capsule_icon_color(false, true, true), [64, 156, 255]);
        assert_eq!(capsule_icon_color(true, true, false), [64, 156, 255]);
    }

    #[test]
    fn native_edit_updates_read_button_and_keyboard_keeps_gear_accessible() {
        unsafe {
            let instance = windows_sys::Win32::System::LibraryLoader::GetModuleHandleW(ptr::null());
            let host = host_window(instance).unwrap();
            let mut overlay = Overlay::new(instance, host).unwrap();
            let read = GetDlgItem(overlay.hwnd, READ);
            let gear = GetDlgItem(overlay.hwnd, SETTINGS);
            SetWindowTextW(overlay.entry, w!(" \t "));
            assert_eq!(IsWindowEnabled(read), 0);
            SetWindowTextW(overlay.entry, w!("测试朗读"));
            assert_ne!(IsWindowEnabled(read), 0);
            let settings = Settings {
                overlay_placeholder: "更新的提示".into(),
                ..Settings::default()
            };
            overlay.set_visual_settings(&settings, 96).unwrap();
            assert_eq!(overlay.text(), "测试朗读");
            SendMessageW(gear, WM_CHAR, VK_RETURN.into(), 0);
            let mut message = MSG::default();
            assert_ne!(
                PeekMessageW(
                    &mut message,
                    host,
                    OVERLAY_MESSAGE,
                    OVERLAY_MESSAGE,
                    PM_REMOVE
                ),
                0
            );
            assert_eq!(message.wParam, OVERLAY_SETTINGS);
            SendMessageW(overlay.hwnd, WM_ACTIVATE, WA_INACTIVE as usize, 0);
            assert_ne!(
                PeekMessageW(
                    &mut message,
                    host,
                    OVERLAY_MESSAGE,
                    OVERLAY_MESSAGE,
                    PM_REMOVE
                ),
                0
            );
            assert_eq!(message.wParam, OVERLAY_DEACTIVATED);
            drop(overlay);
            DestroyWindow(host);
        }
    }
}
