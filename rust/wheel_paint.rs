use std::borrow::Cow;
use std::ptr;
use std::sync::LazyLock;

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
use windows::core::{Error, PCWSTR, Result, w};
use windows_numerics::Vector2;

use crate::wheel::{CANVAS_HEIGHT, CANVAS_WIDTH, Point, SLOT_COUNT, WheelStyle, wheel_label_text};

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

struct Drawing<'a> {
    renderer: &'a WheelRenderer,
    target: &'a ID2D1RenderTarget,
    opacity: f32,
}

impl Drawing<'_> {
    fn paint(&self, frame: WheelFrame<'_>) -> Result<()> {
        match frame.style {
            WheelStyle::Classic => self.classic(frame)?,
            WheelStyle::Glass => self.modern(frame, false)?,
            WheelStyle::Petals => self.modern(frame, true)?,
            WheelStyle::Game => self.game(frame)?,
        }
        if let Some(anchor) = frame.anchor {
            let anchor = point(anchor.x as f32, anchor.y as f32);
            let offset = point(anchor.X - CENTER.X, anchor.Y - CENTER.Y);
            if offset.X.abs() + offset.Y.abs() > 2.0 {
                let edge = self.solid(alpha(PRIMARY, 160))?;
                let background = self.solid(alpha(WINDOW, 190))?;
                unsafe {
                    self.target
                        .DrawLine(CENTER, anchor, &edge, 1.5, &self.renderer.dash_stroke);
                    self.target.FillEllipse(&ellipse(anchor, 12.0), &background);
                    self.target
                        .DrawEllipse(&ellipse(anchor, 12.0), &edge, 1.5, None);
                    self.target.FillEllipse(&ellipse(anchor, 3.0), &background);
                    self.target
                        .DrawEllipse(&ellipse(anchor, 3.0), &self.solid(ACCENT)?, 2.0, None);
                }
            }
        }
        Ok(())
    }

    fn solid(&self, color: Color) -> Result<ID2D1SolidColorBrush> {
        unsafe {
            self.target
                .CreateSolidColorBrush(&native_color(color, self.opacity), None)
        }
    }

    fn stops(&self, stops: &[(f32, Color)]) -> Result<ID2D1GradientStopCollection> {
        let stops: Vec<_> = stops
            .iter()
            .map(|&(position, color)| D2D1_GRADIENT_STOP {
                position,
                color: native_color(color, self.opacity),
            })
            .collect();
        unsafe {
            self.target
                .CreateGradientStopCollection(&stops, D2D1_GAMMA_2_2, D2D1_EXTEND_MODE_CLAMP)
        }
    }

    fn radial(
        &self,
        center: Vector2,
        radius: f32,
        stops: &[(f32, Color)],
    ) -> Result<ID2D1RadialGradientBrush> {
        let stops = self.stops(stops)?;
        unsafe {
            self.target.CreateRadialGradientBrush(
                &D2D1_RADIAL_GRADIENT_BRUSH_PROPERTIES {
                    center,
                    radiusX: radius,
                    radiusY: radius,
                    ..Default::default()
                },
                None,
                &stops,
            )
        }
    }

    fn linear(
        &self,
        start: Vector2,
        end: Vector2,
        stops: &[(f32, Color)],
    ) -> Result<ID2D1LinearGradientBrush> {
        let stops = self.stops(stops)?;
        unsafe {
            self.target.CreateLinearGradientBrush(
                &D2D1_LINEAR_GRADIENT_BRUSH_PROPERTIES {
                    startPoint: start,
                    endPoint: end,
                },
                None,
                &stops,
            )
        }
    }

    fn path(&self, record: impl FnOnce(&ID2D1GeometrySink)) -> Result<ID2D1PathGeometry> {
        let geometry = unsafe { self.renderer.factory.CreatePathGeometry()? };
        let sink = unsafe { geometry.Open()? };
        record(&sink);
        unsafe { sink.Close()? };
        Ok(geometry)
    }

    fn arc(
        &self,
        radius: f32,
        start: f32,
        span: f32,
        color: Color,
        width: f32,
        round: bool,
    ) -> Result<()> {
        let path = self.path(|sink| unsafe {
            sink.BeginFigure(polar(radius, start), D2D1_FIGURE_BEGIN_HOLLOW);
            add_arc(sink, CENTER, radius, start, span);
            sink.EndFigure(D2D1_FIGURE_END_OPEN);
        })?;
        let cap = round.then_some(&self.renderer.round_stroke);
        unsafe {
            self.target
                .DrawGeometry(&path, &self.solid(color)?, width, cap)
        };
        Ok(())
    }

    fn shadow(&self) -> Result<()> {
        let ring = self.path(|sink| unsafe {
            sink.SetFillMode(D2D1_FILL_MODE_ALTERNATE);
            add_circle(sink, SOFT_SHADOW_CENTER, 264.0);
            add_circle(sink, CENTER, 246.0);
        })?;
        let shadow = self.radial(
            SOFT_SHADOW_CENTER,
            264.0,
            &[
                (0.0, alpha(BLACK, 60)),
                (0.90, alpha(BLACK, 60)),
                (0.93, alpha(BLACK, 45)),
                (0.96, alpha(BLACK, 16)),
                (1.0, alpha(BLACK, 0)),
            ],
        )?;
        unsafe { self.target.FillGeometry(&ring, &shadow, None) };
        Ok(())
    }

    fn text_format(
        &self,
        family: PCWSTR,
        size: f32,
        weight: DWRITE_FONT_WEIGHT,
    ) -> Result<IDWriteTextFormat> {
        unsafe {
            let format = self.renderer.text.CreateTextFormat(
                family,
                None,
                weight,
                DWRITE_FONT_STYLE_NORMAL,
                DWRITE_FONT_STRETCH_NORMAL,
                size,
                w!("zh-CN"),
            )?;
            format.SetTextAlignment(DWRITE_TEXT_ALIGNMENT_CENTER)?;
            format.SetParagraphAlignment(DWRITE_PARAGRAPH_ALIGNMENT_CENTER)?;
            format.SetWordWrapping(DWRITE_WORD_WRAPPING_NO_WRAP)?;
            Ok(format)
        }
    }

    fn layout(&self, text: &str, format: &IDWriteTextFormat) -> Result<IDWriteTextLayout> {
        let utf16: Vec<_> = text.encode_utf16().collect();
        unsafe {
            self.renderer
                .text
                .CreateTextLayout(&utf16, format, TEXT_WIDTH, TEXT_HEIGHT)
        }
    }

    fn text_width(&self, text: &str, format: &IDWriteTextFormat) -> Result<f32> {
        let layout = self.layout(text, format)?;
        let mut metrics = DWRITE_TEXT_METRICS::default();
        unsafe { layout.GetMetrics(&mut metrics)? };
        Ok(metrics.widthIncludingTrailingWhitespace)
    }

    fn split_at_width(&self, text: &str, width: f32, format: &IDWriteTextFormat) -> Result<usize> {
        for (end, ch) in text.char_indices() {
            if self.text_width(&text[..end + ch.len_utf8()], format)? > width {
                return Ok(end);
            }
        }
        Ok(text.len())
    }

    fn elide<'t>(
        &self,
        text: &'t str,
        width: f32,
        format: &IDWriteTextFormat,
    ) -> Result<Cow<'t, str>> {
        if self.text_width(text, format)? <= width {
            return Ok(Cow::Borrowed(text));
        }
        for (end, _) in text.char_indices().rev() {
            let shortened = format!("{}…", &text[..end]);
            if self.text_width(&shortened, format)? <= width {
                return Ok(Cow::Owned(shortened));
            }
        }
        Ok(Cow::Borrowed(""))
    }

    fn label(
        &self,
        label: &str,
        center: Vector2,
        format: &IDWriteTextFormat,
        color: Color,
        outline: bool,
    ) -> Result<()> {
        let limit = (2.0 * center.X.min(CANVAS_WIDTH as f32 - center.X) - 12.0)
            .round()
            .max(60.0);
        let label = wheel_label_text(if label.is_empty() { "—" } else { label });
        let text = self.elide(&label, limit, format)?;
        self.draw_text(&text, center, format, color, outline)
    }

    fn draw_text(
        &self,
        text: &str,
        center: Vector2,
        format: &IDWriteTextFormat,
        color: Color,
        outline: bool,
    ) -> Result<()> {
        if text.is_empty() {
            return Ok(());
        }
        let layout = self.layout(text, format)?;
        let origin = point(center.X - TEXT_WIDTH / 2.0, center.Y - TEXT_HEIGHT / 2.0);
        if outline {
            let edge = self.solid(alpha(BLACK, 220))?;
            for (dx, dy) in [(-1.0, 0.0), (1.0, 0.0), (0.0, -1.0), (0.0, 2.0)] {
                unsafe {
                    self.target.DrawTextLayout(
                        point(origin.X + dx, origin.Y + dy),
                        &layout,
                        &edge,
                        D2D1_DRAW_TEXT_OPTIONS_NONE,
                    )
                };
            }
        }
        let shadow = if outline {
            byte(150.0 * self.opacity)
        } else {
            150
        };
        unsafe {
            self.target.DrawTextLayout(
                point(origin.X, origin.Y + 1.0),
                &layout,
                &self.solid(alpha(BLACK, shadow))?,
                D2D1_DRAW_TEXT_OPTIONS_NONE,
            );
            self.target.DrawTextLayout(
                origin,
                &layout,
                &self.solid(color)?,
                D2D1_DRAW_TEXT_OPTIONS_NONE,
            );
        }
        Ok(())
    }

    fn title(&self, frame: WheelFrame<'_>, font: &IDWriteTextFormat, classic: bool) -> Result<()> {
        let title = frame.title();
        let width = if classic { 112.0 } else { 116.0 };
        let color = if classic { ACCENT } else { PRIMARY };
        let split = self.split_at_width(&title, width, font)?;
        if split == title.len() {
            self.draw_text(
                &title,
                point(CENTER.X, if classic { 254.0 } else { 256.0 }),
                font,
                color,
                false,
            )?;
        } else {
            let remainder = self.elide(&title[split..], width, font)?;
            let second_y = if classic { 265.0 } else { 267.0 };
            self.draw_text(&title[..split], point(CENTER.X, 243.0), font, color, false)?;
            self.draw_text(&remainder, point(CENTER.X, second_y), font, color, false)?;
        }
        let hint_size = if classic {
            pt(if frame.editing { 13.0 } else { 9.0 })
        } else if frame.editing {
            17.0
        } else {
            13.0
        };
        let hint_format = self.text_format(
            w!("Microsoft YaHei UI"),
            hint_size,
            DWRITE_FONT_WEIGHT_NORMAL,
        )?;
        let hint = if frame.editing {
            "点击编辑"
        } else if title.is_empty() {
            "移回取消"
        } else {
            "松开播放"
        };
        self.draw_text(hint, point(CENTER.X, 297.5), &hint_format, MUTED, false)
    }

    fn wedge(&self, outer: f32, inner: f32, start: f32, span: f32) -> Result<ID2D1PathGeometry> {
        self.path(|sink| unsafe {
            sink.BeginFigure(polar(outer, start), D2D1_FIGURE_BEGIN_FILLED);
            add_arc(sink, CENTER, outer, start, -span);
            sink.AddLine(polar(inner, start - span));
            add_arc(sink, CENTER, inner, start - span, span);
            sink.EndFigure(D2D1_FIGURE_END_CLOSED);
        })
    }

    fn petal(&self, start: f32) -> Result<ID2D1PathGeometry> {
        let end = start - 45.0;
        let outer = 242.0;
        let inner = 88.0;
        let (outer_gap, inner_gap) = (0.7, 1.9);
        let (outer_corner, inner_corner) = (1.9, 5.2);
        let first = start - outer_gap - outer_corner;
        let inner_start = end + inner_gap + inner_corner;
        self.path(|sink| unsafe {
            sink.BeginFigure(polar(outer, first), D2D1_FIGURE_BEGIN_FILLED);
            add_arc(
                sink,
                CENTER,
                outer,
                first,
                -45.0 + 2.0 * (outer_gap + outer_corner),
            );
            add_corner(sink, outer, end + outer_gap, outer - 8.0, end + outer_gap);
            sink.AddLine(polar(inner + 8.0, end + inner_gap));
            add_corner(sink, inner, end + inner_gap, inner, inner_start);
            add_arc(
                sink,
                CENTER,
                inner,
                inner_start,
                45.0 - 2.0 * (inner_gap + inner_corner),
            );
            add_corner(
                sink,
                inner,
                start - inner_gap,
                inner + 8.0,
                start - inner_gap,
            );
            sink.AddLine(polar(outer - 8.0, start - outer_gap));
            add_corner(sink, outer, start - outer_gap, outer, first);
            sink.EndFigure(D2D1_FIGURE_END_CLOSED);
        })
    }

    fn modern(&self, frame: WheelFrame<'_>, petals: bool) -> Result<()> {
        self.shadow()?;
        if !petals {
            let base = self.linear(
                from_disc(100.0, 24.0),
                from_disc(430.0, 516.0),
                &[
                    (0.0, alpha(rgb(46, 48, 53), 230)),
                    (1.0, alpha(rgb(28, 29, 33), 235)),
                ],
            )?;
            let disc = ellipse(CENTER, 246.0);
            unsafe {
                self.target.FillEllipse(&disc, &base);
                self.target
                    .DrawEllipse(&disc, &self.solid(alpha(WHITE, 65))?, 1.0, None);
            }
        }
        let numbers = self.text_format(
            w!("Segoe UI"),
            if frame.editing { 17.0 } else { 13.0 },
            DWRITE_FONT_WEIGHT_NORMAL,
        )?;
        let font = self.text_format(
            w!("Microsoft YaHei UI"),
            if frame.editing { 25.0 } else { 19.0 },
            DWRITE_FONT_WEIGHT_DEMI_BOLD,
        )?;
        for (index, label) in frame.labels.iter().enumerate() {
            let direction = heading(index);
            let start = direction + 22.5;
            let path = if petals {
                self.petal(start)?
            } else {
                self.wedge(246.0, 80.0, start, 45.0)?
            };
            let strength = frame.strength(index);
            let tint = strength * if petals { 1.0 } else { 0.38 };
            let base = mix_color(rgb(39, 40, 44), ACCENT, tint);
            let lift =
                (155.0 - if petals { 45.0 * strength } else { 0.0 }).round_ties_even() / 100.0;
            let a = if !label.is_empty() || frame.editing {
                240
            } else {
                185
            };
            let face = self.radial(
                from_disc(170.0, 140.0),
                460.0,
                &[
                    (0.0, alpha(light(base, lift), a)),
                    (1.0, alpha(light(base, 1.0 / 1.2), a)),
                ],
            )?;
            let edge = if strength > 0.0 {
                alpha(rgb(80, 170, 255), byte(45.0 + 130.0 * strength))
            } else {
                alpha(WHITE, if label.is_empty() { 22 } else { 45 })
            };
            unsafe {
                self.target.FillGeometry(&path, &face, None);
                self.target.DrawGeometry(
                    &path,
                    &self.solid(edge)?,
                    if petals { 1.3 } else { 0.8 },
                    None,
                );
            }
            let center = polar(164.0, direction);
            let selected = frame.selected(index);
            self.draw_text(
                &format!("0{}", index + 1),
                point(center.X, center.Y - 18.0),
                &numbers,
                if selected { PRIMARY } else { MUTED },
                false,
            )?;
            self.label(
                label,
                point(center.X, center.Y + 13.0),
                &font,
                if !label.is_empty() || selected {
                    PRIMARY
                } else {
                    MUTED
                },
                false,
            )?;
        }
        for index in 0..SLOT_COUNT {
            let strength = frame.strength(index);
            if strength > 0.0 {
                self.arc(
                    243.0,
                    heading(index) - 14.0,
                    28.0,
                    alpha(ACCENT, byte(255.0 * strength)),
                    4.0,
                    true,
                )?;
            }
        }
        let hub = ellipse(CENTER, 76.0);
        let gradient = self.radial(
            from_disc(248.0, 240.0),
            110.0,
            &[
                (0.0, alpha(rgb(42, 43, 47), 250)),
                (1.0, alpha(rgb(24, 25, 29), 250)),
            ],
        )?;
        unsafe {
            self.target.FillEllipse(&hub, &gradient);
            self.target
                .DrawEllipse(&hub, &self.solid(alpha(WHITE, 65))?, 1.2, None);
        }
        self.title(frame, &font, false)
    }

    fn classic(&self, frame: WheelFrame<'_>) -> Result<()> {
        self.shadow()?;
        let disc = ellipse(CENTER, 246.0);
        unsafe {
            self.target
                .FillEllipse(&disc, &self.solid(alpha(WINDOW, 165))?)
        };
        self.classic_noise(&disc)?;
        let edge = self.radial(
            CENTER,
            246.0,
            &[
                (0.0, alpha(WHITE, 0)),
                (0.72, alpha(WHITE, 0)),
                (1.0, alpha(WHITE, 56)),
            ],
        )?;
        unsafe { self.target.FillEllipse(&disc, &edge) };
        let rim = self.path(|sink| unsafe {
            sink.SetFillMode(D2D1_FILL_MODE_ALTERNATE);
            add_circle(sink, CENTER, 246.0);
            add_circle(sink, CENTER, 232.0);
        })?;
        let rim_light = self.linear(
            point(CENTER.X, 24.0),
            point(CENTER.X, 516.0),
            &[
                (0.0, alpha(WHITE, 64)),
                (0.5, alpha(WHITE, 20)),
                (1.0, alpha(BLACK, 65)),
            ],
        )?;
        unsafe { self.target.FillGeometry(&rim, &rim_light, None) };

        let sheen = self.radial(
            from_disc(170.0, 140.0),
            380.0,
            &[
                (0.0, alpha(WHITE, 26)),
                (0.55, alpha(WHITE, 6)),
                (1.0, alpha(WHITE, 0)),
            ],
        )?;
        let numbers = self.text_format(
            w!("Segoe UI"),
            pt(if frame.editing { 13.0 } else { 9.0 }),
            DWRITE_FONT_WEIGHT_NORMAL,
        )?;
        let font = self.text_format(
            w!("Microsoft YaHei UI"),
            pt(if frame.editing { 14.0 } else { 10.0 }),
            DWRITE_FONT_WEIGHT_DEMI_BOLD,
        )?;
        for (index, label) in frame.labels.iter().enumerate() {
            let direction = heading(index);
            let path = self.wedge(232.0, 80.0, direction + 21.5, 43.0)?;
            let strength = frame.strength(index);
            let filled = !label.is_empty();
            let selected = frame.selected(index);
            if strength > 0.0 {
                for (width, amount) in [(14.0, 22.0), (8.0, 40.0)] {
                    let glow = self.solid(alpha(ACCENT, byte(amount * strength)))?;
                    unsafe { self.target.DrawGeometry(&path, &glow, width, None) };
                }
            }
            let base = mix_color(ELEVATED, ACCENT, strength);
            let lit = mix(if filled { 225.0 } else { 100.0 }, 235.0, strength);
            let shaded = mix(if filled { 200.0 } else { 90.0 }, 175.0, strength);
            let face = self.radial(
                from_disc(240.0, 230.0),
                260.0,
                &[
                    (0.0, alpha(light(base, 1.22), byte(lit))),
                    (1.0, alpha(light(base, 1.0 / 1.35), byte(shaded))),
                ],
            )?;
            let edge = if selected {
                ACCENT
            } else {
                alpha(PRIMARY, if filled { 45 } else { 18 })
            };
            unsafe {
                self.target.FillGeometry(&path, &face, None);
                self.target.FillGeometry(&path, &sheen, None);
                self.target
                    .DrawGeometry(&path, &self.solid(edge)?, 1.0, None);
            }
            let center = polar(158.0, direction);
            self.draw_text(
                &format!("0{}", index + 1),
                point(center.X, center.Y - 20.0),
                &numbers,
                if selected || filled { PRIMARY } else { MUTED },
                false,
            )?;
            self.label(
                label,
                point(center.X, center.Y + 11.0),
                &font,
                if filled { PRIMARY } else { MUTED },
                false,
            )?;
        }

        if let Some(index) = frame
            .selected
            .filter(|&index| index < SLOT_COUNT && frame.selected(index))
        {
            self.arc(239.0, heading(index) - 14.0, 28.0, ACCENT, 5.0, true)?;
        }
        self.arc(244.0, 45.0, 90.0, alpha(PRIMARY, 70), 2.0, false)?;
        self.arc(244.0, 225.0, 90.0, alpha(BLACK, 55), 1.0, false)?;
        for (step, a) in [(7.0, 14), (4.0, 26), (1.0, 38)] {
            unsafe {
                self.target.FillEllipse(
                    &ellipse(point(CENTER.X, 271.0), 72.0 + step),
                    &self.solid(alpha(BLACK, a))?,
                )
            };
        }
        let hub = ellipse(CENTER, 72.0);
        let glass = self.radial(
            from_disc(262.0, 248.0),
            96.0,
            &[
                (0.0, alpha(light(WINDOW, 1.85), 250)),
                (1.0, alpha(WINDOW, 238)),
            ],
        )?;
        unsafe {
            self.target.FillEllipse(&hub, &glass);
            self.target
                .DrawEllipse(&hub, &self.solid(alpha(ACCENT, 90))?, 1.0, None);
        }
        self.arc(70.0, 50.0, 80.0, alpha(PRIMARY, 90), 2.0, false)?;
        self.arc(70.0, 230.0, 80.0, alpha(BLACK, 120), 2.0, false)?;
        self.title(frame, &font, true)
    }

    fn classic_noise(&self, disc: &D2D1_ELLIPSE) -> Result<()> {
        const TILE: u32 = 64;
        static NOISE: LazyLock<Vec<u8>> = LazyLock::new(|| {
            // The fixed tile breaks up banding without changing the glass's average brightness.
            let mut seed = 0x5f3a2c_u32;
            let mut next = || {
                seed ^= seed << 13;
                seed ^= seed >> 17;
                seed ^= seed << 5;
                seed
            };
            let mut pixels = Vec::with_capacity((TILE * TILE * 4) as usize);
            for _ in 0..TILE * TILE {
                let value = next() & 1;
                let a = (48 + (next() % 208)) as u8;
                let gray = if value == 0 { 0 } else { a };
                pixels.extend([gray, gray, gray, a]);
            }
            pixels
        });
        let properties = D2D1_BITMAP_PROPERTIES {
            pixelFormat: COLOR_FORMAT,
            dpiX: 96.0,
            dpiY: 96.0,
        };
        let extend = D2D1_BITMAP_BRUSH_PROPERTIES {
            extendModeX: D2D1_EXTEND_MODE_WRAP,
            extendModeY: D2D1_EXTEND_MODE_WRAP,
            interpolationMode: D2D1_BITMAP_INTERPOLATION_MODE_NEAREST_NEIGHBOR,
        };
        unsafe {
            let tile = self.target.CreateBitmap(
                D2D_SIZE_U {
                    width: TILE,
                    height: TILE,
                },
                Some(NOISE.as_ptr().cast()),
                TILE * 4,
                &properties,
            )?;
            let brush = self.target.CreateBitmapBrush(&tile, Some(&extend), None)?;
            brush.SetOpacity(0.035 * self.opacity);
            self.target.FillEllipse(disc, &brush);
        }
        Ok(())
    }

    fn game(&self, frame: WheelFrame<'_>) -> Result<()> {
        let strengths = std::array::from_fn::<_, SLOT_COUNT, _>(|i| {
            if frame.editing || !frame.labels[i].is_empty() {
                frame.strength(i)
            } else {
                0.0
            }
        });
        for (index, &strength) in strengths.iter().enumerate() {
            let path = self.wedge(256.0, 62.0, heading(index) + 22.5, 45.0)?;
            let (base_tone, base_alpha) = if index % 2 == 0 {
                (222.0, 12.0)
            } else {
                (12.0, 78.0)
            };
            let tone = byte(mix(base_tone, 235.0, strength));
            let gray = rgb(tone, tone, tone);
            let inner = mix(base_alpha, 144.0, strength);
            let a = alpha(gray, byte(inner));
            let face = self.radial(
                CENTER,
                256.0,
                &[
                    (0.0, a),
                    (0.35, a),
                    (0.82, alpha(gray, byte(inner * 0.6))),
                    (1.0, alpha(gray, 0)),
                ],
            )?;
            unsafe { self.target.FillGeometry(&path, &face, None) };
        }
        let centers = [
            (270.0, 72.0),
            (420.0, 150.0),
            (451.0, 270.0),
            (420.0, 390.0),
            (270.0, 468.0),
            (120.0, 390.0),
            (89.0, 270.0),
            (120.0, 150.0),
        ];
        let mut knob = CENTER;
        for (index, (label, &strength)) in frame.labels.iter().zip(&strengths).enumerate() {
            let font = self.text_format(
                w!("Microsoft YaHei UI"),
                (22.0 + 4.0 * strength).round_ties_even(),
                DWRITE_FONT_WEIGHT_NORMAL,
            )?;
            let a = if label.is_empty() {
                byte(90.0 + 165.0 * strength)
            } else {
                255
            };
            let (x, y) = centers[index];
            self.label(label, from_disc(x, y), &font, alpha(WHITE, a), true)?;
            let direction = polar_from(point(0.0, 0.0), 28.0 * strength, heading(index));
            knob.X += direction.X;
            knob.Y += direction.Y;
        }
        if let Some(index) = frame
            .selected
            .filter(|&index| index < SLOT_COUNT && frame.selected(index))
        {
            self.game_pointer(index, strengths[index])?;
        }
        let ring = ellipse(CENTER, 62.0);
        let ring_fill = self.linear(
            point(CENTER.X, 208.0),
            point(CENTER.X, 332.0),
            &[
                (0.0, rgb(220, 225, 225)),
                (0.5, rgb(115, 126, 131)),
                (1.0, rgb(177, 187, 190)),
            ],
        )?;
        unsafe {
            self.target.FillEllipse(&ring, &ring_fill);
            self.target
                .DrawEllipse(&ring, &self.solid(rgb(225, 232, 232))?, 1.2, None);
        }
        let (dx, dy) = (knob.X - CENTER.X, knob.Y - CENTER.Y);
        let reach = dx.hypot(dy);
        if reach > 0.5 {
            let spot = point(CENTER.X + dx * 58.0 / reach, CENTER.Y + dy * 58.0 / reach);
            let glow = self.radial(
                spot,
                34.0,
                &[
                    (0.0, alpha(WHITE, byte(180.0 * (reach / 28.0).min(1.0)))),
                    (1.0, alpha(WHITE, 0)),
                ],
            )?;
            unsafe { self.target.FillEllipse(&ring, &glow) };
        }
        let bowl = ellipse(CENTER, 51.0);
        let bowl_fill = self.radial(
            from_disc(258.0, 245.0),
            88.0,
            &[(0.0, rgb(65, 75, 83)), (1.0, rgb(19, 27, 33))],
        )?;
        let cap = self.radial(
            point(knob.X - 6.0, knob.Y - 8.0),
            23.0,
            &[
                (0.0, rgb(207, 216, 219)),
                (0.36, rgb(112, 126, 137)),
                (0.65, rgb(55, 67, 76)),
                (1.0, rgb(17, 25, 32)),
            ],
        )?;
        unsafe {
            self.target.FillEllipse(&bowl, &bowl_fill);
            self.target
                .DrawEllipse(&bowl, &self.solid(rgb(180, 190, 192))?, 1.5, None);
            self.target.FillEllipse(
                &ellipse(point(knob.X + 1.0, knob.Y + 3.0), 18.0),
                &self.solid(alpha(BLACK, 90))?,
            );
            self.target.FillEllipse(&ellipse(knob, 17.0), &cap);
        }
        Ok(())
    }

    fn game_pointer(&self, index: usize, strength: f32) -> Result<()> {
        // The pointer follows the live sector alone while the metal knob blends the animated directions.
        let drawing = Self {
            opacity: self.opacity * strength,
            renderer: self.renderer,
            target: self.target,
        };
        let (sin, cos) = (index as f32 * 45.0).to_radians().sin_cos();
        let rotate = |x, y| point(CENTER.X + x * cos - y * sin, CENTER.Y + x * sin + y * cos);
        let pointer = drawing.path(|sink| unsafe {
            sink.BeginFigure(rotate(0.0, -96.0), D2D1_FIGURE_BEGIN_FILLED);
            sink.AddLine(rotate(-46.0, -54.0));
            sink.AddLine(rotate(46.0, -54.0));
            sink.EndFigure(D2D1_FIGURE_END_CLOSED);
        })?;
        let metal = drawing.linear(
            rotate(-20.0, -96.0),
            rotate(20.0, -54.0),
            &[
                (0.0, alpha(rgb(65, 73, 78), 250)),
                (1.0, alpha(rgb(20, 27, 32), 250)),
            ],
        )?;
        unsafe {
            self.target.FillGeometry(&pointer, &metal, None);
            self.target
                .DrawGeometry(&pointer, &drawing.solid(alpha(BLACK, 85))?, 2.0, None);
        }
        Ok(())
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
