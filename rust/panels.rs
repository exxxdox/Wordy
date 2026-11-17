use std::{cell::Cell, io, mem, ptr};
use windows_sys::Win32::Foundation::{HWND, LPARAM, LRESULT, POINT, RECT, WPARAM};
use windows_sys::Win32::Graphics::Dwm::{DWMWA_USE_IMMERSIVE_DARK_MODE, DwmSetWindowAttribute};
use windows_sys::Win32::Graphics::Gdi::*;
use windows_sys::Win32::UI::Controls::{
    DRAWITEMSTRUCT, EM_SETLIMITTEXT, ODS_DISABLED, ODS_FOCUS, ODS_SELECTED, ODT_BUTTON,
    TB_ENDTRACK, TBM_SETPAGESIZE, TBM_SETPOS, TBM_SETRANGE, TBS_NOTICKS, TRACKBAR_CLASSW,
};
use windows_sys::Win32::UI::HiDpi::GetDpiForWindow;
use windows_sys::Win32::UI::Input::KeyboardAndMouse::{
    EnableWindow, GetFocus, GetKeyState, ReleaseCapture, SetCapture, SetFocus, VK_LEFT, VK_RIGHT,
    VK_SHIFT, VK_SPACE,
};
use windows_sys::Win32::UI::Shell::{DefSubclassProc, RemoveWindowSubclass, SetWindowSubclass};
use windows_sys::Win32::UI::WindowsAndMessaging::*;

use crate::config::{Settings, Wheel};
use crate::desktop::{wide, window_text};
use crate::dialog::{Panel, Template};
use crate::settings_window::paint_embedded_backdrop;
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
const PRECISE: u16 = 224;
const RANGE_SUMMARY: u16 = 225;
const LOADING: u16 = 226;
const START_LABEL: u16 = 301;
const END_LABEL: u16 = 302;
const GAIN_VALUE: u16 = 227;
const TBM_GETPOS: u32 = WM_USER;

// COLORREF stores red, green, blue in least-to-most-significant byte order.
const PANEL_SURFACE: u32 = 0x1e1c1c;
const ELEVATED: u32 = 0x2e2c2c;
const EDITOR_CARD: u32 = 0x3e3b3b;
const SEPARATOR: u32 = 0x3a3838;
const PRIMARY: u32 = 0xf7f5f5;
const MUTED: u32 = 0x9d9898;
const ACCENT: u32 = 0xff840a;
const ERROR: u32 = 0x3a45ff;

const TABS: u32 = WS_TABSTOP;
const BASE: u32 = WS_VISIBLE | WS_CHILD;

fn button(template: &mut Template, id: u16, label: &str, bounds: [u16; 4]) {
    template.control(0x80, id, label, BASE | TABS | BS_OWNERDRAW as u32, bounds);
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

fn editable_combo(template: &mut Template, id: u16, bounds: [u16; 4]) {
    template.control(
        0x85,
        id,
        "",
        BASE | TABS | WS_VSCROLL | CBS_DROPDOWN as u32,
        bounds,
    );
}

struct Chrome {
    hwnd: HWND,
    state: Box<ChromeState>,
}

struct ChromeState {
    surface: HBRUSH,
    elevated: HBRUSH,
    card: HBRUSH,
    gain: HWND,
    trim: bool,
    precise: Cell<bool>,
    previewing: Cell<bool>,
    text_mode: Cell<bool>,
    recording: Cell<bool>,
}

impl Chrome {
    fn attach(panel: &Panel, trim: bool, rolling: bool) -> io::Result<Self> {
        let mut state = Box::new(ChromeState {
            surface: unsafe { CreateSolidBrush(PANEL_SURFACE) },
            elevated: unsafe { CreateSolidBrush(ELEVATED) },
            card: unsafe { CreateSolidBrush(EDITOR_CARD) },
            gain: ptr::null_mut(),
            trim,
            precise: Cell::new(false),
            previewing: Cell::new(false),
            text_mode: Cell::new(false),
            recording: Cell::new(false),
        });
        if !trim || rolling {
            // Keep the numeric field as the existing app-message contract and display a slider instead.
            let edit = panel.child(GAIN);
            let mut rect = RECT::default();
            unsafe {
                GetWindowRect(edit, &mut rect);
                MapWindowPoints(ptr::null_mut(), panel.0, ptr::from_mut(&mut rect).cast(), 2);
                state.gain = CreateWindowExW(
                    0,
                    TRACKBAR_CLASSW,
                    ptr::null(),
                    BASE | TABS | TBS_NOTICKS,
                    rect.left,
                    rect.top,
                    rect.right - rect.left,
                    rect.bottom - rect.top,
                    panel.0,
                    ptr::null_mut(),
                    ptr::null_mut(),
                    ptr::null_mut(),
                );
                if !state.gain.is_null() {
                    SendMessageW(state.gain, TBM_SETRANGE, 1, 300 << 16);
                    SendMessageW(state.gain, TBM_SETPAGESIZE, 0, 10);
                }
            }
        }
        if unsafe {
            SetWindowSubclass(
                panel.0,
                Some(chrome_proc),
                7,
                ptr::from_mut(state.as_mut()) as usize,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }
        if trim {
            unsafe {
                let enabled = 1_i32;
                DwmSetWindowAttribute(
                    panel.0,
                    DWMWA_USE_IMMERSIVE_DARK_MODE as u32,
                    ptr::from_ref(&enabled).cast(),
                    mem::size_of_val(&enabled) as u32,
                );
                precise_controls(panel.0, false);
                if !rolling {
                    ShowWindow(panel.child(LABEL), SW_HIDE);
                }
            }
        } else {
            unsafe { SendMessageW(panel.child(WHEEL), CB_LIMITTEXT, 20, 0) };
        }
        if trim || !state.gain.is_null() {
            unsafe { ShowWindow(panel.child(GAIN), SW_HIDE) };
        }
        Ok(Self {
            hwnd: panel.0,
            state,
        })
    }

    fn gain(&self, gain: f64) {
        let percent = (gain * 100.0).round() as usize;
        set_control_text(self.hwnd, GAIN, &percent.to_string());
        set_control_text(self.hwnd, GAIN_VALUE, &format!("{percent}%"));
        unsafe { SendMessageW(self.state.gain, TBM_SETPOS, 1, percent as isize) };
    }
}

impl Drop for Chrome {
    fn drop(&mut self) {
        unsafe { RemoveWindowSubclass(self.hwnd, Some(chrome_proc), 7) };
    }
}

impl Drop for ChromeState {
    fn drop(&mut self) {
        unsafe {
            DeleteObject(self.surface);
            DeleteObject(self.elevated);
            DeleteObject(self.card);
        }
    }
}

fn set_control_text(hwnd: HWND, id: u16, value: &str) {
    let text = wide(value);
    unsafe { SetDlgItemTextW(hwnd, i32::from(id), text.as_ptr()) };
}

unsafe fn precise_controls(hwnd: HWND, visible: bool) {
    for id in [START_LABEL, END_LABEL, START, END] {
        unsafe {
            ShowWindow(
                GetDlgItem(hwnd, i32::from(id)),
                if visible { SW_SHOW } else { SW_HIDE },
            )
        };
    }
}

unsafe extern "system" fn chrome_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    _: usize,
    data: usize,
) -> LRESULT {
    unsafe {
        let state = &*(data as *const ChromeState);
        match msg {
            WM_SIZE if !state.trim => {
                layout_editor(hwnd, state);
                DefSubclassProc(hwnd, msg, wp, lp)
            }
            WM_ERASEBKGND if !state.trim => 1,
            WM_PAINT if !state.trim => {
                // Limit the background paint to the dialog's uncovered client area.
                let mut paint = PAINTSTRUCT::default();
                let dc = BeginPaint(hwnd, &mut paint);
                paint_embedded_backdrop(hwnd, dc);
                rounded(
                    dc,
                    editor_card(hwnd, state.text_mode.get()),
                    logical_px(hwnd, 24),
                    Some(EDITOR_CARD),
                    0x4b4848,
                );
                EndPaint(hwnd, &paint);
                0
            }
            WM_CTLCOLORDLG => state.surface as LRESULT,
            WM_CTLCOLORSTATIC | WM_CTLCOLORBTN | WM_CTLCOLOREDIT | WM_CTLCOLORLISTBOX => {
                let dc = wp as HDC;
                let field = matches!(msg, WM_CTLCOLOREDIT | WM_CTLCOLORLISTBOX);
                let card = !state.trim
                    && matches!(
                        GetDlgCtrlID(lp as HWND) as u16,
                        INFO | GAIN_VALUE | 302..=305
                    );
                SetTextColor(dc, PRIMARY);
                SetBkMode(dc, if field { OPAQUE } else { TRANSPARENT } as i32);
                SetBkColor(
                    dc,
                    if field {
                        ELEVATED
                    } else if card {
                        EDITOR_CARD
                    } else {
                        PANEL_SURFACE
                    },
                );
                if field {
                    state.elevated as LRESULT
                } else if card {
                    state.card as LRESULT
                } else {
                    state.surface as LRESULT
                }
            }
            WM_DRAWITEM if lp != 0 => {
                let item = &*(lp as *const DRAWITEMSTRUCT);
                if item.CtlType != ODT_BUTTON {
                    return DefSubclassProc(hwnd, msg, wp, lp);
                }
                draw_button(item, state);
                1
            }
            WM_HSCROLL if lp != 0 && lp as HWND == state.gain => {
                let value = SendMessageW(state.gain, TBM_GETPOS, 0, 0);
                set_control_text(hwnd, GAIN, &value.to_string());
                set_control_text(hwnd, GAIN_VALUE, &format!("{value}%"));
                if !state.trim && wp as u16 as u32 == TB_ENDTRACK {
                    PostMessageW(
                        hwnd,
                        WM_COMMAND,
                        usize::from(GAIN) | ((EN_KILLFOCUS as usize) << 16),
                        state.gain as LPARAM,
                    );
                }
                0
            }
            WM_COMMAND if state.trim && wp as u16 == PRECISE => {
                let precise = !state.precise.get();
                state.precise.set(precise);
                precise_controls(hwnd, precise);
                set_control_text(
                    hwnd,
                    PRECISE,
                    if precise {
                        "▾ 精确调整"
                    } else {
                        "▸ 精确调整"
                    },
                );
                1
            }
            WM_COMMAND if state.trim && state.previewing.get() && wp == usize::from(PLAY) => {
                PostMessageW(hwnd, WM_COMMAND, usize::from(STOP), 0);
                1
            }
            WM_COMMAND
                if !state.trim && wp as u16 == WHEEL && (wp >> 16) as u32 == CBN_KILLFOCUS =>
            {
                let combo = GetDlgItem(hwnd, i32::from(WHEEL));
                let name = window_text(combo);
                let selected = SendMessageW(combo, CB_GETCURSEL, 0, 0);
                // An untouched default title is an empty persisted name.
                let current = window_text(GetDlgItem(hwnd, i32::from(NAME)));
                let name = if current.is_empty()
                    && selected >= 0
                    && name == format!("轮盘 {}", selected + 1)
                {
                    ""
                } else {
                    &name
                };
                if name != current {
                    set_control_text(hwnd, NAME, name);
                    PostMessageW(
                        hwnd,
                        WM_COMMAND,
                        usize::from(NAME) | ((EN_KILLFOCUS as usize) << 16),
                        0,
                    );
                }
                1
            }
            _ => DefSubclassProc(hwnd, msg, wp, lp),
        }
    }
}

fn draw_button(item: &DRAWITEMSTRUCT, state: &ChromeState) {
    let id = item.CtlID as u16;
    let selected = (matches!(id, AUDIO_MODE) && !state.text_mode.get())
        || (matches!(id, TEXT_MODE) && state.text_mode.get());
    let disabled = item.itemState & ODS_DISABLED != 0;
    let pressing = item.itemState & ODS_SELECTED != 0;
    let focus = item.itemState & ODS_FOCUS != 0;
    let (background, border) = if selected || state.trim && id == SAVE {
        (0x673c1c, ACCENT)
    } else if pressing {
        (0x494646, SEPARATOR)
    } else if id == PRECISE {
        (PANEL_SURFACE, PANEL_SURFACE)
    } else {
        (ELEVATED, SEPARATOR)
    };
    fill(
        item.hDC,
        item.rcItem,
        if !state.trim
            && matches!(
                id,
                AUDIO_MODE | TEXT_MODE | IMPORT | RECORD | CANCEL_RECORDING | PLAY | CROP | CLEAR
            )
        {
            EDITOR_CARD
        } else {
            PANEL_SURFACE
        },
    );
    rounded(
        item.hDC,
        item.rcItem,
        12,
        Some(background),
        if focus { ACCENT } else { border },
    );
    let text = wide(&window_text(item.hwndItem));
    let mut area = item.rcItem;
    unsafe {
        SetBkMode(item.hDC, TRANSPARENT as i32);
        SetTextColor(
            item.hDC,
            if disabled {
                MUTED
            } else if matches!(id, REMOVE | CLEAR) {
                ERROR
            } else {
                PRIMARY
            },
        );
        DrawTextW(
            item.hDC,
            text.as_ptr(),
            -1,
            &mut area,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
        );
    }
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
    embedded: bool,
}

impl Preview {
    fn new(child: HWND, embedded: bool) -> io::Result<Self> {
        let renderer = WheelRenderer::new().map_err(io::Error::other)?;
        let mut state = Box::new(PreviewState {
            bitmap: None,
            selected: None,
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
        let bitmap = self
            .renderer
            .render(frame, scale)
            .map_err(io::Error::other)?;
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
                if state.embedded {
                    paint_embedded_backdrop(hwnd, dc);
                } else {
                    fill(
                        dc,
                        RECT {
                            left: 0,
                            top: 0,
                            right: width,
                            bottom: height,
                        },
                        PANEL_SURFACE,
                    );
                }
                if let Some(bitmap) = &state.bitmap {
                    blend_preview(dc, bitmap, width, height);
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

fn dimensions(hwnd: HWND) -> (i32, i32) {
    let mut rect = RECT::default();
    unsafe { GetClientRect(hwnd, &mut rect) };
    (rect.right, rect.bottom)
}

pub struct Editor {
    preview: Preview,
    chrome: Chrome,
    pub panel: Panel,
    pub wheel: usize,
    pub slot: usize,
    pub empty_text_mode: bool,
}

impl Editor {
    pub fn new_embedded(host: HWND, parent: HWND, settings: &Settings) -> io::Result<Self> {
        let mut template = Template::embedded(500, 264);
        label(&mut template, 300, "轮盘", [4, 11, 27, 14]);
        editable_combo(&mut template, WHEEL, [31, 7, 108, 100]);
        label(&mut template, 301, "快捷键", [144, 11, 28, 14]);
        button(&mut template, HOTKEY, "录制快捷键", [172, 7, 80, 18]);
        button(&mut template, ADD, "新增", [256, 7, 34, 18]);
        button(&mut template, REMOVE, "删除", [294, 7, 34, 18]);
        label(&mut template, 307, "样式", [407, 11, 28, 14]);
        combo(&mut template, STYLE, [435, 7, 63, 100]);
        label(&mut template, PICK, "", [3, 33, 198, 212]);
        edit(&mut template, NAME, [0, 0, 1, 1]);
        label(&mut template, INFO, "", [217, 42, 252, 16]);
        label(&mut template, 302, "内容", [217, 64, 26, 15]);
        button(&mut template, AUDIO_MODE, "音频", [245, 60, 34, 18]);
        button(&mut template, TEXT_MODE, "文字", [283, 60, 34, 18]);
        label(&mut template, 304, "音量", [217, 88, 26, 15]);
        edit(&mut template, GAIN, [245, 83, 112, 18]);
        label(&mut template, GAIN_VALUE, "100%", [362, 87, 41, 15]);
        label(&mut template, 305, "名称", [217, 108, 52, 15]);
        edit(&mut template, LABEL, [217, 125, 272, 18]);
        label(&mut template, 303, "朗读文字", [217, 150, 60, 15]);
        edit(&mut template, TEXT, [217, 167, 272, 18]);
        button(&mut template, IMPORT, "导入", [217, 152, 33, 18]);
        button(&mut template, RECORD, "录制", [253, 152, 46, 18]);
        button(&mut template, CANCEL_RECORDING, "取消", [302, 152, 37, 18]);
        button(&mut template, PLAY, "试听", [342, 152, 42, 18]);
        button(&mut template, CROP, "截取", [388, 152, 33, 18]);
        button(&mut template, CLEAR, "清空", [425, 152, 33, 18]);
        let panel = Panel::new(parent, &template, host, EDITOR_MESSAGE)?;
        let chrome = Chrome::attach(&panel, false, false)?;
        layout_editor(panel.0, &chrome.state);
        let preview = Preview::new(panel.child(PICK), true)?;
        let mut editor = Self {
            preview,
            chrome,
            panel,
            wheel: settings.chat_wheel_last_index,
            slot: 0,
            empty_text_mode: false,
        };
        for (id, limit) in [(NAME, 20), (LABEL, 40), (TEXT, 200), (GAIN, 8)] {
            unsafe { SendMessageW(editor.panel.child(id), EM_SETLIMITTEXT, limit, 0) };
        }
        unsafe { ShowWindow(editor.panel.child(NAME), SW_HIDE) };
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
        self.panel
            .set_text(HOTKEY, &crate::hotkey::display_hotkey(&wheel.hotkey));
        self.panel.set_text(LABEL, &wheel.labels[self.slot]);
        self.panel.set_text(TEXT, &wheel.texts[self.slot]);
        self.chrome.gain(wheel.volumes[self.slot]);
        let text_mode = !wheel.texts[self.slot].is_empty()
            || (wheel.slots[self.slot].is_empty() && self.empty_text_mode);
        self.chrome.state.text_mode.set(text_mode);
        let recording = self.chrome.state.recording.get();
        unsafe { InvalidateRect(self.panel.0, ptr::null(), 1) };
        for (id, visible) in [
            (TEXT, text_mode),
            (303, text_mode),
            (GAIN, !text_mode && self.chrome.state.gain.is_null()),
            (GAIN_VALUE, !text_mode && !self.chrome.state.gain.is_null()),
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
        let has_audio = !wheel.slots[self.slot].is_empty();
        let has_text = !wheel.texts[self.slot].is_empty();
        unsafe {
            ShowWindow(
                self.chrome.state.gain,
                if text_mode { SW_HIDE } else { SW_SHOW },
            );
            EnableWindow(self.chrome.state.gain, i32::from(has_audio && !recording));
            EnableWindow(
                self.panel.child(REMOVE),
                i32::from(settings.wheel_count() > 1 && !recording),
            );
            EnableWindow(
                self.panel.child(PLAY),
                i32::from((has_audio || has_text) && !recording),
            );
            EnableWindow(self.panel.child(CROP), i32::from(has_audio && !recording));
            EnableWindow(
                self.panel.child(CLEAR),
                i32::from(
                    (has_audio || has_text || !wheel.labels[self.slot].is_empty()) && !recording,
                ),
            );
            for id in [AUDIO_MODE, TEXT_MODE] {
                InvalidateRect(self.panel.child(id), ptr::null(), 0);
            }
        }
        layout_editor(self.panel.0, &self.chrome.state);
        self.panel
            .set_text(INFO, &format!("音频 {}", self.slot + 1));
        self.preview.draw(
            &wheel_labels(&wheel),
            WheelStyle::from_config(&settings.chat_wheel_style),
            self.slot,
        )
    }

    pub fn recording(&self, active: bool) {
        self.chrome.state.recording.set(active);
        self.panel
            .set_text(RECORD, if active { "停止" } else { "录制" });
        unsafe {
            ShowWindow(
                self.panel.child(CANCEL_RECORDING),
                if active { SW_SHOW } else { SW_HIDE },
            );
            for id in [
                WHEEL, PICK, ADD, REMOVE, HOTKEY, STYLE, LABEL, TEXT, AUDIO_MODE, TEXT_MODE,
                IMPORT, PLAY, CROP, CLEAR,
            ] {
                EnableWindow(self.panel.child(id), i32::from(!active));
            }
            EnableWindow(self.chrome.state.gain, i32::from(!active));
        };
        layout_editor(self.panel.0, &self.chrome.state);
    }
}

fn logical_px(hwnd: HWND, units: i32) -> i32 {
    // The embedded page uses the settings shell's coordinate space; only dialogs scale their own controls.
    if unsafe { GetWindowLongPtrW(hwnd, GWL_STYLE) } as u32 & WS_CHILD != 0 {
        return units;
    }
    let dpi = unsafe { GetDpiForWindow(hwnd) }.max(96) as i32;
    (units * dpi + 48) / 96
}

fn place(control: HWND, x: i32, y: i32, width: i32, height: i32) {
    unsafe {
        SetWindowPos(
            control,
            ptr::null_mut(),
            x,
            y,
            width.max(1),
            height,
            SWP_NOZORDER | SWP_NOACTIVATE,
        );
    }
}

fn editor_card(hwnd: HWND, text_mode: bool) -> RECT {
    RECT {
        left: logical_px(hwnd, 424),
        top: logical_px(hwnd, 44),
        right: dimensions(hwnd).0,
        bottom: logical_px(hwnd, if text_mode { 290 } else { 273 }),
    }
}

fn layout_editor(hwnd: HWND, state: &ChromeState) {
    let width = dimensions(hwnd).0;
    let px = |value| logical_px(hwnd, value);
    let at = |id, x, y, w, h| place(unsafe { GetDlgItem(hwnd, id) }, x, px(y), w, px(h));
    let card = editor_card(hwnd, state.text_mode.get());
    let left = card.left + px(18);
    let field = card.right - left - px(18);
    for (id, x, y, w, h) in [
        (300, 0, 7, 34, 20),
        (WHEEL, 35, 0, 220, 200),
        (301, 263, 7, 47, 20),
        (HOTKEY, 310, 0, 160, 32),
        (ADD, 478, 0, 52, 32),
        (REMOVE, 538, 0, 52, 32),
        (PICK, 0, 44, 400, 400),
    ] {
        at(i32::from(id), px(x), y, px(w), h);
    }
    at(307, width - px(172), 7, px(36), 20);
    at(i32::from(STYLE), width - px(136), 0, px(136), 200);
    for (id, x, y, w, h) in [
        (INFO, 0, 60, 180, 23),
        (302, 0, 96, 34, 20),
        (AUDIO_MODE, 35, 86, 42, 32),
        (TEXT_MODE, 85, 86, 42, 32),
        (304, 0, 126, 34, 20),
        (GAIN_VALUE, 268, 126, 50, 20),
        (
            305,
            0,
            if state.text_mode.get() { 123 } else { 152 },
            50,
            20,
        ),
        (303, 0, 184, 140, 20),
    ] {
        at(i32::from(id), left + px(x), y, px(w), h);
    }
    let gain = if state.gain.is_null() {
        unsafe { GetDlgItem(hwnd, i32::from(GAIN)) }
    } else {
        state.gain
    };
    place(gain, left + px(35), px(118), px(220), px(30));
    at(
        i32::from(LABEL),
        left,
        if state.text_mode.get() { 145 } else { 174 },
        field,
        32,
    );
    at(i32::from(TEXT), left, 205, field, 32);
    let mode = state.text_mode.get();
    let active = state.recording.get();
    let action_y = if mode { 244 } else { 222 };
    let (mut x, gap) = (left, px(6));
    for (id, control_width, visible) in [
        (IMPORT, 42, !mode),
        (RECORD, 60, !mode),
        (CANCEL_RECORDING, 48, active && !mode),
        (PLAY, 64, true),
        (CROP, 42, !mode),
        (CLEAR, 42, true),
    ] {
        if visible {
            at(i32::from(id), x, action_y, px(control_width), 32);
            x += px(control_width) + gap;
        }
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
        let mut template = Template::with_size("截取录音", 440, if rolling { 386 } else { 205 });
        label(
            &mut template,
            300,
            "拖动两端手柄调整范围，或在波形上拖选；空格试听 / 停止。",
            [12, 13, 414, 12],
        );
        template.control(0x82, WAVE, "", BASE | TABS, [6, 28, 428, 90]);
        label(&mut template, LOADING, "正在加载波形…", [12, 119, 416, 10]);
        label(&mut template, RANGE_SUMMARY, "", [12, 133, 415, 12]);
        button(&mut template, PRECISE, "▸ 精确调整", [8, 147, 61, 15]);
        label(&mut template, START_LABEL, "开始（秒）", [12, 168, 55, 16]);
        edit(&mut template, START, [69, 165, 73, 16]);
        label(&mut template, END_LABEL, "结束（秒）", [155, 168, 55, 16]);
        edit(&mut template, END, [212, 165, 73, 16]);
        let action_y = if rolling { 362 } else { 183 };
        button(&mut template, PLAY, "试听片段", [12, action_y, 72, 17]);
        if rolling {
            label(&mut template, 303, "保存到", [12, 190, 32, 14]);
            combo(&mut template, WHEEL, [47, 186, 111, 80]);
            button(&mut template, ADD, "新增轮盘", [164, 186, 60, 18]);
            label(&mut template, 304, "名称", [12, 212, 32, 14]);
            edit(&mut template, LABEL, [47, 208, 381, 17]);
            label(&mut template, 305, "音量", [12, 234, 32, 14]);
            edit(&mut template, GAIN, [47, 229, 333, 17]);
            label(&mut template, GAIN_VALUE, "100%", [387, 233, 41, 14]);
            label(&mut template, PICK, "", [145, 250, 150, 90]);
            label(&mut template, INFO, "", [12, 342, 416, 14]);
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
        if let Some(preview) = &mut self.preview {
            let wheel = settings.wheel(self.wheel).expect("wheel index in range");
            choices(&self.panel, WHEEL, wheel_names(settings), self.wheel);
            let detail = if !wheel.texts[self.slot].trim().is_empty() {
                let text = &wheel.texts[self.slot];
                let preview: String = text.chars().take(38).collect();
                let ellipsis = if text.chars().count() > 38 { "…" } else { "" };
                format!("原有文字：{preview}{ellipsis}（保存将替换）")
            } else if !wheel.slots[self.slot].is_empty() {
                let label = if wheel.labels[self.slot].is_empty() {
                    &wheel.slots[self.slot]
                } else {
                    &wheel.labels[self.slot]
                };
                format!("原有音频：{label}（保存将替换）")
            } else {
                "空槽".into()
            };
            self.panel
                .set_text(INFO, &format!("音频 {} · {detail}", self.slot + 1));
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
        self.wave.playhead = seconds.filter(|value| value.is_finite()).map(|value| {
            (self.wave.range.start + value).clamp(self.wave.range.start, self.wave.range.end)
        });
        self.invalidate();
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
        if !active {
            self.wave.playhead = None;
            self.invalidate();
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
                let mut paint = PAINTSTRUCT::default();
                let dc = BeginPaint(hwnd, &mut paint);
                paint_wave(dc, state, plot, width, height, GetFocus() == hwnd);
                EndPaint(hwnd, &paint);
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

fn fill(dc: HDC, rect: RECT, color: u32) {
    unsafe {
        let brush = CreateSolidBrush(color);
        FillRect(dc, &rect, brush);
        DeleteObject(brush);
    }
}

fn with_pen(dc: HDC, width: i32, color: u32, draw: impl FnOnce()) {
    unsafe {
        let pen = CreatePen(PS_SOLID, width, color);
        let old = SelectObject(dc, pen);
        draw();
        SelectObject(dc, old);
        DeleteObject(pen);
    }
}

fn rounded(dc: HDC, rect: RECT, radius: i32, background: Option<u32>, border: u32) {
    unsafe {
        let brush = background.map_or_else(
            || GetStockObject(NULL_BRUSH),
            |color| CreateSolidBrush(color),
        );
        let old = SelectObject(dc, brush);
        with_pen(dc, 1, border, || {
            RoundRect(
                dc,
                rect.left,
                rect.top,
                rect.right,
                rect.bottom,
                radius,
                radius,
            );
        });
        SelectObject(dc, old);
        if background.is_some() {
            DeleteObject(brush);
        }
    }
}

fn line(dc: HDC, x: i32, y: i32, x2: i32, y2: i32) {
    unsafe {
        MoveToEx(dc, x, y, ptr::null_mut());
        LineTo(dc, x2, y2);
    }
}

fn paint_wave(dc: HDC, state: &WaveState, plot: Plot, width: i32, height: i32, focused: bool) {
    let bounds = plot.bounds;
    let middle = (bounds.top + bounds.bottom) / 2;
    let duration = state.range.duration;
    let left = plot.x_at(state.range.start, duration);
    let right = plot.x_at(state.range.end, duration);
    fill(
        dc,
        RECT {
            left: 0,
            top: 0,
            right: width,
            bottom: height,
        },
        PANEL_SURFACE,
    );
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
        SelectClipRgn(dc, clip);
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
        let at = (plot.x_at(time, duration) - 35).clamp(0, (width - 70).max(0));
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
