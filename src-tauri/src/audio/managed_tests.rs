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
        .update_wheel(0, |wheel| {
            wheel.texts[2] = "old text".into();
            wheel.text_modes[2] = true;
        })
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
    // 导入音频不删除原文字，只把槽位切到音频模式。
    assert_eq!(wheel.texts[2], "old text");
    assert!(!wheel.text_modes[2]);
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
fn lists_and_removes_only_unreferenced_clips() {
    let directory = tempfile::tempdir().unwrap();
    let mut store = ConfigStore::load(directory.path().join("config.toml")).unwrap();
    let source = write_wave(&Pcm {
        sample_rate: 24_000,
        channels: 1,
        samples: vec![10],
    })
    .unwrap();
    let staged = stage_clip(source.path(), store.path(), &AtomicBool::new(false)).unwrap();
    let kept = commit_slot_clip(&mut store, 0, 0, staged, "kept", None).unwrap();
    let folder = clips_directory(store.path()).unwrap();
    fs::write(folder.join("orphan.wav"), b"x").unwrap();

    assert_eq!(
        unused_clips(&store).unwrap(),
        vec![folder.join("orphan.wav")]
    );
    assert_eq!(remove_unused_clips(&mut store).unwrap(), 1);
    assert!(resolve_clip(store.path(), "orphan.wav").is_err());
    assert!(resolve_clip(store.path(), &kept).is_ok());
    assert!(unused_clips(&store).unwrap().is_empty());
}

#[test]
fn organizing_reuses_clips_and_preserves_the_text_slot_and_last_audio_name() {
    let directory = tempfile::tempdir().unwrap();
    let mut store = ConfigStore::load(directory.path().join("config.toml")).unwrap();
    let source = write_wave(&Pcm {
        sample_rate: 24_000,
        channels: 1,
        samples: vec![10],
    })
    .unwrap();
    let staged = stage_clip(source.path(), store.path(), &AtomicBool::new(false)).unwrap();
    let id = commit_slot_clip(&mut store, 0, 0, staged, "原音频", None).unwrap();
    set_slot_label(&mut store, 0, 0, "最后的名称").unwrap();
    set_slot_label(&mut store, 0, 0, "").unwrap();
    store
        .update(|settings| {
            settings
                .additional_chat_wheels
                .push(crate::config::Wheel::new("f7"));
        })
        .unwrap();
    store
        .update_wheel(1, |wheel| {
            wheel.texts[0] = "原文字".into();
            wheel.labels[0] = "文字标题".into();
            wheel.text_modes[0] = false;
            wheel.volumes[0] = 0.7;
        })
        .unwrap();
    let staged = stage_clip(source.path(), store.path(), &AtomicBool::new(false)).unwrap();
    let replacement = commit_slot_clip(&mut store, 0, 0, staged, "新音频", None).unwrap();
    assert_ne!(id, replacement);
    let mut store = ConfigStore::load(store.path()).unwrap();
    assert_eq!(store.settings().audio_clip_names[&id], "最后的名称");
    assert!(
        list_clips(&store)
            .unwrap()
            .iter()
            .any(|clip| { clip.id == id && clip.name == "最后的名称" })
    );
    assert!(assign_clip(&mut store, 1, SLOT_COUNT, &id).is_err());
    assert!(assign_clip(&mut store, 1, 0, "../other.wav").is_err());
    assign_clip(&mut store, 1, 0, &id).unwrap();
    let wheel = store.settings().wheel(1).unwrap();
    assert_eq!(wheel.slots[0], id);
    assert_eq!(wheel.texts[0], "原文字");
    assert_eq!(wheel.labels[0], "文字标题");
    assert!(wheel.text_modes[0]);
    assert_eq!(wheel.volumes[0], 0.7);
    set_slot_label(&mut store, 1, 0, "新文字标题").unwrap();
    assert_eq!(store.settings().audio_clip_names[&id], "最后的名称");
    assert_eq!(remove_unused_clips(&mut store).unwrap(), 0);
    assign_clip(&mut store, 0, 0, &id).unwrap();
    assert_eq!(store.settings().wheel(0).unwrap().labels[0], "最后的名称");
    assert_eq!(remove_unused_clips(&mut store).unwrap(), 1);
    assert!(!store.settings().audio_clip_names.contains_key(&replacement));
    assert!(resolve_clip(store.path(), &id).unwrap().is_file());
}

#[test]
fn older_slot_labels_supply_names_when_no_catalog_was_saved() {
    let directory = tempfile::tempdir().unwrap();
    let mut store = ConfigStore::load(directory.path().join("config.toml")).unwrap();
    store
        .update_wheel(0, |wheel| {
            wheel.slots[0] = "old.wav".into();
            wheel.labels[0] = "旧标题".into();
            wheel.texts[0] = "文字".into();
            wheel.text_modes[0] = true;
        })
        .unwrap();
    let folder = clips_directory(store.path()).unwrap();
    fs::create_dir(&folder).unwrap();
    fs::write(folder.join("old.wav"), b"x").unwrap();
    fs::write(folder.join("orphan.wav"), b"x").unwrap();
    let store = ConfigStore::load(store.path()).unwrap();
    assert_eq!(store.settings().audio_clip_names["old.wav"], "旧标题");
    assert!(
        list_clips(&store)
            .unwrap()
            .iter()
            .any(|clip| { clip.id == "orphan.wav" && clip.name == "orphan.wav" })
    );
}
