#[cfg(test)]
#[path = "managed_tests.rs"]
mod tests;
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};

use crate::config::{ConfigStore, SLOT_COUNT};

#[derive(Clone, Debug, serde::Serialize)]
pub struct AudioClip {
    pub id: String,
    pub name: String,
    pub referenced: bool,
}

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
    let mut wheel_state = store
        .settings()
        .wheel(wheel)
        .filter(|_| slot < SLOT_COUNT)
        .ok_or_else(|| invalid("无效的轮盘槽位"))?;
    if clip.path.parent() != Some(clips_directory(store.path())?.as_path()) {
        return Err(invalid("音频副本不属于当前设置"));
    }
    let name = clip
        .path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| invalid("无效的音频文件名"))?
        .to_owned();
    wheel_state.slots[slot].clone_from(&name);
    wheel_state.labels[slot] = label.to_owned();
    // 导入音频只切到音频模式，原文字保留，切回文字模式即可恢复。
    wheel_state.text_modes[slot] = false;
    if let Some(gain) = gain {
        wheel_state.volumes[slot] = gain;
    }
    store.update(|settings| {
        settings.assign_wheel(wheel, wheel_state);
        let display_name = if label.trim().is_empty() {
            &name
        } else {
            label.trim()
        };
        settings
            .audio_clip_names
            .insert(name.clone(), display_name.to_owned());
    })?;
    clip.committed = true;
    Ok(name)
}

pub(crate) fn is_clip_id(name: &str) -> bool {
    !name.is_empty()
        && !name.contains(['/', '\\', ':', '<', '>', '"', '|', '?', '*'])
        && !name.chars().any(|c| c < ' ')
        && !name.ends_with(['.', ' '])
        && super::audio_extension(Path::new(name)).is_some()
}

pub fn resolve_clip(config: &Path, name: &str) -> io::Result<PathBuf> {
    if !is_clip_id(name) {
        return Err(invalid("无效的音频文件名"));
    }
    let folder = clips_directory(config)?.canonicalize()?;
    let file = folder.join(name);
    if !file.is_file() || file.canonicalize()?.parent() != Some(folder.as_path()) {
        return Err(invalid("音频文件不在托管目录中"));
    }
    Ok(file)
}

pub fn assign_clip(store: &mut ConfigStore, wheel: usize, slot: usize, id: &str) -> io::Result<()> {
    let mut state = store
        .settings()
        .wheel(wheel)
        .filter(|_| slot < SLOT_COUNT)
        .ok_or_else(|| invalid("无效的轮盘槽位"))?;
    resolve_clip(store.path(), id)?;
    if state.slots[slot] == id {
        return Ok(());
    }
    // 只有文字的槽位原本会直接朗读，增加音频后需要显式保留文字模式。
    if !state.texts[slot].is_empty() && state.text_mode(slot) {
        state.text_modes[slot] = true;
    }
    state.slots[slot] = id.to_owned();
    let name = clip_name(store, id).to_owned();
    // 槽位标签由两种模式共用，有文字时保留该标题。
    if state.texts[slot].is_empty() {
        state.labels[slot].clone_from(&name);
    }
    store.update(|settings| {
        settings.assign_wheel(wheel, state);
        settings
            .audio_clip_names
            .entry(id.to_owned())
            .or_insert(name);
    })
}

pub fn delete_clip(store: &mut ConfigStore, id: &str) -> io::Result<()> {
    let path = resolve_clip(store.path(), id)?;
    if is_reparse(&fs::symlink_metadata(&path)?) {
        return Err(invalid("只能删除托管目录中的音频文件"));
    }

    let previous = store.settings().clone();
    let normalized_id = id.to_lowercase();
    // 先保存解除引用的配置，避免配置保存失败时丢失文件。
    store.update(|settings| {
        for index in 0..settings.wheel_count() {
            let mut wheel = settings.wheel(index).expect("index within wheel count");
            for (slot, reference) in wheel.slots.iter_mut().enumerate() {
                if reference.to_lowercase() == normalized_id {
                    reference.clear();
                    if wheel.texts[slot].is_empty() {
                        wheel.labels[slot].clear();
                    }
                }
            }
            settings.assign_wheel(index, wheel);
        }
        settings
            .audio_clip_names
            .retain(|name, _| name.to_lowercase() != normalized_id);
    })?;

    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        Err(error) => {
            // Windows 可能拒绝删除正在使用的文件，恢复原有槽位与名称后允许重试。
            store.replace(previous).map_err(|restore| {
                io::Error::other(format!("删除音频失败：{error}；恢复槽位失败：{restore}"))
            })?;
            Err(error)
        }
    }
}

pub fn set_slot_label(
    store: &mut ConfigStore,
    wheel: usize,
    slot: usize,
    label: &str,
) -> io::Result<()> {
    let mut state = store
        .settings()
        .wheel(wheel)
        .filter(|_| slot < SLOT_COUNT)
        .ok_or_else(|| invalid("无效的轮盘槽位"))?;
    let id = &state.slots[slot];
    // 空标签用于清除槽位显示，不抹去音频的最后一个名称。
    let renamed =
        (!state.text_mode(slot) && !label.is_empty() && is_clip_id(id)).then(|| id.clone());
    state.labels[slot] = label.to_owned();
    store.update(|settings| {
        if let Some(id) = renamed {
            settings.audio_clip_names.insert(id, label.to_owned());
        }
        settings.assign_wheel(wheel, state);
    })
}

fn clip_name<'a>(store: &'a ConfigStore, id: &'a str) -> &'a str {
    store
        .settings()
        .audio_clip_names
        .get(id)
        .map_or(id, String::as_str)
}

pub fn list_clips(store: &ConfigStore) -> io::Result<Vec<AudioClip>> {
    // 引用包含所有轮盘中保存的音频，和槽位当前的播放模式无关。
    let referenced: std::collections::HashSet<_> = store
        .settings()
        .referenced_audio()
        .map(str::to_lowercase)
        .collect();
    let mut clips: Vec<_> = clip_paths(store)?
        .into_iter()
        .filter_map(|path| {
            let id = path.file_name()?.to_str()?;
            Some(AudioClip {
                id: id.to_owned(),
                name: clip_name(store, id).to_owned(),
                referenced: referenced.contains(&id.to_lowercase()),
            })
        })
        .collect();
    clips.sort_unstable_by(|a, b| a.name.cmp(&b.name).then_with(|| a.id.cmp(&b.id)));
    Ok(clips)
}

fn clip_paths(store: &ConfigStore) -> io::Result<Vec<PathBuf>> {
    let folder = clips_directory(store.path())?;
    let entries = match fs::read_dir(&folder) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(Vec::new()),
        Err(error) => return Err(error),
    };
    let mut paths = Vec::new();
    for entry in entries {
        let entry = entry?;
        let metadata = match entry.metadata() {
            Ok(metadata) => metadata,
            Err(error) if error.kind() == io::ErrorKind::NotFound => continue,
            Err(error) => return Err(error),
        };
        if !metadata.is_file() || is_reparse(&metadata) {
            continue;
        }
        let name = entry.file_name();
        let Some(name) = name.to_str() else {
            continue;
        };
        if is_clip_id(name) {
            paths.push(entry.path());
        }
    }
    Ok(paths)
}

/// 托管目录中未被任何轮盘槽位引用的音频。
pub fn unused_clips(store: &ConfigStore) -> io::Result<Vec<AudioClip>> {
    let mut clips = list_clips(store)?;
    clips.retain(|clip| !clip.referenced);
    Ok(clips)
}

pub struct CleanupReport {
    pub removed: usize,
    pub error: Option<io::Error>,
}

/// 只删除确认时已列出、且此刻仍未被引用的文件。
pub fn remove_unused_clips(
    store: &mut ConfigStore,
    confirmed: &[AudioClip],
) -> io::Result<CleanupReport> {
    let eligible: std::collections::HashSet<_> = unused_clips(store)?
        .into_iter()
        .map(|clip| clip.id)
        .collect();
    let folder = clips_directory(store.path())?;
    let mut removed = Vec::new();
    let mut error = None;
    for clip in confirmed.iter().filter(|clip| eligible.contains(&clip.id)) {
        match fs::remove_file(folder.join(&clip.id)) {
            Ok(()) => removed.push(&clip.id),
            Err(error) if error.kind() == io::ErrorKind::NotFound => (),
            Err(cause) => {
                error = Some(cause);
                break;
            }
        }
    }
    if !removed.is_empty()
        && let Err(save) = store.update(|settings| {
            for &id in &removed {
                settings.audio_clip_names.remove(id);
            }
        })
    {
        error = Some(match error {
            Some(cause) => io::Error::other(format!("{cause}；音频名称表保存失败：{save}")),
            None => io::Error::other(format!("音频名称表保存失败：{save}")),
        });
    }
    Ok(CleanupReport {
        removed: removed.len(),
        error,
    })
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
