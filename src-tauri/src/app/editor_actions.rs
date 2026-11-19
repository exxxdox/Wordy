use super::*;

impl App {
    pub(super) fn close_editor(&mut self) -> io::Result<()> {
        if let Some((_, text)) = self.editor_preview.take() {
            if text {
                self.playback.stop_speech();
            } else {
                self.playback.stop_clip();
            }
        }
        self.editor.panel.set_text(panels::PLAY, "试听");
        Ok(())
    }

    pub(super) fn refresh_editor(&mut self) -> io::Result<()> {
        self.editor.refresh(self.config.settings())
    }

    pub(super) fn editor_action(&mut self, command: usize, hwnd: HWND) -> io::Result<()> {
        use panels::*;
        const CANCEL: u16 = IDCANCEL as u16;
        if self.backup_running {
            return Ok(());
        }
        let editor = &mut self.editor;
        if editor.panel.0 != hwnd {
            return Ok(());
        }
        let (action, notification) = (command as u16, (command >> 16) as u32);
        if action == CANCEL && notification == 0 {
            return self.close_editor();
        }
        let locked = self.pending_saves > 0
            || self.trim.is_some()
            || self.editor_preview.is_some_and(|(_, is_text)| is_text);
        if locked && action != PLAY {
            return Ok(());
        }
        let (wheel, slot) = (editor.wheel, editor.slot);
        match action {
            PICK => {
                if let Some(slot) = editor.take_slot() {
                    editor.slot = slot;
                    editor.empty_text_mode = false;
                }
            }
            WHEEL if notification == CBN_SELCHANGE => {
                editor.wheel = selected(&editor.panel, WHEEL);
                editor.empty_text_mode = false;
                let index = editor.wheel;
                self.config
                    .update(|settings| settings.chat_wheel_last_index = index)?;
            }
            STYLE if notification == CBN_SELCHANGE => {
                let style = crate::config::CHAT_WHEEL_STYLES[selected(&editor.panel, STYLE)];
                self.config.update(|s| s.chat_wheel_style = style.into())?;
            }
            ADD if notification == 0 => {
                // Add the wheel immediately; its shortcut can be recorded later
                // with the "录制快捷键" button.
                editor.wheel = append_unbound_wheel(&mut self.config, &mut self.hotkeys)?;
                editor.slot = 0;
            }
            REMOVE if notification == 0 => {
                let mut next = self.config.settings().clone();
                if next.wheel_count() == 1 {
                    return Err(io::Error::other("至少保留一个轮盘"));
                }
                if !dialog::confirm(
                    hwnd,
                    "删除轮盘",
                    "删除此轮盘及其槽位？",
                    &self.config.settings().ui_font,
                ) {
                    return Ok(());
                }
                let removed = next.wheel(wheel).unwrap();
                next.discard_wheel(wheel);
                next.chat_wheel_last_index = wheel.min(next.wheel_count() - 1);
                self.hotkeys.update(&mut self.config, next)?;
                for path in removed.slots.iter().filter(|path| !path.is_empty()) {
                    let _ = audio::remove_unreferenced_clip(&self.config, path);
                }
            }
            HOTKEY if notification == 0 => {
                let Some(hotkey) = capture_binding(
                    &mut self.hotkeys,
                    hwnd,
                    false,
                    &self.config.settings().ui_font,
                )?
                else {
                    return Ok(());
                };
                let mut next = self.config.settings().clone();
                let mut edited = next.wheel(wheel).unwrap();
                edited.hotkey = hotkey;
                next.assign_wheel(wheel, edited);
                self.hotkeys.update(&mut self.config, next)?;
            }
            NAME | LABEL | TEXT | GAIN if notification == EN_KILLFOCUS => {
                let value = editor.panel.text(action).trim().to_owned();
                let current = self.config.settings().wheel(wheel).unwrap();
                // Each field commits independently, so a focus change cannot overwrite another slot.
                match action {
                    NAME if value != current.name => self
                        .config
                        .update_wheel(wheel, |wheel| wheel.name = value)?,
                    LABEL if value != current.labels[slot] => self
                        .config
                        .update_wheel(wheel, |wheel| wheel.labels[slot] = value)?,
                    TEXT if current.slots[slot].is_empty() && value != current.texts[slot] => {
                        editor.empty_text_mode = true;
                        self.config
                            .update_wheel(wheel, |wheel| wheel.texts[slot] = value)?;
                    }
                    GAIN if !current.slots[slot].is_empty() => {
                        let gain = gain(&value)?;
                        if gain != current.volumes[slot] {
                            self.config
                                .update_wheel(wheel, |wheel| wheel.volumes[slot] = gain)?;
                        }
                        // Dragging already updated the controls; saving must not rebuild the editor.
                        return Ok(());
                    }
                    _ => return Ok(()),
                }
                if action == NAME {
                    // The selector lists wheel names, so it has to follow the rename.
                    self.refresh_editor()?;
                }
            }
            AUDIO_MODE | TEXT_MODE if notification == 0 => {
                let to_text = action == TEXT_MODE;
                let current = self.config.settings().wheel(wheel).unwrap();
                if to_text
                    && !current.slots[slot].is_empty()
                    && !dialog::confirm(
                        hwnd,
                        "切换为文字",
                        "切换为文字会删除该槽音频，继续？",
                        &self.config.settings().ui_font,
                    )
                {
                    return Ok(());
                }
                editor.empty_text_mode = to_text;
                if (to_text && !current.slots[slot].is_empty())
                    || (!to_text && !current.texts[slot].is_empty())
                {
                    self.playback.stop_clip();
                    self.config.update_wheel(wheel, |wheel| {
                        wheel.slots[slot].clear();
                        wheel.texts[slot].clear();
                    })?;
                    if !current.slots[slot].is_empty() {
                        let _ = audio::remove_unreferenced_clip(&self.config, &current.slots[slot]);
                    }
                }
            }
            CLEAR if notification == 0 => {
                self.playback.stop_clip();
                let old = self.config.settings().wheel(wheel).unwrap().slots[slot].clone();
                self.config.update_wheel(wheel, |wheel| {
                    wheel.slots[slot].clear();
                    wheel.labels[slot].clear();
                    wheel.texts[slot].clear();
                })?;
                if !old.is_empty() {
                    let _ = audio::remove_unreferenced_clip(&self.config, &old);
                }
            }
            IMPORT if notification == 0 => {
                let filter =
                    "音频文件\0*.wav;*.mp3;*.ogg;*.flac;*.m4a;*.aac;*.wma;*.aiff;*.aif;*.opus\0";
                let directory = std::env::current_dir()?;
                let Some(path) = dialog::file(hwnd, "导入音频", filter, &directory, None, false)
                else {
                    return Ok(());
                };
                return self.import_slot_clip(wheel, slot, path);
            }
            CROP if notification == 0 => {
                let item = self.config.settings().wheel(wheel).unwrap();
                if item.slots[slot].is_empty() {
                    return Ok(());
                }
                let path = audio::resolve_clip(self.config.path(), &item.slots[slot])?;
                return self.prepare_trim(wheel, slot, path);
            }
            PLAY if notification == 0 => {
                if self.trim.is_some() {
                    return Ok(());
                }
                if let Some((_, text)) = self.editor_preview {
                    if text {
                        self.playback.stop_speech();
                    } else {
                        self.playback.stop_clip();
                    }
                    return Ok(());
                }
                let item = self.config.settings().wheel(wheel).unwrap();
                if item.texts[slot].is_empty() && item.slots[slot].is_empty() {
                    return Ok(());
                }
                let text = !item.texts[slot].is_empty();
                let preview_id = self.next_job + 1;
                editor.panel.set_text(PLAY, "停止");
                self.editor_preview = Some((preview_id, text));
                let result = self.play_slot(wheel, slot);
                if result.is_err() && self.editor_preview.is_some_and(|(id, _)| id == preview_id) {
                    // Nothing was queued, so no Finished event will unlock the editor.
                    self.editor_preview = None;
                    self.editor.panel.set_text(PLAY, "试听");
                }
                return result;
            }
            _ => return Ok(()),
        }
        self.refresh_editor()
    }
}
