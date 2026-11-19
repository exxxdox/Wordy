use std::path::PathBuf;

#[cfg(windows)]
use std::path::Path;

use sha2::{Digest, Sha256};

const FFMPEG: &str = if cfg!(windows) {
    "ffmpeg.exe"
} else {
    "ffmpeg"
};

/// 构建时嵌入的精简 FFmpeg；没有放 assets/ffmpeg.exe 时为空。
const BUNDLED: &[u8] = include_bytes!(concat!(env!("OUT_DIR"), "/ffmpeg.exe"));

/// 只使用随 exe 打包的精简 FFmpeg：解包到配置目录后复用。
/// 不查找系统安装，也不联网安装；解包失败即视为没有解码器。
pub(crate) fn find() -> Option<PathBuf> {
    #[cfg(windows)]
    {
        extract(&crate::config::config_directory().join("bin"), BUNDLED)
    }
    #[cfg(not(windows))]
    {
        None
    }
}

/// 已解包的文件按 SHA-256 判断是否仍是同一份，避免只看大小放过损坏文件。
#[cfg(windows)]
fn extract(directory: &Path, bytes: &[u8]) -> Option<PathBuf> {
    if bytes.is_empty() {
        return None;
    }
    let path = directory.join(FFMPEG);
    if file_matches(&path, bytes) {
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

#[cfg(windows)]
fn file_matches(path: &Path, bytes: &[u8]) -> bool {
    // 先比大小：不同就不用读整份文件。
    if std::fs::metadata(path).map_or(true, |meta| meta.len() != bytes.len() as u64) {
        return false;
    }
    let Ok(existing) = std::fs::read(path) else {
        return false;
    };
    Sha256::digest(&existing)[..] == Sha256::digest(bytes)[..]
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn replaces_a_file_with_the_same_size_but_different_content() {
        let folder = tempfile::tempdir().unwrap();
        let directory = folder.path().join("bin");
        std::fs::create_dir_all(&directory).unwrap();
        std::fs::write(directory.join(FFMPEG), b"aaaa").unwrap();
        let path = extract(&directory, b"bbbb").unwrap();
        assert_eq!(std::fs::read(path).unwrap(), b"bbbb");
    }

    #[cfg(windows)]
    #[test]
    fn no_bundle_means_no_extraction() {
        let folder = tempfile::tempdir().unwrap();
        assert_eq!(extract(folder.path(), &[]), None);
        assert!(!folder.path().join(FFMPEG).exists());
    }
}
