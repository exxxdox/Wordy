# 项目模块约定

## 脚本入口

`wordy.ps1` 统一提供 `init`（初始化）、`dev`（启动，默认）和 `build`（打包）。`dev.cmd` 保留为参数转发入口；发布工作流使用 `wordy.ps1 build -Clean -NoPause`。

## 音频设备枚举

`audio/capture.py` 只提供 `list_input_devices()`，供设置页面枚举输入设备。麦克风侦听由 Windows 原生策略管理，不维护软件录音流。

## TTS Worker 生命周期

`WordyApp` 在 GUI 线程委托 `TTSManager` 提交操作。任务绑定提交时的引擎；预热和播放共用 worker executor。切换后端/API key 时，janitor 先等待旧任务完成，再关闭旧引擎并释放后继事件。新 worker 可立即接收任务，播放/预热等待旧 worker 清理后才使用共享播放器，避免阻塞 UI 和跨引擎关闭音频流。测试直接访问 `tts_manager`。

## 音频聊天轮盘

轮盘默认 F8，独立于输入框快捷键；按住唤醒，八向选择，松开播放，中心/Esc/右键取消；唤出与收起均不移动光标，方向以唤出点为锚点，屏幕边缘钳位只影响窗口。窗口不抢焦点。八槽文件名、名称与快捷键由 `AppSettings` 持久化。音频存于配置文件旁的 `audio_clips`，Windows 优先硬链接，失败复制；硬链接共享内容。Qt 解码成临时 PCM WAV，经 `TTSManager` 当前 worker 串行播放并等待交接，复用输出路由与返听，不依赖 TTS 密钥或音色。文件替换/清空先保存配置，再删除未引用托管文件。槽位音频也能直接录制系统声音：`audio/recorder.py` 用 PyAudioWPatch 采默认输出端点的 WASAPI loopback（sounddevice 的 PortAudio 不支持 loopback），采集线程不碰任何 Qt 对象，流按端点 mix format 原样打开（采样率和声道数都不能自选，多声道在下游截前两路），产物归一化成 48000 Hz 双声道 PCM 再走同一条托管导入链路——播放回退链在设备拒绝源采样率时只换 `rate` 不重采样数据，44100 的录音会静默变调。录音临时文件只在内容进了托管目录**且**槽位落了盘之后才删：导入失败或槽位保存失败时必须留着，那是这份音频唯一的副本。录制上限 `MAX_RECORD_SECONDS`（60 秒），设置窗口关闭与 `app.quit()`（`main.py:_stop_background_threads`，Qt 退出不派发 Close）都会丢弃未完成的录音。

## 设置窗口玻璃外壳

设置窗口是**无边框自绘玻璃窗口**（`FramelessWindowHint` + `WA_TranslucentBackground` + `_SettingsDialog.paintEvent` 手绘），与悬浮输入框、音频轮盘同材质。原生标题栏、拖动、缩放和三个窗口按钮全部自绘：标题栏是 `settings_widgets._TitleBar`，拖动/缩放交给 Windows 原生的 `startSystemMove()` / `startSystemResize()`，不在 Python 里算鼠标位移。

- **缩放必须先补 `WS_THICKFRAME`（`window.enable_system_resize`）**：`FramelessWindowHint` 让窗口变成 `WS_POPUP`，没有可缩放边框，而 Windows 的缩放循环要求这一位，缺了会**静默忽略** `startSystemResize`——光标会变，但窗口拖不动。补上样式位后 DWM 会顺带加系统圆角，必须同时把 `DWMWA_WINDOW_CORNER_PREFERENCE` 压成 `DONOTROUND`，否则和自绘圆角叠成双层。
- **窗口四周留 `WINDOW_EDGE_PAD`（12px）透明余量**，一举三得：容投影、当 8 向缩放热区、让子控件不贴边——热区必须落在没有子控件的区域，鼠标按下才会直达窗口自己。`DIALOG_WIDTH/HEIGHT` 已含这圈余量（玻璃本体仍是 480×580，高度另加 `TITLE_BAR_HEIGHT`），且必须写成字面量整数，静态尺寸契约只识别顶层 `ast.Constant`。
- **`QDialog` 与 `QWidget#settingsFooter` 的 QSS 必须透明**：窗口改成透明合成后，任何不透明底色都会整块盖住玻璃与投影。玻璃底的渐变分层已从 QSS 的 `qlineargradient` 搬到 `paintEvent`（`test_glass_theme_contract` 改为扫手绘源文件里的 `QLinearGradient`）。
- **首帧用 DWM cloaking（`window.set_window_cloaked`）**：透明窗口从 HWND 创建到首次合成之间必然有一段没有内容的空档，靠"提前多画一帧"堵不住。`show()` 前遮蔽、首帧 `paintEvent` 后解除；`FIRST_FRAME_GUARD_MS` 兜底，防止窗口始终不绘制时被永久藏起来。遮蔽必须在所有窗口标志/属性改完之后设置——`show` 期间若因改标志重建 HWND，遮蔽会连窗口一起丢掉。
- **缩放光标不继承**：窗口在边缘把光标设成缩放形后，Qt 的 `QWidget::cursor()` 会沿祖先链继承，内容区会跟着变缩放光标。`dialogShell` 与 `_TitleBar` 各显式设回 `ArrowCursor`。
- 最大化/全屏时余量与圆角都归零：`edge_pad()` / `corner_radius()` 是派生值，绘制、热区、布局三处自动一致。
- 内容区仍靠留白分组，没有做成卡片：窄窗口下卡片边框会把表单切碎（`_create_tab_page` 的注释记录了这一点）。

## 设置页反馈提示

临时反馈（保存结果、录制结果、密钥与音色操作结果等）统一走 `ui/status_toast.py` 的浮动反馈队列：每条自动淡出、悬停保持、移开继续淡出；多条同时出现时上下并列（新的在下、旧的在上），各自计时逐个消失，同时最多 `MAX_VISIBLE` 条。需要长期停留的状态说明（设备数量、密钥状态、录制中指引）仍留在原地标签——浮动卡片锚在页脚上沿、向上生长，正好盖住页脚上方的操作按钮，而且卡片自身没设 `WA_TransparentForMouseEvents`（只有内层卡片/色条/文字设了），命中的是卡片而不是按钮，停在按钮上时按钮**真的点不动**，不只是被遮住。面板被单独实例化时 `show_status_toast()` 返回 False，调用方回退到原地标签（测试里的独立面板走这条路径）。

- **一个控件只能挂一个 `QGraphicsEffect`**：淡出已占用不透明度效果。曾在其内层卡片再加投影效果，结果卡片在可见状态下改变尺寸时，投影缓存的源图失效，Qt 整块不绘制（离屏渲染与平台合成都复现）。浮起观感改由 `paintEvent` 手绘的分层圆角矩形 + 状态色混出的渐变底色和描边实现（`_mix`）；卡片外留出 `SHADOW_PAD` 画投影，比较可见位置用 `card_geometry()`，不要用控件矩形。
- **不要用 `adjustSize()` 定尺寸**：控件已可见时布局 `sizeHint` 缓存不更新，第二条更长的反馈会沿用上一条的尺寸导致文字被裁。尺寸按字体度量显式算出（见 `StatusToast.fit_width`）。
- **悬停判定要核对真实光标**：显示新卡片时 Qt 会给同层卡片补发没配对的 Enter/Leave（光标并不在卡片上），照单全收会把停留时间压成悬停时长。`enterEvent` 用 `_cursor_inside()` 校验，`leaveEvent` 只在真的悬停过后才重新计时。
- 队列挂在设置窗口（`self.window`）而非页签页，否则会被页签透明度动画一起淡化。
