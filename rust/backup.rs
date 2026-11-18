use std::collections::{BTreeMap, BTreeSet, HashSet};
use std::fs::{self, File};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use tempfile::NamedTempFile;
use toml::{Table, Value};
use zip::write::SimpleFileOptions;
use zip::{CompressionMethod, ZipArchive, ZipWriter};

use crate::audio;
use crate::backup_crypto::{EncryptedWriter, decrypt_backup};
use crate::config::{ConfigStore, Settings, Wheel};
use crate::credentials::{CredentialStore, Provider};
use crate::hotkey::parse_hotkey;

const MAX_SIZE: u64 = 2 * 1024_u64.pow(3);
const MAX_METADATA: u64 = 4 * 1024_u64.pow(2);
const MAX_ENTRIES: usize = 10_000;
const REQUIRED: [&str; 3] = ["manifest.json", "config.toml", "secrets.json"];
const MANIFEST: &[u8] = br#"{"format":"wordy-backup","version":1}"#;

#[derive(Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct Secrets {
    cartesia_api_key: Option<String>,
    volcengine_access_key: Option<String>,
}

impl Secrets {
    fn read(credentials: &dyn CredentialStore) -> io::Result<Self> {
        Ok(Self {
            cartesia_api_key: credentials.load(Provider::Cartesia)?,
            volcengine_access_key: credentials.load(Provider::Volcengine)?,
        })
    }

    fn get(&self, provider: Provider) -> Option<&str> {
        match provider {
            Provider::Cartesia => self.cartesia_api_key.as_deref(),
            Provider::Volcengine => self.volcengine_access_key.as_deref(),
        }
    }
}

pub fn export_backup(
    config: &mut ConfigStore,
    credentials: &dyn CredentialStore,
    destination: &Path,
    password: &str,
) -> io::Result<()> {
    validate_password(password)?;
    let folder = parent(destination);
    let target = resolve_path(destination)?;
    let config_file = resolve_path(config.path())?;
    let audio_dir = resolve_path(&audio::clips_directory(config.path())?)?;
    if same_path(&target, &config_file) || target.ancestors().any(|p| same_path(p, &audio_dir)) {
        return Err(invalid("备份不能覆盖配置文件或保存到音频托管目录"));
    }
    config.flush()?;
    let secrets = serde_json::to_vec(&Secrets::read(credentials)?).map_err(io::Error::other)?;
    let config_text = config.settings().to_toml().map_err(io::Error::other)?;
    let names: BTreeSet<_> = config.settings().referenced_audio().collect();
    let mut seen = HashSet::new();
    if names.len() + REQUIRED.len() > MAX_ENTRIES
        || names
            .iter()
            .any(|name| !valid_filename(name) || !seen.insert(name.to_lowercase()))
        || [config_text.len(), secrets.len()]
            .into_iter()
            .any(|n| n as u64 > MAX_METADATA)
    {
        return Err(invalid("备份超过限制或包含无效文件名"));
    }
    fs::create_dir_all(folder)?;
    let mut staging = NamedTempFile::new_in(folder)?;
    let encrypted = EncryptedWriter::new(staging.as_file_mut(), password)?;
    let mut archive = ZipWriter::new_stream(encrypted);
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    for (name, content) in REQUIRED
        .into_iter()
        .zip([MANIFEST, config_text.as_bytes(), &secrets])
    {
        archive
            .start_file(name, options)
            .map_err(io::Error::other)?;
        archive.write_all(content)?;
    }
    let mut remaining = MAX_SIZE - (MANIFEST.len() + config_text.len() + secrets.len()) as u64;
    for name in names {
        let path = audio_dir.join(name);
        if !same_path(parent(&path.canonicalize()?), &audio_dir) {
            return Err(invalid("音频文件不在托管目录中"));
        }
        let input = File::open(path)?;
        if input.metadata()?.len() > remaining {
            return Err(invalid("备份过大"));
        }
        archive
            .start_file(format!("audio_clips/{name}"), options)
            .map_err(io::Error::other)?;
        let written = io::copy(&mut input.take(remaining + 1), &mut archive)?;
        remaining = remaining
            .checked_sub(written)
            .ok_or_else(|| invalid("备份过大"))?;
    }
    archive
        .finish()
        .map_err(io::Error::other)?
        .into_inner()
        .finish()?;
    staging.as_file().sync_all()?;
    staging.persist(destination).map_err(|error| error.error)?;
    Ok(())
}

pub fn import_backup(
    config: &mut ConfigStore,
    credentials: &dyn CredentialStore,
    source: &Path,
    password: &str,
) -> io::Result<()> {
    validate_password(password)?;
    let folder = parent(config.path());
    fs::create_dir_all(folder)?;
    let mut plaintext = decrypt_backup(source, password, folder)?;
    let entry_count = zip_entry_count(&mut plaintext)?;
    let mut archive = ZipArchive::new(plaintext).map_err(io::Error::other)?;
    let names = validate_archive(&mut archive, entry_count)?;
    let manifest: serde_json::Value =
        serde_json::from_slice(&read_metadata(&mut archive, REQUIRED[0])?)
            .map_err(io::Error::other)?;
    if manifest != serde_json::json!({"format": "wordy-backup", "version": 1}) {
        return Err(invalid("不支持此备份版本"));
    }
    let text =
        String::from_utf8(read_metadata(&mut archive, REQUIRED[1])?).map_err(io::Error::other)?;
    let table = text.parse::<Table>().map_err(io::Error::other)?;
    let mut restored = validate_settings(&table)?;
    let key_data = read_metadata(&mut archive, REQUIRED[2])?;
    let key_table: serde_json::Value =
        serde_json::from_slice(&key_data).map_err(io::Error::other)?;
    for provider in [Provider::Cartesia, Provider::Volcengine] {
        match key_table.get(provider.username()) {
            Some(serde_json::Value::Null) => (),
            Some(serde_json::Value::String(value))
                if !value.trim().is_empty() && value.chars().count() <= 65_536 => {}
            _ => return Err(invalid("备份密钥格式无效")),
        }
    }
    let imported: Secrets = serde_json::from_slice(&key_data).map_err(io::Error::other)?;
    if restored
        .referenced_audio()
        .map(str::to_owned)
        .collect::<BTreeSet<_>>()
        != names
    {
        return Err(invalid("备份音频与配置不匹配"));
    }

    let directory = audio::clips_directory(config.path())?;
    let previous = Secrets::read(credentials)?;
    let mut changed = Vec::new();
    let mut created = Vec::new();
    let result = (|| {
        fs::create_dir_all(&directory)?;
        let mut replacements = BTreeMap::new();
        for name in names {
            let mut entry = archive
                .by_name(&format!("audio_clips/{name}"))
                .map_err(io::Error::other)?;
            let suffix = format!(
                ".{}",
                Path::new(&name)
                    .extension()
                    .and_then(|x| x.to_str())
                    .unwrap_or_default()
                    .to_lowercase()
            );
            let id = uuid::Uuid::new_v4().simple().to_string();
            let replacement = format!("{id}{suffix}");
            let path = directory.join(&replacement);
            let mut output = File::create_new(&path)?;
            created.push(path);
            let size = entry.size();
            if io::copy(&mut (&mut entry).take(size + 1), &mut output)? != size {
                return Err(invalid("备份音频大小无效"));
            }
            output.sync_all()?;
            replacements.insert(name, replacement);
        }
        for slot in restored.chat_wheel_slots.iter_mut().chain(
            restored
                .additional_chat_wheels
                .iter_mut()
                .flat_map(|wheel| &mut wheel.slots),
        ) {
            if let Some(replacement) = replacements.get(slot) {
                slot.clone_from(replacement);
            }
        }
        for provider in [Provider::Cartesia, Provider::Volcengine] {
            let secret = imported.get(provider);
            if secret != previous.get(provider) {
                // A backend can report an error after partially updating a credential.
                changed.push(provider);
                set_secret(credentials, provider, secret)?;
            }
            restored.set_credential_status(provider, secret.is_some());
        }
        config.replace(restored)
    })();
    if let Err(error) = result {
        let mut rollback_error = None;
        for provider in changed.into_iter().rev() {
            if let Err(error) = set_secret(credentials, provider, previous.get(provider)) {
                rollback_error.get_or_insert(error);
            }
        }
        for path in created {
            if let Err(error) = fs::remove_file(path) {
                rollback_error.get_or_insert(error);
            }
        }
        return match rollback_error {
            Some(rollback) => Err(io::Error::other(format!(
                "导入失败，且未能完整回滚: {error} ({rollback})"
            ))),
            None => Err(error),
        };
    }
    Ok(())
}

fn set_secret(
    credentials: &dyn CredentialStore,
    provider: Provider,
    secret: Option<&str>,
) -> io::Result<()> {
    match secret {
        Some(value) => credentials.save(provider, value),
        None => credentials.delete(provider),
    }
}

fn validate_password(password: &str) -> io::Result<()> {
    if password.trim().is_empty() {
        Err(invalid("备份密码不能为空"))
    } else {
        Ok(())
    }
}

fn validate_archive(
    archive: &mut ZipArchive<File>,
    entry_count: usize,
) -> io::Result<BTreeSet<String>> {
    if archive.len() != entry_count || archive.offset() != 0 {
        return Err(invalid("备份包含重复文件或额外数据"));
    }
    let mut seen = HashSet::new();
    let mut names = BTreeSet::new();
    let mut remaining = MAX_SIZE;
    for index in 0..archive.len() {
        let entry = archive.by_index(index).map_err(io::Error::other)?;
        let name = entry.name();
        remaining = remaining
            .checked_sub(entry.size())
            .ok_or_else(|| invalid("备份过大"))?;
        if !entry.is_file() || entry.is_symlink() || !seen.insert(name.to_lowercase()) {
            return Err(invalid("备份包含重复或无效文件"));
        }
        if REQUIRED.contains(&name) {
            if entry.size() > MAX_METADATA {
                return Err(invalid("备份配置过大"));
            }
        } else {
            let filename = name
                .strip_prefix("audio_clips/")
                .filter(|name| valid_filename(name))
                .ok_or_else(|| invalid("备份包含无效音频路径"))?;
            names.insert(filename.to_owned());
        }
    }
    Ok(names)
}

fn read_metadata(archive: &mut ZipArchive<File>, name: &str) -> io::Result<Vec<u8>> {
    let mut bytes = Vec::new();
    archive
        .by_name(name)
        .map_err(io::Error::other)?
        .take(MAX_METADATA + 1)
        .read_to_end(&mut bytes)?;
    if bytes.len() as u64 > MAX_METADATA {
        return Err(invalid("备份配置过大"));
    }
    Ok(bytes)
}

fn zip_entry_count(file: &mut File) -> io::Result<usize> {
    // ZipArchive indexes names, so verify its entry count against the original EOCD.
    // Wordy archives never need a ZIP64 entry count, even if offsets use ZIP64.
    let tail_size = file.metadata()?.len().min(22 + u16::MAX as u64) as usize;
    file.seek(SeekFrom::End(-(tail_size as i64)))?;
    let mut tail = vec![0; tail_size];
    file.read_exact(&mut tail)?;
    file.rewind()?;
    for offset in (0..=tail_size.saturating_sub(22)).rev() {
        let eocd = &tail[offset..];
        if eocd.len() < 22 || !eocd.starts_with(b"PK\x05\x06") {
            continue;
        }
        let read_u16 = |index| u16::from_le_bytes([eocd[index], eocd[index + 1]]) as usize;
        if read_u16(20) + 22 != eocd.len() {
            continue;
        }
        let count = read_u16(10);
        if read_u16(4) == 0 && read_u16(6) == 0 && read_u16(8) == count && count <= MAX_ENTRIES {
            return Ok(count);
        }
        break;
    }
    Err(invalid("备份文件数量或目录无效"))
}

fn validate_settings(table: &Table) -> io::Result<Settings> {
    let mut template = Value::try_from(Settings::default()).map_err(io::Error::other)?;
    let default_fields = template
        .as_table_mut()
        .ok_or_else(|| invalid("配置模板无效"))?;
    if default_fields
        .keys()
        .any(|key| key != "main_playback_enabled" && !table.contains_key(key))
    {
        return Err(invalid("备份配置不完整"));
    }
    let optional_strings = [
        "audio_output_device_name",
        "mic_input_device",
        "virtual_output_device",
    ];
    for key in optional_strings {
        default_fields.insert(key.into(), Value::String(String::new()));
    }
    default_fields.insert(
        "window_position".into(),
        "x=0\ny=0".parse::<Value>().map_err(io::Error::other)?,
    );
    default_fields.insert(
        "audio_output_device".into(),
        "name=''\nhost_api_name=''"
            .parse::<Value>()
            .map_err(io::Error::other)?,
    );
    default_fields.insert(
        "additional_chat_wheels".into(),
        Value::Array(vec![
            Value::try_from(Wheel::new("f8")).map_err(io::Error::other)?,
        ]),
    );
    if let Some(cartesia) = default_fields
        .get_mut("tts_providers")
        .and_then(Value::as_table_mut)
        .and_then(|providers| providers.get_mut("Cartesia"))
        .and_then(Value::as_table_mut)
    {
        for key in ["voice_id", "voice_name"] {
            cartesia.insert(key.into(), Value::String(String::new()));
        }
    }
    validate_value(&Value::Table(table.clone()), &template)?;
    let settings = Settings::from_table(table);
    let mut bindings = HashSet::new();
    for binding in [
        &settings.hotkey,
        &settings.rolling_buffer_hotkey,
        &settings.chat_wheel_hotkey,
    ]
    .into_iter()
    .chain(
        settings
            .additional_chat_wheels
            .iter()
            .map(|wheel| &wheel.hotkey),
    ) {
        let binding = parse_hotkey(binding).map_err(io::Error::other)?;
        if !bindings.insert(binding) {
            return Err(invalid("备份中的快捷键互相冲突"));
        }
    }
    Ok(settings)
}

fn validate_value(value: &Value, template: &Value) -> io::Result<()> {
    match (value, template) {
        (Value::Boolean(_), Value::Boolean(_)) | (Value::String(_), Value::String(_)) => Ok(()),
        (Value::Integer(_), Value::Integer(_) | Value::Float(_)) => Ok(()),
        (Value::Float(value), Value::Integer(_) | Value::Float(_)) if value.is_finite() => Ok(()),
        (Value::Array(items), Value::Array(template)) => {
            let voice = Value::Table(Table::from_iter([
                ("id".into(), Value::String(String::new())),
                ("name".into(), Value::String(String::new())),
            ]));
            let expected = template.first().unwrap_or(&voice);
            items
                .iter()
                .try_for_each(|item| validate_value(item, expected))
        }
        (Value::Table(items), Value::Table(template)) => {
            items.iter().try_for_each(|(key, item)| {
                let expected = template
                    .get(key)
                    .ok_or_else(|| invalid("备份包含未知配置字段"))?;
                validate_value(item, expected)
            })
        }
        _ => Err(invalid("备份配置字段类型无效")),
    }
}

fn valid_filename(name: &str) -> bool {
    let first = name
        .split('.')
        .next()
        .unwrap_or_default()
        .to_ascii_uppercase();
    let reserved = ["CON", "PRN", "AUX", "NUL"].contains(&first.as_str())
        || matches!(
            first.as_bytes(),
            [b'C', b'O', b'M', b'1'..=b'9'] | [b'L', b'P', b'T', b'1'..=b'9']
        );
    !reserved
        && !name.contains(['/', '\\', ':', '<', '>', '"', '|', '?', '*'])
        && !name.chars().any(|c| c < ' ')
        && !name.ends_with(['.', ' '])
        && audio::audio_extension(Path::new(name)).is_some()
}

fn same_path(left: &Path, right: &Path) -> bool {
    left.to_string_lossy().to_lowercase() == right.to_string_lossy().to_lowercase()
}

fn resolve_path(path: &Path) -> io::Result<PathBuf> {
    let path = std::path::absolute(path)?;
    match path.canonicalize() {
        Ok(resolved) => Ok(resolved),
        Err(error) if error.kind() == io::ErrorKind::NotFound => {
            let name = path.file_name().ok_or(error)?;
            Ok(resolve_path(parent(&path))?.join(name))
        }
        Err(error) => Err(error),
    }
}

fn parent(path: &Path) -> &Path {
    path.parent()
        .filter(|path| !path.as_os_str().is_empty())
        .unwrap_or(Path::new("."))
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
