use super::*;

impl SettingsWindow {
    pub(crate) fn refresh(&mut self, settings: &Settings) {
        if self.settings.ui_font != settings.ui_font {
            let family = usable_font(&self.font_families, &settings.ui_font);
            let old = std::mem::replace(&mut self.fonts, create_fonts(family, self.scale));
            for widget in &self.widgets {
                apply_font(widget.hwnd, widget.kind, &self.fonts);
            }
            free_fonts(old);
        }
        if settings.active_tts_provider != self.settings.active_tts_provider {
            set_text(self.control(Field::Key), "");
        }
        self.settings = settings.clone();
        for (field, prefix, binding) in [
            (Field::HotkeyValue, "当前：", &settings.hotkey),
            (Field::HoldKeyValue, "当前：", &settings.hold_key),
            (
                Field::RollingHotkeyValue,
                "快捷键：",
                &settings.rolling_buffer_hotkey,
            ),
        ] {
            set_text(
                self.control(field),
                &format!("{prefix}{}", crate::hotkey::display_hotkey(binding)),
            );
        }
        self.set_edit(
            Field::RollingSeconds,
            &format!("{}", settings.rolling_buffer_seconds.round()),
        );
        self.set_edit(Field::Placeholder, &settings.overlay_placeholder);
        let family = usable_font(&self.font_families, &settings.ui_font);
        let index = self
            .font_families
            .iter()
            .position(|font| font == family)
            .unwrap_or(0);
        fill_choices(self.control(Field::UiFont), &self.font_families, index);
        let font_hint = if settings.ui_font.is_empty() {
            "使用内置字体栈。".into()
        } else if family != settings.ui_font {
            format!("未安装「{}」，已回退到内置字体。", settings.ui_font)
        } else {
            format!("界面使用「{family}」，找不到时回退到内置字体。")
        };
        set_text(self.control(Field::FontHint), &font_hint);
        self.check(Field::HoldKeyEnabled, settings.hold_key_enabled);
        self.check(Field::FixedCenter, settings.fixed_center);
        self.check(Field::MainPlayback, settings.main_playback_enabled);
        self.check(Field::Sidetone, settings.sidetone_enabled);
        slider::set_position(
            self.control(Field::Volume),
            slider_position(settings.volume, 0.5),
        );
        slider::set_position(
            self.control(Field::Opacity),
            slider_position(settings.overlay_opacity, 0.30),
        );
        self.refresh_slider_labels();
        fill_choices(
            self.control(Field::LogLevel),
            LOG_LEVELS,
            LOG_LEVELS
                .iter()
                .position(|&level| level == settings.log_level)
                .unwrap_or(1),
        );
        self.refresh_audio_choices();
        self.refresh_audio_status();
        self.refresh_provider();
    }

    pub(crate) fn sync_debounced_inputs(&mut self, settings: &Settings) {
        // The controls already show the user's edits. Rebuilding the page on a drag flashes its siblings.
        self.settings.volume = settings.volume;
        self.settings.overlay_opacity = settings.overlay_opacity;
        self.settings
            .overlay_placeholder
            .clone_from(&settings.overlay_placeholder);
    }

    pub(crate) fn set_output_devices(&mut self, outputs: Vec<AudioIdentity>) {
        self.outputs = outputs;
        self.refresh_audio_choices();
    }

    pub(crate) fn set_input_devices(&mut self, microphones: Vec<String>) {
        self.microphones = microphones;
        self.refresh_audio_choices();
    }

    pub(crate) fn set_cartesia_voices(&mut self, voices: Vec<Voice>) {
        self.cartesia_voices = voices;
        if self.settings.active_tts_provider == Provider::Cartesia {
            self.refresh_provider();
        }
    }

    pub(crate) fn set_listen_state(&mut self, state: Option<(bool, Option<String>, bool)>) {
        self.listen_state = state;
        self.refresh_audio_status();
    }

    pub(crate) fn set_vb_cable_available(&mut self, available: bool) {
        self.cable_available = available;
        self.refresh_audio_status();
        self.show_page();
    }

    pub(super) fn control(&self, field: Field) -> HWND {
        self.widgets
            .iter()
            .find(|widget| widget.field == Some(field))
            .expect("the settings form declares all controls")
            .hwnd
    }

    fn set_edit(&self, field: Field, value: &str) {
        let control = self.control(field);
        if unsafe { GetFocus() } != control {
            set_text(control, value);
        }
    }

    fn check(&self, field: Field, check: bool) {
        send(self.control(field), BM_SETCHECK, usize::from(check), 0);
    }

    fn refresh_slider_labels(&self) {
        set_text(
            self.control(Field::VolumeValue),
            &format!("{:.2}x", self.settings.volume),
        );
        set_text(
            self.control(Field::OpacityValue),
            &format!("{:.0}%", self.settings.overlay_opacity * 100.0),
        );
    }

    fn refresh_audio_choices(&self) {
        let chosen = self.settings.audio_output_device.as_ref();
        let legacy = self.settings.audio_output_device_name.as_deref();
        // Match the playback rule: stable endpoint id, then exact name, then a
        // unique name fragment. The old code only accepted identical identities.
        let selected = chosen
            .and_then(|chosen| self.outputs.iter().position(|output| output == chosen))
            .or_else(|| {
                chosen
                    .and_then(|chosen| chosen.id.as_deref())
                    .and_then(|id| {
                        self.outputs
                            .iter()
                            .position(|output| output.id.as_deref() == Some(id))
                    })
            })
            .or_else(|| {
                chosen
                    .map(|chosen| chosen.name.as_str())
                    .or(legacy)
                    .and_then(|name| {
                        endpoint_index(self.outputs.iter().map(|output| output.name.as_str()), name)
                    })
            });
        let outputs =
            std::iter::once("默认（跟随系统）".into()).chain(self.outputs.iter().map(|output| {
                output.host_api_name.as_ref().map_or_else(
                    || output.name.clone(),
                    |api| format!("{} ({api})", output.name),
                )
            }));
        fill_choices(
            self.control(Field::Output),
            outputs,
            selected.map_or(0, |index| index + 1),
        );
        let missing = selected.is_none() && (chosen.is_some() || legacy.is_some());
        let status = if missing {
            format!(
                "已找到 {} 个输出设备；已保存的返听设备不可用。",
                self.outputs.len()
            )
        } else {
            format!("已找到 {} 个输出设备。", self.outputs.len())
        };
        set_text(self.control(Field::OutputStatus), &status);
        let selected = self
            .settings
            .mic_input_device
            .as_deref()
            .and_then(|name| endpoint_index(self.microphones.iter().map(String::as_str), name));
        let microphones = std::iter::once("无（不侦听麦克风）")
            .chain(self.microphones.iter().map(String::as_str));
        fill_choices(
            self.control(Field::Mic),
            microphones,
            selected.map_or(0, |index| index + 1),
        );
    }

    fn refresh_audio_status(&self) {
        set_text(
            self.control(Field::CableStatus),
            if self.cable_available {
                "输出至 CABLE Input。语音软件的输入设备选 CABLE Output。"
            } else {
                "未检测到 VB-CABLE。安装后即可输出至 CABLE Input。"
            },
        );
        set_text(
            self.control(Field::VirtualMic),
            if self.cable_available {
                "CABLE Input"
            } else {
                "未检测到 VB-CABLE"
            },
        );
        let listen = self
            .listen_state
            .as_ref()
            .map_or(self.settings.audio_routing_enabled, |state| state.0);
        self.check(Field::Listen, listen);
        unsafe { EnableWindow(self.control(Field::Listen), i32::from(self.cable_available)) };
        let mic_missing = self
            .settings
            .mic_input_device
            .as_deref()
            .is_some_and(|name| {
                endpoint_index(self.microphones.iter().map(String::as_str), name).is_none()
            });
        let mut status = match &self.listen_state {
            Some((enabled, output, has_target)) => {
                let mut text = if *enabled {
                    "Windows 侦听：已开启".to_string()
                } else {
                    "Windows 侦听：未开启".to_string()
                };
                if let Some(output) = output {
                    text.push_str(&format!(" · 输出目标：{}", short(output, 28)));
                } else if *has_target {
                    text.push_str(" · 输出目标：设备不可用");
                } else if *enabled {
                    text.push_str(" · 输出目标：默认播放设备");
                }
                text
            }
            None if self.settings.mic_input_device.is_some() => {
                "无法读取侦听状态——请确认该设备支持系统侦听".into()
            }
            None => "选择麦克风后自动配置".into(),
        };
        if mic_missing {
            status.push_str(" · 已保存的麦克风不可用");
        }
        set_text(self.control(Field::MicStatus), &status);
    }

    fn refresh_provider(&self) {
        let provider = self.settings.active_tts_provider;
        fill_choices(
            self.control(Field::Provider),
            [Provider::Volcengine.name(), Provider::Cartesia.name()],
            usize::from(provider == Provider::Cartesia),
        );
        let backends = backends(provider);
        fill_choices(
            self.control(Field::Backend),
            backends.iter().copied(),
            backends
                .iter()
                .position(|&mode| mode == self.settings.active_backend())
                .unwrap_or(0),
        );
        let has_key = match provider {
            Provider::Cartesia => self.settings.tts_providers.cartesia.api_key_set,
            Provider::Volcengine => self.settings.tts_providers.volcengine.api_key_set,
        };
        set_text(
            self.control(Field::KeyStatus),
            if has_key {
                "API Key 已保存"
            } else {
                "API Key 未保存"
            },
        );
        unsafe { EnableWindow(self.control(Field::DeleteKey), i32::from(has_key)) };
        let voices = self.voices();
        fill_choices(
            self.control(Field::Voices),
            voice_labels(voices),
            selected_voice(voices, self.settings.active_voice_id()),
        );
        let active_volc = self
            .settings
            .tts_providers
            .volcengine
            .voice_list
            .first()
            .map_or("无", |voice| voice.id.as_str());
        set_text(
            self.control(Field::VoiceHint),
            &format!("当前 Speaker ID：{active_volc}"),
        );
        self.show_page();
    }

    pub(super) fn voices(&self) -> &[Voice] {
        match self.settings.active_tts_provider {
            Provider::Cartesia => &self.cartesia_voices,
            Provider::Volcengine => &self.settings.tts_providers.volcengine.voice_list,
        }
    }

    pub(super) fn voice_selected(&self) -> Option<Voice> {
        choice(self.control(Field::Voices))
            .and_then(|index| index.checked_sub(1))
            .and_then(|index| self.voices().get(index).cloned())
    }

    pub(super) fn confirm(&self, message: &str) -> bool {
        let message = wide(message);
        unsafe {
            MessageBoxW(
                self.hwnd,
                message.as_ptr(),
                w!("Wordy"),
                MB_YESNO | MB_ICONQUESTION | MB_DEFBUTTON2,
            ) == IDYES
        }
    }
}
