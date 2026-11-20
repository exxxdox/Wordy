use std::collections::VecDeque;
use std::io;
use std::ptr;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
    mpsc,
};
use std::time::{Duration, Instant, SystemTime};

use tauri::{AppHandle, Emitter, Manager};
use windows_sys::Win32::Foundation::{HANDLE, HINSTANCE, HWND, WAIT_FAILED, WAIT_OBJECT_0};
use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
use windows_sys::Win32::System::Threading::INFINITE;
use windows_sys::Win32::UI::Shell::ShellExecuteW;
use windows_sys::Win32::UI::WindowsAndMessaging::*;
use windows_sys::core::w;

use crate::audio::{self, Recording};
use crate::config::{AudioIdentity, ConfigStore, Settings, Voice, WindowPosition};
use crate::credentials::{CredentialStore, Provider, WindowsCredentials, normalize_api_key_input};
use crate::desktop::*;
use crate::file_log::FileLog;
use crate::hotkey::{display_hotkey, parse_hotkey, windows::Registration};
use crate::playback::{EngineSettings, Event, Playback};
use crate::settings_action::SettingsAction;
use crate::ui::{self, CableStatus, ListenStatus, Snapshot, UiRequest};
use crate::wheel_window::WheelWindow;
use crate::{backup, dialog};

mod actions;
mod backup_schedule;
mod editor_actions;
mod hotkeys;
mod logging;
mod recording;
mod settings_actions;
mod trim;

use hotkeys::*;

use self::recording::RecordingResult;

const WHEEL_TICK: Duration = Duration::from_millis(crate::wheel::POLL_MS);
const LOG_LIMIT: usize = 5000;
const BACKUP_FILTER: &str = "Wordy 加密备份 (*.wordybackup)\0*.wordybackup\0";

/// 网页设置界面需要的数据与唤醒句柄。
pub struct UiContext {
    pub app: AppHandle,
    pub wake: isize,
    pub requests: mpsc::Receiver<UiRequest>,
    pub dialogs: ui::DialogBroker,
}

/// 在 Tauri 工作线程里运行原有 Win32 应用：
/// 托盘、热键、悬浮输入框和轮盘保持原生，设置页由 WebView 承担。
pub fn run_native(ui: UiContext) -> io::Result<()> {
    enable_dark_mode();
    let instance = unsafe { GetModuleHandleW(ptr::null()) };
    if instance.is_null() {
        return Err(io::Error::last_os_error());
    }
    let host = Host(host_window(instance)?);
    let config = ConfigStore::load_default()?;
    let mut app = App::new(instance, host.0, config, ui)?;
    let result = app.event_loop();
    // 托盘退出后关闭 WebView 事件循环。
    app.handle.exit(0);
    result
}

pub fn smoke_test() -> io::Result<()> {
    let instance = unsafe { GetModuleHandleW(ptr::null()) };
    let host = Host(host_window(instance)?);
    let _input = Overlay::new(instance, host.0)?;
    let _wheel = WheelWindow::new(instance).map_err(io::Error::other)?;
    Ok(())
}

#[derive(Clone, Copy)]
struct EditorPreview {
    id: u64,
    text: bool,
    wheel: usize,
    slot: usize,
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
    handle: AppHandle,
    /// Tauri 窗口句柄：原生对话框的 owner、到托盘反馈的锚点。
    hwnd: isize,
    wake: HANDLE,
    requests: mpsc::Receiver<UiRequest>,
    dialogs: ui::DialogBroker,
    outputs: Vec<AudioIdentity>,
    microphones: Vec<String>,
    cartesia_voices: Vec<Voice>,
    cable_status: CableStatus,
    listen_state: Option<ListenStatus>,
    font_families: Vec<String>,
    published_revision: u64,
    published_locked: bool,
    published_preview: Option<(usize, usize, bool)>,
    snapshot_sequence: u64,
    ui_dirty: bool,
    ui_connected: bool,
    feedback_ready: bool,
    settings_window_creating: bool,
    open_logs_on_connect: bool,
    // 截取可先于首次设置页加载完成。先存事件，等波形监听器注册后按顺序送达。
    pending_trim_events: Option<Vec<TrimEvent>>,
    recording: Option<Recording>,
    pending_trim: Option<(tempfile::NamedTempFile, Option<(usize, usize)>)>,
    trim: Option<trim::TrimSession>,
    editor_preview: Option<EditorPreview>,
    jobs: ui::WakeSender<Background>,
    results: mpsc::Receiver<Background>,
    pending_saves: usize,
    background_cancel: Arc<AtomicBool>,
    backup_running: bool,
    auto_backup_schedule: backup_schedule::AutoBackupSchedule,
    backup_password_set: bool,
    audio_clips: Vec<audio::AudioClip>,
    audio_clips_dirty: bool,
    exiting: bool,
    skip_config_flush: bool,
    voices_request: Option<u64>,
    next_job: u64,
    logs: VecDeque<String>,
    file_log: Option<FileLog>,
}

enum Background {
    Recording(RecordingResult),
    NotificationFailed(String),
    SettingsWindow(io::Result<()>),
    Voices(u64, io::Result<Vec<Voice>>),
    Backup {
        import: bool,
        auto: bool,
        result: io::Result<()>,
    },
}

enum TrimEvent {
    Open(ui::TrimOpen),
    Peaks(ui::TrimPeaks),
}

impl TrimEvent {
    fn emit(self, handle: &AppHandle) {
        let _ = match self {
            Self::Open(open) => handle.emit(ui::TRIM_OPEN_EVENT, open),
            Self::Peaks(peaks) => handle.emit(ui::TRIM_PEAKS_EVENT, peaks),
        };
    }
}

impl App {
    fn new(
        instance: HINSTANCE,
        host: HWND,
        config: ConfigStore,
        ui: UiContext,
    ) -> io::Result<Self> {
        let vault = WindowsCredentials;
        // 锁定或损坏的凭据库只影响自动备份，不影响其他功能。
        let backup_password_set = vault
            .load_backup_password()
            .is_ok_and(|password| password.is_some());
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
        let playback = Playback::start(ui.wake)?;
        let mut hotkeys = Hotkeys::default();
        let failures = hotkeys.startup(config.settings());
        let tray = Tray::install(host)?;
        let overlay = Overlay::new(instance, host)?;
        let wheel = WheelWindow::new(instance).map_err(io::Error::other)?;
        let (jobs, results) = ui::wake_channel(ui.wake);
        let auto_backup_age = backup_schedule::previous_age(
            &backup::auto_backup_directory(config.path()),
            SystemTime::now(),
        );
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
            handle: ui.app,
            hwnd: 0,
            wake: ui.wake as HANDLE,
            requests: ui.requests,
            dialogs: ui.dialogs,
            outputs: Vec::new(),
            microphones: Vec::new(),
            cartesia_voices: Vec::new(),
            cable_status: CableStatus::CheckFailed,
            listen_state: None,
            font_families: Vec::new(),
            published_revision: 0,
            published_locked: false,
            published_preview: None,
            snapshot_sequence: 0,
            ui_dirty: true,
            ui_connected: false,
            feedback_ready: false,
            settings_window_creating: false,
            open_logs_on_connect: false,
            pending_trim_events: Some(Vec::new()),
            recording: None,
            pending_trim: None,
            trim: None,
            editor_preview: None,
            jobs,
            results,
            pending_saves: 0,
            background_cancel: Arc::default(),
            backup_running: false,
            auto_backup_schedule: backup_schedule::AutoBackupSchedule::new(
                auto_backup_age.as_ref().ok().copied().flatten(),
            ),
            backup_password_set,
            audio_clips: Vec::new(),
            audio_clips_dirty: false,
            exiting: false,
            skip_config_flush: false,
            voices_request: None,
            next_job: 0,
            logs: VecDeque::new(),
            file_log: None,
        };
        if cfg!(feature = "packaged") {
            match FileLog::beside_executable() {
                Ok(log) => app.file_log = Some(log),
                Err(error) => app.log(format!("无法写入日志文件：{error}")),
            }
        }
        if let Err(error) = auto_backup_age {
            app.log_at("WARNING", format!("无法读取上次自动备份时间：{error}"));
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
        app.load_audio_settings();
        if app.config.settings().main_playback_enabled {
            let message = match app.cable_status {
                CableStatus::Ready(_) => None,
                CableStatus::Unavailable => Some(
                    "找不到可用的 VB-CABLE 播放设备。请在 Windows 声音设置中检查是否已启用，或在 Wordy 的音频设置中下载。",
                ),
                CableStatus::CheckFailed => {
                    Some("无法读取 Windows 播放设备。请在 Wordy 的音频设置中刷新设备。")
                }
            };
            if let Some(message) = message {
                app.notify(message.into());
            }
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
        let mut message = MSG::default();
        let mut wheel_tick = Instant::now();
        while !self.skip_config_flush {
            let now = Instant::now();
            if self.wheel.is_visible() && now >= wheel_tick {
                wheel_tick = now + WHEEL_TICK;
                if let Err(error) = self.tick_wheel() {
                    self.report_error("轮盘操作失败", error);
                }
            }
            self.tick();
            if self.skip_config_flush {
                break;
            }
            // 后台事件和设置命令主动唤醒。只有轮盘活动或有任务到期才设超时。
            let deadline = [
                self.wheel.is_visible().then_some(wheel_tick),
                (!self.backup_running)
                    .then(|| self.config.save_deadline())
                    .flatten(),
                self.auto_backup_deadline(),
            ]
            .into_iter()
            .flatten()
            .min();
            let timeout = deadline.map_or(INFINITE, |at| {
                let remaining = at.saturating_duration_since(Instant::now());
                remaining
                    .as_nanos()
                    .div_ceil(1_000_000)
                    .min(u128::from(INFINITE - 1)) as u32
            });
            let wait = unsafe {
                MsgWaitForMultipleObjectsEx(
                    1,
                    &self.wake,
                    timeout,
                    QS_ALLINPUT,
                    MWMO_INPUTAVAILABLE,
                )
            };
            if wait == WAIT_OBJECT_0 {
                self.dispatch_ui();
                continue;
            }
            if wait == WAIT_FAILED {
                return Err(io::Error::last_os_error());
            }
            if wait != WAIT_OBJECT_0 + 1 {
                continue;
            }
            // 唤醒可能只有同步发送的消息。消化它后继续等待事件，避免 GetMessage 阻塞。
            if unsafe { PeekMessageW(&mut message, ptr::null_mut(), 0, 0, PM_REMOVE) } == 0 {
                continue;
            }
            if message.message == WM_QUIT {
                break;
            }
            let result = match message.message {
                WM_HOTKEY => self.hotkey(message.wParam),
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
                msg if msg != 0 && msg == explorer_restart => self.tray.add(),
                _ => {
                    unsafe {
                        TranslateMessage(&message);
                        DispatchMessageW(&message);
                    }
                    Ok(())
                }
            };
            if let Err(error) = result {
                self.report_error("操作失败", error);
            }
        }
        // 退出流程里的错误只写日志，不再弹失败提示。
        self.exiting = true;
        self.wheel.cancel();
        self.playback.stop_speech();
        self.playback.stop_clip();
        self.trim = None;
        if let Some((source, None)) = self.pending_trim.take() {
            match recording::preserve_rolling(source) {
                Ok(path) => self.log(format!("已停止的录音保存在 {}", path.display())),
                Err(error) => self.log(format!("无法保留录音临时文件：{error}")),
            }
        }
        if let Some(recording) = self.recording.take() {
            self.tray.set_recording(false);
            if let Err(error) = recording.cancel() {
                self.log(format!("停止后台录制失败：{error}"));
            }
        }
        // A submitted save owns the only temporary recording. Finish it before
        // dropping results, but do not hang forever on a stuck decoder.
        let deadline = Instant::now() + Duration::from_secs(10);
        let mut cancel_deadline: Option<Instant> = None;
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
            // 退出时无法保存只记录，不再弹通知打断退出。
            self.log_at("ERROR", format!("保存设置失败：{error}"));
            Ok(())
        } else {
            Ok(())
        }
    }

    fn tick_wheel(&mut self) -> io::Result<()> {
        if let Some(slot) = self.wheel.tick()
            && let Some(wheel) = self.active_wheel.take()
        {
            self.play_slot(wheel, slot)?;
        }
        self.wheel.present().map_err(io::Error::other)
    }

    fn tick(&mut self) {
        // The backup worker owns the file; do not flush a debounce over it.
        if !self.backup_running
            && let Err(error) = self.config.save_if_due(Instant::now())
        {
            self.report_error("保存设置失败", error);
        }
        if let Err(error) = self.maybe_auto_backup() {
            self.report_error("自动备份失败", error);
        }
        while let Ok(event) = self.playback.events.try_recv() {
            match event {
                Event::Finished { id, result } => {
                    self.trim_progress(id, None);
                    if self
                        .editor_preview
                        .as_ref()
                        .is_some_and(|preview| preview.id == id)
                    {
                        self.editor_preview = None;
                        self.ui_dirty = true;
                    }
                    if let Err(error) = result {
                        self.report_error("播放失败", error);
                    }
                }
                Event::Progress { id, seconds } => {
                    self.trim_progress(id, Some(seconds));
                }
                Event::Warning { target, message } => {
                    self.log_target("WARNING", target, message);
                }
                Event::Log {
                    target,
                    level,
                    message,
                } => self.log_target(level, target, message),
            }
        }
        while let Ok(result) = self.results.try_recv() {
            if let Err(error) = self.complete_background(result, false) {
                self.report_error("操作失败", error);
            }
        }
        self.publish_if_changed();
    }

    fn publish_if_changed(&mut self) {
        // 截取、保存与试听状态改变 locked/preview；这些不写配置，必须单独触发推送。
        if self.ui_locked() != self.published_locked
            || self.ui_preview_key() != self.published_preview
        {
            self.ui_dirty = true;
        }
        if self.ui_connected && (self.ui_dirty || self.config.revision() != self.published_revision)
        {
            self.emit_snapshot();
        }
    }

    fn snapshot(&mut self) -> Snapshot {
        self.snapshot_sequence += 1;
        Snapshot {
            sequence: self.snapshot_sequence,
            settings: self.config.settings().clone(),
            outputs: self.outputs.clone(),
            microphones: self.microphones.clone(),
            voices: self.cartesia_voices.clone(),
            cable_status: self.cable_status.clone(),
            listen: self.listen_state.clone(),
            fonts: self.font_families.clone(),
            busy: self.backup_running,
            locked: self.ui_locked(),
            backup_password_set: self.backup_password_set,
            backup_folder: backup::backup_directory(self.config.path())
                .to_string_lossy()
                .into_owned(),
            audio_clips: self.audio_clips.clone(),
            preview: self
                .editor_preview
                .as_ref()
                .map(|preview| ui::PreviewState {
                    wheel: preview.wheel,
                    slot: preview.slot,
                    text: preview.text,
                }),
        }
    }

    fn ui_locked(&self) -> bool {
        self.pending_saves > 0
            || self.trim.is_some()
            || self.pending_trim.is_some()
            || self
                .editor_preview
                .as_ref()
                .is_some_and(|preview| preview.text)
    }

    fn ui_preview_key(&self) -> Option<(usize, usize, bool)> {
        self.editor_preview
            .as_ref()
            .map(|preview| (preview.wheel, preview.slot, preview.text))
    }

    fn emit_snapshot(&mut self) {
        let snapshot = self.snapshot();
        self.mark_snapshot_published(&snapshot);
        if let Err(error) = self.handle.emit(ui::SNAPSHOT_EVENT, snapshot) {
            eprintln!("无法更新设置界面：{error}");
        }
    }

    fn mark_snapshot_published(&mut self, snapshot: &Snapshot) {
        self.published_revision = self.config.revision();
        self.published_locked = snapshot.locked;
        self.published_preview = snapshot
            .preview
            .map(|preview| (preview.wheel, preview.slot, preview.text));
        self.ui_dirty = false;
    }

    /// 处理网页发来的请求。动作仍在原生线程执行，保持原有副作用顺序。
    fn dispatch_ui(&mut self) {
        while let Ok(request) = self.requests.try_recv() {
            match request {
                UiRequest::Logs(reply) => {
                    let _ = reply.send(self.logs.iter().cloned().collect());
                }
                UiRequest::Snapshot(reply) => {
                    if !self.ui_connected {
                        // 首次快照说明 WebView 已加载并完成挂载。
                        self.ui_connected = true;
                        self.font_families = crate::fonts::installed_fonts();
                        self.refresh_audio_clips();
                        self.log("设置界面已连接".into());
                    }
                    let snapshot = self.snapshot();
                    self.mark_snapshot_published(&snapshot);
                    let _ = reply.send(snapshot);
                    if std::mem::take(&mut self.open_logs_on_connect) {
                        let _ = self.handle.emit(ui::OPEN_LOGS_EVENT, ());
                    }
                }
                UiRequest::Action(action, reply) => {
                    let audio_refresh = action.refreshes_audio_library();
                    let result = self.settings_action(action);
                    if audio_refresh {
                        self.refresh_audio_clips();
                    }
                    if let Err(error) = &result {
                        let message = error.to_string();
                        self.report_error("操作失败", io::Error::other(message));
                    }
                    // 快照只走事件通道，动作回复只报告完成或失败。
                    self.publish_if_changed();
                    let _ = reply.send(result.map_err(|error| error.to_string()));
                }
                UiRequest::SettingsMoved(x, y) => {
                    self.config
                        .remember_settings_position(WindowPosition { x, y });
                }
                UiRequest::TrimReady => {
                    for event in self.pending_trim_events.take().into_iter().flatten() {
                        event.emit(&self.handle);
                    }
                }
                UiRequest::FeedbackReady => self.feedback_ready = true,
            }
        }
    }

    pub(super) fn ui_hwnd(&self) -> HWND {
        self.hwnd as HWND
    }

    pub(super) fn show_settings(&mut self) -> io::Result<()> {
        self.overlay.hide(false);
        if self.settings_window_creating {
            return Ok(());
        }
        if let Some(window) = self.handle.get_webview_window("settings") {
            return self.show_settings_window(&window);
        }
        let handle = self.handle.clone();
        let events = self.jobs.clone();
        // Tauri 在内部调度建窗。等待 WebView2 时，原生线程仍要处理热键和退出。
        std::thread::Builder::new()
            .name("WordySettings".into())
            .spawn(move || {
                let result = ui::build_settings_window(&handle).map_err(io::Error::other);
                let _ = events.send(Background::SettingsWindow(result));
            })?;
        self.settings_window_creating = true;
        Ok(())
    }

    fn show_settings_window(&mut self, window: &tauri::WebviewWindow) -> io::Result<()> {
        self.hwnd = window.hwnd().map_err(io::Error::other)?.0 as isize;
        place_settings_window(window, self.config.settings().settings_window_position);
        let _ = window.unminimize();
        window.show().map_err(io::Error::other)?;
        let _ = window.set_focus();
        Ok(())
    }

    pub(super) fn emit_trim_open(&mut self, open: ui::TrimOpen) {
        self.emit_trim(TrimEvent::Open(open));
    }

    pub(super) fn emit_trim_peaks(&mut self, peaks: ui::TrimPeaks) {
        self.emit_trim(TrimEvent::Peaks(peaks));
    }

    fn emit_trim(&mut self, event: TrimEvent) {
        match &mut self.pending_trim_events {
            Some(pending) => pending.push(event),
            None => event.emit(&self.handle),
        }
    }

    pub(super) fn settings_visible(&self) -> bool {
        let hwnd = self.ui_hwnd();
        !hwnd.is_null() && unsafe { IsWindowVisible(hwnd) != 0 && IsIconic(hwnd) == 0 }
    }
    fn complete_background(&mut self, result: Background, exiting: bool) -> io::Result<()> {
        match result {
            Background::SettingsWindow(result) => {
                self.settings_window_creating = false;
                if !exiting {
                    let pending = self.pending_trim.take();
                    let shown = result.and_then(|()| {
                        let window = self
                            .handle
                            .get_webview_window("settings")
                            .ok_or_else(|| io::Error::other("设置窗口创建后不可用"))?;
                        self.show_settings_window(&window)
                    });
                    if let Err(error) = shown {
                        self.open_logs_on_connect = false;
                        if let Some((source, None)) = pending {
                            return Err(match recording::preserve_rolling(source) {
                                Ok(path) => io::Error::other(format!(
                                    "{error}（录音保留在 {}）",
                                    path.display()
                                )),
                                Err(retain) => {
                                    io::Error::other(format!("{error}（无法保留录音：{retain}）"))
                                }
                            });
                        }
                        return Err(error);
                    }
                    if let Some((source, target)) = pending {
                        self.open_trim(source, target)?;
                    }
                }
            }
            Background::Voices(id, result) => {
                if self.voices_request == Some(id) {
                    self.voices_request = None;
                    match result {
                        Ok(voices) => {
                            let count = voices.len();
                            self.cartesia_voices = voices;
                            self.ui_dirty = true;
                            self.status(format!("已加载 {count} 个音色"));
                        }
                        Err(error) => self.failure(format!("获取音色列表失败：{error}")),
                    }
                }
            }
            Background::NotificationFailed(message) => {
                // Fall back to the tray when the notification-center sender is unavailable.
                self.tray.notify(&message);
                self.log_at("WARNING", "Windows 通知发送失败，已改用托盘提示".into());
            }
            Background::Backup {
                import,
                auto,
                result,
            } => {
                self.backup_running = false;
                self.ui_dirty = true;
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
                    }
                    Ok(()) if auto => {
                        // 清理失败只记日志：新备份已经写入，不能提示成整体失败。
                        self.auto_backup_schedule.reset();
                        let keep = self.config.settings().auto_backup_keep;
                        if let Err(error) = backup::prune_backups(self.config.path(), keep) {
                            self.log_at("WARNING", format!("清理旧自动备份失败：{error}"));
                        }
                        self.log("自动备份完成".into());
                    }
                    Ok(()) => self.status("备份已导出".into()),
                    Err(error) => {
                        self.failure(format!("备份失败：{error}"));
                        if import
                            && !matches!(backup::import_pending(self.config.path()), Ok(false))
                        {
                            // 恢复记录保留时退出，避免后续编辑覆盖事务应恢复的状态。
                            self.skip_config_flush = true;
                        }
                    }
                }
            }
            Background::Recording(result) => {
                let refresh =
                    self.audio_clips_dirty || matches!(result, RecordingResult::SlotClip { .. });
                let result = self.complete_recording(result, exiting);
                if refresh {
                    self.refresh_audio_clips();
                }
                result?;
            }
        }
        if self.audio_clips_dirty && !self.backup_running && self.pending_saves == 0 {
            self.refresh_audio_clips();
        }
        Ok(())
    }

    fn refresh_audio_clips(&mut self) {
        // 后台暂存结束后文件才可供整理，导入备份期间也不扫描中间状态。
        self.audio_clips_dirty = self.pending_saves != 0 || self.backup_running;
        if !self.audio_clips_dirty {
            let clips = audio::list_clips(&self.config).unwrap_or_default();
            if self.audio_clips != clips {
                self.audio_clips = clips;
                self.ui_dirty = true;
            }
        }
    }
}

/// 第一次打开居中；之后恢复到上次位置。显示器变化导致位置失效时重新居中。
fn place_settings_window(window: &tauri::WebviewWindow, saved: Option<WindowPosition>) {
    if let Some(position) = saved.filter(|position| position_is_visible(window, *position)) {
        let _ = window.set_position(tauri::PhysicalPosition::new(position.x, position.y));
    } else {
        let _ = window.center();
    }
}

fn position_is_visible(window: &tauri::WebviewWindow, position: WindowPosition) -> bool {
    window
        .available_monitors()
        .map(|monitors| {
            monitors.iter().any(|monitor| {
                let origin = monitor.position();
                let size = monitor.size();
                position.x >= origin.x
                    && position.y >= origin.y
                    && position.x < origin.x + size.width as i32
                    && position.y < origin.y + size.height as i32
            })
        })
        .unwrap_or(false)
}

impl App {
    fn ask_dialog(
        &mut self,
        kind: &str,
        title: &str,
        message: &str,
        value: &str,
        password: bool,
        single: bool,
    ) -> Option<String> {
        // 网页弹窗只在设置窗口可见时才看得到。
        if !self.settings_visible() {
            let _ = self.show_settings();
        }
        let reply = self
            .dialogs
            .ask(&self.handle, kind, title, message, value, password, single);
        reply.ok.then(|| reply.value.unwrap_or_default())
    }

    pub(super) fn confirm_dialog(&mut self, title: &str, message: &str) -> bool {
        self.ask_dialog("confirm", title, message, "", false, false)
            .is_some()
    }

    pub(super) fn prompt_dialog(
        &mut self,
        title: &str,
        label: &str,
        password: bool,
    ) -> Option<String> {
        self.ask_dialog("prompt", title, label, "", password, false)
    }

    pub(super) fn capture_binding(&mut self, single: bool) -> io::Result<Option<String>> {
        // Clear global registrations while capturing so the old shortcut cannot open a second surface.
        let previous: Vec<_> = self
            .hotkeys
            .current
            .drain()
            .map(|(binding, registered)| (binding, registered.action))
            .collect();
        let message = if single {
            "请按下一个键"
        } else {
            "请按下要使用的快捷键组合"
        };
        let binding = self.ask_dialog("record", "录制快捷键", message, "", false, single);
        let mut failures = Vec::new();
        for (key, action) in previous {
            match self.hotkeys.register_new(key, action) {
                Ok(registration) => {
                    self.hotkeys.current.insert(key, registration);
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

    pub(super) fn feedback(&self, message: &str, tone: &str) {
        let _ = self.handle.emit(
            ui::FEEDBACK_EVENT,
            ui::Feedback {
                message: message.into(),
                tone: tone.into(),
            },
        );
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
}
