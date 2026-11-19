use std::io;
use std::path::PathBuf;
use std::time::Duration;

use crate::audio::{self, Recording};

use super::{App, Background};

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
                Ok(peaks) => self.trim_peaks(id, peaks),
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
                    if let Some(trim) = trim {
                        self.trim_save_failed(trim);
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
                if let Some(trim) = trim {
                    self.trim_save_done(trim);
                }
                if rolling {
                    self.config
                        .update(|settings| settings.rolling_buffer_target = [wheel, slot])?;
                }
                if !exiting {
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

fn preserve_rolling(file: tempfile::NamedTempFile) -> io::Result<PathBuf> {
    file.keep()
        .map(|(_, path)| path)
        .map_err(|error| error.error)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn preserving_a_stopped_recording_keeps_the_file_on_disk() {
        let directory = tempfile::tempdir().unwrap();
        let mut file = tempfile::NamedTempFile::new_in(directory.path()).unwrap();
        file.write_all(b"audio").unwrap();
        let path = preserve_rolling(file).unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"audio");
        std::fs::remove_file(path).unwrap();
    }
}
