use std::{
    io::{self, BufRead, BufReader, Read},
    sync::atomic::{AtomicBool, Ordering},
    time::Duration,
};

use base64::{
    Engine,
    alphabet::STANDARD,
    engine::{DecodePaddingMode, GeneralPurpose, GeneralPurposeConfig},
};
use reqwest::{
    blocking::{Client, Response},
    redirect::Policy,
};
use serde_json::{Value, json};

use crate::{
    config::{CARTESIA_REALTIME, Settings, Voice},
    credentials::Provider,
};

mod realtime;
use realtime::{RealtimeConnection, StreamError};

const CARTESIA_BYTES_URL: &str = "https://api.cartesia.ai/tts/bytes";
const CARTESIA_VOICES_URL: &str = "https://api.cartesia.ai/voices?is_owner=true";
const CARTESIA_HTTP_VERSION: &str = "2026-03-01";
const VOLCENGINE_SSE_URL: &str = "https://openspeech.bytedance.com/api/v3/tts/unidirectional/sse";
const VOLCENGINE_RESOURCE_ID: &str = "seed-icl-2.0";
const DEFAULT_SAMPLE_RATE: u32 = 48_000;
const SUPPORTED_SAMPLE_RATES: [u32; 7] = [8000, 16_000, 22_050, 24_000, 32_000, 44_100, 48_000];
const PCM_CHUNK_BYTES: usize = 1024 * 2;
const ENCODING: GeneralPurpose = GeneralPurpose::new(
    &STANDARD,
    GeneralPurposeConfig::new()
        .with_decode_padding_mode(DecodePaddingMode::Indifferent)
        .with_decode_allow_trailing_bits(true),
);

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum Backend {
    CartesiaBytes,
    CartesiaRealtime,
    VolcengineStreaming,
}

impl Backend {
    fn from_settings(settings: &Settings) -> Self {
        match settings.active_tts_provider {
            Provider::Volcengine => Self::VolcengineStreaming,
            Provider::Cartesia if settings.active_backend() == CARTESIA_REALTIME => {
                Self::CartesiaRealtime
            }
            Provider::Cartesia => Self::CartesiaBytes,
        }
    }
}

pub enum TtsAudio<'a> {
    Wav(&'a [u8]),
    PcmStart { sample_rate: u32 },
    Pcm(&'a [u8]),
}

pub struct TtsEngine {
    backend: Backend,
    api_key: Option<String>,
    voice_id: Option<String>,
    volume: f64,
    sample_rate: u32,
    http: Option<Client>,
    socket: Option<RealtimeConnection>,
}

impl TtsEngine {
    pub fn new(settings: &Settings, api_key: Option<String>) -> Self {
        Self {
            backend: Backend::from_settings(settings),
            api_key,
            voice_id: settings.active_voice_id().map(str::to_owned),
            volume: settings.volume,
            sample_rate: DEFAULT_SAMPLE_RATE,
            http: None,
            socket: None,
        }
    }

    pub fn set_voice(&mut self, voice_id: Option<String>) {
        self.voice_id = voice_id;
    }

    pub fn set_volume(&mut self, volume: f64) {
        self.volume = volume;
    }

    pub fn set_device_sample_rate(&mut self, device_rate: Option<u32>) {
        if self.backend != Backend::CartesiaBytes
            && let Some(rate) = device_rate.filter(|rate| SUPPORTED_SAMPLE_RATES.contains(rate))
        {
            self.sample_rate = rate;
        }
    }

    pub fn connect(&mut self) -> io::Result<()> {
        if self.backend != Backend::CartesiaRealtime {
            return Ok(());
        }
        // The background keepalive may have observed a server close while a
        // local clip was playing; replace the dead socket on the next speech.
        if self
            .socket
            .as_ref()
            .is_some_and(RealtimeConnection::is_dead)
        {
            self.close();
        }
        if self.socket.is_none() {
            let key = self.require_key()?;
            self.require_voice()?;
            self.socket = Some(RealtimeConnection::connect(key)?);
        }
        Ok(())
    }

    pub fn close(&mut self) {
        if let Some(mut connection) = self.socket.take() {
            connection.close();
        }
    }

    pub fn poll_keepalive(&mut self) {
        let failed = self.socket.as_ref().is_some_and(|connection| {
            connection.is_dead() || connection.lock().poll_keepalive().is_err()
        });
        if failed {
            self.close();
        }
    }

    pub fn fetch_voices(&mut self) -> io::Result<Vec<Voice>> {
        // Volcengine exposes single-voice lookup only; its list is edited locally.
        if self.backend == Backend::VolcengineStreaming {
            return Ok(Vec::new());
        }
        let client = self.http()?;
        let key = self.require_key()?;
        let response = client
            .get(CARTESIA_VOICES_URL)
            .header("Cartesia-Version", CARTESIA_HTTP_VERSION)
            .bearer_auth(key)
            .timeout(Duration::from_secs(20))
            .send()
            .map_err(|error| transport_error("获取音色列表失败", error))?;
        let data = check_status(response, "获取音色列表失败", key)?
            .json()
            .map_err(|_| invalid("获取音色列表失败：Cartesia 返回的不是有效 JSON"))?;
        cartesia_voices(data)
    }

    pub fn speak(
        &mut self,
        text: &str,
        cancel: &AtomicBool,
        mut emit: impl FnMut(TtsAudio<'_>) -> io::Result<()>,
    ) -> io::Result<bool> {
        if cancelled(cancel) {
            return Ok(false);
        }
        let result = match self.backend {
            Backend::CartesiaBytes => self.speak_bytes(text, cancel, &mut emit),
            Backend::CartesiaRealtime => self.speak_realtime(text, cancel, &mut emit),
            Backend::VolcengineStreaming => self.speak_sse(text, cancel, &mut emit),
        };
        if result
            .as_ref()
            .is_err_and(|error| error.kind() == io::ErrorKind::Interrupted)
            && cancelled(cancel)
        {
            return Ok(false);
        }
        result
    }

    fn speak_bytes(
        &mut self,
        text: &str,
        cancel: &AtomicBool,
        emit: &mut impl FnMut(TtsAudio<'_>) -> io::Result<()>,
    ) -> io::Result<bool> {
        let client = self.http()?;
        let key = self.require_key()?;
        let payload = cartesia_request(
            text,
            self.require_voice()?,
            self.volume,
            DEFAULT_SAMPLE_RATE,
            None,
        );
        let response = client
            .post(CARTESIA_BYTES_URL)
            .header("Cartesia-Version", CARTESIA_HTTP_VERSION)
            .bearer_auth(key)
            .json(&payload)
            .send()
            .map_err(|error| transport_error("Cartesia bytes TTS 失败", error))?;
        let wav = check_status(response, "Cartesia bytes TTS 失败", key)?
            .bytes()
            .map_err(|error| transport_error("读取 Cartesia 音频失败", error))?;
        // The HTTP request produces a complete WAV before playback can begin.
        if cancelled(cancel) {
            return Ok(false);
        }
        emit(TtsAudio::Wav(&wav))?;
        Ok(!cancelled(cancel))
    }

    fn speak_sse(
        &mut self,
        text: &str,
        cancel: &AtomicBool,
        emit: &mut impl FnMut(TtsAudio<'_>) -> io::Result<()>,
    ) -> io::Result<bool> {
        let client = self.http()?;
        let key = self.require_key()?;
        let payload =
            volcengine_request(text, self.require_voice()?, self.volume, self.sample_rate);
        emit(TtsAudio::PcmStart {
            sample_rate: self.sample_rate,
        })?;
        if cancelled(cancel) {
            return Ok(false);
        }
        let response = client
            .post(VOLCENGINE_SSE_URL)
            .header("X-Api-Key", key)
            .header("X-Api-Resource-Id", VOLCENGINE_RESOURCE_ID)
            .header("Accept", "text/event-stream")
            .json(&payload)
            .send()
            .map_err(|error| transport_error("火山引擎流式 TTS 失败", error))?;
        let response = check_status(response, "火山引擎流式 TTS 失败", key)?;
        play_sse(BufReader::new(response), key, cancel, emit)
    }

    fn speak_realtime(
        &mut self,
        text: &str,
        cancel: &AtomicBool,
        emit: &mut impl FnMut(TtsAudio<'_>) -> io::Result<()>,
    ) -> io::Result<bool> {
        self.require_key()?;
        self.require_voice()?;
        emit(TtsAudio::PcmStart {
            sample_rate: self.sample_rate,
        })?;
        for attempt in 0..2 {
            if cancelled(cancel) {
                return Ok(false);
            }
            self.connect()?;
            match self.send_realtime(text, cancel, emit) {
                Ok(played) => return Ok(played),
                Err(StreamError::IdleTimeout) if attempt == 0 => self.close(),
                Err(StreamError::IdleTimeout) => {
                    self.close();
                    return Err(io::Error::new(
                        io::ErrorKind::TimedOut,
                        "Cartesia 实时连接空闲超时",
                    ));
                }
                Err(StreamError::Connection(error)) => {
                    self.close();
                    return Err(error);
                }
                Err(StreamError::Event(error)) => return Err(error),
            }
        }
        unreachable!("the realtime retry loop always returns");
    }

    fn send_realtime(
        &mut self,
        text: &str,
        cancel: &AtomicBool,
        emit: &mut impl FnMut(TtsAudio<'_>) -> io::Result<()>,
    ) -> Result<bool, StreamError> {
        let voice = self.voice_id.as_deref().ok_or_else(|| {
            StreamError::Connection(io::Error::new(
                io::ErrorKind::InvalidInput,
                "缺少音色配置，请先在设置中刷新音色列表并选择一个音色",
            ))
        })?;
        let key = self.api_key.as_deref().ok_or_else(|| {
            StreamError::Connection(io::Error::new(
                io::ErrorKind::InvalidInput,
                "缺少 Cartesia API Key，请先在设置中配置",
            ))
        })?;
        let id = format!("cartesia-tts-{}", uuid::Uuid::new_v4());
        let start = cartesia_request(
            text,
            voice,
            self.volume,
            self.sample_rate,
            Some((&id, true)),
        );
        let end = cartesia_request("", voice, self.volume, self.sample_rate, Some((&id, false)));
        let connection = self
            .socket
            .as_ref()
            .ok_or_else(|| StreamError::Connection(io::Error::other("Cartesia 实时连接未建立")))?;
        connection
            .lock()
            .play(&id, key, [&start, &end], cancel, emit)
    }

    fn require_key(&self) -> io::Result<&str> {
        self.api_key
            .as_deref()
            .filter(|key| !key.is_empty())
            .ok_or_else(|| {
                let provider = if self.backend == Backend::VolcengineStreaming {
                    "火山引擎"
                } else {
                    "Cartesia"
                };
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    format!("缺少 {provider} API Key，请先在设置中配置"),
                )
            })
    }

    fn require_voice(&self) -> io::Result<&str> {
        self.voice_id
            .as_deref()
            .filter(|id| !id.is_empty())
            .ok_or_else(|| {
                let message = if self.backend == Backend::VolcengineStreaming {
                    "缺少音色 ID，请在设置中输入 Speaker ID"
                } else {
                    "缺少音色配置，请先在设置中刷新音色列表并选择一个音色"
                };
                io::Error::new(io::ErrorKind::InvalidInput, message)
            })
    }

    fn http(&mut self) -> io::Result<Client> {
        if let Some(client) = &self.http {
            return Ok(client.clone());
        }
        let client = Client::builder()
            .connect_timeout(Duration::from_secs(10))
            // Python's requests timeout is per socket operation, not a whole-request
            // deadline. reqwest's blocking builder defaults to a 30 second total
            // deadline, so disable it; long TTS streams must not be cut off.
            // Match requests: follow redirects; reqwest strips Authorization on a
            // cross-origin hop, as requests does.
            .timeout(None::<Duration>)
            .redirect(Policy::limited(10))
            .build()
            .map_err(|error| transport_error("无法初始化 TTS HTTP 客户端", error))?;
        self.http = Some(client.clone());
        Ok(client)
    }
}

fn cancelled(cancel: &AtomicBool) -> bool {
    cancel.load(Ordering::Relaxed)
}

fn invalid(message: impl Into<String>) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message.into())
}

fn transport_error(prefix: &str, error: reqwest::Error) -> io::Error {
    if error.is_timeout() {
        return io::Error::new(io::ErrorKind::TimedOut, format!("{prefix}：请求超时"));
    }
    io::Error::other(format!("{prefix}：{}", error.without_url()))
}

fn sanitize(message: &str, key: &str) -> String {
    let text = if key.is_empty() {
        message.to_owned()
    } else {
        message.replace(key, "***")
    };
    // Preserve the old HTTP protection for any key-shaped token in server errors.
    let mut rest = text.as_str();
    let mut redacted = String::new();
    while let Some(start) = rest.find("sk") {
        let (prefix, candidate) = rest.split_at(start);
        redacted.push_str(prefix);
        let suffix = &candidate[2..];
        if let Some(value) = suffix.strip_prefix(['-', '_']) {
            let length = value
                .bytes()
                .take_while(|&byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
                .count();
            if length != 0 {
                redacted.push_str("sk_***");
                rest = &value[length..];
                continue;
            }
        }
        redacted.push_str("sk");
        rest = suffix;
    }
    redacted.push_str(rest);
    redacted
        .chars()
        .take(300)
        .map(|character| {
            if character == '\n' || character == '\r' {
                ' '
            } else {
                character
            }
        })
        .collect()
}

fn check_status(response: Response, prefix: &str, key: &str) -> io::Result<Response> {
    let status = response.status();
    if status.is_success() {
        return Ok(response);
    }
    // Read just enough of the server error body to explain HTTP failures.
    let mut bytes = Vec::new();
    let _ = response.take(4096).read_to_end(&mut bytes);
    let message = sanitize(&String::from_utf8_lossy(&bytes), key);
    Err(io::Error::other(format!(
        "{prefix}：HTTP {} {message}",
        status.as_u16()
    )))
}

fn cartesia_request(
    text: &str,
    voice: &str,
    volume: f64,
    sample_rate: u32,
    context: Option<(&str, bool)>,
) -> Value {
    let container = if context.is_some() { "raw" } else { "wav" };
    let mut body = json!({
        "transcript": text,
        "language": "zh",
        "model_id": "sonic-3.5",
        "voice": {"mode": "id", "id": voice},
        "output_format": {"container": container, "encoding": "pcm_s16le", "sample_rate": sample_rate},
        "generation_config": {"volume": volume, "speed": 1},
    });
    if let Some((id, continuing)) = context {
        body["context_id"] = json!(id);
        body["continue"] = json!(continuing);
    } else {
        body["speed"] = json!("normal");
    }
    body
}

fn cartesia_voices(data: Value) -> io::Result<Vec<Voice>> {
    let records = data["data"]
        .as_array()
        .ok_or_else(|| invalid("获取音色列表失败：Cartesia 返回格式不是 Voice 数组"))?;
    let mut voices: Vec<_> = records
        .iter()
        .filter_map(|record| {
            let id = record["id"].as_str()?.to_owned();
            let name = record["name"].as_str()?.to_owned();
            (!id.is_empty() && !name.is_empty()).then_some(Voice { id, name })
        })
        .collect();
    voices.sort_by_cached_key(|voice| voice.name.to_lowercase());
    Ok(voices)
}

fn volcengine_request(text: &str, voice: &str, volume: f64, sample_rate: u32) -> Value {
    let loudness = ((volume - 1.0) * 100.0)
        .round_ties_even()
        .clamp(-50.0, 100.0) as i32;
    json!({
        "req_params": {
            "text": text,
            "speaker": voice,
            "audio_params": {
                "format": "pcm",
                "sample_rate": sample_rate,
                "loudness_rate": loudness,
                "speech_rate": 0
            }
        }
    })
}

#[derive(Debug, Eq, PartialEq)]
enum Event {
    Audio(Vec<u8>),
    Done,
    Ignore,
}

fn event_audio(event: &Value) -> io::Result<Event> {
    match event["data"].as_str().filter(|data| !data.is_empty()) {
        Some(data) => ENCODING
            .decode(data)
            .map(Event::Audio)
            .map_err(|_| invalid("无法解码 TTS 音频")),
        None => Ok(Event::Ignore),
    }
}

fn sse_event(line: &str, key: &str) -> io::Result<Event> {
    let payload = line
        .strip_prefix("data:")
        .map(str::trim_start)
        .unwrap_or(line);
    let Ok(event) = serde_json::from_str::<Value>(payload) else {
        return Ok(Event::Ignore);
    };
    match event["code"].as_i64() {
        Some(20_000_000 | 3000) => Ok(Event::Done),
        Some(code) if code != 0 => {
            let message = sanitize(event["message"].as_str().unwrap_or("未知错误"), key);
            Err(io::Error::other(format!(
                "Volcengine 流式 TTS 失败（code={code}）: {message}"
            )))
        }
        _ => event_audio(&event),
    }
}

fn play_sse(
    mut body: impl BufRead,
    key: &str,
    cancel: &AtomicBool,
    mut emit: impl FnMut(TtsAudio<'_>) -> io::Result<()>,
) -> io::Result<bool> {
    let mut played = false;
    let mut line = String::new();
    while !cancelled(cancel) && body.read_line(&mut line)? != 0 {
        if cancelled(cancel) {
            return Ok(false);
        }
        match sse_event(&line, key)? {
            Event::Done => break,
            Event::Ignore => (),
            Event::Audio(audio) => {
                for chunk in audio.chunks(PCM_CHUNK_BYTES) {
                    if cancelled(cancel) {
                        return Ok(false);
                    }
                    emit(TtsAudio::Pcm(chunk))?;
                    played = true;
                }
            }
        }
        line.clear();
    }
    Ok(played && !cancelled(cancel))
}

fn ws_event(message: &str, context_id: &str, key: &str) -> io::Result<Event> {
    let event =
        serde_json::from_str::<Value>(message).map_err(|_| invalid("Cartesia 实时返回格式无效"))?;
    // Done and chunk frames from a cancelled context can race the next request.
    if event["context_id"]
        .as_str()
        .is_some_and(|id| id != context_id)
    {
        return Ok(Event::Ignore);
    }
    match event["type"].as_str() {
        Some("chunk") => event_audio(&event),
        Some("done") => Ok(Event::Done),
        Some("error") => {
            let error = event["message"]
                .as_str()
                .filter(|message| !message.is_empty())
                .or_else(|| event["title"].as_str())
                .unwrap_or_default();
            Err(io::Error::other(format!(
                "Cartesia realtime 返回错误: {}",
                sanitize(error, key)
            )))
        }
        _ => Ok(Event::Ignore),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cartesia_bytes_and_websocket_use_existing_wire_payloads() {
        assert_eq!(
            cartesia_request("你好", "voice", 1.5, 48_000, None),
            json!({
                "transcript": "你好",
                "language": "zh",
                "model_id": "sonic-3.5",
                "voice": {"mode": "id", "id": "voice"},
                "output_format": {"container": "wav", "encoding": "pcm_s16le", "sample_rate": 48000},
                "generation_config": {"volume": 1.5, "speed": 1},
                "speed": "normal"
            })
        );
        let continuing = cartesia_request("你好", "voice", 0.5, 44_100, Some(("id", true)));
        assert_eq!(continuing["continue"], true);
        assert_eq!(continuing["context_id"], "id");
        assert_eq!(continuing["output_format"]["sample_rate"], 44_100);
        assert_eq!(continuing["output_format"]["container"], "raw");
        assert!(continuing.get("speed").is_none());
        let final_frame = cartesia_request("", "voice", 0.5, 44_100, Some(("id", false)));
        assert_eq!(final_frame["continue"], false);
        assert_eq!(final_frame["transcript"], "");
    }

    #[test]
    fn volcengine_uses_existing_volume_rate_sample_format_and_stream_completion() {
        let params = volcengine_request("你好", "voice", 1.25, 44_100);
        assert_eq!(
            params["req_params"]["audio_params"],
            json!({
                "format": "pcm",
                "sample_rate": 44_100,
                "loudness_rate": 25,
                "speech_rate": 0
            })
        );
        assert_eq!(
            volcengine_request("a", "v", 10.0, 48_000)["req_params"]["audio_params"]["loudness_rate"],
            100
        );
        assert_eq!(
            sse_event(r#"data: {"code":0,"data":"AQIDBA"}"#, "synthetic").unwrap(),
            Event::Audio(vec![1, 2, 3, 4])
        );
        assert_eq!(
            sse_event(r#"{"code":20000000}"#, "synthetic").unwrap(),
            Event::Done
        );
        assert_eq!(
            sse_event(r#"data: {"code":3000}"#, "synthetic").unwrap(),
            Event::Done
        );
        assert_eq!(
            sse_event("event: message", "synthetic").unwrap(),
            Event::Ignore
        );
        assert_eq!(sse_event(":heartbeat", "synthetic").unwrap(), Event::Ignore);
        let error =
            sse_event(r#"data: {"code":42,"message":"synthetic"}"#, "synthetic").unwrap_err();
        assert!(error.to_string().contains("42"));
        assert!(!error.to_string().contains("synthetic"));
    }

    #[test]
    fn stale_websocket_chunks_and_done_are_ignored_on_context_switch() {
        assert_eq!(
            ws_event(
                r#"{"type":"chunk","context_id":"old","data":"AQI="}"#,
                "current",
                "synthetic"
            )
            .unwrap(),
            Event::Ignore
        );
        assert_eq!(
            ws_event(
                r#"{"type":"done","context_id":"old"}"#,
                "current",
                "synthetic"
            )
            .unwrap(),
            Event::Ignore
        );
        assert_eq!(
            ws_event(
                r#"{"type":"chunk","context_id":"current","data":"AQI="}"#,
                "current",
                "synthetic"
            )
            .unwrap(),
            Event::Audio(vec![1, 2])
        );
        assert_eq!(
            ws_event(
                r#"{"type":"done","context_id":"current"}"#,
                "current",
                "synthetic"
            )
            .unwrap(),
            Event::Done
        );
        assert!(
            ws_event(
                r#"{"type":"error","title":"failure synthetic"}"#,
                "current",
                "synthetic"
            )
            .is_err()
        );
    }

    #[test]
    fn cancelling_sse_stops_remaining_blocks_and_future_events() {
        let stimulus = json!({
            "data": ENCODING.encode(vec![0; 7_000]),
        });
        let body = format!("data: {stimulus}\n\ndata: {stimulus}\n");
        let cancel = AtomicBool::new(false);
        let mut lengths = Vec::new();
        let played = play_sse(body.as_bytes(), "synthetic", &cancel, |event| {
            if let TtsAudio::Pcm(data) = event {
                lengths.push(data.len());
                cancel.store(true, Ordering::Relaxed);
            }
            Ok(())
        })
        .unwrap();
        assert!(!played);
        assert_eq!(lengths, [2048]);
    }

    #[test]
    fn voice_list_filters_invalid_items_and_keeps_alphabetic_order() {
        assert_eq!(
            cartesia_voices(json!({"data":[{"name":"x"}, 22, {"id":"","name":"Empty"}, {"id":"b","name":"beta"}, {"id":"a","name":"Alpha"}]})).unwrap(),
            [
                Voice { id: "a".into(), name: "Alpha".into() },
                Voice { id: "b".into(), name: "beta".into() }
            ]
        );
        assert!(cartesia_voices(json!({"data":null})).is_err());
    }

    #[test]
    fn errors_redact_configured_and_key_shaped_tokens() {
        assert_eq!(
            sanitize(
                "认证失败 synthetic，sk_test_fixture sk-test-fixture",
                "synthetic"
            ),
            "认证失败 ***，sk_*** sk_***"
        );
    }

    #[test]
    fn streaming_rates_follow_supported_device_but_bytes_stays_at_48k() {
        let mut settings = Settings::default();
        let mut stream = TtsEngine::new(&settings, None);
        stream.set_device_sample_rate(Some(44_100));
        stream.set_device_sample_rate(Some(96_000));
        assert_eq!(stream.sample_rate, 44_100);
        settings.active_tts_provider = Provider::Cartesia;
        let mut bytes = TtsEngine::new(&settings, None);
        bytes.set_device_sample_rate(Some(44_100));
        assert_eq!(bytes.sample_rate, 48_000);
    }
}
