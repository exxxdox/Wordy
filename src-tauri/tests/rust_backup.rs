#![cfg(windows)]

use std::cell::{Cell, RefCell};
use std::fs;
use std::io;
use std::io::Write;
use std::path::Path;
use wordy::backup::{export_backup, import_backup, prune_backups};
use wordy::backup_crypto::EncryptedWriter;
use wordy::config::{ConfigStore, Settings, Wheel, WindowPosition};
use wordy::credentials::{CredentialStore, Provider};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipWriter};

#[derive(Default)]
struct Vault {
    keys: RefCell<[Option<String>; 2]>,
    access: Cell<usize>,
    reject_once: Cell<Option<Provider>>,
    interrupt_once: Cell<Option<Provider>>,
}

impl Vault {
    fn index(provider: Provider) -> usize {
        match provider {
            Provider::Cartesia => 0,
            Provider::Volcengine => 1,
        }
    }
}

impl CredentialStore for Vault {
    fn load(&self, provider: Provider) -> io::Result<Option<String>> {
        self.access.set(self.access.get() + 1);
        Ok(self.keys.borrow()[Self::index(provider)].clone())
    }

    fn save(&self, provider: Provider, secret: &str) -> io::Result<()> {
        self.keys.borrow_mut()[Self::index(provider)] = Some(secret.to_owned());
        if self.interrupt_once.get() == Some(provider) {
            self.interrupt_once.set(None);
            panic!("simulated process interruption");
        }
        if self.reject_once.get() == Some(provider) {
            self.reject_once.set(None);
            return Err(io::Error::other("test credential fault after write"));
        }
        Ok(())
    }

    fn delete(&self, provider: Provider) -> io::Result<()> {
        self.keys.borrow_mut()[Self::index(provider)] = None;
        Ok(())
    }
}

fn create_backup(folder: &Path, path: &Path) {
    let mut config = ConfigStore::load(folder.join("config.toml")).unwrap();
    let audio = folder.join("audio_clips");
    fs::create_dir_all(&audio).unwrap();
    fs::write(audio.join("合成.wav"), b"clip fixture").unwrap();
    config
        .update(|settings| {
            settings.main_playback_enabled = false;
            settings.chat_wheel_slots[7] = "合成.wav".into();
            settings.chat_wheel_labels[7] = "测试音频".into();
            settings.chat_wheel_volumes[7] = 2.5;
        })
        .unwrap();
    let vault = Vault::default();
    vault.save(Provider::Cartesia, "fixture-one").unwrap();
    vault.save(Provider::Volcengine, "fixture-two").unwrap();
    export_backup(config.path(), config.settings(), &vault, path, " 测试 ").unwrap();
}

#[test]
fn imports_referenced_audio_by_new_name_and_keeps_secrets_out_of_toml() {
    let workspace = tempfile::tempdir().unwrap();
    let backup = workspace.path().join("fixture.wordybackup");
    create_backup(&workspace.path().join("old"), &backup);
    let new = workspace.path().join("new");
    let mut config = ConfigStore::load(new.join("config.toml")).unwrap();
    let vault = Vault::default();
    assert!(import_backup(&mut config, &vault, &backup, "测试").is_err());
    assert_eq!(vault.access.get(), 0);
    fs::create_dir_all(new.join("audio_clips")).unwrap();
    fs::write(new.join("audio_clips/old.wav"), b"previous fixture").unwrap();
    import_backup(&mut config, &vault, &backup, " 测试 ").unwrap();
    let settings = config.settings();
    assert!(!settings.main_playback_enabled);
    assert_eq!(settings.chat_wheel_volumes[7], 2.5);
    assert_eq!(settings.chat_wheel_labels[7], "测试音频");
    let slot = &settings.chat_wheel_slots[7];
    assert_ne!(slot, "合成.wav");
    assert_eq!(
        fs::read(new.join("audio_clips").join(slot)).unwrap(),
        b"clip fixture"
    );
    assert!(new.join("audio_clips/old.wav").is_file());
    assert_eq!(
        vault.load(Provider::Cartesia).unwrap().as_deref(),
        Some("fixture-one")
    );
    assert!(
        !fs::read_to_string(config.path())
            .unwrap()
            .contains("fixture-one")
    );
}

#[test]
fn rolls_back_both_credentials_and_new_audio_when_a_vault_write_partly_fails() {
    let workspace = tempfile::tempdir().unwrap();
    let backup = workspace.path().join("fixture.wordybackup");
    create_backup(&workspace.path().join("old"), &backup);
    let new = workspace.path().join("new");
    let mut config = ConfigStore::load(new.join("config.toml")).unwrap();
    config
        .update(|settings| settings.overlay_placeholder = "原有配置".into())
        .unwrap();
    let before = fs::read(config.path()).unwrap();
    let vault = Vault::default();
    vault.save(Provider::Cartesia, "original-one").unwrap();
    vault.reject_once.set(Some(Provider::Volcengine));
    assert!(import_backup(&mut config, &vault, &backup, " 测试 ").is_err());
    assert_eq!(fs::read(config.path()).unwrap(), before);
    assert_eq!(
        vault.load(Provider::Cartesia).unwrap().as_deref(),
        Some("original-one")
    );
    assert_eq!(vault.load(Provider::Volcengine).unwrap(), None);
    assert_eq!(fs::read_dir(new.join("audio_clips")).unwrap().count(), 0);
}

#[test]
fn interrupted_import_is_rolled_back_on_the_next_start() {
    let workspace = tempfile::tempdir().unwrap();
    let backup = workspace.path().join("fixture.wordybackup");
    create_backup(&workspace.path().join("old"), &backup);
    let new = workspace.path().join("new");
    let mut config = ConfigStore::load(new.join("config.toml")).unwrap();
    config
        .update(|settings| settings.name = "原有配置".into())
        .unwrap();
    let before = fs::read(config.path()).unwrap();
    let vault = Vault::default();
    vault.save(Provider::Cartesia, "old-key").unwrap();
    vault.interrupt_once.set(Some(Provider::Volcengine));
    let interrupted = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        import_backup(&mut config, &vault, &backup, " 测试 ").unwrap();
    }));
    assert!(interrupted.is_err());
    assert_eq!(
        vault.load(Provider::Cartesia).unwrap().as_deref(),
        Some("fixture-one")
    );
    assert_eq!(fs::read_dir(new.join("audio_clips")).unwrap().count(), 1);

    wordy::backup::recover_import(config.path(), &vault).unwrap();
    wordy::backup::recover_import(config.path(), &vault).unwrap();
    assert_eq!(fs::read(config.path()).unwrap(), before);
    assert_eq!(
        vault.load(Provider::Cartesia).unwrap().as_deref(),
        Some("old-key")
    );
    assert_eq!(vault.load(Provider::Volcengine).unwrap(), None);
    assert_eq!(fs::read_dir(new.join("audio_clips")).unwrap().count(), 0);
}

const MANIFEST: &[u8] = br#"{"format":"wordy-backup","version":1}"#;
const MANIFEST_V2: &[u8] = br#"{"format":"wordy-backup","version":2}"#;
const SECRETS: &[u8] = br#"{"cartesia_api_key":null,"volcengine_access_key":null}"#;

fn encrypted_zip(path: &Path, files: &[(&str, &[u8])]) {
    let writer = EncryptedWriter::new(fs::File::create(path).unwrap(), "pw").unwrap();
    let mut archive = ZipWriter::new_stream(writer);
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    for (name, data) in files {
        archive.start_file(*name, options).unwrap();
        archive.write_all(data).unwrap();
    }
    archive.finish().unwrap().into_inner().finish().unwrap();
}

fn import_entries(entries: &[(&str, &[u8])]) -> io::Result<()> {
    let workspace = tempfile::tempdir().unwrap();
    let backup = workspace.path().join("fixture.wordybackup");
    encrypted_zip(&backup, entries);
    let mut config = ConfigStore::load(workspace.path().join("config.toml")).unwrap();
    import_backup(&mut config, &Vault::default(), &backup, "pw")
}

#[test]
fn restores_named_audio_with_and_without_slots_and_preserves_local_unused_names() {
    let workspace = tempfile::tempdir().unwrap();
    let backup = workspace.path().join("catalog.wordybackup");
    let old = workspace.path().join("old");
    let mut source = ConfigStore::load(old.join("config.toml")).unwrap();
    fs::create_dir_all(old.join("audio_clips")).unwrap();
    let attached = "87139cf956c046e597ae6d367abbf279f.wav";
    let detached = "caed62e60d37441a9f08a96a10cc02db.wav";
    fs::write(old.join("audio_clips").join(attached), b"attached").unwrap();
    fs::write(old.join("audio_clips").join(detached), b"detached").unwrap();
    source
        .update(|settings| {
            settings.chat_wheel_slots[0] = attached.into();
            settings
                .audio_clip_names
                .insert(attached.into(), "开场".into());
            settings
                .audio_clip_names
                .insert(detached.into(), "结尾".into());
        })
        .unwrap();
    let vault = Vault::default();
    export_backup(source.path(), source.settings(), &vault, &backup, "pw").unwrap();

    let new = workspace.path().join("new");
    let mut target = ConfigStore::load(new.join("config.toml")).unwrap();
    fs::create_dir_all(new.join("audio_clips")).unwrap();
    let local = "f3e5c6af679945649305e251faa29539.wav";
    fs::write(new.join("audio_clips").join(local), b"local").unwrap();
    target
        .update(|settings| {
            settings
                .audio_clip_names
                .insert(local.into(), "本地音频".into());
        })
        .unwrap();
    import_backup(&mut target, &vault, &backup, "pw").unwrap();

    let settings = target.settings();
    assert_eq!(settings.audio_clip_names.len(), 3);
    assert_eq!(
        settings.audio_clip_names[&settings.chat_wheel_slots[0]],
        "开场"
    );
    assert_eq!(settings.audio_clip_names[local], "本地音频");
    let (detached_id, _) = settings
        .audio_clip_names
        .iter()
        .find(|(_, name)| name.as_str() == "结尾")
        .unwrap();
    assert_eq!(
        fs::read(new.join("audio_clips").join(detached_id)).unwrap(),
        b"detached"
    );
    assert!(!settings.referenced_audio().any(|id| id == detached_id));
}

#[test]
fn rejects_invalid_or_unmatched_audio_catalog_entries() {
    let base = Settings::default().to_toml().unwrap();
    for entry in [
        r#""../bad.wav" = "越界""#,
        r#""test.wav" = 1"#,
        r#""test.wav" = "缺少文件""#,
    ] {
        let config = format!("{base}\n[audio_clip_names]\n{entry}\n");
        assert!(
            import_entries(&[
                ("manifest.json", MANIFEST_V2),
                ("config.toml", config.as_bytes()),
                ("secrets.json", SECRETS),
            ])
            .is_err()
        );
    }
}

#[test]
fn import_deletes_audio_that_only_the_replaced_config_referenced() {
    let workspace = tempfile::tempdir().unwrap();
    let backup = workspace.path().join("fixture.wordybackup");
    create_backup(&workspace.path().join("old"), &backup);
    let new = workspace.path().join("new");
    let mut config = ConfigStore::load(new.join("config.toml")).unwrap();
    fs::create_dir_all(new.join("audio_clips")).unwrap();
    fs::write(new.join("audio_clips/old.wav"), b"old fixture").unwrap();
    config
        .update(|settings| settings.chat_wheel_slots[0] = "old.wav".into())
        .unwrap();
    import_backup(&mut config, &Vault::default(), &backup, " 测试 ").unwrap();
    assert!(!new.join("audio_clips/old.wav").exists());
    let slot = &config.settings().chat_wheel_slots[7];
    assert!(new.join("audio_clips").join(slot).is_file());
}

#[test]
fn accepts_a_backup_from_before_timed_backup_settings() {
    let config = Settings::default().to_toml().unwrap();
    let legacy = config
        .lines()
        .filter(|line| !line.starts_with("auto_backup_"))
        .collect::<Vec<_>>()
        .join("\n");
    let result = import_entries(&[
        ("manifest.json", MANIFEST),
        ("config.toml", legacy.as_bytes()),
        ("secrets.json", SECRETS),
    ]);
    assert!(result.is_ok());
}

#[test]
fn accepts_an_early_v1_backup_with_retired_wheel_style() {
    let mut config = toml::Table::try_from(Settings::default()).unwrap();
    config.retain(|key, _| key != "chat_wheel_text_modes" && !key.starts_with("auto_backup_"));
    config.insert(
        "chat_wheel_style".into(),
        toml::Value::String("competitive".into()),
    );
    let legacy = toml::to_string(&config).unwrap();
    import_entries(&[
        ("manifest.json", MANIFEST),
        ("config.toml", legacy.as_bytes()),
        ("secrets.json", SECRETS),
    ])
    .unwrap();
}

#[test]
fn pruning_keeps_newest_automatic_backups_and_leaves_manual_files() {
    let directory = tempfile::tempdir().unwrap();
    let config_path = directory.path().join("config.toml");
    let automatic = wordy::backup::auto_backup_directory(&config_path);
    fs::create_dir_all(&automatic).unwrap();
    let legacy = automatic
        .parent()
        .unwrap()
        .join("auto-20250101-000000.wordybackup");
    fs::write(&legacy, b"manual").unwrap();
    for name in [
        "auto-20260101-000000.wordybackup",
        "auto-20260102-000000.wordybackup",
        "auto-20260103-000000.wordybackup",
        "manual.wordybackup",
        "note.txt",
    ] {
        fs::write(automatic.join(name), b"x").unwrap();
    }
    prune_backups(&config_path, 2).unwrap();
    assert!(legacy.exists());
    assert!(wordy::backup::validate_manual_destination(&config_path, &legacy).is_ok());
    assert!(
        wordy::backup::validate_manual_destination(
            &config_path,
            &automatic.join("manual.wordybackup")
        )
        .is_err()
    );
    assert!(!automatic.join("auto-20260101-000000.wordybackup").exists());
    assert!(automatic.join("auto-20260102-000000.wordybackup").exists());
    assert!(automatic.join("auto-20260103-000000.wordybackup").exists());
    assert!(automatic.join("manual.wordybackup").exists());
    assert!(automatic.join("note.txt").exists());
}

#[test]
fn rejects_audio_paths_outside_the_managed_folder() {
    let config = Settings::default().to_toml().unwrap();
    let result = import_entries(&[
        ("manifest.json", MANIFEST),
        ("config.toml", config.as_bytes()),
        ("secrets.json", SECRETS),
        ("audio_clips/../escape.wav", b"x"),
    ]);
    assert!(result.is_err());
}

#[test]
fn accepts_a_wheel_kept_without_a_hotkey() {
    let mut settings = Settings::default();
    let mut wheel = Wheel::new("");
    wheel.labels[0] = "kept".into();
    settings.additional_chat_wheels.push(wheel);
    let config = settings.to_toml().unwrap();
    let result = import_entries(&[
        ("manifest.json", MANIFEST),
        ("config.toml", config.as_bytes()),
        ("secrets.json", SECRETS),
    ]);
    assert!(result.is_ok());
}

#[test]
fn accepts_a_backup_that_remembers_the_settings_window_position() {
    let mut settings = Settings::default();
    settings.settings_window_position = Some(WindowPosition { x: 120, y: 80 });
    let config = settings.to_toml().unwrap();
    let result = import_entries(&[
        ("manifest.json", MANIFEST),
        ("config.toml", config.as_bytes()),
        ("secrets.json", SECRETS),
    ]);
    assert!(result.is_ok());
}

#[test]
fn accepts_a_backup_with_the_retired_rolling_target() {
    let config = Settings::default().to_toml().unwrap();
    let legacy = format!("rolling_buffer_target = [1, 2]\n{config}");
    let result = import_entries(&[
        ("manifest.json", MANIFEST),
        ("config.toml", legacy.as_bytes()),
        ("secrets.json", SECRETS),
    ]);
    assert!(result.is_ok());
}

#[test]
fn rejects_duplicate_audio_names_and_unknown_config_fields() {
    let config = Settings::default().to_toml().unwrap();
    let duplicate = import_entries(&[
        ("manifest.json", MANIFEST),
        ("config.toml", config.as_bytes()),
        ("secrets.json", SECRETS),
        ("audio_clips/A.wav", b"a"),
        ("audio_clips/a.wav", b"b"),
    ]);
    assert!(duplicate.is_err());
    let unknown = format!("bogus = 1\n{config}");
    let rejected = import_entries(&[
        ("manifest.json", MANIFEST),
        ("config.toml", unknown.as_bytes()),
        ("secrets.json", SECRETS),
    ]);
    assert!(rejected.is_err());
    let outside = import_entries(&[
        ("manifest.json", MANIFEST),
        ("config.toml", config.as_bytes()),
        ("secrets.json", SECRETS),
        ("other.txt", b"x"),
    ]);
    assert!(outside.is_err());
}
