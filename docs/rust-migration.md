# Wordy Rust 迁移

Rust 版使用 Win32 消息循环和 WASAPI。设置、轮盘和录制截取共用配置存储，运行轮盘和两处选槽界面共用 Direct2D 绘制。主播放固定走 VB-CABLE，返听和 F10 本地试听使用独立输出。原有 TOML、Windows 凭据目标名称及 `.wordybackup` 加密格式可直接读取。

设置窗口以原版的无边框玻璃和双栏留白为基准，聊天轮盘直接在设置页操作。悬浮输入框保留原生文本编辑器用于输入法与键盘操作，玻璃、图标和投影由 Rust 绘制。设置临时反馈在页脚上方堆叠，悬停时停留，移开后继续淡出。设置轮盘、热键轮盘和截取目标盘共用同一绘制逻辑。

## Windows 本地构建

需要 Rust MSVC 工具链、Visual Studio C++ Build Tools 和 Windows SDK。运行 `cargo build --locked` 生成开发版。压缩音频及非 PCM16 WAV 依赖同目录的 `ffmpeg.exe`。解码器进程接受本地文件，运行时从应用目录取可执行文件。

`wordy.ps1 build -Clean -NoPause` 使用 `packaged` 特性构建 release，并复制为 `dist\Wordy.exe`；`wordy.ps1 dev` 运行调试版。发布工作流调用同一脚本。Python/Qt 源码与测试已移除，配置、凭据和备份格式保持不变。

## 验收

- `cargo fmt --check`、`cargo clippy --locked --all-targets -- -D warnings` 和 `cargo test --locked --all-targets`。
- 在能绑定本机 TCP 回环的环境运行测试。若已安装 FFmpeg，将 `WORDY_TEST_FFMPEG` 设为其绝对路径，并用 `cargo test --locked --all-targets -- --include-ignored` 加测导入格式。
- 实机核对中文输入法、抢焦点和混合 DPI。用 VB-CABLE、非虚拟返听设备及可用麦克风核对播放、侦听读回、普通录制和 F10 截取保存。
- 使用各自的有效凭据核对三种合成模式。检验切换引擎与停止播放时，队列隔离并释放已按下的语音键。
- 迁移已完成：`wordy.ps1`、发布工作流与 `dist/Wordy.exe` 均使用 Rust。
- 仅影响观感的三项差异记录见 [visual-parity-gaps.md](visual-parity-gaps.md)。
