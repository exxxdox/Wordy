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
use crate::config::{ConfigStore, Settings};
use crate::credentials::{CredentialStore, Provider};
use crate::hotkey::parse_hotkey;

mod recovery;
pub use recovery::recover_import;
pub(crate) use recovery::{import_pending, persist_durable};

const MAX_SIZE: u64 = 2 * 1024_u64.pow(3);
const MAX_METADATA: u64 = 4 * 1024_u64.pow(2);
const MAX_ENTRIES: usize = 10_000;
const REQUIRED: [&str; 3] = ["manifest.json", "config.toml", "secrets.json"];
const MANIFEST_V1: &[u8] = br#"{"format":"wordy-backup","version":1}"#;
const MANIFEST_V2: &[u8] = br#"{"format":"wordy-backup","version":2}"#;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct Manifest {
    format: String,
    version: u32,
}

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
    config_path: &Path,
    settings: &Settings,
    credentials: &dyn CredentialStore,
    destination: &Path,
    password: &str,
) -> io::Result<()> {
    validate_password(password)?;
    let folder = parent(destination);
    let target = resolve_path(destination)?;
    let config_file = resolve_path(config_path)?;
    let audio_dir = resolve_path(&audio::clips_directory(config_path)?)?;
    if same_path(&target, &config_file)
        || same_path(
            &target,
            &resolve_path(&recovery::journal_path(config_path))?,
        )
        || target.ancestors().any(|p| same_path(p, &audio_dir))
    {
        return Err(invalid("备份不能覆盖配置文件或保存到音频托管目录"));
    }
    let mut snapshot = settings.clone();
    for name in settings.audio_clip_names.keys() {
        if !valid_filename(name) {
            return Err(invalid("音频库包含无效文件名"));
        }
        // 文件可能在外部被删除，未被槽位引用的失效记录不应阻止备份。
        if !audio_dir.join(name).try_exists()? {
            snapshot.audio_clip_names.remove(name);
        }
    }
    let has_catalog = !snapshot.audio_clip_names.is_empty();
    let manifest = if has_catalog {
        MANIFEST_V2
    } else {
        MANIFEST_V1
    };
    let secrets = serde_json::to_vec(&Secrets::read(credentials)?).map_err(io::Error::other)?;
    let config_text = snapshot.to_toml().map_err(io::Error::other)?;
    let names = backup_audio(&snapshot);
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
    // 导出与导入用同一套规则，避免生成无法恢复的备份。
    validate_settings(
        config_text.parse::<Table>().map_err(io::Error::other)?,
        has_catalog,
    )?;
    fs::create_dir_all(folder)?;
    let mut staging = NamedTempFile::new_in(folder)?;
    let encrypted = EncryptedWriter::new(staging.as_file_mut(), password)?;
    let mut archive = ZipWriter::new_stream(encrypted);
    let options = SimpleFileOptions::default().compression_method(CompressionMethod::Deflated);
    for (name, content) in REQUIRED
        .into_iter()
        .zip([manifest, config_text.as_bytes(), &secrets])
    {
        archive
            .start_file(name, options)
            .map_err(io::Error::other)?;
        archive.write_all(content)?;
    }
    let mut remaining = MAX_SIZE - (manifest.len() + config_text.len() + secrets.len()) as u64;
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

/// 手动导出与定时备份的默认目录：配置目录下的 backup。
pub fn backup_directory(config_path: &Path) -> PathBuf {
    parent(config_path).join("backup")
}

pub fn auto_backup_directory(config_path: &Path) -> PathBuf {
    backup_directory(config_path).join("auto")
}

pub fn validate_manual_destination(config_path: &Path, destination: &Path) -> io::Result<()> {
    let auto = resolve_path(&auto_backup_directory(config_path))?;
    let target = resolve_path(destination)?;
    if target.ancestors().any(|path| same_path(path, &auto)) {
        return Err(invalid("请将手动备份保存到自动备份目录之外"));
    }
    Ok(())
}

pub(crate) fn is_auto_backup_name(name: &str) -> bool {
    let Some(timestamp) = name
        .strip_prefix("auto-")
        .and_then(|name| name.strip_suffix(".wordybackup"))
    else {
        return false;
    };
    let bytes = timestamp.as_bytes();
    bytes.len() == 15
        && bytes[8] == b'-'
        && bytes[..8].iter().all(u8::is_ascii_digit)
        && bytes[9..].iter().all(u8::is_ascii_digit)
}

/// 只清理专用目录中最旧的自动备份；旧版共享目录不动。
pub fn prune_backups(config_path: &Path, keep: usize) -> io::Result<()> {
    let directory = resolve_path(&auto_backup_directory(config_path))?;
    let expected = resolve_path(&backup_directory(config_path))?.join("auto");
    if !same_path(&directory, &expected) {
        return Err(invalid("自动备份目录指向其他位置，已跳过清理"));
    }
    let mut names = Vec::new();
    for entry in fs::read_dir(&directory)? {
        let entry = entry?;
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if is_auto_backup_name(name) && entry.file_type()?.is_file() {
            names.push((entry.metadata()?.modified()?, name.to_owned()));
        }
    }
    names.sort_unstable();
    let remove = names.len().saturating_sub(keep.max(1));
    for (_, name) in &names[..remove] {
        fs::remove_file(directory.join(name))?;
    }
    Ok(())
}

pub fn import_backup(
    config: &mut ConfigStore,
    credentials: &dyn CredentialStore,
    source: &Path,
    password: &str,
) -> io::Result<()> {
    validate_password(password)?;
    recovery::ensure_idle(config.path())?;
    let folder = parent(config.path());
    fs::create_dir_all(folder)?;
    let mut plaintext = decrypt_backup(source, password, folder)?;
    let entry_count = zip_entry_count(&mut plaintext)?;
    let mut archive = ZipArchive::new(plaintext).map_err(io::Error::other)?;
    let names = validate_archive(&mut archive, entry_count)?;
    let manifest: Manifest = serde_json::from_slice(&read_metadata(&mut archive, REQUIRED[0])?)
        .map_err(io::Error::other)?;
    let mut restored = match (manifest.format.as_str(), manifest.version) {
        ("wordy-backup", 1 | 2) => {
            let text = String::from_utf8(read_metadata(&mut archive, REQUIRED[1])?)
                .map_err(io::Error::other)?;
            validate_settings(
                text.parse::<Table>().map_err(io::Error::other)?,
                manifest.version == 2,
            )?
        }
        _ => return Err(invalid("不支持此备份版本")),
    };
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
    if backup_audio(&restored)
        .into_iter()
        .ne(names.iter().map(String::as_str))
    {
        return Err(invalid("备份音频与配置不匹配"));
    }

    let directory = audio::clips_directory(config.path())?;
    let previous = Secrets::read(credentials)?;
    // 旧配置被替换后，这些引用可能不再存在于新配置中。
    let previous_clips: Vec<String> = config
        .settings()
        .referenced_audio()
        .map(str::to_owned)
        .collect();
    let mut replacements = BTreeMap::new();
    for name in names {
        let extension = audio::audio_extension(Path::new(&name))
            .ok_or_else(|| invalid("备份包含无效音频路径"))?;
        let replacement = format!("{}.{extension}", uuid::Uuid::new_v4().simple());
        if directory.join(&replacement).try_exists()? {
            return Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "音频名称已存在",
            ));
        }
        replacements.insert(name, replacement);
    }
    // 槽位与名称表必须使用同一批导入 ID；本机未引用的文件会保留原名称。
    restored.audio_clip_names = std::mem::take(&mut restored.audio_clip_names)
        .into_iter()
        .map(|(name, label)| (replacements[&name].clone(), label))
        .collect();
    let previous_refs: HashSet<_> = previous_clips
        .iter()
        .map(|name| name.to_lowercase())
        .collect();
    restored.audio_clip_names.extend(
        config
            .settings()
            .audio_clip_names
            .iter()
            .filter(|(name, _)| {
                !previous_refs.contains(&name.to_lowercase())
                    && audio::resolve_clip(config.path(), name).is_ok()
            })
            .map(|(name, label)| (name.clone(), label.clone())),
    );
    let journal = recovery::ImportJournal::begin(
        config.path(),
        previous,
        imported,
        replacements.values().cloned().collect(),
        previous_clips,
    )?;
    let result = (|| {
        fs::create_dir_all(&directory)?;
        for (name, replacement) in &replacements {
            let mut entry = archive
                .by_name(&format!("audio_clips/{name}"))
                .map_err(io::Error::other)?;
            let mut output = File::create_new(directory.join(replacement))?;
            let size = entry.size();
            if io::copy(&mut (&mut entry).take(size + 1), &mut output)? != size {
                return Err(invalid("备份音频大小无效"));
            }
            output.sync_all()?;
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
        journal.apply_credentials(credentials, &mut restored)?;
        // 配置与事务标记一次替换，重启后可判断应回滚还是完成提交。
        config.replace_with_prefix(restored, &journal.commit_marker())?;
        Ok(())
    })();
    if let Err(error) = result {
        return match recover_import(config.path(), credentials) {
            Err(rollback) => Err(io::Error::other(format!(
                "导入失败，且未能完整回滚: {error} ({rollback})"
            ))),
            Ok(()) => Err(error),
        };
    }
    // 清理失败不回滚已提交设置。进程中断后，启动恢复会再次清理旧引用。
    journal.clean_replaced_clips(&directory, config.settings());
    if let Err(error) = journal.clear(config.path()) {
        eprintln!("导入已提交，恢复记录将在下次启动时清理：{error}");
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

fn backup_audio(settings: &Settings) -> BTreeSet<&str> {
    settings
        .referenced_audio()
        .chain(settings.audio_clip_names.keys().map(String::as_str))
        .collect()
}

fn validate_settings(mut table: Table, has_catalog: bool) -> io::Result<Settings> {
    let template = include_str!("backup/config-v1.toml")
        .parse::<Table>()
        .map_err(io::Error::other)?;
    if template.keys().any(|key| {
        !matches!(
            key.as_str(),
            // V1 中从未必需、已移除或后来添加的字段由当前配置加载器处理。
            "chat_wheel_style"
                | "chat_wheel_text_modes"
                | "rolling_buffer_target"
                | "window_position"
                | "settings_window_position"
                | "main_playback_enabled"
                | "audio_output_device_name"
                | "audio_output_device"
                | "mic_input_device"
                | "virtual_output_device"
                | "auto_backup_enabled"
                | "auto_backup_interval_hours"
                | "auto_backup_keep"
        ) && !table.contains_key(key)
    }) {
        return Err(invalid("备份配置不完整"));
    }
    table.remove("chat_wheel_style");
    table.remove("rolling_buffer_target");
    let catalog = has_catalog
        .then(|| table.remove("audio_clip_names"))
        .flatten();
    validate_fields(&table, &template)?;
    if let Some(catalog) = catalog {
        if !catalog.as_table().is_some_and(|names| {
            names.iter().all(|(id, name)| {
                valid_filename(id) && name.as_str().is_some_and(|name| !name.trim().is_empty())
            })
        }) {
            return Err(invalid("备份包含无效音频库"));
        }
        table.insert("audio_clip_names".into(), catalog);
    }
    let settings = Settings::from_table(&table);
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
        // A wheel kept from a bad legacy hotkey is unbound, not invalid.
        if binding.trim().is_empty() {
            continue;
        }
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
            let expected = template
                .first()
                .ok_or_else(|| invalid("备份配置模板无效"))?;
            items
                .iter()
                .try_for_each(|item| validate_value(item, expected))
        }
        (Value::Table(items), Value::Table(template)) => validate_fields(items, template),
        _ => Err(invalid("备份配置字段类型无效")),
    }
}

fn validate_fields(items: &Table, template: &Table) -> io::Result<()> {
    items.iter().try_for_each(|(key, item)| {
        let expected = template
            .get(key)
            .ok_or_else(|| invalid("备份包含未知配置字段"))?;
        validate_value(item, expected)
    })
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
