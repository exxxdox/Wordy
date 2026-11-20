use std::{cell::RefCell, ptr};

use windows::Win32::Foundation::{E_INVALIDARG, RPC_E_CHANGED_MODE};
use windows::Win32::Graphics::Direct2D::Common::*;
use windows::Win32::Graphics::Direct2D::*;
use windows::Win32::Graphics::DirectWrite::*;
use windows::Win32::Graphics::Dxgi::Common::DXGI_FORMAT_B8G8R8A8_UNORM;
use windows::Win32::Graphics::Imaging::{
    CLSID_WICImagingFactory, GUID_WICPixelFormat32bppPBGRA, IWICBitmap, IWICImagingFactory,
    WICBitmapCacheOnLoad,
};
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx,
    CoUninitialize,
};
use windows::core::{Error, Result};
use windows_numerics::Vector2;

use crate::wheel::{CANVAS_HEIGHT, CANVAS_WIDTH, Point, SLOT_COUNT, wheel_label_text};

mod draw;
mod styles;

use draw::Drawing;

const CENTER: Vector2 = point(317.0, 270.0);
const PAD: f32 = 47.0;
const TEXT_WIDTH: f32 = 2048.0;
const TEXT_HEIGHT: f32 = 56.0;
type Color = [u8; 4];
const WHITE: Color = rgb(255, 255, 255);
const BLACK: Color = rgb(0, 0, 0);
const ACCENT: Color = rgb(10, 132, 255);
const PRIMARY: Color = rgb(245, 245, 247);
const WINDOW: Color = rgb(19, 19, 22);
const COLOR_FORMAT: D2D1_PIXEL_FORMAT = D2D1_PIXEL_FORMAT {
    format: DXGI_FORMAT_B8G8R8A8_UNORM,
    alphaMode: D2D1_ALPHA_MODE_PREMULTIPLIED,
};

pub struct WheelBitmap {
    pub width: u32,
    pub height: u32,
    /// Top-down, tightly packed premultiplied BGRA pixels.
    pub pixels: Vec<u8>,
}

#[derive(Clone, Copy)]
pub struct WheelFrame<'a> {
    pub labels: &'a [String; SLOT_COUNT],
    pub selected: Option<usize>,
    pub highlights: Option<[f64; SLOT_COUNT]>,
    pub editing: bool,
    pub opacity: f64,
    pub anchor: Option<Point>,
    pub font: &'a str,
}

impl<'a> WheelFrame<'a> {
    pub fn new(labels: &'a [String; SLOT_COUNT]) -> Self {
        Self {
            labels,
            selected: None,
            highlights: None,
            editing: false,
            opacity: 1.0,
            anchor: None,
            font: crate::config::DEFAULT_UI_FONT,
        }
    }

    fn selected(&self, index: usize) -> bool {
        self.selected == Some(index) && (!self.labels[index].is_empty() || self.editing)
    }

    fn strength(&self, index: usize) -> f32 {
        self.highlights
            .map_or(f32::from(self.selected(index)), |values| {
                values[index] as f32
            })
            .clamp(0.0, 1.0)
    }
}

// When the host already initialized COM with another apartment model, use it without releasing it.
struct Apartment(bool);

impl Drop for Apartment {
    fn drop(&mut self) {
        if self.0 {
            unsafe { CoUninitialize() };
        }
    }
}

pub struct WheelRenderer {
    factory: ID2D1Factory,
    text: IDWriteFactory,
    imaging: IWICImagingFactory,
    dash_stroke: ID2D1StrokeStyle,
    cached_target: RefCell<Option<PaintTarget>>,
    _apartment: Apartment,
}

struct PaintTarget {
    target: ID2D1RenderTarget,
    bitmap: IWICBitmap,
    scale: f64,
}

impl WheelRenderer {
    pub fn new() -> Result<Self> {
        let initialized = unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED) };
        if initialized != RPC_E_CHANGED_MODE {
            initialized.ok()?;
        }
        let apartment = Apartment(initialized.is_ok());
        let factory: ID2D1Factory =
            unsafe { D2D1CreateFactory(D2D1_FACTORY_TYPE_SINGLE_THREADED, None)? };
        let text = unsafe { DWriteCreateFactory(DWRITE_FACTORY_TYPE_SHARED)? };
        let imaging =
            unsafe { CoCreateInstance(&CLSID_WICImagingFactory, None, CLSCTX_INPROC_SERVER)? };
        let dash_stroke = unsafe {
            factory.CreateStrokeStyle(
                &D2D1_STROKE_STYLE_PROPERTIES {
                    dashStyle: D2D1_DASH_STYLE_DASH,
                    miterLimit: 10.0,
                    ..Default::default()
                },
                None,
            )?
        };
        Ok(Self {
            factory,
            text,
            imaging,
            dash_stroke,
            cached_target: RefCell::new(None),
            _apartment: apartment,
        })
    }

    pub fn render(&self, frame: WheelFrame<'_>, scale: f64) -> Result<WheelBitmap> {
        let (bitmap, width, height) = self.paint_bitmap(frame, scale)?;
        let stride = width * 4;
        let mut pixels = vec![0; stride as usize * height as usize];
        unsafe { bitmap.CopyPixels(ptr::null(), stride, &mut pixels)? };
        Ok(WheelBitmap {
            width,
            height,
            pixels,
        })
    }

    pub(crate) fn paint_bitmap(
        &self,
        frame: WheelFrame<'_>,
        scale: f64,
    ) -> Result<(IWICBitmap, u32, u32)> {
        let width = (CANVAS_WIDTH * scale).round();
        let height = (CANVAS_HEIGHT * scale).round();
        if !scale.is_finite()
            || width < 1.0
            || height < 1.0
            || width * height * 4.0 > f64::from(u32::MAX)
        {
            return Err(Error::from(E_INVALIDARG));
        }
        let (width, height) = (width as u32, height as u32);
        let mut cached = self.cached_target.borrow_mut();
        // DPI determines both pixel dimensions and the target's drawing coordinates.
        if cached.as_ref().is_none_or(|buffer| buffer.scale != scale) {
            let (bitmap, target) = unsafe {
                let bitmap = self.imaging.CreateBitmap(
                    width,
                    height,
                    &GUID_WICPixelFormat32bppPBGRA,
                    WICBitmapCacheOnLoad,
                )?;
                let target = self.factory.CreateWicBitmapRenderTarget(
                    &bitmap,
                    &D2D1_RENDER_TARGET_PROPERTIES {
                        pixelFormat: COLOR_FORMAT,
                        dpiX: 96.0 * scale as f32,
                        dpiY: 96.0 * scale as f32,
                        ..Default::default()
                    },
                )?;
                target.SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);
                (bitmap, target)
            };
            *cached = Some(PaintTarget {
                target,
                bitmap,
                scale,
            });
        }
        let buffer = cached.as_ref().expect("paint target is initialized above");
        let target = &buffer.target;
        unsafe {
            target.BeginDraw();
            target.Clear(None);
        }
        let drawing = Drawing {
            renderer: self,
            target,
            opacity: frame.opacity.clamp(0.0, 1.0) as f32,
        };
        let painted = drawing.paint(frame);
        let finished = unsafe { target.EndDraw(None, None) };
        let bitmap = buffer.bitmap.clone();
        // Direct2D can invalidate the render target. Recreate it on the next frame.
        if finished.is_err() {
            *cached = None;
        }
        painted?;
        finished?;
        Ok((bitmap, width, height))
    }

    /// 设置页预览：走与运行时相同的绘制，输出 [宽 LE][高 LE][RGBA]。
    /// Canvas 的 putImageData 需要直通（非预乘）RGBA，转换放在这里完成。
    pub fn render_preview(
        &self,
        labels: &[String; SLOT_COUNT],
        selected: Option<usize>,
        font: &str,
        scale: f64,
    ) -> Result<Vec<u8>> {
        let mut frame = WheelFrame::new(labels);
        frame.selected = selected;
        // 设置页/截取窗口允许选中空槽（保存录制时常选空槽），空槽也要显示高亮。
        frame.editing = true;
        frame.font = font;
        let (bitmap, width, height) = self.paint_bitmap(frame, scale)?;
        let stride = width * 4;
        let mut bytes = vec![0; 8 + stride as usize * height as usize];
        bytes[..4].copy_from_slice(&width.to_le_bytes());
        bytes[4..8].copy_from_slice(&height.to_le_bytes());
        // 直接填充 IPC 缓冲并就地换色，省去两份中间像素缓冲。
        let pixels = &mut bytes[8..];
        unsafe { bitmap.CopyPixels(ptr::null(), stride, pixels)? };
        straight_rgba(pixels);
        Ok(bytes)
    }
}

const fn point(x: f32, y: f32) -> Vector2 {
    Vector2 { X: x, Y: y }
}
const fn from_disc(x: f32, y: f32) -> Vector2 {
    point(x + PAD, y)
}
const fn rgb(r: u8, g: u8, b: u8) -> Color {
    [r, g, b, 255]
}
const fn alpha([r, g, b, _]: Color, a: u8) -> Color {
    [r, g, b, a]
}
fn byte(value: f32) -> u8 {
    value.round_ties_even() as u8
}

/// 预乘 BGRA → 直通 RGBA；alpha 为 0 时颜色归零，避免还原时溢出。
fn straight_rgba(pixels: &mut [u8]) {
    for pixel in pixels.as_chunks_mut::<4>().0 {
        let [blue, green, red, alpha] = *pixel;
        *pixel = match alpha {
            0 => [0; 4],
            255 => [red, green, blue, alpha],
            _ => {
                let lift = 255.0 / f32::from(alpha);
                let channel = |value: u8| (f32::from(value) * lift).round().clamp(0.0, 255.0) as u8;
                [channel(red), channel(green), channel(blue), alpha]
            }
        };
    }
}
fn native_color([r, g, b, a]: Color, opacity: f32) -> D2D1_COLOR_F {
    D2D1_COLOR_F {
        r: f32::from(r) / 255.0,
        g: f32::from(g) / 255.0,
        b: f32::from(b) / 255.0,
        a: f32::from(a) / 255.0 * opacity,
    }
}

fn mix(start: f32, end: f32, factor: f32) -> f32 {
    start + (end - start) * factor
}

#[cfg(test)]
mod tests {
    use super::{WheelFrame, WheelRenderer, straight_rgba};

    #[test]
    fn reuses_wheel_bitmap_until_dpi_changes() {
        let renderer = WheelRenderer::new().unwrap();
        let labels = std::array::from_fn(|_| String::new());
        let (first, _, _) = renderer
            .paint_bitmap(WheelFrame::new(&labels), 0.5)
            .unwrap();
        let (next, _, _) = renderer
            .paint_bitmap(WheelFrame::new(&labels), 0.5)
            .unwrap();
        assert!(
            first == next,
            "same-scale frames should reuse the WIC bitmap"
        );

        let (resized, width, height) = renderer
            .paint_bitmap(WheelFrame::new(&labels), 1.0)
            .unwrap();
        assert!(resized != first);
        assert_eq!((width, height), (634, 540));
    }

    #[test]
    fn reused_target_clears_the_previous_frame() {
        let renderer = WheelRenderer::new().unwrap();
        let labels = std::array::from_fn(|_| "测试".to_owned());
        let shown = renderer.render(WheelFrame::new(&labels), 0.5).unwrap();
        let mut hidden = WheelFrame::new(&labels);
        hidden.opacity = 0.0;
        let hidden = renderer.render(hidden, 0.5).unwrap();
        assert!(shown.pixels.iter().any(|&value| value != 0));
        assert!(hidden.pixels.iter().all(|&value| value == 0));
    }

    #[test]
    fn straight_rgba_unpremultiplies_and_swaps_channels() {
        // 预乘 BGRA (b=32,g=64,r=128,a=128) → 直通 RGBA (r=255,g=128,b=64,a=128)。
        let mut frame = [
            3, 0, 0, 0, 1, 0, 0, 0, 32, 64, 128, 128, 10, 20, 30, 255, 9, 9, 9, 0,
        ];
        straight_rgba(&mut frame[8..]);
        assert_eq!(
            frame,
            [
                3, 0, 0, 0, 1, 0, 0, 0, 255, 128, 64, 128, 30, 20, 10, 255, 0, 0, 0, 0
            ]
        );
    }
}

fn ellipse(center: Vector2, radius: f32) -> D2D1_ELLIPSE {
    D2D1_ELLIPSE {
        point: center,
        radiusX: radius,
        radiusY: radius,
    }
}

fn polar_from(center: Vector2, radius: f32, angle: f32) -> Vector2 {
    let (sin, cos) = angle.to_radians().sin_cos();
    point(center.X + radius * cos, center.Y - radius * sin)
}

fn polar(radius: f32, angle: f32) -> Vector2 {
    polar_from(CENTER, radius, angle)
}

fn heading(index: usize) -> f32 {
    90.0 - index as f32 * 45.0
}

fn add_arc(sink: &ID2D1GeometrySink, center: Vector2, radius: f32, start: f32, span: f32) {
    let arc = D2D1_ARC_SEGMENT {
        point: polar_from(center, radius, start + span),
        size: D2D_SIZE_F {
            width: radius,
            height: radius,
        },
        rotationAngle: 0.0,
        sweepDirection: if span > 0.0 {
            D2D1_SWEEP_DIRECTION_COUNTER_CLOCKWISE
        } else {
            D2D1_SWEEP_DIRECTION_CLOCKWISE
        },
        arcSize: D2D1_ARC_SIZE_SMALL,
    };
    unsafe { sink.AddArc(&arc) };
}
