# Wordy

<img width="1112" height="148" alt="overlay" src="https://github.com/user-attachments/assets/a7ffc412-692c-49af-9385-2e19d31bd91e" />


常驻热键输入 → TTS 生成 → 播放：支持通过 Windows 原生“侦听此设备”和 VB-CABLE，将 TTS 与麦克风输出到同一虚拟设备。

快捷键聊天轮盘：按住热键唤出八向轮盘，松开即播放选中的音频，或用当前音色朗读选中的文字。输出跟随当前播放设备；启用音频侦听时同样进入 VB-CABLE。

## 环境要求

- Windows 10/11
- VB-CABLE（可选，[音频侦听](#音频侦听vb-cable) 功能需要）
- [uv](https://docs.astral.sh/uv/)：运行源码需要

## 功能介绍

### 输入框朗读

- 按 **F6** 唤出或收起悬浮输入框，回车朗读，Esc 收起
- 朗读可打断：再次提交、选择轮盘或播放音频都会停止当前朗读

### 聊天轮盘

- 按住 **F8** 唤出八向轮盘，松开立即播放；移到中心、按 Esc 或右键取消
- 每槽可为音频（导入、录制电脑声音、截取片段）或文字（以当前音色朗读，可打断）
- 每槽独立音量，范围 0–200%
- 可新增多个轮盘，各自绑定快捷键
- 四种样式：经典玻璃、轻薄玻璃、悬浮花瓣、竞技轮盘

### TTS 服务商

支持 Volcengine 与 Cartesia。在设置 → **TTS 设置** 中填写对应 API Key 并选择音色，密钥保存在系统凭据库（keyring），不写入配置文件。Volcengine 没有音色枚举接口，音色列表需在设置页手动添加。

### 返听

在设置 → **音频侦听** 中勾选「启用返听」，TTS 播放时同步输出到默认播放设备，启用音频侦听后自己也能听到。

### 音频侦听（VB-CABLE）

音频侦听将 TTS 直接输出到 CABLE Input，并让 Windows 把选定麦克风侦听到同一端点。其他应用选择 CABLE Output 即可接收两路声音。Wordy 不采集麦克风，也不维护软件混音流。

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
# 同步依赖（含 pytest）
uv sync

# 运行测试
uv run python -m pytest tests/ -v

# 类型检查
uv run python -m pyright src/ tests/

# 添加新依赖
uv add <package>
uv add --dev <dev-package>
```

### 快速开始

```powershell
# 1. 初始化环境（自动安装 Python 3.12 + 依赖）
.\wordy.ps1 init

# 2. 运行
.\wordy.ps1 dev
```

首次运行 `wordy.ps1 init` 时，uv 自动下载 Python 3.12、创建 `.venv`、安装全部依赖。再次运行会同步依赖变更。

`wordy.ps1` 不带参数时默认启动应用。保留的 `dev.cmd` 也可双击启动，或转发参数（例如 `dev.cmd build -Clean -NoPause`）。

```powershell
# 打包为 dist/Wordy.exe
.\wordy.ps1 build -Clean -NoPause
```

`init` 和 `build` 默认结束后等待回车，自动化执行时加 `-NoPause`；`-Clean` 清理 PyInstaller 缓存。初始化时，仅在 `.python-version` 不存在时使用 `-PythonVersion`（默认 `3.12`）固定版本。

### 项目结构

| 路径 | 说明 |
|------|------|
| `src/wordy/main.py` | 应用入口与生命周期管理 |
| `src/wordy/audio/` | 设备枚举（`capture`）、播放与返听（`player`/`sidetone`）、VB-CABLE 检测（`driver`）、路由引擎（`router`）、Windows 侦听策略（`listen_policy`）、系统声音录制（`recorder`）、裁剪与波形（`trim`/`waveform`）、托管音频导入（`clips`） |
| `src/wordy/tts/` | 引擎抽象（`engine`）、常量（`constants`）、Cartesia bytes/realtime 后端（`cartesia`）、Volcengine 后端（`volcengine`）、注册表（`registry`）、语音标签（`labels`） |
| `src/wordy/ui/` | 悬浮输入框（`overlay`/`overlay_widgets`）、设置窗口（`settings` + `settings_state`/`settings_widgets`/`settings_style`）、聊天轮盘（`chat_wheel`/`chat_wheel_settings`/`wheel_paint`）、音频截取（`audio_trim_dialog`/`audio_waveform`）、玻璃材质与主题（`glass`/`theme`）、无边框窗口工具（`window`）、托盘（`tray`）、反馈提示（`status_toast`）、TTS 面板（`tts_panels`） |
| `src/wordy/hotkey/` | keyboard 风格快捷键解析（`parser`）+ Win32 原生全局热键监听（`native`） |
| `src/wordy/` 模块级 | 配置（`config`）、全局常量（`constants`）、密钥存储（`secret`）、轮盘控制（`chat_wheel_controller`）、TTS 任务（`tts_manager`）、路由控制（`routing_controller`）、目录解析（`paths`）、日志（`log`）、设备标识（`identity`）、单实例（`single_instance`）、Qt 安全调用（`qt_lifecycle`） |
| `pyproject.toml` | 项目元数据与依赖声明 |

### 技术栈

- **Python 3.12**（uv 管理，`.python-version` 锁定）
- **GUI**: PySide6（自绘玻璃窗口，无原生标题栏）
- **音频**: sounddevice（设备枚举）+ pyaudio（播放）+ pyaudiowpatch（WASAPI 回环录制）+ numpy（PCM 处理）
- **路由**: Windows Core Audio 侦听策略（COM/WASAPI，comtypes）+ VB-CABLE
- **TTS**: Cartesia SDK（`cartesia[websockets]`）、Volcengine HTTP/SSE（requests）
- **其他**: keyring（密钥存储）、keyboard（快捷键录制）
- **包管理**: uv

## 许可证

[MIT](LICENSE)
