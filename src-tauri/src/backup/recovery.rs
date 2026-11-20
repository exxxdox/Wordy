use std::collections::HashSet;
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::{ptr, slice};

use serde::{Deserialize, Serialize};
use tempfile::NamedTempFile;
use uuid::Uuid;
use windows_sys::Win32::Foundation::LocalFree;
use windows_sys::Win32::Security::Cryptography::{
    CRYPT_INTEGER_BLOB, CRYPTPROTECT_UI_FORBIDDEN, CryptProtectData, CryptUnprotectData,
};
use windows_sys::Win32::Storage::FileSystem::{
    MOVEFILE_REPLACE_EXISTING, MOVEFILE_WRITE_THROUGH, MoveFileExW,
};

use super::{Secrets, audio, backup_audio, invalid, parent, set_secret, valid_filename};
use crate::config::Settings;
use crate::credentials::{CredentialStore, Provider};

const PROVIDERS: [Provider; 2] = [Provider::Cartesia, Provider::Volcengine];
const JOURNAL_LIMIT: u64 = 16 * 1024 * 1024;

#[derive(Deserialize, Serialize)]
pub(super) struct ImportJournal {
    id: String,
    previous: Secrets,
    imported: Secrets,
    clips: Vec<String>,
    previous_clips: Vec<String>,
}

impl ImportJournal {
    pub(super) fn begin(
        path: &Path,
        previous: Secrets,
        imported: Secrets,
        clips: Vec<String>,
        previous_clips: Vec<String>,
    ) -> io::Result<Self> {
        let journal = Self {
            id: Uuid::new_v4().simple().to_string(),
            previous,
            imported,
            clips,
            previous_clips,
        };
        let encoded = serde_json::to_vec(&journal).map_err(io::Error::other)?;
        let sealed = dpapi(&encoded, true)?;
        if sealed.len() as u64 > JOURNAL_LIMIT {
            return Err(invalid("导入恢复记录过大"));
        }
        let mut temp = NamedTempFile::new_in(parent(path))?;
        temp.write_all(&sealed)?;
        persist_durable(temp, &journal_path(path), false)?;
        Ok(journal)
    }

    pub(super) fn commit_marker(&self) -> String {
        format!("# wordy-import:{}\n", self.id)
    }

    pub(super) fn apply_credentials(
        &self,
        credentials: &dyn CredentialStore,
        settings: &mut Settings,
    ) -> io::Result<()> {
        apply_secrets(credentials, &self.imported)?;
        for provider in PROVIDERS {
            settings.set_credential_status(provider, self.imported.get(provider).is_some());
        }
        Ok(())
    }

    pub(super) fn clear(&self, path: &Path) -> io::Result<()> {
        remove_if_present(&journal_path(path))
    }

    pub(super) fn clean_replaced_clips(&self, directory: &Path, settings: &Settings) {
        // 仅音频库仍保有的文件，在恢复清理时也必须保留。
        let retained: HashSet<_> = backup_audio(settings)
            .into_iter()
            .map(str::to_lowercase)
            .collect();
        for name in &self.previous_clips {
            if valid_filename(name)
                && !retained.contains(&name.to_lowercase())
                && let Err(error) = remove_if_present(&directory.join(name))
            {
                eprintln!("旧音频清理失败：{error}");
            }
        }
    }
}

fn apply_secrets(credentials: &dyn CredentialStore, target: &Secrets) -> io::Result<()> {
    for provider in PROVIDERS {
        let value = target.get(provider);
        if credentials.load(provider)?.as_deref() != value {
            set_secret(credentials, provider, value)?;
        }
    }
    Ok(())
}

pub(super) fn journal_path(config: &Path) -> PathBuf {
    config.with_extension("toml.import-pending")
}

pub(crate) fn import_pending(path: &Path) -> io::Result<bool> {
    journal_path(path).try_exists()
}

pub(super) fn ensure_idle(path: &Path) -> io::Result<()> {
    if import_pending(path)? {
        return Err(io::Error::other("请重新启动 Wordy 以恢复上一次导入"));
    }
    Ok(())
}

pub fn recover_import(path: &Path, credentials: &dyn CredentialStore) -> io::Result<()> {
    let file = match File::open(journal_path(path)) {
        Ok(file) => file,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    let mut sealed = Vec::new();
    file.take(JOURNAL_LIMIT + 1).read_to_end(&mut sealed)?;
    if sealed.len() as u64 > JOURNAL_LIMIT {
        return Err(invalid("导入恢复记录过大"));
    }
    let bytes = dpapi(&sealed, false)?;
    let journal: ImportJournal = serde_json::from_slice(&bytes).map_err(io::Error::other)?;
    let marker = journal.commit_marker();
    let mut header = vec![0; marker.len()];
    let committed = match File::open(path) {
        Ok(mut file) => match file.read_exact(&mut header) {
            Ok(()) => header == marker.as_bytes(),
            Err(error) if error.kind() == io::ErrorKind::UnexpectedEof => false,
            Err(error) => return Err(error),
        },
        Err(error) if error.kind() == io::ErrorKind::NotFound => false,
        Err(error) => return Err(error),
    };
    if committed {
        apply_secrets(credentials, &journal.imported)?;
        let settings = Settings::from_toml(&fs::read_to_string(path)?).map_err(io::Error::other)?;
        journal.clean_replaced_clips(&audio::clips_directory(path)?, &settings);
    } else {
        apply_secrets(credentials, &journal.previous)?;
        if !journal.clips.is_empty() {
            let directory = audio::clips_directory(path)?;
            for name in &journal.clips {
                if !valid_filename(name) {
                    return Err(invalid("导入恢复记录包含无效的音频名称"));
                }
                remove_if_present(&directory.join(name))?;
            }
        }
    }
    journal.clear(path)
}

fn remove_if_present(path: &Path) -> io::Result<()> {
    match fs::remove_file(path) {
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        result => result,
    }
}

pub(crate) fn persist_durable(temp: NamedTempFile, path: &Path, replace: bool) -> io::Result<()> {
    temp.as_file().sync_all()?;
    let temp = temp.into_temp_path();
    let source = temp.canonicalize()?;
    let target = parent(path)
        .canonicalize()?
        .join(path.file_name().ok_or_else(|| invalid("文件名无效"))?);
    let source: Vec<u16> = source.as_os_str().encode_wide().chain([0]).collect();
    let target: Vec<u16> = target.as_os_str().encode_wide().chain([0]).collect();
    let flags = MOVEFILE_WRITE_THROUGH
        | if replace {
            MOVEFILE_REPLACE_EXISTING
        } else {
            0
        };
    // 恢复记录与配置都先刷盘，再在同目录替换。后台线程可在两步之间被中断。
    if unsafe { MoveFileExW(source.as_ptr(), target.as_ptr(), flags) } == 0 {
        return Err(io::Error::last_os_error());
    }
    Ok(())
}

fn dpapi(bytes: &[u8], encrypt: bool) -> io::Result<Vec<u8>> {
    let input = CRYPT_INTEGER_BLOB {
        cbData: bytes.len().try_into().map_err(io::Error::other)?,
        pbData: bytes.as_ptr().cast_mut(),
    };
    let mut output = CRYPT_INTEGER_BLOB::default();
    // DPAPI 绑定当前 Windows 用户。成功时系统分配输出，由 LocalFree 释放。
    unsafe {
        let result = if encrypt {
            CryptProtectData(
                &input,
                ptr::null(),
                ptr::null(),
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        } else {
            CryptUnprotectData(
                &input,
                ptr::null_mut(),
                ptr::null(),
                ptr::null(),
                ptr::null(),
                CRYPTPROTECT_UI_FORBIDDEN,
                &mut output,
            )
        };
        if result == 0 {
            return Err(io::Error::last_os_error());
        }
        if output.pbData.is_null() {
            return Err(invalid("导入恢复记录为空"));
        }
        let data = slice::from_raw_parts_mut(output.pbData, output.cbData as usize);
        let result = data.to_vec();
        if !encrypt {
            for byte in data {
                ptr::write_volatile(byte, 0);
            }
        }
        LocalFree(output.pbData.cast());
        Ok(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::config::ConfigStore;
    use std::cell::RefCell;

    #[derive(Default)]
    struct Vault(RefCell<[Option<String>; 2]>);

    impl CredentialStore for Vault {
        fn load(&self, provider: Provider) -> io::Result<Option<String>> {
            Ok(self.0.borrow()[usize::from(provider == Provider::Volcengine)].clone())
        }
        fn save(&self, provider: Provider, secret: &str) -> io::Result<()> {
            self.0.borrow_mut()[usize::from(provider == Provider::Volcengine)] =
                Some(secret.into());
            Ok(())
        }
        fn delete(&self, provider: Provider) -> io::Result<()> {
            self.0.borrow_mut()[usize::from(provider == Provider::Volcengine)] = None;
            Ok(())
        }
    }

    #[test]
    fn committed_import_recovery_keeps_new_audio_and_reconciles_credentials() {
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("config.toml");
        let mut config = ConfigStore::load(&path).unwrap();
        let audio = audio::clips_directory(&path).unwrap();
        fs::create_dir(&audio).unwrap();
        fs::write(audio.join("imported.wav"), b"audio fixture").unwrap();
        fs::write(audio.join("previous.wav"), b"old audio fixture").unwrap();
        let detached = "f3e5c6af679945649305e251faa29539.wav";
        fs::write(audio.join(detached), b"detached fixture").unwrap();
        let journal = ImportJournal::begin(
            &path,
            Secrets {
                cartesia_api_key: None,
                volcengine_access_key: None,
            },
            Secrets {
                cartesia_api_key: Some("fixture-after-commit".into()),
                volcengine_access_key: None,
            },
            vec!["imported.wav".into()],
            vec!["previous.wav".into(), detached.into()],
        )
        .unwrap();
        let mut next = config.settings().clone();
        next.chat_wheel_slots[0] = "imported.wav".into();
        next.audio_clip_names
            .insert(detached.into(), "未引用".into());
        config
            .replace_with_prefix(next, &journal.commit_marker())
            .unwrap();
        let vault = Vault::default();
        recover_import(&path, &vault).unwrap();
        assert!(audio.join("imported.wav").exists());
        assert!(audio.join(detached).exists());
        assert!(!audio.join("previous.wav").exists());
        assert!(!import_pending(&path).unwrap());
        assert_eq!(
            vault.load(Provider::Cartesia).unwrap().as_deref(),
            Some("fixture-after-commit")
        );
        assert_eq!(vault.load(Provider::Volcengine).unwrap(), None);
        assert_eq!(
            ConfigStore::load(&path)
                .unwrap()
                .settings()
                .chat_wheel_slots[0],
            "imported.wav"
        );
    }
}
