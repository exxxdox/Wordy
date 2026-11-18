use super::*;

impl SettingsWindow {
    pub(crate) fn action(&mut self, action_id: u16) -> Option<SettingsAction> {
        use SettingsAction::*;
        if action_id == Field::Resize as u16 {
            self.layout();
            return None;
        }
        if action_id == Field::Close as u16 || action_id == Field::TitleClose as u16 {
            unsafe { ShowWindow(self.hwnd, SW_HIDE) };
            set_text(self.control(Field::Key), "");
            self.audio_devices_requested = false;
            return Some(Closed);
        }
        if let Some(&(page, field, ..)) = PAGES
            .iter()
            .find(|(_, field, ..)| *field as u16 == action_id)
        {
            self.page = page;
            unsafe { SetPropW(self.hwnd, SELECTED_TAB, field as usize as _) };
            self.show_page();
            for (_, field, ..) in PAGES {
                unsafe { InvalidateRect(self.control(field), ptr::null(), 0) };
            }
            if page == Page::Audio && !self.audio_devices_requested {
                self.audio_devices_requested = true;
                return Some(RequestAudioDevices);
            }
            if page == Page::Wheels && self.wheel_panel.is_null() {
                return Some(EditWheels);
            }
            return None;
        }
        let field = self
            .widgets
            .iter()
            .filter_map(|widget| widget.field)
            .find(|&field| field as u16 == action_id)?;
        let control = self.control(field);
        let active = self.settings.active_tts_provider;
        match field {
            Field::Minimize => {
                unsafe { ShowWindow(self.hwnd, SW_MINIMIZE) };
                None
            }
            Field::Maximize => {
                toggle_zoom(self.hwnd);
                None
            }
            Field::Hotkey => Some(RecordHotkey),
            Field::HoldKey => Some(RecordHoldKey),
            Field::RollingHotkey => Some(RecordRollingHotkey),
            Field::RollingSeconds => {
                let parsed = text(control).parse::<u16>().ok();
                if let Some(value) = parsed
                    .map(f64::from)
                    .filter(|&value| value != self.settings.rolling_buffer_seconds)
                {
                    return Some(SetRollingSeconds(value));
                }
                if parsed.is_none() {
                    set_text(
                        control,
                        &format!("{}", self.settings.rolling_buffer_seconds.round()),
                    );
                }
                None
            }
            Field::HoldKeyEnabled => Some(SetHoldKeyEnabled(checked(control))),
            Field::FixedCenter => Some(SetFixedCenter(checked(control))),
            Field::MainPlayback => Some(SetMainPlayback(checked(control))),
            Field::Sidetone => Some(SetSidetone(checked(control))),
            Field::Listen => Some(SetListen(checked(control))),
            Field::Volume => {
                let value = slider_value(send(control, TBM_GETPOS, 0, 0), 0.5);
                if value == self.settings.volume {
                    return None;
                }
                set_text(self.control(Field::VolumeValue), &format!("{value:.2}x"));
                Some(SetVolume(value))
            }
            Field::Opacity => {
                let value = slider_value(send(control, TBM_GETPOS, 0, 0), 0.30);
                if value == self.settings.overlay_opacity {
                    return None;
                }
                set_text(
                    self.control(Field::OpacityValue),
                    &format!("{:.0}%", value * 100.0),
                );
                Some(SetOpacity(value))
            }
            Field::Placeholder => {
                changed(text(control), &self.settings.overlay_placeholder).map(SetPlaceholder)
            }
            Field::UiFont => choice(control)
                .and_then(|index| self.font_families.get(index))
                .cloned()
                .map(SetUiFont),
            Field::LogLevel => choice(control)
                .and_then(|index| LOG_LEVELS.get(index))
                .map(|&level| SetLogLevel(level.into())),
            Field::Mic => choice(control).map(|index| {
                SetMic(
                    index
                        .checked_sub(1)
                        .and_then(|index| self.microphones.get(index).cloned()),
                )
            }),
            Field::Output => choice(control).map(|index| {
                SetOutputDevice(
                    index
                        .checked_sub(1)
                        .and_then(|index| self.outputs.get(index).cloned()),
                )
            }),
            Field::Provider => choice(control).map(|index| {
                SetProvider(if index == 0 {
                    Provider::Volcengine
                } else {
                    Provider::Cartesia
                })
            }),
            Field::Backend => choice(control)
                .and_then(|index| backends(active).get(index))
                .map(|&backend| SetBackend(backend.into())),
            Field::SaveKey => {
                let field = self.control(Field::Key);
                let raw = text(field);
                let key = if active == Provider::Cartesia {
                    normalize_api_key_input(&raw)
                } else {
                    raw.trim()
                };
                if key.is_empty() {
                    return Some(Feedback("请输入 API Key。".into()));
                }
                set_text(field, "");
                Some(SaveKey(active, key.to_owned()))
            }
            Field::DeleteKey => self
                .confirm("确定删除当前服务商的 API Key？")
                .then_some(DeleteKey(active)),
            Field::Voices => self
                .voice_selected()
                .map(|voice| SelectVoice(active, voice)),
            Field::RefreshVoices => Some(RefreshVoices),
            Field::SaveVoice => {
                let id = text(self.control(Field::VoiceId)).trim().to_owned();
                if id.is_empty() {
                    return Some(Feedback("请输入 Speaker ID。".into()));
                }
                let name = text(self.control(Field::VoiceName)).trim().to_owned();
                set_text(self.control(Field::VoiceId), "");
                set_text(self.control(Field::VoiceName), "");
                Some(SaveVolcengineVoice(Voice {
                    name: if name.is_empty() { id.clone() } else { name },
                    id,
                }))
            }
            Field::DeleteVoice => self
                .voice_selected()
                .filter(|_| self.confirm("确定删除当前音色？"))
                .map(|voice| DeleteVolcengineVoice(voice.id)),
            Field::Download => Some(DownloadVbCable),
            Field::Wheels => Some(EditWheels),
            Field::OpenLogs => Some(OpenLogs),
            Field::Export => Some(ExportBackup),
            Field::Import => Some(ImportBackup),
            _ => None,
        }
    }
}
