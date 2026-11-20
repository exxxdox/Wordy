# Wordy 项目说明

## 架构

Rust（edition 2024）+ Tauri 2 桌面应用。设置界面是 React（Vite + Tailwind），托盘、悬浮输入框、轮盘、编辑器、截取与日志窗口仍是原生 Win32。入口 `src-tauri/src/main.rs` → 工作线程 `src-tauri/src/app.rs:run_native()`。

| 路径 | 职责 |
|------|------|
| `src-tauri/src/main.rs` | Tauri 入口：单实例、WebView 设置窗口、启动原生工作线程 |
| `src-tauri/src/app.rs` | 原生工作线程：热键分发、窗口消息循环、设置动作、日志 |
| `src-tauri/src/ui.rs` | WebView 与原生线程的桥：动作枚举、配置快照、命令 |
| `src-tauri/src/app/recording.rs` | 后台录制、音频截取与试听、异步导入与保存 |
| `src-tauri/src/desktop.rs` | Win32 宿主窗口、托盘、通知和共用控件辅助函数 |
| `src-tauri/src/desktop/overlay.rs` | 悬浮输入框、输入事件、背景和按钮绘制 |

| `src-tauri/src/app/trim.rs` | React 截取窗口的原生会话：临时 WAV、波形、试听与保存 |
| `src-tauri/src/wheel.rs` / `wheel_paint.rs` / `wheel_window.rs` | 轮盘状态、竞技轮盘绘制、运行时窗口 |
| `src-tauri/src/audio.rs` + `src-tauri/src/audio/` | WASAPI 设备枚举、播放/返听、VB-CABLE 路由、Windows 侦听、回环录制、裁剪/波形、托管音频 |
| `src-tauri/src/tts.rs` + `src-tauri/src/tts/realtime.rs` | TTS 引擎、Cartesia HTTP/WebSocket、Volcengine SSE、音色标签 |
| `src-tauri/src/config.rs` | `Settings` 单一数据源、钳位、原子写入、150ms 防抖写盘 |
| `src-tauri/src/backup.rs` / `backup_crypto.rs` / `credentials.rs` | 加密备份、AES-256-GCM/PBKDF2、Windows 凭据管理器 |
| `src-tauri/src/hotkey.rs` / `instance.rs` / `file_log.rs` | 快捷键解析与注册、单实例互斥体、日志轮转 |
| `src/` | React 设置界面（Vite + Tailwind），构建产物 `dist-ui/` 内嵌进 exe |
| `src-tauri/tauri.conf.json` | 窗口、构建命令、CSP 与图标配置 |

## 设置界面（Tauri + React）

设置页由 WebView 渲染，原生逻辑不变：网页通过 `wordy_action` 发送动作，原生线程执行后回传完整 `Snapshot`，再用 `wordy://snapshot` 事件推送变化。配置仍以 `ConfigStore` 为唯一数据源，网页不做本地持久化。`ConfigStore::revision` 决定是否推送。

- 关闭设置窗口只隐藏，托盘应用继续运行；`wordy_hide` 与窗口关闭事件都只隐藏。
- 设置窗口无系统标题栏和窗口边框（`decorations(false)`）：拖动窗口用侧栏品牌区与页头的 `data-tauri-drag-region`，缩放用页面四周 8 条透明把手调用 `startResizeDragging`；关闭仍走底部「关闭设置」（`wordy_hide`）。对应权限在 `src-tauri/capabilities/default.json`。
- 设置窗口首次打开居中，之后恢复拖动后的位置（`settings_window_position`，拖动时防抖写盘）；位置所在显示器失效时重新居中。
- 确认、密码、快捷键录制弹窗与状态 Toast 都由 React 渲染；原生线程通过 `wordy://dialog` 请求并等待 `wordy_dialog_reply`。文件选择仍是系统对话框。
- 桥命令 `wordy_action`/`wordy_snapshot`/`wordy_wheel_preview` 必须是 async：同步命令会占住 Tauri 主线程，弹窗事件无法送达，表现为“弹窗弹不出来”。
- 状态提示由 React `wordy://feedback` 渲染；设置页隐藏时错误仍走托盘通知。
- 轮盘编辑器由 React「轮盘」页签提供；设置里的轮盘预览通过 `wordy_wheel_preview` 调用原生 `wheel_paint` 渲染，与运行时轮盘使用同一套绘制。
- 截取窗口由 React 渲染波形（Canvas）、拖选、试听与保存；原生只保留临时 WAV、裁剪与托管音频。
- 日志面板由 React 渲染：`wordy_logs` 取历史，`wordy://log` 逐行推送，托盘动作会打开设置窗口并发出 `wordy://open-logs`。
- 设置页文案保持中文，不写内部实现说明。

持久化目录：`config_directory()`（`XDG_CONFIG_HOME/wordy`，未设置时 `%USERPROFILE%\.config\wordy`），含 `config.toml`、`audio_clips/` 与解包出来的 `bin/ffmpeg.exe`；托管音频从配置文件父目录推导。日志仅 `packaged` 构建在 exe 同目录写 `wordy.log`（5 MiB × 5）。图标资源在 `src-tauri/src/assets/wordy.ico`，运行时经 `include_bytes!` 使用；exe 图标由 `src-tauri/icons/icon.ico` 经 tauri-build 嵌入。精简 FFmpeg 在 `src-tauri/assets/ffmpeg.exe`（约 3.2 MB，7.1.1 交叉编译），`build.rs` 复制到 `OUT_DIR` 后同样经 `include_bytes!` 嵌入；缺失时构建仍通过，但运行时退回系统 FFmpeg。

## 构建与验证

```powershell
.\wordy.ps1 init                 # cargo fetch + pnpm install
.\wordy.ps1 dev [-Port 1420]     # 起始端口被占用时自动顺延（默认 1420）
.\wordy.ps1 build -Clean -NoPause # pnpm build + release → dist/Wordy.exe
cargo test --manifest-path src-tauri/Cargo.toml
```

`src-tauri/build.rs` 调用 `tauri_build::build()`，把 `src-tauri/icons/icon.ico` 写入 exe 图标资源并生成权限清单；缺失时资源管理器只显示默认图标。`src-tauri/tests/rust_icon.rs` 回归检查构建产物的 RT_ICON/RT_GROUP_ICON。

发布工作流 `.github/workflows/release.yml` 使用 Rust stable、pnpm 与 Node、`cargo test --locked --manifest-path src-tauri/Cargo.toml`，再调用 `wordy.ps1 build`。`dev.cmd` 仍转发 `wordy.ps1` 参数。

## 配置备份

设置页「备份」页签提供手动 `.wordybackup` 导出/导入，并支持定时备份：全部设置、轮盘音频、音频库中的已命名文件、两家 TTS API Key。手动导出、导入默认从配置目录下的 `backup/` 开始。新自动备份保存在 `backup/auto/`，手动导出不能写入该子目录。导出必须输入并确认非空密码，纯空白同样拒绝；密码不去掉首尾空格、不保存。ZIP 内容 AES-256-GCM 认证加密，随机盐/nonce，PBKDF2-SHA256 120 万次；导出流式写密文；旧明文 ZIP 不导入。认证通过后才解析配置/凭据，校验格式、大小、路径、字段、快捷键冲突，复制音频并原子提交；导入开始前写入 Windows 用户级 DPAPI 加密恢复记录（`config.toml.import-pending`）。配置文件与提交标记一起原子替换；进程中断后，下次启动按标记回滚未提交的凭据和音频，或补齐已提交的凭据。录制、裁剪或后台保存时禁止导入；导出等待音频保存结束。成功后退出，需重新启动。新配置不再引用、但旧配置引用过的音频文件会被删除；导入前就存在且从未被引用的文件保留，可在「备份」页签清理。

「备份」页签显示托管目录中未被任何轮盘引用的音频数量，可一键清理（删除前确认）。结果列出实际删除的音频名称，无名称时显示文件 ID。

定时备份默认关闭，间隔 1–168 小时、保留 1–100 份；自动文件命名 `auto-YYYYMMDD-HHMMSS.wordybackup`，只清理 `backup/auto/` 中自动生成的旧文件，父目录中已有的备份会保留。启动时根据专用目录中最近一次成功备份的时间判断是否到期，无记录时在开启后先备份一次。自动备份密码在首次开启或修改时输入，存 Windows 凭据管理器（`backup_password`）；手动导出/导入仍每次输入，不读取该密码。缺失密码时自动备份跳过并写一条 WARNING，下一间隔重试。备份按 manifest 版本校验；V1 字段契约独立于当前默认配置。包含音频名称映射时写 V2，导入 V1 时从旧槽位标签补齐名称。导入为音频分配新 ID，同时更新槽位引用和名称映射。

## 播放与返听

「启用主播放输出」由 `main_playback_enabled` 持久化，默认开启。文字、轮盘音频和输入框朗读共用 WASAPI 的 VB-CABLE 端点选择。识别启用中的 VB-CABLE 播放端点，优先选标准 CABLE Input；旧配置 `virtual_output_device` 只在同优先级的 VB-CABLE 端点间按完整名称消歧。设置页显示实际选中的端点，区分无可用端点和检测失败，进入音频页、窗口获焦或手动刷新时重查。启动时主输出已开启且设备不可用或检测失败会发对应提示。返听独立开关，使用「返听播放设备」（默认跟随系统）；主端点不可用时，只有独立启用且可用的返听仍可播放。F10 裁剪试听走本地设备，拒绝 VB-CABLE。

「音频侦听」控制所选麦克风的 Windows 侦听：开启送到 CABLE Input，关闭关闭该麦克风。启动沿用 Windows 状态，设置页回显实际开关与输出目标（含设备不可用）；退出保留 Windows 状态；关闭失败保留原状态供重试。麦克风名称匹配先精确（忽略首尾空格与大小写），再唯一子串匹配。设备枚举失败时设置页显示空列表而不是报错中止。

## 轮盘

轮盘导入、播放和截取原生以外的音频格式时需要 FFmpeg。只使用随 exe 打包的精简版：构建时从 `src-tauri/assets/ffmpeg.exe` 复制并由 `include_bytes!` 嵌入，首次使用时解包到配置目录 `bin/ffmpeg.exe`（SHA-256 一致即复用）。不查找系统 FFmpeg，也不在应用内联网安装；内嵌解码器不支持的编码或损坏文件按「不支持导入此音频格式」拒绝。有效的 PCM16 WAV 直接读取，不经过 FFmpeg。发布产物仍只有 `Wordy.exe`。

设置中的轮盘 UI 与运行时轮盘必须保持一致。只保留竞技样式，由 `wheel_paint` 绘制；设置页预览调用同一渲染器（`wordy_wheel_preview`）。画布 634×540（盘体 540×540 居中），名称居中 hover 放大仍完整显示。动画仅影响显示：120ms 淡入、90ms 淡出、90ms 高亮；松键立即提交播放，淡出停止接收选择。

每盘八槽、独立快捷键、任意数量、至少一个。第一盘存 `chat_wheel_name/hotkey/slots/labels/texts/text_modes`，后续在 `additional_chat_wheels`。快捷键不得与输入框/其他轮盘/后台录制重复。槽位可同时保存文字与音频，`text_modes` 决定播放/试听哪一类，切换模式不清空另一类；只有「清空」会同时清掉标签、文字与音频引用。选中的一类为空时回退到另一类。音频槽 `volumes` 0–300%，播放时快照传主输出与返听。托管音频在 `audio_clips`，导入时复制为独立文件。清空槽保留音量。替换音频、清空槽位或删除轮盘后，音频文件仍留在音频库，可在「备份」页签清理未引用音频。新增轮盘直接在编辑器或截取窗口追加（默认名「轮盘 N」），创建时不要求录制快捷键；之后用「录制快捷键」按钮补录（与设置页热键同一录制器，录制期间临时注销现有全局热键），校验通过再注册。未绑定快捷键的轮盘不产生启动警告。所有快捷键（全局、按住键、轮盘）只通过录制弹窗设置，不接受文本输入；弹窗、确认框和文本输入共用 `rust/dialog.rs` 的深色外观，Esc 取消且不记录为快捷键。

「轮盘 → 整理音频」左侧选择轮盘，复用运行时样式的轮盘画布作为拖放目标。右侧可按名称搜索托管音频。拖放或选中后点击扇区会复用该音频，保留槽位的文字、文字标题、播放模式与音量。在音频库确认删除后，永久删除文件并清除各轮盘对该音频的引用，保留槽位文字。配置用 `audio_clip_names` 保存托管文件 ID 与名称，编辑音频槽的非空标题时更新名称。旧文件若无可用标题，音频库显示文件 ID。

轮盘音频可从文件导入，或通过后台录制保存到槽位。后台录制（默认 F10）环缓冲保留 `rolling_buffer_seconds`（5–300），停止后打开截取对话框；默认落点为按轮盘顺序的第一个空槽，全部占满时回到第一盘槽 1，默认音量 100%。名称默认「录音 HHMMSS」。WASAPI 回环先查可读帧再读，停止超时不得关闭仍在 read 的流；取消超时只记警告、不阻断关闭。

## TTS

- Cartesia：bytes（HTTP 整段）与 realtime（WebSocket，PCM16）；realtime 支持后台 keepalive、`ctx.cancel`，base URL 可被 `CARTESIA_BASE_URL` 覆盖；仅正常空闲关闭允许重连，其他关闭按错误处理。
- Volcengine：SSE 返回 `pcm_s16le`（base64），播放必须按 int16 打开。
- HTTP 使用 connect timeout，不设整请求总期限；最多跟随 10 次重定向。
- 音量 0.50–2.00 由 TTS 引擎应用；音频槽增益独立，不叠乘。
- 「播放时自动按住某键」：正式播放前按下，结束/失败/取消后松开；F10 试用跳过。输入框提交先收起窗口再提交。
- API Key 存 Windows 凭据管理器，不写入 TOML。
- 朗读可打断：新提交先停旧朗读/音频；取消只能协作式，禁止跨线程关流。退出时最多等待 worker 30 秒。

## 日志与通知

`log_level` 过滤 INFO/WARNING/ERROR/DEBUG；格式 `YYYY-MM-DD HH:MM:SS [LEVEL] target: message`。内存保留 5000 行，托盘「打开日志窗口」与设置页「查看日志」打开设置窗口内的 React 日志面板，新行通过 `wordy://log` 实时推送。packaged 构建同步写 exe 旁 `wordy.log`，UTF-8、5 MiB 轮转、共 5 个文件。

通知通过 `AppUserModelId = Wordy.Desktop` 注册发送者并调用 Windows 通知中心；发送失败回退托盘气泡。图标写入配置目录 `notification-icon.ico`。

## 测试

`cargo test --manifest-path src-tauri/Cargo.toml`；UI/音频测试使用真实 Win32 句柄在进程内创建，不依赖外部显示器。涉及配置的测试用临时目录，不读真实用户配置。新增/修改行为先补测试。禁止在日志或测试中写入真实 API Key。

## Win32 界面防闪烁

- 高频更新的自绘控件先在离屏合成背景和内容，再一次提交到窗口；完整覆盖失效区域时抑制默认背景擦除。
- 圆角文本框属于原生弹窗：外部四角填页面背景，圆角弧与 1px 边框用逐像素混合（`desktop::paint_round_field`），不要用 `RoundRect` 或窗口区域。
- 悬停样式只在 `WM_MOUSELEAVE`/进入时重画一次，逐次 `WM_MOUSEMOVE` 重画会闪烁。
- 分层窗口在首次显示前设好背景和透明色，首帧及子控件绘制完成后再揭开。
- 后台原生线程不要 `AttachThreadInput` 到前台线程：前台常是 Tauri WebView 线程，会与主线程互锁，对话框可见但不再处理消息。原生弹窗先 `SetForegroundWindow`（短暂重试）并临时置顶；必要时 `SwitchToThisWindow`。
- 从后台弹出的模态对话框在 `WM_INITDIALOG` 时先 `PostMessage`，显示后再请求前台；否则按键留在别的应用里，录制器收不到按键。

## 注意事项

+ 新项目，不考虑兼容旧 Python 数据格式之外的历史；旧明文 ZIP 不导入。
+ 修改符号时用 rust-analyzer/LSP 查找引用。
+ 所有设置页内容必须持久化；滑块/占位文字使用 150ms 防抖，退出时 flush。
- 只接受 `HWND`/`HDC` 等不透明句柄的内部辅助函数用普通 `fn`，内部调用 Win32 时再 `unsafe`；只有真正暴露裸指针、调用方必须满足内存约定的函数才标记 `unsafe fn`。窗口回调保持 `unsafe extern "system" fn`。
- 不要从 `GWLP_USERDATA`/`DWLP_USER` 派生 `&mut` 后跨 `SendMessageW`、`UpdateWindow` 等可能重入窗口过程的调用使用；用裸指针并在每次使用前重新派生，或把借用作用域压到单条语句。
+ 设置窗口按显示器 DPI 缩放（窗口、字体、行高、热区），嵌入编辑器同步；改动布局时保持 1120×820 逻辑坐标。
+ 单实例互斥体 `Local\Wordy.SingleInstance`，重复启动用 TaskDialog 提示。
+ 用户可见文案保持中文；公开 UI 不写内部实现说明。
