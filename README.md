# Wordy

常驻热键输入 → TTS 生成 → 播放。支持 VB-CABLE 虚拟音频侦听，将 TTS 音频与麦克风实时混音后输出到虚拟设备。

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

音频侦听功能可将 TTS 输出与麦克风、桥接音源实时混音，输出到虚拟音频设备（其他应用如 Discord/OBS 可捕获）。

### 安装 VB-CABLE

1. 前往 <https://vb-audio.com/Cable/>
2. 下载 `VBCABLE_Driver_Pack45.zip`
3. 解压后右键 `VBCABLE_Setup_x64.exe` → 以管理员身份运行
4. 重启电脑

### 启用音频侦听

1. 启动应用后打开设置 → **音频侦听** 选项卡
2. 勾选"启用音频侦听"
3. 选择麦克风、桥接源设备
4. 点击"应用"

## 开发

```powershell
# 同步依赖（含 pytest）
uv sync

# 运行测试
uv run pytest tests/ -v

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
- **混音**: numpy 向量化处理
- **GUI**: PySide6
- **包管理**: uv
