use std::collections::VecDeque;
use std::io;
use std::path::Path;

use crate::config::AudioIdentity;

#[path = "audio/managed.rs"]
mod managed;

pub(crate) use managed::clips_directory;
pub use managed::{
    StagedClip, commit_slot_clip, remove_unreferenced_clip, resolve_clip, stage_clip,
};

pub(crate) const AUDIO_EXTENSIONS: [&str; 10] = [
    "wav", "mp3", "ogg", "flac", "m4a", "aac", "wma", "aiff", "aif", "opus",
];

pub(crate) fn audio_extension(path: &Path) -> Option<String> {
    let extension = path.extension()?.to_str()?.to_ascii_lowercase();
    AUDIO_EXTENSIONS
        .contains(&extension.as_str())
        .then_some(extension)
}

#[path = "audio/wave.rs"]
mod wave;

#[cfg(windows)]
pub(crate) use wave::validate_clip;
pub use wave::{
    DecodedClip, decode_clip, read_tts_wave, read_wave, recording_duration, trim_recording,
    waveform_peaks, write_wave,
};

#[cfg(windows)]
#[path = "audio/wasapi.rs"]
mod wasapi;

#[cfg(windows)]
#[path = "audio/sidetone.rs"]
mod sidetone;

#[cfg(windows)]
pub use sidetone::{RoutedStream, play_routed_pcm, play_routed_wave};

#[cfg(windows)]
pub use wasapi::{
    ListenState, PcmStream, Recording, default_rate, list_inputs, list_outputs, play_wave,
    read_listen, restore_listen, set_listen,
};

#[cfg(test)]
#[path = "audio/tests.rs"]
mod tests;

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Endpoint {
    pub id: String,
    pub name: String,
    pub is_default: bool,
}

#[derive(Debug, Eq, PartialEq)]
pub struct PlaybackRoute<'a> {
    pub main: Option<&'a Endpoint>,
    pub local: Option<&'a Endpoint>,
    pub preview_local: bool,
}

pub fn is_cable_device(name: &str) -> bool {
    let name = name.to_lowercase();
    [
        "cable input",
        "cable in ",
        "vb-cable",
        "vb-audio virtual cable",
    ]
    .iter()
    .any(|part| name.contains(part))
}

/// Legacy builds stored a custom VB-CABLE friendly name; keep it working when
/// the endpoint no longer contains the default "CABLE Input" marker.
pub fn cable_endpoint_for<'a>(
    outputs: &'a [Endpoint],
    preferred: Option<&str>,
) -> Option<&'a Endpoint> {
    // One rule for every VB-CABLE check, so a renamed cable endpoint is still
    // recognized by the main route and hidden from the local device list.
    outputs
        .iter()
        .find(|endpoint| is_cable_device(&endpoint.name))
        .or_else(|| {
            let preferred = preferred?.trim().to_lowercase();
            if preferred.is_empty() {
                None
            } else {
                outputs
                    .iter()
                    .find(|endpoint| endpoint.name.to_lowercase().contains(&preferred))
            }
        })
}

/// Exact name match first, then a unique case-insensitive substring. A renamed
/// device keeps working while an ambiguous fragment is rejected.
pub(crate) fn endpoint_index<'a>(
    names: impl IntoIterator<Item = &'a str>,
    name: &str,
) -> Option<usize> {
    let target = name.trim().to_lowercase();
    if target.is_empty() {
        return None;
    }
    let names: Vec<String> = names
        .into_iter()
        .map(|name| name.trim().to_lowercase())
        .collect();
    if let Some(index) = names.iter().position(|name| *name == target) {
        return Some(index);
    }
    let mut matches = names
        .iter()
        .enumerate()
        .filter(|(_, name)| name.contains(&target));
    let (index, _) = matches.next()?;
    matches.next().is_none().then_some(index)
}

pub fn find_endpoint<'a>(endpoints: &'a [Endpoint], name: &str) -> Option<&'a Endpoint> {
    endpoint_index(
        endpoints.iter().map(|endpoint| endpoint.name.as_str()),
        name,
    )
    .map(|index| &endpoints[index])
}

/// Resolve a saved output selection to a current endpoint. The stable endpoint
/// id wins; the saved name is a fallback for older configs and renamed devices.
pub fn resolve_output<'a>(
    outputs: &'a [Endpoint],
    identity: Option<&AudioIdentity>,
    legacy_name: Option<&str>,
) -> Option<&'a Endpoint> {
    if let Some(id) = identity
        .and_then(|identity| identity.id.as_deref())
        .filter(|id| !id.is_empty())
        && let Some(endpoint) = outputs.iter().find(|endpoint| endpoint.id == id)
    {
        return Some(endpoint);
    }
    let name = identity
        .map(|identity| identity.name.as_str())
        .or(legacy_name)?;
    find_endpoint(outputs, name)
}

pub fn route<'a>(
    main_enabled: bool,
    sidetone_enabled: bool,
    preview_local: bool,
    selected_name: Option<&str>,
    outputs: &'a [Endpoint],
    preferred_main: Option<&str>,
) -> PlaybackRoute<'a> {
    let main = if main_enabled && !preview_local {
        cable_endpoint_for(outputs, preferred_main)
    } else {
        None
    };
    let local = if sidetone_enabled || preview_local {
        // Older configurations could explicitly select the virtual endpoint as the local device.
        let selected_name = selected_name.filter(|name| !is_cable_device(name));
        outputs
            .iter()
            .find(|endpoint| match selected_name {
                Some(name) => endpoint.name == name,
                None => endpoint.is_default,
            })
            .filter(|endpoint| !is_cable_device(&endpoint.name))
    } else {
        None
    };
    PlaybackRoute {
        main,
        local,
        preview_local,
    }
}

pub fn apply_gain(samples: &[i16], gain: f64) -> io::Result<Vec<i16>> {
    if !gain.is_finite() || !(0.0..=3.0).contains(&gain) {
        return Err(invalid_audio("音频音量必须在 0 到 3 之间"));
    }
    // Float-to-integer casts truncate and saturate, matching the PCM16 playback path.
    Ok(samples
        .iter()
        .map(|&sample| (f64::from(sample) * gain) as i16)
        .collect())
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Pcm {
    pub sample_rate: u32,
    pub channels: u16,
    pub samples: Vec<i16>,
}

impl Pcm {
    pub fn into_recording_format(mut self) -> io::Result<Self> {
        const TARGET_RATE: u32 = 48_000;
        let channels = usize::from(self.channels);
        if self.sample_rate == 0 || channels == 0 || !self.samples.len().is_multiple_of(channels) {
            return Err(invalid_audio("无效的录制音频格式"));
        }
        let source_frames = self.samples.len() / channels;
        if self.sample_rate == TARGET_RATE && channels == 2 {
            return Ok(self);
        }
        if source_frames == 0 {
            self.sample_rate = TARGET_RATE;
            self.channels = 2;
            return Ok(self);
        }
        let frames = (source_frames as f64 * f64::from(TARGET_RATE) / f64::from(self.sample_rate))
            .round_ties_even()
            .max(1.0) as usize;
        let mut samples = Vec::with_capacity(frames.saturating_mul(2));
        for frame in 0..frames {
            let position = if frames > 1 {
                frame as f64 * (source_frames - 1) as f64 / (frames - 1) as f64
            } else {
                0.0
            };
            let first = position as usize;
            let second = (first + 1).min(source_frames - 1);
            let fraction = position - first as f64;
            // Copy mono to both sides and take the first two channels of wider endpoints.
            for channel in [0, usize::from(channels > 1)] {
                let a = f64::from(self.samples[first * channels + channel]);
                let b = f64::from(self.samples[second * channels + channel]);
                samples.push((a + fraction * (b - a)).round_ties_even() as i16);
            }
        }
        Ok(Self {
            sample_rate: TARGET_RATE,
            channels: 2,
            samples,
        })
    }
}

#[cfg(any(windows, test))]
struct CaptureBuffer {
    samples: VecDeque<i16>,
    channels: usize,
    max_samples: Option<usize>,
}

#[cfg(any(windows, test))]
impl CaptureBuffer {
    fn new(channels: u16, max_frames: Option<usize>) -> Self {
        let channels = usize::from(channels);
        Self {
            samples: VecDeque::new(),
            channels,
            max_samples: max_frames.map(|frames| frames.saturating_mul(channels)),
        }
    }

    fn push(&mut self, samples: &[i16]) {
        let usable = samples.len() - samples.len() % self.channels;
        let samples = &samples[..usable];
        if let Some(maximum) = self.max_samples {
            let samples = &samples[samples.len().saturating_sub(maximum)..];
            let excess = (self.samples.len() + samples.len()).saturating_sub(maximum);
            self.samples.drain(..excess);
            self.samples.extend(samples);
        } else {
            self.samples.extend(samples);
        }
    }

    fn into_samples(self) -> Vec<i16> {
        self.samples.into()
    }
}

fn invalid_audio(message: &'static str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidInput, message)
}
