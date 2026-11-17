# Wordy

<img width="1112" height="148" alt="overlay" src="https://github.com/user-attachments/assets/a7ffc412-692c-49af-9385-2e19d31bd91e" />


常驻热键输入 → TTS 生成 → 播放：支持通过 Windows 原生“侦听此设备”和 VB-CABLE，将 TTS 与麦克风输出到同一虚拟设备。

快捷键聊天轮盘：按住热键唤出八向轮盘，松开即播放选中的音频，或用当前音色朗读选中的文字。输出跟随当前播放设备；启用音频侦听时同样进入 VB-CABLE。

## 环境要求

- Windows 10/11
- VB-CABLE（可选，[音频侦听](#音频侦听vb-cable) 功能需要）
- Rust stable（MSVC 工具链）：运行与打包

## 功能介绍

### 输入框朗读

- 按 **F6** 唤出或收起悬浮输入框，回车朗读，Esc 收起
- 朗读可打断：再次提交、选择轮盘或播放音频都会停止当前朗读

### 聊天轮盘

- 按住 **F8** 唤出八向轮盘，松开立即播放；移到中心、按 Esc 或右键取消
- 每槽可为音频（导入、录制电脑声音、截取片段）或文字（以当前音色朗读，可打断）
- 每槽独立音量，范围 0–300%
- 可新增多个轮盘，各自绑定快捷键
- 四种样式：经典玻璃、轻薄玻璃、悬浮花瓣、竞技轮盘

### TTS 服务商

支持 Volcengine 与 Cartesia。在设置 → **TTS 设置** 中填写对应 API Key 并选择音色，密钥保存在系统凭据库（keyring），不写入配置文件。Volcengine 没有音色枚举接口，音色列表需在设置页手动添加。

### 返听

在设置 → **音频设置** 中选择「播放设备」，例如耳机或扬声器。F10 录制后的试听使用该设备。启用音频侦听后，勾选「启用返听」可让朗读和轮盘音频同时通过该设备播放。

### 音频侦听（VB-CABLE）

安装 VB-CABLE 后，「麦克风输入」自动选择 CABLE Input。音频侦听将 TTS 直接输出到 CABLE Input，并让 Windows 把选定麦克风侦听到同一端点。其他应用选择 CABLE Output 即可接收两路声音。Wordy 不采集麦克风，也不维护软件混音流。

#### 安装 VB-CABLE

1. 前往 <https://vb-audio.com/Cable/>
2. 下载 `VBCABLE_Driver_Pack45.zip`
3. 解压后右键 `VBCABLE_Setup_x64.exe` → 以管理员身份运行
4. 重启电脑

#### 启用音频侦听

1. 启动应用后打开设置 → **音频侦听** 选项卡
2. 勾选"启用音频侦听"
3. 选择要侦听的麦克风

设置会立即保存并生效。停用路由或退出应用时，Wordy 会恢复麦克风原有的 Windows 侦听状态。

## 运行发布版

打包产物为 `dist/Wordy.exe`，双击运行，不带控制台窗口。同一 Windows 会话只允许运行一个实例。

- 配置与轮盘音频：`$XDG_CONFIG_HOME/wordy`，未设置时为 `%USERPROFILE%\.config\wordy`，内含 `config.toml` 与 `audio_clips/`
- 日志：`wordy.log` 写在 exe 同目录，单文件上限 5 MiB，含备份最多 5 个文件；源码运行只输出到控制台

## 开发

```powershell
# 拉取依赖
cargo fetch

# 运行测试
cargo test

# 运行应用（保留控制台日志）
cargo run

# 添加新依赖
cargo add <crate>
```

### 快速开始

```powershell
# 1. 初始化环境
.\wordy.ps1 init

# 2. 运行
.\wordy.ps1 dev
```

`wordy.ps1` 不带参数时默认启动应用。保留的 `dev.cmd` 也可双击启动，或转发参数（例如 `dev.cmd build -Clean -NoPause`）。

```powershell
# 打包为 dist/Wordy.exe（release + packaged，无控制台窗口）
.\wordy.ps1 build -Clean -NoPause
```

`init` 和 `build` 默认结束后等待回车，自动化执行时加 `-NoPause`；`-Clean` 执行 `cargo clean`。

### 项目结构

| 路径 | 说明 |
|------|------|
| `rust/main.rs` / `rust/app.rs` | 进程入口、单实例、热键分发与窗口消息循环 |
| `rust/audio.rs` + `rust/audio/` | WASAPI 设备枚举、播放/返听、VB-CABLE 路由、Windows 侦听策略、回环录制、裁剪与波形、托管音频 |
| `rust/tts.rs` + `rust/tts/` | TTS 引擎、Cartesia HTTP/WebSocket、Volcengine SSE、音色标签 |
| `rust/desktop.rs` | 悬浮输入框、托盘、Windows 通知 |
| `rust/settings_window.rs`/`panels.rs` | 设置窗口与轮盘编辑器（DPI 缩放、玻璃外壳） |
| `rust/wheel*.rs` | 轮盘运行窗口、四种样式绘制、托盘与设置预览 |
| `rust/config.rs`/`backup*.rs`/`credentials.rs` | 配置持久化、加密备份、凭据库 |
| `rust/hotkey.rs`/`instance.rs`/`file_log.rs` | 快捷键解析与注册、单实例、日志轮转 |
| `rust/log_window.rs`/`status_toast.rs`/`dialog.rs` | 日志窗口、反馈提示、原生对话框 |

### 技术栈

- **Rust（edition 2024）**，`windows-sys` / `windows` 直接调用 Win32、Direct2D、WASAPI
- **GUI**: 自绘 Win32 窗口（玻璃外壳、悬浮输入框、轮盘）
- **音频**: WASAPI（设备、回环录制、Windows 侦听策略）
- **TTS**: reqwest（Cartesia bytes、Volcengine SSE）、tungstenite（Cartesia realtime）
- **密钥**: Windows 凭据管理器（Credential Manager）
- **包管理**: Cargo

## 许可证

[MIT](LICENSE)
