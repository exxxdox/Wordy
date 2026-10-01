# 项目模块约定

## 脚本入口

`wordy.ps1` 统一提供 `init`（初始化）、`dev`（启动，默认）和 `build`（打包）。`dev.cmd` 保留为参数转发入口；发布工作流使用 `wordy.ps1 build -Clean -NoPause`。

## 音频设备枚举

`audio/capture.py` 只提供 `list_input_devices()`，供设置页面枚举输入设备。麦克风侦听由 Windows 原生策略管理，不维护软件录音流。

## TTS Worker 生命周期

`WordyApp` 在 GUI 线程委托 `TTSManager` 提交操作。任务绑定提交时的引擎；预热和播放共用 worker executor。切换后端/API key 时，janitor 先等待旧任务完成，再关闭旧引擎并释放后继事件。新 worker 可立即接收任务，播放/预热等待旧 worker 清理后才使用共享播放器，避免阻塞 UI 和跨引擎关闭音频流。测试直接访问 `tts_manager`。
