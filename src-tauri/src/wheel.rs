use std::borrow::Cow;

pub const SLOT_COUNT: usize = 8;
pub const CANVAS_WIDTH: f64 = 634.0;
pub const CANVAS_HEIGHT: f64 = 540.0;
pub const POLL_MS: u64 = 16;

const CANVAS_CENTER: Point = Point::new(CANVAS_WIDTH / 2.0, CANVAS_HEIGHT / 2.0);
const FADE_IN_MS: u64 = 120;
const FADE_OUT_MS: u64 = 90;
const HOVER_MS: u64 = 90;
const MAX_LABEL_CHARS: usize = 10;

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Point {
    pub x: f64,
    pub y: f64,
}

impl Point {
    pub const fn new(x: f64, y: f64) -> Self {
        Self { x, y }
    }

    fn relative_to(self, origin: Self) -> Self {
        Self::new(self.x - origin.x, self.y - origin.y)
    }

    // Keep the selected monitor's origin fixed when scaling its local coordinates.
    pub fn to_logical(self, monitor: Self, scale: f64) -> Self {
        let offset = self.relative_to(monitor);
        Self::new(monitor.x + offset.x / scale, monitor.y + offset.y / scale)
    }

    pub fn to_physical(self, monitor: Self, scale: f64) -> Self {
        let offset = self.relative_to(monitor);
        Self::new(monitor.x + offset.x * scale, monitor.y + offset.y * scale)
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub width: f64,
    pub height: f64,
}

impl Rect {
    pub const fn new(x: f64, y: f64, width: f64, height: f64) -> Self {
        Self {
            x,
            y,
            width,
            height,
        }
    }

    pub fn to_logical(self, monitor: Point, scale: f64) -> Self {
        let origin = Point::new(self.x, self.y).to_logical(monitor, scale);
        Self::new(origin.x, origin.y, self.width / scale, self.height / scale)
    }

    fn wheel_position(self, origin: Point) -> Point {
        let position = origin.relative_to(CANVAS_CENTER);
        let right = self.x + (self.width - CANVAS_WIDTH).max(0.0);
        let bottom = self.y + (self.height - CANVAS_HEIGHT).max(0.0);
        Point::new(
            position.x.clamp(self.x, right),
            position.y.clamp(self.y, bottom),
        )
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum WheelStyle {
    #[default]
    Classic,
    Glass,
    Petals,
    Game,
}

impl WheelStyle {
    pub fn from_config(value: &str) -> Self {
        match value {
            "glass" => Self::Glass,
            "petals" => Self::Petals,
            "game" => Self::Game,
            _ => Self::Classic,
        }
    }
}

pub fn wheel_label_text(label: &str) -> Cow<'_, str> {
    match label.char_indices().nth(MAX_LABEL_CHARS) {
        Some((end, _)) => Cow::Owned(format!("{}...", &label[..end])),
        None => Cow::Borrowed(label),
    }
}

fn direction(offset: Point) -> usize {
    // Sector zero is centred on north, so shift by half the angular width.
    let angle = offset.x.atan2(-offset.y).to_degrees().rem_euclid(360.0);
    ((angle + 22.5).rem_euclid(360.0) / 45.0).floor() as usize
}

fn gesture_slot_at(offset: Point) -> Option<usize> {
    (offset.x.hypot(offset.y) >= 76.0).then(|| direction(offset))
}

pub fn picker_slot_at(local: Point, size: f64) -> Option<usize> {
    let middle = size / 2.0;
    let offset = local.relative_to(Point::new(middle, middle));
    let scale = size / CANVAS_WIDTH;
    let ring = (80.0 * scale)..=(232.0 * scale);
    (size > 0.0 && ring.contains(&offset.x.hypot(offset.y))).then(|| direction(offset))
}

fn eased(now: u64, start: u64, duration: u64) -> f64 {
    let elapsed = now.saturating_sub(start).min(duration);
    let fraction = elapsed as f64 / duration as f64;
    1.0 - (1.0 - fraction).powi(3)
}

#[derive(Default)]
pub struct WheelHover {
    start: [f64; SLOT_COUNT],
    target: Option<usize>,
    started: u64,
}

impl WheelHover {
    pub fn select(&mut self, target: Option<usize>, now: u64) {
        if target != self.target {
            // A fast sweep starts the next transition from the frame already on screen.
            self.start = self.values(now);
            self.target = target;
            self.started = now;
        }
    }

    pub fn values(&self, now: u64) -> [f64; SLOT_COUNT] {
        let fraction = eased(now, self.started, HOVER_MS);
        std::array::from_fn(|index| {
            let value = self.start[index];
            let target = f64::from(self.target == Some(index));
            value + (target - value) * fraction
        })
    }
}

#[derive(Clone, Copy, Default)]
enum Phase {
    #[default]
    Hidden,
    Active {
        origin: Point,
        key: Option<u16>,
        started: u64,
        opacity: f64,
    },
    Closing {
        started: u64,
        opacity: f64,
    },
}

#[derive(Default)]
pub struct WheelState {
    phase: Phase,
    style: WheelStyle,
    labels: [String; SLOT_COUNT],
    selected: Option<usize>,
    position: Point,
    hover: WheelHover,
}

impl WheelState {
    pub fn open(
        &mut self,
        now: u64,
        origin: Point,
        work_area: Rect,
        labels: &[impl AsRef<str>],
        style: WheelStyle,
        key: Option<u16>,
    ) -> bool {
        if self.is_active() {
            return false;
        }
        // Reopening during a fade keeps its current opacity and abandons the old close.
        self.phase = Phase::Active {
            origin,
            key,
            started: now,
            opacity: self.opacity(now),
        };
        self.style = style;
        self.labels = std::array::from_fn(|index| {
            labels
                .get(index)
                .map_or("", |label| label.as_ref().trim())
                .to_owned()
        });
        self.selected = None;
        self.position = work_area.wheel_position(origin);
        self.hover = WheelHover::default();
        true
    }

    pub fn is_active(&self) -> bool {
        matches!(self.phase, Phase::Active { .. })
    }

    pub fn is_visible(&self, now: u64) -> bool {
        self.is_active() || self.opacity(now) > 0.0
    }

    pub fn opacity(&self, now: u64) -> f64 {
        match self.phase {
            Phase::Hidden => 0.0,
            Phase::Active {
                started, opacity, ..
            } => opacity + (1.0 - opacity) * eased(now, started, FADE_IN_MS),
            Phase::Closing { started, opacity } => {
                opacity * (1.0 - eased(now, started, FADE_OUT_MS))
            }
        }
    }

    pub fn style(&self) -> WheelStyle {
        self.style
    }

    pub fn labels(&self) -> &[String; SLOT_COUNT] {
        &self.labels
    }

    pub fn position(&self) -> Point {
        self.position
    }

    pub fn anchor(&self) -> Option<Point> {
        let Phase::Active { origin, .. } = self.phase else {
            return None;
        };
        Some(origin.relative_to(self.position))
    }

    pub fn key(&self) -> Option<u16> {
        match self.phase {
            Phase::Active { key, .. } => key,
            _ => None,
        }
    }

    pub fn selected(&self) -> Option<usize> {
        self.selected
    }

    pub fn playable_selection(&self) -> Option<usize> {
        self.selected
            .filter(|&index| !self.labels[index].is_empty())
    }

    pub fn highlights(&self, now: u64) -> [f64; SLOT_COUNT] {
        self.hover.values(now)
    }

    pub fn pointer_moved(&mut self, now: u64, cursor: Point) {
        if let Phase::Active { origin, .. } = self.phase {
            // Selection stays relative to the cursor at invocation, even when the window is clamped.
            self.select(gesture_slot_at(cursor.relative_to(origin)), now);
        }
    }

    pub fn poll(
        &mut self,
        now: u64,
        cursor: Point,
        key_down: bool,
        cancel_requested: bool,
    ) -> Option<usize> {
        if !self.is_active() {
            if !self.is_visible(now) {
                self.cancel();
            }
            return None;
        }
        if cancel_requested {
            self.dismiss(now);
            return None;
        }
        self.pointer_moved(now, cursor);
        if self.key().is_some() && !key_down {
            return self.commit(now);
        }
        None
    }

    pub fn click(&mut self, now: u64, local: Point) -> Option<usize> {
        if !self.is_active() {
            return None;
        }
        // A click selects the sector shown under the cursor, using the displayed canvas center.
        self.select(gesture_slot_at(local.relative_to(CANVAS_CENTER)), now);
        self.commit(now)
    }

    pub fn dismiss(&mut self, now: u64) {
        if self.is_active() {
            let opacity = self.opacity(now);
            if opacity > 0.0 {
                self.phase = Phase::Closing {
                    started: now,
                    opacity,
                };
            } else {
                self.cancel();
            }
        }
    }

    pub fn cancel(&mut self) {
        self.phase = Phase::Hidden;
        self.selected = None;
        self.hover = WheelHover::default();
    }

    fn select(&mut self, selected: Option<usize>, now: u64) {
        self.selected = selected;
        self.hover.select(self.playable_selection(), now);
    }

    fn commit(&mut self, now: u64) -> Option<usize> {
        // Return the slot now, while the caller can continue drawing the closing frame.
        let slot = self.playable_selection();
        self.dismiss(now);
        slot
    }
}

/// 轮盘槽位在绘制与编辑器里显示的文字：标签优先，其次朗读文字、音频文件名。
pub fn labels(wheel: &crate::config::Wheel) -> [String; crate::config::SLOT_COUNT] {
    std::array::from_fn(|index| {
        if wheel.slots[index].is_empty() && wheel.texts[index].is_empty() {
            String::new()
        } else if !wheel.labels[index].is_empty() {
            wheel.labels[index].clone()
        } else if !wheel.texts[index].is_empty() {
            wheel.texts[index].clone()
        } else {
            wheel.slots[index].clone()
        }
    })
}
