use super::*;

impl App {
    fn edit(&mut self, update: impl FnOnce(&mut Settings)) -> io::Result<()> {
        self.config.update(update)?;
        self.ui_dirty = true;
        Ok(())
    }

    fn edit_debounced(&mut self, update: impl FnOnce(&mut Settings)) -> io::Result<()> {
        self.config.update_debounced(update)?;
        self.ui_dirty = true;
        Ok(())
    }

    pub(super) fn settings_action(&mut self, action: SettingsAction) -> io::Result<()> {
        use SettingsAction::*;
        // 后台备份独占配置与凭据；Closed 只清理临时 UI 状态。
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
                self.overlay.apply_visual_settings(self.config.settings())?;
            }
            SetFixedCenter(fixed) => self.edit(|s| s.fixed_center = fixed)?,
            SetLogLevel(level) => self.edit(|s| s.log_level = level)?,
            SetHoldKeyEnabled(enabled) => self.edit(|s| s.hold_key_enabled = enabled)?,
            RecordHoldKey | RecordHotkey | RecordRollingHotkey => {
                let Some(value) = self.capture_binding(matches!(action, RecordHoldKey))? else {
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
                self.ui_dirty = true;
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
            // 密钥存在 Windows 凭据管理器，删除后只能重新输入。
            DeleteKey(provider) => {
                if self.confirm_dialog("删除密钥", "删除已保存的 API 密钥？删除后需要重新输入。")
                {
                    self.change_key(provider, None)?;
                }
            }
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
            // 删除音色不可撤销，先确认；确认框由 React 渲染。
            DeleteVolcengineVoice(id) => {
                if self.confirm_dialog("删除音色", "删除当前音色？") {
                    self.edit(|s| {
                        s.tts_providers
                            .volcengine
                            .voice_list
                            .retain(|voice| voice.id != id)
                    })?;
                }
            }
            ExportBackup => self.backup(true)?,
            ImportBackup => self.backup(false)?,
            DownloadVbCable => self.open_url("https://vb-audio.com/Cable/")?,
            OpenLogs => self.show_logs()?,
            RequestAudioDevices => self.load_audio_settings()?,
            Feedback(message) => self.failure(message),
            SelectWheel(index) => self.wheel_select(index)?,
            AddWheel => self.wheel_add()?,
            RemoveWheel(index) => self.wheel_remove(index)?,
            SetWheelName(index, value) => self.wheel_set_name(index, &value)?,
            RecordWheelHotkey(index) => self.wheel_record_hotkey(index)?,
            SetSlotLabel(index, slot, value) => self.slot_set_label(index, slot, &value)?,
            SetSlotText(index, slot, value) => self.slot_set_text(index, slot, &value)?,
            SetSlotVolume(index, slot, value) => self.slot_set_volume(index, slot, value)?,
            SetSlotMode(index, slot, text) => self.slot_set_mode(index, slot, text)?,
            ClearSlot(index, slot) => self.slot_clear(index, slot)?,
            ImportSlotAudio(index, slot) => self.slot_import(index, slot)?,
            CropSlotAudio(index, slot) => self.slot_crop(index, slot)?,
            PreviewSlot(index, slot) => self.slot_preview(index, slot)?,
            TrimPreview(id, start, end, gain) => self.trim_preview(id, start, end, gain)?,
            TrimStop(id) => self.trim_stop(id),
            TrimSave(id, start, end, label, gain, wheel, slot) => {
                self.trim_save(id, start, end, &label, gain, wheel, slot)?
            }
            TrimCancel(id) => self.trim_cancel(id),
            Closed => (),
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
        self.ui_dirty = true;
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
        self.cable_available =
            audio::cable_endpoint_for(&outputs, legacy_main.as_deref()).is_some();
        self.outputs = outputs
            .into_iter()
            .filter(|e| !audio::is_cable_device(&e.name))
            .map(|e| AudioIdentity {
                name: e.name,
                host_api_name: Some("WASAPI".into()),
                id: Some(e.id),
            })
            .collect();
        let state = self
            .config
            .settings()
            .mic_input_device
            .as_deref()
            .and_then(|name| audio::find_endpoint(&inputs, name))
            .and_then(|input| audio::read_listen(input).ok());
        self.microphones = inputs.into_iter().map(|e| e.name).collect();
        self.listen_state = state.map(|state| ListenStatus {
            enabled: state.enabled,
            output: state.output_name,
            output_available: state.output_id.is_some(),
        });
        self.ui_dirty = true;
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
        let owner = self.host;
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
            && !self.confirm_dialog(
                title,
                "将替换全部设置和 API 密钥。成功后 Wordy 会退出，请重新启动。是否继续？",
            )
        {
            return Ok(());
        }
        let Some(password) = self.prompt_dialog(title, "备份密码", true) else {
            return Ok(());
        };
        if password.is_empty() {
            return Err(io::Error::other("备份密码不能为空"));
        }
        if export {
            let Some(check) = self.prompt_dialog(title, "确认备份密码", true) else {
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
                self.host,
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
