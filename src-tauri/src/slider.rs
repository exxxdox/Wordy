use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, RECT},
    Graphics::Gdi::InvalidateRect,
    UI::{
        Controls::{
            TB_ENDTRACK, TB_THUMBTRACK, TBM_GETCHANNELRECT, TBM_GETRANGEMAX, TBM_GETRANGEMIN,
            TBM_SETPOS,
        },
        Input::KeyboardAndMouse::{
            GetCapture, IsWindowEnabled, ReleaseCapture, SetCapture, SetFocus,
        },
        WindowsAndMessaging::{
            GetParent, SendMessageW, WM_CANCELMODE, WM_CAPTURECHANGED, WM_HSCROLL,
            WM_LBUTTONDBLCLK, WM_LBUTTONDOWN, WM_LBUTTONUP, WM_MOUSEMOVE, WM_USER,
        },
    },
};

pub(crate) const TBM_GETPOS: u32 = WM_USER;

struct Track {
    channel: RECT,
    min: i32,
    max: i32,
}

impl Track {
    fn read(hwnd: HWND) -> Self {
        let mut channel = RECT::default();
        unsafe {
            SendMessageW(
                hwnd,
                TBM_GETCHANNELRECT,
                0,
                (&mut channel as *mut RECT) as LPARAM,
            );
            Self {
                channel,
                min: SendMessageW(hwnd, TBM_GETRANGEMIN, 0, 0) as i32,
                max: SendMessageW(hwnd, TBM_GETRANGEMAX, 0, 0) as i32,
            }
        }
    }

    fn span(&self) -> i32 {
        // The channel's RECT has an exclusive right edge.
        (self.channel.right - self.channel.left - 1).max(1)
    }
}

pub(crate) fn thumb_geometry(hwnd: HWND) -> (RECT, i32) {
    let track = Track::read(hwnd);
    let value = unsafe { SendMessageW(hwnd, TBM_GETPOS, 0, 0) } as i32;
    let ratio = f64::from(value - track.min) / f64::from((track.max - track.min).max(1));
    let center = track.channel.left + (ratio * f64::from(track.span())).round() as i32;
    (track.channel, center)
}

pub(crate) fn set_position(hwnd: HWND, value: i32) {
    if hwnd.is_null() {
        return;
    }
    unsafe {
        SendMessageW(hwnd, TBM_SETPOS, 0, value as LPARAM);
        // The native redraw misses the previous custom thumb and progress fill.
        InvalidateRect(hwnd, std::ptr::null(), 0);
    }
}

pub(crate) fn handle_pointer(hwnd: HWND, message: u32, point: LPARAM) -> Option<LRESULT> {
    unsafe {
        match message {
            WM_LBUTTONDOWN | WM_LBUTTONDBLCLK if IsWindowEnabled(hwnd) != 0 => {
                SetFocus(hwnd);
                SetCapture(hwnd);
            }
            WM_MOUSEMOVE | WM_LBUTTONUP if GetCapture() == hwnd => {}
            WM_CANCELMODE if GetCapture() == hwnd => {
                ReleaseCapture();
                return Some(0);
            }
            WM_CAPTURECHANGED => {
                SendMessageW(
                    GetParent(hwnd),
                    WM_HSCROLL,
                    TB_ENDTRACK as usize,
                    hwnd as LPARAM,
                );
                return None;
            }
            _ => return None,
        }
        // Native rail clicks page up/down. Map pointer input to the same track used to paint the knob.
        let track = Track::read(hwnd);
        let offset = (i32::from(point as i16) - track.channel.left).clamp(0, track.span());
        let ratio = f64::from(offset) / f64::from(track.span());
        let value = track.min + (ratio * f64::from(track.max - track.min)).round() as i32;
        if SendMessageW(hwnd, TBM_GETPOS, 0, 0) != value as isize {
            set_position(hwnd, value);
            let scroll = TB_THUMBTRACK as usize | ((value as u16 as usize) << 16);
            SendMessageW(GetParent(hwnd), WM_HSCROLL, scroll, hwnd as LPARAM);
        }
        if message == WM_LBUTTONUP {
            ReleaseCapture();
        }
    }
    Some(0)
}
