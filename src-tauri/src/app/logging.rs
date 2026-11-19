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
        let _ = self.handle.emit(crate::ui::LOG_EVENT, message);
    }

    pub(super) fn status(&mut self, message: String) {
        self.toasts.push(&message, Tone::Success);
        self.log(message);
    }

    pub(super) fn report_error(&mut self, action: &str, error: io::Error) {
        if error
            .get_ref()
            .is_some_and(|cause| cause.is::<audio::MissingFfmpeg>())
        {
            self.log_at("WARNING", format!("{action}：{error}"));
            if let Err(error) = self.offer_ffmpeg_install() {
                self.failure(format!("无法安装 FFmpeg：{error}"));
            }
        } else {
            self.failure(format!("{action}：{error}"));
        }
    }

    pub(super) fn offer_ffmpeg_install(&mut self) -> io::Result<()> {
        if self.ffmpeg_installing {
            return Ok(());
        }
        if ffmpeg::find().is_some() {
            self.status("已检测到 FFmpeg，请重试刚才的音频操作".into());
            return Ok(());
        }
        // 截取窗口现在由网页渲染，原生对话框一律挂在本线程的宿主窗口上。
        let owner = if self.settings_visible() {
            self.host
        } else {
            ptr::null_mut()
        };
        let Some(winget) = ffmpeg::winget() else {
            if dialog::confirm(
                owner,
                "缺少 FFmpeg",
                "此音频需要 FFmpeg。系统未找到 WinGet，是否打开 FFmpeg 下载页面？安装后请将其目录加入 PATH。",
                &self.config.settings().ui_font,
            ) {
                self.open_url("https://ffmpeg.org/download.html")?;
            }
            return Ok(());
        };
        if dialog::confirm(
            owner,
            "缺少 FFmpeg",
            "此音频需要 FFmpeg。是否通过 Windows 程序包管理器（WinGet）下载并安装？",
            &self.config.settings().ui_font,
        ) {
            let events = self.jobs.clone();
            // Installation can take time. Let the user keep working and report completion on the UI thread.
            std::thread::Builder::new()
                .name("WordyInstallFfmpeg".into())
                .spawn(move || {
                    let result = ffmpeg::install(&winget);
                    let _ = events.send(Background::FfmpegInstalled(result));
                })?;
            self.ffmpeg_installing = true;
            self.status("正在下载并安装 FFmpeg…".into());
        }
        Ok(())
    }

    pub(super) fn failure(&mut self, message: String) {
        // Report through the toast/tray only; forcing the settings window open
        // on every background error made a plain launch look like a request for it.
        if self.settings_visible() {
            self.toasts.push(&message, Tone::Error);
        } else {
            // Toast cards only exist over the settings window; without it the
            // next tick would clear them before the user could read one.
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
