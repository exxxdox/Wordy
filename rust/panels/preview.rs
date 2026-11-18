use std::{io, mem, ptr};

use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, WPARAM},
    Graphics::Gdi::*,
    UI::{
        Controls::WM_MOUSELEAVE,
        Input::KeyboardAndMouse::{
            SetFocus, TME_LEAVE, TRACKMOUSEEVENT, TrackMouseEvent, VK_DOWN, VK_LEFT, VK_RETURN,
            VK_RIGHT, VK_SPACE, VK_UP,
        },
        Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass},
        WindowsAndMessaging::*,
    },
};

use crate::{
    config::Wheel,
    desktop::paint_buffered,
    settings_backdrop::paint_embedded_backdrop,
    wheel::{CANVAS_HEIGHT, CANVAS_WIDTH, Point, WheelStyle, picker_slot_at},
    wheel_paint::{WheelBitmap, WheelFrame, WheelRenderer},
};

use super::{PANEL_SURFACE, PICK, dimensions, fill};

pub fn wheel_labels(wheel: &Wheel) -> [String; 8] {
    std::array::from_fn(|i| {
        if wheel.slots[i].is_empty() && wheel.texts[i].is_empty() {
            String::new()
        } else if !wheel.labels[i].is_empty() {
            wheel.labels[i].clone()
        } else if !wheel.texts[i].is_empty() {
            wheel.texts[i].clone()
        } else {
            wheel.slots[i].clone()
        }
    })
}

// Editor and rolling-save dialogs share the preview's drawing and slot navigation.
pub(super) struct Preview {
    child: HWND,
    state: Box<PreviewState>,
}

struct PreviewState {
    bitmap: Option<WheelBitmap>,
    selected: Option<usize>,
    hover: Option<usize>,
    labels: [String; 8],
    style: WheelStyle,
    font: String,
    renderer: WheelRenderer,
    embedded: bool,
}

impl Preview {
    pub(super) fn new(child: HWND, embedded: bool) -> io::Result<Self> {
        let renderer = WheelRenderer::new().map_err(io::Error::other)?;
        let mut state = Box::new(PreviewState {
            bitmap: None,
            selected: None,
            hover: None,
            labels: std::array::from_fn(|_| String::new()),
            style: WheelStyle::Classic,
            font: crate::config::DEFAULT_UI_FONT.into(),
            renderer,
            embedded,
        });
        if unsafe {
            SetWindowSubclass(
                child,
                Some(preview_proc),
                1,
                ptr::from_mut(state.as_mut()) as usize,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(Self { child, state })
    }

    pub(super) fn take_slot(&mut self) -> Option<usize> {
        self.state.selected.take()
    }

    pub(super) fn draw(
        &mut self,
        labels: &[String; 8],
        style: WheelStyle,
        slot: usize,
        font: &str,
    ) -> io::Result<()> {
        self.state.labels = labels.clone();
        self.state.style = style;
        self.state.font = crate::config::ui_font_family(font).to_owned();
        self.state.selected = Some(slot);
        render_preview(self.child, &mut self.state);
        Ok(())
    }
}

fn render_preview(child: HWND, state: &mut PreviewState) {
    let labels = state.labels.clone();
    let mut frame = WheelFrame::new(&labels, state.style);
    frame.font = &state.font;
    frame.editing = true;
    frame.selected = state.selected;
    let mut highlights = [0.0; 8];
    if let Some(slot) = state.selected {
        highlights[slot] = 1.0;
    }
    if let Some(slot) = state.hover
        && Some(slot) != state.selected
    {
        highlights[slot] = 0.45;
    }
    frame.highlights = Some(highlights);
    let (width, height) = dimensions(child);
    let scale = (f64::from(width) / CANVAS_WIDTH).min(f64::from(height) / CANVAS_HEIGHT);
    if let Ok(bitmap) = state.renderer.render(frame, scale) {
        state.bitmap = Some(bitmap);
        unsafe { InvalidateRect(child, ptr::null(), 0) };
    }
}

impl Drop for Preview {
    fn drop(&mut self) {
        unsafe { RemoveWindowSubclass(self.child, Some(preview_proc), 1) };
    }
}

unsafe extern "system" fn preview_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    _: usize,
    data: usize,
) -> LRESULT {
    unsafe {
        let state = &mut *(data as *mut PreviewState);
        let (width, height) = dimensions(hwnd);
        match msg {
            WM_ERASEBKGND => 1,
            WM_PAINT => {
                paint_buffered(hwnd, |dc, rect| {
                    fill(dc, rect, PANEL_SURFACE);
                    if state.embedded {
                        paint_embedded_backdrop(hwnd, dc);
                    }
                    if let Some(bitmap) = &state.bitmap {
                        blend_preview(dc, bitmap, width, height);
                    }
                });
                0
            }
            WM_MOUSEMOVE => {
                let slot = preview_slot(width, height, lp);
                if slot != state.hover {
                    state.hover = slot;
                    render_preview(hwnd, state);
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
                if state.hover.take().is_some() {
                    render_preview(hwnd, state);
                }
                0
            }
            WM_GETDLGCODE => (DLGC_WANTARROWS | DLGC_WANTCHARS) as LRESULT,
            WM_KEYDOWN => {
                let step = match wp as u16 {
                    VK_LEFT | VK_UP => -1,
                    VK_RIGHT | VK_DOWN => 1,
                    VK_RETURN | VK_SPACE => {
                        if state.selected.is_some() {
                            PostMessageW(
                                GetParent(hwnd),
                                WM_COMMAND,
                                usize::from(PICK),
                                hwnd as isize,
                            );
                        }
                        return 0;
                    }
                    _ => return DefSubclassProc(hwnd, msg, wp, lp),
                };
                let current = state.selected.unwrap_or(0) as isize;
                let next = (current + step).rem_euclid(8) as usize;
                state.selected = Some(next);
                render_preview(hwnd, state);
                PostMessageW(
                    GetParent(hwnd),
                    WM_COMMAND,
                    usize::from(PICK),
                    hwnd as isize,
                );
                0
            }
            WM_LBUTTONDOWN => {
                SetFocus(hwnd);
                if let Some(slot) = preview_slot(width, height, lp) {
                    state.selected = Some(slot);
                    PostMessageW(
                        GetParent(hwnd),
                        WM_COMMAND,
                        usize::from(PICK),
                        hwnd as isize,
                    );
                }
                0
            }
            _ => DefSubclassProc(hwnd, msg, wp, lp),
        }
    }
}

fn preview_slot(width: i32, height: i32, lp: LPARAM) -> Option<usize> {
    let size =
        (f64::from(width) / CANVAS_WIDTH).min(f64::from(height) / CANVAS_HEIGHT) * CANVAS_WIDTH;
    let x = f64::from(lp as i16) - (f64::from(width) - size) / 2.0;
    let y = f64::from((lp >> 16) as i16) - (f64::from(height) - size) / 2.0;
    picker_slot_at(Point::new(x, y), size)
}

fn blend_preview(dc: HDC, bitmap: &WheelBitmap, width: i32, height: i32) {
    let (w, h) = (bitmap.width as i32, bitmap.height as i32);
    let mut info = BITMAPINFO::default();
    info.bmiHeader.biSize = mem::size_of::<BITMAPINFOHEADER>() as u32;
    info.bmiHeader.biWidth = w;
    info.bmiHeader.biHeight = -h;
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    let mut bits = ptr::null_mut();
    unsafe {
        let image = CreateDIBSection(dc, &info, DIB_RGB_COLORS, &mut bits, ptr::null_mut(), 0);
        if image.is_null() {
            return;
        }
        let source = CreateCompatibleDC(dc);
        if !source.is_null() {
            // The shared wheel renderer emits premultiplied BGRA, the layout expected by AlphaBlend.
            ptr::copy_nonoverlapping(
                bitmap.pixels.as_ptr(),
                bits.cast::<u8>(),
                bitmap.pixels.len(),
            );
            let old = SelectObject(source, image);
            AlphaBlend(
                dc,
                (width - w) / 2,
                (height - h) / 2,
                w,
                h,
                source,
                0,
                0,
                w,
                h,
                BLENDFUNCTION {
                    BlendOp: AC_SRC_OVER as u8,
                    BlendFlags: 0,
                    SourceConstantAlpha: 255,
                    AlphaFormat: AC_SRC_ALPHA as u8,
                },
            );
            SelectObject(source, old);
            DeleteDC(source);
        }
        DeleteObject(image);
    }
}
