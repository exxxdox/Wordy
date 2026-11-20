//! React 设置界面与原生 Win32 后台之间的桥。
//!
//! 原生线程继续独占配置、快捷键注册、音频路由和引擎状态；网页只发送动作，
//! 并接收一份完整快照。命令线程通过自动重置事件唤醒原生消息循环。

use std::cell::RefCell;
use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{self, Receiver, Sender, SyncSender};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use windows_sys::Win32::System::Threading::SetEvent;

use crate::config::{AudioIdentity, Settings, Voice};
use crate::credentials::Provider;
use crate::settings_action::SettingsAction;
use crate::wheel::SLOT_COUNT;
use crate::wheel_paint::WheelRenderer;

/// 原生快照超过这个时间没有回应，说明后台线程已阻塞在对话框里。
const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(10);
const ACTION_TIMEOUT: Duration = Duration::from_secs(600);

/// 原生线程主动推送完整设置快照。
pub const SNAPSHOT_EVENT: &str = "wordy://snapshot";
/// 原生线程逐行推送日志。
pub const LOG_EVENT: &str = "wordy://log";
/// 托盘请求打开日志面板。
pub const OPEN_LOGS_EVENT: &str = "wordy://open-logs";
/// 截取窗口生命周期与波形数据。
pub const TRIM_OPEN_EVENT: &str = "wordy://trim-open";
pub const TRIM_PEAKS_EVENT: &str = "wordy://trim-peaks";
pub const TRIM_PROGRESS_EVENT: &str = "wordy://trim-progress";
pub const TRIM_CLOSED_EVENT: &str = "wordy://trim-closed";
pub const TRIM_SAVE_FAILED_EVENT: &str = "wordy://trim-save-failed";
/// React 原生替代弹窗与状态提示。
pub const DIALOG_EVENT: &str = "wordy://dialog";
pub const FEEDBACK_EVENT: &str = "wordy://feedback";

#[derive(Clone, Debug, Serialize)]
pub struct ListenStatus {
    pub enabled: bool,
    pub output: Option<String>,
    pub output_available: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct Feedback {
    pub message: String,
    pub tone: String,
}

#[derive(Clone, Debug, Serialize)]
pub struct DialogRequest {
    pub id: u64,
    pub kind: String,
    pub title: String,
    pub message: String,
    pub value: String,
    pub password: bool,
    pub single: bool,
}

#[derive(Clone, Debug, Deserialize)]
pub struct DialogReply {
    pub id: u64,
    pub ok: bool,
    pub value: Option<String>,
}

/// 原生线程请求网页渲染弹窗，并阻塞等待回答。
#[derive(Clone, Default)]
pub struct DialogBroker {
    next: Arc<AtomicU64>,
    pending: Arc<Mutex<HashMap<u64, SyncSender<DialogReply>>>>,
}

impl DialogBroker {
    #[allow(clippy::too_many_arguments)]
    pub fn ask(
        &self,
        app: &tauri::AppHandle,
        kind: &str,
        title: &str,
        message: &str,
        value: &str,
        password: bool,
        single: bool,
    ) -> DialogReply {
        use tauri::Emitter;
        let id = self.next.fetch_add(1, Ordering::Relaxed) + 1;
        let (reply, receiver) = mpsc::sync_channel(1);
        self.pending
            .lock()
            .expect("dialog broker poisoned")
            .insert(id, reply);
        let _ = app.emit(
            DIALOG_EVENT,
            DialogRequest {
                id,
                kind: kind.into(),
                title: title.into(),
                message: message.into(),
                value: value.into(),
                password,
                single,
            },
        );
        let reply = receiver
            .recv_timeout(ACTION_TIMEOUT)
            .unwrap_or(DialogReply {
                id,
                ok: false,
                value: None,
            });
        self.pending
            .lock()
            .expect("dialog broker poisoned")
            .remove(&id);
        reply
    }

    pub fn resolve(&self, reply: DialogReply) {
        if let Some(sender) = self
            .pending
            .lock()
            .expect("dialog broker poisoned")
            .remove(&reply.id)
        {
            let _ = sender.send(reply);
        }
    }
}

#[derive(Clone, Debug, Serialize)]
pub struct TrimOpen {
    pub id: u64,
    pub duration: f64,
    pub rolling: bool,
    pub wheel: usize,
    pub slot: usize,
    pub label: String,
    pub gain: f64,
}

#[derive(Clone, Debug, Serialize)]
pub struct TrimPeaks {
    pub id: u64,
    pub peaks: Vec<f32>,
}

#[derive(Clone, Copy, Debug, Serialize)]
pub struct TrimProgress {
    pub playing: bool,
    pub seconds: Option<f64>,
}

#[derive(Clone, Copy, Debug, Serialize)]
pub struct PreviewState {
    pub wheel: usize,
    pub slot: usize,
    pub text: bool,
}

#[derive(Clone, Debug, Serialize)]
#[serde(tag = "state", content = "output", rename_all = "snake_case")]
pub enum CableStatus {
    Ready(String),
    Unavailable,
    CheckFailed,
}

#[derive(Clone, Debug, Serialize)]
pub struct Snapshot {
    pub settings: Settings,
    pub outputs: Vec<AudioIdentity>,
    pub microphones: Vec<String>,
    pub voices: Vec<Voice>,
    pub cable_status: CableStatus,
    pub listen: Option<ListenStatus>,
    pub fonts: Vec<String>,
    pub busy: bool,
    /// 是否已保存自动备份密码。
    pub backup_password_set: bool,
    /// 手动与定时备份的默认目录。
    pub backup_folder: String,
    /// 托管目录中未被任何轮盘引用的音频数量。
    pub unused_clips: usize,
    pub audio_clips: Vec<crate::audio::AudioClip>,
    /// 导入、保存或截取阻塞编辑控件。
    pub locked: bool,
    pub preview: Option<PreviewState>,
}

pub enum UiRequest {
    Snapshot(SyncSender<Snapshot>),
    Logs(SyncSender<Vec<String>>),
    Action(SettingsAction, SyncSender<Result<Snapshot, String>>),
    /// 设置窗口移动后的物理坐标；只更新配置，不需要回复。
    SettingsMoved(i32, i32),
}

/// 网页侧持有的原生线程句柄。事件句柄跨线程仅用于 SetEvent，语义安全。
#[derive(Clone)]
pub struct NativeHandle {
    wake: isize,
    requests: Sender<UiRequest>,
}

// SAFETY: `wake` 是进程级内核事件句柄，SetEvent 可从任意线程调用。
unsafe impl Send for NativeHandle {}
unsafe impl Sync for NativeHandle {}

impl NativeHandle {
    pub fn new(wake: isize, requests: Sender<UiRequest>) -> Self {
        Self { wake, requests }
    }

    fn send(&self, request: UiRequest) -> Result<(), String> {
        self.requests
            .send(request)
            .map_err(|_| "后台服务已停止".to_string())?;
        if unsafe { SetEvent(self.wake as _) } == 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        Ok(())
    }

    pub fn snapshot(&self) -> Result<Snapshot, String> {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.send(UiRequest::Snapshot(reply))?;
        receiver
            .recv_timeout(SNAPSHOT_TIMEOUT)
            .map_err(|_| "读取设置超时".to_string())
    }

    pub fn logs(&self) -> Result<Vec<String>, String> {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.send(UiRequest::Logs(reply))?;
        receiver
            .recv_timeout(SNAPSHOT_TIMEOUT)
            .map_err(|_| "读取日志超时".to_string())
    }

    pub fn action(&self, action: SettingsAction) -> Result<Snapshot, String> {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.send(UiRequest::Action(action, reply))?;
        receiver
            .recv_timeout(ACTION_TIMEOUT)
            .map_err(|_| "操作超时".to_string())?
    }

    /// 窗口移动事件很密集，异步交给原生线程，不等待回复。
    pub fn settings_moved(&self, x: i32, y: i32) {
        let _ = self.send(UiRequest::SettingsMoved(x, y));
    }
}

pub fn channel() -> (Sender<UiRequest>, Receiver<UiRequest>) {
    mpsc::channel()
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum UiAction {
    SetProvider {
        provider: String,
    },
    SetBackend {
        backend: String,
    },
    SetVolume {
        value: f64,
    },
    SetOpacity {
        value: f64,
    },
    SetPlaceholder {
        value: String,
    },
    SetUiFont {
        value: String,
    },
    SetFixedCenter {
        value: bool,
    },
    SetLogLevel {
        value: String,
    },
    SetHoldKeyEnabled {
        value: bool,
    },
    RecordHoldKey,
    RecordHotkey,
    RecordRollingHotkey,
    SetRollingSeconds {
        value: f64,
    },
    SetOutputDevice {
        device: Option<AudioIdentity>,
    },
    SetMic {
        name: Option<String>,
    },
    SetMainPlayback {
        value: bool,
    },
    SetSidetone {
        value: bool,
    },
    SetListen {
        value: bool,
    },
    SaveKey {
        provider: String,
        text: String,
    },
    DeleteKey {
        provider: String,
    },
    RefreshVoices,
    SelectVoice {
        provider: String,
        voice: Voice,
    },
    SaveVolcengineVoice {
        voice: Voice,
    },
    DeleteVolcengineVoice {
        id: String,
    },
    ExportBackup,
    ImportBackup,
    SetAutoBackupEnabled {
        value: bool,
    },
    SetAutoBackupInterval {
        value: f64,
    },
    SetAutoBackupKeep {
        value: usize,
    },
    SetBackupPassword,
    CleanUnusedAudio,
    DownloadVbCable,
    OpenLogs,
    RequestAudioDevices,
    SelectWheel {
        index: usize,
    },
    AddWheel,
    RemoveWheel {
        index: usize,
    },
    SetWheelName {
        index: usize,
        name: String,
    },
    RecordWheelHotkey {
        index: usize,
    },
    SetSlotLabel {
        index: usize,
        slot: usize,
        value: String,
    },
    SetSlotText {
        index: usize,
        slot: usize,
        value: String,
    },
    SetSlotVolume {
        index: usize,
        slot: usize,
        value: f64,
    },
    SetSlotMode {
        index: usize,
        slot: usize,
        text: bool,
    },
    ClearSlot {
        index: usize,
        slot: usize,
    },
    ImportSlotAudio {
        index: usize,
        slot: usize,
    },
    AssignWheelAudio {
        index: usize,
        slot: usize,
        id: String,
    },
    DeleteLibraryAudio {
        id: String,
    },
    CropSlotAudio {
        index: usize,
        slot: usize,
    },
    PreviewSlot {
        index: usize,
        slot: usize,
    },
    TrimPreview {
        id: u64,
        start: f64,
        end: f64,
        gain: f64,
    },
    TrimStop {
        id: u64,
    },
    TrimSave {
        id: u64,
        start: f64,
        end: f64,
        label: String,
        gain: f64,
        wheel: usize,
        slot: usize,
    },
    TrimCancel {
        id: u64,
    },
    Closed,
}

impl UiAction {
    pub fn into_action(self) -> Result<SettingsAction, String> {
        Ok(match self {
            Self::SetProvider { provider } => {
                SettingsAction::SetProvider(provider_from(&provider)?)
            }
            Self::SetBackend { backend } => SettingsAction::SetBackend(backend),
            Self::SetVolume { value } => SettingsAction::SetVolume(value),
            Self::SetOpacity { value } => SettingsAction::SetOpacity(value),
            Self::SetPlaceholder { value } => SettingsAction::SetPlaceholder(value),
            Self::SetUiFont { value } => SettingsAction::SetUiFont(value),
            Self::SetFixedCenter { value } => SettingsAction::SetFixedCenter(value),
            Self::SetLogLevel { value } => SettingsAction::SetLogLevel(value),
            Self::SetHoldKeyEnabled { value } => SettingsAction::SetHoldKeyEnabled(value),
            Self::RecordHoldKey => SettingsAction::RecordHoldKey,
            Self::RecordHotkey => SettingsAction::RecordHotkey,
            Self::RecordRollingHotkey => SettingsAction::RecordRollingHotkey,
            Self::SetRollingSeconds { value } => SettingsAction::SetRollingSeconds(value),
            Self::SetOutputDevice { device } => SettingsAction::SetOutputDevice(device),
            Self::SetMic { name } => SettingsAction::SetMic(name),
            Self::SetMainPlayback { value } => SettingsAction::SetMainPlayback(value),
            Self::SetSidetone { value } => SettingsAction::SetSidetone(value),
            Self::SetListen { value } => SettingsAction::SetListen(value),
            Self::SaveKey { provider, text } => {
                SettingsAction::SaveKey(provider_from(&provider)?, text)
            }
            Self::DeleteKey { provider } => SettingsAction::DeleteKey(provider_from(&provider)?),
            Self::RefreshVoices => SettingsAction::RefreshVoices,
            Self::SelectVoice { provider, voice } => {
                SettingsAction::SelectVoice(provider_from(&provider)?, voice)
            }
            Self::SaveVolcengineVoice { voice } => SettingsAction::SaveVolcengineVoice(voice),
            Self::DeleteVolcengineVoice { id } => SettingsAction::DeleteVolcengineVoice(id),
            Self::ExportBackup => SettingsAction::ExportBackup,
            Self::ImportBackup => SettingsAction::ImportBackup,
            Self::SetAutoBackupEnabled { value } => SettingsAction::SetAutoBackupEnabled(value),
            Self::SetAutoBackupInterval { value } => SettingsAction::SetAutoBackupInterval(value),
            Self::SetAutoBackupKeep { value } => SettingsAction::SetAutoBackupKeep(value),
            Self::SetBackupPassword => SettingsAction::SetBackupPassword,
            Self::CleanUnusedAudio => SettingsAction::CleanUnusedAudio,
            Self::DownloadVbCable => SettingsAction::DownloadVbCable,
            Self::OpenLogs => SettingsAction::OpenLogs,
            Self::RequestAudioDevices => SettingsAction::RequestAudioDevices,
            Self::SelectWheel { index } => SettingsAction::SelectWheel(index),
            Self::AddWheel => SettingsAction::AddWheel,
            Self::RemoveWheel { index } => SettingsAction::RemoveWheel(index),
            Self::SetWheelName { index, name } => SettingsAction::SetWheelName(index, name),
            Self::RecordWheelHotkey { index } => SettingsAction::RecordWheelHotkey(index),
            Self::SetSlotLabel { index, slot, value } => {
                SettingsAction::SetSlotLabel(index, slot, value)
            }
            Self::SetSlotText { index, slot, value } => {
                SettingsAction::SetSlotText(index, slot, value)
            }
            Self::SetSlotVolume { index, slot, value } => {
                SettingsAction::SetSlotVolume(index, slot, value)
            }
            Self::SetSlotMode { index, slot, text } => {
                SettingsAction::SetSlotMode(index, slot, text)
            }
            Self::ClearSlot { index, slot } => SettingsAction::ClearSlot(index, slot),
            Self::ImportSlotAudio { index, slot } => SettingsAction::ImportSlotAudio(index, slot),
            Self::AssignWheelAudio { index, slot, id } => {
                SettingsAction::AssignWheelAudio(index, slot, id)
            }
            Self::DeleteLibraryAudio { id } => SettingsAction::DeleteLibraryAudio(id),
            Self::CropSlotAudio { index, slot } => SettingsAction::CropSlotAudio(index, slot),
            Self::PreviewSlot { index, slot } => SettingsAction::PreviewSlot(index, slot),
            Self::TrimPreview {
                id,
                start,
                end,
                gain,
            } => SettingsAction::TrimPreview(id, start, end, gain),
            Self::TrimStop { id } => SettingsAction::TrimStop(id),
            Self::TrimSave {
                id,
                start,
                end,
                label,
                gain,
                wheel,
                slot,
            } => SettingsAction::TrimSave(id, start, end, label, gain, wheel, slot),
            Self::TrimCancel { id } => SettingsAction::TrimCancel(id),
            Self::Closed => SettingsAction::Closed,
        })
    }
}

fn provider_from(value: &str) -> Result<Provider, String> {
    match value.to_ascii_lowercase().as_str() {
        "cartesia" => Ok(Provider::Cartesia),
        "volcengine" => Ok(Provider::Volcengine),
        _ => Err(format!("未知服务商：{value}")),
    }
}

#[tauri::command]
pub async fn wordy_snapshot(state: tauri::State<'_, NativeHandle>) -> Result<Snapshot, String> {
    state.snapshot()
}

#[tauri::command]
pub async fn wordy_action(
    action: UiAction,
    state: tauri::State<'_, NativeHandle>,
) -> Result<Snapshot, String> {
    state.action(action.into_action()?)
}

/// 设置页轮盘预览：运行时同一渲染器输出 [宽 LE][高 LE][BGRA] 原始像素。
#[tauri::command]
pub async fn wordy_wheel_preview(
    labels: Vec<String>,
    selected: Option<usize>,
    font: String,
    scale: f64,
) -> Result<tauri::ipc::Response, String> {
    wheel_preview_bytes(&labels, selected, &font, scale).map(tauri::ipc::Response::new)
}

pub(crate) fn wheel_preview_bytes(
    labels: &[String],
    selected: Option<usize>,
    font: &str,
    scale: f64,
) -> Result<Vec<u8>, String> {
    let labels: [String; SLOT_COUNT] =
        std::array::from_fn(|index| labels.get(index).cloned().unwrap_or_default());
    let selected = selected.filter(|&index| index < SLOT_COUNT);
    with_preview_renderer(|renderer| {
        renderer
            .render_preview(
                &labels,
                selected,
                crate::config::ui_font_family(font),
                scale.clamp(0.1, 4.0),
            )
            .map_err(|error| error.to_string())
    })
}

thread_local! {
    // D2D 单线程工厂只能在创建它的线程上使用，而 Tauri 命令会落在不同工作线程；
    // 按线程缓存渲染器，避免每次切换槽位都重建 COM/D2D/DWrite/WIC 工厂。
    static PREVIEW_RENDERER: RefCell<Option<WheelRenderer>> = const { RefCell::new(None) };
}

fn with_preview_renderer<T>(
    paint: impl FnOnce(&WheelRenderer) -> Result<T, String>,
) -> Result<T, String> {
    PREVIEW_RENDERER.with(|cell| {
        let mut renderer = cell.borrow_mut();
        if renderer.is_none() {
            *renderer = Some(WheelRenderer::new().map_err(|error| error.to_string())?);
        }
        paint(renderer.as_ref().expect("renderer created above"))
    })
}

/// 网页里的「关闭」按钮只隐藏设置窗口，托盘应用继续运行。
#[tauri::command]
pub fn wordy_hide(app: tauri::AppHandle) -> Result<(), String> {
    use tauri::Manager;
    let window = app
        .get_webview_window("settings")
        .ok_or_else(|| "设置窗口不存在".to_string())?;
    window.hide().map_err(|error| error.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_ui_actions_and_rejects_unknown_providers() {
        let action: UiAction =
            serde_json::from_str(r#"{"type":"set_volume","value":1.5}"#).unwrap();
        assert!(matches!(
            action.into_action().unwrap(),
            SettingsAction::SetVolume(value) if value == 1.5
        ));
        let action: UiAction =
            serde_json::from_str(r#"{"type":"set_provider","provider":"cartesia"}"#).unwrap();
        assert!(matches!(
            action.into_action().unwrap(),
            SettingsAction::SetProvider(Provider::Cartesia)
        ));
        let action: UiAction =
            serde_json::from_str(r#"{"type":"set_provider","provider":"other"}"#).unwrap();
        assert!(action.into_action().is_err());
    }

    #[cfg(windows)]
    #[test]
    fn wheel_preview_pads_labels_and_reports_dimensions() {
        let bytes = wheel_preview_bytes(&["北".into()], Some(0), "", 0.5).unwrap();
        assert_eq!(u32::from_le_bytes(bytes[..4].try_into().unwrap()), 317);
        assert_eq!(u32::from_le_bytes(bytes[4..8].try_into().unwrap()), 270);
        assert_eq!(bytes.len(), 8 + 317 * 270 * 4);
    }

    #[cfg(windows)]
    #[test]
    fn reused_preview_renderer_still_honours_the_selected_slot() {
        let first = wheel_preview_bytes(&["北".into(), "南".into()], Some(0), "", 0.5).unwrap();
        let second = wheel_preview_bytes(&["北".into(), "南".into()], Some(1), "", 0.5).unwrap();
        assert_eq!(first[..8], second[..8]);
        assert_eq!(first.len(), second.len());
        // 高亮跟随所选槽位；渲染器复用后结果不能停留在上一次的选择上。
        assert_ne!(first, second);
    }

    #[test]
    fn parses_device_selection_sent_by_the_settings_page() {
        let action: UiAction = serde_json::from_str(
            r#"{"type":"set_output_device","device":{"name":"Speakers","host_api_name":"WASAPI","id":"{0.0.1}"}}"#,
        )
        .unwrap();
        assert!(matches!(
            action.into_action().unwrap(),
            SettingsAction::SetOutputDevice(Some(device)) if device.name == "Speakers"
        ));
    }
}

#[tauri::command]
pub async fn wordy_logs(state: tauri::State<'_, NativeHandle>) -> Result<Vec<String>, String> {
    state.logs()
}

#[tauri::command]
pub fn wordy_dialog_reply(reply: DialogReply, broker: tauri::State<'_, DialogBroker>) {
    broker.resolve(reply);
}
