use super::paint::*;
use super::*;

pub(super) unsafe extern "system" fn overlay_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
) -> LRESULT {
    // Never borrow app state inside a window procedure. Turn actions into queued messages.
    unsafe {
        let action = match msg {
            WM_COMMAND => match wp as u16 as i32 {
                READ => Some(OVERLAY_READ),
                SETTINGS => Some(OVERLAY_SETTINGS),
                ENTRY if wp >> 16 == EN_CHANGE as usize => {
                    enable_read(hwnd, !window_text(lp as HWND).trim().is_empty());
                    None
                }
                _ => None,
            },
            WM_ACTIVATE if wp as u16 == WA_INACTIVE as u16 => Some(OVERLAY_DEACTIVATED),
            WM_WINDOWPOSCHANGED => {
                move_overlay_shadow(hwnd);
                None
            }
            WM_EXITSIZEMOVE => Some(OVERLAY_POSITION),
            WM_CLOSE => Some(OVERLAY_DISMISS),
            WM_LBUTTONDOWN if !GetPropW(hwnd, w!("WordyDrag")).is_null() => {
                SetFocus(GetDlgItem(hwnd, ENTRY));
                ReleaseCapture();
                SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION as usize, 0);
                return 0;
            }
            WM_TIMER if (1..=3).contains(&wp) => {
                KillTimer(hwnd, wp);
                if GetForegroundWindow() == hwnd
                    && IsWindowVisible(hwnd) != 0
                    && GetFocus() != GetDlgItem(hwnd, SETTINGS)
                {
                    SetFocus(GetDlgItem(hwnd, ENTRY));
                }
                return 0;
            }
            WM_TIMER if wp == GLASS_TIMER => {
                sync_overlay_glass(hwnd);
                return 0;
            }
            WM_ERASEBKGND => return 1,
            WM_PAINT => {
                let mut paint = PAINTSTRUCT::default();
                let dc = BeginPaint(hwnd, &mut paint);
                let mut bounds = RECT::default();
                GetClientRect(hwnd, &mut bounds);
                FillRect(dc, &bounds, overlay_key_brush(hwnd));
                EndPaint(hwnd, &paint);
                return 0;
            }
            WM_DRAWITEM if wp == READ as usize || wp == SETTINGS as usize => {
                let item = &*(lp as *const DRAWITEMSTRUCT);
                let mut origin = POINT::default();
                ClientToScreen(item.hwndItem, &mut origin);
                ScreenToClient(hwnd, &mut origin);
                let hovered = GetPropW(hwnd, w!("WordyHoverIcon")) as usize == wp
                    || (wp == SETTINGS as usize && GetFocus() == item.hwndItem);
                let enabled = IsWindowEnabled(item.hwndItem) != 0;
                let color = capsule_icon_color(wp == SETTINGS as usize, hovered, enabled);
                paint_capsule(
                    hwnd,
                    item.hDC,
                    item.rcItem,
                    origin.x,
                    origin.y,
                    Some(color),
                    wp == SETTINGS as usize,
                    true,
                );
                return 1;
            }
            WM_CTLCOLOREDIT | WM_CTLCOLORSTATIC | WM_CTLCOLORBTN => {
                let dc = wp as HDC;
                let text = if lp as HWND == GetDlgItem(hwnd, PROMPT) {
                    [152, 152, 157]
                } else {
                    [245, 245, 247]
                };
                SetTextColor(dc, colorref(text));
                SetBkMode(dc, TRANSPARENT as i32);
                // Child HWNDs must use the same key instead of a system-colored background.
                SetBkColor(dc, OVERLAY_KEY);
                return overlay_key_brush(hwnd) as isize;
            }
            WM_NCDESTROY => {
                for prop in [w!("WordyGlassBrush"), w!("WordyKeyBrush")] {
                    let brush = RemovePropW(hwnd, prop);
                    if !brush.is_null() {
                        DeleteObject(brush);
                    }
                }
                None
            }
            _ => None,
        };
        if let Some(action) = action {
            post_overlay(hwnd, action);
            0
        } else {
            DefWindowProcW(hwnd, msg, wp, lp)
        }
    }
}

pub(super) unsafe extern "system" fn edit_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    id: usize,
    parent: usize,
) -> LRESULT {
    unsafe {
        if msg == WM_NCDESTROY {
            RemovePropW(hwnd, w!("WordyPlaceholder"));
            RemoveWindowSubclass(hwnd, Some(edit_proc), id);
        }
        if msg == WM_CHAR {
            match wp as u16 {
                VK_RETURN => {
                    post_overlay(parent as HWND, OVERLAY_SUBMIT);
                    return 0;
                }
                VK_ESCAPE => {
                    post_overlay(parent as HWND, OVERLAY_DISMISS);
                    return 0;
                }
                VK_TAB => {
                    SetFocus(GetDlgItem(parent as HWND, SETTINGS));
                    return 0;
                }
                _ => (),
            }
        }
        let result = DefSubclassProc(hwnd, msg, wp, lp);
        if msg == WM_PAINT || msg == WM_PRINTCLIENT {
            paint_overlay_placeholder(hwnd, (msg == WM_PRINTCLIENT).then_some(wp as HDC));
        }
        result
    }
}

unsafe fn paint_overlay_placeholder(hwnd: HWND, print_dc: Option<HDC>) {
    unsafe {
        let placeholder = GetPropW(hwnd, w!("WordyPlaceholder")).cast::<u16>();
        if placeholder.is_null() || *placeholder == 0 || GetWindowTextLengthW(hwnd) != 0 {
            return;
        }
        let dc = print_dc.unwrap_or_else(|| GetDC(hwnd));
        if dc.is_null() {
            return;
        }
        // The Win32 cue banner depends on a manifest. Drawing over EDIT keeps native IME and selection.
        let mut rect = RECT::default();
        GetClientRect(hwnd, &mut rect);
        let saved = SaveDC(dc);
        SetTextColor(dc, colorref([152, 152, 157]));
        SetBkMode(dc, TRANSPARENT as i32);
        let font = SendMessageW(hwnd, WM_GETFONT, 0, 0) as HFONT;
        let previous = if font.is_null() {
            ptr::null_mut()
        } else {
            SelectObject(dc, font)
        };
        let hidden = HideCaret(hwnd) != 0;
        DrawTextW(
            dc,
            placeholder,
            -1,
            &mut rect,
            DT_SINGLELINE | DT_VCENTER | DT_END_ELLIPSIS | DT_NOPREFIX,
        );
        if hidden {
            ShowCaret(hwnd);
        }
        if saved != 0 {
            RestoreDC(dc, saved);
        } else if !previous.is_null() {
            SelectObject(dc, previous);
        }
        if print_dc.is_none() {
            ReleaseDC(hwnd, dc);
        }
    }
}

pub(super) unsafe extern "system" fn overlay_button_proc(
    hwnd: HWND,
    msg: u32,
    wp: WPARAM,
    lp: LPARAM,
    id: usize,
    parent: usize,
) -> LRESULT {
    unsafe {
        let parent = parent as HWND;
        let command = if GetDlgCtrlID(hwnd) == SETTINGS {
            OVERLAY_SETTINGS
        } else {
            OVERLAY_READ
        };
        match msg {
            // Owner-draw replaces the entire button surface in one blit.
            WM_ERASEBKGND => return 1,
            WM_NCDESTROY => {
                RemoveWindowSubclass(hwnd, Some(overlay_button_proc), id);
            }
            WM_LBUTTONDOWN if IsWindowEnabled(hwnd) != 0 => {
                // Clicking an icon leaves the native text editor and its caret focused.
                SetCapture(hwnd);
                return 0;
            }
            WM_LBUTTONUP if GetCapture() == hwnd => {
                ReleaseCapture();
                let mut rect = RECT::default();
                GetClientRect(hwnd, &mut rect);
                let x = i32::from(lp as i16);
                let y = i32::from((lp >> 16) as i16);
                if (rect.left..rect.right).contains(&x)
                    && (rect.top..rect.bottom).contains(&y)
                    && IsWindowEnabled(hwnd) != 0
                {
                    post_overlay(parent, command);
                }
                return 0;
            }
            WM_SETCURSOR => {
                let icon = if IsWindowEnabled(hwnd) != 0 {
                    IDC_HAND
                } else {
                    IDC_ARROW
                };
                SetCursor(LoadCursorW(ptr::null_mut(), icon));
                return 1;
            }
            WM_CHAR if wp == VK_RETURN as usize || wp == usize::from(b' ') => {
                if IsWindowEnabled(hwnd) != 0 {
                    post_overlay(parent, command);
                }
                return 0;
            }
            WM_CHAR if wp == VK_TAB as usize => {
                SetFocus(GetDlgItem(parent, ENTRY));
                return 0;
            }
            WM_CHAR if wp == VK_ESCAPE as usize => {
                post_overlay(parent, OVERLAY_DISMISS);
                return 0;
            }
            _ => {}
        }
        DefSubclassProc(hwnd, msg, wp, lp)
    }
}

unsafe fn move_overlay_shadow(hwnd: HWND) {
    unsafe {
        let shadow = GetPropW(hwnd, w!("WordyShadow"));
        let mut pos = RECT::default();
        if !shadow.is_null() && IsWindowVisible(hwnd) != 0 && GetWindowRect(hwnd, &mut pos) != 0 {
            SetWindowPos(
                shadow,
                hwnd,
                pos.left,
                pos.top,
                0,
                0,
                SWP_NOSIZE | SWP_NOACTIVATE | SWP_SHOWWINDOW,
            );
        }
    }
}

unsafe fn sync_overlay_glass(hwnd: HWND) {
    unsafe {
        let previous = GetPropW(hwnd, w!("WordyFocusGlow")) as usize;
        let focused = GetFocus();
        let active = focused == hwnd || IsChild(hwnd, focused) != 0;
        let step = if active {
            (previous + 1).min(GLOW_STEPS)
        } else {
            previous.saturating_sub(1)
        };
        let read = GetDlgItem(hwnd, READ);
        let settings = GetDlgItem(hwnd, SETTINGS);
        let mut cursor = POINT::default();
        GetCursorPos(&mut cursor);
        let is_over = |child| {
            let mut bounds = RECT::default();
            GetWindowRect(child, &mut bounds) != 0
                && (bounds.left..bounds.right).contains(&cursor.x)
                && (bounds.top..bounds.bottom).contains(&cursor.y)
        };
        let hovered = if is_over(settings) {
            SETTINGS
        } else if IsWindowEnabled(read) != 0 && is_over(read) {
            READ
        } else {
            0
        };
        let previous_hover = GetPropW(hwnd, w!("WordyHoverIcon")) as i32;
        if previous_hover != hovered {
            if hovered == 0 {
                RemovePropW(hwnd, w!("WordyHoverIcon"));
            } else {
                SetPropW(
                    hwnd,
                    w!("WordyHoverIcon"),
                    ptr::without_provenance_mut(hovered as usize),
                );
            }
            for button in [read, settings] {
                InvalidateRect(button, ptr::null(), 0);
            }
        }
        if previous != step {
            if step == 0 {
                RemovePropW(hwnd, w!("WordyFocusGlow"));
            } else {
                SetPropW(
                    hwnd,
                    w!("WordyFocusGlow"),
                    ptr::without_provenance_mut(step),
                );
            }
            let backdrop = GetPropW(hwnd, w!("WordyShadow")) as HWND;
            let opacity = GetPropW(hwnd, OVERLAY_OPACITY) as usize as f64 / 1000.0;
            let mut rect = RECT::default();
            if GetWindowRect(hwnd, &mut rect) != 0 {
                let _ = draw_backdrop(
                    backdrop,
                    rect.left,
                    rect.top,
                    rect.right - rect.left,
                    rect.bottom - rect.top,
                    opacity,
                    capsule_glow(hwnd),
                );
            }
        }
    }
}

unsafe fn overlay_key_brush(hwnd: HWND) -> HBRUSH {
    unsafe {
        let cached = GetPropW(hwnd, w!("WordyKeyBrush"));
        if !cached.is_null() {
            return cached;
        }
        let brush = CreateSolidBrush(OVERLAY_KEY);
        if !brush.is_null() && SetPropW(hwnd, w!("WordyKeyBrush"), brush) != 0 {
            return brush;
        }
        GetStockObject(NULL_BRUSH)
    }
}

unsafe fn post_overlay(overlay: HWND, action: usize) {
    unsafe {
        let host = GetWindowLongPtrW(overlay, GWLP_USERDATA) as HWND;
        if !host.is_null() {
            PostMessageW(host, OVERLAY_MESSAGE, action, 0);
        }
    }
}

pub(super) unsafe fn enable_read(hwnd: HWND, enabled: bool) {
    unsafe {
        windows_sys::Win32::UI::Input::KeyboardAndMouse::EnableWindow(
            GetDlgItem(hwnd, READ),
            i32::from(enabled),
        )
    };
}
