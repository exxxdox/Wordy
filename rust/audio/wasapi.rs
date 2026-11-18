use std::io;
use std::mem::ManuallyDrop;
use std::path::Path;
use std::ptr::{copy_nonoverlapping, null_mut};
use std::slice;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use windows::Win32::Foundation::{PROPERTYKEY, RPC_E_CHANGED_MODE};
use windows::Win32::Media::Audio::{
    AUDCLNT_BUFFERFLAGS_SILENT, AUDCLNT_SHAREMODE_SHARED, AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,
    AUDCLNT_STREAMFLAGS_LOOPBACK, AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY, DEVICE_STATE_ACTIVE,
    EDataFlow, IAudioCaptureClient, IAudioClient, IAudioRenderClient, IMMDevice,
    IMMDeviceEnumerator, MMDeviceEnumerator, WAVEFORMATEX, WAVEFORMATEXTENSIBLE, eCapture,
    eMultimedia, eRender,
};
use windows::Win32::System::Com::StructuredStorage::{
    PROPVARIANT, PROPVARIANT_0, PROPVARIANT_0_0, PROPVARIANT_0_0_0, PropVariantToStringAlloc,
};
use windows::Win32::System::Com::{
    CLSCTX_INPROC_SERVER, COINIT_APARTMENTTHREADED, CoCreateInstance, CoInitializeEx,
    CoTaskMemAlloc, CoTaskMemFree, CoUninitialize, STGM_READ, STGM_READWRITE,
};
use windows::Win32::System::Variant::{VT_BOOL, VT_LPWSTR};
use windows::core::{GUID, PCWSTR, PWSTR};

use super::wave::WaveFile;
use super::{CaptureBuffer, Endpoint, Pcm, apply_gain, invalid_audio};

const FRIENDLY_NAME: PROPERTYKEY = PROPERTYKEY {
    fmtid: GUID::from_u128(0xa45c254e_df1c_4efd_8020_67d146a850e0),
    pid: 14,
};
const LISTEN_GUID: GUID = GUID::from_u128(0x24dbb0fc_9311_4b3d_9cf0_18ff155639d4);
const LISTEN_TARGET: PROPERTYKEY = PROPERTYKEY {
    fmtid: LISTEN_GUID,
    pid: 0,
};
const LISTEN_ENABLED: PROPERTYKEY = PROPERTYKEY {
    fmtid: LISTEN_GUID,
    pid: 1,
};
const POLL: Duration = Duration::from_millis(10);
const JOIN_TIMEOUT: Duration = Duration::from_secs(2);
const WAVE_PCM: u16 = 1;
const WAVE_FLOAT: u16 = 3;
const WAVE_EXTENSIBLE: u16 = 0xfffe;

fn native<T>(result: windows::core::Result<T>) -> io::Result<T> {
    result.map_err(io::Error::other)
}

struct Apartment(bool);

impl Apartment {
    fn enter() -> io::Result<Self> {
        // The window thread or toolkit may already have initialized a different apartment.
        let result = unsafe { CoInitializeEx(None, COINIT_APARTMENTTHREADED) };
        if result == RPC_E_CHANGED_MODE {
            return Ok(Self(false));
        }
        native(result.ok())?;
        Ok(Self(true))
    }
}

impl Drop for Apartment {
    fn drop(&mut self) {
        if self.0 {
            unsafe { CoUninitialize() };
        }
    }
}

fn enumerator() -> io::Result<IMMDeviceEnumerator> {
    native(unsafe { CoCreateInstance(&MMDeviceEnumerator, None, CLSCTX_INPROC_SERVER) })
}

fn task_string(pointer: PWSTR) -> io::Result<String> {
    if pointer.0.is_null() {
        return Err(invalid_audio("Windows 未返回音频设备名称或 ID"));
    }
    let value = unsafe { pointer.to_string() }.map_err(io::Error::other);
    unsafe { CoTaskMemFree(Some(pointer.0.cast())) };
    value
}

fn device_id(device: &IMMDevice) -> io::Result<String> {
    task_string(native(unsafe { device.GetId() })?)
}

fn device_name(device: &IMMDevice) -> io::Result<String> {
    let properties = native(unsafe { device.OpenPropertyStore(STGM_READ) })?;
    let value = native(unsafe { properties.GetValue(&FRIENDLY_NAME) })?;
    task_string(native(unsafe { PropVariantToStringAlloc(&value) })?)
}

fn device_by_id(enumerator: &IMMDeviceEnumerator, id: &str) -> io::Result<IMMDevice> {
    let wide: Vec<_> = id.encode_utf16().chain([0]).collect();
    native(unsafe { enumerator.GetDevice(PCWSTR(wide.as_ptr())) })
}

fn list_endpoints(flow: EDataFlow) -> io::Result<Vec<Endpoint>> {
    let _apartment = Apartment::enter()?;
    let enumerator = enumerator()?;
    let default = unsafe { enumerator.GetDefaultAudioEndpoint(flow, eMultimedia) }
        .ok()
        .and_then(|device| device_id(&device).ok());
    let devices = native(unsafe { enumerator.EnumAudioEndpoints(flow, DEVICE_STATE_ACTIVE) })?;
    let mut result = Vec::new();
    for index in 0..native(unsafe { devices.GetCount() })? {
        let Ok(device) = (unsafe { devices.Item(index) }) else {
            continue;
        };
        let (Ok(id), Ok(name)) = (device_id(&device), device_name(&device)) else {
            continue;
        };
        if !name.is_empty() {
            result.push(Endpoint {
                is_default: default.as_ref() == Some(&id),
                id,
                name,
            });
        }
    }
    Ok(result)
}

pub fn list_outputs() -> io::Result<Vec<Endpoint>> {
    list_endpoints(eRender)
}

pub fn list_inputs() -> io::Result<Vec<Endpoint>> {
    let mut inputs = list_endpoints(eCapture)?;
    inputs.retain(|device| {
        !device.name.contains("CABLE Output") && !device.name.contains("CABLE Input")
    });
    Ok(inputs)
}

struct MixFormat(*mut WAVEFORMATEX);

impl MixFormat {
    fn get(client: &IAudioClient) -> io::Result<Self> {
        let pointer = native(unsafe { client.GetMixFormat() })?;
        if pointer.is_null() {
            return Err(invalid_audio("WASAPI 没有返回音频格式"));
        }
        Ok(Self(pointer))
    }

    fn base(&self) -> WAVEFORMATEX {
        // SAFETY: GetMixFormat returns a WAVEFORMATEX followed by any extension data.
        unsafe { self.0.read() }
    }
}

impl Drop for MixFormat {
    fn drop(&mut self) {
        unsafe { CoTaskMemFree(Some(self.0.cast())) };
    }
}

fn activate(device: &IMMDevice) -> io::Result<IAudioClient> {
    native(unsafe { device.Activate(CLSCTX_INPROC_SERVER, None) })
}

pub fn default_rate(endpoint: &Endpoint) -> io::Result<u32> {
    let _apartment = Apartment::enter()?;
    let client = activate(&device_by_id(&enumerator()?, &endpoint.id)?)?;
    Ok(MixFormat::get(&client)?.base().nSamplesPerSec)
}

pub(super) fn cancellation_point(cancel: &AtomicBool) -> io::Result<()> {
    if cancel.load(Ordering::Acquire) {
        return Err(io::Error::new(io::ErrorKind::Interrupted, "播放已取消"));
    }
    Ok(())
}

pub struct PcmStream {
    render: IAudioRenderClient,
    client: IAudioClient,
    _apartment: Apartment,
    input_channels: u16,
    device_channels: u16,
    capacity: u32,
}

impl PcmStream {
    pub fn open(endpoint: &Endpoint, sample_rate: u32, channels: u16) -> io::Result<Self> {
        if sample_rate == 0 || channels == 0 || channels > 32 {
            return Err(invalid_audio("无效的 PCM 输出格式"));
        }
        // VB-CABLE expects L/R frames. Preserve mono on ordinary devices.
        let device_channels = if channels == 1 && super::is_cable_device(&endpoint.name) {
            2
        } else {
            channels
        };
        let block_bytes = device_channels * 2;
        let wave = WAVEFORMATEX {
            wFormatTag: WAVE_PCM,
            nChannels: device_channels,
            nSamplesPerSec: sample_rate,
            nAvgBytesPerSec: sample_rate
                .checked_mul(u32::from(block_bytes))
                .ok_or_else(|| invalid_audio("无效的 PCM 采样率"))?,
            nBlockAlign: block_bytes,
            wBitsPerSample: 16,
            cbSize: 0,
        };
        let apartment = Apartment::enter()?;
        let client = activate(&device_by_id(&enumerator()?, &endpoint.id)?)?;
        // The engine converts the stream without changing the pitch when the endpoint rate differs.
        native(unsafe {
            client.Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM | AUDCLNT_STREAMFLAGS_SRC_DEFAULT_QUALITY,
                1_000_000,
                0,
                &wave,
                None,
            )
        })?;
        let render = native(unsafe { client.GetService() })?;
        let capacity = native(unsafe { client.GetBufferSize() })?;
        native(unsafe { client.Start() })?;
        Ok(Self {
            render,
            client,
            _apartment: apartment,
            input_channels: channels,
            device_channels,
            capacity,
        })
    }

    pub fn write(&mut self, samples: &[i16], cancel: &AtomicBool) -> io::Result<()> {
        let channels = usize::from(self.input_channels);
        if !samples.len().is_multiple_of(channels) {
            return Err(invalid_audio("PCM 数据未对齐到帧"));
        }
        let mut pending = samples;
        while !pending.is_empty() {
            cancellation_point(cancel)?;
            let available = self
                .capacity
                .saturating_sub(native(unsafe { self.client.GetCurrentPadding() })?);
            if available == 0 {
                thread::sleep(POLL);
                continue;
            }
            let frames = (pending.len() / channels).min(available as usize);
            let (part, remaining) = pending.split_at(frames * channels);
            let destination =
                native(unsafe { self.render.GetBuffer(frames as u32) })?.cast::<i16>();
            // SAFETY: GetBuffer supplies writable storage of `frames * device_channels` samples.
            unsafe {
                if channels == usize::from(self.device_channels) {
                    copy_nonoverlapping(part.as_ptr(), destination, part.len());
                } else {
                    for (index, &sample) in part.iter().enumerate() {
                        destination.add(index * 2).write_unaligned(sample);
                        destination.add(index * 2 + 1).write_unaligned(sample);
                    }
                }
                native(self.render.ReleaseBuffer(frames as u32, 0))?;
            }
            pending = remaining;
        }
        cancellation_point(cancel)
    }

    pub fn finish(&mut self, cancel: &AtomicBool) -> io::Result<()> {
        let started = Instant::now();
        loop {
            cancellation_point(cancel)?;
            if native(unsafe { self.client.GetCurrentPadding() })? == 0 {
                return Ok(());
            }
            if started.elapsed() >= JOIN_TIMEOUT {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "播放设备未在规定时间内收尾",
                ));
            }
            thread::sleep(POLL);
        }
    }
}

impl Drop for PcmStream {
    fn drop(&mut self) {
        let _ = unsafe { self.client.Stop() };
    }
}

pub fn play_wave(
    endpoint: &Endpoint,
    path: &Path,
    gain: f64,
    cancel: &AtomicBool,
    progress: Option<&mut dyn FnMut(f64)>,
) -> io::Result<()> {
    cancellation_point(cancel)?;
    play_open_wave(endpoint, WaveFile::open(path)?, gain, cancel, progress)
}

pub(super) fn play_open_wave(
    endpoint: &Endpoint,
    mut wave: WaveFile,
    gain: f64,
    cancel: &AtomicBool,
    progress: Option<&mut dyn FnMut(f64)>,
) -> io::Result<()> {
    let (rate, channels) = (wave.sample_rate(), wave.channels());
    let mut remaining = wave.total_frames;
    let mut buffer = Vec::new();
    let blocks = std::iter::from_fn(|| {
        if remaining == 0 {
            return None;
        }
        let frames = remaining.min(1024);
        remaining -= frames;
        Some(wave.read_pcm16(frames as usize, &mut buffer))
    });
    play_blocks(endpoint, rate, channels, blocks, gain, cancel, progress)
}

fn play_blocks<S: AsRef<[i16]>>(
    endpoint: &Endpoint,
    sample_rate: u32,
    channels: u16,
    blocks: impl IntoIterator<Item = io::Result<S>>,
    gain: f64,
    cancel: &AtomicBool,
    mut progress: Option<&mut dyn FnMut(f64)>,
) -> io::Result<()> {
    cancellation_point(cancel)?;
    let mut stream = PcmStream::open(endpoint, sample_rate, channels)?;
    let channels = usize::from(channels);
    let mut submitted = 0;
    if let Some(callback) = progress.as_mut() {
        callback(0.0);
    }
    for block in blocks {
        let block = block?;
        let block = block.as_ref();
        let adjusted;
        let block = if gain == 1.0 {
            block
        } else {
            adjusted = apply_gain(block, gain)?;
            &adjusted
        };
        stream.write(block, cancel)?;
        submitted += block.len() / channels;
        if let Some(callback) = progress.as_mut() {
            cancellation_point(cancel)?;
            callback(submitted as f64 / f64::from(sample_rate));
        }
    }
    stream.finish(cancel)
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ListenState {
    pub enabled: bool,
    pub output_id: Option<String>,
    pub output_name: Option<String>,
}

fn read_endpoint_listen(
    enumerator: &IMMDeviceEnumerator,
    device: &IMMDevice,
) -> io::Result<ListenState> {
    let store = native(unsafe { device.OpenPropertyStore(STGM_READ) })?;
    let enabled = native(unsafe { store.GetValue(&LISTEN_ENABLED) })?;
    if enabled.vt() != VT_BOOL {
        return Err(invalid_audio("输入设备不支持 Windows 侦听启用属性"));
    }
    let enabled = native(bool::try_from(&enabled))?;
    let output_id = unsafe { store.GetValue(&LISTEN_TARGET) }
        .ok()
        .filter(|value| value.vt() == VT_LPWSTR)
        .and_then(|value| unsafe { PropVariantToStringAlloc(&value) }.ok())
        .and_then(|pointer| task_string(pointer).ok())
        .filter(|value| !value.is_empty());
    let output_name = output_id
        .as_deref()
        .and_then(|id| device_by_id(enumerator, id).ok())
        .and_then(|device| device_name(&device).ok());
    Ok(ListenState {
        enabled,
        output_id,
        output_name,
    })
}

pub fn read_listen(input: &Endpoint) -> io::Result<ListenState> {
    let _apartment = Apartment::enter()?;
    let enumerator = enumerator()?;
    let device = device_by_id(&enumerator, &input.id)?;
    read_endpoint_listen(&enumerator, &device)
}

fn string_property(value: &str) -> io::Result<PROPVARIANT> {
    let wide: Vec<_> = value.encode_utf16().chain([0]).collect();
    // PROPVARIANT owns the resulting UTF-16 buffer and frees it using CoTaskMemFree.
    let pointer = unsafe { CoTaskMemAlloc(wide.len() * size_of::<u16>()) }.cast::<u16>();
    if pointer.is_null() {
        return Err(io::ErrorKind::OutOfMemory.into());
    }
    unsafe { copy_nonoverlapping(wide.as_ptr(), pointer, wide.len()) };
    Ok(PROPVARIANT {
        Anonymous: PROPVARIANT_0 {
            Anonymous: ManuallyDrop::new(PROPVARIANT_0_0 {
                vt: VT_LPWSTR,
                Anonymous: PROPVARIANT_0_0_0 {
                    pwszVal: PWSTR(pointer),
                },
                ..Default::default()
            }),
        },
    })
}

fn write_listen(input: &Endpoint, output_id: Option<&str>, enabled: bool) -> io::Result<()> {
    if enabled && output_id.is_none() {
        return Err(invalid_audio("启用 Windows 侦听需要 CABLE Input 输出设备"));
    }
    let _apartment = Apartment::enter()?;
    let enumerator = enumerator()?;
    let device = device_by_id(&enumerator, &input.id)?;
    let store = native(unsafe { device.OpenPropertyStore(STGM_READWRITE) })?;
    if let Some(id) = output_id {
        let target = string_property(id)?;
        native(unsafe { store.SetValue(&LISTEN_TARGET, &target) })?;
    }
    native(unsafe { store.SetValue(&LISTEN_ENABLED, &PROPVARIANT::from(enabled)) })?;
    native(unsafe { store.Commit() })?;
    drop(store);
    let actual = read_endpoint_listen(&enumerator, &device)?;
    if actual.enabled != enabled
        || output_id.is_some_and(|id| actual.output_id.as_deref() != Some(id))
    {
        return Err(io::Error::other("写入后未能验证 Windows 侦听状态"));
    }
    Ok(())
}

pub fn set_listen(input: &Endpoint, output: Option<&Endpoint>, enabled: bool) -> io::Result<()> {
    // An explicit disable preserves the previous output target in Windows.
    write_listen(
        input,
        output.filter(|_| enabled).map(|device| device.id.as_str()),
        enabled,
    )
}

pub fn restore_listen(input: &Endpoint, state: &ListenState) -> io::Result<()> {
    write_listen(input, state.output_id.as_deref(), state.enabled)
}

#[derive(Clone, Copy)]
enum CaptureEncoding {
    Pcm16,
    Pcm24,
    Pcm32,
    Float32,
}

impl CaptureEncoding {
    fn from_format(format: &MixFormat) -> io::Result<Self> {
        let wave = format.base();
        let mut tag = wave.wFormatTag;
        if tag == WAVE_EXTENSIBLE && wave.cbSize >= 22 {
            // Standard PCM/float format GUIDs embed the corresponding WAVE_FORMAT tag.
            let extended = unsafe { format.0.cast::<WAVEFORMATEXTENSIBLE>().read() };
            let subtype = extended.SubFormat;
            tag = match subtype {
                PCM_SUBTYPE => WAVE_PCM,
                FLOAT_SUBTYPE => WAVE_FLOAT,
                _ => return Err(invalid_audio("不支持的系统声音格式")),
            };
        }
        match (tag, wave.wBitsPerSample) {
            (WAVE_PCM, 16) => Ok(Self::Pcm16),
            (WAVE_PCM, 24) => Ok(Self::Pcm24),
            (WAVE_PCM, 32) => Ok(Self::Pcm32),
            (WAVE_FLOAT, 32) => Ok(Self::Float32),
            _ => Err(invalid_audio("不支持的系统声音格式")),
        }
    }

    fn bytes_per_sample(self) -> usize {
        match self {
            Self::Pcm16 => 2,
            Self::Pcm24 => 3,
            Self::Pcm32 | Self::Float32 => 4,
        }
    }

    fn samples(self, bytes: &[u8]) -> Vec<i16> {
        bytes
            .chunks_exact(self.bytes_per_sample())
            .map(|sample| match self {
                Self::Pcm16 => i16::from_le_bytes([sample[0], sample[1]]),
                // Integer formats store the most significant 16 bits last in little-endian order.
                Self::Pcm24 => i16::from_le_bytes([sample[1], sample[2]]),
                Self::Pcm32 => i16::from_le_bytes([sample[2], sample[3]]),
                Self::Float32 => {
                    (f32::from_le_bytes([sample[0], sample[1], sample[2], sample[3]]) * 32768.0)
                        as i16
                }
            })
            .collect()
    }
}

const PCM_SUBTYPE: GUID = GUID::from_u128(0x00000001_0000_0010_8000_00aa00389b71);
const FLOAT_SUBTYPE: GUID = GUID::from_u128(0x00000003_0000_0010_8000_00aa00389b71);

struct Loopback {
    capture: IAudioCaptureClient,
    client: IAudioClient,
    _apartment: Apartment,
    sample_rate: u32,
    channels: u16,
    encoding: CaptureEncoding,
}

impl Loopback {
    fn open() -> io::Result<Self> {
        let apartment = Apartment::enter()?;
        let enumerator = enumerator()?;
        let device = native(unsafe { enumerator.GetDefaultAudioEndpoint(eRender, eMultimedia) })
            .map_err(|error| {
                io::Error::new(
                    error.kind(),
                    format!("找不到可录制的系统声音输出设备：{error}"),
                )
            })?;
        let client = activate(&device)?;
        let format = MixFormat::get(&client)?;
        let wave = format.base();
        let encoding = CaptureEncoding::from_format(&format)?;
        if wave.nSamplesPerSec == 0
            || wave.nChannels == 0
            || usize::from(wave.nBlockAlign)
                != usize::from(wave.nChannels) * encoding.bytes_per_sample()
        {
            return Err(invalid_audio("无效的系统声音混音格式"));
        }
        // Shared loopback must be initialized on the rendering endpoint in its own mix format.
        native(unsafe {
            client.Initialize(
                AUDCLNT_SHAREMODE_SHARED,
                AUDCLNT_STREAMFLAGS_LOOPBACK,
                1_000_000,
                0,
                format.0,
                None,
            )
        })?;
        let capture = native(unsafe { client.GetService() })?;
        native(unsafe { client.Start() })?;
        Ok(Self {
            capture,
            client,
            _apartment: apartment,
            sample_rate: wave.nSamplesPerSec,
            channels: wave.nChannels,
            encoding,
        })
    }

    fn read_into(&self, buffer: &mut CaptureBuffer) -> io::Result<bool> {
        // An inactive endpoint can go indefinitely without packets. Poll without blocking.
        if native(unsafe { self.capture.GetNextPacketSize() })? == 0 {
            return Ok(false);
        }
        let mut bytes = null_mut();
        let mut frames = 0;
        let mut flags = 0;
        native(unsafe {
            self.capture
                .GetBuffer(&mut bytes, &mut frames, &mut flags, None, None)
        })?;
        let count = frames as usize * usize::from(self.channels);
        let samples = if flags & AUDCLNT_BUFFERFLAGS_SILENT.0 as u32 != 0 {
            Ok(vec![0; count])
        } else if count == 0 {
            Ok(Vec::new())
        } else if bytes.is_null() {
            Err(invalid_audio("无效的系统声音采集缓冲区"))
        } else {
            // SAFETY: the packet provides one native mix-format block per frame until release.
            let bytes =
                unsafe { slice::from_raw_parts(bytes, count * self.encoding.bytes_per_sample()) };
            Ok(self.encoding.samples(bytes))
        };
        native(unsafe { self.capture.ReleaseBuffer(frames) })?;
        buffer.push(&samples?);
        Ok(true)
    }
}

impl Drop for Loopback {
    fn drop(&mut self) {
        let _ = unsafe { self.client.Stop() };
    }
}

static ROLLING_CAPTURE_ACTIVE: AtomicBool = AtomicBool::new(false);

struct CaptureReservation(&'static AtomicBool);

impl CaptureReservation {
    fn take() -> io::Result<Self> {
        ROLLING_CAPTURE_ACTIVE
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .map_err(|_| io::Error::other("上一次录音设备尚未停止，请稍后重试"))?;
        Ok(Self(&ROLLING_CAPTURE_ACTIVE))
    }
}

impl Drop for CaptureReservation {
    fn drop(&mut self) {
        self.0.store(false, Ordering::Release);
    }
}

pub struct Recording {
    stop: Arc<AtomicBool>,
    worker: thread::JoinHandle<()>,
    finished: mpsc::Receiver<(Pcm, Duration)>,
}

impl Recording {
    pub fn start(duration: Duration) -> io::Result<Self> {
        if duration.is_zero() || duration > Duration::from_secs(300) {
            return Err(invalid_audio("无效的后台录制时长"));
        }
        let reservation = CaptureReservation::take()?;
        let stop = Arc::new(AtomicBool::new(false));
        let stop_worker = Arc::clone(&stop);
        let (ready_tx, ready_rx) = mpsc::sync_channel(1);
        let (finished_tx, finished) = mpsc::sync_channel(1);
        let worker = thread::Builder::new()
            .name("Wordy audio capture".into())
            .spawn(move || {
                let _reservation = reservation;
                let stream = match Loopback::open() {
                    Ok(stream) => stream,
                    Err(error) => {
                        let _ = ready_tx.send(Err(error));
                        return;
                    }
                };
                let started = Instant::now();
                if ready_tx.send(Ok(started)).is_err() {
                    return;
                }
                let limit = (duration.as_secs_f64() * f64::from(stream.sample_rate)) as usize;
                let mut buffer = CaptureBuffer::new(stream.channels, Some(limit));
                while !stop_worker.load(Ordering::Acquire) {
                    match stream.read_into(&mut buffer) {
                        Ok(true) => {}
                        Ok(false) => thread::park_timeout(POLL),
                        Err(error) => {
                            eprintln!("系统声音采集中断：{error}");
                            break;
                        }
                    }
                }
                let elapsed = started.elapsed();
                let pcm = Pcm {
                    sample_rate: stream.sample_rate,
                    channels: stream.channels,
                    samples: buffer.into_samples(),
                };
                // Only this thread owns COM stream interfaces, including on stop timeout.
                drop(stream);
                drop(_reservation);
                let _ = finished_tx.send((pcm, elapsed));
            })?;
        match ready_rx.recv_timeout(JOIN_TIMEOUT) {
            Ok(Ok(_)) => Ok(Self {
                stop,
                worker,
                finished,
            }),
            Ok(Err(error)) => Err(error),
            Err(error) => {
                stop.store(true, Ordering::Release);
                worker.thread().unpark();
                Err(io::Error::other(format!("无法启动系统声音录制：{error}")))
            }
        }
    }

    pub fn stop(self) -> io::Result<Pcm> {
        let (pcm, elapsed) = self.collect()?;
        finish_recording(pcm, elapsed)
    }

    pub fn cancel(self) -> io::Result<()> {
        self.collect().map(drop)
    }

    fn collect(&self) -> io::Result<(Pcm, Duration)> {
        self.stop.store(true, Ordering::Release);
        self.worker.thread().unpark();
        self.finished
            .recv_timeout(JOIN_TIMEOUT)
            .map_err(|error| match error {
                mpsc::RecvTimeoutError::Timeout => {
                    io::Error::new(io::ErrorKind::TimedOut, "录制设备尚未停止，请稍后重试")
                }
                mpsc::RecvTimeoutError::Disconnected => io::Error::other("录制线程意外退出"),
            })
    }
}

impl Drop for Recording {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.worker.thread().unpark();
    }
}

fn finish_recording(pcm: Pcm, elapsed: Duration) -> io::Result<Pcm> {
    if pcm.samples.is_empty() && elapsed >= Duration::from_millis(500) {
        return Err(io::Error::other("未录到系统声音，请播放声音后重试"));
    }
    if pcm.samples.len() / usize::from(pcm.channels) * 2 < pcm.sample_rate as usize {
        return Err(io::Error::other("录音太短，请至少录制 0.5 秒"));
    }
    pcm.into_recording_format()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn loopback_float_samples_convert_to_saturated_pcm() {
        let bytes: Vec<_> = [-1.0_f32, 0.5, 2.0, f32::NAN]
            .into_iter()
            .flat_map(f32::to_le_bytes)
            .collect();
        assert_eq!(
            CaptureEncoding::Float32.samples(&bytes),
            [-32_768, 16_384, 32_767, 0]
        );
    }

    #[test]
    fn capture_distinguishes_silent_endpoint_from_short_clip() {
        let silent = Pcm {
            sample_rate: 48_000,
            channels: 2,
            samples: Vec::new(),
        };
        let mut clip = silent.clone();
        clip.samples = vec![0; 1024];
        let elapsed = Duration::from_secs(1);
        assert!(
            finish_recording(silent, elapsed)
                .unwrap_err()
                .to_string()
                .contains("未录到系统声音")
        );
        assert!(
            finish_recording(clip, elapsed)
                .unwrap_err()
                .to_string()
                .contains("录音太短")
        );
    }
}
