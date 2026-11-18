use std::collections::VecDeque;
use std::io;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, mpsc};
use std::thread::{self, JoinHandle};
use std::time::Duration;

use crate::audio::{self, PlaybackRoute, RoutedStream};
use crate::config::{Settings, Voice};
use crate::hotkey::windows::PressedKey;
use crate::tts::{TtsAudio, TtsEngine};

pub struct EngineSettings {
    pub settings: Settings,
    pub api_key: Option<String>,
}

pub enum Event {
    Finished {
        id: u64,
        result: io::Result<()>,
    },
    Progress {
        id: u64,
        seconds: f64,
    },
    Voices {
        id: u64,
        result: io::Result<Vec<Voice>>,
    },
    Warning {
        target: &'static str,
        message: String,
    },
    Log {
        target: &'static str,
        level: &'static str,
        message: String,
    },
}

struct Speech {
    id: u64,
    text: String,
    engine: Arc<EngineSettings>,
    settings: Settings,
    cancel: Arc<AtomicBool>,
}

struct Clip {
    id: u64,
    path: PathBuf,
    gain: f64,
    preview: bool,
    settings: Settings,
    cancel: Arc<AtomicBool>,
}

enum Work {
    Speech(Speech),
    Clip(Clip),
    Prewarm(Arc<EngineSettings>),
    Voices(u64, Arc<EngineSettings>),
}

#[derive(Default)]
struct Queue {
    jobs: VecDeque<Work>,
    speech: Option<Arc<AtomicBool>>,
    pending_speech: Option<Speech>,
    clip: Option<(bool, Arc<AtomicBool>)>,
    shutdown: bool,
}

impl Queue {
    fn speak(&mut self, job: Speech) -> Option<Speech> {
        if self.speech.is_some() {
            self.pending_speech.replace(job)
        } else {
            self.speech = Some(job.cancel.clone());
            self.jobs.push_back(Work::Speech(job));
            None
        }
    }

    fn request_speech(&mut self, job: Speech) -> Option<Speech> {
        // A new submission must stop the current speech, not wait behind it.
        if let Some(cancel) = &self.speech {
            cancel.store(true, Ordering::Release);
        }
        self.speak(job)
    }

    fn stop_speech(&mut self) -> Option<u64> {
        if let Some(cancel) = &self.speech {
            cancel.store(true, Ordering::Release);
        }
        self.pending_speech.take().map(|job| job.id)
    }

    fn stop_clip(&mut self, keep_preview: bool) {
        if let Some((preview, cancel)) = &self.clip
            && !(*preview && keep_preview)
        {
            cancel.store(true, Ordering::Release);
        }
    }

    fn speech_done(&mut self) {
        self.speech = None;
        if let Some(next) = self.pending_speech.take() {
            self.speak(next);
        }
    }
}

pub struct Playback {
    shared: Arc<(Mutex<Queue>, Condvar)>,
    worker: Option<JoinHandle<()>>,
    sender: mpsc::Sender<Event>,
    pub events: mpsc::Receiver<Event>,
}

impl Playback {
    pub fn start() -> io::Result<Self> {
        let (sender, events) = mpsc::channel();
        let shared = Arc::new((Mutex::new(Queue::default()), Condvar::new()));
        let (worker_queue, worker_events) = (shared.clone(), sender.clone());
        let worker = thread::Builder::new()
            .name("WordyTTS".into())
            .spawn(move || run(&worker_queue, &worker_events))?;
        Ok(Self {
            shared,
            worker: Some(worker),
            sender,
            events,
        })
    }

    pub fn speak(&self, id: u64, text: String, engine: Arc<EngineSettings>, settings: &Settings) {
        if text.trim().is_empty() || !(settings.main_playback_enabled || settings.sidetone_enabled)
        {
            finished(&self.sender, id, Ok(()));
            return;
        }
        let job = Speech {
            id,
            text,
            engine,
            settings: settings.clone(),
            cancel: Arc::default(),
        };
        let mut queue = self.shared.0.lock().unwrap();
        queue.stop_clip(false);
        let replaced = queue.request_speech(job);
        self.shared.1.notify_one();
        drop(queue);
        if let Some(replaced) = replaced {
            finished(&self.sender, replaced.id, Ok(()));
        }
    }

    pub fn play_clip(&self, id: u64, path: PathBuf, gain: f64, preview: bool, settings: &Settings) {
        let mut queue = self.shared.0.lock().unwrap();
        queue.stop_clip(false);
        if !(preview || settings.main_playback_enabled || settings.sidetone_enabled) {
            finished(&self.sender, id, Ok(()));
            return;
        }
        let cancel = Arc::new(AtomicBool::new(false));
        queue.clip = Some((preview, cancel.clone()));
        queue.jobs.push_back(Work::Clip(Clip {
            id,
            path,
            gain,
            preview,
            settings: settings.clone(),
            cancel,
        }));
        self.shared.1.notify_one();
    }

    pub fn stop_clip(&self) {
        self.shared.0.lock().unwrap().stop_clip(false);
    }

    pub fn stop_speech(&self) {
        if let Some(id) = self.shared.0.lock().unwrap().stop_speech() {
            finished(&self.sender, id, Ok(()));
        }
    }

    pub fn stop_main(&self) {
        self.stop_speech();
        self.shared.0.lock().unwrap().stop_clip(true);
    }

    pub fn prewarm(&self, engine: Arc<EngineSettings>) {
        self.push(Work::Prewarm(engine));
    }

    pub fn fetch_voices(&self, id: u64, engine: Arc<EngineSettings>) {
        self.push(Work::Voices(id, engine));
    }

    fn push(&self, work: Work) {
        self.shared.0.lock().unwrap().jobs.push_back(work);
        self.shared.1.notify_one();
    }
}

impl Drop for Playback {
    fn drop(&mut self) {
        let mut queue = self.shared.0.lock().unwrap();
        queue.stop_speech();
        queue.stop_clip(false);
        queue.shutdown = true;
        drop(queue);
        self.shared.1.notify_one();
        if let Some(worker) = self.worker.take() {
            // Never let exit wait for the full HTTP timeout; 30s matches Python's janitor cap.
            let deadline = std::time::Instant::now() + Duration::from_secs(30);
            while !worker.is_finished() && std::time::Instant::now() < deadline {
                thread::sleep(Duration::from_millis(50));
            }
            if worker.is_finished() {
                let _ = worker.join();
            }
            // Otherwise detach: the worker still owns the socket and exits on its own.
        }
    }
}

fn finished(events: &mpsc::Sender<Event>, id: u64, result: io::Result<()>) {
    let _ = events.send(Event::Finished { id, result });
}

fn run(shared: &(Mutex<Queue>, Condvar), events: &mpsc::Sender<Event>) {
    let mut current: Option<(Arc<EngineSettings>, TtsEngine)> = None;
    loop {
        let queue = shared.0.lock().unwrap();
        let (mut queue, _) = shared
            .1
            .wait_timeout_while(queue, Duration::from_secs(1), |queue| {
                queue.jobs.is_empty() && !queue.shutdown
            })
            .unwrap();
        if queue.shutdown {
            break;
        }
        let work = queue.jobs.pop_front();
        drop(queue);
        match work {
            None => {
                if let Some((_, engine)) = &mut current {
                    engine.poll_keepalive();
                }
            }
            Some(Work::Speech(job)) => {
                let result = if job.cancel.load(Ordering::Acquire) {
                    Ok(())
                } else {
                    speak(engine_for(&mut current, &job.engine), &job, events)
                };
                let result = if job.cancel.load(Ordering::Acquire) {
                    Ok(())
                } else {
                    result
                };
                finished(events, job.id, result);
                shared.0.lock().unwrap().speech_done();
            }
            Some(Work::Clip(job)) => {
                let result = if job.cancel.load(Ordering::Acquire) {
                    Ok(())
                } else {
                    play_clip(&job, events)
                };
                let result = if job.cancel.load(Ordering::Acquire) {
                    Ok(())
                } else {
                    result
                };
                finished(events, job.id, result);
                let mut queue = shared.0.lock().unwrap();
                if queue
                    .clip
                    .as_ref()
                    .is_some_and(|(_, cancel)| Arc::ptr_eq(cancel, &job.cancel))
                {
                    queue.clip = None;
                }
            }
            Some(Work::Prewarm(spec)) => {
                if let Err(error) = engine_for(&mut current, &spec).connect() {
                    let _ = events.send(Event::Warning {
                        target: "wordy.tts",
                        message: format!("预热失败：{error}"),
                    });
                }
            }
            Some(Work::Voices(id, spec)) => {
                let result = TtsEngine::new(&spec.settings, spec.api_key.clone()).fetch_voices();
                let _ = events.send(Event::Voices { id, result });
            }
        }
    }
    if let Some((_, mut engine)) = current {
        engine.close();
    }
}

fn engine_for<'a>(
    current: &'a mut Option<(Arc<EngineSettings>, TtsEngine)>,
    spec: &Arc<EngineSettings>,
) -> &'a mut TtsEngine {
    if current
        .as_ref()
        .is_none_or(|(generation, _)| !Arc::ptr_eq(generation, spec))
    {
        if let Some((_, mut old)) = current.take() {
            old.close();
        }
        *current = Some((
            spec.clone(),
            TtsEngine::new(&spec.settings, spec.api_key.clone()),
        ));
    }
    &mut current.as_mut().expect("the active engine is installed").1
}

fn select_route<'a>(
    settings: &Settings,
    preview: bool,
    endpoints: &'a [audio::Endpoint],
) -> PlaybackRoute<'a> {
    let preferred = settings
        .audio_output_device
        .as_ref()
        .map(|id| id.name.as_str())
        .or(settings.audio_output_device_name.as_deref());
    audio::route(
        settings.main_playback_enabled,
        settings.sidetone_enabled,
        preview,
        preferred,
        endpoints,
        settings.virtual_output_device.as_deref(),
    )
}

fn hold_key(
    settings: &Settings,
    preview: bool,
    events: &mpsc::Sender<Event>,
) -> Option<PressedKey> {
    if preview || !settings.main_playback_enabled || !settings.hold_key_enabled {
        return None;
    }
    match PressedKey::press(&settings.hold_key) {
        Ok(handle) => Some(handle),
        Err(error) => {
            let _ = events.send(Event::Warning {
                target: "wordy.hotkey",
                message: format!("自动按住语音按键失败：{error}"),
            });
            None
        }
    }
}

fn speak(engine: &mut TtsEngine, job: &Speech, events: &mpsc::Sender<Event>) -> io::Result<()> {
    let _ = events.send(Event::Log {
        target: "wordy.tts",
        level: "INFO",
        message: format!("开始朗读 {} 字", job.text.chars().count()),
    });
    let output = audio::list_outputs()?;
    let route = select_route(&job.settings, false, &output);
    engine.set_volume(job.settings.volume);
    engine.set_voice(job.settings.active_voice_id().map(str::to_owned));
    engine.set_device_sample_rate(
        route
            .main
            .or(route.local)
            .and_then(|endpoint| audio::default_rate(endpoint).ok()),
    );
    let _pressed = hold_key(&job.settings, false, events);
    let mut stream = None;
    let played = engine.speak(&job.text, &job.cancel, |audio| match audio {
        TtsAudio::Wav(data) => {
            let pcm = audio::read_tts_wave(data)?;
            audio::play_routed_pcm(&route, &pcm, &job.cancel)
        }
        TtsAudio::PcmStart { sample_rate } => {
            stream = Some(RoutedStream::open(&route, sample_rate, 1)?);
            Ok(())
        }
        TtsAudio::Pcm(data) => {
            let (frames, trailing) = data.as_chunks::<2>();
            if !trailing.is_empty() {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    "TTS PCM 数据不完整",
                ));
            }
            let buffer: Vec<i16> = frames.iter().copied().map(i16::from_le_bytes).collect();
            stream
                .as_mut()
                .ok_or_else(|| io::Error::other("未初始化 TTS 音频流"))?
                .write(&buffer, &job.cancel)
        }
    })?;
    if let Some(stream) = &mut stream {
        stream.finish(&job.cancel)?;
    }
    if played || job.cancel.load(Ordering::Acquire) {
        Ok(())
    } else {
        Err(io::Error::other("未输出音频"))
    }
}

fn play_clip(job: &Clip, events: &mpsc::Sender<Event>) -> io::Result<()> {
    let _ = events.send(Event::Log {
        target: "wordy.audio",
        level: "INFO",
        message: if job.preview {
            "开始播放试听片段".into()
        } else {
            "开始播放音频片段".into()
        },
    });
    let endpoints = audio::list_outputs()?;
    let route = select_route(&job.settings, job.preview, &endpoints);
    let _pressed = hold_key(&job.settings, job.preview, events);
    let clip = audio::decode_clip(&job.path, &job.cancel)?;
    let mut progress = |seconds| {
        let _ = events.send(Event::Progress {
            id: job.id,
            seconds,
        });
    };
    audio::play_routed_wave(
        &route,
        clip.as_ref(),
        job.gain,
        job.cancel.clone(),
        Some(&mut progress),
    )
}

#[cfg(test)]
mod tests {
    use super::*;

    fn job(id: u64) -> Speech {
        Speech {
            id,
            text: id.to_string(),
            engine: Arc::new(EngineSettings {
                settings: Settings::default(),
                api_key: None,
            }),
            settings: Settings::default(),
            cancel: Arc::default(),
        }
    }

    #[test]
    fn keeps_only_latest_pending_speech_and_cancels_after_promotion() {
        let mut queue = Queue::default();
        assert!(queue.speak(job(1)).is_none());
        assert!(queue.speak(job(2)).is_none());
        assert_eq!(queue.speak(job(3)).unwrap().id, 2);
        let Some(Work::Speech(first)) = queue.jobs.pop_front() else {
            panic!("missing first job")
        };
        assert_eq!(first.id, 1);
        assert!(!first.cancel.load(Ordering::Acquire));
        queue.speech_done();
        let Some(Work::Speech(latest)) = queue.jobs.pop_front() else {
            panic!("missing latest job")
        };
        assert_eq!(latest.id, 3);
        assert!(queue.stop_speech().is_none());
        assert!(latest.cancel.load(Ordering::Acquire));
    }

    #[test]
    fn a_new_speech_cancels_the_current_one_and_keeps_the_latest_pending() {
        let mut queue = Queue::default();
        assert!(queue.request_speech(job(1)).is_none());
        let Some(Work::Speech(first)) = queue.jobs.pop_front() else {
            panic!("missing first job")
        };
        assert!(!first.cancel.load(Ordering::Acquire));

        assert!(queue.request_speech(job(2)).is_none());
        assert!(first.cancel.load(Ordering::Acquire));
        assert_eq!(queue.pending_speech.as_ref().unwrap().id, 2);
    }

    #[test]
    fn legacy_output_name_drives_the_local_route_without_an_identity() {
        let settings = Settings {
            audio_output_device_name: Some("Speakers".into()),
            sidetone_enabled: true,
            ..Settings::default()
        };
        let outputs = vec![
            audio::Endpoint {
                id: "system".into(),
                name: "System".into(),
                is_default: true,
            },
            audio::Endpoint {
                id: "speakers".into(),
                name: "Speakers".into(),
                is_default: false,
            },
        ];
        let route = select_route(&settings, false, &outputs);
        assert_eq!(
            route.local.map(|endpoint| endpoint.name.as_str()),
            Some("Speakers")
        );
    }

    #[test]
    fn stopping_main_leaves_local_preview_running() {
        let mut queue = Queue::default();
        let cancel = Arc::new(AtomicBool::new(false));
        queue.clip = Some((true, cancel.clone()));
        queue.stop_clip(true);
        assert!(!cancel.load(Ordering::Acquire));
        queue.stop_clip(false);
        assert!(cancel.load(Ordering::Acquire));
    }
}
