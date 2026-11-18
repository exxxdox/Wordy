use std::collections::{HashMap, VecDeque};
use std::io;
use std::path::PathBuf;
use std::ptr;
use std::sync::{Arc, mpsc};
use std::time::Instant;

use windows_sys::Win32::Foundation::{HINSTANCE, HWND};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::HiDpi::{
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, SetProcessDpiAwarenessContext,
};
use windows_sys::Win32::UI::Shell::ShellExecuteW;
use windows_sys::Win32::UI::WindowsAndMessaging::*;
use windows_sys::core::w;

use crate::audio::{self, Endpoint, Recording};
use crate::config::{AudioIdentity, ConfigStore, Settings, Voice};
use crate::credentials::{CredentialStore, Provider, WindowsCredentials, normalize_api_key_input};
use crate::desktop::*;
use crate::ffmpeg;
use crate::file_log::FileLog;
use crate::hotkey::{display_hotkey, parse_hotkey, windows::Registration};
use crate::instance::{acquire_single_instance, show_duplicate_instance_message};
use crate::log_window::LogWindow;
use crate::panels::{self, Editor, Trim};
use crate::playback::{EngineSettings, Event, Playback};
use crate::settings_window::{SETTINGS_ACTION_MESSAGE, SettingsAction, SettingsWindow};
use crate::status_toast::{Toasts, Tone};
use crate::wheel::WheelStyle;
use crate::wheel_window::WheelWindow;
use crate::{backup, dialog};

mod recording;

use self::recording::{RecordingResult, TrimSession};

const CONTROLS_TIMER: usize = 1;
const LOG_LIMIT: usize = 5000;
const BACKUP_FILTER: &str = "Wordy 加密备份 (*.wordybackup)\0*.wordybackup\0";

pub fn run() -> io::Result<()> {
    if std::env::args_os().nth(1).as_deref() == Some(std::ffi::OsStr::new("--smoke-test")) {
        return smoke_test();
    }
    if !acquire_single_instance()? {
        show_duplicate_instance_message();
        return Ok(());
    }
    unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };
    enable_dark_mode();
    let instance = unsafe { GetModuleHandleW(ptr::null()) };
    if instance.is_null() {
        return Err(io::Error::last_os_error());
    }
    let host = Host(host_window(instance)?);
    let config = ConfigStore::load_default()?;
    let mut app = App::new(instance, host.0, config)?;
    app.event_loop()
}

fn smoke_test() -> io::Result<()> {
    let instance = unsafe { GetModuleHandleW(ptr::null()) };
    let host = Host(host_window(instance)?);
    let settings = Settings::default();
    let mut settings_window = SettingsWindow::new(host.0, &settings)?;
    let _input = Overlay::new(instance, host.0)?;
    let _wheel = WheelWindow::new(instance).map_err(io::Error::other)?;
    let editor = Editor::new_embedded(host.0, settings_window.hwnd(), &settings)?;
    settings_window.set_wheel_panel(editor.panel.0);
    let trim = Trim::new(host.0, settings_window.hwnd(), &settings, None, 1.0, 1.0)?;
    if unsafe { IsChild(settings_window.hwnd(), editor.panel.0) } == 0
        || editor.panel.text(panels::GAIN) != "100"
        || trim.panel.text(panels::END) != "1.000"
    {
        return Err(io::Error::other("设置或截取窗口未正确初始化"));
    }
    Ok(())
}

struct Host(HWND);

impl Drop for Host {
    fn drop(&mut self) {
        unsafe { DestroyWindow(self.0) };
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum Action {
    Overlay,
    Rolling,
    Wheel(usize),
}

type Binding = (u32, u32);

struct Registered {
    action: Action,
    id: i32,
    _registration: Registration,
}

#[derive(Default)]
struct Hotkeys {
    current: HashMap<Binding, Registered>,
    next_id: i32,
}

impl Hotkeys {
    fn register_new(&mut self, binding: Binding, action: Action) -> io::Result<Registered> {
        // Register replacements before dropping old bindings so failed switches preserve working keys.
        self.next_id = self.next_id % 0xbfff + 1;
        while self.current.values().any(|entry| entry.id == self.next_id) {
            self.next_id = self.next_id % 0xbfff + 1;
        }
        let id = self.next_id;
        let registration = Registration::register(id, binding)?;
        Ok(Registered {
            action,
            id,
            _registration: registration,
        })
    }

    fn action(&self, id: usize) -> Option<(Action, u16)> {
        self.current.iter().find_map(|(&(_, key), entry)| {
            (entry.id as usize == id).then_some((entry.action, key as u16))
        })
    }

    fn startup(&mut self, settings: &Settings) -> Vec<String> {
        let mut failures = Vec::new();
        for (action, value) in configured_keys(settings) {
            let result = (|| {
                let key = parse_hotkey(value).map_err(io::Error::other)?;
                if self.current.contains_key(&key) {
                    return Err(io::Error::other("快捷键互相冲突"));
                }
                let registration = self.register_new(key, action)?;
                self.current.insert(key, registration);
                Ok(())
            })();
            if let Err(error) = result {
                failures.push(format!("快捷键 {value} 未启用：{error}"));
            }
        }
        failures
    }

    fn update(&mut self, config: &mut ConfigStore, next: Settings) -> io::Result<()> {
        let keys = checked_keys(&next)?;
        let mut added = Vec::new();
        for (&binding, &action) in &keys {
            if !self.current.contains_key(&binding) {
                added.push((binding, self.register_new(binding, action)?));
            }
        }
        config.replace(next)?;
        self.current.retain(|key, registration| {
            if let Some(action) = keys.get(key) {
                registration.action = *action;
                true
            } else {
                false
            }
        });
        self.current.extend(added);
        Ok(())
    }
}

fn configured_keys(settings: &Settings) -> impl Iterator<Item = (Action, &str)> {
    [
        (Action::Overlay, settings.hotkey.as_str()),
        (Action::Rolling, settings.rolling_buffer_hotkey.as_str()),
        (Action::Wheel(0), settings.chat_wheel_hotkey.as_str()),
    ]
    .into_iter()
    .chain(
        settings
            .additional_chat_wheels
            .iter()
            .enumerate()
            .map(|(index, wheel)| (Action::Wheel(index + 1), wheel.hotkey.as_str())),
    )
}

fn checked_keys(settings: &Settings) -> io::Result<HashMap<Binding, Action>> {
    let mut keys = HashMap::new();
    for (action, value) in configured_keys(settings) {
        let binding = parse_hotkey(value).map_err(io::Error::other)?;
        if keys.insert(binding, action).is_some() {
            return Err(io::Error::other("快捷键互相冲突"));
        }
    }
    Ok(keys)
}

struct App {
    host: HWND,
    config: ConfigStore,
    vault: WindowsCredentials,
    engine: Arc<EngineSettings>,
    playback: Playback,
    hotkeys: Hotkeys,
    tray: Tray,
    overlay: Overlay,
    wheel: WheelWindow,
    active_wheel: Option<usize>,
    editor: Editor,
    settings_window: SettingsWindow,
    toasts: Toasts,
    recording: Option<Recording>,
    trim: Option<TrimSession>,
    previews: HashMap<u64, tempfile::NamedTempFile>,
    editor_preview: Option<(u64, bool)>,
    jobs: mpsc::Sender<Background>,
    results: mpsc::Receiver<Background>,
    pending_saves: usize,
    voices_request: Option<u64>,
    ffmpeg_installing: bool,
    next_job: u64,
    logs: VecDeque<String>,
    file_log: Option<FileLog>,
    log_window: Option<LogWindow>,
}

enum Background {
    Recording(RecordingResult),
    NotificationFailed(String),
    FfmpegInstalled(io::Result<()>),
}

impl App {
    fn new(instance: HINSTANCE, host: HWND, config: ConfigStore) -> io::Result<Self> {
        let vault = WindowsCredentials;
        let engine = Arc::new(EngineSettings {
            settings: config.settings().clone(),
            api_key: vault.load(config.settings().active_tts_provider)?,
        });
        let playback = Playback::start()?;
        let mut hotkeys = Hotkeys::default();
        let failures = hotkeys.startup(config.settings());
        let tray = Tray::install(host)?;
        let overlay = Overlay::new(instance, host)?;
        let wheel = WheelWindow::new(instance).map_err(io::Error::other)?;
        let mut settings_window = SettingsWindow::new(host, config.settings())?;
        let editor = Editor::new_embedded(host, settings_window.hwnd(), config.settings())?;
        settings_window.set_wheel_panel(editor.panel.0);
        let (jobs, results) = mpsc::channel();
        let mut app = Self {
            host,
            config,
            vault,
            engine,
            playback,
            hotkeys,
            tray,
            overlay,
            wheel,
            active_wheel: None,
            editor,
            settings_window,
            toasts: Toasts::new(),
            recording: None,
            trim: None,
            previews: HashMap::new(),
            editor_preview: None,
            jobs,
            results,
            pending_saves: 0,
            voices_request: None,
            ffmpeg_installing: false,
            next_job: 0,
            logs: VecDeque::new(),
            file_log: None,
            log_window: None,
        };
        if cfg!(feature = "packaged") {
            match FileLog::beside_executable() {
                Ok(log) => app.file_log = Some(log),
                Err(error) => app.log(format!("无法写入日志文件：{error}")),
            }
        }
        if let Some(warning) = app.config.take_load_warning() {
            app.notify(warning);
        }
        for failure in failures {
            app.notify(failure);
        }
        let legacy_main = app.config.settings().virtual_output_device.clone();
        if app.config.settings().main_playback_enabled
            && audio::list_outputs().ok().is_none_or(|outputs| {
                audio::cable_endpoint_for(&outputs, legacy_main.as_deref()).is_none()
            })
        {
            app.notify("未检测到 VB-CABLE，请在 Wordy 的音频设置中下载并安装。".into());
        }
        if app.config.settings().active_voice_id().is_some() && app.engine.api_key.is_some() {
            app.playback.prewarm(app.engine.clone());
        }
        let settings = app.config.settings().clone();
        app.log(format!(
            "Wordy 已启动（输入框 {}，轮盘 {} 个，后台录制 {}）",
            settings.hotkey,
            settings.wheel_count(),
            settings.rolling_buffer_hotkey
        ));
        Ok(app)
    }

    fn event_loop(&mut self) -> io::Result<()> {
        let explorer_restart = unsafe { RegisterWindowMessageW(w!("TaskbarCreated")) };
        if unsafe { SetTimer(self.host, CONTROLS_TIMER, 16, None) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let mut message = MSG::default();
        loop {
            let result = unsafe { GetMessageW(&mut message, ptr::null_mut(), 0, 0) };
            if result == 0 {
                break;
            }
            if result < 0 {
                return Err(io::Error::last_os_error());
            }
            let result = match message.message {
                WM_HOTKEY => self.hotkey(message.wParam),
                WM_TIMER if message.hwnd == self.host => self.tick(),
                TRAY_EVENT if message.hwnd == self.host => match message.lParam as u32 {
                    WM_RBUTTONUP | WM_CONTEXTMENU => self.tray.open_menu(),
                    WM_LBUTTONDBLCLK => self.show_settings(),
                    _ => Ok(()),
                },
                TRAY_COMMAND if message.hwnd == self.host => match message.wParam {
                    TRAY_OVERLAY => self.toggle_overlay(),
                    TRAY_SETTINGS => self.show_settings(),
                    TRAY_LOGS => self.show_logs(),
                    TRAY_EXIT => break,
                    _ => Ok(()),
                },
                OVERLAY_MESSAGE if message.hwnd == self.host => self.overlay_action(message.wParam),
                SETTINGS_ACTION_MESSAGE if message.hwnd == self.host => {
                    let action = self.settings_window.action(message.wParam as u16);
                    let result = action.map_or(Ok(()), |action| self.settings_action(action));
                    if result.is_err() {
                        self.settings_window.refresh(self.config.settings());
                    }
                    result
                }
                panels::EDITOR_MESSAGE if message.hwnd == self.host => {
                    self.editor_action(message.wParam, message.lParam as HWND)
                }
                panels::TRIM_MESSAGE if message.hwnd == self.host => {
                    self.trim_action(message.wParam, message.lParam as HWND)
                }
                msg if msg != 0 && msg == explorer_restart => self.tray.add(),
                _ => {
                    let handled = self
                        .trim
                        .as_ref()
                        .is_some_and(|trim| trim.window.accepts(&message))
                        || self.settings_window.handle_message(&message);
                    if !handled {
                        unsafe {
                            TranslateMessage(&message);
                            DispatchMessageW(&message);
                        }
                    }
                    Ok(())
                }
            };
            if let Err(error) = result {
                self.report_error("操作失败", error);
            }
        }
        unsafe { KillTimer(self.host, CONTROLS_TIMER) };
        self.wheel.cancel();
        self.playback.stop_speech();
        self.playback.stop_clip();
        self.trim = None;
        if let Some(recording) = self.recording.take() {
            self.tray.set_recording(false);
            if let Err(error) = recording.cancel() {
                self.log(format!("停止后台录制失败：{error}"));
            }
        }
        // A submitted save owns the only temporary recording. Finish it before dropping results.
        while self.pending_saves > 0 {
            let result = self.results.recv().map_err(io::Error::other)?;
            if let Err(error) = self.complete_background(result, true) {
                self.log(format!("保存失败：{error}"));
            }
        }
        self.config.flush()
    }

    fn tick(&mut self) -> io::Result<()> {
        if let Err(error) = self.toasts.tick(
            self.settings_window.hwnd(),
            self.settings_window.toast_anchor(),
        ) {
            self.toasts.clear();
            self.log_at("WARNING", format!("设置反馈显示失败：{error}"));
        }
        let busy = self.pending_saves > 0 || self.trim.is_some() || self.editor_preview.is_some();
        self.editor.busy(busy);
        if let Some(slot) = self.wheel.tick()
            && let Some(wheel) = self.active_wheel.take()
        {
            self.play_slot(wheel, slot)?;
        }
        self.wheel.present().map_err(io::Error::other)?;
        self.config.save_if_due(Instant::now())?;
        while let Ok(event) = self.playback.events.try_recv() {
            match event {
                Event::Finished { id, result } => {
                    self.previews.remove(&id);
                    if let Some(trim) = &mut self.trim {
                        trim.playback_progress(id, None);
                    }
                    if self
                        .editor_preview
                        .is_some_and(|(preview, _)| preview == id)
                    {
                        self.editor_preview = None;
                        self.editor.panel.set_text(panels::PLAY, "试听");
                    }
                    if let Err(error) = result {
                        self.report_error("播放失败", error);
                    }
                }
                Event::Progress { id, seconds } => {
                    if let Some(trim) = &mut self.trim {
                        trim.playback_progress(id, Some(seconds));
                    }
                }
                Event::Voices { id, result } if Some(id) == self.voices_request => {
                    self.voices_request = None;
                    match result {
                        Ok(voices) => {
                            let count = voices.len();
                            self.settings_window.set_cartesia_voices(voices);
                            self.status(format!("已加载 {count} 个音色"));
                        }
                        Err(error) => self.failure(format!("获取音色列表失败：{error}")),
                    }
                }
                Event::Warning { target, message } => {
                    self.log_target("WARNING", target, message);
                }
                Event::Log {
                    target,
                    level,
                    message,
                } => self.log_target(level, target, message),
                _ => (),
            }
        }
        while let Ok(result) = self.results.try_recv() {
            self.complete_background(result, false)?;
        }
        Ok(())
    }

    fn complete_background(&mut self, result: Background, exiting: bool) -> io::Result<()> {
        match result {
            Background::NotificationFailed(message) => {
                // Fall back to the tray when the notification-center sender is unavailable.
                self.tray.notify_balloon(&message);
                self.log_at("WARNING", "Windows 通知发送失败，已改用托盘提示".into());
            }
            Background::FfmpegInstalled(result) => {
                self.ffmpeg_installing = false;
                if !exiting {
                    match result {
                        Ok(()) => {
                            let message = "FFmpeg 已安装，请重试刚才的音频操作".into();
                            if self.settings_visible() {
                                self.status(message);
                            } else {
                                self.notify(message);
                            }
                        }
                        Err(error) => self.failure(format!("安装 FFmpeg 失败：{error}")),
                    }
                }
            }
            Background::Recording(result) => self.complete_recording(result, exiting)?,
        }
        Ok(())
    }

    fn hotkey(&mut self, id: usize) -> io::Result<()> {
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

    fn play_slot(&mut self, index: usize, slot: usize) -> io::Result<()> {
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

    fn toggle_overlay(&mut self) -> io::Result<()> {
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

    fn overlay_action(&mut self, action: usize) -> io::Result<()> {
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

    fn show_settings(&mut self) -> io::Result<()> {
        // Settings takes over the foreground; the floating bar must not stay on top of it.
        self.overlay.hide(false);
        self.settings_window.refresh(self.config.settings());
        self.refresh_editor()?;
        self.settings_window.show();
        Ok(())
    }

    fn show_logs(&mut self) -> io::Result<()> {
        match &self.log_window {
            Some(window) => window.show_existing(),
            None => {
                let window = LogWindow::open(self.host)?;
                let lines = self.logs.iter().cloned().collect::<Vec<_>>();
                window.set_lines(&lines);
                self.log_window = Some(window);
            }
        }
        Ok(())
    }

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

    fn settings_action(&mut self, action: SettingsAction) -> io::Result<()> {
        use SettingsAction::*;
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
                let Some(value) = capture_binding(
                    &mut self.hotkeys,
                    self.settings_window.hwnd(),
                    matches!(action, RecordHoldKey),
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
                })
                .collect(),
        );
        let state = self
            .config
            .settings()
            .mic_input_device
            .as_deref()
            .and_then(|name| find(&inputs, name))
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
        let input = find(&inputs, name).ok_or_else(|| io::Error::other("请先选择麦克风"))?;
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
            .and_then(|name| find(&inputs, name));
        let new = name.as_deref().and_then(|name| find(&inputs, name));
        if old == new {
            return Ok(());
        }
        let old_state = old.map(audio::read_listen).transpose()?;
        let new_state = new.map(audio::read_listen).transpose()?;
        // Follow Wordy's routing switch, not a possibly manual Windows listen state.
        let enabled = self.config.settings().audio_routing_enabled;
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
            if enabled && let Some((input, state)) = new.zip(new_state.as_ref()) {
                audio::restore_listen(input, state)?;
            }
            if let Some((input, state)) = old.zip(old_state.as_ref()) {
                audio::restore_listen(input, state)?;
            }
            return Err(error);
        }
        self.load_audio_settings()
    }

    fn backup(&mut self, export: bool) -> io::Result<()> {
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
            )
        {
            return Ok(());
        }
        let Some(password) = dialog::prompt(owner, title, "备份密码", "", true) else {
            return Ok(());
        };
        if password.is_empty() {
            return Err(io::Error::other("备份密码不能为空"));
        }
        if export {
            let Some(check) = dialog::prompt(owner, title, "确认备份密码", "", true) else {
                return Ok(());
            };
            if check != password {
                return Err(io::Error::other("两次输入的密码不一致"));
            }
            backup::export_backup(&mut self.config, &self.vault, &path, &password)?;
            self.status("备份已导出".into());
        } else {
            backup::import_backup(&mut self.config, &self.vault, &path, &password)?;
            self.status("备份已导入，请重新启动 Wordy".into());
            unsafe { PostQuitMessage(0) };
        }
        Ok(())
    }

    fn open_url(&self, url: &str) -> io::Result<()> {
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

    fn close_editor(&mut self) -> io::Result<()> {
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

    fn refresh_editor(&mut self) -> io::Result<()> {
        self.editor.refresh(self.config.settings())
    }

    fn editor_action(&mut self, command: usize, hwnd: HWND) -> io::Result<()> {
        use panels::*;
        const CANCEL: u16 = IDCANCEL as u16;
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
                let Some(value) =
                    dialog::prompt(hwnd, "新增轮盘", "快捷键（例如 f9）", "f9", false)
                else {
                    return Ok(());
                };
                let hotkey = value.trim().to_owned();
                parse_hotkey(&hotkey)
                    .map_err(|error| io::Error::other(format!("快捷键无效：{error}")))?;
                let mut next = self.config.settings().clone();
                next.additional_chat_wheels
                    .push(crate::config::Wheel::new(hotkey));
                next.chat_wheel_last_index = next.wheel_count() - 1;
                self.hotkeys.update(&mut self.config, next)?;
                editor.wheel = self.config.settings().chat_wheel_last_index;
            }
            REMOVE if notification == 0 => {
                let mut next = self.config.settings().clone();
                if next.wheel_count() == 1 {
                    return Err(io::Error::other("至少保留一个轮盘"));
                }
                if !dialog::confirm(hwnd, "删除轮盘", "删除此轮盘及其槽位？") {
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
                let Some(hotkey) = capture_binding(&mut self.hotkeys, hwnd, false)? else {
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
                    && !dialog::confirm(hwnd, "切换为文字", "切换为文字会删除该槽音频，继续？")
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

    fn id(&mut self) -> u64 {
        self.next_job += 1;
        self.next_job
    }

    fn log(&mut self, message: String) {
        self.log_at("INFO", message);
    }

    fn log_at(&mut self, level: &str, message: String) {
        self.log_target(level, "wordy", message);
    }

    fn log_target(&mut self, level: &str, target: &str, message: String) {
        let levels = crate::config::LOG_LEVELS;
        let rank = |value| levels.iter().position(|item| *item == value).unwrap_or(1);
        if rank(level) < rank(self.config.settings().log_level.as_str()) {
            return;
        }
        let now = local_time();
        let message = format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02} [{level}] {target}: {message}",
            now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond
        );
        eprintln!("{message}");
        let trim = self.logs.len() == LOG_LIMIT;
        if trim {
            self.logs.pop_front();
        }
        if let Some(log) = &mut self.file_log
            && let Err(error) = log.append(&message)
        {
            eprintln!("写入日志失败：{error}");
        }
        if let Some(window) = &self.log_window {
            if trim {
                window.trim_front();
            }
            window.append(&message);
        }
        self.logs.push_back(message);
    }

    fn status(&mut self, message: String) {
        self.toasts.push(&message, Tone::Success);
        self.log(message);
    }

    fn report_error(&mut self, action: &str, error: io::Error) {
        if error
            .get_ref()
            .is_some_and(|cause| cause.is::<audio::MissingFfmpeg>())
        {
            self.log_at("WARNING", format!("{action}：{error}"));
            if let Err(error) = self.offer_ffmpeg_install() {
                self.failure(format!("无法安装 FFmpeg：{error}"));
            }
        } else {
            self.failure(format!("{action}：{error}"));
        }
    }

    fn offer_ffmpeg_install(&mut self) -> io::Result<()> {
        if self.ffmpeg_installing {
            return Ok(());
        }
        if ffmpeg::find().is_some() {
            self.status("已检测到 FFmpeg，请重试刚才的音频操作".into());
            return Ok(());
        }
        let owner = if let Some(trim) = &self.trim {
            trim.window.panel.0
        } else if self.settings_visible() {
            self.settings_window.hwnd()
        } else {
            ptr::null_mut()
        };
        let Some(winget) = ffmpeg::winget() else {
            if dialog::confirm(
                owner,
                "缺少 FFmpeg",
                "此音频需要 FFmpeg。系统未找到 WinGet，是否打开 FFmpeg 下载页面？安装后请将其目录加入 PATH。",
            ) {
                self.open_url("https://ffmpeg.org/download.html")?;
            }
            return Ok(());
        };
        if dialog::confirm(
            owner,
            "缺少 FFmpeg",
            "此音频需要 FFmpeg。是否通过 Windows 程序包管理器（WinGet）下载并安装？",
        ) {
            let events = self.jobs.clone();
            // Installation can take time. Let the user keep working and report completion on the UI thread.
            std::thread::Builder::new()
                .name("WordyInstallFfmpeg".into())
                .spawn(move || {
                    let result = ffmpeg::install(&winget);
                    let _ = events.send(Background::FfmpegInstalled(result));
                })?;
            self.ffmpeg_installing = true;
            self.status("正在下载并安装 FFmpeg…".into());
        }
        Ok(())
    }

    fn failure(&mut self, message: String) {
        // Report through the toast/tray only; forcing the settings window open
        // on every background error made a plain launch look like a request for it.
        let settings_visible =
            self.settings_visible() && unsafe { IsIconic(self.settings_window.hwnd()) == 0 };
        if settings_visible {
            self.toasts.push(&message, Tone::Error);
        } else {
            // Toast cards only exist over the settings window; without it the
            // next tick would clear them before the user could read one.
            self.tray.notify(&message);
        }
        self.log_at("ERROR", message);
    }

    fn settings_visible(&self) -> bool {
        unsafe { IsWindowVisible(self.settings_window.hwnd()) != 0 }
    }

    fn notify(&mut self, message: String) {
        let events = self.jobs.clone();
        let text = message.clone();
        // Native toasts shell out through PowerShell; keep the GUI thread free.
        let _ = std::thread::Builder::new()
            .name("WordyToast".into())
            .spawn(move || {
                if notify_toast(&text).is_err() {
                    let _ = events.send(Background::NotificationFailed(text));
                }
            });
        self.log(message);
    }
}

fn find<'a>(endpoints: &'a [Endpoint], name: &str) -> Option<&'a Endpoint> {
    let target = name.trim().to_lowercase();
    if target.is_empty() {
        return None;
    }
    let normalized = |endpoint: &Endpoint| endpoint.name.trim().to_lowercase();
    if let Some(exact) = endpoints
        .iter()
        .find(|endpoint| normalized(endpoint) == target)
    {
        return Some(exact);
    }
    // A renamed device keeps working when the saved name is still a unique fragment.
    let mut partial = endpoints
        .iter()
        .filter(|endpoint| normalized(endpoint).contains(&target));
    let found = partial.next()?;
    partial.next().is_none().then_some(found)
}

fn set_volc_voice(voices: &mut Vec<Voice>, voice: Voice) {
    if !voice.id.trim().is_empty() {
        voices.retain(|item| item.id != voice.id);
        voices.insert(0, voice);
    }
}

fn gain(value: &str) -> io::Result<f64> {
    value
        .trim()
        .parse::<f64>()
        .ok()
        .filter(|value| value.is_finite())
        .map(|value| value.clamp(0.0, 300.0) / 100.0)
        .ok_or_else(|| io::Error::other("请输入有效音量"))
}

fn capture_binding(hotkeys: &mut Hotkeys, owner: HWND, single: bool) -> io::Result<Option<String>> {
    // Clear global registrations while capturing so the old shortcut cannot open a second surface.
    let previous: Vec<_> = hotkeys
        .current
        .drain()
        .map(|(binding, registered)| (binding, registered.action))
        .collect();
    let binding = dialog::record_key(owner, single);
    let mut failures = Vec::new();
    for (key, action) in previous {
        match hotkeys.register_new(key, action) {
            Ok(registration) => {
                hotkeys.current.insert(key, registration);
            }
            Err(error) => failures.push(format!("恢复快捷键失败：{error}")),
        }
    }
    if failures.is_empty() {
        Ok(binding)
    } else {
        Err(io::Error::other(failures.join("；")))
    }
}

fn local_time() -> windows_sys::Win32::Foundation::SYSTEMTIME {
    use windows_sys::Win32::Foundation::SYSTEMTIME;
    use windows_sys::Win32::System::SystemInformation::GetLocalTime;
    let mut now = SYSTEMTIME::default();
    unsafe { GetLocalTime(&mut now) };
    now
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hotkey_collision_checks_aliases_across_all_entry_points() {
        let mut settings = Settings {
            hotkey: "control+page down".into(),
            ..Settings::default()
        };
        settings
            .additional_chat_wheels
            .push(crate::config::Wheel::new("ctrl+page_down"));
        assert!(checked_keys(&settings).is_err());
        settings.additional_chat_wheels[0].hotkey = "f9".into();
        assert_eq!(checked_keys(&settings).unwrap().len(), 4);
    }

    #[test]
    fn native_dialogs_can_be_created_without_touching_live_config() {
        smoke_test().unwrap();
    }

    #[test]
    fn audio_slot_editor_offers_import_preview_and_trim_without_recording() {
        let instance = unsafe { GetModuleHandleW(ptr::null()) };
        let host = Host(host_window(instance).unwrap());
        let settings = Settings::default();
        let settings_window = SettingsWindow::new(host.0, &settings).unwrap();
        let editor = Editor::new_embedded(host.0, settings_window.hwnd(), &settings).unwrap();
        let mut captions = Vec::new();
        unsafe {
            let mut child = GetWindow(editor.panel.0, GW_CHILD);
            while !child.is_null() {
                captions.push(window_text(child));
                child = GetWindow(child, GW_HWNDNEXT);
            }
        }
        assert!(
            captions
                .iter()
                .all(|caption| caption != "录制" && caption != "取消"),
            "recording controls are redundant with the background recording hotkey"
        );
        for action in ["导入", "试听", "截取", "清空"] {
            assert!(captions.iter().any(|caption| caption == action));
        }
    }
}
