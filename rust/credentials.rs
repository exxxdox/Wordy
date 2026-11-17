use std::io;

use serde::Serialize;

#[derive(Clone, Copy, Debug, Eq, PartialEq, Serialize)]
pub enum Provider {
    Cartesia,
    Volcengine,
}

impl Provider {
    pub const fn username(self) -> &'static str {
        match self {
            Self::Cartesia => "cartesia_api_key",
            Self::Volcengine => "volcengine_access_key",
        }
    }

    pub const fn name(self) -> &'static str {
        match self {
            Self::Cartesia => "Cartesia",
            Self::Volcengine => "Volcengine",
        }
    }
}

pub trait CredentialStore {
    fn load(&self, provider: Provider) -> io::Result<Option<String>>;
    fn save(&self, provider: Provider, secret: &str) -> io::Result<()>;
    fn delete(&self, provider: Provider) -> io::Result<()>;
}

pub struct WindowsCredentials;

pub fn normalize_api_key_input(raw: &str) -> &str {
    let trimmed = raw.trim();
    trimmed
        .strip_prefix("CARTESIA_API_KEY=")
        .map_or(trimmed, str::trim)
}

#[cfg(any(windows, test))]
const SERVICE: &str = "Wordy";

#[cfg(any(windows, test))]
#[derive(Clone)]
struct Record {
    username: String,
    secret: String,
}

#[cfg(any(windows, test))]
trait Vault {
    fn read(&self, target: &str) -> io::Result<Option<Record>>;
    fn write(&mut self, target: &str, record: &Record) -> io::Result<()>;
    fn delete(&mut self, target: &str) -> io::Result<()>;
}

#[cfg(any(windows, test))]
fn compound_target(username: &str) -> String {
    format!("{username}@{SERVICE}")
}

#[cfg(any(windows, test))]
fn read_provider(vault: &impl Vault, provider: Provider) -> io::Result<Option<String>> {
    let username = provider.username();
    if let Some(record) = vault
        .read(SERVICE)?
        .filter(|entry| entry.username == username)
    {
        return Ok(Some(record.secret));
    }
    Ok(vault
        .read(&compound_target(username))?
        .filter(|entry| entry.username == username)
        .map(|entry| entry.secret))
}

#[cfg(any(windows, test))]
fn write_provider(vault: &mut impl Vault, provider: Provider, secret: &str) -> io::Result<()> {
    let secret = secret.trim();
    if secret.is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "API 密钥不能为空",
        ));
    }
    // Preserve Python WinVault's primary target and collision layout so both
    // applications can access credentials saved under either target.
    if let Some(previous) = vault.read(SERVICE)? {
        vault.write(&compound_target(&previous.username), &previous)?;
    }
    vault.write(
        SERVICE,
        &Record {
            username: provider.username().into(),
            secret: secret.into(),
        },
    )
}

#[cfg(any(windows, test))]
fn delete_provider(vault: &mut impl Vault, provider: Provider) -> io::Result<()> {
    let username = provider.username();
    let compound = compound_target(username);
    // Keep the authoritative entry readable if removal of its backup fails.
    for target in [compound.as_str(), SERVICE] {
        if vault
            .read(target)?
            .is_some_and(|entry| entry.username == username)
        {
            vault.delete(target)?;
        }
    }
    Ok(())
}

#[cfg(windows)]
mod win32 {
    use std::{ptr, slice, sync::Mutex};

    use windows_sys::Win32::{
        Foundation::ERROR_NOT_FOUND,
        Security::Credentials::{
            CRED_MAX_CREDENTIAL_BLOB_SIZE, CRED_PERSIST_ENTERPRISE, CRED_TYPE_GENERIC, CREDENTIALW,
            CredDeleteW, CredFree, CredReadW, CredWriteW,
        },
    };

    use super::*;

    struct WinVault;
    static VAULT_LOCK: Mutex<()> = Mutex::new(());

    fn locked<T>(operation: impl FnOnce(&mut WinVault) -> io::Result<T>) -> io::Result<T> {
        // Updating the primary and compound targets is one operation for Wordy.
        let _guard = VAULT_LOCK
            .lock()
            .map_err(|_| io::Error::other("无法访问 Windows 凭据管理器"))?;
        operation(&mut WinVault)
    }

    fn wide(value: &str) -> Vec<u16> {
        value.encode_utf16().chain([0]).collect()
    }

    fn not_found(error: &io::Error) -> bool {
        error.raw_os_error() == Some(ERROR_NOT_FOUND as i32)
    }

    fn invalid_record() -> io::Error {
        io::Error::new(io::ErrorKind::InvalidData, "Windows 凭据格式无效")
    }

    struct NativeCredential(ptr::NonNull<CREDENTIALW>);

    impl Drop for NativeCredential {
        fn drop(&mut self) {
            // CredReadW transfers ownership of one allocation to its caller.
            unsafe { CredFree(self.0.as_ptr().cast()) };
        }
    }

    impl NativeCredential {
        fn decode(&self) -> io::Result<Record> {
            // Win32 owns the nested pointers for the lifetime of this allocation.
            let credential = unsafe { self.0.as_ref() };
            let username = if credential.UserName.is_null() {
                String::new()
            } else {
                let mut len = 0;
                // A non-null UserName returned by CredReadW is null-terminated.
                while unsafe { *credential.UserName.add(len) } != 0 {
                    len += 1;
                }
                let units = unsafe { slice::from_raw_parts(credential.UserName, len) };
                String::from_utf16(units).map_err(|_| invalid_record())?
            };
            let bytes = match credential.CredentialBlobSize {
                0 => &[][..],
                size if !credential.CredentialBlob.is_null() => unsafe {
                    slice::from_raw_parts(credential.CredentialBlob, size as usize)
                },
                _ => return Err(invalid_record()),
            };
            let secret = decode_secret(bytes).ok_or_else(invalid_record)?;
            Ok(Record { username, secret })
        }
    }

    fn decode_secret(bytes: &[u8]) -> Option<String> {
        let (bytes, big_endian) = match bytes {
            [0xff, 0xfe, tail @ ..] => (tail, false),
            [0xfe, 0xff, tail @ ..] => (tail, true),
            _ => (bytes, false),
        };
        let (words, rest) = bytes.as_chunks::<2>();
        if rest.is_empty() {
            let units: Vec<u16> = words
                .iter()
                .copied()
                .map(if big_endian {
                    u16::from_be_bytes
                } else {
                    u16::from_le_bytes
                })
                .collect();
            if let Ok(decoded) = String::from_utf16(&units) {
                return Some(decoded);
            }
        }
        // WinVault also reads credentials that were historically written as UTF-8.
        std::str::from_utf8(bytes).ok().map(str::to_owned)
    }

    impl Vault for WinVault {
        fn read(&self, target: &str) -> io::Result<Option<Record>> {
            let target = wide(target);
            let mut raw = ptr::null_mut();
            // The target is a terminated UTF-16 string alive for the OS call.
            if unsafe { CredReadW(target.as_ptr(), CRED_TYPE_GENERIC, 0, &mut raw) } == 0 {
                let error = io::Error::last_os_error();
                return if not_found(&error) {
                    Ok(None)
                } else {
                    Err(error)
                };
            }
            let allocation = NativeCredential(ptr::NonNull::new(raw).ok_or_else(invalid_record)?);
            allocation.decode().map(Some)
        }

        fn write(&mut self, target: &str, record: &Record) -> io::Result<()> {
            let mut target = wide(target);
            let mut username = wide(&record.username);
            let mut secret: Vec<u16> = record.secret.encode_utf16().collect();
            let size = size_of_val(secret.as_slice());
            if size > CRED_MAX_CREDENTIAL_BLOB_SIZE as usize {
                return Err(io::Error::new(io::ErrorKind::InvalidInput, "API 密钥过长"));
            }
            let credential = CREDENTIALW {
                Type: CRED_TYPE_GENERIC,
                Persist: CRED_PERSIST_ENTERPRISE,
                TargetName: target.as_mut_ptr(),
                UserName: username.as_mut_ptr(),
                CredentialBlob: secret.as_mut_ptr().cast(),
                CredentialBlobSize: size as u32,
                ..Default::default()
            };
            // The pointed-to buffers remain valid until CredWriteW returns.
            if unsafe { CredWriteW(&credential, 0) } == 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        }

        fn delete(&mut self, target: &str) -> io::Result<()> {
            let target = wide(target);
            // The target is a terminated UTF-16 string alive for the OS call.
            if unsafe { CredDeleteW(target.as_ptr(), CRED_TYPE_GENERIC, 0) } == 0 {
                let error = io::Error::last_os_error();
                if !not_found(&error) {
                    return Err(error);
                }
            }
            Ok(())
        }
    }

    impl CredentialStore for WindowsCredentials {
        fn load(&self, provider: Provider) -> io::Result<Option<String>> {
            locked(|vault| read_provider(vault, provider))
        }

        fn save(&self, provider: Provider, secret: &str) -> io::Result<()> {
            locked(|vault| write_provider(vault, provider, secret))
        }

        fn delete(&self, provider: Provider) -> io::Result<()> {
            locked(|vault| delete_provider(vault, provider))
        }
    }

    #[cfg(test)]
    #[test]
    fn decodes_native_utf16_and_older_utf8_blobs() {
        assert_eq!(
            decode_secret(&[b'a', 0, 0x2d, 0x4e]).as_deref(),
            Some("a中")
        );
        assert_eq!(
            decode_secret(&[0xfe, 0xff, 0x4e, 0x2d]).as_deref(),
            Some("中")
        );
        assert_eq!(decode_secret(b"old").as_deref(), Some("old"));
    }
}

#[cfg(not(windows))]
impl CredentialStore for WindowsCredentials {
    fn load(&self, _: Provider) -> io::Result<Option<String>> {
        Err(io::ErrorKind::Unsupported.into())
    }

    fn save(&self, _: Provider, _: &str) -> io::Result<()> {
        Err(io::ErrorKind::Unsupported.into())
    }

    fn delete(&self, _: Provider) -> io::Result<()> {
        Err(io::ErrorKind::Unsupported.into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;

    #[derive(Default)]
    struct MemoryVault {
        values: HashMap<String, Record>,
    }

    impl Vault for MemoryVault {
        fn read(&self, target: &str) -> io::Result<Option<Record>> {
            Ok(self.values.get(target).cloned())
        }

        fn write(&mut self, target: &str, record: &Record) -> io::Result<()> {
            self.values.insert(target.into(), record.clone());
            Ok(())
        }

        fn delete(&mut self, target: &str) -> io::Result<()> {
            self.values.remove(target);
            Ok(())
        }
    }

    #[test]
    fn legacy_targets_keep_provider_keys_separate_and_delete_both_matching_entries() {
        let mut vault = MemoryVault::default();
        vault
            .write(
                "Wordy",
                &Record {
                    username: "cartesia_api_key".into(),
                    secret: "旧".into(),
                },
            )
            .unwrap();
        assert_eq!(
            read_provider(&vault, Provider::Cartesia)
                .unwrap()
                .as_deref(),
            Some("旧")
        );
        assert_eq!(read_provider(&vault, Provider::Volcengine).unwrap(), None);
        write_provider(&mut vault, Provider::Volcengine, "  new  ").unwrap();
        assert!(vault.values.contains_key("cartesia_api_key@Wordy"));
        assert_eq!(
            read_provider(&vault, Provider::Cartesia)
                .unwrap()
                .as_deref(),
            Some("旧")
        );
        assert_eq!(
            read_provider(&vault, Provider::Volcengine)
                .unwrap()
                .as_deref(),
            Some("new")
        );
        write_provider(&mut vault, Provider::Cartesia, "replacement").unwrap();
        delete_provider(&mut vault, Provider::Cartesia).unwrap();
        delete_provider(&mut vault, Provider::Cartesia).unwrap();
        assert_eq!(read_provider(&vault, Provider::Cartesia).unwrap(), None);
        assert_eq!(
            read_provider(&vault, Provider::Volcengine)
                .unwrap()
                .as_deref(),
            Some("new")
        );
        assert!(write_provider(&mut vault, Provider::Cartesia, "   ").is_err());
    }

    #[test]
    fn normalizes_the_existing_paste_format_without_affecting_storage() {
        assert_eq!(normalize_api_key_input(" CARTESIA_API_KEY= key "), "key");
        assert_eq!(normalize_api_key_input("  my key  "), "my key");
        assert_eq!(normalize_api_key_input(" CARTESIA_API_KEY= "), "");
    }
}
