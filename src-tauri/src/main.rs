#![cfg_attr(all(windows, feature = "packaged"), windows_subsystem = "windows")]

#[cfg(windows)]
fn main() -> std::process::ExitCode {
    use std::io;
    use windows_sys::Win32::System::Threading::CreateEventW;
    use windows_sys::Win32::UI::HiDpi::{
        DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2, SetProcessDpiAwarenessContext,
    };

    use wordy::app::UiContext;
    use wordy::instance::{acquire_single_instance, show_duplicate_instance_message};
    use wordy::ui::NativeHandle;

    if std::env::args_os().nth(1).as_deref() == Some(std::ffi::OsStr::new("--smoke-test")) {
        return match wordy::app::smoke_test() {
            Ok(()) => std::process::ExitCode::SUCCESS,
            Err(error) => {
                eprintln!("Wordy 启动失败：{error}");
                std::process::ExitCode::FAILURE
            }
        };
    }
    if !acquire_single_instance().unwrap_or(false) {
        show_duplicate_instance_message();
        return std::process::ExitCode::SUCCESS;
    }
    unsafe { SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2) };

    let outcome = tauri::Builder::default()
        .setup(|app| {
            use tauri::Manager;

            let (requests, receiver) = wordy::ui::channel();
            let wake = unsafe { CreateEventW(std::ptr::null(), 0, 0, std::ptr::null()) };
            if wake.is_null() {
                return Err(io::Error::last_os_error().into());
            }
            let wake_value = wake as isize;
            app.manage(NativeHandle::new(wake_value, requests));
            let dialogs = wordy::ui::DialogBroker::default();
            app.manage(dialogs.clone());

            let handle = app.handle().clone();
            std::thread::Builder::new()
                .name("WordyNative".into())
                .spawn(move || {
                    let context = UiContext {
                        app: handle.clone(),
                        wake: wake_value,
                        requests: receiver,
                        dialogs,
                    };
                    if let Err(error) = wordy::app::run_native(context) {
                        eprintln!("Wordy 启动失败：{error}");
                        wordy::instance::show_native_message(&format!("Wordy 启动失败：{error}"));
                        handle.exit(1);
                    }
                })?;
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            wordy::ui::wordy_snapshot,
            wordy::ui::wordy_listeners_ready,
            wordy::ui::wordy_action,
            wordy::ui::wordy_hide,
            wordy::ui::wordy_logs,
            wordy::ui::wordy_wheel_preview,
            wordy::ui::wordy_dialog_reply
        ])
        .run(tauri::generate_context!());

    match outcome {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("Wordy 启动失败：{error}");
            std::process::ExitCode::FAILURE
        }
    }
}

#[cfg(not(windows))]
fn main() -> std::process::ExitCode {
    eprintln!("Wordy 仅支持 Windows。");
    std::process::ExitCode::FAILURE
}
