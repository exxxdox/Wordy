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
    let extension = extension(source).ok_or_else(|| invalid("不支持此音频格式"))?;
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
    super::decode_clip(clip.path(), cancel)?;
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
        wheel.texts[slot].clear();
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
        || extension(Path::new(name)).is_none()
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

fn extension(path: &Path) -> Option<String> {
    let extension = path.extension()?.to_str()?.to_ascii_lowercase();
    [
        "wav", "mp3", "ogg", "flac", "m4a", "aac", "wma", "aiff", "aif", "opus",
    ]
    .contains(&extension.as_str())
    .then_some(extension)
}

fn invalid(message: &str) -> io::Error {
    io::Error::new(io::ErrorKind::InvalidData, message)
}
