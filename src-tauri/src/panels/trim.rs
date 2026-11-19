use std::{io, mem, ptr};

use windows_sys::Win32::{
    Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, WPARAM},
    Graphics::Gdi::*,
    System::SystemServices::SS_NOTIFY,
    UI::{
        Input::KeyboardAndMouse::{
            GetFocus, GetKeyState, ReleaseCapture, SetCapture, SetFocus, VK_LEFT, VK_RIGHT,
            VK_SHIFT, VK_SPACE,
        },
        Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass},
        WindowsAndMessaging::*,
    },
};

use crate::{
    config::Settings,
    desktop::{paint_buffered, wide},
    dialog::{Panel, Template},
    wheel::WheelStyle,
};

use super::{
    ACCENT, ADD, BASE, Chrome, ELEVATED, END, END_LABEL, ERROR, GAIN, GAIN_VALUE, INFO, LABEL,
    LOADING, MUTED, PANEL_SURFACE, PICK, PLAY, PRECISE, PRIMARY, RANGE_SUMMARY, SAVE, SEPARATOR,
    START, START_LABEL, STOP, TABS, TRIM_MESSAGE, WAVE, WHEEL, button, choices, combo, dimensions,
    edit, fill, label,
    preview::{Preview, wheel_labels},
    rounded, wheel_names, with_pen,
};

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Range {
    pub start: f64,
    pub end: f64,
    pub duration: f64,
}

impl Range {
    fn set(&mut self, start: f64, end: f64) {
        if start.is_finite() && end.is_finite() {
            self.start = start.min(end).clamp(0.0, self.duration);
            self.end = start.max(end).clamp(0.0, self.duration);
        }
    }

    fn dragged(self, drag: Drag, at: f64) -> Self {
        let mut next = self;
        let at = at.clamp(0.0, self.duration);
        let gap = self.duration.min(0.001);
        match drag {
            Drag::Start => next.start = at.min((self.end - gap).max(0.0)),
            Drag::End => next.end = at.max((self.start + gap).min(self.duration)),
            Drag::Range(anchor) if (at - anchor).abs() >= gap => next.set(anchor, at),
            Drag::Range(_) => (),
        }
        next
    }

    fn edited(self, start: f64, end: f64) -> Option<Self> {
        let preserve = |value: f64, current: f64| {
            if (value - current).abs() <= 0.0005 {
                current
            } else {
                value.clamp(0.0, self.duration)
            }
        };
        let range = Self {
            start: preserve(start, self.start),
            end: preserve(end, self.end),
            ..self
        };
        // The millisecond fields can round the actual last PCM frame's timestamp.
        let range = if (end - self.duration).abs() <= 0.0005 {
            Self {
                end: self.duration,
                ..range
            }
        } else {
            range
        };
        (range.end > range.start).then_some(range)
    }
}

#[derive(Clone, Copy)]
enum Drag {
    Start,
    End,
    Range(f64),
}

fn scaled_peaks(mut peaks: Vec<f32>) -> Vec<f32> {
    let maximum = peaks.iter().copied().fold(0.0_f32, f32::max);
    if maximum > 0.0 {
        for peak in &mut peaks {
            *peak /= maximum;
        }
    }
    peaks
}

struct WaveState {
    range: Range,
    peaks: Vec<f32>,
    playhead: Option<f64>,
    drag: Option<Drag>,
    active: Drag,
    previewing: bool,
}

pub struct Trim {
    preview: Option<Preview>,
    wave: Box<WaveState>,
    chrome: Chrome,
    pub panel: Panel,
    pub wheel: usize,
    pub slot: usize,
    pub rolling: bool,
}

impl Trim {
    pub fn new(
        host: HWND,
        owner: HWND,
        settings: &Settings,
        target: Option<(usize, usize)>,
        duration: f64,
        gain: f64,
    ) -> io::Result<Self> {
        let rolling = target.is_none();
        let (wheel, slot) = target.unwrap_or((
            settings.rolling_buffer_target[0],
            settings.rolling_buffer_target[1],
        ));
        let mut template = if rolling {
            Template::with_size("保存后台录制", 440, 345)
        } else {
            Template::with_size("截取录音", 440, 190)
        };
        label(
            &mut template,
            300,
            "拖动两端手柄调整范围，或在波形上拖选；空格试听 / 停止。",
            [12, 13, 414, 12],
        );
        // A static control without SS_NOTIFY answers WM_NCHITTEST with
        // HTTRANSPARENT, so the dialog would swallow every click and the wave
        // subclass would never receive the drag.
        template.control(0x82, WAVE, "", BASE | TABS | SS_NOTIFY, [6, 28, 428, 90]);
        label(&mut template, LOADING, "正在加载波形…", [321, 127, 107, 12]);
        label(&mut template, RANGE_SUMMARY, "", [12, 127, 306, 12]);
        // Keep expanded time fields alongside the toggle so collapsing them doesn't leave an empty row.
        button(&mut template, PRECISE, "▸ 精确调整", [8, 143, 73, 17]);
        label(&mut template, START_LABEL, "开始（秒）", [92, 143, 52, 17]);
        edit(&mut template, START, [145, 143, 62, 17]);
        label(&mut template, END_LABEL, "结束（秒）", [221, 143, 52, 17]);
        edit(&mut template, END, [274, 143, 62, 17]);
        let action_y = if rolling { 319 } else { 167 };
        button(&mut template, PLAY, "试听片段", [12, action_y, 72, 17]);
        if rolling {
            // Keep the wheel large enough to read its labels, with the save fields alongside it.
            label(&mut template, 303, "保存到", [225, 170, 198, 14]);
            combo(&mut template, WHEEL, [225, 185, 137, 80]);
            button(&mut template, ADD, "新增轮盘", [368, 185, 60, 17]);
            label(&mut template, 304, "名称", [225, 208, 198, 14]);
            edit(&mut template, LABEL, [225, 223, 203, 17]);
            label(&mut template, 305, "音量", [225, 246, 198, 14]);
            edit(&mut template, GAIN, [225, 260, 163, 17]);
            label(&mut template, GAIN_VALUE, "100%", [394, 261, 34, 14]);
            template.control(
                0x82,
                PICK,
                "",
                BASE | TABS | 0x0000_0100,
                [12, 167, 205, 128],
            );
            label(&mut template, INFO, "", [12, 300, 416, 14]);
        } else {
            // Regular trims keep their current slot label and volume via the shared action contract.
            edit(&mut template, LABEL, [0, 0, 1, 1]);
            edit(&mut template, GAIN, [0, 0, 1, 1]);
        }
        button(&mut template, SAVE, "保存片段", [302, action_y, 70, 17]);
        button(
            &mut template,
            IDCANCEL as u16,
            "取消",
            [379, action_y, 49, 17],
        );
        let panel = Panel::new(owner, &template, host, TRIM_MESSAGE)?;
        let chrome = Chrome::attach(&panel, true, rolling)?;
        let preview = if rolling {
            Some(Preview::new(panel.child(PICK), false)?)
        } else {
            None
        };
        let mut wave = Box::new(WaveState {
            range: Range {
                start: 0.0,
                end: duration,
                duration,
            },
            peaks: Vec::new(),
            playhead: None,
            drag: None,
            active: Drag::Start,
            previewing: false,
        });
        if unsafe {
            SetWindowSubclass(
                panel.child(WAVE),
                Some(wave_proc),
                1,
                ptr::from_mut(wave.as_mut()) as usize,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        let mut trim = Self {
            preview,
            wave,
            chrome,
            panel,
            wheel,
            slot,
            rolling,
        };
        trim.update_range_fields();
        trim.chrome.gain(gain);
        trim.refresh(settings)?;
        Ok(trim)
    }

    pub fn accepts(&self, message: &MSG) -> bool {
        unsafe {
            if message.message == WM_KEYDOWN
                && message.wParam as u16 == VK_SPACE
                && IsWindowVisible(self.panel.0) != 0
                && (message.hwnd == self.panel.0 || IsChild(self.panel.0, message.hwnd) != 0)
                && ![START, END, LABEL]
                    .iter()
                    .any(|id| message.hwnd == self.panel.child(*id))
                && message.hwnd != self.panel.child(WHEEL)
            {
                if message.lParam & (1 << 30) == 0 {
                    PostMessageW(self.panel.0, WM_COMMAND, usize::from(PLAY), 0);
                }
                return true;
            }
        }
        self.panel.accepts(message)
    }

    pub fn refresh(&mut self, settings: &Settings) -> io::Result<()> {
        self.chrome.set_ui_font(&settings.ui_font);
        if let Some(preview) = &mut self.preview {
            let wheel = settings.wheel(self.wheel).expect("wheel index in range");
            choices(&self.panel, WHEEL, wheel_names(settings), self.wheel);
            let detail = if !wheel.texts[self.slot].trim().is_empty() {
                let text = &wheel.texts[self.slot];
                let preview: String = text.chars().take(38).collect();
                let ellipsis = if text.chars().count() > 38 { "…" } else { "" };
                format!("保存将替换原有文字：{preview}{ellipsis}")
            } else if !wheel.slots[self.slot].is_empty() {
                let label = if wheel.labels[self.slot].is_empty() {
                    &wheel.slots[self.slot]
                } else {
                    &wheel.labels[self.slot]
                };
                format!("保存将替换原有音频：{label}")
            } else {
                "空槽".into()
            };
            self.panel
                .set_text(INFO, &format!("槽位 {} · {detail}", self.slot + 1));
            preview.draw(
                &wheel_labels(&wheel),
                WheelStyle::from_config(&settings.chat_wheel_style),
                self.slot,
                &settings.ui_font,
            )?;
        }
        Ok(())
    }

    pub fn take_slot(&mut self) -> Option<usize> {
        self.preview.as_mut()?.take_slot()
    }

    pub fn update_range_fields(&self) {
        self.panel
            .set_text(START, &format!("{:.3}", self.wave.range.start));
        self.panel
            .set_text(END, &format!("{:.3}", self.wave.range.end));
        self.update_summary();
    }

    fn update_summary(&self) {
        let range = self.wave.range;
        self.panel.set_text(
            RANGE_SUMMARY,
            &format!(
                "起点 {:.3} 秒    终点 {:.3} 秒    片段 {:.3} 秒",
                range.start,
                range.end,
                range.end - range.start
            ),
        );
    }

    pub fn range(&mut self) -> io::Result<Range> {
        let parse = |id| {
            self.panel
                .text(id)
                .trim()
                .parse::<f64>()
                .ok()
                .filter(|value| value.is_finite())
        };
        let (Some(start), Some(end)) = (parse(START), parse(END)) else {
            return Err(io::Error::other("请输入有效的起止时间"));
        };
        let range = self
            .wave
            .range
            .edited(start, end)
            .ok_or_else(|| io::Error::other("请先选择截取区间"))?;
        if self.wave.range != range {
            self.wave.range = range;
            self.update_summary();
            self.invalidate();
        }
        Ok(self.wave.range)
    }

    pub fn set_peaks(&mut self, peaks: Vec<f32>) {
        self.wave.peaks = scaled_peaks(peaks);
        unsafe { ShowWindow(self.panel.child(LOADING), SW_HIDE) };
        self.invalidate();
    }
    pub fn progress(&mut self, seconds: Option<f64>) {
        self.set_previewing(seconds.is_some());
        let next = seconds.filter(|value| value.is_finite()).map(|value| {
            (self.wave.range.start + value).clamp(self.wave.range.start, self.wave.range.end)
        });
        let previous = mem::replace(&mut self.wave.playhead, next);
        let wave = self.panel.child(WAVE);
        let (width, height) = dimensions(wave);
        let plot = Plot::new(width, height);
        let column = |time: f64| plot.x_at(time, self.wave.range.duration);
        let (before, after) = (previous.map(column), next.map(column));
        if before != after {
            // Repaint the old and new cursor strips, leaving the rest of the plot untouched.
            for x in [before, after].into_iter().flatten() {
                let dirty = RECT {
                    left: x - 1,
                    right: x + 2,
                    bottom: plot.bounds.bottom + 1,
                    ..plot.bounds
                };
                unsafe { InvalidateRect(wave, &dirty, 0) };
            }
        }
    }

    pub fn set_previewing(&mut self, active: bool) {
        self.chrome.state.previewing.set(active);
        if active != self.wave.previewing {
            self.wave.previewing = active;
            self.panel.set_text(
                PLAY,
                if active {
                    "停止试听"
                } else {
                    "试听片段"
                },
            );
        }
    }
    fn invalidate(&self) {
        unsafe { InvalidateRect(self.panel.child(WAVE), ptr::null(), 0) };
    }
}

impl Drop for Trim {
    fn drop(&mut self) {
        unsafe { RemoveWindowSubclass(self.panel.child(WAVE), Some(wave_proc), 1) };
    }
}

unsafe extern "system" fn wave_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    _: usize,
    data: usize,
) -> LRESULT {
    unsafe {
        let state = &mut *(data as *mut WaveState);
        let (width, height) = dimensions(hwnd);
        let plot = Plot::new(width, height);
        let x = i32::from(lp as i16);
        let point = POINT {
            x,
            y: i32::from((lp >> 16) as i16),
        };
        let duration = state.range.duration;
        match msg {
            WM_LBUTTONDOWN if plot.contains(point) => {
                let edge = plot.edge_at(state.range, x);
                if let Some(edge) = edge {
                    state.active = edge;
                }
                state.drag = Some(edge.unwrap_or(Drag::Range(plot.time_at(x, duration))));
                SetFocus(hwnd);
                SetCapture(hwnd);
                0
            }
            WM_MOUSEMOVE | WM_LBUTTONUP if state.drag.is_some() => {
                let drag = state.drag.unwrap();
                change_range(hwnd, state, drag, plot.time_at(x, duration));
                if msg == WM_LBUTTONUP {
                    state.drag = None;
                    ReleaseCapture();
                }
                0
            }
            WM_CAPTURECHANGED => {
                state.drag = None;
                0
            }
            WM_SETCURSOR => {
                let mut point = POINT::default();
                GetCursorPos(&mut point);
                ScreenToClient(hwnd, &mut point);
                if plot.contains(point) {
                    let cursor = if plot.edge_at(state.range, point.x).is_some() {
                        IDC_SIZEWE
                    } else {
                        IDC_CROSS
                    };
                    SetCursor(LoadCursorW(ptr::null_mut(), cursor));
                    return 1;
                }
                DefSubclassProc(hwnd, msg, wp, lp)
            }
            WM_GETDLGCODE => (DLGC_WANTARROWS | DLGC_WANTCHARS) as LRESULT,
            WM_KEYDOWN if matches!(wp as u16, VK_LEFT | VK_RIGHT) => {
                let drag = if GetKeyState(i32::from(VK_SHIFT)) < 0 {
                    Drag::End
                } else {
                    state.active
                };
                let at = if matches!(drag, Drag::Start) {
                    state.range.start
                } else {
                    state.range.end
                };
                let delta =
                    (duration / 100.0).min(0.01) * if wp as u16 == VK_LEFT { -1.0 } else { 1.0 };
                change_range(hwnd, state, drag, at + delta);
                0
            }
            WM_KEYDOWN if wp as u16 == VK_SPACE => {
                let action = if state.previewing { STOP } else { PLAY };
                PostMessageW(GetParent(hwnd), WM_COMMAND, usize::from(action), 0);
                0
            }
            WM_SETFOCUS | WM_KILLFOCUS => {
                InvalidateRect(hwnd, ptr::null(), 0);
                DefSubclassProc(hwnd, msg, wp, lp)
            }
            WM_ERASEBKGND => 1,
            WM_PAINT => {
                paint_buffered(hwnd, |dc, rect| {
                    paint_wave(dc, state, plot, rect, GetFocus() == hwnd);
                });
                0
            }
            _ => DefSubclassProc(hwnd, msg, wp, lp),
        }
    }
}

struct Plot {
    bounds: RECT,
}

impl Plot {
    fn new(width: i32, height: i32) -> Self {
        Self {
            bounds: RECT {
                left: 18,
                top: 18,
                right: (width - 18).max(19),
                bottom: (height - 34).max(19),
            },
        }
    }

    fn contains(&self, point: POINT) -> bool {
        let bounds = self.bounds;
        (bounds.left..=bounds.right).contains(&point.x)
            && (bounds.top..=bounds.bottom).contains(&point.y)
    }

    fn x_at(&self, time: f64, duration: f64) -> i32 {
        self.bounds.left
            + (time / duration * f64::from(self.bounds.right - self.bounds.left)).round() as i32
    }

    fn time_at(&self, x: i32, duration: f64) -> f64 {
        (f64::from(x - self.bounds.left) / f64::from(self.bounds.right - self.bounds.left))
            .clamp(0.0, 1.0)
            * duration
    }

    fn edge_at(&self, range: Range, x: i32) -> Option<Drag> {
        let start = x.abs_diff(self.x_at(range.start, range.duration));
        let end = x.abs_diff(self.x_at(range.end, range.duration));
        if start.min(end) > 12 {
            None
        } else if start <= end {
            Some(Drag::Start)
        } else {
            Some(Drag::End)
        }
    }
}

fn change_range(hwnd: HWND, state: &mut WaveState, drag: Drag, at: f64) {
    let range = state.range.dragged(drag, at);
    if state.range != range {
        state.range = range;
        unsafe {
            InvalidateRect(hwnd, ptr::null(), 0);
            PostMessageW(GetParent(hwnd), WM_COMMAND, usize::from(WAVE), 0);
        }
    }
}

fn line(dc: HDC, x: i32, y: i32, x2: i32, y2: i32) {
    unsafe {
        MoveToEx(dc, x, y, ptr::null_mut());
        LineTo(dc, x2, y2);
    }
}

fn paint_wave(dc: HDC, state: &WaveState, plot: Plot, rect: RECT, focused: bool) {
    let bounds = plot.bounds;
    let middle = (bounds.top + bounds.bottom) / 2;
    let duration = state.range.duration;
    let left = plot.x_at(state.range.start, duration);
    let right = plot.x_at(state.range.end, duration);
    fill(dc, rect, PANEL_SURFACE);
    rounded(dc, bounds, 16, Some(ELEVATED), SEPARATOR);
    let saved_dc = unsafe { SaveDC(dc) };
    let clip = unsafe {
        CreateRoundRectRgn(
            bounds.left + 1,
            bounds.top + 1,
            bounds.right,
            bounds.bottom,
            16,
            16,
        )
    };
    unsafe {
        ExtSelectClipRgn(dc, clip, RGN_AND);
        DeleteObject(clip);
    }
    fill(
        dc,
        RECT {
            left,
            right,
            ..bounds
        },
        0x4b3827,
    );
    fill(
        dc,
        RECT {
            right: left,
            ..bounds
        },
        0x201f1e,
    );
    fill(
        dc,
        RECT {
            left: right,
            ..bounds
        },
        0x201f1e,
    );
    with_pen(dc, 1, SEPARATOR, || {
        line(dc, bounds.left, middle, bounds.right, middle);
        for tick in 0..=5 {
            let x = plot.x_at(duration * f64::from(tick) / 5.0, duration);
            line(dc, x, bounds.top, x, bounds.bottom);
        }
    });
    for (inside, color) in [(false, 0x6c6969), (true, PRIMARY)] {
        with_pen(dc, 1, color, || {
            for (index, peak) in state.peaks.iter().enumerate() {
                let x = bounds.left
                    + ((index as f64 + 0.5) * f64::from(bounds.right - bounds.left)
                        / state.peaks.len() as f64) as i32;
                if inside == (left..=right).contains(&x) {
                    let amplitude =
                        (peak * ((bounds.bottom - bounds.top) / 2 - 12).max(0) as f32) as i32;
                    line(dc, x, middle - amplitude, x, middle + amplitude + 1);
                }
            }
        });
    }
    unsafe {
        RestoreDC(dc, saved_dc);
    }
    for tick in 0..=5 {
        let time = duration * f64::from(tick) / 5.0;
        let at = (plot.x_at(time, duration) - 35).clamp(0, (rect.right - 70).max(0));
        let mut label = RECT {
            left: at,
            top: bounds.bottom + 7,
            right: at + 70,
            bottom: bounds.bottom + 27,
        };
        let text = wide(&format!("{time:.2}s"));
        unsafe {
            SetBkMode(dc, TRANSPARENT as i32);
            SetTextColor(dc, MUTED);
            DrawTextW(
                dc,
                text.as_ptr(),
                -1,
                &mut label,
                DT_CENTER | DT_SINGLELINE | DT_NOPREFIX,
            );
        }
    }
    for x in [left, right] {
        with_pen(dc, 2, ACCENT, || line(dc, x, bounds.top, x, bounds.bottom));
        rounded(
            dc,
            RECT {
                left: x - 5,
                right: x + 5,
                top: middle - 14,
                bottom: middle + 14,
            },
            8,
            Some(ACCENT),
            ACCENT,
        );
    }
    if let Some(time) = state.playhead {
        let x = plot.x_at(time, duration);
        with_pen(dc, 2, ERROR, || line(dc, x, bounds.top, x, bounds.bottom));
    }
    if focused {
        rounded(
            dc,
            RECT {
                left: bounds.left - 4,
                top: bounds.top - 4,
                right: bounds.right + 4,
                bottom: bounds.bottom + 4,
            },
            20,
            None,
            0x8d5329,
        );
    }
}

#[cfg(test)]
mod tests {
    use super::super::logical_px;
    use super::*;
    use crate::desktop::window_text;

    #[test]
    fn recording_trim_keeps_precise_time_fields_on_the_toggle_row() {
        let mut settings = Settings::default();
        settings.chat_wheel_texts[0] = "原有文字".repeat(20);
        let trim = Trim::new(ptr::null_mut(), ptr::null_mut(), &settings, None, 3.0, 1.0).unwrap();
        assert!(
            trim.panel
                .text(INFO)
                .starts_with("槽位 1 · 保存将替换原有文字")
        );
        let mut toggle = RECT::default();
        let mut start = RECT::default();
        unsafe {
            GetWindowRect(trim.panel.child(PRECISE), &mut toggle);
            GetWindowRect(trim.panel.child(START), &mut start);
        }
        assert!(start.left > toggle.right);
        assert!((toggle.top..toggle.bottom).contains(&((start.top + start.bottom) / 2)));
    }

    #[test]
    fn moving_gain_repaints_the_old_thumb_and_progress_track() {
        let trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            3.0,
            1.0,
        )
        .unwrap();
        let gain = trim.chrome.state.gain;
        let (width, height) = dimensions(gain);
        let pointer = |x: i32| (x as u16 as isize) | ((height as isize / 2) << 16);
        unsafe {
            SetWindowPos(
                trim.panel.0,
                ptr::null_mut(),
                -32000,
                -32000,
                0,
                0,
                SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW,
            );
            SendMessageW(gain, WM_LBUTTONDOWN, 1, pointer(width - 2));
            ValidateRect(gain, ptr::null());
            SendMessageW(gain, WM_MOUSEMOVE, 1, pointer(2));
        }
        let mut dirty = RECT::default();
        unsafe {
            GetUpdateRect(gain, &mut dirty, 0);
            SendMessageW(gain, WM_LBUTTONUP, 0, pointer(2));
        }
        assert_eq!(
            (dirty.left, dirty.top, dirty.right, dirty.bottom),
            (0, 0, width, height)
        );
    }

    #[test]
    fn trim_gain_click_and_drag_follow_the_visible_track() {
        let trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            3.0,
            1.0,
        )
        .unwrap();
        let gain = trim.chrome.state.gain;
        let (width, height) = dimensions(gain);
        let pointer = |x: i32| (x as u16 as isize) | ((height as isize / 2) << 16);
        unsafe {
            SendMessageW(gain, WM_LBUTTONDOWN, 1, pointer(width / 2));
        }
        assert_eq!(trim.panel.text(GAIN), "150");
        unsafe {
            SendMessageW(gain, WM_MOUSEMOVE, 1, pointer(width));
        }
        assert_eq!(trim.panel.text(GAIN), "300");
        unsafe {
            SendMessageW(gain, WM_MOUSEMOVE, 1, pointer(-1));
            SendMessageW(gain, WM_LBUTTONUP, 0, pointer(-1));
        }
        assert_eq!(trim.panel.text(GAIN), "0");
        assert_ne!(
            unsafe { windows_sys::Win32::UI::Input::KeyboardAndMouse::GetCapture() },
            gain
        );
    }

    #[test]
    fn preview_progress_invalidates_only_the_changed_playhead_pixels() {
        let mut trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            10.0,
            1.0,
        )
        .unwrap();
        let wave = trim.panel.child(WAVE);
        let (width, height) = dimensions(wave);
        let plot = Plot::new(width, height);
        let x = plot.x_at(1.0, 10.0);
        let mut dirty = RECT::default();
        unsafe {
            SetWindowPos(
                trim.panel.0,
                ptr::null_mut(),
                -32000,
                -32000,
                0,
                0,
                SWP_NOACTIVATE | SWP_NOSIZE | SWP_NOZORDER | SWP_SHOWWINDOW,
            );
            ValidateRect(wave, ptr::null());
        }

        trim.progress(Some(1.0));
        assert_ne!(unsafe { GetUpdateRect(wave, &mut dirty, 0) }, 0);
        assert!(dirty.left < x && dirty.right > x);
        assert!(
            dirty.right - dirty.left <= 3,
            "only the playhead should be dirty"
        );
        unsafe { ValidateRect(wave, ptr::null()) };

        trim.progress(Some(1.0 + f64::EPSILON));
        assert_eq!(unsafe { GetUpdateRect(wave, &mut dirty, 0) }, 0);

        trim.progress(None);
        assert_ne!(unsafe { GetUpdateRect(wave, &mut dirty, 0) }, 0);
        assert!(dirty.left < x && dirty.right > x);
        assert!(dirty.right - dirty.left <= 3);
    }

    #[test]
    fn rolling_trim_shows_a_large_wheel_beside_its_save_fields() {
        let trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            3.0,
            1.0,
        )
        .unwrap();
        let bounds = |id| {
            let mut rect = RECT::default();
            assert_ne!(unsafe { GetWindowRect(trim.panel.child(id), &mut rect) }, 0);
            rect
        };
        let wheel = bounds(PICK);
        let name = bounds(LABEL);
        let info = bounds(INFO);
        assert_eq!(window_text(trim.panel.0), "保存后台录制");
        assert!(wheel.right <= name.left && (wheel.top..wheel.bottom).contains(&name.top));
        assert!(wheel.bottom <= info.top && info.bottom < bounds(SAVE).top);
        assert!(wheel.bottom - wheel.top >= logical_px(trim.panel.0, 220));
    }

    #[test]
    fn wave_control_is_not_transparent_to_the_mouse() {
        // The wave is a static control. Without SS_NOTIFY, the static window
        // answers WM_NCHITTEST with HTTRANSPARENT, so clicks land on the dialog
        // and the subclass never receives WM_LBUTTONDOWN: dragging does nothing.
        let trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            3.0,
            1.0,
        )
        .unwrap();
        let wave = trim.panel.child(WAVE);
        let hit = unsafe { SendMessageW(wave, WM_NCHITTEST, 0, 0) } as u32;
        assert_eq!(hit, HTCLIENT);
    }

    #[test]
    fn rolling_save_wave_accepts_a_pointer_drag() {
        let trim = Trim::new(
            ptr::null_mut(),
            ptr::null_mut(),
            &Settings::default(),
            None,
            10.0,
            1.0,
        )
        .unwrap();
        let wave = trim.panel.child(WAVE);
        let (width, height) = dimensions(wave);
        let plot = Plot::new(width, height);
        let y = (plot.bounds.top + plot.bounds.bottom) / 2;
        let pointer = |time: f64| (plot.x_at(time, 10.0) as isize) | ((y as isize) << 16);
        unsafe {
            SendMessageW(wave, WM_LBUTTONDOWN, 1, pointer(3.0));
            SendMessageW(wave, WM_MOUSEMOVE, 1, pointer(7.0));
            SendMessageW(wave, WM_LBUTTONUP, 0, pointer(7.0));
        }
        assert_eq!((trim.wave.range.start, trim.wave.range.end), (3.0, 7.0));
    }

    #[test]
    fn selection_orders_and_clamps_dragged_bounds() {
        let mut range = Range {
            start: 0.0,
            end: 3.0,
            duration: 3.0,
        };
        range.set(2.0, -4.0);
        assert_eq!((range.start, range.end), (0.0, 2.0));
        range.set(f64::NAN, 3.0);
        assert_eq!((range.start, range.end), (0.0, 2.0));
    }

    #[test]
    fn trim_handles_keep_a_gap_and_cannot_cross() {
        let range = Range {
            start: 0.25,
            end: 0.75,
            duration: 1.0,
        };
        let start = range.dragged(Drag::Start, 1.0);
        assert_eq!((start.start, start.end), (0.749, 0.75));
        let end = range.dragged(Drag::End, 0.0);
        assert_eq!((end.start, end.end), (0.25, 0.251));
    }

    #[test]
    fn trim_drag_selects_in_both_directions_and_ignores_a_click() {
        let range = Range {
            start: 0.0,
            end: 1.0,
            duration: 1.0,
        };
        assert_eq!(range.dragged(Drag::Range(0.5), 0.5), range);
        let selected = range.dragged(Drag::Range(0.6), 0.2);
        assert_eq!((selected.start, selected.end), (0.2, 0.6));
    }

    #[test]
    fn wave_preview_uses_its_own_peak_and_preserves_silence() {
        assert_eq!(scaled_peaks(vec![0.0, 0.1, 0.2]), [0.0, 0.5, 1.0]);
        assert_eq!(scaled_peaks(vec![0.0]), [0.0]);
    }
}
