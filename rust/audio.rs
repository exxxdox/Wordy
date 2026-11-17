use std::collections::VecDeque;
use std::io;

#[path = "audio/managed.rs"]
mod managed;

pub use managed::{
    StagedClip, commit_slot_clip, remove_unreferenced_clip, resolve_clip, stage_clip,
};

#[path = "audio/wave.rs"]
mod wave;

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
    ListenState, PcmStream, Recording, default_rate, list_inputs, list_outputs, play_pcm,
    play_wave, read_listen, restore_listen, set_listen,
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

pub fn cable_endpoint(outputs: &[Endpoint]) -> Option<&Endpoint> {
    cable_endpoint_for(outputs, None)
}

/// Legacy builds stored a custom VB-CABLE friendly name; keep it working when
/// the endpoint no longer contains the default "CABLE Input" marker.
pub fn cable_endpoint_for<'a>(
    outputs: &'a [Endpoint],
    preferred: Option<&str>,
) -> Option<&'a Endpoint> {
    outputs
        .iter()
        .find(|endpoint| endpoint.name.to_lowercase().contains("cable input"))
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
