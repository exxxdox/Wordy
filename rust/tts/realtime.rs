use std::{
    io::{self, Read, Write},
    net::{TcpStream, ToSocketAddrs},
    sync::{
        Arc, Mutex, MutexGuard,
        atomic::{AtomicBool, Ordering},
    },
    thread::{self, JoinHandle},
    time::{Duration, Instant},
};

use serde_json::{Value, json};
use tungstenite::{
    Error, Message, WebSocket,
    client::IntoClientRequest,
    client_tls,
    http::{HeaderValue, header::AUTHORIZATION},
    protocol::frame::{CloseFrame, coding::CloseCode},
    stream::MaybeTlsStream,
};

use super::{Event, TtsAudio, cancelled, sanitize, ws_event};

const DEFAULT_BASE_URL: &str = "wss://api.cartesia.ai";
const WS_PATH: &str = "/tts/websocket";
// The SDK used a separate default version from the Bytes and Voices HTTP calls.
const WS_VERSION: &str = "2025-11-04";
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_POLL: Duration = Duration::from_millis(200);
const PING_INTERVAL: Duration = Duration::from_secs(60);
const PING_TIMEOUT: Duration = Duration::from_secs(20);
const PING_PAYLOAD: &[u8] = b"wordy";
const IDLE_TIMEOUT_REASON: &str = "connection idle timeout";
// Local clip playback blocks the TTS worker, so a second thread owns the idle
// keepalive; it only touches the socket between play calls.
const KEEPALIVE_TICK: Duration = Duration::from_millis(500);

pub(super) enum StreamError {
    IdleTimeout,
    Connection(io::Error),
    Event(io::Error),
}

impl StreamError {
    fn transport(error: Error) -> Self {
        Self::Connection(io::Error::other(format!("Cartesia 实时连接错误：{error}")))
    }
}

pub(super) struct CartesiaSocket<T = MaybeTlsStream<TcpStream>> {
    socket: WebSocket<T>,
    next_ping: Instant,
    outstanding_ping: Option<Instant>,
}

impl CartesiaSocket {
    pub(super) fn connect(key: &str) -> io::Result<Self> {
        let mut request = websocket_url()
            .into_client_request()
            .map_err(|error| io::Error::other(error.to_string()))?;
        let mut authorization = HeaderValue::from_str(&format!("Bearer {key}")).map_err(|_| {
            io::Error::new(io::ErrorKind::InvalidInput, "Cartesia API Key 格式无效")
        })?;
        authorization.set_sensitive(true);
        request.headers_mut().insert(AUTHORIZATION, authorization);
        request
            .headers_mut()
            .insert("Cartesia-Version", HeaderValue::from_static(WS_VERSION));
        let uri = request.uri();
        let host = uri
            .host()
            .ok_or_else(|| {
                io::Error::new(io::ErrorKind::InvalidInput, "Cartesia 实时服务地址缺少主机")
            })?
            .to_owned();
        let encrypted = matches!(uri.scheme_str(), Some("wss" | "https"));
        let port = uri.port_u16().unwrap_or(if encrypted { 443 } else { 80 });
        let stream = connect_tcp((host.as_str(), port))?;
        stream.set_read_timeout(Some(CONNECT_TIMEOUT))?;
        stream.set_write_timeout(Some(CONNECT_TIMEOUT))?;
        stream.set_nodelay(true)?;
        let (socket, _) = client_tls(request, stream)
            .map_err(|error| io::Error::other(format!("连接 Cartesia 实时服务失败：{error}")))?;
        match socket.get_ref() {
            MaybeTlsStream::NativeTls(tls) => tls.get_ref().set_read_timeout(Some(READ_POLL))?,
            MaybeTlsStream::Plain(stream) => stream.set_read_timeout(Some(READ_POLL))?,
            _ => return Err(io::Error::other("Cartesia 实时连接的 TLS 提供程序无效")),
        }
        Ok(Self {
            socket,
            next_ping: Instant::now() + PING_INTERVAL,
            outstanding_ping: None,
        })
    }
}

impl<T: Read + Write> CartesiaSocket<T> {
    pub(super) fn close(&mut self) {
        let _ = self.socket.close(None);
    }

    pub(super) fn play(
        &mut self,
        context: &str,
        key: &str,
        frames: [&Value; 2],
        cancel: &AtomicBool,
        emit: &mut impl FnMut(TtsAudio<'_>) -> io::Result<()>,
    ) -> Result<bool, StreamError> {
        for frame in frames {
            self.send_json(frame)?;
        }
        loop {
            if cancelled(cancel) {
                self.cancel_context(context);
                return Ok(false);
            }
            self.ping_if_due()?;
            let Some(message) = self.read_message()? else {
                continue;
            };
            let decoded = match &message {
                Message::Text(text) => text.as_str(),
                Message::Binary(data) => std::str::from_utf8(data).map_err(|_| {
                    StreamError::Event(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "Cartesia 实时返回格式无效",
                    ))
                })?,
                Message::Close(frame) => {
                    let _ = self.socket.flush();
                    // Only the server's idle-timeout close is retryable; any other
                    // close is a failure (Python cartesia.py:432-443).
                    return Err(close_result(frame.as_ref(), Some(key)));
                }
                _ => continue,
            };
            match ws_event(decoded, context, key).map_err(StreamError::Event)? {
                Event::Ignore => (),
                Event::Done => return Ok(true),
                Event::Audio(audio) => {
                    if let Err(error) = emit(TtsAudio::Pcm(&audio)) {
                        if error.kind() == io::ErrorKind::Interrupted {
                            self.cancel_context(context);
                            return Ok(false);
                        }
                        return Err(StreamError::Event(error));
                    }
                }
            }
        }
    }

    pub(super) fn poll_keepalive(&mut self) -> Result<(), StreamError> {
        self.ping_if_due()?;
        if self.outstanding_ping.is_some()
            && let Some(Message::Close(frame)) = self.read_message()?
        {
            let _ = self.socket.flush();
            return Err(close_result(frame.as_ref(), None));
        }
        Ok(())
    }

    fn send_json(&mut self, value: &Value) -> Result<(), StreamError> {
        self.socket
            .send(Message::Text(value.to_string().into()))
            .map_err(StreamError::transport)
    }

    fn cancel_context(&mut self, context: &str) {
        // Worker owns both synthesis and cancellation writes to this socket.
        let _ = self.send_json(&json!({"cancel": true, "context_id": context}));
    }

    fn ping_if_due(&mut self) -> Result<(), StreamError> {
        let now = Instant::now();
        if self
            .outstanding_ping
            .is_some_and(|deadline| now >= deadline)
        {
            return Err(StreamError::Connection(io::Error::new(
                io::ErrorKind::TimedOut,
                "Cartesia 实时连接保活超时",
            )));
        }
        if now >= self.next_ping && self.outstanding_ping.is_none() {
            self.socket
                .send(Message::Ping(PING_PAYLOAD.into()))
                .map_err(StreamError::transport)?;
            self.outstanding_ping = Some(now + PING_TIMEOUT);
            self.next_ping = now + PING_INTERVAL;
        }
        Ok(())
    }

    fn read_message(&mut self) -> Result<Option<Message>, StreamError> {
        match self.socket.read() {
            Ok(Message::Pong(payload)) if payload.as_ref() == PING_PAYLOAD => {
                self.outstanding_ping = None;
                Ok(None)
            }
            Ok(message) => Ok(Some(message)),
            Err(Error::Io(error))
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) =>
            {
                Ok(None)
            }
            Err(error) => Err(StreamError::transport(error)),
        }
    }
}

/// Owns a live socket plus the background keepalive thread, so long local
/// clip playback cannot let the websocket go idle.
pub(super) struct RealtimeConnection {
    socket: Arc<Mutex<CartesiaSocket>>,
    stop: Arc<AtomicBool>,
    dead: Arc<AtomicBool>,
    pinger: Option<JoinHandle<()>>,
}

impl RealtimeConnection {
    pub(super) fn connect(key: &str) -> io::Result<Self> {
        let socket = Arc::new(Mutex::new(CartesiaSocket::connect(key)?));
        let stop = Arc::new(AtomicBool::new(false));
        let dead = Arc::new(AtomicBool::new(false));
        let pinger = {
            let socket = Arc::clone(&socket);
            let stop = Arc::clone(&stop);
            let dead = Arc::clone(&dead);
            thread::Builder::new()
                .name("cartesia-keepalive".into())
                .spawn(move || keepalive_loop(&socket, &stop, &dead))
                .map_err(|error| {
                    io::Error::other(format!("启动 Cartesia 实时保活线程失败：{error}"))
                })?
        };
        Ok(Self {
            socket,
            stop,
            dead,
            pinger: Some(pinger),
        })
    }

    pub(super) fn is_dead(&self) -> bool {
        self.dead.load(Ordering::Relaxed)
    }

    pub(super) fn lock(&self) -> MutexGuard<'_, CartesiaSocket> {
        match self.socket.lock() {
            Ok(socket) => socket,
            Err(poisoned) => poisoned.into_inner(),
        }
    }

    pub(super) fn close(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(pinger) = self.pinger.take() {
            let _ = pinger.join();
        }
        self.lock().close();
    }
}

impl Drop for RealtimeConnection {
    fn drop(&mut self) {
        self.close();
    }
}

fn keepalive_loop(socket: &Mutex<CartesiaSocket>, stop: &AtomicBool, dead: &AtomicBool) {
    loop {
        thread::sleep(KEEPALIVE_TICK);
        if stop.load(Ordering::Relaxed) {
            return;
        }
        let mut socket = match socket.lock() {
            Ok(socket) => socket,
            Err(_) => break,
        };
        // While a speech is playing the worker holds this lock and pings from
        // inside its own read loop, so blocking here is expected.
        if socket.poll_keepalive().is_err() {
            break;
        }
    }
    dead.store(true, Ordering::Relaxed);
}

fn close_result(frame: Option<&CloseFrame>, key: Option<&str>) -> StreamError {
    match frame {
        // The server asks the client to reconnect only with this exact close.
        Some(frame) if frame.code == CloseCode::Normal && frame.reason == IDLE_TIMEOUT_REASON => {
            StreamError::IdleTimeout
        }
        Some(frame) => {
            let reason = match key {
                Some(key) => sanitize(&frame.reason, key),
                None => frame.reason.to_string(),
            };
            StreamError::Connection(io::Error::other(format!(
                "Cartesia 实时连接关闭：{} {reason}",
                frame.code
            )))
        }
        None => StreamError::Connection(io::Error::other("Cartesia 实时连接已关闭")),
    }
}

fn websocket_url() -> String {
    // The SDK reads CARTESIA_BASE_URL as an HTTP base URL and derives the
    // websocket endpoint from it.
    let base = std::env::var("CARTESIA_BASE_URL")
        .ok()
        .filter(|value| !value.trim().is_empty())
        .unwrap_or_else(|| DEFAULT_BASE_URL.to_owned());
    websocket_endpoint(&base)
}

fn websocket_endpoint(base: &str) -> String {
    let base = base.trim().trim_end_matches('/');
    let base = if let Some(rest) = base.strip_prefix("https://") {
        format!("wss://{rest}")
    } else if let Some(rest) = base.strip_prefix("http://") {
        format!("ws://{rest}")
    } else {
        base.to_owned()
    };
    format!("{base}{WS_PATH}")
}

fn connect_tcp(host: impl ToSocketAddrs) -> io::Result<TcpStream> {
    let addresses = host.to_socket_addrs()?;
    let deadline = Instant::now() + CONNECT_TIMEOUT;
    let mut error = io::Error::new(io::ErrorKind::NotFound, "找不到 Cartesia 实时服务地址");
    for address in addresses {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            break;
        }
        match TcpStream::connect_timeout(&address, remaining) {
            Ok(stream) => return Ok(stream),
            Err(last) => error = last,
        }
    }
    Err(error)
}

#[cfg(test)]
mod tests {
    use std::{io::Cursor, sync::atomic::Ordering};

    use tungstenite::protocol::Role;

    use super::*;

    struct ScriptedIo {
        incoming: Cursor<Vec<u8>>,
        outgoing: Vec<u8>,
    }

    impl Read for ScriptedIo {
        fn read(&mut self, output: &mut [u8]) -> io::Result<usize> {
            self.incoming.read(output)
        }
    }

    impl Write for ScriptedIo {
        fn write(&mut self, input: &[u8]) -> io::Result<usize> {
            self.outgoing.write(input)
        }

        fn flush(&mut self) -> io::Result<()> {
            Ok(())
        }
    }

    fn socket_with(messages: Vec<Message>) -> CartesiaSocket<ScriptedIo> {
        let mut server = WebSocket::from_raw_socket(Cursor::new(Vec::new()), Role::Server, None);
        for message in messages {
            server.send(message).unwrap();
        }
        CartesiaSocket {
            socket: WebSocket::from_raw_socket(
                ScriptedIo {
                    incoming: Cursor::new(server.into_inner().into_inner()),
                    outgoing: Vec::new(),
                },
                Role::Client,
                None,
            ),
            next_ping: Instant::now() + PING_INTERVAL,
            outstanding_ping: None,
        }
    }

    fn play_fixture(mut client: CartesiaSocket<ScriptedIo>) -> Result<bool, StreamError> {
        let cancel = AtomicBool::new(false);
        client.play(
            "context",
            "fixture",
            [
                &json!({"context_id": "context", "continue": true}),
                &json!({"context_id": "context", "continue": false}),
            ],
            &cancel,
            &mut |_| Ok(()),
        )
    }

    #[test]
    fn close_frames_only_retry_the_clean_idle_timeout() {
        let close = |code, reason: &str| {
            Message::Close(Some(CloseFrame {
                code,
                reason: reason.into(),
            }))
        };
        assert!(matches!(
            play_fixture(socket_with(vec![close(
                CloseCode::Normal,
                IDLE_TIMEOUT_REASON
            )])),
            Err(StreamError::IdleTimeout)
        ));
        // A normal close without the idle-timeout reason is still a failure.
        assert!(matches!(
            play_fixture(socket_with(vec![close(CloseCode::Normal, "bye")])),
            Err(StreamError::Connection(_))
        ));
        assert!(matches!(
            play_fixture(socket_with(vec![close(CloseCode::Away, "restart")])),
            Err(StreamError::Connection(_))
        ));
        assert!(matches!(
            play_fixture(socket_with(vec![Message::Close(None)])),
            Err(StreamError::Connection(_))
        ));
    }

    #[test]
    fn base_url_is_converted_to_the_websocket_endpoint() {
        assert_eq!(
            websocket_endpoint("https://api.cartesia.ai/"),
            "wss://api.cartesia.ai/tts/websocket"
        );
        assert_eq!(
            websocket_endpoint("http://127.0.0.1:8080"),
            "ws://127.0.0.1:8080/tts/websocket"
        );
        assert_eq!(
            websocket_endpoint("wss://proxy.example/base/"),
            "wss://proxy.example/base/tts/websocket"
        );
    }

    #[test]
    fn websocket_sends_cancel_and_drops_old_context_frames_after_it() {
        let mut server = WebSocket::from_raw_socket(Cursor::new(Vec::new()), Role::Server, None);
        for message in [
            r#"{"type":"chunk","context_id":"old","data":"AQI="}"#,
            r#"{"type":"chunk","context_id":"old","data":"AQI="}"#,
            r#"{"type":"done","context_id":"old"}"#,
            r#"{"type":"chunk","context_id":"new","data":"AwQ="}"#,
            r#"{"type":"done","context_id":"new"}"#,
        ] {
            server.send(Message::Text(message.into())).unwrap();
        }
        let fixture = ScriptedIo {
            incoming: Cursor::new(server.into_inner().into_inner()),
            outgoing: Vec::new(),
        };
        let mut client = CartesiaSocket {
            socket: WebSocket::from_raw_socket(fixture, Role::Client, None),
            next_ping: Instant::now() + PING_INTERVAL,
            outstanding_ping: None,
        };
        let cancel = AtomicBool::new(false);
        let mut played = Vec::new();
        let mut speak = |context, should_cancel| {
            cancel.store(false, Ordering::Relaxed);
            client.play(
                context,
                "fixture",
                [
                    &json!({"context_id":context,"continue":true}),
                    &json!({"context_id":context,"continue":false}),
                ],
                &cancel,
                &mut |event| {
                    if let TtsAudio::Pcm(samples) = event {
                        played.extend_from_slice(samples);
                        cancel.store(should_cancel, Ordering::Relaxed);
                    }
                    Ok(())
                },
            )
        };
        assert!(matches!(speak("old", true), Ok(false)));
        assert!(matches!(speak("new", false), Ok(true)));
        assert_eq!(played, [1, 2, 3, 4]);

        let outgoing = client.socket.into_inner().outgoing;
        let mut receiver = WebSocket::from_raw_socket(Cursor::new(outgoing), Role::Server, None);
        for expected in [
            json!({"context_id":"old","continue":true}),
            json!({"context_id":"old","continue":false}),
            json!({"context_id":"old","cancel":true}),
            json!({"context_id":"new","continue":true}),
            json!({"context_id":"new","continue":false}),
        ] {
            let sent = receiver.read().unwrap().into_text().unwrap();
            assert_eq!(serde_json::from_str::<Value>(&sent).unwrap(), expected);
        }
    }
}
