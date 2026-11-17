use std::io;
use std::path::Path;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc};
use std::thread;
use std::time::Duration;

use super::wasapi::{cancellation_point, play_open_wave};
use super::wave::WaveFile;
use super::{Endpoint, Pcm, PcmStream, PlaybackRoute, invalid_audio, play_wave};

static SIDETONE_ACTIVE: AtomicBool = AtomicBool::new(false);

struct SidetoneLease;

impl SidetoneLease {
    fn take() -> Option<Self> {
        SIDETONE_ACTIVE
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .ok()
            .map(|_| Self)
    }
}

impl Drop for SidetoneLease {
    fn drop(&mut self) {
        SIDETONE_ACTIVE.store(false, Ordering::Release);
    }
}

fn start_sidetone(
    work: impl FnOnce() -> io::Result<()> + Send + 'static,
) -> Option<mpsc::Receiver<io::Result<()>>> {
    let lease = SidetoneLease::take()?;
    let (sender, result) = mpsc::sync_channel(1);
    thread::Builder::new()
        .name("Wordy sidetone".into())
        .spawn(move || {
            let result = work();
            // Signal completion only after the device is released and the next worker can start.
            drop(lease);
            let _ = sender.send(result);
        })
        .ok()?;
    Some(result)
}

pub fn play_routed_wave(
    route: &PlaybackRoute<'_>,
    path: &Path,
    gain: f64,
    cancel: Arc<AtomicBool>,
    mut progress: Option<&mut dyn FnMut(f64)>,
) -> io::Result<()> {
    cancellation_point(&cancel)?;
    let mut local = route.local;
    let mut monitor = None;
    let main = route.main.map(|endpoint| {
        let mut progress_callback = |elapsed| {
            if let Some(local) = local.take().filter(|local| local.id != endpoint.id)
                && let Ok(wave) = WaveFile::open(path)
            {
                // Give the worker its own existing handle before the caller can drop the temporary path.
                let local = local.clone();
                let cancel = Arc::clone(&cancel);
                monitor = start_sidetone(move || play_open_wave(&local, wave, gain, &cancel, None));
            }
            if let Some(callback) = progress.as_mut() {
                callback(elapsed);
            }
        };
        play_wave(endpoint, path, gain, &cancel, Some(&mut progress_callback))
    });
    if let Some(monitor) = monitor {
        let result = monitor.recv_timeout(Duration::from_secs(2)).ok();
        cancellation_point(&cancel)?;
        if result.is_some_and(|result| result.is_ok()) {
            return Ok(());
        }
    }
    if !main.as_ref().is_some_and(|result| result.is_ok())
        && let Some(local) = local
    {
        // A failed or disabled main sink does not suppress independently enabled local playback.
        let _lease = if route.preview_local {
            None
        } else {
            Some(SidetoneLease::take().ok_or_else(|| io::Error::other("上一段返听尚未收尾"))?)
        };
        return play_wave(local, path, gain, &cancel, progress);
    }
    main.unwrap_or_else(|| Err(io::Error::other("没有可用的播放设备")))
}

pub fn play_routed_pcm(
    route: &PlaybackRoute<'_>,
    pcm: &Pcm,
    cancel: &AtomicBool,
) -> io::Result<()> {
    cancellation_point(cancel)?;
    if pcm.channels == 0 || !pcm.samples.len().is_multiple_of(usize::from(pcm.channels)) {
        return Err(invalid_audio("PCM 数据未对齐到帧"));
    }
    let mut stream = RoutedStream::open(route, pcm.sample_rate, pcm.channels)?;
    for block in pcm.samples.chunks(1024 * usize::from(pcm.channels)) {
        stream.write(block, cancel)?;
    }
    stream.finish(cancel)
}

struct StreamSidetone {
    queue: Option<mpsc::SyncSender<Vec<i16>>>,
    cancel: Arc<AtomicBool>,
    result: mpsc::Receiver<io::Result<()>>,
}

impl StreamSidetone {
    fn start(endpoint: &Endpoint, rate: u32, channels: u16) -> Option<Self> {
        let endpoint = endpoint.clone();
        let (queue, blocks) = mpsc::sync_channel::<Vec<i16>>(8);
        let cancel = Arc::new(AtomicBool::new(false));
        let cancelled = Arc::clone(&cancel);
        let result = start_sidetone(move || {
            let mut stream = PcmStream::open(&endpoint, rate, channels)?;
            while let Ok(block) = blocks.recv() {
                stream.write(&block, &cancelled)?;
            }
            stream.finish(&cancelled)
        })?;
        Some(Self {
            queue: Some(queue),
            cancel,
            result,
        })
    }

    fn write(&mut self, samples: &[i16]) {
        // After sustained lag, finish the already queued blocks and drop sidetone for this stream.
        if self
            .queue
            .as_ref()
            .is_some_and(|queue| queue.try_send(samples.to_vec()).is_err())
        {
            self.queue = None;
        }
    }

    fn stop(mut self, cancel: bool) {
        if cancel {
            self.cancel.store(true, Ordering::Release);
        }
        self.queue = None;
        let _ = self.result.recv_timeout(Duration::from_millis(100));
    }
}

/// Keep the primary stream on its caller's worker and isolate an optional slow monitor.
pub struct RoutedStream {
    primary: PcmStream,
    sidetone: Option<StreamSidetone>,
    _local_lease: Option<SidetoneLease>,
}

impl RoutedStream {
    pub fn open(route: &PlaybackRoute<'_>, rate: u32, channels: u16) -> io::Result<Self> {
        let main = route
            .main
            .map(|endpoint| PcmStream::open(endpoint, rate, channels));
        let failure = match main {
            Some(Ok(primary)) => {
                let sidetone = route
                    .local
                    .filter(|local| {
                        Some(local.id.as_str()) != route.main.map(|main| main.id.as_str())
                    })
                    .and_then(|local| StreamSidetone::start(local, rate, channels));
                return Ok(Self {
                    primary,
                    sidetone,
                    _local_lease: None,
                });
            }
            Some(Err(error)) => error,
            None => io::Error::other("没有可用的播放设备"),
        };
        let local = route.local.ok_or(failure)?;
        let lease = SidetoneLease::take().ok_or_else(|| io::Error::other("上一段返听尚未收尾"))?;
        let primary = PcmStream::open(local, rate, channels)?;
        Ok(Self {
            primary,
            sidetone: None,
            _local_lease: Some(lease),
        })
    }

    pub fn write(&mut self, samples: &[i16], cancel: &AtomicBool) -> io::Result<()> {
        cancellation_point(cancel)?;
        if let Some(sidetone) = self.sidetone.as_mut() {
            sidetone.write(samples);
        }
        self.primary.write(samples, cancel)
    }

    pub fn finish(&mut self, cancel: &AtomicBool) -> io::Result<()> {
        let result = self.primary.finish(cancel);
        if let Some(sidetone) = self.sidetone.take() {
            sidetone.stop(result.is_err());
        }
        result
    }
}

impl Drop for RoutedStream {
    fn drop(&mut self) {
        if let Some(sidetone) = self.sidetone.take() {
            sidetone.stop(true);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn an_unfinished_sidetone_skips_a_second_worker_without_waiting() {
        let (release, hold) = mpsc::sync_channel(0);
        let finished = start_sidetone(move || {
            hold.recv().unwrap();
            Ok(())
        })
        .unwrap();
        assert!(start_sidetone(|| panic!("another worker still owns sidetone")).is_none());
        release.send(()).unwrap();
        finished
            .recv_timeout(Duration::from_secs(1))
            .unwrap()
            .unwrap();
        assert!(SidetoneLease::take().is_some());
    }
}
