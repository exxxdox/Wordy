use super::*;

impl App {
    fn edit(&mut self, update: impl FnOnce(&mut Settings)) -> io::Result<()> {
        self.config.update(update)?;
        self.settings_window.refresh(self.config.settings());
        Ok(())
    }

    fn edit_debounced(&mut self, update: impl FnOnce(&mut Settings)) -> io::Result<()> {
        self.config.update_debounced(update)?;
        self.settings_window
            .sync_debounced_inputs(self.config.settings());
        Ok(())
    }

    pub(super) fn settings_action(&mut self, action: SettingsAction) -> io::Result<()> {
        use SettingsAction::*;
        // A background backup owns config.toml and the credential store. Closed
        // only clears transient UI state, so it must still reach close_editor.
        if self.backup_running && !matches!(action, SettingsAction::Closed) {
            return Ok(());
        }
        match action {
            SetProvider(provider) => {
                let previous = self.config.settings().clone();
                self.edit(|s| s.active_tts_provider = provider)?;
                if let Err(error) = self.rebuild_engine() {
                    // Do not leave a provider active when its engine cannot start.
                    self.edit(|s| *s = previous)?;
                    return Err(error);
                }
                if provider == Provider::Cartesia {
                    self.refresh_voices();
                }
            }
            SetBackend(backend) => {
                self.edit(|s| match s.active_tts_provider {
                    Provider::Cartesia => s.tts_providers.cartesia.backend = backend,
                    Provider::Volcengine => s.tts_providers.volcengine.backend = backend,
                })?;
                self.rebuild_engine()?;
            }
            SetVolume(volume) => self.edit_debounced(|s| s.volume = volume)?,
            SetOpacity(opacity) => {
                self.edit_debounced(|s| s.overlay_opacity = opacity)?;
                self.overlay
                    .apply_opacity(self.config.settings().overlay_opacity)?;
            }
            SetPlaceholder(value) => {
                self.edit_debounced(|s| s.overlay_placeholder = value)?;
                self.overlay.apply_visual_settings(self.config.settings())?;
            }
            SetUiFont(value) => {
                self.edit(|s| s.ui_font = value)?;
                self.settings_window.apply_panel_font(self.editor.panel.0);
                if let Some(trim) = &self.trim {
                    self.settings_window.apply_panel_font(trim.window.panel.0);
                }
                self.refresh_editor()?;
                if let Some(trim) = &mut self.trim {
                    trim.window.refresh(self.config.settings())?;
                }
                self.overlay.apply_visual_settings(self.config.settings())?;
                if let Some(window) = &self.log_window {
                    window.set_font(&self.config.settings().ui_font);
                }
            }
            SetFixedCenter(fixed) => self.edit(|s| s.fixed_center = fixed)?,
            SetLogLevel(level) => self.edit(|s| s.log_level = level)?,
            SetHoldKeyEnabled(enabled) => self.edit(|s| s.hold_key_enabled = enabled)?,
            RecordHoldKey | RecordHotkey | RecordRollingHotkey => {
                let Some(value) = capture_binding(
                    &mut self.hotkeys,
                    self.settings_window.hwnd(),
                    matches!(action, RecordHoldKey),
                    &self.config.settings().ui_font,
                )?
                else {
                    return Ok(());
                };
                let mut next = self.config.settings().clone();
                match action {
                    RecordHotkey => {
                        next.name = display_hotkey(&value);
                        next.hotkey = value;
                    }
                    RecordHoldKey => next.hold_key = value,
                    RecordRollingHotkey => next.rolling_buffer_hotkey = value,
                    _ => unreachable!(),
                }
                self.hotkeys.update(&mut self.config, next)?;
                self.settings_window.refresh(self.config.settings());
            }
            SetRollingSeconds(seconds) => self.edit(|s| s.rolling_buffer_seconds = seconds)?,
            SetOutputDevice(output) => self.edit(|s| {
                s.audio_output_device_name = output.as_ref().map(|id| id.name.clone());
                s.audio_output_device = output;
            })?,
            SetMic(name) => self.switch_microphone(name)?,
            SetMainPlayback(enabled) => {
                self.edit(|s| s.main_playback_enabled = enabled)?;
                if !enabled {
                    self.playback.stop_main();
                }
            }
            SetSidetone(enabled) => self.edit(|s| s.sidetone_enabled = enabled)?,
            SetListen(enabled) => self.set_listen(enabled)?,
            SaveKey(provider, text) => {
                let value = normalize_api_key_input(&text);
                if value.is_empty() {
                    return Err(io::Error::other("API 密钥不能为空"));
                }
                self.change_key(provider, Some(value))?;
            }
            DeleteKey(provider) => self.change_key(provider, None)?,
            RefreshVoices => {
                self.status("正在加载音色…".into());
                self.refresh_voices();
            }
            SelectVoice(Provider::Cartesia, voice) => self.edit(|s| {
                s.tts_providers.cartesia.voice_id = (!voice.id.is_empty()).then_some(voice.id);
                s.tts_providers.cartesia.voice_name =
                    (!voice.name.is_empty()).then_some(voice.name);
            })?,
            SelectVoice(Provider::Volcengine, voice) | SaveVolcengineVoice(voice) => {
                self.edit(|s| set_volc_voice(&mut s.tts_providers.volcengine.voice_list, voice))?;
            }
            DeleteVolcengineVoice(id) => self.edit(|s| {
                s.tts_providers
                    .volcengine
                    .voice_list
                    .retain(|voice| voice.id != id)
            })?,
            ExportBackup => self.backup(true)?,
            ImportBackup => self.backup(false)?,
            EditWheels => self.refresh_editor()?,
            DownloadVbCable => self.open_url("https://vb-audio.com/Cable/")?,
            OpenLogs => self.show_logs()?,
            RequestAudioDevices => self.load_audio_settings()?,
            Feedback(message) => self.failure(message),
            Closed => {
                self.toasts.clear();
                self.close_editor()?;
            }
        }
        Ok(())
    }

    fn rebuild_engine(&mut self) -> io::Result<()> {
        let settings = self.config.settings().clone();
        let api_key = self.vault.load(settings.active_tts_provider)?;
        self.voices_request = None;
        self.engine = Arc::new(EngineSettings { settings, api_key });
        Ok(())
    }

    fn change_key(&mut self, provider: Provider, value: Option<&str>) -> io::Result<()> {
        let previous = self.vault.load(provider)?;
        let write = |key| {
            if let Some(key) = key {
                self.vault.save(provider, key)
            } else {
                self.vault.delete(provider)
            }
        };
        write(value)?;
        if let Err(error) = self
            .config
            .update(|s| s.set_credential_status(provider, value.is_some()))
        {
            write(previous.as_deref())?;
            return Err(error);
        }
        self.settings_window.refresh(self.config.settings());
        self.rebuild_engine()?;
        if value.is_some() && provider == Provider::Cartesia {
            self.refresh_voices();
        }
        self.status("已更新 API 密钥".into());
        Ok(())
    }

    fn refresh_voices(&mut self) {
        if self.config.settings().active_tts_provider != Provider::Cartesia {
            return;
        }
        let id = self.id();
        self.voices_request = Some(id);
        self.playback.fetch_voices(id, self.engine.clone());
    }

    fn load_audio_settings(&mut self) -> io::Result<()> {
        // A transient WASAPI failure must not empty the whole settings page.
        let outputs = audio::list_outputs().unwrap_or_default();
        let inputs = audio::list_inputs().unwrap_or_default();
        let legacy_main = self.config.settings().virtual_output_device.clone();
        self.settings_window.set_vb_cable_available(
            audio::cable_endpoint_for(&outputs, legacy_main.as_deref()).is_some(),
        );
        self.settings_window.set_output_devices(
            outputs
                .into_iter()
                .filter(|e| !audio::is_cable_device(&e.name))
                .map(|e| AudioIdentity {
                    name: e.name,
                    host_api_name: Some("WASAPI".into()),
                    id: Some(e.id),
                })
                .collect(),
        );
        let state = self
            .config
            .settings()
            .mic_input_device
            .as_deref()
            .and_then(|name| audio::find_endpoint(&inputs, name))
            .and_then(|input| audio::read_listen(input).ok());
        self.settings_window
            .set_input_devices(inputs.into_iter().map(|e| e.name).collect());
        self.settings_window.set_listen_state(
            state.map(|state| (state.enabled, state.output_name, state.output_id.is_some())),
        );
        Ok(())
    }

    fn set_listen(&mut self, enabled: bool) -> io::Result<()> {
        let inputs = audio::list_inputs()?;
        let outputs = audio::list_outputs()?;
        let name = self
            .config
            .settings()
            .mic_input_device
            .as_deref()
            .unwrap_or_default();
        let input = audio::find_endpoint(&inputs, name)
            .ok_or_else(|| io::Error::other("请先选择麦克风"))?;
        let before = audio::read_listen(input)?;
        let legacy_main = self.config.settings().virtual_output_device.clone();
        let target = if enabled {
            Some(
                audio::cable_endpoint_for(&outputs, legacy_main.as_deref())
                    .ok_or_else(|| io::Error::other("未检测到 VB-CABLE"))?,
            )
        } else {
            None
        };
        audio::set_listen(input, target, enabled)?;
        if let Err(error) = self.edit(|s| s.audio_routing_enabled = enabled) {
            audio::restore_listen(input, &before)?;
            return Err(error);
        }
        self.status(
            if enabled {
                "音频侦听已启用"
            } else {
                "音频侦听已关闭"
            }
            .into(),
        );
        self.load_audio_settings()
    }

    fn switch_microphone(&mut self, name: Option<String>) -> io::Result<()> {
        let inputs = audio::list_inputs()?;
        let old = self
            .config
            .settings()
            .mic_input_device
            .as_deref()
            .and_then(|name| audio::find_endpoint(&inputs, name));
        let new = name
            .as_deref()
            .and_then(|name| audio::find_endpoint(&inputs, name));
        if old == new {
            return Ok(());
        }
        // Follow Wordy's routing switch, not a possibly manual Windows listen state.
        let enabled = self.config.settings().audio_routing_enabled;
        // Read Windows state only when routing is on. Some devices do not expose
        // the listen properties, and that must not block a plain mic switch.
        let (old_state, new_state) = if enabled {
            (
                old.map(audio::read_listen).transpose()?,
                new.map(audio::read_listen).transpose()?,
            )
        } else {
            (None, None)
        };
        let outputs = audio::list_outputs().unwrap_or_default();
        let legacy_main = self.config.settings().virtual_output_device.clone();
        let cable = audio::cable_endpoint_for(&outputs, legacy_main.as_deref());
        if enabled && new.is_some() && cable.is_none() {
            return Err(io::Error::other("未检测到 VB-CABLE"));
        }
        // Without Wordy routing, the old microphone's Windows listen state is the
        // user's own setting; switching the Wordy selection must not turn it off.
        if enabled
            && let Some(old) = old
            && old_state.as_ref().is_some_and(|state| state.enabled)
        {
            audio::set_listen(old, None, false)?;
        }
        let result = (|| {
            if enabled && let Some(new) = new {
                audio::set_listen(new, cable, true)?;
            }
            self.edit(|s| s.mic_input_device = name)
        })();
        if let Err(error) = result {
            if enabled {
                if let Some((input, state)) = new.zip(new_state.as_ref()) {
                    audio::restore_listen(input, state)?;
                }
                if let Some((input, state)) = old.zip(old_state.as_ref()) {
                    audio::restore_listen(input, state)?;
                }
            }
            return Err(error);
        }
        self.load_audio_settings()
    }

    fn backup(&mut self, export: bool) -> io::Result<()> {
        if self.backup_running {
            return Err(io::Error::other("正在处理备份，请稍候"));
        }
        let owner = self.settings_window.hwnd();
        if !export && (self.recording.is_some() || self.pending_saves > 0 || self.trim.is_some()) {
            return Err(io::Error::other("请先结束录制或音频导入，再导入备份"));
        }
        let base = if cfg!(feature = "packaged") {
            std::env::current_exe()?.parent().unwrap().to_owned()
        } else {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        };
        let title = if export {
            "导出备份"
        } else {
            "导入备份"
        };
        let Some(path) = dialog::file(
            owner,
            title,
            BACKUP_FILTER,
            &base,
            Some("wordybackup"),
            export,
        ) else {
            return Ok(());
        };
        if !export
            && !dialog::confirm(
                owner,
                title,
                "将替换全部设置和 API 密钥。成功后 Wordy 会退出，请重新启动。是否继续？",
                &self.config.settings().ui_font,
            )
        {
            return Ok(());
        }
        let Some(password) = dialog::prompt(
            owner,
            title,
            "备份密码",
            "",
            true,
            &self.config.settings().ui_font,
        ) else {
            return Ok(());
        };
        if password.is_empty() {
            return Err(io::Error::other("备份密码不能为空"));
        }
        if export {
            let Some(check) = dialog::prompt(
                owner,
                title,
                "确认备份密码",
                "",
                true,
                &self.config.settings().ui_font,
            ) else {
                return Ok(());
            };
            if check != password {
                return Err(io::Error::other("两次输入的密码不一致"));
            }
            self.config.flush()?;
            let settings = self.config.settings().clone();
            let config_path = self.config.path().to_path_buf();
            let events = self.jobs.clone();
            std::thread::Builder::new()
                .name("WordyBackupExport".into())
                .spawn(move || {
                    let result = backup::export_backup(
                        &config_path,
                        &settings,
                        &WindowsCredentials,
                        &path,
                        &password,
                    );
                    let _ = events.send(Background::Backup {
                        import: false,
                        result,
                    });
                })?;
            self.backup_running = true;
            self.status("正在导出备份…".into());
        } else {
            self.config.flush()?;
            let config_path = self.config.path().to_path_buf();
            let events = self.jobs.clone();
            std::thread::Builder::new()
                .name("WordyBackupImport".into())
                .spawn(move || {
                    // A fresh store writes the imported file; the UI store stays
                    // untouched until the completion event reloads it.
                    let result = (|| {
                        let mut config = ConfigStore::load(&config_path)?;
                        backup::import_backup(&mut config, &WindowsCredentials, &path, &password)
                    })();
                    let _ = events.send(Background::Backup {
                        import: true,
                        result,
                    });
                })?;
            self.backup_running = true;
            self.status("正在导入备份…".into());
        }
        Ok(())
    }

    pub(super) fn open_url(&self, url: &str) -> io::Result<()> {
        let url = wide(url);
        let result = unsafe {
            ShellExecuteW(
                self.settings_window.hwnd(),
                w!("open"),
                url.as_ptr(),
                ptr::null(),
                ptr::null(),
                SW_SHOWNORMAL,
            )
        };
        if result as isize <= 32 {
            Err(io::Error::other("无法打开浏览器"))
        } else {
            Ok(())
        }
    }
}

fn set_volc_voice(voices: &mut Vec<Voice>, voice: Voice) {
    if !voice.id.trim().is_empty() {
        voices.retain(|item| item.id != voice.id);
        voices.insert(0, voice);
    }
}
