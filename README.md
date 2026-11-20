# Wordy

<img width="1112" height="148" alt="Wordy 悬浮输入框" src="https://github.com/user-attachments/assets/a7ffc412-692c-49af-9385-2e19d31bd91e" />


常驻热键输入 → TTS 生成 → 播放：支持通过 Windows 原生“侦听此设备”和 VB-CABLE，将 TTS 与麦克风输出到同一虚拟设备。

快捷键聊天轮盘：按住热键唤出八向轮盘，松开即播放选中的音频，或用当前音色朗读选中的文字。音频与朗读固定输出到 CABLE Input；开启返听时同时输出到本机播放设备。

## 环境要求

- Windows 10/11
- VB-CABLE（主播放输出与[音频侦听](#音频侦听vb-cable)需要；可在设置 → **音频** 中关闭「启用主播放输出」）
- Rust stable（MSVC 工具链）：源码开发与打包
- Node.js 20+ 与 pnpm、`cargo install tauri-cli --locked --version "^2"`：设置界面（Tauri + React）开发与打包

## 功能介绍

### 输入框朗读

- 按 **F6** 唤出或收起悬浮输入框，回车朗读，Esc 收起
- 朗读可打断：再次提交、选择轮盘或播放音频都会停止当前朗读

### 聊天轮盘

- 按住 **F8** 唤出八向轮盘，松开立即播放；移到中心、按 Esc 或右键取消
- 每槽可同时保存音频（导入、录制电脑声音、截取片段）与文字（以当前音色朗读，可打断），槽位的「内容类型」决定播放哪一类，切换不丢失另一类
- 音频导入支持常见格式；转码使用随 exe 打包的精简 FFmpeg，首次使用时自动解包到配置目录，无需另行安装
- 每槽独立音量，范围 0–300%
- 可新增多个轮盘，各自绑定快捷键
- 设置 → **轮盘** → **整理音频**。在左侧选轮盘，从右侧音频库搜索音频并拖到轮盘扇区，也可以先选音频再点扇区。替换音频或清空槽位后，原音频仍可在库中找回。在音频库确认删除会永久删除文件，并移除它在各轮盘的引用。整理音频和从库中删除音频都会保留槽位中的文字。
- 竞技轮盘样式，设置页预览与运行时一致

### 后台录制

按 **F10** 开始，循环保留最近一段时间的电脑声音（默认 30 秒，可在设置 → **后台录制** 中改为 5–300 秒）；再次按下停止并打开截取窗口，拖选范围、试听后保存到指定轮盘的槽位。

### TTS 服务商

支持 Volcengine 与 Cartesia。在设置 → **TTS** 中填写对应 API Key 并选择音色，密钥保存在 Windows 凭据管理器，不写入配置文件。Volcengine 没有音色枚举接口，音色列表需在设置页手动添加。

### 返听

在设置 → **音频** → 播放与返听 中选择「返听播放设备」，例如耳机或扬声器。勾选「启用返听」后，朗读和轮盘音频同时通过该设备播放；截取窗口的试听也使用该设备。返听独立开关，不需要开启音频侦听。

### 播放时自动按住某键

在设置 → **通用** 中开启「播放时自动按住某键」并录制按键。播放前按下该键，朗读结束、失败或取消后松开，适合按住说话类软件。

### 音频侦听（VB-CABLE）

安装 VB-CABLE 后，主播放输出固定送到 CABLE Input（「侦听输出设备」只读显示该端点）；音频侦听再让 Windows 把选定麦克风侦听到同一端点。其他应用选择 CABLE Output 即可接收两路声音。Wordy 不采集麦克风，也不维护软件混音流。

#### 安装 VB-CABLE

1. 前往 <https://vb-audio.com/Cable/>
2. 下载 `VBCABLE_Driver_Pack45.zip`
3. 解压后右键 `VBCABLE_Setup_x64.exe` → 以管理员身份运行
4. 重启电脑

#### 启用音频侦听

1. 启动应用后打开设置 → **音频** → 麦克风与侦听
2. 勾选「启用音频侦听」
3. 在「侦听麦克风」中选择要侦听的麦克风

设置会立即保存并生效。关闭侦听会同时关闭该麦克风的 Windows 侦听；退出应用时保留 Windows 当前的侦听状态。

## 运行发布版

从 [Releases](https://github.com/exxxdox/Wordy/releases) 下载 `Wordy.exe`，或自行打包生成 `dist/Wordy.exe`。双击运行，不带控制台窗口。同一 Windows 会话只允许运行一个实例。

- PCM16 WAV 和后台录制可直接使用。其他格式由内置的精简 FFmpeg 转码，首次使用时解包到配置目录 `bin/ffmpeg.exe`，不依赖系统安装
- 配置与轮盘音频：`$XDG_CONFIG_HOME/wordy`，未设置时为 `%USERPROFILE%\.config\wordy`，内含 `config.toml`、`audio_clips/` 与解包出的 `bin/ffmpeg.exe`
- 日志：`wordy.log` 写在 exe 同目录，单文件上限 5 MiB，含备份最多 5 个文件；源码运行只输出到控制台

### 配置备份

设置页「备份」页签可手动导出或导入 `.wordybackup`，也可开启定时备份（1–168 小时间隔，保留 1–100 份）。手动备份默认保存在配置目录的 `backup` 文件夹，自动备份保存在其中的 `auto` 子目录。Wordy 会在启动后补做已到期的自动备份，仅清理 `auto` 中自动生成的旧备份。备份包含全部设置、轮盘音频、音频库中的已命名文件与两家 TTS 的 API Key，使用 AES-256-GCM 加密；手动导出时须设置密码且不保存密码，自动备份使用单独保存的备份密码。导入成功后应用退出，请重新启动。导入中断时，Wordy 会在下次启动时恢复到一致状态。导入会替换原轮盘配置及其引用的音频。页签同时显示未引用音频数量，可一键清理。

## 开发

```powershell
# 初始化：拉取 Cargo 依赖并安装前端依赖
.\wordy.ps1 init

# 运行：同时启动 Vite 与 Tauri，保留控制台日志
.\wordy.ps1 dev

# 运行测试
cargo test --manifest-path src-tauri/Cargo.toml

# 打包为 dist/Wordy.exe（release + packaged，无控制台窗口）
.\wordy.ps1 build -Clean -NoPause
```

`wordy.ps1` 不带参数时默认启动应用。`dev.cmd` 也可双击启动，或转发参数（例如 `dev.cmd build -Clean -NoPause`）。

`init` 和 `build` 默认结束后等待回车，自动化执行时加 `-NoPause`；`-Clean` 执行 `cargo clean`。

添加依赖：`cargo add --manifest-path src-tauri/Cargo.toml <crate>` 或 `pnpm add <pkg>`。

首次启动 `pnpm dev` 时 Tailwind 会扫描项目并编译一次样式，第一个 CSS 请求可能等待 10–40 秒；之后热更新恢复为秒级。`.\wordy.ps1 dev -Port 1421` 可指定起始端口；端口被占用时会自动顺延到下一个空闲端口。

### 项目结构

| 路径 | 说明 |
|------|------|
| `src-tauri/src/main.rs` | Tauri 入口：单实例、WebView 设置窗口、启动原生工作线程 |
| `src-tauri/src/app.rs` | 原生工作线程：热键分发、窗口消息循环、设置动作、日志 |
| `src-tauri/src/ui.rs` | WebView 与原生线程的桥：动作枚举、配置快照、命令 |
| `src-tauri/src/app/recording.rs` | 后台录制、音频截取、试听和保存 |
| `src-tauri/src/audio.rs` + `src-tauri/src/audio/` | WASAPI 设备枚举、播放/返听、VB-CABLE 路由、Windows 侦听策略、回环录制、裁剪与波形、托管音频 |
| `src-tauri/src/tts.rs` + `src-tauri/src/tts/` | TTS 引擎、Cartesia HTTP/WebSocket、Volcengine SSE、音色标签 |
| `src-tauri/src/desktop.rs` | 宿主窗口、托盘、Windows 通知 |
| `src-tauri/src/desktop/overlay.rs` | 悬浮输入框和绘制 |
| `src-tauri/src/fonts.rs` | 系统字体枚举与回退 |
| `src-tauri/src/app/trim.rs` | React 截取窗口的原生会话：临时 WAV、波形、试听与保存 |
| `src-tauri/src/wheel*.rs` | 轮盘运行窗口、竞技轮盘绘制与设置预览 |
| `src-tauri/src/playback.rs` / `ffmpeg.rs` | 播放调度；内置 FFmpeg 的解包与复用 |
| `src-tauri/src/config.rs` / `backup*.rs` / `credentials.rs` | 配置持久化、加密备份、凭据库 |
| `src-tauri/src/hotkey.rs` / `instance.rs` / `file_log.rs` | 快捷键解析与注册、单实例、日志轮转 |
| `src-tauri/src/dialog.rs` | 系统文件选择框；其余弹窗与提示由 React 渲染 |
| `src-tauri/assets/ffmpeg.exe` | 随 exe 打包的精简 FFmpeg，构建时经 `include_bytes!` 嵌入 |
| `src/` | React 设置界面（Vite + Tailwind），构建到 `dist-ui/` 后嵌入 exe |
| `src-tauri/tauri.conf.json` | 窗口、构建命令、CSP 与图标配置 |

### 技术栈

- **Rust（edition 2024）**，`windows-sys` / `windows` 直接调用 Win32、Direct2D、WASAPI
- **GUI**: 设置页、日志、轮盘编辑器与截取窗口为 Tauri 2 + React 19 + Tailwind；托盘、悬浮输入框、运行时轮盘为原生 Win32
- **音频**: WASAPI（设备、回环录制、Windows 侦听策略）
- **TTS**: reqwest（Cartesia bytes、Volcengine SSE）、tungstenite（Cartesia realtime）
- **密钥**: Windows 凭据管理器（Credential Manager）
- **包管理**: Cargo + pnpm

## 许可证

[MIT](LICENSE)
