use std::fs::{self, File, OpenOptions};
use std::io::{self, Write};
use std::path::PathBuf;

pub struct FileLog {
    path: PathBuf,
    limit: usize,
    backups: usize,
}

impl FileLog {
    pub fn beside_executable() -> io::Result<Self> {
        Self::open(
            std::env::current_exe()?.with_file_name("wordy.log"),
            5 * 1024 * 1024,
            4,
        )
    }

    pub fn open(path: PathBuf, limit: usize, backups: usize) -> io::Result<Self> {
        if limit < 4 || backups == 0 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "日志轮转参数无效",
            ));
        }
        let log = Self {
            path,
            limit,
            backups,
        };
        log.open_file()?;
        Ok(log)
    }

    pub fn append(&mut self, message: &str) -> io::Result<()> {
        let record = format!("{message}\n");
        let mut pending = record.as_str();
        let mut file = self.open_file()?;
        while !pending.is_empty() {
            let mut length = pending.len().min(self.limit);
            while !pending.is_char_boundary(length) {
                length -= 1;
            }
            if file.metadata()?.len() > (self.limit - length) as u64 {
                // Close the current Windows file handle before renaming it.
                drop(file);
                self.rotate()?;
                file = self.open_file()?;
            }
            file.write_all(&pending.as_bytes()[..length])?;
            file.flush()?;
            pending = &pending[length..];
        }
        Ok(())
    }

    fn open_file(&self) -> io::Result<File> {
        OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)
    }

    fn backup(&self, index: usize) -> PathBuf {
        let mut path = self.path.as_os_str().to_os_string();
        path.push(format!(".{index}"));
        PathBuf::from(path)
    }

    fn rotate(&self) -> io::Result<()> {
        ignore_missing(fs::remove_file(self.backup(self.backups)))?;
        for index in (1..self.backups).rev() {
            ignore_missing(fs::rename(self.backup(index), self.backup(index + 1)))?;
        }
        fs::rename(&self.path, self.backup(1))
    }
}

fn ignore_missing(result: io::Result<()>) -> io::Result<()> {
    match result {
        Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
        result => result,
    }
}
