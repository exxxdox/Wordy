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

**配置持久化**：`AppSettings` dataclass（`src/wordy/config.py`，~330 行）是唯一数据源。`AppSettings.load()` 从 `$XDG_CONFIG_HOME/wordy/config.toml` 加载一次，属性访问零磁盘 I/O；`settings.update(key=value)` 部分更新 + 自动钳位 + 原子写入。UI 常量（`MIN_VOLUME`、`LOG_LEVELS` 等）在模块级。

**持久化目录**：所有落盘文件集中在 `paths.config_directory()` 返回的同一个目录（目录名固定小写 `wordy`）——配置文件 `config.toml` 与轮盘托管音频 `audio_clips/`（后者从配置文件父目录推导，自动跟随）。`XDG_CONFIG_HOME` 未设置、为空串或给的是相对路径时退回 `~/.config/wordy`（目录名小写；相对路径按 XDG 规范视为无效）。根目录解析刻意放在 `src/wordy/paths.py` 而不是 `config.py`：`config` 有一条契约禁止出现 `os.environ`（防止密钥重新从环境读回来），而路径解析本质上要读环境。录制过程中的临时文件不在这里，走 `%TEMP%`。

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

## 音频聊天轮盘

轮盘默认 F8，独立于输入框快捷键；按住唤醒，八向选择，松开播放，中心/Esc/右键取消；唤出与收起均不移动光标，方向以唤出点为锚点，屏幕边缘钳位只影响窗口。窗口不抢焦点。八槽文件名、名称与快捷键由 `AppSettings` 持久化。音频存于配置目录下的 `audio_clips`（`$XDG_CONFIG_HOME/wordy/audio_clips`），Windows 优先硬链接，失败复制；硬链接共享内容。Qt 解码成临时 PCM WAV，经 `TTSManager` 当前 worker 串行播放并等待交接，复用输出路由与返听，不依赖 TTS 密钥或音色。文件替换/清空先保存配置，再删除未引用托管文件。槽位音频也能直接录制系统声音：`audio/recorder.py` 用 PyAudioWPatch 采默认输出端点的 WASAPI loopback（sounddevice 的 PortAudio 不支持 loopback），采集线程不碰任何 Qt 对象，流按端点 mix format 原样打开（采样率和声道数都不能自选，多声道在下游截前两路），产物归一化成 48000 Hz 双声道 PCM 再走同一条托管导入链路——播放回退链在设备拒绝源采样率时只换 `rate` 不重采样数据，44100 的录音会静默变调。录音临时文件只在内容进了托管目录**且**槽位落了盘之后才删：导入失败或槽位保存失败时必须留着，那是这份音频唯一的副本。录制上限 `MAX_RECORD_SECONDS`（60 秒），设置窗口关闭与 `app.quit()`（`main.py:_stop_background_threads`，Qt 退出不派发 Close）都会丢弃未完成的录音。

## 新增 TTS 引擎注意事项

- **PCM 格式匹配**：Volcengine SSE 返回 `pcm_s16le`（base64 编码），PyAudio 流**必须**用 `pyaudio.paInt16` 打开。不要复用 Cartesia 的 CABLE/非CABLE 的 float32/int16 分支——Cartesia 可切换输出编码，但固定编码的引擎必须硬编码正确的 PyAudio format。

## 设置窗口玻璃外壳

设置窗口是**无边框自绘玻璃窗口**（`FramelessWindowHint` + `WA_TranslucentBackground` + `_SettingsDialog.paintEvent` 手绘），与悬浮输入框、音频轮盘同材质。原生标题栏、拖动、缩放和三个窗口按钮全部自绘：标题栏是 `settings_widgets._TitleBar`，拖动/缩放交给 Windows 原生的 `startSystemMove()` / `startSystemResize()`，不在 Python 里算鼠标位移。

- **缩放必须先补 `WS_THICKFRAME`（`window.enable_system_resize`）**：`FramelessWindowHint` 让窗口变成 `WS_POPUP`，没有可缩放边框，而 Windows 的缩放循环要求这一位，缺了会**静默忽略** `startSystemResize`——光标会变，但窗口拖不动。补上样式位后 DWM 会顺带加系统圆角，必须同时把 `DWMWA_WINDOW_CORNER_PREFERENCE` 压成 `DONOTROUND`，否则和自绘圆角叠成双层。
- **窗口四周留 `WINDOW_EDGE_PAD`（12px）透明余量**，一举三得：容投影、当 8 向缩放热区、让子控件不贴边——热区必须落在没有子控件的区域，鼠标按下才会直达窗口自己。`DIALOG_WIDTH/HEIGHT` 已含这圈余量（玻璃本体仍是 480×580，高度另加 `TITLE_BAR_HEIGHT`），且必须写成字面量整数，静态尺寸契约只识别顶层 `ast.Constant`。
- **`QDialog` 与 `QWidget#settingsFooter` 的 QSS 必须透明**：窗口改成透明合成后，任何不透明底色都会整块盖住玻璃与投影。玻璃底的渐变分层已从 QSS 的 `qlineargradient` 搬到 `paintEvent`（`test_glass_theme_contract` 改为扫手绘源文件里的 `QLinearGradient`）。
- **首帧用 DWM cloaking（`window.set_window_cloaked`）**：透明窗口从 HWND 创建到首次合成之间必然有一段没有内容的空档，靠"提前多画一帧"堵不住。`show()` 前遮蔽、首帧 `paintEvent` 后解除；`FIRST_FRAME_GUARD_MS` 兜底，防止窗口始终不绘制时被永久藏起来。遮蔽必须在所有窗口标志/属性改完之后设置——`show` 期间若因改标志重建 HWND，遮蔽会连窗口一起丢掉。
- **缩放光标不继承**：窗口在边缘把光标设成缩放形后，Qt 的 `QWidget::cursor()` 会沿祖先链继承，内容区会跟着变缩放光标。`dialogShell` 与 `_TitleBar` 各显式设回 `ArrowCursor`。
- 最大化/全屏时余量与圆角都归零：`edge_pad()` / `corner_radius()` 是派生值，绘制、热区、布局三处自动一致。
- 内容区仍靠留白分组，没有做成卡片：窄窗口下卡片边框会把表单切碎（`_create_tab_page` 的注释记录了这一点）。

## 悬浮输入框的投影留白

输入框与设置窗口同材质，但**窗口比胶囊本体大一圈**：`overlay_widgets.SHADOW_PAD`（8px）那圈余量只用来画浮起投影（`glass.paint_drop_shadow`），胶囊本体是 `content_rect()`。投影收得很短、四周等宽、不带方向偏移（`glass.SHADOW_STEPS / SHADOW_ALPHA`），是贴着桌面的一点点分离感，不是落地影。

- **投影只画在面板外侧**：投影若压在面板底下，用户把不透明度调低时那层黑会从胶囊里透出来糊在内容上。`paint_drop_shadow` 用「层的外接矩形减去面板」的裁剪区保证这一点（把设置页的不透明度滑到 0 就能复现旧写法的问题）。
- **留白不算窗口**：`contains_global_point()` 判外部点击、`mousePressEvent` 判是否开始拖动，都只看 `content_rect()`——按在那圈看不见的余量上应当收起输入框，而不是拖走窗口。
- **几何一律按真实窗口算**：居中、固定居中、屏幕边界钳位走 `InputOverlay._window_size()`（取 root 实际尺寸）；`InputOverlay.width/height` 是胶囊内容尺寸，只用于布局子控件。

## 设置页反馈提示

临时反馈（保存结果、录制结果、密钥与音色操作结果等）统一走 `ui/status_toast.py` 的浮动反馈队列：每条自动淡出、悬停保持、移开继续淡出；多条同时出现时上下并列（新的在下、旧的在上），各自计时逐个消失，同时最多 `MAX_VISIBLE` 条。需要长期停留的状态说明（设备数量、密钥状态、录制中指引）仍留在原地标签——浮动卡片锚在页脚上沿、向上生长，正好盖住页脚上方的操作按钮，而且卡片自身没设 `WA_TransparentForMouseEvents`（只有内层卡片/色条/文字设了），命中的是卡片而不是按钮，停在按钮上时按钮**真的点不动**，不只是被遮住。面板被单独实例化时 `show_status_toast()` 返回 False，调用方回退到原地标签（测试里的独立面板走这条路径）。

- **一个控件只能挂一个 `QGraphicsEffect`**：淡出已占用不透明度效果。曾在其内层卡片再加投影效果，结果卡片在可见状态下改变尺寸时，投影缓存的源图失效，Qt 整块不绘制（离屏渲染与平台合成都复现）。浮起观感改由 `paintEvent` 手绘的分层圆角矩形 + 状态色混出的渐变底色和描边实现（`_mix`）；卡片外留出 `SHADOW_PAD` 画投影，比较可见位置用 `card_geometry()`，不要用控件矩形。
- **不要用 `adjustSize()` 定尺寸**：控件已可见时布局 `sizeHint` 缓存不更新，第二条更长的反馈会沿用上一条的尺寸导致文字被裁。尺寸按字体度量显式算出（见 `StatusToast.fit_width`）。
- **悬停判定要核对真实光标**：显示新卡片时 Qt 会给同层卡片补发没配对的 Enter/Leave（光标并不在卡片上），照单全收会把停留时间压成悬停时长。`enterEvent` 用 `_cursor_inside()` 校验，`leaveEvent` 只在真的悬停过后才重新计时。
- 队列挂在设置窗口（`self.window`）而非页签页，否则会被页签透明度动画一起淡化。

## 测试注意事项

- `conftest.py` 提供 session 级 `qapp` fixture（全局唯一 QApplication + offscreen 平台），PySide6 测试通过参数 `qapp` 引用
- `test_pyside6_*` 需要 display server，CI 环境可能跳过
- `test_native_hotkey.py` 仅 Windows，部分测试依赖实际 Win32 API
- 涉及 `USER_CONFIG_FILE` 的测试用 `monkeypatch.setattr(wordy.config, "USER_CONFIG_FILE", tmp_path)` 隔离
- PySide6 类型桩不完整——`_Widget`/`_SettingsDialog` 协议不兼容警告是已知误报，忽略
- `conftest.py` 提供共享 fixtures（`tmp_config_file`、`mock_player`、`qapp` 等）
- 全量测试必须用 `uv run python -m pytest`（`uv run pytest` 会因 uv trampoline 截断输出）
