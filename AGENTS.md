# 项目模块约定

## 轮盘 UI 一致性

设置中的轮盘 UI 与快捷键唤出的轮盘 UI 必须保持一致。调整轮盘布局、材质、槽位编号、名称呈现、空槽或选中状态时，须同步检查并更新两处，避免视觉规则分叉。

## 脚本入口

`wordy.ps1` 统一提供 `init`（初始化）、`dev`（启动，默认）和 `build`（打包）。`dev.cmd` 保留为参数转发入口；发布工作流使用 `wordy.ps1 build -Clean -NoPause`。

## 持久化目录

配置和轮盘托管音频集中在 `paths.config_directory()` 返回的同一个目录（目录名固定小写 `wordy`）——配置文件 `config.toml` 与轮盘托管音频 `audio_clips/`（后者从配置文件父目录推导，自动跟随）。`XDG_CONFIG_HOME` 未设置、为空串或给的是相对路径时退回 `~/.config/wordy`（相对路径按 XDG 规范视为无效）。根目录解析刻意放在 `src/wordy/paths.py` 而不是 `config.py`：`config` 有一条契约禁止出现 `os.environ`（防止密钥重新从环境读回来），而路径解析本质上要读环境。录制过程中的临时文件不在这里，走 `%TEMP%`。

## PortAudio 生命周期

`AudioPlayer` 每次播放都新建再终止一个 `PyAudio` 实例，而 PortAudio 的初始化计数是**进程级且非原子**的：两个线程并发跑 `Pa_Initialize`/`Pa_Terminate`，会让一个线程把全局设备表释放掉、另一个线程还在读它。症状是 `Pa_GetDeviceCount` 返回垃圾值（实测是一个被截断的指针，如 `-1294395680`）、日志报「没有默认输出设备」，重则原生访问违例**直接段错误**——没有 Python 堆栈，看起来就是「闪退」。

返听（`SidetoneAudioPlayer.play_wav`）主输出在调用线程同步播、返听另起守护线程，两路同时播放，正好制造这个并发。因此 `player.py` 用模块级 `_PORTAUDIO_LIFECYCLE_LOCK` 包住每一次 `_new_pyaudio()` 与 `_terminate_pyaudio_locked()`，**只锁生命周期调用、不锁中间的实际播放**（否则一路播放会把另一路整个堵住）。返听那一路只等固定上限（`_SIDETONE_TAIL_TIMEOUT_SECONDS`）就放手——调用方是串行的 TTS worker，无限等下去会把后续所有朗读一起拖住；上一段返听还没收尾时，本次直接跳过返听而不是排队叠加。回归用例在 `tests/test_audio_player.py::test_portaudio_lifecycle_is_serialized`：真跑真 PortAudio 会让 pytest 进程本身挂掉，所以它用假实例检测重叠。

`pyaudiowpatch`（录音）是另一份静态链接的 PortAudio，有独立的初始化计数，不共用这把锁。

## 音频设备枚举

`audio/capture.py` 只提供 `list_input_devices()`，供设置页面枚举输入设备。麦克风侦听由 Windows 原生策略管理，不维护软件录音流。

## TTS Worker 生命周期

`WordyApp` 在 GUI 线程委托 `TTSManager` 提交操作。任务绑定提交时的引擎；预热和播放共用 worker executor。切换后端/API key 时，janitor 先等待旧任务完成，再关闭旧引擎并释放后继事件。新 worker 可立即接收任务，播放/预热等待旧 worker 清理后才使用共享播放器，避免阻塞 UI 和跨引擎关闭音频流。测试直接访问 `tts_manager`。

## Volcengine 音色列表

**厂商没有可枚举音色的 HTTP 接口**，别去写「拉取音色列表」。`POST /api/v3/tts/get_voice` 是**单查**（入参必填 `speaker_id`），只返回该音色的注册状态与试听音频 `demo_audio`，**没有任何人类可读名称字段**，也没有分页/列表参数；官方「大模型音色列表」文档是静态表格，不是接口。

因此音色列表由用户**手动维护**并持久化在 `AppSettings.volcengine_voice_list`（`[{"id", "name"}]`，读取时由 `_normalize_voice_entries` 清洗：id 必须非空字符串、同 id 留第一条、缺名用 id 兜底——下拉按名称做标签，没名称的条目会被 `build_voice_label_maps` 静默丢掉）。火山音色**不再单独存 id/name**：列表顺序就是选择，首条即当前生效音色；旧配置里的 `voice_id`/`voice_name` 键加载时直接丢弃。`VolcenginePanel` 只做三件事：下拉快速切换（把选中条目移到首位并通知重建引擎）、添加（同 ID 改名而非新增，新条目排首位直接生效）、删除（落到剩下第一条，全删光才回「无」）。`set_voices_loaded` 忽略传入的网络结果，始终按本地列表重填。

## 音频聊天轮盘

WASAPI 回环在系统静音时可能完全不发数据包，采集循环必须先查 `get_read_available()`，仅按已就绪帧数读取；无数据时用停止事件做短等待，不能固定帧数阻塞读取。`stop()` / `cancel()` 等待超时后禁止对仍在 `read()` 的流执行 `close()` / `terminate()`，否则会原生访问违例。超时保留 `_ACTIVE` 和设备所有权、拒绝新录制，读取线程退出后再安全回收。足够时长但没有收到音频时提示未录到系统声音，不把静音当作读取故障。

每个音频槽独立保存 `volumes`（首盘 `chat_wheel_volumes`），默认 1.0，范围 0.0 到 2.0（0% 到 200%）；文字槽仍由 TTS 音量控制。槽位标题同行的音量滑块在拖动结束时保存，切槽/切盘时恢复对应值；改动从下次播放生效。普通试听、裁剪试听和轮盘正式播放均传递该槽位增益。音量随每次播放提交快照传给主输出与返听，二者逐块使用相同 PCM 增益与饱和限幅，不修改源文件，也不叠乘 TTS 音量。

用户可新增任意数量轮盘，每盘固定八槽、绑定独立快捷键；至少保留一个轮盘。第一盘仍由 `chat_wheel_name/hotkey/slots/labels/texts` 保存，后续盘存于 `additional_chat_wheels`（各含 `name`）；通过 `get_wheel`、`update_wheel`、`replace_wheels` 统一访问并原子保存。轮盘名称可选，空名回退显示「轮盘 N」，重命名直接在设置页的轮盘下拉框里编辑（`WHEEL_NAME_MAX_LENGTH` 截断），编辑结束才落盘。快捷键不能与输入框或其他轮盘重复，注册/保存失败保持原配置。运行时共用一个轮盘窗口，已有轮盘显示时忽略其他轮盘热键。设置页通过选择器切盘，录制、导入和文字试听期间锁住切盘；清空槽位或删除轮盘后，仅清理全部轮盘都不再引用的音频。

录音结束直接整段导入槽位（名称「录音 HHMMSS」），不再强制先裁剪；「截取」按钮随时对当前槽位已保存的音频打开「截取录音」对话框，按开始/结束秒数试听并保存片段，结果替换该槽，取消保持原样。`audio/trim.py` 按 PCM 帧创建独立 WAV，不修改源文件。片段导入和槽位配置都成功后，才删除裁剪临时文件；失败保留副本。片段试听通过 TTS worker 与控制器排队信号完成，不能从播放线程直接更新 Qt 控件。

首个轮盘默认 F8，独立于输入框快捷键；按住唤醒，八向选择，松开播放，中心/Esc/右键取消；唤出与收起均不移动光标，方向以唤出点为锚点，屏幕边缘钳位只影响窗口。窗口不抢焦点。八槽文件名、名称与快捷键由 `AppSettings` 持久化。槽内容是**互斥**的：`chat_wheel_texts` 非空则该槽朗读这段文字（当前 TTS），否则才播放 `chat_wheel_slots` 里的音频；填文字会按「清空」语义删掉该槽音频，导入/录制音频会清掉该槽文字。三个数组（`chat_wheel_slots` / `chat_wheel_labels` / `chat_wheel_texts`）必须在同一次 `update()` 里写、一起回滚，否则落盘失败会留下错位状态。轮盘的「可选中」判据就是标签非空，所以文字槽必须把文字（或名称）当标签传进 `open_wheel`——`ChatWheel` 没有别的传参通道。朗读**无法打断**（三个引擎的 `engine.speak()` 都阻塞到底且没有取消钩子，`close()` 抢同一把锁），因此文字槽试听时按钮如实显示「朗读中…」并对**所有槽**禁用：完成回调 `playback_finished` 不带槽号，允许期间开新试听会让回调误复位。朗读期间也拒绝开始录音（否则录音会把 TTS 录进去）。设置页的八个槽用数字编号（`音频 1`…`音频 8`）标识，不用方向名——方向概念只属于运行时轮盘的八向选择。试听按钮在播放期间变成「停止」，再点即停止试听；`TTSManager` 的取消句柄是**单次播放**的（`stop_audio_file()`），不能用全局的 `_audio_files_stopped`，否则一次停止会连累后续播放；`shutdown()` 因此必须显式调用 `stop_audio_file()`。播放结束（播完/被停止/失败）经 `ChatWheelController.playback_finished` 排队回 GUI 线程复位按钮，否则「停止」会一直挂着；替换或清空槽位、开始录音都会先停掉试听。音频存于配置目录下的 `audio_clips`（`$XDG_CONFIG_HOME/wordy/audio_clips`），Windows 优先硬链接，失败复制；硬链接共享内容。Qt 解码成临时 PCM WAV，经 `TTSManager` 当前 worker 串行播放并等待交接，复用输出路由与返听，不依赖 TTS 密钥或音色。文件替换/清空先保存配置，再删除未引用托管文件。槽位音频也能直接录制系统声音：`audio/recorder.py` 用 PyAudioWPatch 采默认输出端点的 WASAPI loopback（sounddevice 的 PortAudio 不支持 loopback），采集线程不碰任何 Qt 对象，流按端点 mix format 原样打开（采样率和声道数都不能自选，多声道在下游截前两路），产物归一化成 48000 Hz 双声道 PCM 再走同一条托管导入链路——播放回退链在设备拒绝源采样率时只换 `rate` 不重采样数据，44100 的录音会静默变调。录音临时文件只在内容进了托管目录**且**槽位落了盘之后才删：导入失败或槽位保存失败时必须留着，那是这份音频唯一的副本。录制上限 `MAX_RECORD_SECONDS`（60 秒），设置窗口关闭与 `app.quit()`（`main.py:_stop_background_threads`，Qt 退出不派发 Close）都会丢弃未完成的录音。

## 设置窗口玻璃外壳

设置窗口是**无边框自绘玻璃窗口**（`FramelessWindowHint` + `WA_TranslucentBackground` + `_SettingsDialog.paintEvent` 手绘），与悬浮输入框、音频轮盘同材质。原生标题栏、拖动、缩放和三个窗口按钮全部自绘：标题栏是 `settings_widgets._TitleBar`，拖动/缩放交给 Windows 原生的 `startSystemMove()` / `startSystemResize()`，不在 Python 里算鼠标位移。

- **缩放必须先补 `WS_THICKFRAME`（`window.enable_system_resize`）**：`FramelessWindowHint` 让窗口变成 `WS_POPUP`，没有可缩放边框，而 Windows 的缩放循环要求这一位，缺了会**静默忽略** `startSystemResize`——光标会变，但窗口拖不动。补上样式位后 DWM 会顺带加系统圆角，必须同时把 `DWMWA_WINDOW_CORNER_PREFERENCE` 压成 `DONOTROUND`，否则和自绘圆角叠成双层。
- **窗口四周留 `WINDOW_EDGE_PAD`（12px）透明余量**，一举三得：容投影、当 8 向缩放热区、让子控件不贴边——热区必须落在没有子控件的区域，鼠标按下才会直达窗口自己。`DIALOG_WIDTH/HEIGHT` 已含这圈余量（默认窗口 1120×760，最小宽度960px，设置按任务双栏排列，含透明边缘与标题栏；400px 轮盘及编辑区默认完整显示，无需纵向滚动），且必须写成字面量整数，静态尺寸契约只识别顶层 `ast.Constant`。
- **`QDialog` 与 `QWidget#settingsFooter` 的 QSS 必须透明**：窗口改成透明合成后，任何不透明底色都会整块盖住玻璃与投影。玻璃底的渐变分层已从 QSS 的 `qlineargradient` 搬到 `paintEvent`（`test_glass_theme_contract` 改为扫手绘源文件里的 `QLinearGradient`）。
- **首帧用 DWM cloaking（`window.set_window_cloaked`）**：透明窗口从 HWND 创建到首次合成之间必然有一段没有内容的空档，靠"提前多画一帧"堵不住。`show()` 前遮蔽、首帧 `paintEvent` 后解除；`FIRST_FRAME_GUARD_MS` 兜底，防止窗口始终不绘制时被永久藏起来。遮蔽必须在所有窗口标志/属性改完之后设置——`show` 期间若因改标志重建 HWND，遮蔽会连窗口一起丢掉。
- **缩放光标不继承**：窗口在边缘把光标设成缩放形后，Qt 的 `QWidget::cursor()` 会沿祖先链继承，内容区会跟着变缩放光标。`dialogShell` 与 `_TitleBar` 各显式设回 `ArrowCursor`。
- 最大化/全屏时余量与圆角都归零：`edge_pad()` / `corner_radius()` 是派生值，绘制、热区、布局三处自动一致。
- 内容区仍靠留白分组，没有做成卡片：分栏表单通过间距表达层级，避免卡片边框切碎内容（`_create_tab_page` 的注释记录了这一点）。

## 设置页反馈提示

临时反馈（保存结果、录制结果、密钥与音色操作结果等）统一走 `ui/status_toast.py` 的浮动反馈队列：每条自动淡出、悬停保持、移开继续淡出；多条同时出现时上下并列（新的在下、旧的在上），各自计时逐个消失，同时最多 `MAX_VISIBLE` 条。需要长期停留的状态说明（设备数量、密钥状态）仍留在原地标签——浮动卡片锚在页脚上沿、向上生长，正好盖住页脚上方的操作按钮，而且卡片自身没设 `WA_TransparentForMouseEvents`（只有内层卡片/色条/文字设了），命中的是卡片而不是按钮，停在按钮上时按钮**真的点不动**，不只是被遮住。面板被单独实例化时 `show_status_toast()` 返回 False，调用方回退到原地标签（测试里的独立面板走这条路径）。

- **一个控件只能挂一个 `QGraphicsEffect`**：淡出已占用不透明度效果。曾在其内层卡片再加投影效果，结果卡片在可见状态下改变尺寸时，投影缓存的源图失效，Qt 整块不绘制（离屏渲染与平台合成都复现）。浮起观感改由 `paintEvent` 手绘的分层圆角矩形 + 状态色混出的渐变底色和描边实现（`_mix`）；卡片外留出 `SHADOW_PAD` 画投影，比较可见位置用 `card_geometry()`，不要用控件矩形。
- **不要用 `adjustSize()` 定尺寸**：控件已可见时布局 `sizeHint` 缓存不更新，第二条更长的反馈会沿用上一条的尺寸导致文字被裁。尺寸按字体度量显式算出（见 `StatusToast.fit_width`）。
- **悬停判定要核对真实光标**：显示新卡片时 Qt 会给同层卡片补发没配对的 Enter/Leave（光标并不在卡片上），照单全收会把停留时间压成悬停时长。`enterEvent` 用 `_cursor_inside()` 校验，`leaveEvent` 只在真的悬停过后才重新计时。
- 队列挂在设置窗口（`self.window`）而非页签页，否则会被页签透明度动画一起淡化。

## 日志持久化

仅 `sys.frozen` 打包运行时在 `Path(sys.executable)` 同目录同步写入 UTF-8 `wordy.log`；源码运行保留控制台和内存日志，不创建日志文件。每个文件最多 5 MiB，当前文件加 `wordy.log.1` 到 `wordy.log.4` 共五个，超限轮转并删除最旧备份。按实际 UTF-8 字节计量且禁用 Windows 换行扩展，超长单条记录按字符边界跨文件保存；每次写入立即 flush，不使用后台日志队列。目录不可写时在现有日志流报告错误，不擅自改到其他目录。

## 单实例运行

`main()` 在配置、日志处理器和音频设备初始化前获取 Windows 命名互斥体 `Local\Wordy.SingleInstance`；同一 Windows 会话内已有实例时，新进程先用原生提示框显示“Wordy 已在运行，请勿重复启动。”，关闭提示后退出，不初始化第二套 Qt UI、日志或播放链路。互斥体名称不依赖 exe 路径，源码版与不同目录的 exe 共用限制。首实例句柄一直保留到进程终止，由 Windows 自动回收，不能在 `main()` 返回或日志关闭前提前释放。
