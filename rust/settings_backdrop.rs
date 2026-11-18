//! The frame and embedded controls share the glass surface so it aligns across HWNDs.

use std::{cell::RefCell, mem::size_of, ptr};

use windows_sys::Win32::{
    Foundation::{HWND, POINT, RECT},
    Graphics::Gdi::*,
    UI::{HiDpi::GetDpiForWindow, WindowsAndMessaging::*},
};

pub(crate) const EDGE_PAD: i32 = 12;
const RADIUS: i32 = 20;
pub(crate) const FOOTER_HEIGHT: i32 = 54;
pub(crate) const COLOR_KEY: u32 = rgb(255, 0, 255);
const SEPARATOR: u32 = rgb(56, 56, 58);

pub(crate) fn scaled(units: i32, dpi: i32) -> i32 {
    (units * dpi.max(96) + 48) / 96
}

pub(crate) const fn rgb(red: u8, green: u8, blue: u8) -> u32 {
    red as u32 | ((green as u32) << 8) | ((blue as u32) << 16)
}

pub(crate) fn paint_shell(hwnd: HWND) {
    let mut paint = PAINTSTRUCT::default();
    let dc = unsafe { BeginPaint(hwnd, &mut paint) };
    let mut window = RECT::default();
    unsafe { GetClientRect(hwnd, &mut window) };
    let maximized = unsafe { IsZoomed(hwnd) != 0 };
    let scale = unsafe { GetDpiForWindow(hwnd) }.max(96) as i32;
    SHELL_CACHE.with_borrow_mut(|cache| {
        let stale = cache.as_ref().is_none_or(|shell| {
            shell.width != window.right
                || shell.height != window.bottom
                || shell.scale != scale
                || shell.maximized != maximized
        });
        if stale {
            *cache = Some(build_shell(window.right, window.bottom, scale, maximized));
        }
        if let Some(shell) = cache.as_ref() {
            // The whole frame is composed once per size; a page switch only blits it.
            unsafe {
                BitBlt(
                    dc,
                    0,
                    0,
                    window.right,
                    window.bottom,
                    shell.dc,
                    0,
                    0,
                    SRCCOPY,
                )
            };
        }
    });
    unsafe { EndPaint(hwnd, &paint) };
}

struct ShellCache {
    width: i32,
    height: i32,
    scale: i32,
    maximized: bool,
    dc: HDC,
    bitmap: HBITMAP,
    previous: HGDIOBJ,
}

impl Drop for ShellCache {
    fn drop(&mut self) {
        unsafe {
            SelectObject(self.dc, self.previous);
            DeleteObject(self.bitmap);
            DeleteDC(self.dc);
        }
    }
}

thread_local! {
    static SHELL_CACHE: RefCell<Option<ShellCache>> = const { RefCell::new(None) };
}

fn build_shell(width: i32, height: i32, scale: i32, maximized: bool) -> ShellCache {
    unsafe {
        let screen = GetDC(ptr::null_mut());
        let dc = CreateCompatibleDC(screen);
        let bitmap = CreateCompatibleBitmap(screen, width.max(1), height.max(1));
        ReleaseDC(ptr::null_mut(), screen);
        let previous = SelectObject(dc, bitmap);
        draw_shell(
            dc,
            RECT {
                left: 0,
                top: 0,
                right: width,
                bottom: height,
            },
            maximized,
            scale,
        );
        ShellCache {
            width,
            height,
            scale,
            maximized,
            dc,
            bitmap,
            previous,
        }
    }
}

fn draw_shell(dc: HDC, window: RECT, maximized: bool, scale: i32) {
    let pad = if maximized {
        0
    } else {
        scaled(EDGE_PAD, scale)
    };
    let radius = if maximized { 0 } else { scaled(RADIUS, scale) };
    let body = RECT {
        left: pad,
        top: pad,
        right: window.right - pad,
        bottom: window.bottom - pad,
    };
    if body.right > body.left && body.bottom > body.top {
        // Clear only the transparent margin. Flooding the whole client with the
        // color key would composite a fully transparent frame before the glass
        // lands, which flashes on every page switch; margin pixels are already
        // transparent, so repainting them is invisible.
        for margin in [
            RECT {
                left: 0,
                top: 0,
                right: window.right,
                bottom: body.top,
            },
            RECT {
                left: 0,
                top: body.bottom,
                right: window.right,
                bottom: window.bottom,
            },
            RECT {
                left: 0,
                top: body.top,
                right: body.left,
                bottom: body.bottom,
            },
            RECT {
                left: body.right,
                top: body.top,
                right: window.right,
                bottom: body.bottom,
            },
        ] {
            fill(dc, &margin, COLOR_KEY);
        }
        // The glass is clipped to a rounded region, so the four corner squares
        // inside the body stay untouched unless they are cleared to the key;
        // otherwise they keep stale/black pixels.
        let corner = radius.min((body.right - body.left) / 2);
        for square in [
            RECT {
                left: body.left,
                top: body.top,
                right: body.left + corner,
                bottom: body.top + corner,
            },
            RECT {
                left: body.right - corner,
                top: body.top,
                right: body.right,
                bottom: body.top + corner,
            },
            RECT {
                left: body.left,
                top: body.bottom - corner,
                right: body.left + corner,
                bottom: body.bottom,
            },
            RECT {
                left: body.right - corner,
                top: body.bottom - corner,
                right: body.right,
                bottom: body.bottom,
            },
        ] {
            fill(dc, &square, COLOR_KEY);
        }
        let clip = unsafe {
            CreateRoundRectRgn(
                body.left,
                body.top,
                body.right + 1,
                body.bottom + 1,
                radius * 2,
                radius * 2,
            )
        };
        let saved = unsafe { SaveDC(dc) };
        unsafe {
            ExtSelectClipRgn(dc, clip, RGN_AND);
            DeleteObject(clip);
        }
        paint_glass(dc, body);
        unsafe { RestoreDC(dc, saved) };
        // The upper edge catches more light than the sides and bottom of the glass.
        for band in 0..16 {
            let top = body.top + (body.bottom - body.top) * band / 16;
            let bottom = body.top + (body.bottom - body.top) * (band + 1) / 16;
            let level = if band == 0 {
                249
            } else {
                (204 - band * 10).max(91) as u8
            };
            let saved = unsafe { SaveDC(dc) };
            unsafe { IntersectClipRect(dc, body.left, top, body.right + 1, bottom + 1) };
            outline(dc, &body, radius, rgb(level, level, level));
            unsafe { RestoreDC(dc, saved) };
        }
        let inset = RECT {
            left: body.left + 1,
            top: body.top + 1,
            right: body.right - 1,
            bottom: body.bottom - 1,
        };
        outline(dc, &inset, (radius - 1).max(0), rgb(56, 59, 64));
        let footer = body.bottom - scaled(FOOTER_HEIGHT, scale);
        stroke(
            dc,
            &[[body.left + 1, footer], [body.right - 1, footer]],
            SEPARATOR,
        );
    } else {
        fill(dc, &window, COLOR_KEY);
    }
}

fn paint_glass(dc: HDC, body: RECT) {
    let (width, height) = (body.right - body.left, body.bottom - body.top);
    if width <= 0 || height <= 0 {
        return;
    }
    GLASS_PIXELS.with_borrow_mut(|cache| {
        if cache.0 != width || cache.1 != height {
            cache.0 = width;
            cache.1 = height;
            cache.2.clear();
            cache.2.reserve(width as usize * height as usize);
            for y in 0..height {
                for x in 0..width {
                    cache.2.push(glass_pixel(x, y, width, height));
                }
            }
        }
        let mut info = BITMAPINFO::default();
        info.bmiHeader.biSize = size_of::<BITMAPINFOHEADER>() as u32;
        info.bmiHeader.biWidth = width;
        info.bmiHeader.biHeight = -height;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        // Controls use the same cached surface translated into their client coordinates.
        unsafe {
            StretchDIBits(
                dc,
                body.left,
                body.top,
                width,
                height,
                0,
                0,
                width,
                height,
                cache.2.as_ptr().cast(),
                &info,
                DIB_RGB_COLORS,
                SRCCOPY,
            )
        };
    });
}

thread_local! {
    static GLASS_PIXELS: RefCell<(i32, i32, Vec<u32>)> = const { RefCell::new((0, 0, Vec::new())) };
}

fn glass_pixel(x: i32, y: i32, width: i32, height: i32) -> u32 {
    let f = y as f32 / height as f32;
    let radius = width as f32 * 0.62;
    let light = (1.0 - (x as f32 - width as f32 * 0.18).hypot(y as f32) / radius).max(0.0);
    let hash =
        (x as u32).wrapping_mul(0x045d_9f3b).rotate_right(9) ^ (y as u32).wrapping_mul(0x9e37_79b9);
    let grain = ((hash ^ (hash >> 16)) % 11) as f32 - 5.0;
    let channel =
        |offset: f32, tint: f32| (53.0 - 26.0 * f + offset + light * tint + grain).round() as u8;
    u32::from_le_bytes([channel(2.0, 29.0), channel(0.0, 16.0), channel(0.0, 5.0), 0])
}

pub(crate) fn paint_embedded_backdrop(hwnd: HWND, dc: HDC) {
    let parent = unsafe { GetAncestor(hwnd, GA_ROOT) };
    let mut client = RECT::default();
    let mut child = RECT::default();
    let mut origin = POINT::default();
    unsafe {
        GetClientRect(hwnd, &mut child);
        GetClientRect(parent, &mut client);
        ClientToScreen(hwnd, &mut origin);
        ScreenToClient(parent, &mut origin);
    }
    let pad = if unsafe { IsZoomed(parent) } != 0 {
        0
    } else {
        EDGE_PAD
    };
    // Match the parent's light direction across every child HWND.
    let saved = unsafe { SaveDC(dc) };
    unsafe { IntersectClipRect(dc, child.left, child.top, child.right, child.bottom) };
    paint_glass(
        dc,
        RECT {
            left: pad - origin.x,
            top: pad - origin.y,
            right: client.right - pad - origin.x,
            bottom: client.bottom - pad - origin.y,
        },
    );
    unsafe { RestoreDC(dc, saved) };
}

pub(crate) fn fill(dc: HDC, rect: &RECT, color: u32) {
    unsafe {
        SetDCBrushColor(dc, color);
        FillRect(dc, rect, GetStockObject(DC_BRUSH));
    }
}

pub(crate) fn round(dc: HDC, rect: &RECT, radius: i32, border: u32, background: Option<u32>) {
    let diameter = radius * 2;
    unsafe {
        let pen = SelectObject(dc, GetStockObject(DC_PEN));
        let brush = SelectObject(
            dc,
            GetStockObject(if background.is_some() {
                DC_BRUSH
            } else {
                NULL_BRUSH
            }),
        );
        SetDCPenColor(dc, border);
        if let Some(color) = background {
            SetDCBrushColor(dc, color);
        }
        RoundRect(
            dc,
            rect.left,
            rect.top,
            rect.right,
            rect.bottom,
            diameter,
            diameter,
        );
        SelectObject(dc, brush);
        SelectObject(dc, pen);
    }
}

pub(crate) fn outline(dc: HDC, rect: &RECT, radius: i32, color: u32) {
    round(dc, rect, radius, color, None);
}

pub(crate) fn stroke(dc: HDC, points: &[[i32; 2]], color: u32) {
    let native: Vec<_> = points.iter().map(|&[x, y]| POINT { x, y }).collect();
    unsafe {
        let pen = SelectObject(dc, GetStockObject(DC_PEN));
        SetDCPenColor(dc, color);
        Polyline(dc, native.as_ptr(), native.len() as i32);
        SelectObject(dc, pen);
    }
}
