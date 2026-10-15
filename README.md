# Wordy

常驻热键输入 → TTS 生成 → 播放。支持通过 Windows 原生“侦听此设备”和 VB-CABLE，将 TTS 与麦克风输出到同一虚拟设备。

## 环境要求

- **uv**（Python 包管理器，[安装指南](#安装-uv)）
- Windows 10/11
- VB-CABLE（可选，[音频侦听](#音频侦听vb-cable) 功能需要）

## 快速开始

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

## 安装 uv

```powershell
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"
```

或参考 [uv 官方安装文档](https://docs.astral.sh/uv/getting-started/installation/)。

## 音频侦听（VB-CABLE）

音频侦听将 TTS 直接输出到 CABLE Input，并让 Windows 把选定麦克风侦听到同一端点。其他应用选择 CABLE Output 即可接收两路声音。Wordy 不采集麦克风，也不维护软件混音流。

### 安装 VB-CABLE

1. 前往 <https://vb-audio.com/Cable/>
2. 下载 `VBCABLE_Driver_Pack45.zip`
3. 解压后右键 `VBCABLE_Setup_x64.exe` → 以管理员身份运行
4. 重启电脑

### 启用音频侦听

1. 启动应用后打开设置 → **音频侦听** 选项卡
2. 勾选"启用音频侦听"
3. 选择要侦听的麦克风

设置会立即保存并生效。停用路由或退出应用时，Wordy 会恢复麦克风原有的 Windows 侦听状态。

## 开发

```powershell
# 同步依赖（含 pytest）
uv sync

# 运行测试
uv run python -m pytest tests/ -v

# 添加新依赖
uv add <package>
uv add --dev <dev-package>
```

## 项目结构

| 文件 | 说明 |
|------|------|
| `src/wordy/main.py` | 应用入口，生命周期管理 |
| `src/wordy/audio/router.py` | Windows 原生音频侦听管理 |
| `src/wordy/audio/capture.py` | 麦克风输入设备枚举（sounddevice） |
| `src/wordy/audio/player.py` | WAV/PCM 播放器（pyaudio） |
| `src/wordy/audio/driver.py` | VB-CABLE 驱动检测 |
| `src/wordy/ui/overlay.py` | 热键输入悬浮窗 |
| `src/wordy/ui/settings.py` | 设置界面（PySide6） |
| `src/wordy/config.py` | 配置持久化 |
| `pyproject.toml` | 项目元数据与依赖声明 |

## 技术栈

- **Python 3.12**（uv 管理，`.python-version` 锁定）
- **TTS**: Cartesia API
- **音频**: sounddevice + pyaudio
- **路由**: Windows Core Audio 侦听策略 + VB-CABLE
- **GUI**: PySide6
- **包管理**: uv
