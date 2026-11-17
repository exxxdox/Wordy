use std::env;
use std::fs::File;
use std::io::{self, Cursor, Read, Seek, SeekFrom, Write};
use std::path::Path;
use std::process::{Child, Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::thread;
use std::time::{Duration, Instant};

use tempfile::{Builder, NamedTempFile};

use super::{Pcm, invalid_audio};

#[cfg(test)]
#[path = "wave_tests.rs"]
mod tests;

const BLOCK_FRAMES: usize = 1024;
const PCM_SUBTYPE: [u8; 16] = [1, 0, 0, 0, 0, 0, 16, 0, 128, 0, 0, 170, 0, 56, 155, 113];

#[derive(Clone, Copy)]
struct WaveFormat {
    sample_rate: u32,
    channels: u16,
    sample_width: u16,
}

impl WaveFormat {
    fn block_align(self) -> usize {
        usize::from(self.channels) * usize::from(self.sample_width)
    }
}

pub(super) struct WaveFile {
    file: File,
    format: WaveFormat,
    data_start: u64,
    pub(super) total_frames: u64,
    remaining_frames: u64,
}

impl WaveFile {
    pub(super) fn open(path: &Path) -> io::Result<Self> {
        let mut file = File::open(path)?;
        let size = file.metadata()?.len();
        let (format, data_start, frames) = parse_wave(&mut file, size, false)?;
        Ok(Self {
            file,
            format,
            data_start,
            total_frames: frames,
            remaining_frames: frames,
        })
    }

    pub(super) fn sample_rate(&self) -> u32 {
        self.format.sample_rate
    }

    pub(super) fn channels(&self) -> u16 {
        self.format.channels
    }

    pub(super) fn read_pcm16(
        &mut self,
        frames: usize,
        buffer: &mut Vec<u8>,
    ) -> io::Result<Vec<i16>> {
        if self.format.sample_width != 2 {
            return Err(invalid_audio("音频需要 16 位 PCM"));
        }
        let frames = self.remaining_frames.min(frames as u64);
        buffer.resize(frames as usize * self.format.block_align(), 0);
        self.file.read_exact(buffer)?;
        self.remaining_frames -= frames;
        Ok(buffer
            .as_chunks::<2>()
            .0
            .iter()
            .copied()
            .map(i16::from_le_bytes)
            .collect())
    }
}

fn parse_wave(
    input: &mut (impl Read + Seek),
    size: u64,
    from_tts: bool,
) -> io::Result<(WaveFormat, u64, u64)> {
    let mut header = [0; 12];
    input.read_exact(&mut header)?;
    if &header[..4] != b"RIFF" || &header[8..] != b"WAVE" {
        return Err(invalid_audio("文件不是有效的 RIFF/WAV 音频"));
    }
    let riff_size = u32::from_le_bytes(header[4..8].try_into().unwrap());
    let mut end = u64::from(riff_size) + 8;
    if end > size {
        // Cartesia sends a WAVE stream with placeholder sizes. Imported files stay strict.
        if from_tts && riff_size >= 0xffff_fffe {
            end = size;
        } else {
            return Err(invalid_audio("录音文件不完整"));
        }
    }
    let mut position = 12;
    let mut format = None;
    while position + 8 <= end {
        input.seek(SeekFrom::Start(position))?;
        let mut chunk = [0; 8];
        input.read_exact(&mut chunk)?;
        let length = u64::from(u32::from_le_bytes(chunk[4..].try_into().unwrap()));
        let start = position + 8;
        let unknown = from_tts && &chunk[..4] == b"data" && length >= 0xffff_fffe;
        if start + length > end && !unknown {
            return Err(invalid_audio("录音文件不完整"));
        }
        match &chunk[..4] {
            b"fmt " => format = Some(read_format(input, length)?),
            b"data" => {
                let format = format.ok_or_else(|| invalid_audio("WAV 缺少音频格式"))?;
                let align = format.block_align() as u64;
                if !unknown && !length.is_multiple_of(align) {
                    return Err(invalid_audio("WAV 数据未对齐到音频帧"));
                }
                let frames = if unknown {
                    (end - start) / align
                } else {
                    length / align
                };
                return Ok((format, start, frames));
            }
            _ => {}
        }
        position = start + length + (length & 1);
    }
    Err(invalid_audio("WAV 缺少音频数据"))
}

fn read_format(file: &mut impl Read, length: u64) -> io::Result<WaveFormat> {
    if length < 16 {
        return Err(invalid_audio("无效的 WAV 头"));
    }
    let mut header = [0; 16];
    file.read_exact(&mut header)?;
    let tag = u16::from_le_bytes([header[0], header[1]]);
    if tag == 0xfffe && length >= 40 {
        let mut extended = [0; 24];
        file.read_exact(&mut extended)?;
        if extended[8..] != PCM_SUBTYPE {
            return Err(invalid_audio("WAV 需要整数 PCM 音频"));
        }
    } else if tag != 1 {
        return Err(invalid_audio("WAV 需要整数 PCM 音频"));
    }
    let sample_bits = u16::from_le_bytes([header[14], header[15]]);
    let format = WaveFormat {
        sample_rate: u32::from_le_bytes(header[4..8].try_into().unwrap()),
        channels: u16::from_le_bytes([header[2], header[3]]),
        sample_width: sample_bits / 8,
    };
    let align = usize::from(u16::from_le_bytes([header[12], header[13]]));
    if !matches!(sample_bits, 8 | 16 | 24 | 32)
        || format.sample_rate == 0
        || format.channels == 0
        || align != format.block_align()
    {
        return Err(invalid_audio("无效的 PCM 音频格式"));
    }
    Ok(format)
}

pub fn read_tts_wave(bytes: &[u8]) -> io::Result<Pcm> {
    let (format, data_start, frames) =
        parse_wave(&mut Cursor::new(bytes), bytes.len() as u64, true)?;
    if format.sample_width != 2 || frames == 0 {
        return Err(invalid_audio("TTS 未返回有效的 PCM16 音频"));
    }
    let end = data_start + frames * format.block_align() as u64;
    let samples = bytes[data_start as usize..end as usize]
        .as_chunks::<2>()
        .0
        .iter()
        .copied()
        .map(i16::from_le_bytes)
        .collect();
    Ok(Pcm {
        sample_rate: format.sample_rate,
        channels: format.channels,
        samples,
    })
}

fn write_header(file: &mut File, format: WaveFormat, data_bytes: u64) -> io::Result<()> {
    let align = u16::try_from(format.block_align()).map_err(|_| invalid_audio("WAV 声道数过多"))?;
    let average_bytes = format
        .sample_rate
        .checked_mul(u32::from(align))
        .ok_or_else(|| invalid_audio("WAV 采样率过大"))?;
    let riff_size = u32::try_from(data_bytes + (data_bytes & 1) + 36)
        .map_err(|_| invalid_audio("WAV 音频过大"))?;
    file.write_all(b"RIFF")?;
    file.write_all(&riff_size.to_le_bytes())?;
    file.write_all(b"WAVEfmt \x10\0\0\0\x01\0")?;
    file.write_all(&format.channels.to_le_bytes())?;
    file.write_all(&format.sample_rate.to_le_bytes())?;
    file.write_all(&average_bytes.to_le_bytes())?;
    file.write_all(&align.to_le_bytes())?;
    file.write_all(&(format.sample_width * 8).to_le_bytes())?;
    file.write_all(b"data")?;
    file.write_all(&(data_bytes as u32).to_le_bytes())
}

pub fn read_wave(path: &Path) -> io::Result<Pcm> {
    let mut wave = WaveFile::open(path)?;
    let mut buffer = Vec::new();
    let mut samples = Vec::new();
    while wave.remaining_frames > 0 {
        samples.extend(wave.read_pcm16(BLOCK_FRAMES, &mut buffer)?);
    }
    Ok(Pcm {
        sample_rate: wave.sample_rate(),
        channels: wave.channels(),
        samples,
    })
}

pub fn write_wave(pcm: &Pcm) -> io::Result<NamedTempFile> {
    if pcm.sample_rate == 0
        || pcm.channels == 0
        || !pcm.samples.len().is_multiple_of(usize::from(pcm.channels))
    {
        return Err(invalid_audio("无效的 PCM 音频格式"));
    }
    let mut temporary = Builder::new()
        .prefix("wordy-record-")
        .suffix(".wav")
        .tempfile()?;
    let file = temporary.as_file_mut();
    write_header(
        file,
        WaveFormat {
            sample_rate: pcm.sample_rate,
            channels: pcm.channels,
            sample_width: 2,
        },
        pcm.samples.len() as u64 * 2,
    )?;
    for block in pcm.samples.chunks(BLOCK_FRAMES * usize::from(pcm.channels)) {
        let bytes: Vec<_> = block
            .iter()
            .flat_map(|sample| sample.to_le_bytes())
            .collect();
        file.write_all(&bytes)?;
    }
    file.flush()?;
    Ok(temporary)
}

pub fn recording_duration(path: &Path) -> io::Result<f64> {
    let wave = WaveFile::open(path)?;
    Ok(wave.total_frames as f64 / f64::from(wave.sample_rate()))
}

pub fn trim_recording(path: &Path, start: f64, end: f64) -> io::Result<NamedTempFile> {
    let mut wave = WaveFile::open(path)?;
    let rate = f64::from(wave.sample_rate());
    if !start.is_finite()
        || !end.is_finite()
        || start < 0.0
        || start >= end
        || end > wave.total_frames as f64 / rate
    {
        return Err(invalid_audio("请选择录音范围内的有效起止时间"));
    }
    let first = (start * rate).round_ties_even() as u64;
    let last = (end * rate).round_ties_even() as u64;
    if first >= last {
        return Err(invalid_audio("片段至少需要包含一帧音频"));
    }
    let align = wave.format.block_align() as u64;
    let length = (last - first) * align;
    let mut temporary = Builder::new()
        .prefix("wordy-trim-")
        .suffix(".wav")
        .tempfile()?;
    let file = temporary.as_file_mut();
    write_header(file, wave.format, length)?;
    wave.file
        .seek(SeekFrom::Start(wave.data_start + first * align))?;
    if io::copy(&mut wave.file.take(length), file)? != length {
        return Err(invalid_audio("录音文件不完整"));
    }
    if length % 2 == 1 {
        file.write_all(&[0])?;
    }
    file.flush()?;
    Ok(temporary)
}

pub fn waveform_peaks(path: &Path, cancel: &AtomicBool, bins: usize) -> io::Result<Vec<f32>> {
    if bins == 0 {
        return Err(invalid_audio("波形分箱数必须大于零"));
    }
    if cancel.load(Ordering::Acquire) {
        return Ok(Vec::new());
    }
    let mut wave = WaveFile::open(path)?;
    let frames = wave.total_frames;
    if frames == 0 {
        return Err(invalid_audio("音频没有可显示的采样"));
    }
    let count = (bins as u64).min(frames);
    let mut buffer = Vec::new();
    let mut peaks = Vec::with_capacity(count as usize);
    for index in 0..count {
        let mut remaining = (index + 1) * frames / count - index * frames / count;
        let mut peak = 0;
        while remaining > 0 {
            if cancel.load(Ordering::Acquire) {
                return Ok(Vec::new());
            }
            let requested = remaining.min(65_536);
            let samples = wave.read_pcm16(requested as usize, &mut buffer)?;
            // unsigned_abs preserves the full magnitude of -32768 and avoids channel cancellation.
            peak = peak.max(
                samples
                    .into_iter()
                    .map(i16::unsigned_abs)
                    .max()
                    .unwrap_or(0),
            );
            remaining -= requested;
        }
        peaks.push(f32::from(peak) / 32_768.0);
    }
    if cancel.load(Ordering::Acquire) {
        return Ok(Vec::new());
    }
    Ok(peaks)
}

pub enum DecodedClip<'a> {
    Original(&'a Path),
    Temporary(NamedTempFile),
}

impl AsRef<Path> for DecodedClip<'_> {
    fn as_ref(&self) -> &Path {
        match self {
            Self::Original(path) => path,
            Self::Temporary(file) => file.path(),
        }
    }
}

pub(super) fn clip_format(path: &Path) -> Option<&'static str> {
    match path.extension()?.to_str()?.to_ascii_lowercase().as_str() {
        "wav" => Some("wav"),
        "mp3" => Some("mp3"),
        "ogg" | "opus" => Some("ogg"),
        "flac" => Some("flac"),
        "m4a" => Some("mov"),
        "aac" => Some("aac"),
        "wma" => Some("asf"),
        "aiff" | "aif" => Some("aiff"),
        _ => None,
    }
}

pub fn decode_clip<'a>(path: &'a Path, cancel: &AtomicBool) -> io::Result<DecodedClip<'a>> {
    let executable = env::current_exe()?.with_file_name(if cfg!(windows) {
        "ffmpeg.exe"
    } else {
        "ffmpeg"
    });
    decode_clip_with(path, cancel, &executable)
}

fn decode_clip_with<'a>(
    path: &'a Path,
    cancel: &AtomicBool,
    executable: &Path,
) -> io::Result<DecodedClip<'a>> {
    check_cancelled(cancel)?;
    if !path.is_file() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "找不到音频文件"));
    }
    let format = clip_format(path).ok_or_else(|| invalid_audio("不支持此音频格式"))?;
    if format == "wav"
        && let Ok(wave) = WaveFile::open(path)
        && wave.format.sample_width == 2
        && wave.total_frames > 0
    {
        return Ok(DecodedClip::Original(path));
    }
    let output = Builder::new()
        .prefix("wordy-clip-")
        .suffix(".wav")
        .tempfile()?;
    let diagnostic = Builder::new().prefix("wordy-decode-").tempfile()?;
    // Force a known audio demuxer and local-only protocol for untrusted clip contents.
    let mut command = Command::new(executable);
    command
        .args([
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-xerror",
            "-y",
            "-protocol_whitelist",
            "file",
            "-f",
            format,
            "-i",
        ])
        .arg(path.canonicalize()?)
        .args([
            "-map",
            "0:a:0",
            "-map_metadata",
            "-1",
            "-vn",
            "-ac",
            "2",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            "-f",
            "wav",
        ])
        .arg(output.path())
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::from(diagnostic.as_file().try_clone()?));
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        use windows_sys::Win32::System::Threading::CREATE_NO_WINDOW;
        command.creation_flags(CREATE_NO_WINDOW);
    }
    let mut child = command.spawn().map_err(|error| {
        io::Error::new(
            error.kind(),
            format!("无法启动随应用分发的音频解码器：{error}"),
        )
    })?;
    let status = wait_for_decoder(&mut child, output.as_file(), cancel);
    if status.is_err() {
        let _ = child.kill();
        let _ = child.wait();
    }
    if !status?.success() {
        let mut detail = Vec::new();
        let mut log = diagnostic.reopen()?;
        log.seek(SeekFrom::Start(log.metadata()?.len().saturating_sub(4096)))?;
        log.take(4096).read_to_end(&mut detail)?;
        return Err(io::Error::other(format!(
            "音频解码失败：{}",
            String::from_utf8_lossy(&detail).trim()
        )));
    }
    let wave = WaveFile::open(output.path())?;
    if wave.total_frames == 0
        || wave.format.sample_width != 2
        || wave.format.sample_rate != 48_000
        || wave.format.channels != 2
    {
        return Err(invalid_audio("音频解码失败：文件没有有效的 PCM16 音频内容"));
    }
    Ok(DecodedClip::Temporary(output))
}

fn check_cancelled(cancel: &AtomicBool) -> io::Result<()> {
    if cancel.load(Ordering::Acquire) {
        return Err(io::Error::new(io::ErrorKind::Interrupted, "音频播放已取消"));
    }
    Ok(())
}

fn wait_for_decoder(
    child: &mut Child,
    output: &File,
    cancel: &AtomicBool,
) -> io::Result<ExitStatus> {
    let mut last_size = 0;
    let mut last_progress = Instant::now();
    loop {
        check_cancelled(cancel)?;
        if let Some(status) = child.try_wait()? {
            return Ok(status);
        }
        let size = output.metadata()?.len();
        if size != last_size {
            last_size = size;
            last_progress = Instant::now();
        } else if last_progress.elapsed() >= Duration::from_secs(30) {
            return Err(io::Error::new(io::ErrorKind::TimedOut, "音频解码超时"));
        }
        thread::sleep(Duration::from_millis(100));
    }
}
