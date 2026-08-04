# Easy TTS

常驻热键输入 → TTS 生成 → 播放。支持 VB-CABLE 虚拟音频路由，将 TTS 音频与麦克风实时混音后输出到虚拟设备。

## 环境要求

- **uv**（Python 包管理器，[安装指南](#安装-uv)）
- Windows 10/11
- VB-CABLE（可选，[音频路由](#音频路由vb-cable) 功能需要）

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

## 音频路由（VB-CABLE）

音频路由功能可将 TTS 输出与麦克风、桥接音源实时混音，输出到虚拟音频设备（其他应用如 Discord/OBS 可捕获）。

### 安装 VB-CABLE

1. 前往 <https://vb-audio.com/Cable/>
2. 下载 `VBCABLE_Driver_Pack45.zip`
3. 解压后右键 `VBCABLE_Setup_x64.exe` → 以管理员身份运行
4. 重启电脑

### 启用音频路由

1. 启动应用后打开设置 → **音频路由** 选项卡
2. 勾选"启用音频路由"
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
| `main.py` | 应用入口，生命周期管理 |
| `audio_router.py` | 音频路由引擎（混音、TTS 注入） |
| `audio_capture.py` | 麦克风/音频输入捕获（sounddevice） |
| `audio_player.py` | WAV 播放器（pyaudio） |
| `driver_manager.py` | VB-CABLE 驱动检测 |
| `input_overlay.py` | 热键输入悬浮窗 |
| `settings_window.py` | 设置界面（PySide6） |
| `app_config.py` | 配置持久化 |
| `pyproject.toml` | 项目元数据与依赖声明 |

## 技术栈

- **Python 3.12**（uv 管理，`.python-version` 锁定）
- **TTS**: Cartesia API
- **音频**: sounddevice + pyaudio
- **混音**: numpy 向量化处理
- **GUI**: PySide6
- **包管理**: uv
