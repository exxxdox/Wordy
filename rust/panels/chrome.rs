use super::*;
use crate::desktop::paint_round_field;

pub(crate) struct Chrome {
    hwnd: HWND,
    pub(super) state: Box<ChromeState>,
}

pub(crate) struct ChromeState {
    surface: HBRUSH,
    elevated: HBRUSH,
    card: HBRUSH,
    pub(super) gain: HWND,
    trim: bool,
    precise: Cell<bool>,
    pub(super) previewing: Cell<bool>,
    pub(super) text_mode: Cell<bool>,
    ui_font: RefCell<String>,
}

impl Chrome {
    pub(super) fn set_ui_font(&self, font: &str) {
        self.state.ui_font.replace(font.to_owned());
    }

    pub(super) fn attach(panel: &Panel, trim: bool, rolling: bool) -> io::Result<Self> {
        let mut state = Box::new(ChromeState {
            surface: unsafe { CreateSolidBrush(PANEL_SURFACE) },
            elevated: unsafe { CreateSolidBrush(ELEVATED) },
            card: unsafe { CreateSolidBrush(EDITOR_CARD) },
            gain: ptr::null_mut(),
            trim,
            precise: Cell::new(false),
            previewing: Cell::new(false),
            text_mode: Cell::new(false),
            ui_font: RefCell::new(String::new()),
        });
        if !trim {
            install_editor_tooltips(panel);
        }
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
                    GAIN_SLIDER as isize as HMENU,
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
                // A dialog redraw must leave the wave and other child surfaces intact.
                let style = GetWindowLongPtrW(panel.0, GWL_STYLE);
                SetWindowLongPtrW(panel.0, GWL_STYLE, style | WS_CLIPCHILDREN as isize);
                // Match the dark client while retaining the system frame and shadow.
                for (attribute, value) in [
                    (DWMWA_USE_IMMERSIVE_DARK_MODE, 1),
                    (DWMWA_CAPTION_COLOR, PANEL_SURFACE),
                    (DWMWA_WINDOW_CORNER_PREFERENCE, DWMWCP_ROUND as u32),
                ] {
                    DwmSetWindowAttribute(
                        panel.0,
                        attribute as u32,
                        ptr::from_ref(&value).cast(),
                        mem::size_of_val(&value) as u32,
                    );
                }
                precise_controls(panel.0, false);
                if !rolling {
                    ShowWindow(panel.child(LABEL), SW_HIDE);
                }
            }
        }
        // Style the selectors as rounded dark fields instead of system chrome.
        let item_height = logical_px(panel.0, 28);
        for id in [WHEEL, STYLE] {
            let combo = panel.child(id);
            if !combo.is_null() {
                unsafe {
                    SendMessageW(combo, CB_SETITEMHEIGHT, usize::MAX, item_height as isize);
                    SendMessageW(combo, CB_SETITEMHEIGHT, 0, item_height as isize);
                }
                style_selector(combo, !trim);
            }
        }
        if trim || !state.gain.is_null() {
            unsafe { ShowWindow(panel.child(GAIN), SW_HIDE) };
            if !state.gain.is_null() {
                let background = if trim { PANEL_SURFACE } else { EDITOR_CARD };
                unsafe {
                    SetWindowSubclass(
                        state.gain,
                        Some(slider_proc),
                        GAIN_SUBCLASS,
                        background as usize,
                    )
                };
            }
        }
        // Single-line edits draw their text at the top; center it in the field.
        // Rounded corners match the card and the dropdowns instead of the flat
        // framed rectangle the panels used before.
        let card = if trim { PANEL_SURFACE } else { EDITOR_CARD };
        for id in [GAIN, NAME, LABEL, TEXT, START, END] {
            let child = panel.child(id);
            if !child.is_null() {
                style_card_edit(child, ELEVATED, card);
                let margin = logical_px(panel.0, 10);
                unsafe {
                    SendMessageW(
                        child,
                        EM_SETMARGINS,
                        (EC_LEFTMARGIN | EC_RIGHTMARGIN) as usize,
                        (margin | (margin << 16)) as isize,
                    )
                };
            }
        }
        // Ownerdraw buttons paint their full face, so the class-brush erase only flashes.
        suppress_button_erase(panel.0);
        Ok(Self {
            hwnd: panel.0,
            state,
        })
    }

    pub(super) fn gain(&self, gain: f64) {
        let percent = (gain * 100.0).round() as i32;
        set_gain_readout(self.hwnd, percent);
        slider::set_position(self.state.gain, percent);
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

const EDIT_CORNER: windows_sys::core::PCWSTR = windows_sys::core::w!("Wordy.EditCorner");
const EDIT_SUBCLASS: usize = 12;
// Same focus border as the settings edits, so every text field reacts alike.
const FOCUS_ACCENT: u32 = 0x00ff_9c40;

/// Centres the single-line text and rounds the field: the corners outside the
/// border show the card behind the edit instead of the edit's flat fill.
pub(crate) fn style_card_edit(hwnd: HWND, fill_color: u32, corner: u32) {
    unsafe {
        SetPropW(hwnd, EDIT_CORNER, corner as usize as _);
        SetWindowSubclass(hwnd, Some(card_edit_proc), EDIT_SUBCLASS, 0);
    }
    center_edit_text(hwnd, fill_color);
}

unsafe extern "system" fn card_edit_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    _data: usize,
) -> LRESULT {
    unsafe {
        let result = DefSubclassProc(hwnd, message, wparam, lparam);
        if message == WM_NCDESTROY {
            RemovePropW(hwnd, EDIT_CORNER);
        }
        // The focus accent lives in both the client and frame bands.
        if matches!(message, WM_SETFOCUS | WM_KILLFOCUS) {
            RedrawWindow(
                hwnd,
                ptr::null(),
                ptr::null_mut(),
                RDW_INVALIDATE | RDW_FRAME,
            );
        }
        // Each paint DC is clipped to its own update region (client or frame),
        // so the border is drawn from both passes to cover all four edges.
        if !matches!(message, WM_PAINT | WM_NCPAINT) {
            return result;
        }
        let corner = GetPropW(hwnd, EDIT_CORNER) as u32;
        let dc = GetWindowDC(hwnd);
        if dc.is_null() || corner == 0 {
            return result;
        }
        let mut window = RECT::default();
        GetWindowRect(hwnd, &mut window);
        let rect = RECT {
            left: 0,
            top: 0,
            right: window.right - window.left,
            bottom: window.bottom - window.top,
        };
        // GDI regions and `RoundRect` leave the corner arcs stair-stepped; blend
        // them against the card instead.
        let border = if GetFocus() == hwnd {
            FOCUS_ACCENT
        } else {
            SEPARATOR
        };
        paint_round_field(dc, rect, 9, border, |memory| fill(memory, rect, corner));
        ReleaseDC(hwnd, dc);
        result
    }
}

fn set_control_text(hwnd: HWND, id: u16, value: &str) {
    let text = wide(value);
    unsafe { SetDlgItemTextW(hwnd, i32::from(id), text.as_ptr()) };
}

fn set_gain_readout(hwnd: HWND, value: i32) {
    let percent = value.to_string();
    let edit = unsafe { GetDlgItem(hwnd, i32::from(GAIN)) };
    // A drag's final notification repeats the last value and needs no visual update.
    if window_text(edit) != percent {
        set_control_text(hwnd, GAIN, &percent);
        set_control_text(hwnd, GAIN_VALUE, &format!("{percent}%"));
    }
}

fn precise_controls(hwnd: HWND, visible: bool) {
    for id in [START_LABEL, END_LABEL, START, END] {
        unsafe {
            ShowWindow(
                GetDlgItem(hwnd, i32::from(id)),
                if visible { SW_SHOW } else { SW_HIDE },
            )
        };
    }
}

fn suppress_button_erase(panel: HWND) {
    unsafe {
        let mut child = GetWindow(panel, GW_CHILD);
        while !child.is_null() {
            let style = GetWindowLongPtrW(child, GWL_STYLE) as u32;
            if style & BS_TYPEMASK as u32 == BS_OWNERDRAW as u32 {
                SetWindowSubclass(child, Some(no_erase_proc), 8, 0);
            }
            child = GetWindow(child, GW_HWNDNEXT);
        }
    }
}

fn paint_gain(hwnd: HWND, dc: HDC, rect: RECT, background: u32) {
    fill(dc, rect, background);
    let (channel, x) = slider::thumb_geometry(hwnd);
    let enabled = unsafe { IsWindowEnabled(hwnd) != 0 };
    let y = (rect.top + rect.bottom) / 2;
    let track = RECT {
        left: channel.left,
        top: y - 3,
        right: channel.right,
        bottom: y + 3,
    };
    let progress = RECT {
        right: x.max(channel.left + 1),
        ..track
    };
    rounded(dc, track, 6, Some(SEPARATOR), SEPARATOR);
    if enabled {
        rounded(dc, progress, 6, Some(ACCENT), ACCENT);
    }
    let thumb = RECT {
        left: x - 8,
        top: y - 8,
        right: x + 8,
        bottom: y + 8,
    };
    let thumb_color = if enabled { PRIMARY } else { MUTED };
    let ring = if enabled && unsafe { GetFocus() } == hwnd {
        ACCENT
    } else {
        thumb_color
    };
    rounded(dc, thumb, 16, Some(thumb_color), ring);
}

unsafe extern "system" fn slider_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    background: usize,
) -> LRESULT {
    if let Some(result) = slider::handle_pointer(hwnd, message, lparam) {
        return result;
    }
    unsafe {
        match message {
            WM_ERASEBKGND => 1,
            WM_PAINT => {
                paint_buffered(hwnd, |dc, rect| {
                    paint_gain(hwnd, dc, rect, background as u32)
                });
                0
            }
            WM_SETFOCUS | WM_KILLFOCUS | WM_KEYDOWN | WM_MOUSEWHEEL | WM_ENABLE => {
                let result = DefSubclassProc(hwnd, message, wparam, lparam);
                InvalidateRect(hwnd, ptr::null(), 0);
                result
            }
            _ => DefSubclassProc(hwnd, message, wparam, lparam),
        }
    }
}

unsafe extern "system" fn no_erase_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
    _id: usize,
    _data: usize,
) -> LRESULT {
    if message == WM_ERASEBKGND {
        return 1;
    }
    if message == WM_MOUSEMOVE {
        // Ownerdraw buttons have no hover state of their own; repaint on entry
        // and exit so the hover fill shows.
        let mut track = TRACKMOUSEEVENT {
            cbSize: mem::size_of::<TRACKMOUSEEVENT>() as u32,
            dwFlags: TME_LEAVE,
            hwndTrack: hwnd,
            dwHoverTime: 0,
        };
        unsafe {
            TrackMouseEvent(&mut track);
            InvalidateRect(hwnd, ptr::null(), 0);
        }
    } else if message == WM_MOUSELEAVE {
        unsafe { InvalidateRect(hwnd, ptr::null(), 0) };
    }
    unsafe { DefSubclassProc(hwnd, message, wparam, lparam) }
}

fn hovering(hwnd: HWND) -> bool {
    let mut rect = RECT::default();
    let mut cursor = POINT::default();
    unsafe {
        GetWindowRect(hwnd, &mut rect);
        GetCursorPos(&mut cursor);
    }
    (rect.left..rect.right).contains(&cursor.x) && (rect.top..rect.bottom).contains(&cursor.y)
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
                let control = GetDlgCtrlID(lp as HWND) as u16;
                let field = matches!(msg, WM_CTLCOLOREDIT | WM_CTLCOLORLISTBOX)
                    || matches!(control, WHEEL | STYLE);
                // The gain readout supplies its own buffered background in WM_DRAWITEM.
                let card = !state.trim && matches!(control, INFO | GAIN_SLIDER | 302..=305);
                // Values read bright; captions stay muted. Captions sit straight
                // on the glass, so they must not erase a solid surface rectangle.
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
                    GetStockObject(NULL_BRUSH) as LRESULT
                }
            }
            WM_DRAWITEM if lp != 0 => {
                let item = &*(lp as *const DRAWITEMSTRUCT);
                match item.CtlType {
                    ODT_BUTTON => draw_button(item, state),
                    ODT_STATIC if item.CtlID == u32::from(GAIN_VALUE) => {
                        paint_buffered_rect(item.hDC, item.rcItem, |dc, rect| {
                            draw_label(item.hwndItem, dc, rect, state.trim);
                        });
                    }
                    ODT_STATIC => draw_label(item.hwndItem, item.hDC, item.rcItem, state.trim),
                    ODT_COMBOBOX if item.itemState & ODS_COMBOBOXEDIT == 0 => draw_combo_item(item),
                    // The request's DC is clipped inside the field; paint the
                    // face now through the combo's own client DC so a pending
                    // repaint never leaves the field empty.
                    ODT_COMBOBOX => crate::selector::paint_face(item.hwndItem, !state.trim),
                    _ => return DefSubclassProc(hwnd, msg, wp, lp),
                }
                1
            }
            WM_HSCROLL if lp != 0 && lp as HWND == state.gain => {
                let value = SendMessageW(state.gain, TBM_GETPOS, 0, 0) as i32;
                set_gain_readout(hwnd, value);
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
            // The selector's border follows the dropped state; these are the
            // only notifications for the open and close transitions.
            WM_COMMAND
                if matches!(
                    ((wp >> 16) & 0xffff) as u32,
                    windows_sys::Win32::UI::WindowsAndMessaging::CBN_DROPDOWN
                        | windows_sys::Win32::UI::WindowsAndMessaging::CBN_CLOSEUP
                ) =>
            {
                InvalidateRect(lp as HWND, ptr::null(), 0);
                DefSubclassProc(hwnd, msg, wp, lp)
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
            WM_COMMAND if !state.trim && wp as u16 == RENAME => {
                let current = window_text(GetDlgItem(hwnd, i32::from(NAME)));
                let ui_font = state.ui_font.borrow().clone();
                if let Some(name) =
                    crate::dialog::prompt(hwnd, "重命名轮盘", "轮盘名称", &current, false, &ui_font)
                {
                    let name = name.trim().to_owned();
                    if name != current {
                        // Reuse the NAME edit's commit path so the app owns persistence.
                        set_control_text(hwnd, NAME, &name);
                        PostMessageW(
                            hwnd,
                            WM_COMMAND,
                            usize::from(NAME) | ((EN_KILLFOCUS as usize) << 16),
                            0,
                        );
                    }
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
    let hovering = !disabled && hovering(item.hwndItem);
    let danger = matches!(id, REMOVE | CLEAR);
    let (background, border) = if selected || state.trim && id == SAVE {
        (
            if hovering { 0x7b4a26 } else { 0x673c1c },
            if focus { PRIMARY } else { ACCENT },
        )
    } else if pressing {
        (0x242426, SEPARATOR)
    } else if id == PRECISE {
        // Keep the collapsed control quiet while retaining visible mouse and keyboard feedback.
        (
            if hovering { ELEVATED } else { PANEL_SURFACE },
            if focus { ACCENT } else { PANEL_SURFACE },
        )
    } else if hovering {
        (
            if danger { 0x32262a } else { 0x3a3a3e },
            if focus { ACCENT } else { 0x4a4a4e },
        )
    } else {
        (ELEVATED, if focus { ACCENT } else { SEPARATOR })
    };
    let card_button =
        !state.trim && matches!(id, AUDIO_MODE | TEXT_MODE | IMPORT | PLAY | CROP | CLEAR);
    // Toolbar buttons sit on the glass, so their rounded corners must show the
    // glass; a solid surface fill would leave dark corner squares.
    let toolbar = !state.trim && matches!(id, HOTKEY | ADD | REMOVE | RENAME);
    if toolbar {
        paint_embedded_backdrop(item.hwndItem, item.hDC);
    } else {
        fill(
            item.hDC,
            item.rcItem,
            if card_button {
                EDITOR_CARD
            } else {
                PANEL_SURFACE
            },
        );
    }
    rounded(item.hDC, item.rcItem, 10, Some(background), border);
    let label = window_text(item.hwndItem);
    let color = if disabled {
        MUTED
    } else if matches!(id, REMOVE | CLEAR) {
        ERROR
    } else {
        PRIMARY
    };
    if !state.trim {
        let ui_font = state.ui_font.borrow().clone();
        if draw_slot_glyph(
            item.hwndItem,
            item.hDC,
            item.rcItem,
            id,
            &label,
            color,
            &ui_font,
        ) {
            return;
        }
    }
    let text = wide(&label);
    let mut area = item.rcItem;
    unsafe {
        // Ownerdraw DCs do not guarantee the control font; select it so the
        // settings UI font applies here too.
        let font = SendMessageW(item.hwndItem, WM_GETFONT, 0, 0) as HFONT;
        let previous = if font.is_null() {
            ptr::null_mut()
        } else {
            SelectObject(item.hDC, font)
        };
        SetBkMode(item.hDC, TRANSPARENT as i32);
        SetTextColor(item.hDC, color);
        DrawTextW(
            item.hDC,
            text.as_ptr(),
            -1,
            &mut area,
            DT_CENTER | DT_VCENTER | DT_SINGLELINE | DT_NOPREFIX,
        );
        if !previous.is_null() {
            SelectObject(item.hDC, previous);
        }
    }
}

fn install_editor_tooltips(panel: &Panel) {
    let tip = unsafe {
        CreateWindowExW(
            WS_EX_TOPMOST,
            TOOLTIPS_CLASSW,
            ptr::null(),
            WS_POPUP | TTS_ALWAYSTIP,
            0,
            0,
            0,
            0,
            panel.0,
            ptr::null_mut(),
            GetModuleHandleW(ptr::null()),
            ptr::null(),
        )
    };
    if tip.is_null() {
        return;
    }
    for (id, text) in [
        (IMPORT, w!("导入音频")),
        (PLAY, w!("试听 / 停止")),
        (CROP, w!("截取片段")),
        (CLEAR, w!("清空当前槽位")),
        (AUDIO_MODE, w!("音频内容")),
        (TEXT_MODE, w!("文字内容")),
        (ADD, w!("新增轮盘")),
        (REMOVE, w!("删除轮盘")),
    ] {
        let child = panel.child(id);
        let mut tool = TTTOOLINFOW {
            cbSize: mem::size_of::<TTTOOLINFOW>() as u32,
            uFlags: TTF_IDISHWND | TTF_SUBCLASS,
            hwnd: panel.0,
            uId: child as usize,
            lpszText: text.cast_mut(),
            ..Default::default()
        };
        unsafe { SendMessageW(tip, TTM_ADDTOOLW, 0, ptr::from_mut(&mut tool) as isize) };
    }
}
