#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置界面和音频播放共用的常量。"""

MIN_VOLUME = 0.5
MAX_VOLUME = 2.0
VOLUME_STEP = 0.05
# 文件播放的增益上限由滑块、配置和播放器共用，与朗读音量独立。
MAX_AUDIO_FILE_VOLUME = 3.0
MIN_OVERLAY_OPACITY = 0.30
MAX_OVERLAY_OPACITY = 1.0
OVERLAY_OPACITY_STEP = 0.05
LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
