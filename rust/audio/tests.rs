use super::*;

fn endpoint(id: &str, name: &str, is_default: bool) -> Endpoint {
    Endpoint {
        id: id.into(),
        name: name.into(),
        is_default,
    }
}

#[test]
fn absent_main_endpoint_keeps_only_independently_enabled_local_output() {
    let endpoints = [endpoint("speakers", "Speakers", true)];
    let selected = route(true, true, false, None, &endpoints, None);
    assert_eq!(selected.main, None);
    assert_eq!(selected.local, Some(&endpoints[0]));
    assert_eq!(
        route(true, false, false, None, &endpoints, None).local,
        None
    );
}

#[test]
fn preview_bypasses_switches_and_never_uses_cable() {
    let endpoints = [
        endpoint("cable", "CABLE Input (VB-Audio Virtual Cable)", true),
        endpoint("speakers", "Speakers", false),
    ];
    let selected = route(true, true, false, None, &endpoints, None);
    assert_eq!(selected.main, Some(&endpoints[0]));
    assert_eq!(selected.local, None);
    let preview = route(false, false, true, Some("Speakers"), &endpoints, None);
    assert_eq!(preview.main, None);
    assert_eq!(preview.local, Some(&endpoints[1]));
    assert_eq!(
        route(false, false, true, Some("Unplugged"), &endpoints, None).local,
        None
    );
}

#[test]
fn gain_saturates_without_distorting_the_input_buffer() {
    let samples = [20_000, -20_000, 125, -125];
    assert_eq!(
        apply_gain(&samples, 2.0).unwrap(),
        [32_767, -32_768, 250, -250]
    );
    assert_eq!(apply_gain(&samples, 0.0).unwrap(), [0; 4]);
    assert!(apply_gain(&samples, f64::NAN).is_err());
    assert_eq!(samples[0], 20_000);
}

#[test]
fn normalizing_mono_resamples_and_duplicates_whole_frames() {
    let source = Pcm {
        sample_rate: 24_000,
        channels: 1,
        samples: vec![0, 900],
    };
    let normalized = source.into_recording_format().unwrap();
    assert_eq!(normalized.sample_rate, 48_000);
    assert_eq!(normalized.channels, 2);
    assert_eq!(normalized.samples, [0, 0, 300, 300, 600, 600, 900, 900]);
}

#[test]
fn rolling_buffer_removes_old_frames_even_within_a_block() {
    let mut buffer = CaptureBuffer::new(2, Some(3));
    buffer.push(&[1, -1, 2, -2, 3, -3]);
    buffer.push(&[4, -4, 5, -5]);
    assert_eq!(buffer.into_samples(), [3, -3, 4, -4, 5, -5]);
}
