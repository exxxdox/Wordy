//! React 设置界面与原生 Win32 后台之间的桥。
//!
//! 原生线程继续独占配置、快捷键注册、音频路由和引擎状态；网页只发送动作，
//! 并接收一份完整快照。命令线程通过自动重置事件唤醒原生消息循环。

use std::sync::mpsc::{self, Receiver, Sender, SyncSender};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use windows_sys::Win32::System::Threading::SetEvent;

use crate::config::{AudioIdentity, Settings, Voice};
use crate::credentials::Provider;
use crate::settings_action::SettingsAction;

/// 原生快照超过这个时间没有回应，说明后台线程已阻塞在对话框里。
const SNAPSHOT_TIMEOUT: Duration = Duration::from_secs(10);
const ACTION_TIMEOUT: Duration = Duration::from_secs(600);

/// 原生线程主动推送完整设置快照。
pub const SNAPSHOT_EVENT: &str = "wordy://snapshot";
/// 原生线程逐行推送日志。
pub const LOG_EVENT: &str = "wordy://log";
/// 托盘请求打开日志面板。
pub const OPEN_LOGS_EVENT: &str = "wordy://open-logs";

#[derive(Clone, Debug, Serialize)]
pub struct ListenStatus {
    pub enabled: bool,
    pub output: Option<String>,
    pub output_available: bool,
}

#[derive(Clone, Copy, Debug, Serialize)]
pub struct PreviewState {
    pub wheel: usize,
    pub slot: usize,
    pub text: bool,
}

#[derive(Clone, Debug, Serialize)]
pub struct Snapshot {
    pub settings: Settings,
    pub outputs: Vec<AudioIdentity>,
    pub microphones: Vec<String>,
    pub voices: Vec<Voice>,
    pub cable_available: bool,
    pub listen: Option<ListenStatus>,
    pub fonts: Vec<String>,
    pub busy: bool,
    /// 导入、保存或截取阻塞编辑控件。
    pub locked: bool,
    pub preview: Option<PreviewState>,
}

pub enum UiRequest {
    Snapshot(SyncSender<Snapshot>),
    Logs(SyncSender<Vec<String>>),
    Action(SettingsAction, SyncSender<Result<Snapshot, String>>),
    Preview {
        wheel: usize,
        slot: usize,
        width: u32,
        reply: SyncSender<Result<String, String>>,
    },
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

    pub fn preview(&self, wheel: usize, slot: usize, width: u32) -> Result<String, String> {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.send(UiRequest::Preview {
            wheel,
            slot,
            width,
            reply,
        })?;
        receiver
            .recv_timeout(SNAPSHOT_TIMEOUT)
            .map_err(|_| "生成轮盘预览超时".to_string())?
    }

    pub fn action(&self, action: SettingsAction) -> Result<Snapshot, String> {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.send(UiRequest::Action(action, reply))?;
        receiver
            .recv_timeout(ACTION_TIMEOUT)
            .map_err(|_| "操作超时".to_string())?
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
    SetWheelStyle {
        style: String,
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
    CropSlotAudio {
        index: usize,
        slot: usize,
    },
    PreviewSlot {
        index: usize,
        slot: usize,
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
            Self::DownloadVbCable => SettingsAction::DownloadVbCable,
            Self::OpenLogs => SettingsAction::OpenLogs,
            Self::RequestAudioDevices => SettingsAction::RequestAudioDevices,
            Self::SelectWheel { index } => SettingsAction::SelectWheel(index),
            Self::AddWheel => SettingsAction::AddWheel,
            Self::RemoveWheel { index } => SettingsAction::RemoveWheel(index),
            Self::SetWheelName { index, name } => SettingsAction::SetWheelName(index, name),
            Self::SetWheelStyle { style } => SettingsAction::SetWheelStyle(style),
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
            Self::CropSlotAudio { index, slot } => SettingsAction::CropSlotAudio(index, slot),
            Self::PreviewSlot { index, slot } => SettingsAction::PreviewSlot(index, slot),
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
pub fn wordy_snapshot(state: tauri::State<'_, NativeHandle>) -> Result<Snapshot, String> {
    state.snapshot()
}

#[tauri::command]
pub fn wordy_action(
    action: UiAction,
    state: tauri::State<'_, NativeHandle>,
) -> Result<Snapshot, String> {
    state.action(action.into_action()?)
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
pub fn wordy_logs(state: tauri::State<'_, NativeHandle>) -> Result<Vec<String>, String> {
    state.logs()
}

#[tauri::command]
pub fn wordy_wheel_preview(
    wheel: usize,
    slot: usize,
    width: Option<u32>,
    state: tauri::State<'_, NativeHandle>,
) -> Result<String, String> {
    state.preview(wheel, slot, width.unwrap_or(420))
}
