use super::*;

pub(super) unsafe extern "system" fn window_proc(
    hwnd: HWND,
    message: u32,
    wparam: WPARAM,
    lparam: LPARAM,
) -> LRESULT {
    // Store only the owner's HWND. The callback posts actions and never borrows
    // SettingsWindow, including during CreateWindowExW or programmatic refresh.
    if message == WM_NCCREATE {
        let creation = unsafe { &*(lparam as *const CREATESTRUCTW) };
        unsafe { SetWindowLongPtrW(hwnd, GWLP_USERDATA, creation.lpCreateParams as isize) };
    }
    let host = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } as HWND;
    let post = |id: u16| {
        if !host.is_null() {
            unsafe { PostMessageW(host, SETTINGS_ACTION_MESSAGE, id as WPARAM, 0) };
        }
    };
    match message {
        WM_NCCALCSIZE | WM_NCPAINT | WM_ERASEBKGND => return 0,
        WM_NCACTIVATE => return 1,
        WM_NCHITTEST => {
            let x = lparam as i16 as i32;
            let y = (lparam >> 16) as i16 as i32;
            let mut point = POINT { x, y };
            let mut client = RECT::default();
            unsafe {
                ScreenToClient(hwnd, &mut point);
                GetClientRect(hwnd, &mut client);
            }
            return shell_hit(
                point.x,
                point.y,
                client.right,
                client.bottom,
                unsafe { IsZoomed(hwnd) != 0 },
                unsafe { GetDpiForWindow(hwnd) }.max(96) as i32,
            );
        }
        WM_NCLBUTTONDBLCLK if wparam == HTCAPTION as usize => {
            toggle_zoom(hwnd);
            return 0;
        }
        WM_KEYDOWN if wparam as u16 == VK_ESCAPE => {
            close_window(hwnd);
            return 0;
        }
        WM_CLOSE => {
            close_window(hwnd);
            return 0;
        }
        WM_SIZE => post(Field::Resize as u16),
        WM_GETMINMAXINFO => {
            let limits = unsafe { &mut *(lparam as *mut MINMAXINFO) };
            let monitor = unsafe { MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST) };
            let mut info = MONITORINFO {
                cbSize: size_of::<MONITORINFO>() as u32,
                ..Default::default()
            };
            if unsafe { GetMonitorInfoW(monitor, &mut info) } != 0 {
                limits.ptMaxPosition.x = info.rcWork.left - info.rcMonitor.left;
                limits.ptMaxPosition.y = info.rcWork.top - info.rcMonitor.top;
                limits.ptMaxSize.x = info.rcWork.right - info.rcWork.left;
                limits.ptMaxSize.y = info.rcWork.bottom - info.rcWork.top;
            }
            let scale = scale_at_cursor();
            limits.ptMinTrackSize.x = scaled(960, scale);
            // The fixed two-column layout has no scrollbars; keep the full height usable.
            limits.ptMinTrackSize.y = scaled(820, scale);
        }
        WM_PAINT => {
            paint_shell(hwnd);
            return 0;
        }
        WM_CTLCOLORSTATIC | WM_CTLCOLOREDIT | WM_CTLCOLORLISTBOX if wparam != 0 => {
            let dc = wparam as HDC;
            let kind = unsafe { GetPropW(lparam as HWND, CONTROL_KIND) } as usize;
            let color = if kind == Kind::Muted as usize + 1 {
                MUTED
            } else {
                PRIMARY
            };
            unsafe {
                SetTextColor(dc, color);
                if message == WM_CTLCOLORSTATIC {
                    SetBkMode(dc, TRANSPARENT as i32);
                    return GetStockObject(NULL_BRUSH) as LRESULT;
                }
                SetBkColor(dc, CONTROL_BG);
                SetDCBrushColor(dc, CONTROL_BG);
                return GetStockObject(DC_BRUSH) as LRESULT;
            }
        }
        WM_DRAWITEM if lparam != 0 => {
            let item = unsafe { &*(lparam as *const DRAWITEMSTRUCT) };
            paint_item(hwnd, item);
            return 1;
        }
        WM_COMMAND if lparam != 0 => {
            use windows_sys::Win32::UI::WindowsAndMessaging::{CBN_CLOSEUP, CBN_DROPDOWN};
            let id = wparam as u16;
            let notification = ((wparam >> 16) & 0xffff) as u32;
            if matches!(notification, CBN_DROPDOWN | CBN_CLOSEUP) {
                // The selector's border follows the dropped state, and these are
                // the only notifications for the open/close transitions.
                unsafe { InvalidateRect(lparam as HWND, ptr::null(), 0) };
            }
            if notification == BN_CLICKED
                && (Field::TabLocal as u16..=Field::TabRolling as u16).contains(&id)
            {
                // The page swap runs after the posted action; paint the selected
                // tab now so the click has immediate feedback.
                unsafe { SetPropW(hwnd, SELECTED_TAB, id as usize as _) };
                for tab in Field::TabLocal as u16..=Field::TabRolling as u16 {
                    unsafe { InvalidateRect(GetDlgItem(hwnd, i32::from(tab)), ptr::null(), 0) };
                }
            }
            if [BN_CLICKED, CBN_SELCHANGE, EN_KILLFOCUS].contains(&notification) {
                post(id);
                return 0;
            }
        }
        WM_HSCROLL if lparam != 0 => {
            let field = unsafe { GetDlgCtrlID(lparam as HWND) };
            if field == Field::Volume as i32 || field == Field::Opacity as i32 {
                post(field as u16);
                return 0;
            }
        }
        _ => {}
    }
    unsafe { DefWindowProcW(hwnd, message, wparam, lparam) }
}

pub(super) fn close_window(hwnd: HWND) {
    // Hide before posting: the host tears down the embedded editor from the
    // queued Close action, and the key press must not leave a stale frame.
    unsafe { ShowWindow(hwnd, SW_HIDE) };
    let host = unsafe { GetWindowLongPtrW(hwnd, GWLP_USERDATA) } as HWND;
    if !host.is_null() {
        unsafe { PostMessageW(host, SETTINGS_ACTION_MESSAGE, Field::Close as WPARAM, 0) };
    }
}

pub(super) fn shell_hit(
    x: i32,
    y: i32,
    width: i32,
    height: i32,
    maximized: bool,
    scale: i32,
) -> LRESULT {
    let pad = if maximized {
        0
    } else {
        scaled(EDGE_PAD, scale)
    };
    if !maximized {
        // The first opaque pixels inside the transparent margin also resize.
        let hot_zone = scaled(EDGE_PAD + 4, scale);
        let column = if x < hot_zone {
            -1
        } else if x >= width - hot_zone {
            1
        } else {
            0
        };
        let row = if y < hot_zone {
            -1
        } else if y >= height - hot_zone {
            1
        } else {
            0
        };
        let hit = match (column, row) {
            (-1, -1) => HTTOPLEFT,
            (1, -1) => HTTOPRIGHT,
            (-1, 1) => HTBOTTOMLEFT,
            (1, 1) => HTBOTTOMRIGHT,
            (-1, _) => HTLEFT,
            (1, _) => HTRIGHT,
            (_, -1) => HTTOP,
            (_, 1) => HTBOTTOM,
            _ => HTCLIENT,
        };
        if hit != HTCLIENT {
            return hit as LRESULT;
        }
    }
    if y >= pad && y < pad + scaled(TITLE_HEIGHT, scale) {
        HTCAPTION as LRESULT
    } else {
        HTCLIENT as LRESULT
    }
}

pub(super) fn toggle_zoom(hwnd: HWND) {
    unsafe {
        ShowWindow(
            hwnd,
            if IsZoomed(hwnd) != 0 {
                SW_RESTORE
            } else {
                SW_MAXIMIZE
            },
        )
    };
}

pub(super) fn centered_position(width: i32, height: i32) -> POINT {
    let mut cursor = POINT::default();
    unsafe { GetCursorPos(&mut cursor) };
    let monitor = unsafe { MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST) };
    let mut info = MONITORINFO {
        cbSize: size_of::<MONITORINFO>() as u32,
        ..Default::default()
    };
    if unsafe { GetMonitorInfoW(monitor, &mut info) } != 0 {
        let work = info.rcWork;
        POINT {
            x: work.left + ((work.right - work.left - width) / 2).max(0),
            y: work.top + ((work.bottom - work.top - height) / 2).max(0),
        }
    } else {
        POINT::default()
    }
}

/// Settings runs per-monitor DPI aware, so every 96-DPI unit must be converted
/// before reaching Win32 controls. The cursor monitor decides the initial size.
pub(super) fn scale_at_cursor() -> i32 {
    let mut cursor = POINT::default();
    unsafe { GetCursorPos(&mut cursor) };
    let monitor = unsafe { MonitorFromPoint(cursor, MONITOR_DEFAULTTONEAREST) };
    let (mut x, mut y) = (96, 96);
    if unsafe { GetDpiForMonitor(monitor, MDT_EFFECTIVE_DPI, &mut x, &mut y) } >= 0 && x >= 96 {
        x as i32
    } else {
        96
    }
}

const DEFAULT_FAMILY: &str = crate::config::DEFAULT_UI_FONT;

pub(super) fn installed_fonts() -> Vec<String> {
    let mut family_names: Vec<String> = Vec::new();
    let query = LOGFONTW {
        lfCharSet: DEFAULT_CHARSET,
        ..Default::default()
    };
    let dc = unsafe { GetDC(ptr::null_mut()) };
    if !dc.is_null() {
        unsafe {
            EnumFontFamiliesExW(
                dc,
                &query,
                Some(add_font),
                ptr::from_mut(&mut family_names) as LPARAM,
                0,
            );
            ReleaseDC(ptr::null_mut(), dc);
        }
    }
    if !family_names.iter().any(|name| name == DEFAULT_FAMILY) {
        family_names.push(DEFAULT_FAMILY.into());
    }
    family_names.sort_by_cached_key(|name| name.to_lowercase());
    family_names.dedup();
    family_names
}

pub(super) unsafe extern "system" fn add_font(
    font: *const LOGFONTW,
    _: *const TEXTMETRICW,
    font_type: u32,
    data: LPARAM,
) -> i32 {
    if font_type & RASTER_FONTTYPE == 0 {
        let units = &unsafe { &*font }.lfFaceName;
        let length = units
            .iter()
            .position(|&unit| unit == 0)
            .unwrap_or(units.len());
        let name = String::from_utf16_lossy(&units[..length]);
        if !name.is_empty() && !name.starts_with('@') {
            unsafe { &mut *(data as *mut Vec<String>) }.push(name);
        }
    }
    1
}

pub(super) fn short(text: &str, limit: usize) -> String {
    if text.chars().count() <= limit {
        return text.to_owned();
    }
    text.chars()
        .take(limit.saturating_sub(1))
        .chain(['…'])
        .collect()
}

pub(super) fn usable_font<'a>(families: &[String], configured: &'a str) -> &'a str {
    if families.iter().any(|family| family == configured) {
        configured
    } else {
        DEFAULT_FAMILY
    }
}

pub(super) fn create_fonts(family: &str, scale: i32) -> [HFONT; 4] {
    let family = wide(if family.is_empty() {
        DEFAULT_FAMILY
    } else {
        family
    });
    [(13, 400), (16, 600), (30, 700), (13, 600)].map(|(size, weight)| unsafe {
        CreateFontW(
            -scaled(size, scale),
            0,
            0,
            0,
            weight,
            0,
            0,
            0,
            DEFAULT_CHARSET as u32,
            OUT_DEFAULT_PRECIS as u32,
            CLIP_DEFAULT_PRECIS as u32,
            CLEARTYPE_QUALITY as u32,
            DEFAULT_PITCH as u32,
            family.as_ptr(),
        )
    })
}

pub(super) fn free_fonts(fonts: [HFONT; 4]) {
    for font in fonts {
        if !font.is_null() {
            unsafe { DeleteObject(font) };
        }
    }
}

pub(super) fn apply_font(hwnd: HWND, kind: Kind, fonts: &[HFONT; 4]) {
    let font = fonts[match kind {
        Kind::Column => 1,
        Kind::Heading => 2,
        Kind::Button | Kind::Current | Kind::Value => 3,
        _ => 0,
    }];
    let font = if font.is_null() {
        unsafe { GetStockObject(DEFAULT_GUI_FONT) }
    } else {
        font
    };
    send(hwnd, WM_SETFONT, font as WPARAM, 1);
}

pub(super) fn text(hwnd: HWND) -> String {
    let length = unsafe { GetWindowTextLengthW(hwnd) };
    let mut units = vec![0; (length + 1) as usize];
    let length = unsafe { GetWindowTextW(hwnd, units.as_mut_ptr(), units.len() as i32) };
    String::from_utf16_lossy(&units[..length as usize])
}

pub(super) fn set_text(hwnd: HWND, value: &str) {
    let units = wide(value);
    unsafe { SetWindowTextW(hwnd, units.as_ptr()) };
}

pub(super) fn send(hwnd: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    unsafe { SendMessageW(hwnd, message, wparam, lparam) }
}

pub(super) fn checked(hwnd: HWND) -> bool {
    send(hwnd, BM_GETCHECK, 0, 0) != 0
}

pub(super) fn slider_position(value: f64, min: f64) -> i32 {
    ((value - min) * 20.0).round() as i32
}

pub(super) fn slider_value(position: LRESULT, min: f64) -> f64 {
    (position as f64 * 5.0 + min * 100.0) / 100.0
}

pub(super) fn choice(hwnd: HWND) -> Option<usize> {
    usize::try_from(send(hwnd, CB_GETCURSEL, 0, 0)).ok()
}

pub(super) fn fill_choices(
    hwnd: HWND,
    choices: impl IntoIterator<Item = impl AsRef<str>>,
    selected: usize,
) {
    let labels: Vec<String> = choices
        .into_iter()
        .map(|label| label.as_ref().to_owned())
        .collect();
    // Refreshes run on every settings action and rebuild every combo. Resetting
    // an unchanged list empties the field and repaints it, which flashes the
    // combos the user is not touching. An open dropdown must never be reset at
    // all: its visible list would collapse and rebuild in place.
    if send(hwnd, CB_GETDROPPEDSTATE, 0, 0) != 0 {
        return;
    }
    let count = send(hwnd, CB_GETCOUNT, 0, 0);
    let same_labels = count == labels.len() as LRESULT
        && (0..labels.len()).all(|index| super::controls::combo_text(hwnd, index) == labels[index]);
    if same_labels {
        if send(hwnd, CB_GETCURSEL, 0, 0) != selected as LRESULT {
            send(hwnd, CB_SETCURSEL, selected, 0);
        }
        return;
    }
    send(hwnd, CB_RESETCONTENT, 0, 0);
    for label in labels {
        let label = wide(&label);
        send(hwnd, CB_ADDSTRING, 0, label.as_ptr() as LPARAM);
    }
    send(hwnd, CB_SETCURSEL, selected, 0);
}

pub(super) fn changed(text: String, saved: &str) -> Option<String> {
    (text != saved).then_some(text)
}

pub(super) fn backends(provider: Provider) -> &'static [&'static str] {
    match provider {
        Provider::Cartesia => &[CARTESIA_BYTES, CARTESIA_REALTIME],
        Provider::Volcengine => &[VOLCENGINE_STREAMING],
    }
}

pub(super) fn selected_voice(voices: &[Voice], selected: Option<&str>) -> usize {
    voices
        .iter()
        .position(|voice| Some(voice.id.as_str()) == selected)
        .map_or(0, |index| index + 1)
}

pub(super) fn voice_labels(voices: &[Voice]) -> Vec<String> {
    let mut names = HashSet::new();
    let labels = voices.iter().map(|voice| {
        if names.insert(&voice.name) {
            voice.name.clone()
        } else {
            format!(
                "{} ({})",
                voice.name,
                voice.id.chars().take(8).collect::<String>()
            )
        }
    });
    std::iter::once(EMPTY_VOICE.to_owned())
        .chain(labels)
        .collect()
}
