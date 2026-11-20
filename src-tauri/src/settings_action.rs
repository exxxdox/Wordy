use crate::credentials::Provider;

/// 设置页发出的动作。原生设置窗口已由 React 界面取代，但动作语义不变，
/// 这样引擎重建、快捷键注册、音频路由等行为仍集中在后端一处。
pub enum SettingsAction {
    SetProvider(Provider),
    SetBackend(String),
    SetVolume(f64),
    SetOpacity(f64),
    SetPlaceholder(String),
    SetUiFont(String),
    SetFixedCenter(bool),
    SetLogLevel(String),
    SetHoldKeyEnabled(bool),
    RecordHoldKey,
    RecordHotkey,
    RecordRollingHotkey,
    SetRollingSeconds(f64),
    SetOutputDevice(Option<crate::config::AudioIdentity>),
    SetMic(Option<String>),
    SetMainPlayback(bool),
    SetSidetone(bool),
    SetListen(bool),
    SaveKey(Provider, String),
    DeleteKey(Provider),
    RefreshVoices,
    SelectVoice(Provider, crate::config::Voice),
    SaveVolcengineVoice(crate::config::Voice),
    DeleteVolcengineVoice(String),
    ExportBackup,
    ImportBackup,
    SetAutoBackupEnabled(bool),
    SetAutoBackupInterval(f64),
    SetAutoBackupKeep(usize),
    SetBackupPassword,
    CleanUnusedAudio,
    DownloadVbCable,
    OpenLogs,
    RequestAudioDevices,
    RefreshAudioLibrary,
    SelectWheel(usize),
    AddWheel,
    RemoveWheel(usize),
    SetWheelName(usize, String),
    RecordWheelHotkey(usize),
    SetSlotLabel(usize, usize, String),
    SetSlotText(usize, usize, String),
    SetSlotVolume(usize, usize, f64),
    SetSlotMode(usize, usize, bool),
    ClearSlot(usize, usize),
    ImportSlotAudio(usize, usize),
    AssignWheelAudio(usize, usize, String),
    DeleteLibraryAudio(String),
    CropSlotAudio(usize, usize),
    PreviewSlot(usize, usize),
    TrimPreview(u64, f64, f64, f64),
    TrimStop(u64),
    TrimSave(u64, f64, f64, String, f64, usize, usize),
    TrimCancel(u64),
    Closed,
    Feedback(String),
}

impl SettingsAction {
    pub(crate) fn refreshes_audio_library(&self) -> bool {
        use SettingsAction::*;
        // 后台导入和保存由完成事件刷新。常规设置无需遍历托管目录。
        matches!(
            self,
            RefreshAudioLibrary
                | RemoveWheel(_)
                | SetSlotLabel(..)
                | ClearSlot(..)
                | AssignWheelAudio(..)
                | DeleteLibraryAudio(_)
                | CleanUnusedAudio
        )
    }
}

#[cfg(test)]
mod audio_library_tests {
    use super::SettingsAction::*;

    #[test]
    fn refreshes_for_library_changes_and_explicit_open_only() {
        for action in [
            RefreshAudioLibrary,
            RemoveWheel(1),
            SetSlotLabel(0, 1, "名称".into()),
            ClearSlot(0, 1),
            AssignWheelAudio(0, 1, "a.wav".into()),
            DeleteLibraryAudio("a.wav".into()),
            CleanUnusedAudio,
        ] {
            assert!(action.refreshes_audio_library());
        }
        for action in [
            SetVolume(1.0),
            SetOpacity(0.5),
            SelectWheel(0),
            Closed,
            ImportSlotAudio(0, 1),
        ] {
            assert!(!action.refreshes_audio_library());
        }
    }
}
