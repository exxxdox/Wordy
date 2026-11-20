use std::io;
use std::path::PathBuf;
use std::sync::atomic::AtomicBool;
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
            self.play_cue(false);
            let events = self.jobs.clone();
            std::thread::Builder::new()
                .name("WordyRollingCapture".into())
                .spawn(move || {
                    let result = recording.stop().and_then(|pcm| audio::write_wave(&pcm));
                    let _ = events.send(Background::Recording(RecordingResult::Rolling(result)));
                })?;
            self.pending_saves += 1;
            // The save window is the next step; keep the log short like the old toast.
            self.log("后台录制已结束，正在准备保存窗口".into());
        } else if self.pending_saves == 0 && self.trim.is_none() && self.pending_trim.is_none() {
            let seconds = self.config.settings().rolling_buffer_seconds;
            self.recording = Some(Recording::start(Duration::from_secs_f64(seconds))?);
            self.tray.set_recording(true);
            // 提示音代替系统通知：开始和停止各用一段不同的短音。
            self.play_cue(true);
            self.log(format!(
                "后台录制已开始（保留最近 {seconds:.0} 秒；再按一次结束并截取）"
            ));
        } else {
            return Err(io::Error::other("请先结束当前保存或音频截取"));
        }
        Ok(())
    }

    /// 提示音只走本地播放设备（与返听同一套解析），不占虚拟声卡、也不弹系统通知。
    fn play_cue(&mut self, start: bool) {
        let settings = self.config.settings().clone();
        let spawned = std::thread::Builder::new()
            .name("WordyCue".into())
            .spawn(move || {
                let Ok(outputs) = audio::list_outputs() else {
                    return;
                };
                // preview_local 让 route 只解析本地设备，不会送到 VB-CABLE。
                let route = audio::route(&settings, true, &outputs);
                let cancel = AtomicBool::new(false);
                let _ = audio::play_routed_pcm(&route, &cue_tone(start), &cancel);
            });
        if let Err(error) = spawned {
            self.log(format!("无法播放录制提示音：{error}"));
        }
    }

    pub(super) fn import_slot_clip(
        &mut self,
        wheel: usize,
        slot: usize,
        path: PathBuf,
    ) -> io::Result<()> {
        // 覆盖已占用槽位会删除旧音频，先确认；空槽直接导入。
        if !self.confirm_slot_replace(wheel, slot) {
            return Ok(());
        }
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
                let result = audio::decode_clip(&path, &cancel).and_then(trim_input);
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

/// 录制开始/结束提示音：频率和时长都不同，5 ms 淡入淡出避免爆音。
fn cue_tone(start: bool) -> audio::Pcm {
    const RATE: u32 = 44_100;
    let (frequency, millis) = if start {
        (880.0_f32, 120)
    } else {
        (560.0_f32, 160)
    };
    let total = RATE as usize * millis / 1000;
    let fade = (RATE / 200) as f32;
    let samples = (0..total)
        .map(|index| {
            let edge = index.min(total - 1 - index) as f32;
            let envelope = (edge / fade).min(1.0);
            let wave = (std::f32::consts::TAU * frequency * index as f32 / RATE as f32).sin();
            (wave * envelope * 0.22 * f32::from(i16::MAX)) as i16
        })
        .collect();
    audio::Pcm {
        sample_rate: RATE,
        channels: 1,
        samples,
    }
}

fn trim_input(decoded: audio::DecodedClip<'_>) -> io::Result<tempfile::NamedTempFile> {
    match decoded {
        // The decoder's temporary WAV already belongs exclusively to this trim session.
        audio::DecodedClip::Temporary(file) => Ok(file),
        audio::DecodedClip::Original(path) => {
            // The slot's source may be replaced or deleted while the trim session is open.
            let mut source = std::fs::File::open(path)?;
            let mut copy = tempfile::Builder::new().suffix(".wav").tempfile()?;
            io::copy(&mut source, copy.as_file_mut())?;
            Ok(copy)
        }
    }
}

pub(super) fn preserve_rolling(file: tempfile::NamedTempFile) -> io::Result<PathBuf> {
    file.keep()
        .map(|(_, path)| path)
        .map_err(|error| error.error)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn recording_cues_differ_and_fade_to_silence() {
        let start = cue_tone(true);
        let stop = cue_tone(false);
        assert_ne!(start.samples.len(), stop.samples.len());
        for cue in [&start, &stop] {
            assert_eq!(cue.channels, 1);
            assert_eq!(cue.samples.first(), Some(&0));
            assert_eq!(cue.samples.last(), Some(&0));
            assert!(cue.samples.iter().any(|&sample| sample != 0));
        }
    }

    #[test]
    fn trim_input_reuses_the_decoded_temporary_wave() {
        let wave = audio::write_wave(&audio::Pcm {
            sample_rate: 48_000,
            channels: 2,
            samples: vec![12, -34],
        })
        .unwrap();
        let original_path = wave.path().to_path_buf();
        let input = trim_input(audio::DecodedClip::Temporary(wave)).unwrap();
        assert_eq!(input.path(), original_path);
        assert_eq!(audio::read_wave(input.path()).unwrap().samples, [12, -34]);
    }

    #[test]
    fn trim_input_copies_a_wave_owned_by_the_slot() {
        let wave = audio::write_wave(&audio::Pcm {
            sample_rate: 48_000,
            channels: 2,
            samples: vec![56, -78],
        })
        .unwrap();
        let input = trim_input(audio::DecodedClip::Original(wave.path())).unwrap();
        assert_ne!(input.path(), wave.path());
        drop(wave);
        assert_eq!(audio::read_wave(input.path()).unwrap().samples, [56, -78]);
    }

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
