use super::*;

impl App {
    pub(super) fn id(&mut self) -> u64 {
        self.next_job += 1;
        self.next_job
    }

    pub(super) fn log(&mut self, message: String) {
        self.log_at("INFO", message);
    }

    pub(super) fn log_at(&mut self, level: &str, message: String) {
        self.log_target(level, "wordy", message);
    }

    pub(super) fn log_target(&mut self, level: &str, target: &str, message: String) {
        let levels = crate::config::LOG_LEVELS;
        let rank = |value| levels.iter().position(|item| *item == value).unwrap_or(1);
        if rank(level) < rank(self.config.settings().log_level.as_str()) {
            return;
        }
        let now = local_time();
        let message = format!(
            "{:04}-{:02}-{:02} {:02}:{:02}:{:02} [{level}] {target}: {message}",
            now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond
        );
        eprintln!("{message}");
        let trim = self.logs.len() == LOG_LIMIT;
        if trim {
            self.logs.pop_front();
        }
        if let Some(log) = &mut self.file_log
            && let Err(error) = log.append(&message)
        {
            eprintln!("写入日志失败：{error}");
        }
        self.logs.push_back(message.clone());
        // 设置页打开日志面板时依赖逐行事件，而不是整份历史。
        if self.ui_connected {
            let _ = self.handle.emit(crate::ui::LOG_EVENT, message);
        }
    }

    pub(super) fn status(&mut self, message: String) {
        self.feedback(&message, "success");
        self.log(message);
    }

    pub(super) fn report_error(&mut self, action: &str, error: io::Error) {
        self.failure(format!("{action}：{error}"));
    }

    pub(super) fn failure(&mut self, message: String) {
        if self.exiting {
            self.log_at("ERROR", message);
            return;
        }
        // Report through the toast/tray only; forcing the settings window open
        // on every background error made a plain launch look like a request for it.
        if self.settings_visible() && self.feedback_ready {
            self.feedback(&message, "error");
        } else {
            self.tray.notify(&message);
        }
        self.log_at("ERROR", message);
    }

    pub(super) fn notify(&mut self, message: String) {
        let events = self.jobs.clone();
        let text = message.clone();
        // Native toasts shell out through PowerShell; keep the GUI thread free.
        let _ = std::thread::Builder::new()
            .name("WordyToast".into())
            .spawn(move || {
                if notify_toast(&text).is_err() {
                    let _ = events.send(Background::NotificationFailed(text));
                }
            });
        self.log(message);
    }
}
