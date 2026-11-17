use std::cell::Cell;
use std::ffi::c_void;
use std::mem::size_of;
use std::ptr;
use std::time::Instant;

use windows::core::{Error, Result, w};
use windows_sys::Win32::Foundation::{
    ERROR_CLASS_ALREADY_EXISTS, GetLastError, HINSTANCE, HWND, LPARAM, LRESULT, POINT, SIZE, WPARAM,
};
use windows_sys::Win32::Graphics::Gdi::{
    AC_SRC_ALPHA, AC_SRC_OVER, BI_RGB, BITMAPINFO, BITMAPINFOHEADER, BLENDFUNCTION,
    CreateCompatibleDC, CreateDIBSection, DIB_RGB_COLORS, DeleteDC, DeleteObject, GetMonitorInfoW,
    HBITMAP, HDC, HGDIOBJ, MONITOR_DEFAULTTONEAREST, MONITORINFO, MonitorFromPoint, SelectObject,
};
use windows_sys::Win32::UI::HiDpi::{GetDpiForMonitor, MDT_EFFECTIVE_DPI};
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_ESCAPE, VK_RBUTTON};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

use crate::wheel::{Point, Rect, SLOT_COUNT, WheelState, WheelStyle};
use crate::wheel_paint::{WheelBitmap, WheelFrame, WheelRenderer};

const EX_STYLE: u32 = WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW | WS_EX_TOPMOST;

#[derive(Debug, Clone, Copy, PartialEq)]
enum MouseEvent {
    Select(Point),
    Cancel,
}

#[derive(Clone, Copy, PartialEq)]
struct Visuals {
    active: bool,
    opacity: f64,
    selected: Option<usize>,
    highlights: [f64; SLOT_COUNT],
}

pub struct WheelWindow {
    hwnd: HWND,
    event: Box<Cell<Option<MouseEvent>>>,
    renderer: WheelRenderer,
    state: WheelState,
    surface: Option<LayeredSurface>,
    visuals: Option<Visuals>,
    visible: bool,
    clock: Instant,
    monitor_origin: Point,
    scale: f64,
}

impl WheelWindow {
    pub(crate) fn new(instance: HINSTANCE) -> Result<Self> {
        let class = w!("Wordy.Rust.ChatWheel");
        let renderer = WheelRenderer::new()?;
        let event = Box::new(Cell::new(None));
        let window_class = WNDCLASSW {
            lpfnWndProc: Some(window_proc),
            hInstance: instance,
            lpszClassName: class.as_ptr(),
            hCursor: unsafe { LoadCursorW(ptr::null_mut(), IDC_ARROW) },
            ..Default::default()
        };
        if unsafe { RegisterClassW(&window_class) } == 0
            && unsafe { GetLastError() } != ERROR_CLASS_ALREADY_EXISTS
        {
            return Err(Error::from_thread());
        }
        let hwnd = unsafe {
            CreateWindowExW(
                EX_STYLE,
                class.as_ptr(),
                w!("Wordy · 音频轮盘").as_ptr(),
                WS_POPUP,
                0,
                0,
                0,
                0,
                ptr::null_mut(),
                ptr::null_mut(),
                instance,
                ptr::from_ref(event.as_ref()).cast(),
            )
        };
        if hwnd.is_null() {
            return Err(Error::from_thread());
        }
        Ok(Self {
            hwnd,
            event,
            renderer,
            state: WheelState::default(),
            surface: None,
            visuals: None,
            visible: false,
            clock: Instant::now(),
            monitor_origin: Point::default(),
            scale: 1.0,
        })
    }

    pub fn is_active(&self) -> bool {
        self.state.is_active()
    }

    pub fn is_visible(&self) -> bool {
        self.visible
    }

    pub fn open(&mut self, labels: &[String], style: WheelStyle, key: u16) -> Result<bool> {
        if self.state.is_active() {
            return Ok(false);
        }
        let cursor = cursor_position()?;
        let monitor = unsafe { MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST) };
        let mut info = MONITORINFO {
            cbSize: size_of::<MONITORINFO>() as u32,
            ..Default::default()
        };
        if unsafe { GetMonitorInfoW(monitor, &mut info) } == 0 {
            return Err(Error::from_thread());
        }
        let (mut dpi, mut dpi_y) = (96, 96);
        let result = unsafe { GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut dpi, &mut dpi_y) };
        self.scale = if result >= 0 && dpi > 0 {
            f64::from(dpi) / 96.0
        } else {
            1.0
        };
        self.monitor_origin = Point::new(
            f64::from(info.rcMonitor.left),
            f64::from(info.rcMonitor.top),
        );
        let work = info.rcWork;
        let bounds = Rect::new(
            f64::from(work.left),
            f64::from(work.top),
            f64::from(work.right - work.left),
            f64::from(work.bottom - work.top),
        )
        .to_logical(self.monitor_origin, self.scale);
        let origin = screen_point(cursor).to_logical(self.monitor_origin, self.scale);
        self.event.set(None);
        self.state
            .open(self.now(), origin, bounds, labels, style, Some(key));
        self.visuals = None;
        if let Err(error) = self.present() {
            self.cancel();
            return Err(error);
        }
        Ok(true)
    }

    /// Reads the invocation key and mouse state without painting or moving the cursor.
    pub fn tick(&mut self) -> Option<usize> {
        if !self.state.is_active() {
            self.event.set(None);
            return None;
        }
        let now = self.now();
        match self.event.take() {
            Some(MouseEvent::Select(physical)) => {
                let local = Point::new(physical.x / self.scale, physical.y / self.scale);
                return self.state.click(now, local);
            }
            Some(MouseEvent::Cancel) => {
                self.state.dismiss(now);
                return None;
            }
            None => {}
        }
        let cancel = key_down(VK_ESCAPE) || key_down(VK_RBUTTON);
        if cancel {
            self.state.dismiss(now);
            return None;
        }
        let cursor =
            screen_point(cursor_position().ok()?).to_logical(self.monitor_origin, self.scale);
        let down = self.state.key().is_some_and(key_down);
        self.state.poll(now, cursor, down, false)
    }

    /// Called after the host dispatches the slot so drawing never delays the playback submission.
    pub fn present(&mut self) -> Result<()> {
        let now = self.now();
        if !self.state.is_visible(now) {
            if self.visible {
                self.cancel();
            }
            return Ok(());
        }
        let visuals = Visuals {
            active: self.state.is_active(),
            opacity: self.state.opacity(now),
            selected: self.state.selected(),
            highlights: self.state.highlights(now),
        };
        if self.visuals == Some(visuals) {
            return Ok(());
        }
        if self.visuals.is_none_or(|old| old.active != visuals.active) {
            // The closing window can remain visible, but it must stop owning mouse input.
            let style = if visuals.active {
                EX_STYLE
            } else {
                EX_STYLE | WS_EX_TRANSPARENT
            };
            if unsafe { SetWindowLongPtrW(self.hwnd, GWL_EXSTYLE, style as isize) } == 0 {
                return Err(Error::from_thread());
            }
        }
        let mut frame = WheelFrame::new(self.state.labels(), self.state.style());
        frame.opacity = visuals.opacity;
        frame.selected = visuals.selected;
        frame.highlights = Some(visuals.highlights);
        frame.anchor = self.state.anchor();
        let bitmap = self.renderer.render(frame, self.scale)?;
        if self
            .surface
            .as_ref()
            .is_none_or(|surface| surface.width != bitmap.width || surface.height != bitmap.height)
        {
            self.surface = Some(LayeredSurface::new(bitmap.width, bitmap.height)?);
        }
        let position = self
            .state
            .position()
            .to_physical(self.monitor_origin, self.scale);
        self.surface
            .as_mut()
            .unwrap()
            .present(self.hwnd, position, &bitmap)?;
        if !self.visible {
            unsafe { ShowWindow(self.hwnd, SW_SHOWNOACTIVATE) };
            self.visible = true;
        }
        self.visuals = Some(visuals);
        Ok(())
    }

    pub fn dismiss(&mut self) {
        self.state.dismiss(self.now());
    }

    pub fn cancel(&mut self) {
        self.state.cancel();
        self.visuals = None;
        self.event.set(None);
        if self.visible {
            unsafe { ShowWindow(self.hwnd, SW_HIDE) };
            self.visible = false;
        }
    }

    fn now(&self) -> u64 {
        self.clock.elapsed().as_millis() as u64
    }
}

impl Drop for WheelWindow {
    fn drop(&mut self) {
        // Destroying the window first invalidates its callback pointer before freeing the event cell.
        unsafe { DestroyWindow(self.hwnd) };
    }
}

fn cursor_position() -> Result<POINT> {
    let mut position = POINT::default();
    if unsafe { GetCursorPos(&mut position) } == 0 {
        return Err(Error::from_thread());
    }
    Ok(position)
}

fn screen_point(position: POINT) -> Point {
    Point::new(f64::from(position.x), f64::from(position.y))
}

fn key_down(key: u16) -> bool {
    (unsafe { GetAsyncKeyState(i32::from(key)) }) < 0
}

unsafe extern "system" fn window_proc(
    window: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    match message {
        WM_NCCREATE => {
            let event = unsafe { (*(lparam as *const CREATESTRUCTW)).lpCreateParams };
            unsafe { SetWindowLongPtrW(window, GWLP_USERDATA, event as isize) };
        }
        WM_NCDESTROY => {
            unsafe { SetWindowLongPtrW(window, GWLP_USERDATA, 0) };
        }
        WM_MOUSEACTIVATE => return MA_NOACTIVATE as LRESULT,
        WM_LBUTTONDOWN | WM_RBUTTONDOWN => {
            let address = unsafe { GetWindowLongPtrW(window, GWLP_USERDATA) };
            if let Some(event) = unsafe { (address as *const Cell<Option<MouseEvent>>).as_ref() }
                && event.get().is_none()
            {
                let next = if message == WM_LBUTTONDOWN {
                    let local =
                        Point::new(f64::from(lparam as i16), f64::from((lparam >> 16) as i16));
                    MouseEvent::Select(local)
                } else {
                    MouseEvent::Cancel
                };
                event.set(Some(next));
            }
            return 0;
        }
        _ => {}
    }
    unsafe { DefWindowProcW(window, message, wparam, lparam) }
}

pub(crate) struct LayeredSurface {
    dc: HDC,
    bitmap: HBITMAP,
    previous: HGDIOBJ,
    pixels: *mut c_void,
    width: u32,
    height: u32,
}

impl LayeredSurface {
    pub(crate) fn new(width: u32, height: u32) -> Result<Self> {
        let info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: width as i32,
                biHeight: -(height as i32),
                biPlanes: 1,
                biBitCount: 32,
                biCompression: BI_RGB,
                ..Default::default()
            },
            ..Default::default()
        };
        let dc = unsafe { CreateCompatibleDC(ptr::null_mut()) };
        if dc.is_null() {
            return Err(Error::from_thread());
        }
        let mut pixels = ptr::null_mut();
        let bitmap =
            unsafe { CreateDIBSection(dc, &info, DIB_RGB_COLORS, &mut pixels, ptr::null_mut(), 0) };
        if bitmap.is_null() || pixels.is_null() {
            let error = Error::from_thread();
            if !bitmap.is_null() {
                unsafe { DeleteObject(bitmap) };
            }
            unsafe { DeleteDC(dc) };
            return Err(error);
        }
        let previous = unsafe { SelectObject(dc, bitmap) };
        if previous.is_null() {
            let error = Error::from_thread();
            unsafe {
                DeleteObject(bitmap);
                DeleteDC(dc);
            }
            return Err(error);
        }
        Ok(Self {
            dc,
            bitmap,
            previous,
            pixels,
            width,
            height,
        })
    }

    pub(crate) fn present(
        &mut self,
        window: HWND,
        position: Point,
        bitmap: &WheelBitmap,
    ) -> Result<()> {
        debug_assert_eq!((self.width, self.height), (bitmap.width, bitmap.height));
        let dest = POINT {
            x: position.x.round() as i32,
            y: position.y.round() as i32,
        };
        let source = POINT::default();
        let size = SIZE {
            cx: self.width as i32,
            cy: self.height as i32,
        };
        let blend = BLENDFUNCTION {
            BlendOp: AC_SRC_OVER as u8,
            BlendFlags: 0,
            SourceConstantAlpha: 255,
            AlphaFormat: AC_SRC_ALPHA as u8,
        };
        // DIB sections and the renderer both use top-down premultiplied BGRA rows.
        let updated = unsafe {
            ptr::copy_nonoverlapping(
                bitmap.pixels.as_ptr(),
                self.pixels.cast(),
                bitmap.pixels.len(),
            );
            UpdateLayeredWindow(
                window,
                ptr::null_mut(),
                &dest,
                &size,
                self.dc,
                &source,
                0,
                &blend,
                ULW_ALPHA,
            )
        };
        if updated == 0 {
            return Err(Error::from_thread());
        }
        Ok(())
    }
}

impl Drop for LayeredSurface {
    fn drop(&mut self) {
        unsafe {
            SelectObject(self.dc, self.previous);
            DeleteObject(self.bitmap);
            DeleteDC(self.dc);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use windows_sys::Win32::UI::WindowsAndMessaging::{
        GetForegroundWindow, IsWindowVisible, SendMessageW,
    };

    #[test]
    fn wheel_window_is_hidden_topmost_layered_and_never_activates() {
        let wheel = WheelWindow::new(ptr::null_mut()).unwrap();
        let styles = unsafe { GetWindowLongPtrW(wheel.hwnd, GWL_EXSTYLE) } as u32;
        assert_eq!(styles & EX_STYLE, EX_STYLE);
        let foreground = unsafe { GetForegroundWindow() };
        assert_eq!(unsafe { IsWindowVisible(wheel.hwnd) }, 0);
        let activation = unsafe { SendMessageW(wheel.hwnd, WM_MOUSEACTIVATE, 0, 0) };
        assert_eq!(activation, MA_NOACTIVATE as LRESULT);
        assert_eq!(unsafe { GetForegroundWindow() }, foreground);
    }

    #[test]
    fn window_proc_captures_local_click_and_right_button_without_activating() {
        let wheel = WheelWindow::new(ptr::null_mut()).unwrap();
        let coordinates = (25 << 16) | 40;
        unsafe { SendMessageW(wheel.hwnd, WM_LBUTTONDOWN, 0, coordinates) };
        assert_eq!(
            wheel.event.take(),
            Some(MouseEvent::Select(Point::new(40.0, 25.0)))
        );
        unsafe { SendMessageW(wheel.hwnd, WM_RBUTTONDOWN, 0, 0) };
        assert_eq!(wheel.event.take(), Some(MouseEvent::Cancel));
        assert_eq!(wheel.event.take(), None);
    }

    #[test]
    fn updates_layered_pixels_and_geometry_while_the_window_is_hidden() {
        let wheel = WheelWindow::new(ptr::null_mut()).unwrap();
        let labels = std::array::from_fn(|_| String::new());
        let bitmap = wheel
            .renderer
            .render(WheelFrame::new(&labels, WheelStyle::Classic), 0.5)
            .unwrap();
        let mut surface = LayeredSurface::new(bitmap.width, bitmap.height).unwrap();
        surface
            .present(wheel.hwnd, Point::new(31.0, 32.0), &bitmap)
            .unwrap();
        let mut bounds = windows_sys::Win32::Foundation::RECT::default();
        assert_ne!(unsafe { GetWindowRect(wheel.hwnd, &mut bounds) }, 0);
        assert_eq!((bounds.left, bounds.top), (31, 32));
        assert_eq!(
            (bounds.right - bounds.left, bounds.bottom - bounds.top),
            (317, 270)
        );
        assert_eq!(unsafe { IsWindowVisible(wheel.hwnd) }, 0);
    }
}
