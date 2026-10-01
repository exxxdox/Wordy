# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 注意事项

+ 新项目，不考虑兼容旧接口或数据格式，直接删除旧的增加新的
+ 修改符号时用 LSP 查找引用
+ TDD：新增/修改功能先写或修改测试
+ 所有设置页面的内容都要持久化保存
+ pyright和测试错误要清零，不管是否这次引入

## 脚本入口

`wordy.ps1` 统一提供 `init`（初始化）、`dev`（启动，默认）和 `build`（打包）。`dev.cmd` 保留为参数转发入口；发布工作流使用 `wordy.ps1 build -Clean -NoPause`。

### 命令示例

```bash
# 环境初始化（首次）
.\wordy.ps1 init               # uv sync，自动下载 Python 3.12 + 依赖

# 运行
uv run python -m wordy      # 启动应用
.\wordy.ps1 dev                # 启动应用，保留控制台日志；dev.cmd 转发参数
.\wordy.ps1 build -Clean -NoPause  # 打包 dist/Wordy.exe

# 测试
uv run python -m pytest tests/ --tb=no -q  # 全量
uv run python -m pytest tests/test_app_config.py -v            # 单文件
uv run python -m pytest tests/test_app_config.py::test_update_volume_clamps -v  # 单用例

# 类型检查
uv run python -m pyright src/ tests/
```

## 架构

**包管理**：uv（`pyproject.toml` + `uv.lock`）。Python 3.12 由 `.python-version` 锁定（不可删除——pyaudio 只有 cp312 wheel，3.14 会回退源码编译失败）。

**入口**：`python -m wordy` → `src/wordy/__main__.py` → `src/wordy/main.py:main()` → `WordyApp.run()`。

**源码**：`src/wordy/` 下分六个子系统：

| 包 | 职责 |
|----|------|
| `audio/` | 输入设备枚举(sounddevice)、播放(pyaudio)、路由(VB-CABLE)、Windows 侦听策略(COM/WASAPI) |
| `tts/` | TTS 引擎抽象(`BackendTTSEngine`)、Cartesia bytes/realtime 后端、注册表、语音标签 |
| `ui/` | PySide6 悬浮输入框、设置窗口、系统托盘、终端风格主题常量 |
| `hotkey/` | `keyboard` 库快捷键解析 + Win32 原生全局热键监听(`NativeHotkeyListener`) |
| 模块级 | `config.py`(AppSettings)、`secret.py`(keyring 密钥存储)、`log.py`(日志流)、`identity.py`(音频设备标识)、`qt_lifecycle.py`(Qt 安全调用) |

**配置持久化**：`AppSettings` dataclass（`src/wordy/config.py`，~330 行）是唯一数据源。`AppSettings.load()` 从 `~/.wavtrans_config.json` 加载一次，属性访问零磁盘 I/O；`settings.update(key=value)` 部分更新 + 自动钳位 + 原子写入。UI 常量（`MIN_VOLUME`、`LOG_LEVELS` 等）在模块级。

## VB-Cable 音频侦听链路

```
driver.py(VBCableDriverManager) → config.py(AppSettings) → main.py(生命周期)
  → router.py(AudioRouter: 路由引擎，无软件混音)
    → listen_policy.py(Windows "侦听此设备" COM/WASAPI 策略管理)
  → player.py(AudioPlayer: TTS 播放到 CABLE Input)
  → capture.py(list_input_devices: sounddevice 输入设备枚举)
  → ui/settings.py(音频侦听设置页签)
```

**路由原理**（不再做软件混音）：

1. `AudioRouter` 将 TTS 输出重定向到 VB-CABLE Input（通过 `player.set_output_device`）
2. 麦克风通过 Windows "侦听此设备" 功能直通到 CABLE Input（`listen_policy.py` 通过 COM/WASAPI `IPropertyStore` 读写 `PKEY_ListenTo`）
3. 系统层叠加后从 CABLE Output 输出——零延迟、零 CPU 混音开销
4. 停止路由时自动恢复麦克风原侦听状态

**AudioPlayer 回退链**：打开流失败时依次尝试 → int16 格式 → int16 + 设备默认采样率 → 系统默认输出设备。VB-CABLE 设备自动做单声道→双声道 upmix（避免 Pa 将相邻样本错拆为 L/R）。

## 关机顺序

`pre_stop_hook` → `_stop_background_threads()`(停止路由器 + janitor) → `shutdown_log_stream()` → `app.quit()`。后台线程必须在 Qt 事件循环退出前停止，否则 QThreadStorage 警告。

## 音频设备枚举

`audio/capture.py` 只提供 `list_input_devices()`，供设置页面枚举输入设备。麦克风侦听由 Windows 原生策略管理，不维护软件录音流。

## TTS Worker 生命周期

`WordyApp` 在 GUI 线程委托 `TTSManager` 提交操作。任务绑定提交时的引擎；预热和播放共用 worker executor。切换后端/API key 时，janitor 先等待旧任务完成，再关闭旧引擎并释放后继事件。新 worker 可立即接收任务，播放/预热等待旧 worker 清理后才使用共享播放器，避免阻塞 UI 和跨引擎关闭音频流。测试直接访问 `tts_manager`。

## 新增 TTS 引擎注意事项

- **PCM 格式匹配**：Volcengine SSE 返回 `pcm_s16le`（base64 编码），PyAudio 流**必须**用 `pyaudio.paInt16` 打开。不要复用 Cartesia 的 CABLE/非CABLE 的 float32/int16 分支——Cartesia 可切换输出编码，但固定编码的引擎必须硬编码正确的 PyAudio format。

## 测试注意事项

- `conftest.py` 提供 session 级 `qapp` fixture（全局唯一 QApplication + offscreen 平台），PySide6 测试通过参数 `qapp` 引用
- `test_pyside6_*` 需要 display server，CI 环境可能跳过
- `test_native_hotkey.py` 仅 Windows，部分测试依赖实际 Win32 API
- 涉及 `USER_CONFIG_FILE` 的测试用 `monkeypatch.setattr(wordy.config, "USER_CONFIG_FILE", tmp_path)` 隔离
- PySide6 类型桩不完整——`_Widget`/`_SettingsDialog` 协议不兼容警告是已知误报，忽略
- `conftest.py` 提供共享 fixtures（`tmp_config_file`、`mock_player`、`qapp` 等）
- 全量测试必须用 `uv run python -m pytest`（`uv run pytest` 会因 uv trampoline 截断输出）
