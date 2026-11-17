use std::{
    io,
    net::{TcpStream, ToSocketAddrs},
    sync::atomic::AtomicBool,
    time::{Duration, Instant},
};

use serde_json::{Value, json};
use tungstenite::{
    Error, Message, WebSocket,
    client::IntoClientRequest,
    client_tls,
    http::{HeaderValue, header::AUTHORIZATION},
    protocol::frame::coding::CloseCode,
    stream::MaybeTlsStream,
};

use super::{Event, TtsAudio, cancelled, sanitize, ws_event};

const WS_URL: &str = "wss://api.cartesia.ai/tts/websocket";
// The SDK used a separate default version from the Bytes and Voices HTTP calls.
const WS_VERSION: &str = "2025-11-04";
const CONNECT_TIMEOUT: Duration = Duration::from_secs(10);
const READ_POLL: Duration = Duration::from_millis(200);
const PING_INTERVAL: Duration = Duration::from_secs(60);
const PING_TIMEOUT: Duration = Duration::from_secs(20);
const PING_PAYLOAD: &[u8] = b"wordy";

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

pub(super) struct CartesiaSocket {
    socket: WebSocket<MaybeTlsStream<TcpStream>>,
    next_ping: Instant,
    outstanding_ping: Option<Instant>,
}

impl CartesiaSocket {
    pub(super) fn connect(key: &str) -> io::Result<Self> {
        let mut request = WS_URL
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
        let host = (
            request
                .uri()
                .host()
                .expect("the Cartesia endpoint has a host"),
            443,
        );
        let stream = connect_tcp(host)?;
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
                    match frame {
                        Some(frame)
                            if frame.code == CloseCode::Normal
                                && frame.reason == "connection idle timeout" =>
                        {
                            return Err(StreamError::IdleTimeout);
                        }
                        Some(frame)
                            if frame.code != CloseCode::Normal && frame.code != CloseCode::Away =>
                        {
                            let reason = sanitize(&frame.reason, key);
                            return Err(StreamError::Connection(io::Error::other(format!(
                                "Cartesia 实时连接关闭：{} {reason}",
                                frame.code
                            ))));
                        }
                        _ => return Ok(true),
                    }
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
            && let Some(Message::Close(_)) = self.read_message()?
        {
            let _ = self.socket.flush();
            return Err(StreamError::IdleTimeout);
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
    use std::{net::TcpListener, sync::atomic::Ordering, thread};

    use super::*;

    #[test]
    fn websocket_sends_cancel_and_drops_old_context_frames_after_it() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = thread::spawn(move || {
            let (stream, _) = listener.accept().unwrap();
            stream.set_read_timeout(Some(CONNECT_TIMEOUT)).unwrap();
            let mut socket = tungstenite::accept(stream).unwrap();
            for expected in [
                json!({"context_id":"old","continue":true}),
                json!({"context_id":"old","continue":false}),
            ] {
                let request = socket.read().unwrap().into_text().unwrap();
                assert_eq!(serde_json::from_str::<Value>(&request).unwrap(), expected);
            }
            socket
                .send(Message::Text(
                    r#"{"type":"chunk","context_id":"old","data":"AQI="}"#.into(),
                ))
                .unwrap();
            let cancel = socket.read().unwrap().into_text().unwrap();
            assert_eq!(
                serde_json::from_str::<Value>(&cancel).unwrap(),
                json!({"context_id":"old","cancel":true})
            );
            for expected in [
                json!({"context_id":"new","continue":true}),
                json!({"context_id":"new","continue":false}),
            ] {
                let request = socket.read().unwrap().into_text().unwrap();
                assert_eq!(serde_json::from_str::<Value>(&request).unwrap(), expected);
            }
            for message in [
                r#"{"type":"chunk","context_id":"old","data":"AQI="}"#,
                r#"{"type":"done","context_id":"old"}"#,
                r#"{"type":"chunk","context_id":"new","data":"AwQ="}"#,
                r#"{"type":"done","context_id":"new"}"#,
            ] {
                socket.send(Message::Text(message.into())).unwrap();
            }
        });

        let stream = TcpStream::connect(address).unwrap();
        stream.set_read_timeout(Some(CONNECT_TIMEOUT)).unwrap();
        let (socket, _) =
            tungstenite::client(format!("ws://{address}"), MaybeTlsStream::Plain(stream)).unwrap();
        let mut client = CartesiaSocket {
            socket,
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
        server.join().unwrap();
    }
}
