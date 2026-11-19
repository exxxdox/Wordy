use std::collections::{HashMap, VecDeque};
use std::io;
use std::path::PathBuf;
use std::ptr;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
    mpsc,
};
use std::time::{Duration, Instant};

use windows_sys::Win32::Foundation::{HINSTANCE, HWND};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::UI::HiDpi::{
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, SetProcessDpiAwarenessContext,
};
use windows_sys::Win32::UI::Shell::ShellExecuteW;
use windows_sys::Win32::UI::WindowsAndMessaging::*;
use windows_sys::core::w;

use crate::audio::{self, Recording};
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

mod actions;
mod editor_actions;
mod hotkeys;
mod logging;
mod recording;
mod settings_actions;

use hotkeys::*;

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
    background_cancel: Arc<AtomicBool>,
    backup_running: bool,
    skip_config_flush: bool,
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
    Backup {
        import: bool,
        result: io::Result<()>,
    },
}

impl App {
    fn new(instance: HINSTANCE, host: HWND, config: ConfigStore) -> io::Result<Self> {
        let vault = WindowsCredentials;
        let mut credential_warning = None;
        // A locked or damaged vault must not stop unrelated features from running.
        let api_key = vault
            .load(config.settings().active_tts_provider)
            .unwrap_or_else(|error| {
                credential_warning = Some(format!("无法读取已保存的 API Key：{error}"));
                None
            });
        let engine = Arc::new(EngineSettings {
            settings: config.settings().clone(),
            api_key,
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
            background_cancel: Arc::default(),
            backup_running: false,
            skip_config_flush: false,
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
        if let Some(warning) = credential_warning {
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
                    TRAY_EXIT if self.backup_running => {
                        self.failure("备份处理中，完成后再退出".into());
                        Ok(())
                    }
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
        // A submitted save owns the only temporary recording. Finish it before
        // dropping results, but do not hang forever on a stuck decoder.
        let deadline = Instant::now() + Duration::from_secs(10);
        let mut cancel_deadline = None;
        while self.pending_saves > 0 {
            let now = Instant::now();
            let wait = match cancel_deadline {
                None if now >= deadline => Duration::ZERO,
                None => deadline.saturating_duration_since(now),
                Some(limit) if now >= limit => {
                    self.log("仍有后台音频任务未结束，已放弃等待".into());
                    break;
                }
                Some(limit) => limit.saturating_duration_since(now),
            };
            if wait.is_zero() && cancel_deadline.is_none() {
                self.background_cancel.store(true, Ordering::Release);
                cancel_deadline = Some(now + Duration::from_secs(3));
            }
            match self.results.recv_timeout(wait) {
                Ok(result) => {
                    if let Err(error) = self.complete_background(result, true) {
                        self.log(format!("保存失败：{error}"));
                    }
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {
                    if cancel_deadline.is_none() {
                        self.background_cancel.store(true, Ordering::Release);
                        cancel_deadline = Some(Instant::now() + Duration::from_secs(3));
                    }
                }
                Err(mpsc::RecvTimeoutError::Disconnected) => break,
            }
        }
        if self.skip_config_flush {
            Ok(())
        } else if let Err(error) = self.config.flush() {
            // A save failure during quit is not a startup failure.
            self.failure(format!("保存设置失败：{error}"));
            Ok(())
        } else {
            Ok(())
        }
    }

    fn tick(&mut self) -> io::Result<()> {
        if let Err(error) = self.toasts.tick(
            self.settings_window.hwnd(),
            self.settings_window.toast_anchor(),
            &self.config.settings().ui_font,
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
        // The backup worker owns the file; do not flush a debounce over it.
        if !self.backup_running {
            self.config.save_if_due(Instant::now())?;
        }
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
                self.tray.notify(&message);
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
            Background::Backup { import, result } => {
                self.backup_running = false;
                match result {
                    Ok(()) if import => {
                        // The worker wrote the imported config. Reload it so the
                        // shutdown path cannot overwrite it with stale settings.
                        let path = self.config.path().to_path_buf();
                        match ConfigStore::load(&path) {
                            Ok(config) => self.config = config,
                            Err(error) => {
                                self.failure(format!("备份已导入，但重新读取配置失败：{error}"));
                            }
                        }
                        self.skip_config_flush = true;
                        self.status("备份已导入，请重新启动 Wordy".into());
                        unsafe { PostQuitMessage(0) };
                    }
                    Ok(()) => self.status("备份已导出".into()),
                    Err(error) => self.failure(format!("备份失败：{error}")),
                }
            }
            Background::Recording(result) => self.complete_recording(result, exiting)?,
        }
        Ok(())
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

fn capture_binding(
    hotkeys: &mut Hotkeys,
    owner: HWND,
    single: bool,
    font_family: &str,
) -> io::Result<Option<String>> {
    // Clear global registrations while capturing so the old shortcut cannot open a second surface.
    let previous: Vec<_> = hotkeys
        .current
        .drain()
        .map(|(binding, registered)| (binding, registered.action))
        .collect();
    let binding = dialog::record_key(owner, single, font_family);
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

/// New wheels are added unbound; their shortcut is recorded later from the
/// wheel editor. A default name keeps an otherwise empty wheel on disk.
fn append_unbound_wheel(config: &mut ConfigStore, hotkeys: &mut Hotkeys) -> io::Result<usize> {
    let mut next = config.settings().clone();
    let mut wheel = crate::config::Wheel::new("");
    wheel.name = format!("轮盘 {}", next.wheel_count() + 1);
    next.additional_chat_wheels.push(wheel);
    next.chat_wheel_last_index = next.wheel_count() - 1;
    hotkeys.update(config, next)?;
    Ok(config.settings().chat_wheel_last_index)
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
        // A wheel kept from a bad legacy hotkey contributes no binding.
        settings.additional_chat_wheels[0].hotkey.clear();
        assert_eq!(checked_keys(&settings).unwrap().len(), 3);
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
