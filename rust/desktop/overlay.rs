use std::{io, mem, ptr};
use windows_sys::Win32::Foundation::{HINSTANCE, HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Dwm::DWMWA_CLOAK;
use windows_sys::Win32::Graphics::Gdi::*;
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
use windows_sys::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::*;
use windows_sys::core::w;

use crate::config::{Settings, WindowPosition};
use crate::wheel::Point;
use crate::wheel_paint::WheelBitmap;
use crate::wheel_window::LayeredSurface;

use super::{lookup_class, set_dwm, wide, window_text};

pub const OVERLAY_MESSAGE: u32 = WM_APP + 2;
pub const OVERLAY_SUBMIT: usize = 1;
pub const OVERLAY_READ: usize = 2;
pub const OVERLAY_SETTINGS: usize = 3;
pub const OVERLAY_POSITION: usize = 4;
pub const OVERLAY_DISMISS: usize = 5;
pub const OVERLAY_DEACTIVATED: usize = 6;

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

mod input;
mod paint;

use input::{edit_proc, enable_read, overlay_button_proc, overlay_proc};
use paint::{capsule_glow, draw_backdrop};
// LWA_COLORKEY treats exactly this RGB as transparent; the input surface paints it everywhere text is not.
// Matches the capsule mid-tone, so antialiased glyph edges blend into the backdrop.
const OVERLAY_KEY: u32 = 0x002a_3038;
const OVERLAY_OPACITY: windows_sys::core::PCWSTR = w!("WordyOpacity");

pub struct Overlay {
    hwnd: HWND,
    backdrop: HWND,
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
                WS_POPUP | WS_CLIPCHILDREN,
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
                entry: ptr::null_mut(),
                font: ptr::null_mut(),
                prompt_font: ptr::null_mut(),
                placeholder: vec![0],
                previous_foreground: ptr::null_mut(),
            };
            // Configure transparency before any child paints and keep it stable across reopens.
            if SetLayeredWindowAttributes(hwnd, OVERLAY_KEY, 255, LWA_COLORKEY) == 0 {
                return Err(io::Error::last_os_error());
            }
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
                MoveWindow(child, px(x), px(y), px(w), px(h), 0);
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
            // The parent and native child surfaces must finish their first paint before DWM shows them.
            set_dwm(self.hwnd, DWMWA_CLOAK, 1);
            SetWindowPos(
                self.hwnd,
                HWND_TOPMOST,
                x,
                y,
                width,
                height,
                SWP_SHOWWINDOW | SWP_NOACTIVATE,
            );
            // The capsule now lives on the backdrop window; keep it directly under the text layer.
            SetWindowPos(
                self.backdrop,
                self.hwnd,
                0,
                0,
                0,
                0,
                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE,
            );
            RedrawWindow(
                self.hwnd,
                ptr::null(),
                ptr::null_mut(),
                RDW_INVALIDATE | RDW_ALLCHILDREN | RDW_UPDATENOW,
            );
            set_dwm(self.hwnd, DWMWA_CLOAK, 0);
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
        let dpi = unsafe { GetDpiForWindow(self.hwnd) }.max(96);
        self.set_visual_settings(settings, dpi)
    }

    pub fn apply_opacity(&self, opacity: f64) -> io::Result<()> {
        if !self.is_visible() {
            return Ok(());
        }
        let mut bounds = RECT::default();
        if unsafe { GetWindowRect(self.hwnd, &mut bounds) } == 0 {
            return Err(io::Error::last_os_error());
        }
        // Opacity belongs to the backdrop, so an active drag leaves the text and icons intact.
        self.paint_shadow(
            bounds.left,
            bounds.top,
            bounds.right - bounds.left,
            bounds.bottom - bounds.top,
            opacity,
        )
    }

    fn set_visual_settings(&mut self, settings: &Settings, dpi: u32) -> io::Result<()> {
        let pixel = |value| (value * dpi as i32 + 48) / 96;
        let font_name = wide(crate::config::ui_font_family(&settings.ui_font));
        let placeholder = wide(&settings.overlay_placeholder);
        unsafe {
            if SetPropW(
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
        &self,
        x: i32,
        y: i32,
        width: i32,
        height: i32,
        opacity: f64,
    ) -> io::Result<()> {
        draw_backdrop(self.backdrop, x, y, width, height, opacity, unsafe {
            capsule_glow(self.hwnd)
        })?;
        let alpha = (opacity.clamp(0.0, 1.0) * 1000.0).round() as usize;
        unsafe {
            SetPropW(
                self.hwnd,
                OVERLAY_OPACITY,
                ptr::without_provenance_mut(alpha),
            )
        };
        Ok(())
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

#[cfg(test)]
mod overlay_visual_tests {
    use super::super::host_window;
    use super::paint::*;
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
    fn overlay_opacity_sets_the_backdrop_alpha() {
        let center = (WIDTH as usize * (HEIGHT as usize / 2) + WIDTH as usize / 2) * 4;
        for (opacity, alpha) in [(0.30, 77), (0.65, 166), (1.0, 255)] {
            let backdrop = overlay_bitmap(WIDTH as u32, HEIGHT as u32, opacity, 0.0);
            assert_eq!(backdrop.pixels[center + 3], alpha);
        }
    }

    #[test]
    fn updating_visible_overlay_opacity_leaves_its_text_layer_intact() {
        unsafe {
            let instance = windows_sys::Win32::System::LibraryLoader::GetModuleHandleW(ptr::null());
            let mut overlay = Overlay::new(instance, ptr::null_mut()).unwrap();
            overlay
                .set_visual_settings(&Settings::default(), 96)
                .unwrap();
            SendMessageW(overlay.entry, WM_SETTEXT, 0, w!("保留输入") as LPARAM);
            SetWindowPos(
                overlay.hwnd,
                ptr::null_mut(),
                -32000,
                -32000,
                WIDTH,
                HEIGHT,
                SWP_NOACTIVATE | SWP_NOZORDER | SWP_SHOWWINDOW,
            );
            let font = SendMessageW(overlay.entry, WM_GETFONT, 0, 0);
            overlay.apply_opacity(0.55).unwrap();
            assert_eq!(GetPropW(overlay.hwnd, OVERLAY_OPACITY) as usize, 550);
            assert_eq!(SendMessageW(overlay.entry, WM_GETFONT, 0, 0), font);
            assert_eq!(overlay.text(), "保留输入");
        }
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
    fn native_overlay_buttons_use_the_color_key_for_background_requests() {
        unsafe {
            let instance = windows_sys::Win32::System::LibraryLoader::GetModuleHandleW(ptr::null());
            let overlay = Overlay::new(instance, ptr::null_mut()).unwrap();
            let dc = CreateCompatibleDC(ptr::null_mut());
            for id in [READ, SETTINGS] {
                let button = GetDlgItem(overlay.hwnd, id);
                let brush =
                    SendMessageW(overlay.hwnd, WM_CTLCOLORBTN, dc as usize, button as isize);
                let mut info = LOGBRUSH::default();
                let bytes = GetObjectW(
                    brush as HGDIOBJ,
                    mem::size_of_val(&info) as i32,
                    ptr::from_mut(&mut info).cast(),
                );
                assert_eq!(bytes as usize, mem::size_of_val(&info));
                assert_eq!(info.lbColor, OVERLAY_KEY);
                assert_eq!(GetBkColor(dc), OVERLAY_KEY);
            }
            DeleteDC(dc);
        }
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
