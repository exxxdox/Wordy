# Wordy 项目说明

## 架构

纯 Rust（edition 2024）Win32 应用，Cargo 管理。入口 `rust/main.rs` → `rust/app.rs:run()`。所有 UI、音频、TTS 均为原生实现，不再依赖 Python/Qt。

| 路径 | 职责 |
|------|------|
| `rust/app.rs` | 单实例、热键分发、窗口消息循环、设置动作、日志 |
| `rust/app/recording.rs` | 后台录制、音频截取与试听、异步导入与保存 |
| `rust/desktop.rs` | Win32 宿主窗口、托盘、通知和共用控件辅助函数 |
| `rust/desktop/overlay.rs` | 悬浮输入框、输入事件、背景和按钮绘制 |
| `rust/settings_window.rs` / `rust/settings_window/controls.rs` | 设置窗口、DPI 布局、设置控件绘制 |
| `rust/settings_backdrop.rs` | 设置窗口和嵌入控件共用的玻璃背景及绘制缓存 |
| `rust/panels.rs` / `rust/panels/` | 共用面板控件、轮盘编辑器、截取对话框和轮盘预览 |
| `rust/wheel.rs` / `wheel_paint.rs` / `wheel_window.rs` | 轮盘状态、四种样式绘制、运行时窗口 |
| `rust/audio.rs` + `rust/audio/` | WASAPI 设备枚举、播放/返听、VB-CABLE 路由、Windows 侦听、回环录制、裁剪/波形、托管音频 |
| `rust/tts.rs` + `rust/tts/realtime.rs` | TTS 引擎、Cartesia HTTP/WebSocket、Volcengine SSE、音色标签 |
| `rust/config.rs` | `Settings` 单一数据源、钳位、原子写入、150ms 防抖写盘 |
| `rust/backup.rs` / `backup_crypto.rs` / `credentials.rs` | 加密备份、AES-256-GCM/PBKDF2、Windows 凭据管理器 |
| `rust/hotkey.rs` / `instance.rs` / `file_log.rs` / `log_window.rs` | 快捷键解析与注册、单实例互斥体、日志轮转与日志窗口 |

持久化目录：`config_directory()`（`XDG_CONFIG_HOME/wordy`，未设置时 `%USERPROFILE%\.config\wordy`），含 `config.toml` 与 `audio_clips/`；托管音频从配置文件父目录推导。日志仅 `packaged` 构建在 exe 同目录写 `wordy.log`（5 MiB × 5）。图标资源在 `rust/assets/wordy.ico`，通过 `include_bytes!` 打入。

## 构建与验证

```powershell
.\wordy.ps1 init                # cargo fetch
.\wordy.ps1 dev                 # cargo run，保留控制台
.\wordy.ps1 build -Clean -NoPause # release + packaged → dist/Wordy.exe
cargo test                       # 全部 Rust 测试
```

发布工作流 `.github/workflows/release.yml` 使用 Rust stable、`cargo test --locked`，再调用 `wordy.ps1 build`。`dev.cmd` 仍转发 `wordy.ps1` 参数。

## 配置备份

设置页底部提供 `.wordybackup` 导入/导出：全部设置、轮盘音频、两家 TTS API Key。默认 exe 同目录（源码运行为项目根目录）。导出必须输入并确认非空密码，纯空白同样拒绝；密码不去掉首尾空格、不保存。ZIP 内容 AES-256-GCM 认证加密，随机盐/nonce，PBKDF2-SHA256 120 万次；导出流式写密文；旧明文 ZIP 不导入。认证通过后才解析配置/凭据，校验格式、大小、路径、字段、快捷键冲突，复制音频并原子提交；失败回滚配置、音频与密钥并如实提示。录制、裁剪、导入、后台保存、快捷键录制期间禁止导入。成功后退出重启；不删除原有音频。

## 播放与返听

「启用主播放输出」由 `main_playback_enabled` 持久化，默认开启。文字/音频/输入框朗读主输出固定为 WASAPI CABLE Input，不回落系统默认设备；缺失时设置页给下载入口、启动时发通知。旧配置 `virtual_output_device` 作为端点名称回退。返听独立开关，使用「返听播放设备」（默认跟随系统）；F10 裁剪试听走本地设备，拒绝 VB-CABLE。播放设备/开关变化后重建音频流。

「音频侦听」控制所选麦克风的 Windows 侦听：开启送到 CABLE Input，关闭关闭该麦克风。启动沿用 Windows 状态，设置页回显实际开关与输出目标（含设备不可用）；退出保留 Windows 状态；关闭失败保留原状态供重试。麦克风名称匹配先精确（忽略首尾空格与大小写），再唯一子串匹配。设备枚举失败时设置页显示空列表而不是报错中止。

## 轮盘

轮盘导入、播放和截取原生以外的音频格式时使用系统安装的 FFmpeg，从系统 `PATH` 和 WinGet 命令目录查找。有效的 PCM16 WAV 直接读取。缺少解码器时提示通过 WinGet 安装，发布产物只包含 `Wordy.exe`。

设置中的轮盘 UI 与运行时轮盘必须保持一致。样式由 `chat_wheel_style` 保存：`classic`/`glass`/`petals`/`game`，无效值回退经典；两处共用 `wheel_paint`。画布 634×540（盘体 540×540 居中），名称居中 hover 放大仍完整显示。动画仅影响显示：120ms 淡入、90ms 淡出、90ms 高亮；松键立即提交播放，淡出停止接收选择。

每盘八槽、独立快捷键、任意数量、至少一个。第一盘存 `chat_wheel_name/hotkey/slots/labels/texts`，后续在 `additional_chat_wheels`。快捷键不得与输入框/其他轮盘/后台录制重复。槽内容互斥：有文字则朗读，否则播放音频；切换会清空另一类。音频槽 `volumes` 0–300%，播放时快照传主输出与返听。托管音频在 `audio_clips`，优先硬链接。清空槽保留音量。删除/清空后仅删除无任何轮盘引用的文件。新增轮盘先弹窗输入快捷键，校验通过再注册。

轮盘音频可从文件导入，或通过后台录制保存到槽位。后台录制（默认 F10）环缓冲保留 `rolling_buffer_seconds`（5–300），停止后打开截取对话框；默认目标 `rolling_buffer_target`，保存成功后写回。名称默认「录音 HHMMSS」。WASAPI 回环先查可读帧再读，停止超时不得关闭仍在 read 的流；取消超时只记警告、不阻断关闭。

## TTS

- Cartesia：bytes（HTTP 整段）与 realtime（WebSocket，PCM16）；realtime 支持后台 keepalive、`ctx.cancel`，base URL 可被 `CARTESIA_BASE_URL` 覆盖；仅正常空闲关闭允许重连，其他关闭按错误处理。
- Volcengine：SSE 返回 `pcm_s16le`（base64），播放必须按 int16 打开。
- HTTP 使用 connect timeout，不设整请求总期限；最多跟随 10 次重定向。
- 音量 0.50–2.00 由 TTS 引擎应用；音频槽增益独立，不叠乘。
- 「播放时自动按住某键」：正式播放前按下，结束/失败/取消后松开；F10 试用跳过。输入框提交先收起窗口再提交。
- API Key 存 Windows 凭据管理器，不写入 TOML。
- 朗读可打断：新提交先停旧朗读/音频；取消只能协作式，禁止跨线程关流。退出时最多等待 worker 30 秒。

## 日志与通知

`log_level` 过滤 INFO/WARNING/ERROR/DEBUG；格式 `YYYY-MM-DD HH:MM:SS [LEVEL] target: message`。内存保留 5000 行，托盘「打开日志窗口」与设置页「查看日志」打开实时窗口。packaged 构建同步写 exe 旁 `wordy.log`，UTF-8、5 MiB 轮转、共 5 个文件。

通知通过 `AppUserModelId = Wordy.Desktop` 注册发送者并调用 Windows 通知中心；发送失败回退托盘气泡。图标写入配置目录 `notification-icon.ico`。

## 测试

`cargo test`；UI/音频测试使用真实 Win32 句柄在进程内创建，不依赖外部显示器。涉及配置的测试用临时目录，不读真实用户配置。新增/修改行为先补测试。禁止在日志或测试中写入真实 API Key。

## Win32 界面防闪烁

- 高频更新的自绘控件先在离屏合成背景和内容，再一次提交到窗口。波形进度只使旧、新游标区域失效，同一像素无需重画。自绘控件完整覆盖失效区域时，抑制默认背景擦除。
- 自绘滑块改变数值后由应用标记整个控件待重画，覆盖旧滑块和进度填充，避免依赖原生控件局部重画。
- 滑块连续变化时只同步对应的数值和关联预览。配置防抖写盘时不重建页面、下拉列表或无关控件。
- 分层窗口在首次显示前设好背景和透明色，首帧及子控件绘制完成后再揭开窗口。原生子控件的背景画刷应匹配应用表面或透明色，父窗口重画时裁剪子控件区域。
- 修改绘制顺序、控件显隐或高频刷新时，补对应回归测试，并检查首次唤出、反复打开、焦点与悬停切换及试听进度。

## 注意事项

+ 新项目，不考虑兼容旧 Python 数据格式之外的历史；旧明文 ZIP 不导入。
+ 修改符号时用 rust-analyzer/LSP 查找引用。
+ 所有设置页内容必须持久化；滑块/占位文字使用 150ms 防抖，退出时 flush。
+ 设置窗口按显示器 DPI 缩放（窗口、字体、行高、热区），嵌入编辑器同步；改动布局时保持 1120×820 逻辑坐标。
+ 单实例互斥体 `Local\Wordy.SingleInstance`，重复启动用 TaskDialog 提示。
+ 用户可见文案保持中文；公开 UI 不写内部实现说明。
