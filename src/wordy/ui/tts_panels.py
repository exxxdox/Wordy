#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS 服务商设置面板：每个 provider 提供独立的面板实现。

新增引擎时只需实现一个新 Panel 并注册到 ``PROVIDER_PANELS`` 即可，
无需修改 settings.py。
"""

from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

import wordy.secret
from wordy.config import AppSettings
from wordy.tts.labels import VoiceLabelMaps, build_voice_label_maps
from wordy.ui.theme import (
    GREEN_ACCENT, TEXT_ERROR, TEXT_MUTED, TEXT_WARNING,
)
from wordy.ui.settings_widgets import NoWheelComboBox

INPUT_TEXT_COLOR = GREEN_ACCENT
INLINE_GAP = 10
BUTTON_MIN_WIDTH = 88


class TTSProviderPanel(Protocol):
    """每个 TTS 服务商提供一个面板，贡献自己的设置 UI 控件。"""

    def build(
        self, parent: QVBoxLayout, state: Any, on_refresh_voices: Any,
        on_field_changed: Any = None,
    ) -> None:
        """首次构建面板 UI。"""
        ...

    def on_selected(self) -> None:
        """provider 被选中时调用（切换 provider、初次显示）。"""
        ...

    def set_voices_loading(self) -> None:
        """音色加载中状态。"""
        ...

    def set_voices_error(self, error: Exception | str) -> None:
        """音色加载失败状态。"""
        ...

    def set_voices_loaded(
        self, voices: list[dict[str, object]], selected_voice_id: str | None
    ) -> list[str]:
        """音色加载完成，返回 label 列表。"""
        ...


# ── 面板基类：共享控件构建 ────────────────────────────────────────────


class _BasePanel:
    """提供 _section_title / _body_label / _hint_label 快捷方法。"""

    @staticmethod
    def _section_title(text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionTitle")
        return label

    @staticmethod
    def _body_label(text: str, color: str, emphasis: bool = False) -> QLabel:
        label = QLabel(text)
        label.setObjectName("bodyLabelEmphasis" if emphasis else "bodyLabel")
        label.setStyleSheet(f"color: {color};")
        return label

    @staticmethod
    def _hint_label(text: str, color: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("hintLabel")
        label.setStyleSheet(f"color: {color};")
        label.setWordWrap(True)
        return label

    @staticmethod
    def _set_label(widget: QLabel, text: str, color: str) -> None:
        widget.setText(text)
        widget.setStyleSheet(f"color: {color};")

    @staticmethod
    def _create_section(parent: QVBoxLayout) -> QVBoxLayout:
        section = QVBoxLayout()
        section.setContentsMargins(0, 0, 0, 0)
        section.setSpacing(6)
        parent.addLayout(section)
        return section


# ── Cartesia 面板 ─────────────────────────────────────────────────────


class CartesiaPanel(_BasePanel):
    """Cartesia TTS 设置面板：API Key + 音色下拉 + 刷新按钮。"""

    def __init__(self) -> None:
        self.voice_label_to_id: dict[str, str] = {}
        self.voice_label_to_name: dict[str, str] = {}

    # ── build ────────────────────────────────────────────────────────

    def build(self, parent: QVBoxLayout, state, on_refresh_voices, on_field_changed=None) -> None:
        self._on_field_changed = on_field_changed
        # API Key
        sec = self._create_section(parent)
        sec.addWidget(self._section_title("Cartesia API Key"))
        saved = "已保存" if state.cartesia_api_key_saved else "未保存"
        self.key_status_label = self._body_label(f"当前密钥：{saved}", TEXT_MUTED)
        sec.addWidget(self.key_status_label)
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(INLINE_GAP)
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        ph = "粘贴新密钥后点击保存" if state.cartesia_api_key_saved else "粘贴密钥"
        self.api_key_input.setPlaceholderText(ph)
        key_row.addWidget(self.api_key_input, 1)
        self.save_key_btn = QPushButton("保存")
        self.save_key_btn.setMinimumWidth(BUTTON_MIN_WIDTH)
        self.save_key_btn.clicked.connect(self._on_save_key)
        key_row.addWidget(self.save_key_btn)
        self.clear_key_btn = QPushButton("清除")
        self.clear_key_btn.setMinimumWidth(BUTTON_MIN_WIDTH)
        self.clear_key_btn.clicked.connect(self._on_clear_key)
        key_row.addWidget(self.clear_key_btn)
        sec.addLayout(key_row)
        self.api_status = self._hint_label("粘贴 API Key 后点击保存，立即生效。", TEXT_MUTED)
        sec.addWidget(self.api_status)
        parent.addSpacing(12)

        # 音色
        sec2 = self._create_section(parent)
        self.voice_title = self._section_title("音色设置")
        sec2.addWidget(self.voice_title)
        self.voice_status = self._hint_label("", TEXT_MUTED)
        sec2.addWidget(self.voice_status)
        voice_row = QHBoxLayout()
        voice_row.setContentsMargins(0, 0, 0, 0)
        voice_row.setSpacing(INLINE_GAP)
        self.voice_combo = NoWheelComboBox()
        self.voice_combo.setMinimumContentsLength(24)
        self.voice_combo.currentTextChanged.connect(self._on_voice_selected)
        voice_row.addWidget(self.voice_combo, 1)
        self.refresh_btn = QPushButton("刷新音色列表")
        self.refresh_btn.clicked.connect(lambda _checked=False: on_refresh_voices())
        voice_row.addWidget(self.refresh_btn)
        sec2.addLayout(voice_row)
        self._apply_voices(state.voices_cache, AppSettings.load().cartesia_voice_id)
        if state.voices_loading:
            self.refresh_btn.setEnabled(False)
            self.refresh_btn.setText("加载中...")

    def on_selected(self) -> None:
        pass

    # ── voice helpers ────────────────────────────────────────────────

    NONE_VOICE_LABEL = "无（不启用 TTS 音色）"

    def _apply_voices(self, cache: list[dict], selected_id: str | None) -> None:
        """填充音色下拉列表，首项为"无"。"""
        maps = build_voice_label_maps(cache, selected_id)
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        # 首项：无——显式表示不启用音色
        self.voice_combo.addItem(self.NONE_VOICE_LABEL)
        self.voice_combo.addItems(maps.labels)
        if maps.selected_label:
            self.voice_combo.setCurrentText(maps.selected_label)
        else:
            # 无匹配音色时默认选"无"
            self.voice_combo.setCurrentText(self.NONE_VOICE_LABEL)
        self.voice_combo.blockSignals(False)
        self.voice_label_to_id = maps.label_to_id
        self.voice_label_to_name = maps.label_to_name

    def _on_voice_selected(self, label: str) -> None:
        """音色选择：直接写入 AppSettings 并通知 overlay 重建引擎。"""
        if label == self.NONE_VOICE_LABEL:
            s = AppSettings.load()
            s.cartesia_voice_id = None
            s.cartesia_voice_name = None
            return
        vid = self.voice_label_to_id.get(label)
        vname = self.voice_label_to_name.get(label, label)
        if vid:
            s = AppSettings.load()
            s.cartesia_voice_id = vid  # auto-save + notify listeners
            s.cartesia_voice_name = vname
            # 通知 overlay → WordyApp 重建 TTS 引擎
            if self._on_field_changed is not None:
                self._on_field_changed("cartesia_voice_id", vid)

    # ── API key ──────────────────────────────────────────────────────

    def _on_save_key(self, _checked: bool = False) -> None:
        """保存 API Key 到 keyring 并触发引擎重建 + 音色刷新。"""
        raw = self.api_key_input.text()
        normalized = wordy.secret.normalize_api_key_input(raw) if raw else ""
        if not normalized:
            self._set_label(self.api_status, "请输入有效的 API Key。", TEXT_WARNING)
            return
        try:
            wordy.secret.save_cartesia_api_key(normalized)
            self.api_key_input.clear()
            AppSettings.load().cartesia_api_key_set = True
            # 更新"当前密钥"状态标签
            self._set_label(self.key_status_label, "当前密钥：已保存", INPUT_TEXT_COLOR)
            self._set_label(self.api_status, "粘贴 API Key 后点击保存，立即生效。", TEXT_MUTED)
            # 通知 overlay 重建 TTS 引擎 + 自动加载音色列表
            if self._on_field_changed is not None:
                self._on_field_changed("cartesia_api_key", normalized)
        except Exception as e:
            self._set_label(self.api_status, f"保存失败：{e}", TEXT_ERROR)

    def _on_clear_key(self, _checked: bool = False) -> None:
        """清除已保存的 API Key 并触发引擎重建。"""
        try:
            wordy.secret.delete_cartesia_api_key()
            AppSettings.load().cartesia_api_key_set = False
            self.api_key_input.clear()
            self._set_label(self.key_status_label, "当前密钥：未保存", TEXT_MUTED)
            self._set_label(self.api_status, "API Key 已清除。", INPUT_TEXT_COLOR)
            # 通知 overlay 重建引擎（无密钥状态）
            if self._on_field_changed is not None:
                self._on_field_changed("cartesia_api_key", None)
        except Exception as e:
            self._set_label(self.api_status, f"清除失败：{e}", TEXT_ERROR)

    # ── status ───────────────────────────────────────────────────────

    def set_voices_loading(self) -> None:
        self.refresh_btn.setEnabled(False)
        self.refresh_btn.setText("加载中...")
        self._set_label(self.voice_status, "正在加载音色列表...", TEXT_WARNING)

    def set_voices_error(self, error: Exception | str) -> None:
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新音色列表")
        self._set_label(self.voice_status, f"加载失败：{error}", TEXT_ERROR)

    def set_voices_loaded(self, voices: list[dict], selected_voice_id: str | None) -> list[str]:
        self.refresh_btn.setEnabled(True)
        self.refresh_btn.setText("刷新音色列表")
        self._apply_voices(voices, selected_voice_id)
        labels = list(self.voice_label_to_id.keys())
        self._set_label(self.voice_status, f"已加载 {len(labels)} 个音色", INPUT_TEXT_COLOR)
        return labels


# ── Volcengine 面板 ───────────────────────────────────────────────────


class VolcenginePanel(_BasePanel):
    """Volcengine TTS 设置面板：API Key + Speaker ID 文本框。"""

    def __init__(self) -> None:
        pass

    # ── build ────────────────────────────────────────────────────────

    def build(self, parent: QVBoxLayout, state, on_refresh_voices, on_field_changed=None) -> None:
        self._on_field_changed = on_field_changed
        # API Key
        sec = self._create_section(parent)
        sec.addWidget(self._section_title("火山引擎 API Key（X-Api-Key）"))
        saved = "已保存" if state.volcengine_access_key_saved else "未保存"
        self.key_status_label = self._body_label(f"当前密钥：{saved}", TEXT_MUTED)
        sec.addWidget(self.key_status_label)
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        ph = "粘贴新密钥后点击保存" if state.volcengine_access_key_saved else "粘贴密钥"
        self.api_key_input.setPlaceholderText(ph)
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(INLINE_GAP)
        key_row.addWidget(self.api_key_input, 1)
        self.save_key_btn = QPushButton("保存")
        self.save_key_btn.setMinimumWidth(BUTTON_MIN_WIDTH)
        self.save_key_btn.clicked.connect(self._on_save_key)
        key_row.addWidget(self.save_key_btn)
        self.clear_key_btn = QPushButton("清除")
        self.clear_key_btn.setMinimumWidth(BUTTON_MIN_WIDTH)
        self.clear_key_btn.clicked.connect(self._on_clear_key)
        key_row.addWidget(self.clear_key_btn)
        sec.addLayout(key_row)
        self.api_status = self._hint_label("粘贴 API Key 后点击保存，立即生效。", TEXT_MUTED)
        sec.addWidget(self.api_status)
        parent.addSpacing(12)

        # Speaker ID
        sec2 = self._create_section(parent)
        sec2.addWidget(self._section_title("Speaker ID"))
        self.voice_status = self._hint_label("输入火山引擎控制台中的 Speaker ID", TEXT_MUTED)
        sec2.addWidget(self.voice_status)
        self.speaker_input = QLineEdit()
        self.speaker_input.setPlaceholderText("Speaker ID，如 BV001_streaming")
        vid = AppSettings.load().volcengine_voice_id
        if vid:
            self.speaker_input.setText(vid)
        self.speaker_input.textChanged.connect(self._on_speaker_changed)
        sec2.addWidget(self.speaker_input)

    def on_selected(self) -> None:
        pass

    # ── handlers ─────────────────────────────────────────────────────

    def _on_save_key(self, _checked: bool = False) -> None:
        """保存 Access Key 到 keyring 并触发引擎重建。"""
        raw = self.api_key_input.text()
        normalized = raw.strip() if raw else ""
        if not normalized:
            self._set_label(self.api_status, "请输入有效的 Access Key。", TEXT_WARNING)
            return
        try:
            wordy.secret.save_volcengine_access_key(normalized)
            self.api_key_input.clear()
            AppSettings.load().volcengine_access_key_set = True
            # 更新"当前密钥"状态标签
            self._set_label(self.key_status_label, "当前密钥：已保存", INPUT_TEXT_COLOR)
            self._set_label(self.api_status, "粘贴 API Key 后点击保存，立即生效。", TEXT_MUTED)
            # 通知 overlay 重建 TTS 引擎
            if self._on_field_changed is not None:
                self._on_field_changed("volcengine_access_key", normalized)
        except Exception as e:
            self._set_label(self.api_status, f"保存失败：{e}", TEXT_ERROR)

    def _on_clear_key(self, _checked: bool = False) -> None:
        """清除已保存的 Access Key 并触发引擎重建。"""
        try:
            wordy.secret.delete_volcengine_access_key()
            AppSettings.load().volcengine_access_key_set = False
            self.api_key_input.clear()
            self._set_label(self.key_status_label, "当前密钥：未保存", TEXT_MUTED)
            self._set_label(self.api_status, "Access Key 已清除。", INPUT_TEXT_COLOR)
            # 通知 overlay 重建引擎（无密钥状态）
            if self._on_field_changed is not None:
                self._on_field_changed("volcengine_access_key", None)
        except Exception as e:
            self._set_label(self.api_status, f"清除失败：{e}", TEXT_ERROR)

    def _on_speaker_changed(self, text: str) -> None:
        """Speaker ID 变更：直接写入 AppSettings 并通知 overlay 重建引擎。"""
        sid = text.strip()
        s = AppSettings.load()
        s.volcengine_voice_id = sid if sid else None
        s.volcengine_voice_name = sid if sid else None
        if self._on_field_changed is not None:
            self._on_field_changed("volcengine_voice_id", sid if sid else None)

    # ── status ───────────────────────────────────────────────────────

    def set_voices_loading(self) -> None:
        pass

    def set_voices_error(self, error: Exception | str) -> None:
        pass

    def set_voices_loaded(self, voices: list[dict], selected_voice_id: str | None) -> list[str]:
        return []


# ── 面板注册表 ────────────────────────────────────────────────────────

from wordy.tts.constants import TTS_API_PROVIDER_CARTESIA, TTS_API_PROVIDER_VOLCENGINE

PROVIDER_PANELS: dict[str, Any] = {
    TTS_API_PROVIDER_CARTESIA: CartesiaPanel(),
    TTS_API_PROVIDER_VOLCENGINE: VolcenginePanel(),
}
