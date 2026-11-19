use std::io;
use std::path::PathBuf;
use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};
use std::time::Duration;

use windows_sys::Win32::Foundation::HWND;
use windows_sys::Win32::UI::WindowsAndMessaging::{CBN_SELCHANGE, EN_KILLFOCUS, IDCANCEL};

use crate::audio::{self, Recording};
use crate::panels::{self, Trim};

use super::{App, Background, append_unbound_wheel, gain, local_time};

// Handle clip saves alongside recording because both can update or close the trim session.
pub(super) enum RecordingResult {
    Rolling(io::Result<tempfile::NamedTempFile>),
    Waveform {
        id: u64,
        result: io::Result<Vec<f32>>,
    },
    SlotClip {
        wheel: usize,
        slot: usize,
        label: String,
        gain: Option<f64>,
        result: io::Result<audio::StagedClip>,
        source: Option<tempfile::NamedTempFile>,
        trim: Option<u64>,
        rolling: bool,
    },
    TrimInput {
        target: (usize, usize),
        result: io::Result<tempfile::NamedTempFile>,
    },
}

pub(super) struct TrimSession {
    pub(super) window: Trim,
    id: u64,
    source: Arc<tempfile::NamedTempFile>,
    cancel: Arc<AtomicBool>,
    preview: Option<u64>,
    busy: bool,
}

impl Drop for TrimSession {
    fn drop(&mut self) {
        self.cancel.store(true, Ordering::Release);
    }
}

impl TrimSession {
    pub(super) fn discard(self) {
        // No save happens: dropping the session removes the temporary source
        // once the waveform reader observes the cancel flag.
        drop(self);
    }

    pub(super) fn playback_progress(&mut self, id: u64, seconds: Option<f64>) {
        if self.preview == Some(id) {
            if seconds.is_none() {
                self.preview = None;
            }
            self.window.progress(seconds);
        }
    }
}

impl App {
    pub(super) fn toggle_recording(&mut self) -> io::Result<()> {
        // Stopping an active recording is always allowed; only starting one and
        // writing its result would touch config during a backup.
        if self.backup_running && self.recording.is_none() {
            return Err(io::Error::other("正在处理备份，请稍候"));
        }
        if let Some(recording) = self.recording.take() {
            self.tray.set_recording(false);
            let events = self.jobs.clone();
            std::thread::Builder::new()
                .name("WordyRollingCapture".into())
                .spawn(move || {
                    let result = recording.stop().and_then(|pcm| audio::write_wave(&pcm));
                    let _ = events.send(Background::Recording(RecordingResult::Rolling(result)));
                })?;
            self.pending_saves += 1;
            // The save window is the next step; a late system notification would cover it.
            self.log("后台录制已结束，正在准备保存窗口".into());
        } else if self.pending_saves == 0 && self.trim.is_none() {
            let seconds = self.config.settings().rolling_buffer_seconds;
            self.recording = Some(Recording::start(Duration::from_secs_f64(seconds))?);
            self.tray.set_recording(true);
            self.notify(format!(
                "后台录制已开始（保留最近 {seconds:.0} 秒；再按一次结束并截取）"
            ));
        } else {
            return Err(io::Error::other("请先结束当前保存或音频截取"));
        }
        Ok(())
    }

    fn open_trim(
        &mut self,
        source: tempfile::NamedTempFile,
        target: Option<(usize, usize)>,
    ) -> io::Result<()> {
        if self.trim.is_some() {
            return Err(io::Error::other("请先关闭当前截取窗口"));
        }
        let duration = audio::recording_duration(source.path())?;
        let gain = target
            .and_then(|(wheel, slot)| {
                self.config
                    .settings()
                    .wheel(wheel)
                    .map(|wheel| wheel.volumes[slot])
            })
            .unwrap_or(1.0);
        let window = Trim::new(
            self.host,
            self.ui_hwnd(),
            self.config.settings(),
            target,
            duration,
            gain,
        )?;
        if let Some((wheel, slot)) = target
            && let Some(wheel) = self.config.settings().wheel(wheel)
        {
            window.panel.set_text(panels::LABEL, &wheel.labels[slot]);
        } else {
            window.panel.set_text(panels::LABEL, &recording_label());
        }
        let source = Arc::new(source);
        let cancel = Arc::new(AtomicBool::new(false));
        let id = self.id();
        let events = self.jobs.clone();
        let file = source.clone();
        let cancel_waveform = cancel.clone();
        std::thread::Builder::new()
            .name("WordyWaveform".into())
            .spawn(move || {
                let result = audio::waveform_peaks(file.path(), &cancel_waveform, 1600);
                let _ = events.send(Background::Recording(RecordingResult::Waveform {
                    id,
                    result,
                }));
            })?;
        let trim = self.trim.insert(TrimSession {
            window,
            id,
            source,
            cancel,
            preview: None,
            busy: false,
        });
        // Activate only after the label and session are ready for the first paint and input.
        trim.window.panel.show();
        Ok(())
    }

    pub(super) fn trim_action(&mut self, command: usize, hwnd: HWND) -> io::Result<()> {
        use panels::*;
        const CANCEL: u16 = IDCANCEL as u16;
        let Some(session) = &mut self.trim else {
            return Ok(());
        };
        if session.window.panel.0 != hwnd || session.busy {
            return Ok(());
        }
        let (action, notification) = (command as u16, (command >> 16) as u32);
        // SAVE/ADD write config and audio; playback and closing stay available.
        if self.backup_running && matches!(action, SAVE | ADD) {
            return Ok(());
        }
        match action {
            PICK => {
                if let Some(slot) = session.window.take_slot() {
                    session.window.slot = slot;
                    session.window.refresh(self.config.settings())?;
                }
            }
            WHEEL if notification == CBN_SELCHANGE => {
                session.window.wheel = selected(&session.window.panel, WHEEL);
                session.window.refresh(self.config.settings())?;
            }
            ADD if notification == 0 => {
                // Same unbound append path as the wheel editor.
                session.window.wheel = append_unbound_wheel(&mut self.config, &mut self.hotkeys)?;
                session.window.slot = 0;
                session.window.refresh(self.config.settings())?;
            }
            WAVE | STOP => {
                if session.preview.take().is_some() {
                    self.playback.stop_clip();
                }
                session.window.progress(None);
                if action == WAVE {
                    session.window.update_range_fields();
                }
            }
            START | END if notification == EN_KILLFOCUS => {
                if session.preview.take().is_some() {
                    self.playback.stop_clip();
                }
                session.window.progress(None);
                session.window.range()?;
            }
            PLAY if notification == 0 => {
                let range = session.window.range()?;
                let gain = gain(&session.window.panel.text(GAIN))?;
                let clip = audio::trim_recording(session.source.path(), range.start, range.end)?;
                let path = clip.path().to_path_buf();
                self.next_job += 1;
                let id = self.next_job;
                session.preview = Some(id);
                session.window.set_previewing(true);
                self.previews.insert(id, clip);
                self.playback.play_clip(
                    id,
                    path,
                    gain,
                    session.window.rolling,
                    self.config.settings(),
                );
            }
            SAVE if notification == 0 => {
                if session.preview.take().is_some() {
                    self.playback.stop_clip();
                }
                let range = session.window.range()?;
                let gain = gain(&session.window.panel.text(GAIN))?;
                let label = session.window.panel.text(LABEL).trim().to_owned();
                let (wheel, slot) = (session.window.wheel, session.window.slot);
                let source = session.source.clone();
                let id = session.id;
                let rolling = session.window.rolling;
                let config = self.config.path().to_path_buf();
                let events = self.jobs.clone();
                let cancel = self.background_cancel.clone();
                std::thread::Builder::new()
                    .name("WordyTrimSave".into())
                    .spawn(move || {
                        let clip = audio::trim_recording(source.path(), range.start, range.end);
                        let (result, source) = match clip {
                            Ok(clip) => {
                                (audio::stage_clip(clip.path(), &config, &cancel), Some(clip))
                            }
                            Err(error) => (Err(error), None),
                        };
                        let _ = events.send(Background::Recording(RecordingResult::SlotClip {
                            wheel,
                            slot,
                            label: if label.is_empty() {
                                "录音片段".into()
                            } else {
                                label
                            },
                            gain: Some(gain),
                            result,
                            source,
                            trim: Some(id),
                            rolling,
                        }));
                    })?;
                session.busy = true;
                self.pending_saves += 1;
            }
            CANCEL if notification == 0 => {
                if session.preview.is_some() {
                    self.playback.stop_clip();
                }
                if let Some(session) = self.trim.take() {
                    session.discard();
                }
                self.log("已取消保存录音".into());
            }
            _ => (),
        }
        Ok(())
    }

    pub(super) fn import_slot_clip(
        &mut self,
        wheel: usize,
        slot: usize,
        path: PathBuf,
    ) -> io::Result<()> {
        self.playback.stop_clip();
        let label = path
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy()
            .into_owned();
        let config = self.config.path().to_path_buf();
        let events = self.jobs.clone();
        let cancel = self.background_cancel.clone();
        std::thread::Builder::new()
            .name("WordyClipImport".into())
            .spawn(move || {
                let result = audio::stage_clip(&path, &config, &cancel);
                let _ = events.send(Background::Recording(RecordingResult::SlotClip {
                    wheel,
                    slot,
                    label,
                    gain: None,
                    result,
                    source: None,
                    trim: None,
                    rolling: false,
                }));
            })?;
        self.pending_saves += 1;
        Ok(())
    }

    pub(super) fn prepare_trim(
        &mut self,
        wheel: usize,
        slot: usize,
        path: PathBuf,
    ) -> io::Result<()> {
        let events = self.jobs.clone();
        let cancel = self.background_cancel.clone();
        self.playback.stop_clip();
        std::thread::Builder::new()
            .name("WordyDecodeTrim".into())
            .spawn(move || {
                let result = (|| {
                    let decoded = audio::decode_clip(&path, &cancel)?;
                    let mut source = std::fs::File::open(decoded.as_ref())?;
                    let mut copy = tempfile::Builder::new().suffix(".wav").tempfile()?;
                    io::copy(&mut source, copy.as_file_mut())?;
                    Ok(copy)
                })();
                let _ = events.send(Background::Recording(RecordingResult::TrimInput {
                    target: (wheel, slot),
                    result,
                }));
            })?;
        self.pending_saves += 1;
        Ok(())
    }

    pub(super) fn complete_recording(
        &mut self,
        result: RecordingResult,
        exiting: bool,
    ) -> io::Result<()> {
        match result {
            RecordingResult::Rolling(result) => {
                self.pending_saves -= 1;
                if exiting {
                    // The save window cannot open during shutdown. Keep the stopped
                    // recording instead of dropping its only temporary file.
                    match result {
                        Ok(file) => match preserve_rolling(file) {
                            Ok(path) => self.log(format!("已停止的录音保存在 {}", path.display())),
                            Err(error) => self.log(format!("无法保留录音临时文件：{error}")),
                        },
                        Err(error) => self.log(format!("后台录制保存失败：{error}")),
                    }
                } else {
                    self.open_trim(result?, None)?;
                }
            }
            RecordingResult::Waveform { id, result } => match result {
                Ok(peaks) => {
                    if let Some(trim) = &mut self.trim
                        && trim.id == id
                    {
                        trim.window.set_peaks(peaks);
                    }
                }
                Err(error) if error.kind() == io::ErrorKind::Interrupted => (),
                Err(error) => return Err(error),
            },
            RecordingResult::TrimInput { target, result } => {
                self.pending_saves -= 1;
                if !exiting {
                    self.open_trim(result?, Some(target))?;
                }
            }
            RecordingResult::SlotClip {
                wheel,
                slot,
                label,
                gain,
                result,
                source,
                trim,
                rolling,
            } => {
                self.pending_saves -= 1;
                let result = result.and_then(|staged| {
                    audio::commit_slot_clip(&mut self.config, wheel, slot, staged, &label, gain)
                });
                if let Err(error) = result {
                    if let Some(session) = &mut self.trim
                        && Some(session.id) == trim
                    {
                        session.busy = false;
                    }
                    return if let Some(source) = source {
                        match source.keep() {
                            Ok((_, path)) => Err(io::Error::other(format!(
                                "{error}（临时文件保留在 {}）",
                                path.display()
                            ))),
                            Err(retain) => Err(io::Error::other(format!(
                                "{error}（无法保留临时文件：{retain}）"
                            ))),
                        }
                    } else {
                        Err(error)
                    };
                }
                if let Some(session) = &self.trim
                    && Some(session.id) == trim
                {
                    self.trim = None;
                }
                if rolling {
                    self.config
                        .update(|settings| settings.rolling_buffer_target = [wheel, slot])?;
                }
                if !exiting {
                    self.refresh_editor()?;
                    // In-settings operations report where the user is looking.
                    if self.settings_visible() {
                        self.status(format!("已保存到轮盘 {} · 音频 {}", wheel + 1, slot + 1));
                    } else {
                        self.notify(format!("已保存到轮盘 {} · 音频 {}", wheel + 1, slot + 1));
                    }
                }
            }
        }
        Ok(())
    }
}

fn recording_label() -> String {
    let now = local_time();
    format!("录音 {:02}{:02}{:02}", now.wHour, now.wMinute, now.wSecond)
}

fn preserve_rolling(file: tempfile::NamedTempFile) -> io::Result<PathBuf> {
    file.keep()
        .map(|(_, path)| path)
        .map_err(|error| error.error)
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::Settings;
    use crate::desktop::host_window;
    use crate::panels::Trim;
    use std::io::Write;
    use windows_sys::Win32::System::LibraryLoader::GetModuleHandleW;
    use windows_sys::Win32::UI::WindowsAndMessaging::DestroyWindow;

    #[test]
    fn preserving_a_stopped_recording_keeps_the_file_on_disk() {
        let directory = tempfile::tempdir().unwrap();
        let mut file = tempfile::NamedTempFile::new_in(directory.path()).unwrap();
        file.write_all(b"audio").unwrap();
        let path = preserve_rolling(file).unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"audio");
        std::fs::remove_file(path).unwrap();
    }

    #[test]
    fn discarding_a_trim_session_deletes_the_recording_source() {
        let instance = unsafe { GetModuleHandleW(std::ptr::null()) };
        let host = host_window(instance).unwrap();
        let window = Trim::new(
            host,
            std::ptr::null_mut(),
            &Settings::default(),
            None,
            1.0,
            1.0,
        )
        .unwrap();
        let directory = tempfile::tempdir().unwrap();
        let file = tempfile::NamedTempFile::new_in(directory.path()).unwrap();
        let path = file.path().to_owned();
        TrimSession {
            window,
            id: 1,
            source: Arc::new(file),
            cancel: Arc::new(AtomicBool::new(false)),
            preview: None,
            busy: false,
        }
        .discard();
        assert!(!path.exists());
        unsafe { DestroyWindow(host) };
    }
}
