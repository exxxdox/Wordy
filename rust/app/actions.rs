use super::*;

impl App {
    pub(super) fn hotkey(&mut self, id: usize) -> io::Result<()> {
        let Some((action, key)) = self.hotkeys.action(id) else {
            return Ok(());
        };
        match action {
            Action::Overlay => self.toggle_overlay(),
            Action::Rolling => self.toggle_recording(),
            Action::Wheel(index) => {
                if let Some(wheel) = self.config.settings().wheel(index) {
                    let labels = panels::wheel_labels(&wheel);
                    if self
                        .wheel
                        .open(
                            &labels,
                            WheelStyle::from_config(&self.config.settings().chat_wheel_style),
                            key,
                            &self.config.settings().ui_font,
                        )
                        .map_err(io::Error::other)?
                    {
                        self.active_wheel = Some(index);
                        self.log(format!("打开轮盘 {}", index + 1));
                    }
                }
                Ok(())
            }
        }
    }

    pub(super) fn play_slot(&mut self, index: usize, slot: usize) -> io::Result<()> {
        self.log(format!("播放轮盘 {} · 音频 {}", index + 1, slot + 1));
        if let Some(wheel) = self.config.settings().wheel(index) {
            let id = self.id();
            if !wheel.texts[slot].is_empty() {
                self.playback.speak(
                    id,
                    wheel.texts[slot].clone(),
                    self.engine.clone(),
                    self.config.settings(),
                );
            } else if !wheel.slots[slot].is_empty() {
                let path = audio::resolve_clip(self.config.path(), &wheel.slots[slot])?;
                self.playback.stop_speech();
                self.playback.play_clip(
                    id,
                    path,
                    wheel.volumes[slot],
                    false,
                    self.config.settings(),
                );
            }
        }
        Ok(())
    }

    pub(super) fn toggle_overlay(&mut self) -> io::Result<()> {
        if self.overlay.is_visible() {
            self.overlay.hide(true);
            self.log("收起输入框".into());
            Ok(())
        } else {
            self.overlay.show(self.config.settings())?;
            self.log("打开输入框".into());
            Ok(())
        }
    }

    pub(super) fn overlay_action(&mut self, action: usize) -> io::Result<()> {
        // Only the saved position writes config; the rest stays usable.
        if self.backup_running && action == OVERLAY_POSITION {
            return Ok(());
        }
        match action {
            OVERLAY_SUBMIT | OVERLAY_READ => {
                let text = self.overlay.text();
                if text.is_empty() {
                    return Ok(());
                }
                if action == OVERLAY_SUBMIT {
                    self.overlay.hide(true);
                }
                let id = self.id();
                self.log(format!("提交朗读（{} 字）", text.chars().count()));
                self.playback
                    .speak(id, text, self.engine.clone(), self.config.settings());
            }
            OVERLAY_SETTINGS => self.show_settings()?,
            OVERLAY_DISMISS => self.overlay.hide(false),
            OVERLAY_DEACTIVATED if unsafe { IsWindowVisible(self.settings_window.hwnd()) } == 0 => {
                self.overlay.hide(false);
            }
            OVERLAY_POSITION => {
                if let Some(position) = self.overlay.position() {
                    self.config
                        .update(|settings| settings.window_position = Some(position))?;
                }
            }
            _ => (),
        }
        Ok(())
    }

    pub(super) fn show_settings(&mut self) -> io::Result<()> {
        // Settings takes over the foreground; the floating bar must not stay on top of it.
        self.overlay.hide(false);
        self.settings_window.refresh(self.config.settings());
        self.refresh_editor()?;
        self.settings_window.show();
        Ok(())
    }

    pub(super) fn show_logs(&mut self) -> io::Result<()> {
        match &self.log_window {
            Some(window) => window.show_existing(),
            None => {
                let window = LogWindow::open(self.host, &self.config.settings().ui_font)?;
                let lines = self.logs.iter().cloned().collect::<Vec<_>>();
                window.set_lines(&lines);
                self.log_window = Some(window);
            }
        }
        Ok(())
    }

}
