# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 注意事项

+ 这是个新项目，不要考虑兼容旧的接口或者数据格式等，直接删除旧的增加新的即可

## 常用命令

```bash
# 环境初始化（首次）
.\init.ps1                     # uv sync，自动下载 Python 3.12 + 依赖

# 运行
uv run python main.py          # 启动应用

# 测试
uv run pytest tests/ -v        # 全量
uv run pytest tests/test_app_config.py -v            # 单文件
uv run pytest tests/test_app_config.py::test_update_volume_clamps -v  # 单用例

# 类型检查
uv run pyright main.py src/
```

## 架构

**包管理**：uv（`pyproject.toml` + `uv.lock`）。Python 3.12 由 `.python-version` 锁定（不可删除——pyaudio 只有 cp312 wheel，3.14 会回退源码编译失败）。

**源码**：`src/easy_tts/` 下分四个子系统：

| 包 | 职责 |
|----|------|
| `audio/` | 音频捕获(sounddevice)、播放(pyaudio)、路由混音(numpy)、VB-CABLE 驱动检测 |
| `tts/` | Cartesia TTS 引擎（bytes/realtime）、后端注册表、语音标签 |
| `ui/` | PySide6 悬浮输入框、设置窗口、系统托盘、主题常量 |
| 模块级 | `config.py`(AppSettings)、`secret.py`(keyring 密钥存储)、`log.py`(日志流)、`hotkey.py`/`native_hotkey.py`(Windows 全局快捷键) |

**入口**：`main.py` 在根目录——`WavTransApp` 类组装所有子系统。

**配置持久化**：`AppSettings` dataclass（`src/easy_tts/config.py`，~330 行）是唯一数据源。`AppSettings.load()` 从 `~/.wavtrans_config.json` 加载一次，属性访问零磁盘 I/O；`settings.update(key=value)` 部分更新 + 自动钳位 + 原子写入。UI 常量（`MIN_VOLUME`、`LOG_LEVELS` 等）在模块级。无 load/save 函数对——已全部移除。

## VB-Cable 音频路由链路

```
driver.py(VBCableDriverManager) → config.py(AppSettings) → main.py(生命周期)
  → router.py(AudioRouter: 混音引擎, mixer daemon thread)
    → capture.py(AudioCapture: sounddevice RawInputStream 回调)
    → player.py(AudioPlayer: play_wav 注入 TTS 到路由器)
  → ui/settings.py(音频路由设置页签)
```

`AudioRouter` 将麦克风 + 桥接源 + TTS 实时混音为 48kHz/16bit/单声道，输出到 VB-CABLE 虚拟设备。TTS 注入通过 `inject_tts_from_wav(pcm_data, src_rate, src_channels)` 自动重采样到 48kHz。

## 关机顺序

`pre_stop_hook` → `_stop_background_threads()`(停止路由器 + janitor) → `shutdown_log_stream()` → `app.quit()`。后台线程必须在 Qt 事件循环退出前停止，否则 QThreadStorage 警告。

## 测试注意事项

- `test_pyside6_*` 需要 display server，CI 环境可能跳过
- `test_native_hotkey.py` 仅 Windows，部分测试依赖实际 Win32 API
- 涉及 `USER_CONFIG_FILE` 的测试用 `monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", tmp_path)` 隔离
- PySide6 类型桩不完整——`_Widget`/`_SettingsDialog` 协议不兼容警告是已知误报，忽略
