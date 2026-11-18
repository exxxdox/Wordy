#![cfg_attr(all(windows, feature = "packaged"), windows_subsystem = "windows")]

#[cfg(windows)]
fn main() -> std::process::ExitCode {
    match wordy::app::run() {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("Wordy 启动失败：{error}");
            if std::env::args_os().nth(1).as_deref() != Some(std::ffi::OsStr::new("--smoke-test")) {
                wordy::instance::show_native_message(&format!("Wordy 启动失败：{error}"));
            }
            std::process::ExitCode::FAILURE
        }
    }
}

#[cfg(not(windows))]
fn main() -> std::process::ExitCode {
    eprintln!("Wordy 仅支持 Windows。");
    std::process::ExitCode::FAILURE
}
