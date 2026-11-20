use std::fs;
use std::io;
use std::path::Path;
use std::time::{Duration, Instant, SystemTime};

use crate::backup::is_auto_backup_name;

pub(super) struct AutoBackupSchedule {
    started: Instant,
    previous_age: Option<Duration>,
}

impl AutoBackupSchedule {
    pub(super) fn new(previous_age: Option<Duration>) -> Self {
        Self {
            started: Instant::now(),
            previous_age,
        }
    }

    pub(super) fn reset(&mut self) {
        *self = Self::new(Some(Duration::ZERO));
    }

    pub(super) fn is_due(&self, interval: Duration, pending_saves: usize) -> bool {
        // Keep disk age separate: a backup can predate this boot's monotonic clock.
        pending_saves == 0
            && self
                .previous_age
                .is_none_or(|age| age.saturating_add(self.started.elapsed()) >= interval)
    }
}

pub(super) fn previous_age(automatic: &Path, now: SystemTime) -> io::Result<Option<Duration>> {
    let entries = match fs::read_dir(automatic) {
        Ok(entries) => entries,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(error),
    };
    let mut latest = None;
    for entry in entries {
        let entry = entry?;
        if entry.file_name().to_str().is_some_and(is_auto_backup_name)
            && entry.file_type()?.is_file()
            && let Ok(age) = now.duration_since(entry.metadata()?.modified()?)
        {
            latest = Some(latest.map_or(age, |previous: Duration| previous.min(age)));
        }
    }
    Ok(latest)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs::{File, FileTimes};
    use std::time::UNIX_EPOCH;

    #[test]
    fn resumes_from_the_newest_automatic_backup_across_restart() {
        let temp = tempfile::tempdir().unwrap();
        let legacy = temp.path();
        let auto = legacy.join("auto");
        fs::create_dir(&auto).unwrap();
        let now = UNIX_EPOCH + Duration::from_secs(1_800_000_000);
        let age = Duration::from_secs(2 * 3600);
        write_backup(
            &auto.join("auto-20261001-010000.wordybackup"),
            now - age - Duration::from_secs(3600),
        );
        write_backup(&auto.join("auto-20261001-020000.wordybackup"), now - age);
        write_backup(&legacy.join("auto-manual.wordybackup"), now);
        write_backup(
            &auto.join("auto-20261001-030000.wordybackup"),
            now + Duration::from_secs(3600),
        );

        let last = previous_age(&auto, now).unwrap();
        assert_eq!(last, Some(age));
        let mut schedule = AutoBackupSchedule::new(last);
        assert!(schedule.is_due(age, 0));
        assert!(!schedule.is_due(age, 1));
        let next_hour = age + Duration::from_secs(3600);
        assert!(!schedule.is_due(next_hour, 0));
        schedule.reset();
        assert!(!schedule.is_due(age, 0));
    }

    #[test]
    fn ignores_ambiguous_legacy_filenames_and_starts_due_when_none_exist() {
        let temp = tempfile::tempdir().unwrap();
        let legacy = temp.path();
        let auto = legacy.join("auto");
        let now = UNIX_EPOCH + Duration::from_secs(1_800_000_000);
        let age = Duration::from_secs(3600);
        write_backup(&legacy.join("auto-20261001-020000.wordybackup"), now - age);
        let last = previous_age(&auto, now).unwrap();
        assert_eq!(last, None);
        assert!(AutoBackupSchedule::new(last).is_due(age, 0));
    }

    fn write_backup(path: &Path, modified: SystemTime) {
        File::create(path)
            .unwrap()
            .set_times(FileTimes::new().set_modified(modified))
            .unwrap();
    }
}
