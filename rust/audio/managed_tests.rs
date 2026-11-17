use super::*;
use crate::audio::{Pcm, read_wave, write_wave};

#[test]
fn installing_clip_copies_source_and_commits_slot_as_one_change() {
    let directory = tempfile::tempdir().unwrap();
    let mut store = ConfigStore::load(directory.path().join("config.toml")).unwrap();
    let source = write_wave(&Pcm {
        sample_rate: 48_000,
        channels: 1,
        samples: vec![10, 20],
    })
    .unwrap();
    store
        .update_wheel(0, |wheel| wheel.texts[2] = "old text".into())
        .unwrap();
    let clip = stage_clip(source.path(), store.path(), &AtomicBool::new(false)).unwrap();
    let saved = commit_slot_clip(&mut store, 0, 2, clip, "sample", Some(2.0)).unwrap();
    source.as_file().set_len(0).unwrap();
    assert_eq!(
        read_wave(&resolve_clip(store.path(), &saved).unwrap())
            .unwrap()
            .samples,
        [10, 20]
    );
    let restored = ConfigStore::load(store.path()).unwrap();
    let wheel = restored.settings().wheel(0).unwrap();
    assert_eq!(wheel.slots[2], saved);
    assert_eq!(wheel.labels[2], "sample");
    assert!(wheel.texts[2].is_empty());
    assert_eq!(wheel.volumes[2], 2.0);
}

#[test]
fn failed_config_save_removes_staged_copy_and_retains_original_source() {
    let directory = tempfile::tempdir().unwrap();
    let mut store = ConfigStore::load(directory.path().join("config.toml")).unwrap();
    let source = write_wave(&Pcm {
        sample_rate: 24_000,
        channels: 1,
        samples: vec![10, 20],
    })
    .unwrap();
    let clip = stage_clip(source.path(), store.path(), &AtomicBool::new(false)).unwrap();
    let path = clip.path().to_owned();
    fs::create_dir(store.path()).unwrap();
    assert!(commit_slot_clip(&mut store, 0, 2, clip, "sample", None).is_err());
    assert!(!path.exists());
    assert!(source.path().is_file());
    assert!(store.settings().wheel(0).unwrap().slots[2].is_empty());
}

#[test]
fn removing_clip_keeps_other_wheel_references() {
    let directory = tempfile::tempdir().unwrap();
    let mut store = ConfigStore::load(directory.path().join("config.toml")).unwrap();
    let source = write_wave(&Pcm {
        sample_rate: 24_000,
        channels: 1,
        samples: vec![10],
    })
    .unwrap();
    let staged = stage_clip(source.path(), store.path(), &AtomicBool::new(false)).unwrap();
    let name = commit_slot_clip(&mut store, 0, 0, staged, "one", None).unwrap();
    store.add_wheel("f7").unwrap();
    store
        .update_wheel(1, |wheel| wheel.slots[0].clone_from(&name))
        .unwrap();
    store
        .update_wheel(0, |wheel| wheel.slots[0].clear())
        .unwrap();
    assert!(!remove_unreferenced_clip(&store, &name).unwrap());
    assert!(resolve_clip(store.path(), &name).unwrap().is_file());
    store
        .update_wheel(1, |wheel| wheel.slots[0].clear())
        .unwrap();
    assert!(remove_unreferenced_clip(&store, &name).unwrap());
    assert!(resolve_clip(store.path(), &name).is_err());
    assert!(resolve_clip(store.path(), "../other.wav").is_err());
}
