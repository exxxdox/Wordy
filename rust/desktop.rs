use std::{io, iter, mem, ptr};
use windows_sys::Win32::Foundation::{
    ERROR_CLASS_ALREADY_EXISTS, HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM,
};
use windows_sys::Win32::Graphics::Dwm::DwmSetWindowAttribute;
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::System::Registry::{
    HKEY_CURRENT_USER, KEY_WRITE, REG_OPTION_NON_VOLATILE, REG_SZ, RegCloseKey, RegCreateKeyExW,
    RegSetValueExW,
};
use windows_sys::Win32::UI::Shell::*;
use windows_sys::Win32::UI::WindowsAndMessaging::*;
use windows_sys::core::w;

mod overlay;

pub use overlay::{
    OVERLAY_DEACTIVATED, OVERLAY_DISMISS, OVERLAY_MESSAGE, OVERLAY_POSITION, OVERLAY_READ,
    OVERLAY_SETTINGS, OVERLAY_SUBMIT, Overlay,
};

pub const TRAY_MESSAGE: u32 = WM_APP + 1;
pub const TRAY_OVERLAY: usize = 1;
pub const TRAY_SETTINGS: usize = 2;
pub const TRAY_EXIT: usize = 3;
pub const TRAY_LOGS: usize = 4;
pub const TRAY_COMMAND: u32 = WM_APP + 4;
pub const TRAY_EVENT: u32 = WM_APP + 5;

pub(crate) fn set_dwm(hwnd: HWND, attribute: i32, value: i32) {
    unsafe {
        DwmSetWindowAttribute(
            hwnd,
            attribute as u32,
            ptr::from_ref(&value).cast(),
            mem::size_of_val(&value) as u32,
        )
    };
}

pub(crate) fn paint_buffered(hwnd: HWND, draw: impl FnOnce(HDC, RECT)) {
    unsafe {
        let mut paint = PAINTSTRUCT::default();
        let target = BeginPaint(hwnd, &mut paint);
        let mut rect = RECT::default();
        GetClientRect(hwnd, &mut rect);
        paint_buffered_rect(target, rect, draw);
        EndPaint(hwnd, &paint);
    }
}

pub(crate) fn paint_buffered_rect(target: HDC, rect: RECT, draw: impl FnOnce(HDC, RECT)) {
    let (width, height) = (rect.right - rect.left, rect.bottom - rect.top);
    if target.is_null() || width <= 0 || height <= 0 {
        return;
    }
    unsafe {
        let buffer = CreateCompatibleDC(target);
        let bitmap = CreateCompatibleBitmap(target, width, height);
        let mut original = ptr::null_mut();
        if !buffer.is_null() && !bitmap.is_null() {
            original = SelectObject(buffer, bitmap);
        }
        if original.is_null() {
            draw(target, rect);
        } else {
            SetViewportOrgEx(buffer, -rect.left, -rect.top, ptr::null_mut());
            // Publish one complete frame without exposing the background between draw calls.
            draw(buffer, rect);
            BitBlt(
                target, rect.left, rect.top, width, height, buffer, rect.left, rect.top, SRCCOPY,
            );
            SelectObject(buffer, original);
        }
        if !bitmap.is_null() {
            DeleteObject(bitmap);
        }
        if !buffer.is_null() {
            DeleteDC(buffer);
        }
    }
}

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
    if msg == TRAY_MESSAGE {
        // Shell callbacks can bypass GetMessage's queue; defer app actions without reentrant handling.
        unsafe { PostMessageW(hwnd, TRAY_EVENT, wp, lp) };
        return 0;
    }
    if msg == WM_CLOSE {
        unsafe { PostQuitMessage(0) };
        return 0;
    }
    unsafe { DefWindowProcW(hwnd, msg, wp, lp) }
}

fn lookup_class(class: &WNDCLASSW) -> io::Result<()> {
    if unsafe { RegisterClassW(class) } == 0 {
        let error = io::Error::last_os_error();
        // Another GUI thread may have registered the same process-wide class.
        if error.raw_os_error() != Some(ERROR_CLASS_ALREADY_EXISTS as i32) {
            return Err(error);
        }
    }
    Ok(())
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

    pub fn open_menu(&self) -> io::Result<()> {
        // Native popup: `enable_dark_mode` already opted the process into the
        // Windows 11 dark menu theme, so no custom drawing is needed. Tray
        // callbacks now reach the event loop, so the modal menu is safe here.
        unsafe {
            let menu = CreatePopupMenu();
            if menu.is_null() {
                return Err(io::Error::last_os_error());
            }
            AppendMenuW(menu, MF_STRING, TRAY_OVERLAY, w!("显示输入框"));
            AppendMenuW(menu, MF_STRING, TRAY_SETTINGS, w!("设置"));
            AppendMenuW(menu, MF_STRING, TRAY_LOGS, w!("打开日志窗口"));
            AppendMenuW(menu, MF_SEPARATOR, 0, ptr::null());
            AppendMenuW(menu, MF_STRING, TRAY_EXIT, w!("退出"));
            let mut position = POINT::default();
            GetCursorPos(&mut position);
            // The menu needs its owner in the foreground to dismiss when the
            // user clicks elsewhere, so try before showing it.
            SetForegroundWindow(self.host);
            let command = TrackPopupMenu(
                menu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY | TPM_WORKAREA,
                position.x,
                position.y,
                0,
                self.host,
                ptr::null(),
            );
            // Documented workaround so the menu's owner can be activated again.
            PostMessageW(self.host, WM_NULL, 0, 0);
            DestroyMenu(menu);
            if command != 0 {
                // Same queue as the dismissed custom menu: actions stay in the loop.
                PostMessageW(self.host, TRAY_COMMAND, command as usize, 0);
            }
        }
        Ok(())
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
/// Opt into Windows 11 dark menus; unsupported builds keep the system menu.
pub fn enable_dark_mode() {
    use windows_sys::Win32::System::LibraryLoader::{GetProcAddress, LoadLibraryW};
    // Ordinal 135 of uxtheme is the undocumented SetPreferredAppMode. Only a
    // build that ships that export is safe to call; an older uxtheme may map the
    // ordinal to another function.
    const DARK_MODE_BUILD: u32 = 17763;
    unsafe {
        if windows_build() < DARK_MODE_BUILD {
            return;
        }
        let module = LoadLibraryW(w!("uxtheme.dll"));
        if module.is_null() {
            return;
        }
        let set_mode = GetProcAddress(module, 135usize as *const u8);
        if let Some(set_mode) = set_mode {
            let set_mode: unsafe extern "system" fn(i32) -> i32 = std::mem::transmute(set_mode);
            set_mode(2);
        }
    }
}

/// Current Windows build from ntdll, which reports the real version even
/// without a compatibility manifest.
fn windows_build() -> u32 {
    use windows_sys::Win32::{
        Foundation::HMODULE,
        System::{
            LibraryLoader::{GetProcAddress, LoadLibraryW},
            SystemInformation::OSVERSIONINFOW,
        },
    };
    type RtlGetVersion = unsafe extern "system" fn(*mut OSVERSIONINFOW) -> i32;
    unsafe {
        let module: HMODULE = LoadLibraryW(w!("ntdll.dll"));
        if module.is_null() {
            return 0;
        }
        let Some(get_version) = GetProcAddress(module, c"RtlGetVersion".as_ptr().cast()) else {
            return 0;
        };
        let get_version: RtlGetVersion = std::mem::transmute(get_version);
        let mut info = OSVERSIONINFOW {
            dwOSVersionInfoSize: std::mem::size_of::<OSVERSIONINFOW>() as u32,
            ..Default::default()
        };
        if get_version(&mut info) == 0 {
            info.dwBuildNumber
        } else {
            0
        }
    }
}

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

pub(crate) fn wordy_icon() -> io::Result<HICON> {
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

const CENTERED_EDIT: usize = 9;

/// Win32 draws single-line edit text at the top of the client area. Shrink the
/// client to one text line so the text sits vertically centered, and paint the
/// rest of the field with `background`; editing behaviour stays single-line.
#[allow(clippy::not_unsafe_ptr_arg_deref)]
pub fn center_edit_text(hwnd: HWND, background: u32) {
    unsafe {
        SetWindowSubclass(
            hwnd,
            Some(centered_edit_proc),
            CENTERED_EDIT,
            background as usize,
        );
        // Force NCCALCSIZE so the text band is in place before the control is shown.
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

unsafe extern "system" fn centered_edit_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    background: usize,
) -> LRESULT {
    unsafe {
        match message {
            WM_NCCALCSIZE if wparam != 0 => {
                let result = DefSubclassProc(hwnd, message, wparam, lparam);
                let bounds = &mut (*(lparam as *mut NCCALCSIZE_PARAMS)).rgrc[0];
                let height = edit_line_height(hwnd).max(1);
                let top = bounds.top + ((bounds.bottom - bounds.top - height) / 2).max(0);
                bounds.top = top;
                bounds.bottom = (top + height).min(bounds.bottom);
                result
            }
            WM_NCPAINT => {
                // Only the bands outside the centered client belong here; the
                // client itself paints the text with the edit's brush.
                let mut window = RECT::default();
                let mut client = RECT::default();
                GetWindowRect(hwnd, &mut window);
                GetClientRect(hwnd, &mut client);
                let mut origin = POINT::default();
                ClientToScreen(hwnd, &mut origin);
                let left = origin.x - window.left;
                let top = origin.y - window.top;
                let right = left + client.right;
                let bottom = top + client.bottom;
                let (width, height) = (window.right - window.left, window.bottom - window.top);
                let dc = GetWindowDC(hwnd);
                if !dc.is_null() {
                    let brush = CreateSolidBrush(background as u32);
                    for band in [
                        RECT {
                            left: 0,
                            top: 0,
                            right: width,
                            bottom: top,
                        },
                        RECT {
                            left: 0,
                            top: bottom,
                            right: width,
                            bottom: height,
                        },
                        RECT {
                            left: 0,
                            top,
                            right: left,
                            bottom,
                        },
                        RECT {
                            left: right,
                            top,
                            right: width,
                            bottom,
                        },
                    ] {
                        FillRect(dc, &band, brush);
                    }
                    DeleteObject(brush);
                    ReleaseDC(hwnd, dc);
                }
                DefSubclassProc(hwnd, message, wparam, lparam)
            }
            WM_NCDESTROY => DefSubclassProc(hwnd, message, wparam, lparam),
            // The centred band depends on the font height; recompute when the
            // caller applies the settings font after construction.
            WM_SETFONT => {
                let result = DefSubclassProc(hwnd, message, wparam, lparam);
                SetWindowPos(
                    hwnd,
                    ptr::null_mut(),
                    0,
                    0,
                    0,
                    0,
                    SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED,
                );
                result
            }
            // The whole field must focus the edit, not only the centered band.
            WM_NCHITTEST => HTCLIENT as LRESULT,
            _ => DefSubclassProc(hwnd, message, wparam, lparam),
        }
    }
}

fn edit_line_height(hwnd: HWND) -> i32 {
    let mut metrics = TEXTMETRICW::default();
    let dc = unsafe { GetDC(hwnd) };
    if dc.is_null() {
        return 0;
    }
    let font = unsafe { SendMessageW(hwnd, WM_GETFONT, 0, 0) } as HFONT;
    let previous = if font.is_null() {
        ptr::null_mut()
    } else {
        unsafe { SelectObject(dc, font) }
    };
    let measured = unsafe { GetTextMetricsW(dc, &mut metrics) };
    if !previous.is_null() {
        unsafe { SelectObject(dc, previous) };
    }
    unsafe { ReleaseDC(hwnd, dc) };
    if measured != 0 { metrics.tmHeight } else { 0 }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tray_callbacks_reach_the_event_queue_without_reposting_forever() {
        unsafe {
            let instance = windows_sys::Win32::System::LibraryLoader::GetModuleHandleW(ptr::null());
            let host = host_window(instance).unwrap();
            for event in [WM_RBUTTONUP, WM_CONTEXTMENU, WM_LBUTTONDBLCLK] {
                SendMessageW(host, TRAY_MESSAGE, 1, event as isize);
                let mut message = MSG::default();
                let received = PeekMessageW(&mut message, host, TRAY_EVENT, TRAY_EVENT, PM_REMOVE);
                if received == 0 {
                    DestroyWindow(host);
                }
                assert_ne!(received, 0, "tray callback {event:#x} was swallowed");
                assert_eq!(message.wParam, 1);
                assert_eq!(message.lParam, event as isize);
                DispatchMessageW(&message);
                assert_eq!(
                    PeekMessageW(&mut message, host, TRAY_EVENT, TRAY_EVENT, PM_REMOVE),
                    0,
                    "dispatching the internal event must not enqueue it again"
                );
            }
            DestroyWindow(host);
        }
    }
}
