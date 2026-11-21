use super::draw::Drawing;
use super::*;

impl Drawing<'_> {
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
            sink.BeginFigure(rotate(0.0, -POINTER_REACH), D2D1_FIGURE_BEGIN_FILLED);
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
