use super::*;

#[test]
fn trim_creates_new_wave_with_exact_interleaved_selection() {
    let source = Pcm {
        sample_rate: 4,
        channels: 2,
        samples: vec![1, -1, 2, -2, 3, -3, 4, -4],
    };
    let file = write_wave(&source).unwrap();
    let trimmed = trim_recording(file.path(), 0.25, 0.75).unwrap();
    assert_eq!(read_wave(trimmed.path()).unwrap().samples, [2, -2, 3, -3]);
    assert_eq!(recording_duration(trimmed.path()).unwrap(), 0.5);
    assert_eq!(read_wave(file.path()).unwrap(), source);
}

#[test]
fn waveform_uses_cross_channel_peak_without_overflow() {
    let pcm = Pcm {
        sample_rate: 4,
        channels: 2,
        samples: vec![1, -1, 12, -16384, 3, -32768, 4, -4],
    };
    let file = write_wave(&pcm).unwrap();
    let cancel = AtomicBool::new(false);
    assert_eq!(waveform_peaks(file.path(), &cancel, 2).unwrap(), [0.5, 1.0]);
    cancel.store(true, Ordering::Release);
    assert!(waveform_peaks(file.path(), &cancel, 2).unwrap().is_empty());
}

#[test]
fn truncated_wave_cannot_be_saved_as_a_partial_clip() {
    let pcm = Pcm {
        sample_rate: 4,
        channels: 1,
        samples: vec![1, 2, 3, 4],
    };
    let file = write_wave(&pcm).unwrap();
    file.as_file().set_len(50).unwrap();
    assert!(trim_recording(file.path(), 0.0, 1.0).is_err());
}

#[test]
fn cartesia_wave_size_placeholders_recover_only_complete_pcm_frames() {
    let source = Pcm {
        sample_rate: 24_000,
        channels: 1,
        samples: vec![17, -14],
    };
    let original = write_wave(&source).unwrap();
    let mut data = std::fs::read(original.path()).unwrap();
    data[4..8].copy_from_slice(&u32::MAX.to_le_bytes());
    data[40..44].copy_from_slice(&u32::MAX.to_le_bytes());
    data.push(6);
    assert_eq!(read_tts_wave(&data).unwrap(), source);
    assert!(parse_wave(&mut Cursor::new(&data), data.len() as u64, false).is_err());
}

#[test]
fn pcm16_clip_does_not_require_external_decoder() {
    let source = write_wave(&Pcm {
        sample_rate: 24_000,
        channels: 1,
        samples: vec![20],
    })
    .unwrap();
    let cancel = AtomicBool::new(false);
    let decoded = decode_clip(source.path(), &cancel).unwrap();
    assert_eq!(decoded.as_ref(), source.path());
}

#[test]
fn decoder_input_uses_a_fixed_demuxer_for_each_allowed_format() {
    assert_eq!(clip_format(Path::new("tone.OpUs")), Some("ogg"));
    assert_eq!(clip_format(Path::new("tone.WMA")), Some("asf"));
    assert_eq!(clip_format(Path::new("tone.m3u8")), None);
}

#[test]
#[ignore = "set WORDY_TEST_FFMPEG to an absolute path to run the codec matrix"]
fn original_clip_formats_decode_with_bundled_ffmpeg_command() {
    let ffmpeg = env::var_os("WORDY_TEST_FFMPEG").expect("provide the FFmpeg fixture path");
    let ffmpeg = Path::new(&ffmpeg);
    let directory = tempfile::tempdir().unwrap();
    let source = write_wave(&Pcm {
        sample_rate: 44_100,
        channels: 1,
        samples: (0..4_410)
            .map(|i| ((i / 50 % 2) * 16_000 - 8_000) as i16)
            .collect(),
    })
    .unwrap();
    let cancel = AtomicBool::new(false);
    for (extension, codec) in [
        ("wav", "pcm_f32le"),
        ("mp3", "libmp3lame"),
        ("ogg", "libvorbis"),
        ("flac", "flac"),
        ("m4a", "aac"),
        ("aac", "aac"),
        ("wma", "wmav2"),
        ("aiff", "pcm_s16be"),
        ("aif", "pcm_s16be"),
        ("opus", "libopus"),
    ] {
        let fixture = directory.path().join(format!("片段 tone.{extension}"));
        let result = Command::new(ffmpeg)
            .args(["-hide_banner", "-loglevel", "error", "-y", "-i"])
            .arg(source.path())
            .args(["-c:a", codec])
            .arg(&fixture)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{extension}: {}",
            String::from_utf8_lossy(&result.stderr)
        );
        let decoded = decode_clip_with(&fixture, &cancel, ffmpeg)
            .unwrap_or_else(|error| panic!("{extension}: {error}"));
        let pcm = read_wave(decoded.as_ref()).unwrap();
        assert_eq!((pcm.sample_rate, pcm.channels), (48_000, 2), "{extension}");
        assert!(pcm.samples.iter().any(|&sample| sample != 0), "{extension}");
    }
}
