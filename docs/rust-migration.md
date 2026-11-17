# Wordy Rust 迁移

Rust 版使用 Win32 消息循环和 WASAPI。设置、轮盘和录制截取共用配置存储，运行轮盘和两处选槽界面共用 Direct2D 绘制。主播放固定走 VB-CABLE，返听和 F10 本地试听使用独立输出。原有 TOML、Windows 凭据目标名称及 `.wordybackup` 加密格式可直接读取。

## Windows 本地构建

需要 Rust MSVC 工具链、Visual Studio C++ Build Tools 和 Windows SDK。运行 `cargo build --locked` 生成开发版。压缩音频及非 PCM16 WAV 依赖同目录的 `ffmpeg.exe`。解码器进程接受本地文件，运行时从应用目录取可执行文件。

运行 `rust/build.ps1` 时通过 `-FfmpegPath` 指定实际的解码器可执行文件，`-FfmpegSha256` 可校验已知摘要。脚本用 `packaged` 特性构建并启动窗口初始化检查，结果写入 `dist/wordy-rust`。运行 `Wordy.exe` 时保留同目录的 `ffmpeg.exe`。该脚本用于并行验证 Rust 版，正式发布仍走原工作流。

## 验收

- `cargo fmt --check`、`cargo clippy --locked --all-targets -- -D warnings` 和 `cargo test --locked --all-targets`。
- 在能绑定本机 TCP 回环的环境运行测试。若已安装 FFmpeg，将 `WORDY_TEST_FFMPEG` 设为其绝对路径，并用 `cargo test --locked --all-targets -- --include-ignored` 加测导入格式。
- 实机核对中文输入法、抢焦点和混合 DPI。用 VB-CABLE、非虚拟返听设备及可用麦克风核对播放、侦听读回、普通录制和 F10 截取保存。
- 使用各自的有效凭据核对三种合成模式。检验切换引擎与停止播放时，队列隔离并释放已按下的语音键。
- 确认 FFmpeg 可分发包和桌面交互等价后，再将 `wordy.ps1` 和标签发布切换到 Rust。
