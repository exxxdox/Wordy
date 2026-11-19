use std::borrow::Cow;
use std::ptr;

use windows::Win32::Foundation::{E_INVALIDARG, RPC_E_CHANGED_MODE};
use windows::Win32::Graphics::Direct2D::Common::*;
use windows::Win32::Graphics::Direct2D::*;
use windows::Win32::Graphics::DirectWrite::*;
use windows::Win32::Graphics::Dxgi::Common::DXGI_FORMAT_B8G8R8A8_UNORM;
use windows::Win32::Graphics::Imaging::{
    CLSID_WICImagingFactory, GUID_WICPixelFormat32bppPBGRA, IWICImagingFactory,
    WICBitmapCacheOnLoad,
};
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx,
    CoUninitialize,
};
use windows::core::{Error, Result};
use windows_numerics::Vector2;

use crate::wheel::{CANVAS_HEIGHT, CANVAS_WIDTH, Point, SLOT_COUNT, WheelStyle, wheel_label_text};

mod draw;
mod styles;

use draw::Drawing;

const CENTER: Vector2 = point(317.0, 270.0);
const SOFT_SHADOW_CENTER: Vector2 = point(317.0, 273.0);
const PAD: f32 = 47.0;
const TEXT_WIDTH: f32 = 2048.0;
const TEXT_HEIGHT: f32 = 56.0;
type Color = [u8; 4];
const WHITE: Color = rgb(255, 255, 255);
const BLACK: Color = rgb(0, 0, 0);
const ACCENT: Color = rgb(10, 132, 255);
const PRIMARY: Color = rgb(245, 245, 247);
const MUTED: Color = rgb(152, 152, 157);
const WINDOW: Color = rgb(19, 19, 22);
const ELEVATED: Color = rgb(44, 44, 46);
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
    pub style: WheelStyle,
    pub selected: Option<usize>,
    pub highlights: Option<[f64; SLOT_COUNT]>,
    pub editing: bool,
    pub opacity: f64,
    pub anchor: Option<Point>,
    pub font: &'a str,
}

impl<'a> WheelFrame<'a> {
    pub fn new(labels: &'a [String; SLOT_COUNT], style: WheelStyle) -> Self {
        Self {
            labels,
            style,
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

    fn title(&self) -> Cow<'_, str> {
        wheel_label_text(
            self.selected
                .and_then(|i| self.labels.get(i))
                .map_or("", String::as_str),
        )
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
    round_stroke: ID2D1StrokeStyle,
    dash_stroke: ID2D1StrokeStyle,
    _apartment: Apartment,
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
        let round_stroke = unsafe {
            factory.CreateStrokeStyle(
                &D2D1_STROKE_STYLE_PROPERTIES {
                    startCap: D2D1_CAP_STYLE_ROUND,
                    endCap: D2D1_CAP_STYLE_ROUND,
                    lineJoin: D2D1_LINE_JOIN_ROUND,
                    miterLimit: 10.0,
                    ..Default::default()
                },
                None,
            )?
        };
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
            round_stroke,
            dash_stroke,
            _apartment: apartment,
        })
    }

    pub fn render(&self, frame: WheelFrame<'_>, scale: f64) -> Result<WheelBitmap> {
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
        let bitmap = unsafe {
            self.imaging.CreateBitmap(
                width,
                height,
                &GUID_WICPixelFormat32bppPBGRA,
                WICBitmapCacheOnLoad,
            )?
        };
        let target = unsafe {
            self.factory.CreateWicBitmapRenderTarget(
                &bitmap,
                &D2D1_RENDER_TARGET_PROPERTIES {
                    pixelFormat: COLOR_FORMAT,
                    dpiX: 96.0 * scale as f32,
                    dpiY: 96.0 * scale as f32,
                    ..Default::default()
                },
            )?
        };
        unsafe {
            target.SetTextAntialiasMode(D2D1_TEXT_ANTIALIAS_MODE_GRAYSCALE);
            target.BeginDraw();
            target.Clear(None);
        }
        let drawing = Drawing {
            renderer: self,
            target: &target,
            opacity: frame.opacity.clamp(0.0, 1.0) as f32,
        };
        let painted = drawing.paint(frame);
        let finished = unsafe { target.EndDraw(None, None) };
        painted?;
        finished?;
        let stride = width * 4;
        let mut pixels = vec![0; stride as usize * height as usize];
        unsafe { bitmap.CopyPixels(ptr::null(), stride, &mut pixels)? };
        Ok(WheelBitmap {
            width,
            height,
            pixels,
        })
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
fn pt(size: f32) -> f32 {
    (size * (96.0 / 72.0)).round_ties_even()
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

fn mix_color(start: Color, end: Color, factor: f32) -> Color {
    std::array::from_fn(|i| byte(mix(f32::from(start[i]), f32::from(end[i]), factor)))
}

fn light([r, g, b, a]: Color, factor: f32) -> Color {
    let peak = f32::from(r.max(g).max(b));
    let spread = peak - f32::from(r.min(g).min(b));
    let lifted = peak * factor;
    let max = lifted.min(255.0);
    // Glass highlights increase the HSV value and reduce saturation when it clips.
    let saturation = (spread / peak.max(1.0) - (lifted - max) / 255.0).max(0.0);
    let channel =
        |value| byte(max - (peak - f32::from(value)) * max * saturation / spread.max(1.0));
    [channel(r), channel(g), channel(b), a]
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

fn add_corner(
    sink: &ID2D1GeometrySink,
    control_radius: f32,
    control_angle: f32,
    radius: f32,
    angle: f32,
) {
    let corner = D2D1_QUADRATIC_BEZIER_SEGMENT {
        point1: polar(control_radius, control_angle),
        point2: polar(radius, angle),
    };
    unsafe { sink.AddQuadraticBezier(&corner) };
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

fn add_circle(sink: &ID2D1GeometrySink, center: Vector2, radius: f32) {
    unsafe {
        sink.BeginFigure(polar_from(center, radius, 0.0), D2D1_FIGURE_BEGIN_FILLED);
        add_arc(sink, center, radius, 0.0, 180.0);
        add_arc(sink, center, radius, 180.0, 180.0);
        sink.EndFigure(D2D1_FIGURE_END_CLOSED);
    }
}
