# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 注意事项

+ 新项目，不考虑兼容旧接口或数据格式，直接删除旧的增加新的
+ 修改符号时用 LSP 查找引用
+ TDD：新增/修改功能先写或修改测试

## 常用命令

```bash
# 环境初始化（首次）
.\init.ps1                     # uv sync，自动下载 Python 3.12 + 依赖

# 运行
uv run python -m easy_tts      # 启动应用
.\dev.ps1                      # 开发模式（设置 EASY_TTS_DEV=1）

# 测试
uv run pytest tests/ -v        # 全量
uv run pytest tests/test_app_config.py -v            # 单文件
uv run pytest tests/test_app_config.py::test_update_volume_clamps -v  # 单用例

# 类型检查
uv run pyright main.py src/
```

## 架构

**包管理**：uv（`pyproject.toml` + `uv.lock`）。Python 3.12 由 `.python-version` 锁定（不可删除——pyaudio 只有 cp312 wheel，3.14 会回退源码编译失败）。

**入口**：`python -m easy_tts` → `src/easy_tts/__main__.py` → `src/easy_tts/main.py:main()` → `WavTransApp.run()`。

**源码**：`src/easy_tts/` 下分六个子系统：

| 包 | 职责 |
|----|------|
| `audio/` | 音频捕获(sounddevice)、播放(pyaudio)、路由(VB-CABLE)、Windows 侦听策略(COM/WASAPI) |
| `tts/` | TTS 引擎抽象(`BackendTTSEngine`)、Cartesia bytes/realtime 后端、注册表、语音标签 |
| `ui/` | PySide6 悬浮输入框、设置窗口、系统托盘、终端风格主题常量 |
| `hotkey/` | `keyboard` 库快捷键解析 + Win32 原生全局热键监听(`NativeHotkeyListener`) |
| 模块级 | `config.py`(AppSettings)、`secret.py`(keyring 密钥存储)、`log.py`(日志流)、`identity.py`(音频设备标识)、`qt_lifecycle.py`(Qt 安全调用) |

**配置持久化**：`AppSettings` dataclass（`src/easy_tts/config.py`，~330 行）是唯一数据源。`AppSettings.load()` 从 `~/.wavtrans_config.json` 加载一次，属性访问零磁盘 I/O；`settings.update(key=value)` 部分更新 + 自动钳位 + 原子写入。UI 常量（`MIN_VOLUME`、`LOG_LEVELS` 等）在模块级。

## VB-Cable 音频路由链路

```
driver.py(VBCableDriverManager) → config.py(AppSettings) → main.py(生命周期)
  → router.py(AudioRouter: 路由引擎，无软件混音)
    → listen_policy.py(Windows "侦听此设备" COM/WASAPI 策略管理)
  → player.py(AudioPlayer: TTS 播放到 CABLE Input)
  → capture.py(AudioCapture: sounddevice RawInputStream 输入枚举)
  → ui/settings.py(音频路由设置页签)
```

**路由原理**（不再做软件混音）：

1. `AudioRouter` 将 TTS 输出重定向到 VB-CABLE Input（通过 `player.set_output_device`）
2. 麦克风通过 Windows "侦听此设备" 功能直通到 CABLE Input（`listen_policy.py` 通过 COM/WASAPI `IPropertyStore` 读写 `PKEY_ListenTo`）
3. 系统层叠加后从 CABLE Output 输出——零延迟、零 CPU 混音开销
4. 停止路由时自动恢复麦克风原侦听状态

**AudioPlayer 回退链**：打开流失败时依次尝试 → int16 格式 → int16 + 设备默认采样率 → 系统默认输出设备。VB-CABLE 设备自动做单声道→双声道 upmix（避免 Pa 将相邻样本错拆为 L/R）。

## 关机顺序

`pre_stop_hook` → `_stop_background_threads()`(停止路由器 + janitor) → `shutdown_log_stream()` → `app.quit()`。后台线程必须在 Qt 事件循环退出前停止，否则 QThreadStorage 警告。

## TTS Worker 生命周期

`WavTransApp` 持有 `_TTSWorker`（单线程 `ThreadPoolExecutor` + `BackendTTSEngine`）。切换后端/API key 时旧 worker 入队 `_RetiredWorker` 到 janitor 线程异步关闭（先 `executor.shutdown` 再 `engine.close`），避免阻塞 UI。新 worker 立即可用。

## 测试注意事项

- `test_pyside6_*` 需要 display server，CI 环境可能跳过
- `test_native_hotkey.py` 仅 Windows，部分测试依赖实际 Win32 API
- 涉及 `USER_CONFIG_FILE` 的测试用 `monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", tmp_path)` 隔离
- PySide6 类型桩不完整——`_Widget`/`_SettingsDialog` 协议不兼容警告是已知误报，忽略
- `conftest.py` 提供共享 fixtures（`tmp_config_file`、`mock_player` 等）
