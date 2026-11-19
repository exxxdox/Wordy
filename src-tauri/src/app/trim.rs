use std::sync::{
    Arc,
    atomic::{AtomicBool, Ordering},
};

use super::*;
use crate::ui::{TrimOpen, TrimPeaks, TrimProgress};

/// React 截取窗口的原生会话：临时 WAV、波形与试听状态。
pub(super) struct TrimSession {
    pub(super) id: u64,
    source: Arc<tempfile::NamedTempFile>,
    cancel: Arc<AtomicBool>,
    rolling: bool,
    duration: f64,
    preview: Option<u64>,
    busy: bool,
}

impl Drop for TrimSession {
    fn drop(&mut self) {
        self.cancel.store(true, Ordering::Release);
    }
}

impl App {
    pub(super) fn open_trim(
        &mut self,
        source: tempfile::NamedTempFile,
        target: Option<(usize, usize)>,
    ) -> io::Result<()> {
        let duration = audio::recording_duration(source.path())?;
        let id = self.id();
        let rolling = target.is_none();
        let (wheel, slot) = target.unwrap_or((
            self.config.settings().rolling_buffer_target[0],
            self.config.settings().rolling_buffer_target[1],
        ));
        let wheel = wheel.min(self.config.settings().wheel_count() - 1);
        let slot = slot.min(crate::config::SLOT_COUNT - 1);
        let item = self.config.settings().wheel(wheel);
        let label = if rolling {
            recording_label()
        } else {
            item.as_ref()
                .map_or(String::new(), |wheel| wheel.labels[slot].clone())
        };
        let gain = item.map_or(1.0, |wheel| wheel.volumes[slot]) * 100.0;
        let cancel = Arc::new(AtomicBool::new(false));
        let source = Arc::new(source);
        let path = source.path().to_path_buf();
        self.trim = Some(TrimSession {
            id,
            source: source.clone(),
            cancel: cancel.clone(),
            rolling,
            duration,
            preview: None,
            busy: false,
        });
        let _ = self.handle.emit(
            ui::TRIM_OPEN_EVENT,
            TrimOpen {
                id,
                duration,
                rolling,
                wheel,
                slot,
                label,
                gain,
            },
        );
        let events = self.jobs.clone();
        std::thread::Builder::new()
            .name("WordyWaveform".into())
            .spawn(move || {
                let result = audio::waveform_peaks(&path, &cancel, 1600);
                let _ = events.send(Background::Recording(RecordingResult::Waveform {
                    id,
                    result,
                }));
            })?;
        Ok(())
    }

    pub(super) fn trim_peaks(&mut self, id: u64, peaks: Vec<f32>) {
        if self.trim.as_ref().is_none_or(|session| session.id != id) {
            return;
        }
        let maximum = peaks.iter().copied().fold(0.0_f32, f32::max);
        let peaks = if maximum > 0.0 {
            peaks.into_iter().map(|peak| peak / maximum).collect()
        } else {
            peaks
        };
        let _ = self
            .handle
            .emit(ui::TRIM_PEAKS_EVENT, TrimPeaks { id, peaks });
    }

    fn active_trim(&mut self, id: u64) -> io::Result<&mut TrimSession> {
        self.trim
            .as_mut()
            .filter(|session| session.id == id && !session.busy)
            .ok_or_else(|| io::Error::other("截取窗口已关闭"))
    }

    pub(super) fn trim_preview(
        &mut self,
        id: u64,
        start: f64,
        end: f64,
        gain: f64,
    ) -> io::Result<()> {
        let duration = self.active_trim(id)?.duration;
        let (start, end) = trim_range(duration, start, end)?;
        let (path, rolling, previous) = {
            let session = self.active_trim(id)?;
            (
                session.source.path().to_path_buf(),
                session.rolling,
                session.preview,
            )
        };
        if previous.is_some() {
            self.playback.stop_clip();
        }
        let clip = audio::trim_recording(&path, start, end)?;
        let clip_path = clip.path().to_path_buf();
        let preview = self.id();
        self.previews.insert(preview, clip);
        self.active_trim(id)?.preview = Some(preview);
        self.playback.play_clip(
            preview,
            clip_path,
            gain.clamp(0.0, 300.0) / 100.0,
            rolling,
            self.config.settings(),
        );
        self.emit_trim_progress(true, Some(start));
        Ok(())
    }

    pub(super) fn trim_stop(&mut self, id: u64) {
        if let Some(session) = self.trim.as_mut()
            && session.id == id
            && session.preview.take().is_some()
        {
            self.playback.stop_clip();
            self.emit_trim_progress(false, None);
        }
    }

    pub(super) fn trim_progress(&mut self, id: u64, seconds: Option<f64>) {
        if let Some(session) = self.trim.as_mut()
            && session.preview == Some(id)
        {
            if seconds.is_none() {
                session.preview = None;
            }
            self.emit_trim_progress(seconds.is_some(), seconds);
        }
    }

    fn emit_trim_progress(&self, playing: bool, seconds: Option<f64>) {
        let _ = self
            .handle
            .emit(ui::TRIM_PROGRESS_EVENT, TrimProgress { playing, seconds });
    }

    pub(super) fn trim_save_failed(&mut self, id: u64) {
        if let Some(session) = self.trim.as_mut()
            && session.id == id
        {
            session.busy = false;
        }
        let _ = self.handle.emit(ui::TRIM_SAVE_FAILED_EVENT, ());
        self.ui_dirty = true;
    }

    pub(super) fn trim_save_done(&mut self, id: u64) {
        if self.trim.as_ref().is_some_and(|session| session.id == id) {
            self.trim = None;
            let _ = self.handle.emit(ui::TRIM_CLOSED_EVENT, ());
        }
        self.ui_dirty = true;
    }

    pub(super) fn trim_cancel(&mut self, id: u64) {
        if self.trim.as_ref().is_none_or(|session| session.id != id) {
            return;
        }
        if self
            .trim
            .as_ref()
            .is_some_and(|session| session.preview.is_some())
        {
            self.playback.stop_clip();
        }
        self.trim = None;
        let _ = self.handle.emit(ui::TRIM_CLOSED_EVENT, ());
        self.log("已取消保存录音".into());
    }

    #[allow(clippy::too_many_arguments)]
    pub(super) fn trim_save(
        &mut self,
        id: u64,
        start: f64,
        end: f64,
        label: &str,
        gain: f64,
        wheel: usize,
        slot: usize,
    ) -> io::Result<()> {
        let duration = self.active_trim(id)?.duration;
        let (start, end) = trim_range(duration, start, end)?;
        if self.config.settings().wheel(wheel).is_none() {
            return Err(io::Error::other("轮盘不存在"));
        }
        let slot = slot.min(crate::config::SLOT_COUNT - 1);
        let (rolling, previewing, source) = {
            let session = self.active_trim(id)?;
            (
                session.rolling,
                session.preview.take().is_some(),
                session.source.clone(),
            )
        };
        if previewing {
            self.playback.stop_clip();
        }
        self.active_trim(id)?.busy = true;
        let config = self.config.path().to_path_buf();
        let events = self.jobs.clone();
        let cancel = self.background_cancel.clone();
        let label = if label.trim().is_empty() {
            "录音片段".into()
        } else {
            label.trim().to_owned()
        };
        std::thread::Builder::new()
            .name("WordyTrimSave".into())
            .spawn(move || {
                let clip = audio::trim_recording(source.path(), start, end);
                let (result, source) = match clip {
                    Ok(clip) => (audio::stage_clip(clip.path(), &config, &cancel), Some(clip)),
                    Err(error) => (Err(error), None),
                };
                let _ = events.send(Background::Recording(RecordingResult::SlotClip {
                    wheel,
                    slot,
                    label,
                    gain: Some(gain.clamp(0.0, 300.0) / 100.0),
                    result,
                    source,
                    trim: Some(id),
                    rolling,
                }));
            })?;
        self.pending_saves += 1;
        self.ui_dirty = true;
        Ok(())
    }
}

pub(super) fn trim_range(duration: f64, start: f64, end: f64) -> io::Result<(f64, f64)> {
    if !duration.is_finite() || duration <= 0.0 {
        return Err(io::Error::other("录音时长无效"));
    }
    let clamp = |value: f64| {
        if value.is_finite() {
            value.clamp(0.0, duration)
        } else {
            0.0
        }
    };
    let (mut start, mut end) = (clamp(start), clamp(end));
    if start > end {
        std::mem::swap(&mut start, &mut end);
    }
    if end - start < 0.001 {
        return Err(io::Error::other("截取范围太短"));
    }
    Ok((start, end))
}

pub(super) fn recording_label() -> String {
    let now = local_time();
    format!("录音 {:02}{:02}{:02}", now.wHour, now.wMinute, now.wSecond)
}
