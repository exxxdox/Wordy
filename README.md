# Wordy

常驻热键输入 → TTS 生成 → 播放。支持 VB-CABLE 虚拟音频侦听，将 TTS 音频与麦克风实时混音后输出到虚拟设备。

## 环境要求

- **uv**（Python 包管理器，[安装指南](#安装-uv)）
- Windows 10/11
- VB-CABLE（可选，[音频侦听](#音频侦听vb-cable) 功能需要）

## 快速开始

```powershell
# 1. 初始化环境（自动安装 Python 3.12 + 依赖）
.\init.ps1

# 2. 运行
uv run python -m wordy
```

首次运行 `init.ps1` 时，uv 自动下载 Python 3.12、创建 `.venv`、安装全部依赖。之后再次运行只检查依赖是否变更，秒级完成。

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
