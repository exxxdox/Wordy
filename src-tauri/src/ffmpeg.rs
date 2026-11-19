use std::{env, path::PathBuf};

#[cfg(windows)]
use std::{
    ffi::OsString,
    io,
    os::windows::{ffi::OsStringExt, process::CommandExt},
    path::Path,
    process::{Command, Stdio},
    ptr,
};
#[cfg(windows)]
use windows_sys::{
    Win32::{
        Foundation::ERROR_SUCCESS,
        System::{
            Registry::{
                HKEY, HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE, RRF_RT_REG_EXPAND_SZ, RRF_RT_REG_SZ,
                RegGetValueW,
            },
            Threading::CREATE_NO_WINDOW,
        },
    },
    core::{PCWSTR, w},
};

const FFMPEG: &str = if cfg!(windows) {
    "ffmpeg.exe"
} else {
    "ffmpeg"
};

/// 构建时嵌入的精简 FFmpeg；没有放 assets/ffmpeg.exe 时为空。
const BUNDLED: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/ffmpeg.exe"));

fn find_in_dirs(directories: impl IntoIterator<Item = PathBuf>, name: &str) -> Option<PathBuf> {
    let current = env::current_dir().ok();
    // Resolve an explicit path before spawning so Windows cannot search the current directory.
    directories
        .into_iter()
        .filter(|directory| directory.is_absolute() && current.as_ref() != Some(directory))
        .map(|directory| directory.join(name))
        .find(|candidate| candidate.is_file())
}

fn find_on_path(name: &str) -> Option<PathBuf> {
    find_in_dirs(env::split_paths(&env::var_os("PATH")?), name)
}

pub(crate) fn find() -> Option<PathBuf> {
    #[cfg(windows)]
    if let Some(path) = bundled() {
        return Some(path);
    }
    find_system()
}

/// 打包的 FFmpeg 优先：解包到配置目录后复用，系统 FFmpeg 只作为兜底。
#[cfg(windows)]
fn bundled() -> Option<PathBuf> {
    extract(&crate::config::config_directory().join("bin"), BUNDLED)
}

/// 大小一致即视为同一份文件，避免每次调用都重写。
#[cfg(windows)]
fn extract(directory: &Path, bytes: &[u8]) -> Option<PathBuf> {
    if bytes.is_empty() {
        return None;
    }
    let path = directory.join(FFMPEG);
    if std::fs::metadata(&path).is_ok_and(|meta| meta.len() == bytes.len() as u64) {
        return Some(path);
    }
    std::fs::create_dir_all(directory).ok()?;
    let staging = directory.join(format!("{FFMPEG}.new"));
    std::fs::write(&staging, bytes).ok()?;
    // Windows 不能用重命名覆盖已存在的文件。
    let _ = std::fs::remove_file(&path);
    if std::fs::rename(&staging, &path).is_err() {
        let _ = std::fs::remove_file(&staging);
        return None;
    }
    Some(path)
}

fn find_system() -> Option<PathBuf> {
    let from_path = find_on_path(FFMPEG);
    #[cfg(windows)]
    {
        from_path
            .or_else(|| {
                find_in_dirs(
                    [
                        ("LOCALAPPDATA", r"Microsoft\WinGet\Links"),
                        ("ProgramFiles", r"WinGet\Links"),
                    ]
                    .into_iter()
                    .filter_map(|(variable, suffix)| {
                        env::var_os(variable).map(|directory| PathBuf::from(directory).join(suffix))
                    }),
                    FFMPEG,
                )
            })
            // Without symlink privileges, WinGet adds the portable folder to the registered PATH.
            .or_else(|| {
                [
                    (HKEY_CURRENT_USER, w!("Environment")),
                    (
                        HKEY_LOCAL_MACHINE,
                        w!(r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                    ),
                ]
                .into_iter()
                .filter_map(|(key, subkey)| registered_path(key, subkey))
                .find_map(|path| find_in_dirs(env::split_paths(&path), FFMPEG))
            })
    }
    #[cfg(not(windows))]
    from_path
}

#[cfg(windows)]
fn registered_path(key: HKEY, subkey: PCWSTR) -> Option<OsString> {
    let mut bytes = 0;
    let get_path = |data, size| unsafe {
        RegGetValueW(
            key,
            subkey,
            w!("Path"),
            RRF_RT_REG_SZ | RRF_RT_REG_EXPAND_SZ,
            ptr::null_mut(),
            data,
            size,
        )
    };
    if get_path(ptr::null_mut(), &mut bytes) != ERROR_SUCCESS {
        return None;
    }
    let mut value = vec![0u16; (bytes as usize).div_ceil(2)];
    if get_path(value.as_mut_ptr().cast(), &mut bytes) != ERROR_SUCCESS {
        return None;
    }
    let len = value
        .iter()
        .position(|&unit| unit == 0)
        .unwrap_or(value.len());
    Some(OsString::from_wide(&value[..len]))
}

#[cfg(windows)]
pub(crate) fn winget() -> Option<PathBuf> {
    const WINGET: &str = "winget.exe";
    let app_alias = env::var_os("LOCALAPPDATA")
        .map(|directory| PathBuf::from(directory).join(r"Microsoft\WindowsApps"));
    find_in_dirs(app_alias, WINGET).or_else(|| find_on_path(WINGET))
}

#[cfg(windows)]
pub(crate) fn install(winget: &Path) -> io::Result<()> {
    let status = Command::new(winget)
        .args([
            "install",
            "--id",
            "Gyan.FFmpeg",
            "--exact",
            "--source",
            "winget",
            "--scope",
            "user",
            "--accept-source-agreements",
            "--accept-package-agreements",
            "--disable-interactivity",
        ])
        .creation_flags(CREATE_NO_WINDOW)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|error| io::Error::new(error.kind(), format!("无法启动 WinGet：{error}")))?;
    if !status.success() {
        return Err(io::Error::other(format!("WinGet 安装失败（{status}）")));
    }
    // A successful installer exit does not guarantee usable command discovery.
    find().ok_or_else(|| io::Error::other("WinGet 已完成，但仍未找到 FFmpeg，请检查系统 PATH"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_first_absolute_path_entry_containing_the_executable() {
        let folder = tempfile::tempdir().unwrap();
        let first = folder.path().join("first");
        let second = folder.path().join("second");
        for directory in [&first, &second] {
            std::fs::create_dir(directory).unwrap();
            std::fs::write(directory.join(FFMPEG), []).unwrap();
        }

        let paths = [
            std::path::PathBuf::new(),
            folder.path().join("missing"),
            first.clone(),
            second,
        ];
        assert_eq!(find_in_dirs(paths, FFMPEG), Some(first.join(FFMPEG)));
    }

    #[test]
    fn ignores_a_relative_directory_even_if_the_file_exists() {
        let current = env::current_dir().unwrap();
        assert!(current.join("Cargo.toml").is_file());
        assert_eq!(
            find_in_dirs([PathBuf::from("."), current], "Cargo.toml"),
            None
        );
    }

    #[cfg(windows)]
    #[test]
    fn extracts_the_bundled_binary_and_reuses_it() {
        let folder = tempfile::tempdir().unwrap();
        let directory = folder.path().join("bin");
        let path = extract(&directory, b"fake-ffmpeg").unwrap();
        assert_eq!(std::fs::read(&path).unwrap(), b"fake-ffmpeg");
        assert_eq!(extract(&directory, b"fake-ffmpeg"), Some(path.clone()));
        // 大小不同说明换了构建，必须覆盖。
        let replaced = extract(&directory, b"next-build").unwrap();
        assert_eq!(std::fs::read(replaced).unwrap(), b"next-build");
    }

    #[cfg(windows)]
    #[test]
    fn no_bundle_means_no_extraction() {
        let folder = tempfile::tempdir().unwrap();
        assert_eq!(extract(folder.path(), &[]), None);
        assert!(!folder.path().join(FFMPEG).exists());
    }
}
