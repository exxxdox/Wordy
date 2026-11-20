use super::*;

fn endpoint(id: &str, name: &str, is_default: bool) -> Endpoint {
    Endpoint {
        id: id.into(),
        name: name.into(),
        is_default,
        cable_priority: cable_priority(name, None, None),
    }
}

#[test]
fn absent_main_endpoint_keeps_only_independently_enabled_local_output() {
    let endpoints = [endpoint("speakers", "Speakers", true)];
    let settings = Settings {
        sidetone_enabled: true,
        ..Settings::default()
    };
    let selected = route(&settings, false, &endpoints);
    assert_eq!(selected.main, None);
    assert_eq!(selected.local, Some(&endpoints[0]));
    assert_eq!(route(&Settings::default(), false, &endpoints).local, None);
}

#[test]
fn preview_bypasses_switches_and_never_uses_cable() {
    let endpoints = [
        endpoint("cable", "CABLE Input (VB-Audio Virtual Cable)", true),
        endpoint("speakers", "Speakers", false),
    ];
    let settings = Settings {
        sidetone_enabled: true,
        ..Settings::default()
    };
    let selected = route(&settings, false, &endpoints);
    assert_eq!(selected.main, Some(&endpoints[0]));
    assert_eq!(selected.local, None);
    let mut settings = Settings {
        main_playback_enabled: false,
        audio_output_device_name: Some("Speakers".into()),
        ..Settings::default()
    };
    let preview = route(&settings, true, &endpoints);
    assert_eq!(preview.main, None);
    assert_eq!(preview.local, Some(&endpoints[1]));
    settings.audio_output_device_name = Some("Unplugged".into());
    assert_eq!(route(&settings, true, &endpoints).local, None);
}

#[test]
fn endpoint_matching_is_exact_then_unique_fragment() {
    let endpoints = [
        endpoint("a", "Speakers", false),
        endpoint("b", "Headset", true),
    ];
    assert_eq!(
        find_endpoint(&endpoints, " speakers ").map(|e| e.id.as_str()),
        Some("a")
    );
    assert_eq!(
        find_endpoint(&endpoints, "speak").map(|e| e.id.as_str()),
        Some("a")
    );
    assert_eq!(find_endpoint(&endpoints, "missing"), None);
    let ambiguous = [endpoint("a", "Mic A", false), endpoint("b", "Mic B", false)];
    assert_eq!(find_endpoint(&ambiguous, "mic"), None);
}

#[test]
fn saved_endpoint_id_wins_over_a_renamed_device() {
    let endpoints = [endpoint("new-id", "Renamed", false)];
    let current = AudioIdentity {
        name: "Old".into(),
        host_api_name: None,
        id: Some("new-id".into()),
    };
    assert_eq!(
        resolve_output(&endpoints, Some(&current), None).map(|e| e.name.as_str()),
        Some("Renamed")
    );
    let stale = AudioIdentity {
        name: "Old".into(),
        host_api_name: None,
        id: Some("gone".into()),
    };
    assert_eq!(resolve_output(&endpoints, Some(&stale), None), None);
}

#[test]
fn renamed_virtual_cable_is_still_the_main_route() {
    let endpoints = [Endpoint {
        cable_priority: cable_priority(
            "My output",
            Some("VB-Audio Virtual Cable"),
            Some("CABLE Input"),
        ),
        ..endpoint("cable", "My output", true)
    }];
    let selected = route(&Settings::default(), false, &endpoints);
    assert_eq!(selected.main.map(|e| e.id.as_str()), Some("cable"));
    assert_eq!(
        cable_endpoint_for(&endpoints, None).map(|e| e.id.as_str()),
        Some("cable")
    );
    assert_eq!(route(&Settings::default(), true, &endpoints).local, None);
}

#[test]
fn standard_cable_input_beats_the_multichannel_endpoint() {
    let endpoints = [
        endpoint("multi", "CABLE In 16ch (VB-Audio Virtual Cable)", true),
        endpoint("standard", "CABLE Input (VB-Audio Virtual Cable)", false),
    ];
    assert_eq!(cable_endpoint_for(&endpoints, None), Some(&endpoints[1]));
}

#[test]
fn an_unrelated_output_does_not_become_the_cable_through_its_name_or_legacy_setting() {
    let endpoints = [endpoint("speakers", "Speakers - cable input", true)];
    assert_eq!(cable_endpoint_for(&endpoints, Some("Speakers")), None);
    let settings = Settings {
        sidetone_enabled: true,
        virtual_output_device: Some("Speakers".into()),
        ..Settings::default()
    };
    assert_eq!(
        route(&settings, false, &endpoints).local,
        Some(&endpoints[0])
    );
    assert_eq!(
        cable_priority(
            "CABLE Input (VB-Audio Virtual Cable)",
            Some("Realtek Audio"),
            None
        ),
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
