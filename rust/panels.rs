use std::{io, mem, ptr};
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::UI::Controls::EM_SETLIMITTEXT;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{ReleaseCapture, SetCapture};
use windows_sys::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

use crate::config::{Settings, Wheel};
use crate::desktop::wide;
use crate::dialog::{Panel, Template};
use crate::wheel::{CANVAS_HEIGHT, CANVAS_WIDTH, Point, WheelStyle, picker_slot_at};
use crate::wheel_paint::{WheelBitmap, WheelFrame, WheelRenderer};

pub const EDITOR_MESSAGE: u32 = WM_APP + 11;
pub const TRIM_MESSAGE: u32 = WM_APP + 12;
pub const PICK: u16 = 200;
pub const WHEEL: u16 = 201;
pub const ADD: u16 = 202;
pub const REMOVE: u16 = 203;
pub const NAME: u16 = 204;
pub const HOTKEY: u16 = 205;
pub const STYLE: u16 = 206;
pub const LABEL: u16 = 207;
pub const TEXT: u16 = 208;
pub const GAIN: u16 = 209;
pub const SAVE: u16 = 210;
pub const IMPORT: u16 = 211;
pub const RECORD: u16 = 212;
pub const CROP: u16 = 213;
pub const PLAY: u16 = 214;
pub const CLEAR: u16 = 215;
pub const INFO: u16 = 216;
pub const WAVE: u16 = 217;
pub const START: u16 = 218;
pub const END: u16 = 219;
pub const STOP: u16 = 220;
pub const AUDIO_MODE: u16 = 221;
pub const TEXT_MODE: u16 = 222;
pub const CANCEL_RECORDING: u16 = 223;

const TABS: u32 = WS_TABSTOP;
const BASE: u32 = WS_VISIBLE | WS_CHILD;

fn button(template: &mut Template, id: u16, label: &str, bounds: [u16; 4]) {
    template.control(0x80, id, label, BASE | TABS | BS_PUSHBUTTON as u32, bounds);
}

fn label(template: &mut Template, id: u16, label: &str, bounds: [u16; 4]) {
    template.control(0x82, id, label, BASE, bounds);
}

fn edit(template: &mut Template, id: u16, bounds: [u16; 4]) {
    template.control(
        0x81,
        id,
        "",
        BASE | TABS | WS_BORDER | ES_AUTOHSCROLL as u32,
        bounds,
    );
}

fn combo(template: &mut Template, id: u16, bounds: [u16; 4]) {
    template.control(
        0x85,
        id,
        "",
        BASE | TABS | WS_VSCROLL | CBS_DROPDOWNLIST as u32,
        bounds,
    );
}

fn choices(panel: &Panel, id: u16, values: impl IntoIterator<Item = String>, index: usize) {
    let widget = panel.child(id);
    unsafe {
        SendMessageW(widget, CB_RESETCONTENT, 0, 0);
        for value in values {
            let value = wide(&value);
            SendMessageW(widget, CB_ADDSTRING, 0, value.as_ptr() as isize);
        }
        SendMessageW(widget, CB_SETCURSEL, index, 0);
    }
}

pub fn selected(panel: &Panel, id: u16) -> usize {
    usize::try_from(unsafe { SendMessageW(panel.child(id), CB_GETCURSEL, 0, 0) }).unwrap_or(0)
}

fn wheel_names(settings: &Settings) -> impl Iterator<Item = String> + '_ {
    (0..settings.wheel_count()).map(|i| {
        let name = settings.wheel(i).expect("wheel index in range").name;
        if name.is_empty() {
            format!("轮盘 {}", i + 1)
        } else {
            name
        }
    })
}

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

struct Preview {
    child: HWND,
    renderer: WheelRenderer,
    state: Box<PreviewState>,
}

struct PreviewState {
    bitmap: Option<WheelBitmap>,
    selected: Option<usize>,
}

impl Preview {
    fn new(child: HWND) -> io::Result<Self> {
        let renderer = WheelRenderer::new().map_err(io::Error::other)?;
        let mut state = Box::new(PreviewState {
            bitmap: None,
            selected: None,
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
        Ok(Self {
            child,
            renderer,
            state,
        })
    }

    fn draw(&mut self, labels: &[String; 8], style: WheelStyle, slot: usize) -> io::Result<()> {
        let mut frame = WheelFrame::new(labels, style);
        frame.editing = true;
        frame.selected = Some(slot);
        let mut highlights = [0.0; 8];
        highlights[slot] = 1.0;
        frame.highlights = Some(highlights);
        let (width, height) = dimensions(self.child);
        let scale = (f64::from(width) / CANVAS_WIDTH).min(f64::from(height) / CANVAS_HEIGHT);
        let mut bitmap = self
            .renderer
            .render(frame, scale)
            .map_err(io::Error::other)?;
        // Composite premultiplied pixels on the dialog background while preserving native geometry.
        for pixel in bitmap.pixels.as_chunks_mut::<4>().0 {
            let base = ((255 - u16::from(pixel[3])) * 240 / 255) as u8;
            for channel in &mut pixel[..3] {
                *channel = channel.saturating_add(base);
            }
        }
        self.state.bitmap = Some(bitmap);
        unsafe { InvalidateRect(self.child, ptr::null(), 1) };
        Ok(())
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
            WM_PAINT => {
                let mut paint = PAINTSTRUCT::default();
                let dc = BeginPaint(hwnd, &mut paint);
                if let Some(bitmap) = &state.bitmap {
                    let (w, h) = (bitmap.width as i32, bitmap.height as i32);
                    let mut info = BITMAPINFO::default();
                    info.bmiHeader.biSize = mem::size_of::<BITMAPINFOHEADER>() as u32;
                    info.bmiHeader.biWidth = w;
                    info.bmiHeader.biHeight = -h;
                    info.bmiHeader.biPlanes = 1;
                    info.bmiHeader.biBitCount = 32;
                    StretchDIBits(
                        dc,
                        (width - w) / 2,
                        (height - h) / 2,
                        w,
                        h,
                        0,
                        0,
                        w,
                        h,
                        bitmap.pixels.as_ptr().cast(),
                        &info,
                        DIB_RGB_COLORS,
                        SRCCOPY,
                    );
                }
                EndPaint(hwnd, &paint);
                0
            }
            WM_LBUTTONDOWN => {
                let size = (f64::from(width) / CANVAS_WIDTH).min(f64::from(height) / CANVAS_HEIGHT)
                    * CANVAS_WIDTH;
                let x = f64::from(lp as i16) - (f64::from(width) - size) / 2.0;
                let y = f64::from((lp >> 16) as i16) - (f64::from(height) - size) / 2.0;
                if let Some(slot) = picker_slot_at(Point::new(x, y), size) {
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

fn dimensions(hwnd: HWND) -> (i32, i32) {
    let mut rect = RECT::default();
    unsafe { GetClientRect(hwnd, &mut rect) };
    (rect.right, rect.bottom)
}

pub struct Editor {
    preview: Preview,
    pub panel: Panel,
    pub wheel: usize,
    pub slot: usize,
    pub empty_text_mode: bool,
}

impl Editor {
    pub fn new(host: HWND, owner: HWND, settings: &Settings) -> io::Result<Self> {
        let mut template = Template::with_size("聊天轮盘", 500, 282);
        combo(&mut template, WHEEL, [12, 12, 130, 100]);
        button(&mut template, ADD, "新增轮盘", [150, 12, 72, 17]);
        button(&mut template, REMOVE, "删除轮盘", [228, 12, 72, 17]);
        combo(&mut template, STYLE, [347, 12, 140, 100]);
        label(&mut template, PICK, "", [8, 38, 224, 214]);
        label(&mut template, 300, "轮盘名称", [240, 42, 52, 15]);
        edit(&mut template, NAME, [296, 39, 191, 16]);
        label(&mut template, 301, "轮盘快捷键", [240, 64, 55, 15]);
        button(&mut template, HOTKEY, "录制快捷键", [296, 61, 191, 16]);
        label(&mut template, 302, "槽位名称", [240, 92, 52, 15]);
        edit(&mut template, LABEL, [296, 89, 191, 16]);
        label(&mut template, 303, "朗读文本", [240, 114, 52, 15]);
        edit(&mut template, TEXT, [296, 111, 191, 16]);
        label(&mut template, 304, "音量 (%)", [240, 136, 52, 15]);
        edit(&mut template, GAIN, [296, 133, 58, 16]);
        button(&mut template, AUDIO_MODE, "音频", [361, 133, 59, 17]);
        button(&mut template, TEXT_MODE, "文字", [428, 133, 59, 17]);
        button(&mut template, IMPORT, "导入音频", [240, 163, 74, 17]);
        button(&mut template, RECORD, "录制系统声音", [320, 163, 84, 17]);
        button(&mut template, CROP, "截取", [410, 163, 77, 17]);
        button(&mut template, PLAY, "试听", [240, 188, 74, 17]);
        button(&mut template, CLEAR, "清空槽位", [320, 188, 84, 17]);
        button(
            &mut template,
            CANCEL_RECORDING,
            "取消录制",
            [410, 188, 77, 17],
        );
        label(&mut template, INFO, "", [240, 216, 247, 35]);
        button(&mut template, IDCANCEL as u16, "关闭", [425, 253, 62, 17]);
        let panel = Panel::new(owner, &template, host, EDITOR_MESSAGE)?;
        let preview = Preview::new(panel.child(PICK))?;
        let mut editor = Self {
            preview,
            panel,
            wheel: settings.chat_wheel_last_index,
            slot: 0,
            empty_text_mode: false,
        };
        for (id, limit) in [(NAME, 20), (LABEL, 40), (TEXT, 200), (GAIN, 8)] {
            unsafe { SendMessageW(editor.panel.child(id), EM_SETLIMITTEXT, limit, 0) };
        }
        editor.recording(false);
        editor.refresh(settings)?;
        Ok(editor)
    }

    pub fn take_slot(&mut self) -> Option<usize> {
        self.preview.state.selected.take()
    }

    pub fn refresh(&mut self, settings: &Settings) -> io::Result<()> {
        self.wheel = self.wheel.min(settings.wheel_count() - 1);
        let wheel = settings.wheel(self.wheel).expect("wheel index in range");
        choices(&self.panel, WHEEL, wheel_names(settings), self.wheel);
        let style_index = crate::config::CHAT_WHEEL_STYLES
            .iter()
            .position(|style| *style == settings.chat_wheel_style)
            .unwrap_or(0);
        choices(
            &self.panel,
            STYLE,
            ["经典玻璃", "轻薄玻璃", "悬浮花瓣", "竞技轮盘"]
                .into_iter()
                .map(str::to_owned),
            style_index,
        );
        self.panel.set_text(NAME, &wheel.name);
        self.panel.set_text(
            HOTKEY,
            &format!("录制 · {}", crate::hotkey::display_hotkey(&wheel.hotkey)),
        );
        self.panel.set_text(LABEL, &wheel.labels[self.slot]);
        self.panel.set_text(TEXT, &wheel.texts[self.slot]);
        self.panel
            .set_text(GAIN, &format!("{:.0}", wheel.volumes[self.slot] * 100.0));
        let text_mode = !wheel.texts[self.slot].is_empty()
            || (wheel.slots[self.slot].is_empty() && self.empty_text_mode);
        for (id, visible) in [
            (TEXT, text_mode),
            (303, text_mode),
            (GAIN, !text_mode),
            (304, !text_mode),
            (IMPORT, !text_mode),
            (RECORD, !text_mode),
            (CROP, !text_mode),
        ] {
            unsafe {
                ShowWindow(
                    self.panel.child(id),
                    if visible { SW_SHOW } else { SW_HIDE },
                )
            };
        }
        let content = if !wheel.texts[self.slot].is_empty() {
            "文字"
        } else if !wheel.slots[self.slot].is_empty() {
            "音频"
        } else {
            "空槽"
        };
        self.panel
            .set_text(INFO, &format!("音频 {}　{content}", self.slot + 1));
        self.preview.draw(
            &wheel_labels(&wheel),
            WheelStyle::from_config(&settings.chat_wheel_style),
            self.slot,
        )
    }

    pub fn recording(&self, active: bool) {
        self.panel.set_text(
            RECORD,
            if active {
                "停止录制"
            } else {
                "录制系统声音"
            },
        );
        unsafe {
            ShowWindow(
                self.panel.child(CANCEL_RECORDING),
                if active { SW_SHOW } else { SW_HIDE },
            )
        };
    }
}

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
}

struct WaveState {
    range: Range,
    peaks: Vec<f32>,
    playhead: Option<f64>,
    anchor: Option<f64>,
}

pub struct Trim {
    preview: Option<Preview>,
    wave: Box<WaveState>,
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
        let mut template = Template::with_size(
            if rolling {
                "截取后台录制"
            } else {
                "截取录音"
            },
            500,
            if rolling { 345 } else { 199 },
        );
        label(&mut template, WAVE, "", [10, 10, 478, 82]);
        label(&mut template, 300, "开始 (秒)", [10, 106, 55, 16]);
        edit(&mut template, START, [64, 103, 76, 17]);
        label(&mut template, 301, "结束 (秒)", [153, 106, 55, 16]);
        edit(&mut template, END, [207, 103, 76, 17]);
        button(&mut template, PLAY, "试听", [307, 103, 54, 18]);
        button(&mut template, STOP, "停止", [369, 103, 54, 18]);
        if rolling {
            label(&mut template, PICK, "", [10, 130, 220, 200]);
            combo(&mut template, WHEEL, [242, 143, 192, 80]);
            button(&mut template, ADD, "新增轮盘", [242, 171, 80, 18]);
        }
        let x = if rolling { 242 } else { 10 };
        let y = if rolling { 204 } else { 134 };
        label(&mut template, 302, "名称", [x, y, 38, 16]);
        edit(&mut template, LABEL, [x + 44, y - 3, 190, 17]);
        label(&mut template, 303, "音量 (%)", [x, y + 24, 44, 16]);
        edit(&mut template, GAIN, [x + 44, y + 21, 60, 17]);
        button(
            &mut template,
            SAVE,
            "保存片段",
            [342, if rolling { 303 } else { 160 }, 74, 18],
        );
        button(
            &mut template,
            IDCANCEL as u16,
            "取消",
            [422, if rolling { 303 } else { 160 }, 65, 18],
        );
        label(
            &mut template,
            INFO,
            "",
            [x, if rolling { 259 } else { 178 }, 230, 15],
        );
        let panel = Panel::new(owner, &template, host, TRIM_MESSAGE)?;
        let preview = if rolling {
            Some(Preview::new(panel.child(PICK))?)
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
            anchor: None,
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
            panel,
            wheel,
            slot,
            rolling,
        };
        trim.update_range_fields();
        trim.panel.set_text(GAIN, &format!("{:.0}", gain * 100.0));
        trim.refresh(settings)?;
        Ok(trim)
    }

    pub fn refresh(&mut self, settings: &Settings) -> io::Result<()> {
        if let Some(preview) = &mut self.preview {
            let wheel = settings.wheel(self.wheel).expect("wheel index in range");
            choices(&self.panel, WHEEL, wheel_names(settings), self.wheel);
            self.panel.set_text(
                INFO,
                &format!(
                    "音频 {}　{}",
                    self.slot + 1,
                    wheel_labels(&wheel)[self.slot]
                ),
            );
            preview.draw(
                &wheel_labels(&wheel),
                WheelStyle::from_config(&settings.chat_wheel_style),
                self.slot,
            )?;
        }
        Ok(())
    }

    pub fn take_slot(&mut self) -> Option<usize> {
        self.preview.as_mut()?.state.selected.take()
    }

    pub fn update_range_fields(&self) {
        self.panel
            .set_text(START, &format!("{:.3}", self.wave.range.start));
        self.panel
            .set_text(END, &format!("{:.3}", self.wave.range.end));
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
        if (start - self.wave.range.start).abs() > 0.0005
            || (end - self.wave.range.end).abs() > 0.0005
        {
            self.wave.range.set(start, end);
            self.invalidate();
        }
        if self.wave.range.end <= self.wave.range.start {
            return Err(io::Error::other("请先选择截取区间"));
        }
        Ok(self.wave.range)
    }

    pub fn set_peaks(&mut self, peaks: Vec<f32>) {
        self.wave.peaks = peaks;
        self.invalidate();
    }
    pub fn progress(&mut self, seconds: Option<f64>) {
        self.wave.playhead = seconds.map(|value| self.wave.range.start + value);
        self.invalidate();
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
        let seconds = || {
            (f64::from(lp as i16) / f64::from(width.max(1))).clamp(0.0, 1.0) * state.range.duration
        };
        match msg {
            WM_LBUTTONDOWN => {
                let x = seconds();
                let tolerance = 8.0 * state.range.duration / f64::from(width.max(1));
                let anchor = if (x - state.range.start).abs() < tolerance {
                    state.range.end
                } else if (x - state.range.end).abs() < tolerance {
                    state.range.start
                } else {
                    x
                };
                state.anchor = Some(anchor);
                state.range.set(anchor, x);
                SetCapture(hwnd);
                InvalidateRect(hwnd, ptr::null(), 0);
                PostMessageW(GetParent(hwnd), WM_COMMAND, usize::from(WAVE), 0);
                0
            }
            WM_MOUSEMOVE | WM_LBUTTONUP if state.anchor.is_some() => {
                let x = seconds();
                state.range.set(state.anchor.unwrap_or(x), x);
                if msg == WM_LBUTTONUP {
                    state.anchor = None;
                    ReleaseCapture();
                }
                InvalidateRect(hwnd, ptr::null(), 0);
                PostMessageW(GetParent(hwnd), WM_COMMAND, usize::from(WAVE), 0);
                0
            }
            WM_CAPTURECHANGED => {
                state.anchor = None;
                0
            }
            WM_PAINT => {
                let mut paint = PAINTSTRUCT::default();
                let dc = BeginPaint(hwnd, &mut paint);
                let pixel = |seconds| (seconds / state.range.duration * f64::from(width)) as i32;
                for (left, right, color) in [
                    (0, width, 0x29231f),
                    (pixel(state.range.start), pixel(state.range.end), 0x474134),
                ] {
                    let brush = CreateSolidBrush(color);
                    FillRect(
                        dc,
                        &RECT {
                            left,
                            top: 0,
                            right,
                            bottom: height,
                        },
                        brush,
                    );
                    DeleteObject(brush);
                }
                let pen = CreatePen(PS_SOLID, 1, 0x8ed890);
                let old = SelectObject(dc, pen);
                for (index, peak) in state.peaks.iter().enumerate() {
                    let x = (index * width as usize / state.peaks.len()) as i32;
                    let amplitude = (peak * height as f32 / 2.0) as i32;
                    MoveToEx(dc, x, height / 2 - amplitude, ptr::null_mut());
                    LineTo(dc, x, height / 2 + amplitude + 1);
                }
                SelectObject(dc, old);
                DeleteObject(pen);
                if let Some(position) = state.playhead {
                    let red = CreatePen(PS_SOLID, 2, 0x3434f0);
                    let old = SelectObject(dc, red);
                    MoveToEx(dc, pixel(position), 0, ptr::null_mut());
                    LineTo(dc, pixel(position), height);
                    SelectObject(dc, old);
                    DeleteObject(red);
                }
                EndPaint(hwnd, &paint);
                0
            }
            _ => DefSubclassProc(hwnd, msg, wp, lp),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
}
