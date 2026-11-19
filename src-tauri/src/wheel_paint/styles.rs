use super::draw::Drawing;
use super::*;
use std::sync::LazyLock;

impl Drawing<'_> {
    pub(super) fn modern(&self, frame: WheelFrame<'_>, petals: bool) -> Result<()> {
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
            frame.font,
            if frame.editing { 17.0 } else { 13.0 },
            DWRITE_FONT_WEIGHT_NORMAL,
        )?;
        let font = self.text_format(
            frame.font,
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

    pub(super) fn classic(&self, frame: WheelFrame<'_>) -> Result<()> {
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
            frame.font,
            pt(if frame.editing { 13.0 } else { 9.0 }),
            DWRITE_FONT_WEIGHT_NORMAL,
        )?;
        let font = self.text_format(
            frame.font,
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

    pub(super) fn classic_noise(&self, disc: &D2D1_ELLIPSE) -> Result<()> {
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

    pub(super) fn game(&self, frame: WheelFrame<'_>) -> Result<()> {
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
                frame.font,
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
            // A picker can highlight two sectors, but its knob represents the chosen slot.
            let knob_strength = if frame.editing {
                f32::from(frame.selected(index))
            } else {
                strength
            };
            let direction = polar_from(point(0.0, 0.0), 28.0 * knob_strength, heading(index));
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

    pub(super) fn game_pointer(&self, index: usize, strength: f32) -> Result<()> {
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
