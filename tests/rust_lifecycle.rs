use std::fs;
use wordy::file_log::FileLog;

#[test]
fn rotates_utf8_record_without_splitting_a_character() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("wordy.log");
    let mut file_log = FileLog::open(path.clone(), 5, 4).unwrap();

    file_log.append("1234").unwrap();
    file_log.append("一二三").unwrap();

    assert_eq!(
        fs::read_to_string(path.with_extension("log.3")).unwrap(),
        "1234\n"
    );
    assert_eq!(
        fs::read_to_string(path.with_extension("log.2")).unwrap(),
        "一"
    );
    assert_eq!(
        fs::read_to_string(path.with_extension("log.1")).unwrap(),
        "二"
    );
    assert_eq!(fs::read_to_string(&path).unwrap(), "三\n");
}

#[test]
fn retains_current_file_and_four_backups_across_reopen() {
    let directory = tempfile::tempdir().unwrap();
    let path = directory.path().join("wordy.log");
    for index in 0..7 {
        FileLog::open(path.clone(), 5, 4)
            .unwrap()
            .append(&format!("{index:04}"))
            .unwrap();
    }

    assert_eq!(fs::read_to_string(&path).unwrap(), "0006\n");
    assert_eq!(
        fs::read_to_string(path.with_extension("log.4")).unwrap(),
        "0002\n"
    );
    assert_eq!(fs::read_dir(directory.path()).unwrap().count(), 5);
}
