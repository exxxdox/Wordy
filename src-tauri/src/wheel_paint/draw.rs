use std::borrow::Cow;

use super::*;
use windows::core::{PCWSTR, w};

pub(super) struct Drawing<'a> {
    pub(super) renderer: &'a WheelRenderer,
    pub(super) target: &'a ID2D1RenderTarget,
    pub(super) opacity: f32,
}

impl Drawing<'_> {
    pub(super) fn paint(&self, frame: WheelFrame<'_>) -> Result<()> {
        self.game(frame)?;
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

    pub(super) fn solid(&self, color: Color) -> Result<ID2D1SolidColorBrush> {
        unsafe {
            self.target
                .CreateSolidColorBrush(&native_color(color, self.opacity), None)
        }
    }

    pub(super) fn stops(&self, stops: &[(f32, Color)]) -> Result<ID2D1GradientStopCollection> {
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

    pub(super) fn radial(
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

    pub(super) fn linear(
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

    pub(super) fn path(
        &self,
        record: impl FnOnce(&ID2D1GeometrySink),
    ) -> Result<ID2D1PathGeometry> {
        let geometry = unsafe { self.renderer.factory.CreatePathGeometry()? };
        let sink = unsafe { geometry.Open()? };
        record(&sink);
        unsafe { sink.Close()? };
        Ok(geometry)
    }

    pub(super) fn text_format(
        &self,
        family: &str,
        size: f32,
        weight: DWRITE_FONT_WEIGHT,
    ) -> Result<IDWriteTextFormat> {
        let family: Vec<u16> = family.encode_utf16().chain([0]).collect();
        unsafe {
            let format = self.renderer.text.CreateTextFormat(
                PCWSTR(family.as_ptr()),
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

    pub(super) fn layout(
        &self,
        text: &str,
        format: &IDWriteTextFormat,
    ) -> Result<IDWriteTextLayout> {
        let utf16: Vec<_> = text.encode_utf16().collect();
        unsafe {
            self.renderer
                .text
                .CreateTextLayout(&utf16, format, TEXT_WIDTH, TEXT_HEIGHT)
        }
    }

    pub(super) fn text_width(&self, text: &str, format: &IDWriteTextFormat) -> Result<f32> {
        let layout = self.layout(text, format)?;
        let mut metrics = DWRITE_TEXT_METRICS::default();
        unsafe { layout.GetMetrics(&mut metrics)? };
        Ok(metrics.widthIncludingTrailingWhitespace)
    }

    pub(super) fn elide<'t>(
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

    pub(super) fn label(
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

    pub(super) fn draw_text(
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

    pub(super) fn wedge(
        &self,
        outer: f32,
        inner: f32,
        start: f32,
        span: f32,
    ) -> Result<ID2D1PathGeometry> {
        self.path(|sink| unsafe {
            sink.BeginFigure(polar(outer, start), D2D1_FIGURE_BEGIN_FILLED);
            add_arc(sink, CENTER, outer, start, -span);
            sink.AddLine(polar(inner, start - span));
            add_arc(sink, CENTER, inner, start - span, span);
            sink.EndFigure(D2D1_FIGURE_END_CLOSED);
        })
    }
}
