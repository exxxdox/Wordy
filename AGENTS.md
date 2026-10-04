# 项目模块约定

## 脚本入口

`wordy.ps1` 统一提供 `init`（初始化）、`dev`（启动，默认）和 `build`（打包）。`dev.cmd` 保留为参数转发入口；发布工作流使用 `wordy.ps1 build -Clean -NoPause`。

## 音频设备枚举

`audio/capture.py` 只提供 `list_input_devices()`，供设置页面枚举输入设备。麦克风侦听由 Windows 原生策略管理，不维护软件录音流。

## TTS Worker 生命周期

`WordyApp` 在 GUI 线程委托 `TTSManager` 提交操作。任务绑定提交时的引擎；预热和播放共用 worker executor。切换后端/API key 时，janitor 先等待旧任务完成，再关闭旧引擎并释放后继事件。新 worker 可立即接收任务，播放/预热等待旧 worker 清理后才使用共享播放器，避免阻塞 UI 和跨引擎关闭音频流。测试直接访问 `tts_manager`。

## 音频聊天轮盘

轮盘默认 F8，独立于输入框快捷键；按住唤醒，八向选择，松开播放，中心/Esc/右键取消；唤出与收起均不移动光标，方向以唤出点为锚点，屏幕边缘钳位只影响窗口。窗口不抢焦点。八槽文件名、名称与快捷键由 `AppSettings` 持久化。音频存于配置文件旁的 `audio_clips`，Windows 优先硬链接，失败复制；硬链接共享内容。Qt 解码成临时 PCM WAV，经 `TTSManager` 当前 worker 串行播放并等待交接，复用输出路由与返听，不依赖 TTS 密钥或音色。文件替换/清空先保存配置，再删除未引用托管文件。
