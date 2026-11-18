use std::{io, ptr};

use windows_sys::Win32::{
    Foundation::{HWND, RECT},
    Graphics::Gdi::InvalidateRect,
    UI::{
        Controls::EM_SETLIMITTEXT, Input::KeyboardAndMouse::EnableWindow, WindowsAndMessaging::*,
    },
};

use crate::{
    config::Settings,
    dialog::{Panel, Template},
    wheel::WheelStyle,
};

use super::{
    ADD, AUDIO_MODE, BASE, CLEAR, CROP, Chrome, ChromeState, EDITOR_MESSAGE, GAIN, GAIN_VALUE,
    HOTKEY, IMPORT, INFO, LABEL, NAME, PICK, PLAY, REMOVE, RENAME, STYLE, TABS, TEXT, TEXT_MODE,
    WHEEL, button, choices, combo, dimensions, edit, label, logical_px,
    preview::{Preview, wheel_labels},
    wheel_names,
};

pub struct Editor {
    preview: Preview,
    chrome: Chrome,
    pub panel: Panel,
    pub wheel: usize,
    pub slot: usize,
    pub empty_text_mode: bool,
    busy: bool,
}

impl Editor {
    pub fn new_embedded(host: HWND, parent: HWND, settings: &Settings) -> io::Result<Self> {
        let mut template = Template::embedded(500, 264);
        label(&mut template, 300, "轮盘", [4, 11, 27, 14]);
        combo(&mut template, WHEEL, [31, 7, 108, 100]);
        label(&mut template, 301, "快捷键", [144, 11, 28, 14]);
        button(&mut template, HOTKEY, "录制快捷键", [172, 7, 80, 18]);
        button(&mut template, ADD, "新增", [256, 7, 34, 18]);
        button(&mut template, REMOVE, "删除", [294, 7, 34, 18]);
        button(&mut template, RENAME, "重命名", [0, 0, 1, 1]);
        label(&mut template, 307, "样式", [407, 11, 28, 14]);
        combo(&mut template, STYLE, [435, 7, 63, 100]);
        template.control(0x82, PICK, "", BASE | TABS | 0x0000_0100, [3, 33, 198, 212]);
        edit(&mut template, NAME, [0, 0, 1, 1]);
        label(&mut template, INFO, "", [217, 42, 252, 16]);
        label(&mut template, 302, "内容", [217, 64, 26, 15]);
        button(&mut template, AUDIO_MODE, "音频", [245, 60, 52, 18]);
        button(&mut template, TEXT_MODE, "文字", [303, 60, 52, 18]);
        label(&mut template, 304, "音量", [217, 88, 26, 15]);
        edit(&mut template, GAIN, [245, 83, 112, 18]);
        label(&mut template, GAIN_VALUE, "100%", [362, 87, 41, 15]);
        label(&mut template, 305, "名称", [217, 108, 52, 15]);
        edit(&mut template, LABEL, [217, 125, 272, 18]);
        label(&mut template, 303, "朗读文字", [217, 150, 60, 15]);
        edit(&mut template, TEXT, [217, 167, 272, 18]);
        button(&mut template, IMPORT, "导入", [217, 152, 52, 18]);
        button(&mut template, PLAY, "试听", [275, 152, 52, 18]);
        button(&mut template, CROP, "截取", [333, 152, 52, 18]);
        button(&mut template, CLEAR, "清空", [391, 152, 52, 18]);
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
            busy: false,
        };
        for (id, limit) in [(NAME, 20), (LABEL, 40), (TEXT, 200), (GAIN, 8)] {
            unsafe { SendMessageW(editor.panel.child(id), EM_SETLIMITTEXT, limit, 0) };
        }
        unsafe { ShowWindow(editor.panel.child(NAME), SW_HIDE) };
        editor.refresh(settings)?;
        Ok(editor)
    }

    pub fn take_slot(&mut self) -> Option<usize> {
        self.preview.take_slot()
    }

    pub fn refresh(&mut self, settings: &Settings) -> io::Result<()> {
        self.chrome.set_ui_font(&settings.ui_font);
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
        unsafe { InvalidateRect(self.panel.0, ptr::null(), 1) };
        for (id, visible) in [
            (TEXT, text_mode),
            (303, text_mode),
            (GAIN, !text_mode && self.chrome.state.gain.is_null()),
            (GAIN_VALUE, !text_mode && !self.chrome.state.gain.is_null()),
            (304, !text_mode),
            (IMPORT, !text_mode),
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
            EnableWindow(self.chrome.state.gain, i32::from(has_audio));
            EnableWindow(
                self.panel.child(REMOVE),
                i32::from(settings.wheel_count() > 1),
            );
            EnableWindow(self.panel.child(PLAY), i32::from(has_audio || has_text));
            EnableWindow(self.panel.child(CROP), i32::from(has_audio));
            EnableWindow(
                self.panel.child(CLEAR),
                i32::from(has_audio || has_text || !wheel.labels[self.slot].is_empty()),
            );
            for id in [AUDIO_MODE, TEXT_MODE] {
                InvalidateRect(self.panel.child(id), ptr::null(), 0);
            }
        }
        layout_editor(self.panel.0, &self.chrome.state);
        let content = if has_text {
            "文字"
        } else if has_audio {
            "音频"
        } else {
            "空槽"
        };
        self.panel
            .set_text(INFO, &format!("槽位 {} · {content}", self.slot + 1));
        self.preview.draw(
            &wheel_labels(&wheel),
            WheelStyle::from_config(&settings.chat_wheel_style),
            self.slot,
            &settings.ui_font,
        )
    }

    /// Disables editing while a sample is being imported, saved, or previewed,
    /// leaving the preview button available to stop playback.
    pub fn busy(&mut self, active: bool) {
        if self.busy == active {
            return;
        }
        self.busy = active;
        unsafe {
            for id in [
                WHEEL, PICK, ADD, REMOVE, HOTKEY, STYLE, LABEL, TEXT, AUDIO_MODE, TEXT_MODE,
                IMPORT, CROP, CLEAR,
            ] {
                EnableWindow(self.panel.child(id), i32::from(!active));
            }
            EnableWindow(self.chrome.state.gain, i32::from(!active));
        }
    }
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

pub(super) fn editor_card(hwnd: HWND, text_mode: bool) -> RECT {
    let height = if text_mode { 246 } else { 229 };
    // Center the shorter control card against the 400-unit wheel preview.
    let top = 44 + (400 - height) / 2;
    RECT {
        left: logical_px(hwnd, 424),
        top: logical_px(hwnd, top),
        right: dimensions(hwnd).0,
        bottom: logical_px(hwnd, top + height),
    }
}

pub(super) fn layout_editor(hwnd: HWND, state: &ChromeState) {
    let width = dimensions(hwnd).0;
    let px = |value| logical_px(hwnd, value);
    let at = |id, x, y, w, h| place(unsafe { GetDlgItem(hwnd, id) }, x, px(y), w, px(h));
    let card = editor_card(hwnd, state.text_mode.get());
    let left = card.left + px(18);
    let field = card.right - left - px(18);
    for (id, x, y, w, h) in [
        (300, 0, 7, 40, 20),
        (WHEEL, 46, 0, 204, 200),
        (301, 256, 7, 56, 20),
        (HOTKEY, 318, 0, 145, 32),
        (ADD, 471, 0, 52, 32),
        (REMOVE, 531, 0, 52, 32),
        (RENAME, 591, 0, 58, 32),
        (PICK, 0, 44, 400, 400),
    ] {
        at(i32::from(id), px(x), y, px(w), h);
    }
    at(307, width - px(184), 7, px(40), 20);
    at(i32::from(STYLE), width - px(136), 0, px(136), 200);
    // The right card is vertically centered on the wheel; its fields keep their
    // original spacing but move with the card instead of hugging the top edge.
    let at_card = |id, x, y, w, h| {
        place(
            unsafe { GetDlgItem(hwnd, id) },
            left + px(x),
            card.top + px(y - 44),
            px(w),
            px(h),
        )
    };
    for (id, x, y, w, h) in [
        (INFO, 0, 60, 180, 23),
        (302, 0, 92, 44, 20),
        (AUDIO_MODE, 43, 86, 52, 32),
        (TEXT_MODE, 101, 86, 52, 32),
        (304, 0, 126, 44, 20),
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
        at_card(i32::from(id), x, y, w, h);
    }
    let gain = if state.gain.is_null() {
        unsafe { GetDlgItem(hwnd, i32::from(GAIN)) }
    } else {
        state.gain
    };
    place(
        gain,
        left + px(35),
        card.top + px(118 - 44),
        px(220),
        px(30),
    );
    let mode = state.text_mode.get();
    let card_y = |y| card.top + px(y - 44);
    place(
        unsafe { GetDlgItem(hwnd, i32::from(LABEL)) },
        left,
        card_y(if mode { 145 } else { 174 }),
        field,
        px(32),
    );
    place(
        unsafe { GetDlgItem(hwnd, i32::from(TEXT)) },
        left,
        card_y(205),
        field,
        px(32),
    );
    let action_y = if mode { 244 } else { 222 };
    let (mut x, gap) = (0, 6);
    for (id, control_width, visible) in [
        (IMPORT, 52, !mode),
        (PLAY, 52, true),
        (CROP, 52, !mode),
        (CLEAR, 52, true),
    ] {
        if visible {
            at_card(i32::from(id), x, action_y, control_width, 32);
            x += control_width + gap;
        }
    }
}

#[cfg(test)]
mod tests {
    use std::cell::Cell;
    use windows_sys::Win32::{
        Foundation::{LPARAM, LRESULT, WPARAM},
        Graphics::Gdi::MapWindowPoints,
        UI::{
            Controls::TB_ENDTRACK,
            Shell::{DefSubclassProc, SetWindowSubclass},
        },
    };

    use super::*;
    use crate::slider;

    #[test]
    fn emoji_support_is_checked_per_font() {
        let settings = Settings::default();
        let window =
            crate::settings_window::SettingsWindow::new(ptr::null_mut(), &settings).unwrap();
        let _editor = Editor::new_embedded(ptr::null_mut(), window.hwnd(), &settings).unwrap();
        assert!(super::super::font_has_glyph("Segoe UI Emoji", "📥"));
        assert!(!super::super::font_has_glyph("Consolas", "📥"));
    }

    #[test]
    fn editor_icon_buttons_share_the_same_width() {
        let settings = Settings::default();
        let window =
            crate::settings_window::SettingsWindow::new(ptr::null_mut(), &settings).unwrap();
        let editor = Editor::new_embedded(ptr::null_mut(), window.hwnd(), &settings).unwrap();
        let width = |id| {
            let mut rect = RECT::default();
            unsafe { GetWindowRect(editor.panel.child(id), &mut rect) };
            rect.right - rect.left
        };
        let expected = width(ADD);
        for id in [IMPORT, PLAY, CROP, CLEAR, AUDIO_MODE, TEXT_MODE, REMOVE] {
            assert_eq!(width(id), expected, "icon button {id} width differs");
        }
    }

    #[test]
    fn editor_card_is_vertically_centered_on_the_wheel_preview() {
        let settings = Settings::default();
        let window =
            crate::settings_window::SettingsWindow::new(ptr::null_mut(), &settings).unwrap();
        let editor = Editor::new_embedded(ptr::null_mut(), window.hwnd(), &settings).unwrap();
        let mut pick = RECT::default();
        unsafe {
            GetWindowRect(editor.panel.child(PICK), &mut pick);
            MapWindowPoints(
                ptr::null_mut(),
                editor.panel.0,
                ptr::from_mut(&mut pick).cast(),
                2,
            );
        }
        let card = editor_card(editor.panel.0, false);
        assert!(
            (card.top - pick.top - (pick.bottom - card.bottom)).abs() <= 2,
            "editor card is not centered in the wheel preview"
        );
    }

    #[test]
    fn editor_gain_readout_is_written_only_when_its_value_changes() {
        unsafe extern "system" fn count_writes(
            hwnd: HWND,
            msg: u32,
            wp: WPARAM,
            lp: LPARAM,
            _: usize,
            data: usize,
        ) -> LRESULT {
            unsafe {
                if msg == WM_SETTEXT {
                    let writes = &*(data as *const Cell<usize>);
                    writes.set(writes.get() + 1);
                }
                DefSubclassProc(hwnd, msg, wp, lp)
            }
        }

        let writes = Cell::new(0usize);
        let mut settings = Settings::default();
        settings.chat_wheel_slots[0] = "test.wav".into();
        let window =
            crate::settings_window::SettingsWindow::new(ptr::null_mut(), &settings).unwrap();
        let editor = Editor::new_embedded(ptr::null_mut(), window.hwnd(), &settings).unwrap();
        let gain = editor.chrome.state.gain;
        let readout = editor.panel.child(GAIN_VALUE);
        unsafe {
            assert_ne!(
                SetWindowSubclass(
                    readout,
                    Some(count_writes),
                    1,
                    ptr::from_ref(&writes) as usize
                ),
                0
            );
            slider::set_position(gain, 145);
            SendMessageW(editor.panel.0, WM_HSCROLL, 0, gain as LPARAM);
            SendMessageW(
                editor.panel.0,
                WM_HSCROLL,
                TB_ENDTRACK as usize,
                gain as LPARAM,
            );
        }
        assert_eq!(editor.panel.text(GAIN_VALUE), "145%");
        assert_eq!(editor.panel.text(GAIN), "145");
        assert_eq!(writes.get(), 1);
    }
}
