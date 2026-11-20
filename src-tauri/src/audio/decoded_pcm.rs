use std::fs::File;
use std::io::{self, Cursor, Read};
use std::path::{Path, PathBuf};
use std::process::{Child, Stdio};
use std::sync::{atomic::AtomicBool, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use super::{
    DECODED_BLOCK_BYTES, check_cancelled, clip_format, decoder_command, invalid_audio, parse_wave,
    read_decoded_block, spawn_decoder,
};

pub(crate) struct DecodedPcm {
    child: Child,
    blocks: mpsc::Receiver<io::Result<Vec<i16>>>,
}

impl DecodedPcm {
    pub(crate) fn open(path: &Path, cancel: &AtomicBool) -> io::Result<Self> {
        Self::open_with(path, cancel, crate::ffmpeg::find)
    }

    pub(super) fn open_with(
        path: &Path,
        cancel: &AtomicBool,
        resolve_decoder: impl FnOnce() -> Option<PathBuf>,
    ) -> io::Result<Self> {
        use std::os::windows::io::{AsRawHandle, FromRawHandle};
        use std::ptr::null_mut;
        use windows_sys::Win32::Foundation::{
            ERROR_NO_DATA, ERROR_PIPE_CONNECTED, ERROR_PIPE_LISTENING, INVALID_HANDLE_VALUE,
        };
        use windows_sys::Win32::Storage::FileSystem::{
            FILE_FLAG_FIRST_PIPE_INSTANCE, PIPE_ACCESS_DUPLEX,
        };
        use windows_sys::Win32::System::Pipes::{
            ConnectNamedPipe, CreateNamedPipeW, GetNamedPipeClientProcessId, PIPE_NOWAIT,
            PIPE_REJECT_REMOTE_CLIENTS, PIPE_WAIT, SetNamedPipeHandleState,
        };

        check_cancelled(cancel)?;
        if !path.is_file() {
            return Err(io::Error::new(io::ErrorKind::NotFound, "找不到音频文件"));
        }
        let format = clip_format(path).ok_or_else(|| invalid_audio("不支持此音频格式"))?;
        let executable = resolve_decoder().ok_or_else(|| invalid_audio("音频解码器不可用"))?;
        let pipe = format!(r"\\.\pipe\Wordy.Decode.{}", uuid::Uuid::new_v4());
        let wide: Vec<u16> = pipe.encode_utf16().chain([0]).collect();
        // The bundled FFmpeg supports only the file protocol and WAV muxer.
        let handle = unsafe {
            CreateNamedPipeW(
                wide.as_ptr(),
                PIPE_ACCESS_DUPLEX | FILE_FLAG_FIRST_PIPE_INSTANCE,
                PIPE_NOWAIT | PIPE_REJECT_REMOTE_CLIENTS,
                1,
                0,
                DECODED_BLOCK_BYTES as u32,
                0,
                std::ptr::null(),
            )
        };
        if handle == INVALID_HANDLE_VALUE {
            return Err(io::Error::other(format!(
                "无法创建音频解码管道：{}",
                io::Error::last_os_error()
            )));
        }
        let mut input = unsafe { File::from_raw_handle(handle.cast()) };
        let mut command = decoder_command(&executable, path, format)?;
        command
            .args(["-f", "wav"])
            .arg(format!("file:{pipe}"))
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        let (send, blocks) = mpsc::sync_channel(4);
        let mut decoder = Self {
            child: spawn_decoder(&mut command)?,
            blocks,
        };
        let started = Instant::now();
        loop {
            check_cancelled(cancel)?;
            if unsafe { ConnectNamedPipe(input.as_raw_handle().cast(), null_mut()) } != 0 {
                break;
            }
            let error = io::Error::last_os_error();
            match error.raw_os_error().map(|code| code as u32) {
                Some(ERROR_PIPE_CONNECTED | ERROR_NO_DATA) => break,
                Some(ERROR_PIPE_LISTENING) => {}
                _ => return Err(error),
            }
            if decoder.child.try_wait()?.is_some() {
                return Err(invalid_audio("不支持播放此音频格式"));
            }
            check_decoder_timeout(started)?;
            thread::sleep(Duration::from_millis(10));
        }
        let mut client_pid = 0;
        if unsafe { GetNamedPipeClientProcessId(handle, &mut client_pid) } == 0 {
            return Err(io::Error::last_os_error());
        }
        if client_pid != decoder.child.id() {
            return Err(io::Error::new(
                io::ErrorKind::PermissionDenied,
                "音频解码管道连接的进程不匹配",
            ));
        }
        if unsafe {
            SetNamedPipeHandleState(
                input.as_raw_handle().cast(),
                &PIPE_WAIT,
                null_mut(),
                null_mut(),
            )
        } == 0
        {
            return Err(io::Error::other(format!(
                "无法设置音频解码管道：{}",
                io::Error::last_os_error()
            )));
        }
        // The bounded queue applies backpressure without blocking cancellation on a pipe read.
        thread::Builder::new()
            .name("WordyDecodePlayback".into())
            .spawn(move || {
                if let Err(error) = read_decoded_header(&mut input) {
                    let _ = send.send(Err(error));
                    return;
                }
                let mut buffer = [0; DECODED_BLOCK_BYTES];
                loop {
                    match read_decoded_block(&mut input, &mut buffer) {
                        Ok(samples) if samples.is_empty() => break,
                        Ok(samples) => {
                            if send.send(Ok(samples)).is_err() {
                                break;
                            }
                        }
                        Err(error) => {
                            let _ = send.send(Err(error));
                            break;
                        }
                    }
                }
            })?;
        Ok(decoder)
    }

    pub(crate) fn next_block(&mut self, cancel: &AtomicBool) -> io::Result<Option<Vec<i16>>> {
        let started = Instant::now();
        let poll = Duration::from_millis(10);
        loop {
            check_cancelled(cancel)?;
            match self.blocks.recv_timeout(poll) {
                Ok(block) => return block.map(Some),
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    if let Some(status) = self.child.try_wait()? {
                        return if status.success() {
                            Ok(None)
                        } else {
                            Err(invalid_audio("不支持播放此音频格式"))
                        };
                    }
                    thread::sleep(poll);
                }
                Err(mpsc::RecvTimeoutError::Timeout) => {}
            }
            check_decoder_timeout(started)?;
        }
    }
}

impl Drop for DecodedPcm {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn check_decoder_timeout(started: Instant) -> io::Result<()> {
    if started.elapsed() >= Duration::from_secs(30) {
        return Err(io::Error::new(io::ErrorKind::TimedOut, "音频解码超时"));
    }
    Ok(())
}

fn read_decoded_header(input: &mut impl Read) -> io::Result<()> {
    let mut header = [0; 4096];
    let read = |input: &mut _, bytes: &mut [u8]| {
        Read::read_exact(input, bytes).map_err(|_| invalid_audio("音频解码未返回有效的 WAV 头"))
    };
    read(input, &mut header[..12])?;
    let mut position = 12;
    while position + 8 <= header.len() {
        let chunk_start = position;
        position += 8;
        read(input, &mut header[chunk_start..position])?;
        let length = u32::from_le_bytes(header[chunk_start + 4..position].try_into().unwrap());
        if &header[chunk_start..chunk_start + 4] == b"data" && length == u32::MAX {
            let (format, _, _) =
                parse_wave(&mut Cursor::new(&header[..position]), position as u64, true)?;
            if format.sample_rate == 48_000 && format.channels == 2 && format.sample_width == 2 {
                return Ok(());
            }
            break;
        }
        let end = position as u64 + u64::from(length) + u64::from(length & 1);
        if end > header.len() as u64 {
            break;
        }
        read(input, &mut header[position..end as usize])?;
        position = end as usize;
    }
    Err(invalid_audio("音频解码未返回有效的 48 kHz 双声道 PCM16"))
}
