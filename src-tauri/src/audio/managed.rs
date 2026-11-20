#[cfg(test)]
#[path = "managed_tests.rs"]
mod tests;
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use crate::config::{ConfigStore, SLOT_COUNT};

pub struct StagedClip {
    path: PathBuf,
    committed: bool,
}

impl StagedClip {
    pub fn path(&self) -> &Path {
        &self.path
    }
}

impl Drop for StagedClip {
    fn drop(&mut self) {
        if !self.committed {
            let _ = fs::remove_file(&self.path);
        }
    }
}

pub fn stage_clip(source: &Path, config: &Path, cancel: &AtomicBool) -> io::Result<StagedClip> {
    let extension = super::audio_extension(source).ok_or_else(|| invalid("不支持此音频格式"))?;
    let folder = clips_directory(config)?;
    fs::create_dir_all(&folder)?;
    let path = folder.join(format!("{}.{}", uuid::Uuid::new_v4().simple(), extension));
    let mut input = File::open(source)?;
    let mut output = File::create_new(&path)?;
    // The source can be the user's only copy. Stage an independent copy, and validate that copy.
    let clip = StagedClip {
        path,
        committed: false,
    };
    let mut buffer = [0; 64 * 1024];
    let result = (|| {
        loop {
            if cancel.load(Ordering::Acquire) {
                return Err(io::Error::new(io::ErrorKind::Interrupted, "音频导入已取消"));
            }
            let count = input.read(&mut buffer)?;
            if count == 0 {
                break;
            }
            output.write_all(&buffer[..count])?;
        }
        output.sync_all()
    })();
    drop(output);
    result?;
    super::validate_clip(clip.path(), cancel)?;
    Ok(clip)
}

pub fn commit_slot_clip(
    store: &mut ConfigStore,
    wheel: usize,
    slot: usize,
    mut clip: StagedClip,
    label: &str,
    gain: Option<f64>,
) -> io::Result<String> {
    let previous = store
        .settings()
        .wheel(wheel)
        .filter(|_| slot < SLOT_COUNT)
        .ok_or_else(|| invalid("无效的轮盘槽位"))?
        .slots[slot]
        .clone();
    if clip.path.parent() != Some(clips_directory(store.path())?.as_path()) {
        return Err(invalid("音频副本不属于当前设置"));
    }
    let name = clip
        .path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| invalid("无效的音频文件名"))?
        .to_owned();
    store.update_wheel(wheel, |wheel| {
        wheel.slots[slot].clone_from(&name);
        wheel.labels[slot] = label.to_owned();
        // 导入音频只切到音频模式，原文字保留，切回文字模式即可恢复。
        wheel.text_modes[slot] = false;
        if let Some(gain) = gain {
            wheel.volumes[slot] = gain;
        }
    })?;
    clip.committed = true;
    if !previous.is_empty()
        && let Err(error) = remove_unreferenced_clip(store, &previous)
    {
        eprintln!("旧音频清理失败：{error}");
    }
    Ok(name)
}

pub fn resolve_clip(config: &Path, name: &str) -> io::Result<PathBuf> {
    if name.is_empty()
        || name.contains(['/', '\\', ':', '<', '>', '"', '|', '?', '*'])
        || name.chars().any(|c| c < ' ')
        || name.ends_with(['.', ' '])
        || super::audio_extension(Path::new(name)).is_none()
    {
        return Err(invalid("无效的音频文件名"));
    }
    let folder = clips_directory(config)?.canonicalize()?;
    let file = folder.join(name);
    if !file.is_file() || file.canonicalize()?.parent() != Some(folder.as_path()) {
        return Err(invalid("音频文件不在托管目录中"));
    }
    Ok(file)
}

pub fn remove_unreferenced_clip(store: &ConfigStore, name: &str) -> io::Result<bool> {
    if store
        .settings()
        .referenced_audio()
        .any(|slot| slot.eq_ignore_ascii_case(name))
    {
        return Ok(false);
    }
    match resolve_clip(store.path(), name).and_then(fs::remove_file) {
        Ok(()) => Ok(true),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(false),
        Err(error) => Err(error),
    }
}

/// 托管目录中未被任何轮盘槽位引用的文件；目录不存在时视为空。
pub fn unused_clips(store: &ConfigStore) -> io::Result<Vec<PathBuf>> {
    let folder = clips_directory(store.path())?;
    let entries = match fs::read_dir(&folder) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let mut paths = Vec::new();
    for entry in entries {
        let entry = entry?;
        if !entry.file_type()?.is_file() {
            continue;
        }
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if !referenced(store, name) {
            paths.push(entry.path());
        }
    }
    Ok(paths)
}

/// 删除所有未引用文件；删除前再次确认引用，配置变化时不会误删。
pub fn remove_unused_clips(store: &ConfigStore) -> io::Result<usize> {
    let mut removed = 0;
    for path in unused_clips(store)? {
        let name = path
            .file_name()
            .and_then(|name| name.to_str())
            .unwrap_or_default();
        if referenced(store, name) {
            continue;
        }
        match fs::remove_file(&path) {
            Ok(()) => removed += 1,
            Err(error) if error.kind() == io::ErrorKind::NotFound => (),
            Err(error) => return Err(error),
        }
    }
    Ok(removed)
}

fn referenced(store: &ConfigStore, name: &str) -> bool {
    store
        .settings()
        .referenced_audio()
        .any(|slot| slot.eq_ignore_ascii_case(name))
}

pub(crate) fn clips_directory(config: &Path) -> io::Result<PathBuf> {
    let folder = config
        .parent()
        .unwrap_or(Path::new("."))
        .join("audio_clips");
    match fs::symlink_metadata(&folder) {
        Ok(meta) if is_reparse(&meta) => Err(invalid("音频托管目录不能是符号链接或 junction")),
        Ok(_) => Ok(folder),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(folder),
        Err(error) => Err(error),
    }
}

fn is_reparse(meta: &fs::Metadata) -> bool {
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        meta.file_attributes() & 0x400 != 0
    }
    #[cfg(not(windows))]
    meta.is_symlink()
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
