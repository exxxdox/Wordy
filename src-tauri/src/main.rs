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

            let window = tauri::WebviewWindowBuilder::new(
                app,
                "settings",
                tauri::WebviewUrl::App("index.html".into()),
            )
            .title("Wordy 设置")
            .inner_size(1120.0, 820.0)
            .min_inner_size(960.0, 640.0)
            .theme(Some(tauri::Theme::Dark))
            // 与原应用一致：启动只驻留托盘，设置窗口由托盘或悬浮输入框唤出。
            .visible(false)
            .build()?;
            let hwnd = window.hwnd().map(|handle| handle.0 as isize).unwrap_or(0);

            let (requests, receiver) = wordy::ui::channel();
            let wake = unsafe { CreateEventW(std::ptr::null(), 0, 0, std::ptr::null()) };
            if wake.is_null() {
                return Err(io::Error::last_os_error().into());
            }
            let wake_value = wake as isize;
            app.manage(NativeHandle::new(wake_value, requests));

            let handle = app.handle().clone();
            let closable = window.clone();
            window.on_window_event(move |event| {
                if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                    // 关闭设置窗口只隐藏，托盘应用继续运行。
                    api.prevent_close();
                    let _ = closable.hide();
                }
            });

            std::thread::Builder::new()
                .name("WordyNative".into())
                .spawn(move || {
                    let context = UiContext {
                        app: handle.clone(),
                        hwnd,
                        wake: wake_value,
                        requests: receiver,
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
            wordy::ui::wordy_action,
            wordy::ui::wordy_hide
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
