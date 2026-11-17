use std::time::Instant;
use std::{io, mem, ptr, slice};

use windows_sys::Win32::Foundation::{HWND, POINT, RECT, SIZE};
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::UI::HiDpi::GetDpiForWindow;
use windows_sys::Win32::UI::WindowsAndMessaging::*;
use windows_sys::core::w;

use crate::desktop::wide;

const HOLD: u64 = 2600;
const HOVER_HOLD: u64 = 1400;
const FADE_IN: u64 = 160;
const FADE_OUT: u64 = 420;
const SLIDE: u64 = 150;
const MAX_VISIBLE: usize = 3;
const SHADOW_PAD: i32 = 12;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum Tone {
    Success,
    Error,
}

impl Tone {
    fn rgb(self) -> [u8; 3] {
        match self {
            Self::Success => [10, 132, 255],
            Self::Error => [255, 69, 58],
        }
    }
}

struct Card {
    id: u64,
    text: String,
    tone: Tone,
    fade_from: f64,
    fade_in: u64,
    deadline: u64,
    hovered: bool,
}

impl Card {
    fn opacity(&self, now: u64) -> f64 {
        let fraction = |elapsed: u64, duration: u64| elapsed.min(duration) as f64 / duration as f64;
        if !self.hovered && now >= self.deadline {
            (1.0 - fraction(now - self.deadline, FADE_OUT)).powi(3)
        } else {
            let remaining = (1.0 - fraction(now.saturating_sub(self.fade_in), FADE_IN)).powi(3);
            1.0 - remaining * (1.0 - self.fade_from)
        }
    }
}

#[derive(Default)]
struct Queue {
    cards: Vec<Card>,
    next: u64,
}

impl Queue {
    fn push(&mut self, text: &str, tone: Tone, now: u64) {
        if let Some(card) = self.cards.last_mut().filter(|card| card.text == text) {
            card.fade_from = card.opacity(now);
            card.fade_in = now;
            card.tone = tone;
            card.deadline = now + HOLD;
            return;
        }
        if self.cards.len() == MAX_VISIBLE {
            self.cards.remove(0);
        }
        self.next += 1;
        self.cards.push(Card {
            id: self.next,
            text: text.into(),
            tone,
            fade_from: 0.0,
            fade_in: now,
            deadline: now + HOLD,
            hovered: false,
        });
    }

    fn update(&mut self, now: u64, hovered: Option<u64>) {
        for card in &mut self.cards {
            let inside = Some(card.id) == hovered;
            if inside && !card.hovered {
                card.fade_from = card.opacity(now);
                card.fade_in = now;
            } else if !inside && card.hovered {
                card.deadline = now + HOVER_HOLD;
            }
            card.hovered = inside;
        }
        self.cards
            .retain(|card| card.hovered || now < card.deadline + FADE_OUT);
    }
}

pub(crate) struct Toasts {
    clock: Instant,
    queue: Queue,
    popups: Vec<Popup>,
}

impl Toasts {
    pub(crate) fn new() -> Self {
        Self {
            clock: Instant::now(),
            queue: Queue::default(),
            popups: Vec::new(),
        }
    }

    pub(crate) fn push(&mut self, message: &str, tone: Tone) {
        if message.is_empty() {
            self.clear();
        } else {
            self.queue.push(message, tone, self.now());
        }
    }

    pub(crate) fn clear(&mut self) {
        self.queue.cards.clear();
        self.popups.clear();
    }

    fn now(&self) -> u64 {
        self.clock.elapsed().as_millis() as u64
    }

    pub(crate) fn tick(&mut self, owner: HWND, anchor: Option<RECT>) -> io::Result<()> {
        if self.queue.cards.is_empty() {
            return Ok(());
        }
        let Some(anchor) = anchor.filter(|_| unsafe { IsIconic(owner) == 0 }) else {
            self.clear();
            return Ok(());
        };
        let now = self.now();
        let mut mouse = POINT::default();
        let foreground = unsafe { GetForegroundWindow() };
        let cursor = unsafe { GetCursorPos(&mut mouse) } != 0 && foreground == owner;
        let hovering = if cursor {
            self.popups
                .iter()
                .rev()
                .find(|popup| {
                    let rect = popup.bounds;
                    (rect.left..rect.right).contains(&mouse.x)
                        && (rect.top..rect.bottom).contains(&mouse.y)
                })
                .map(|popup| popup.id)
        } else {
            None
        };
        self.queue.update(now, hovering);
        self.popups
            .retain(|popup| self.queue.cards.iter().any(|card| card.id == popup.id));

        let dpi = unsafe { GetDpiForWindow(owner) }.max(96);
        let px = |value| scale(value, dpi);
        let max_text = (anchor.right - anchor.left - px(73)).max(px(140));
        let mut bottom = anchor.bottom - px(10);
        for card in self.queue.cards.iter().rev() {
            let index = match self.popups.iter().position(|popup| popup.id == card.id) {
                Some(index) => index,
                None => {
                    self.popups.push(Popup::new(owner, card, max_text, dpi)?);
                    self.popups.len() - 1
                }
            };
            let popup = &mut self.popups[index];
            if popup.tone != card.tone || popup.dpi != dpi || popup.max_text != max_text {
                popup.surface = Surface::new(&card.text, card.tone, max_text, dpi)?;
                popup.tone = card.tone;
                popup.dpi = dpi;
                popup.max_text = max_text;
                popup.presented = None;
            }
            let (width, height) = (popup.surface.size.cx, popup.surface.size.cy);
            let left = anchor.left + ((anchor.right - anchor.left - width) / 2).max(0);
            let target = bottom - height + px(SHADOW_PAD);
            popup.present(left, target, now, (card.opacity(now) * 255.0).round() as u8)?;
            bottom = target + px(SHADOW_PAD - 8);
        }
        Ok(())
    }
}

fn scale(value: i32, dpi: u32) -> i32 {
    (value * dpi as i32 + 48) / 96
}

struct Popup {
    hwnd: HWND,
    id: u64,
    tone: Tone,
    dpi: u32,
    max_text: i32,
    surface: Surface,
    bounds: RECT,
    presented: Option<(i32, i32, u8)>,
    slide_from: i32,
    slide_to: i32,
    slide_start: u64,
}

impl Popup {
    fn new(owner: HWND, card: &Card, max_text: i32, dpi: u32) -> io::Result<Self> {
        let surface = Surface::new(&card.text, card.tone, max_text, dpi)?;
        // An owned popup stays above its settings window without taking keyboard focus.
        let hwnd = unsafe {
            CreateWindowExW(
                WS_EX_LAYERED | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW,
                w!("STATIC"),
                w!(""),
                WS_POPUP,
                0,
                0,
                surface.size.cx,
                surface.size.cy,
                owner,
                ptr::null_mut(),
                ptr::null_mut(),
                ptr::null(),
            )
        };
        if hwnd.is_null() {
            return Err(io::Error::last_os_error());
        }
        Ok(Self {
            hwnd,
            id: card.id,
            tone: card.tone,
            dpi,
            max_text,
            surface,
            bounds: RECT::default(),
            presented: None,
            slide_from: 0,
            slide_to: 0,
            slide_start: 0,
        })
    }

    fn y_at(&self, now: u64) -> i32 {
        let remaining = (1.0 - (now - self.slide_start).min(SLIDE) as f64 / SLIDE as f64).powi(3);
        self.slide_to + (f64::from(self.slide_from - self.slide_to) * remaining).round() as i32
    }

    fn present(&mut self, left: i32, target: i32, now: u64, alpha: u8) -> io::Result<()> {
        if self.presented.is_none() {
            self.slide_from = target;
            self.slide_to = target;
        }
        if self.slide_to != target {
            self.slide_from = self.y_at(now);
            self.slide_start = now;
            self.slide_to = target;
        }
        let top = self.y_at(now);
        if self.presented == Some((left, top, alpha)) {
            return Ok(());
        }
        let dest = POINT { x: left, y: top };
        let source = POINT::default();
        let blend = BLENDFUNCTION {
            BlendOp: AC_SRC_OVER as u8,
            BlendFlags: 0,
            SourceConstantAlpha: alpha,
            AlphaFormat: AC_SRC_ALPHA as u8,
        };
        let painted = unsafe {
            UpdateLayeredWindow(
                self.hwnd,
                ptr::null_mut(),
                &dest,
                &self.surface.size,
                self.surface.dc,
                &source,
                0,
                &blend,
                ULW_ALPHA,
            )
        };
        if painted == 0 {
            return Err(io::Error::last_os_error());
        }
        if self.presented.is_none() {
            unsafe { ShowWindow(self.hwnd, SW_SHOWNOACTIVATE) };
        }
        self.bounds = RECT {
            left,
            top,
            right: left + self.surface.size.cx,
            bottom: top + self.surface.size.cy,
        };
        self.presented = Some((left, top, alpha));
        Ok(())
    }
}

impl Drop for Popup {
    fn drop(&mut self) {
        unsafe { DestroyWindow(self.hwnd) };
    }
}

struct Surface {
    dc: HDC,
    bitmap: HBITMAP,
    previous_bitmap: HGDIOBJ,
    font: HFONT,
    previous_font: HGDIOBJ,
    size: SIZE,
}

impl Surface {
    fn new(message: &str, tone: Tone, max_text: i32, dpi: u32) -> io::Result<Self> {
        let px = |value| scale(value, dpi);
        let mut text = wide(message);
        // Draw into private top-down pixels, then pass the surface to UpdateLayeredWindow.
        unsafe {
            let dc = CreateCompatibleDC(ptr::null_mut());
            if dc.is_null() {
                return Err(io::Error::last_os_error());
            }
            let font = CreateFontW(
                -px(12),
                0,
                0,
                0,
                FW_NORMAL as i32,
                0,
                0,
                0,
                DEFAULT_CHARSET as u32,
                0,
                0,
                ANTIALIASED_QUALITY as u32,
                0,
                w!("Microsoft YaHei UI"),
            );
            let previous_font = if font.is_null() {
                ptr::null_mut()
            } else {
                SelectObject(dc, font)
            };
            let mut surface = Self {
                dc,
                bitmap: ptr::null_mut(),
                previous_bitmap: ptr::null_mut(),
                font,
                previous_font,
                size: SIZE::default(),
            };
            let mut natural = SIZE::default();
            let units = (text.len() - 1) as i32;
            GetTextExtentPoint32W(dc, text.as_ptr(), units, &mut natural);
            let text_width = natural.cx.clamp(px(12), max_text);
            let mut bounds = RECT {
                right: text_width,
                ..Default::default()
            };
            let flags = DT_WORDBREAK | DT_NOPREFIX | DT_EDITCONTROL;
            DrawTextW(
                dc,
                text.as_mut_ptr(),
                units,
                &mut bounds,
                flags | DT_CALCRECT,
            );
            let pad = px(SHADOW_PAD);
            let width = text_width + 2 * pad + px(41);
            let height = (bounds.bottom - bounds.top).max(px(14)) + 2 * pad + px(18);
            surface.size = SIZE {
                cx: width,
                cy: height,
            };
            let mut info = BITMAPINFO::default();
            info.bmiHeader.biSize = mem::size_of::<BITMAPINFOHEADER>() as u32;
            info.bmiHeader.biWidth = width;
            info.bmiHeader.biHeight = -height;
            info.bmiHeader.biPlanes = 1;
            info.bmiHeader.biBitCount = 32;
            let mut buffer = ptr::null_mut();
            surface.bitmap =
                CreateDIBSection(dc, &info, DIB_RGB_COLORS, &mut buffer, ptr::null_mut(), 0);
            if surface.bitmap.is_null() {
                return Err(io::Error::last_os_error());
            }
            surface.previous_bitmap = SelectObject(dc, surface.bitmap);
            let count = (width * height) as usize;
            paint_card(
                slice::from_raw_parts_mut(buffer.cast::<u32>(), count),
                width,
                height,
                dpi,
                tone,
            );
            let mut label = RECT {
                left: pad + px(24),
                top: pad + px(9),
                right: width - pad - px(16),
                bottom: height - pad - px(8),
            };
            SetBkMode(dc, TRANSPARENT as i32);
            SetTextColor(dc, 0x00f7f5f5);
            DrawTextW(dc, text.as_mut_ptr(), units, &mut label, flags);
            GdiFlush();
            // GDI may zero alpha in glyph pixels. The text box sits wholly on the opaque card.
            let pixels = slice::from_raw_parts_mut(buffer.cast::<u32>(), count);
            for y in label.top..label.bottom {
                for x in label.left..label.right {
                    pixels[(y * width + x) as usize] |= 0xff00_0000;
                }
            }
            Ok(surface)
        }
    }
}

impl Drop for Surface {
    fn drop(&mut self) {
        unsafe {
            if !self.previous_bitmap.is_null() {
                SelectObject(self.dc, self.previous_bitmap);
            }
            if !self.previous_font.is_null() {
                SelectObject(self.dc, self.previous_font);
            }
            if !self.bitmap.is_null() {
                DeleteObject(self.bitmap);
            }
            if !self.font.is_null() {
                DeleteObject(self.font);
            }
            DeleteDC(self.dc);
        }
    }
}

fn paint_card(pixels: &mut [u32], width: i32, height: i32, dpi: u32, tone: Tone) {
    let px = |value| f64::from(scale(value, dpi));
    let mix = |base: [u8; 3], tint: [u8; 3], fraction: f64| {
        std::array::from_fn::<_, 3, _>(|index| {
            (f64::from(base[index]) * (1.0 - fraction) + f64::from(tint[index]) * fraction).round()
                as u8
        })
    };
    let accent = tone.rgb();
    let top = mix([52, 52, 54], accent, 0.16);
    let bottom = mix([41, 41, 43], accent, 0.07);
    let edge = mix([60, 60, 67], accent, 0.5);
    let pack = |[r, g, b]: [u8; 3], a: u8| u32::from_le_bytes([b, g, r, a]);
    let pad = px(SHADOW_PAD);
    let radius = px(14);
    let right = f64::from(width) - pad;
    for y in 0..height {
        for x in 0..width {
            let p = (f64::from(x) + 0.5, f64::from(y) + 0.5);
            let distance = |offset: f64, inset: f64| {
                let cx = f64::from(width) / 2.0;
                let cy = f64::from(height) / 2.0 + offset;
                let h = f64::from(height) / 2.0 - pad - inset;
                let w = cx - pad - inset;
                let dx = (p.0 - cx).abs() - w + radius;
                let dy = (p.1 - cy).abs() - h + radius;
                dx.max(0.0).hypot(dy.max(0.0)) + dx.max(dy).min(0.0) - radius
            };
            let d = distance(0.0, 0.0);
            let coverage = (0.5 - d).clamp(0.0, 1.0);
            let shadow = (1.0 - distance(px(4), 0.0).max(0.0) / px(10))
                .clamp(0.0, 1.0)
                .powi(2)
                * 0.43;
            let alpha = ((coverage + (1.0 - coverage) * shadow) * 255.0).round() as u8;
            if alpha == 0 {
                continue;
            }
            let body = if p.0 >= pad + px(10)
                && p.0 < pad + px(13)
                && p.1 >= pad + px(9)
                && p.1 < f64::from(height) - pad - px(9)
                && p.0 < right
            {
                accent
            } else if d > -px(1) {
                edge
            } else {
                mix(
                    top,
                    bottom,
                    ((p.1 - pad) / (f64::from(height) - 2.0 * pad)).clamp(0.0, 1.0),
                )
            };
            pixels[(y * width + x) as usize] = pack(
                body.map(|channel| (f64::from(channel) * coverage).round() as u8),
                alpha,
            );
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn queue_caps_and_restarts_only_the_newest_duplicate() {
        let mut queue = Queue::default();
        for value in ["一", "二", "三", "四", "四"] {
            queue.push(value, Tone::Success, 100);
        }
        assert_eq!(
            queue
                .cards
                .iter()
                .map(|card| card.text.as_str())
                .collect::<Vec<_>>(),
            ["二", "三", "四"]
        );
        queue.push("四", Tone::Error, 1000);
        queue.update(2800, None);
        assert_eq!(queue.cards.len(), 3);
        assert!(queue.cards[0].opacity(2800) < 1.0);
        assert_eq!(queue.cards[2].opacity(2800), 1.0);
        assert_eq!(queue.cards[2].tone, Tone::Error);
        queue.update(3200, None);
        assert_eq!(queue.cards.len(), 1);
    }

    #[test]
    fn hovering_keeps_a_card_and_leaving_gives_it_a_reading_buffer() {
        let mut queue = Queue::default();
        queue.push("已保存", Tone::Success, 0);
        let id = queue.cards[0].id;
        queue.update(2700, Some(id));
        queue.update(9000, Some(id));
        assert_eq!(queue.cards[0].opacity(9000), 1.0);
        queue.update(9001, None);
        queue.update(10399, None);
        assert_eq!(queue.cards[0].opacity(10399), 1.0);
        queue.update(10822, None);
        assert!(queue.cards.is_empty());
    }

    #[test]
    fn rounded_card_has_transparency_shadow_and_status_accent() {
        let (width, height) = (180, 64);
        let mut pixels = vec![0; width * height];
        paint_card(&mut pixels, width as i32, height as i32, 96, Tone::Error);
        let pixel = |x: usize, y: usize| pixels[y * width + x].to_le_bytes();
        assert_eq!(pixel(0, 0), [0, 0, 0, 0]);
        assert_eq!(pixel(23, 27), [58, 69, 255, 255]);
        assert!(pixel(80, 54)[3] > 0 && pixel(80, 54)[3] < 128);
        assert_ne!(pixel(70, 22), pixel(70, 38));
        let short = Surface::new("已保存", Tone::Success, 300, 96).unwrap();
        let long = Surface::new(
            &"设置已保存，请重试操作。".repeat(20),
            Tone::Success,
            300,
            96,
        )
        .unwrap();
        assert!(long.size.cx > short.size.cx && long.size.cy > short.size.cy);
    }
}
