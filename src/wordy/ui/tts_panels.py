#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""TTS 服务商设置面板：每个 provider 提供独立的面板实现。

新增引擎时只需实现一个新 Panel 并注册到 ``PROVIDER_PANELS`` 即可，
无需修改 settings.py。
"""

from __future__ import annotations

from typing import Any, Protocol

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout,
)

import wordy.secret
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
        self, parent: QVBoxLayout, state: Any, on_refresh_voices: Any, on_apply: Any
    ) -> None:
        """首次构建面板 UI。"""
        ...

    def on_selected(self) -> None:
        """provider 被选中时调用（切换 provider、初次显示）。"""
        ...

    def sync_pending(self) -> None:
        """apply 前同步 pending 状态（从 UI 控件读取到 pending 字段）。"""
        ...

    def collect_pending(self, p: Any) -> None:
        """将 pending 状态写入 PendingSettings 对象。"""
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
    """Cartesia TTS 设置面板：API Key + 音色下拉 + 刷新按钮 + 生成模式。"""

    def __init__(self) -> None:
        self.pending_api_key_action = "unchanged"
        self.pending_api_key_value: str | None = None
        self.voice_label_to_id: dict[str, str] = {}
        self.voice_label_to_name: dict[str, str] = {}

    # ── build ────────────────────────────────────────────────────────

    def build(self, parent: QVBoxLayout, state, on_refresh_voices, on_apply) -> None:
        # API Key
        sec = self._create_section(parent)
        sec.addWidget(self._section_title("Cartesia API Key"))
        saved = "已保存" if state.cartesia_api_key_saved else "未保存"
        sec.addWidget(self._body_label(f"当前密钥：{saved}", TEXT_MUTED))
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(INLINE_GAP)
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        ph = "已保存；留空保持不变" if state.cartesia_api_key_saved else "未保存；粘贴新密钥后应用"
        self.api_key_input.setPlaceholderText(ph)
        self.api_key_input.textChanged.connect(self._on_key_text_changed)
        key_row.addWidget(self.api_key_input, 1)
        self.clear_key_btn = QPushButton("清除已保存密钥")
        self.clear_key_btn.clicked.connect(self._on_clear_clicked)
        key_row.addWidget(self.clear_key_btn)
        sec.addLayout(key_row)
        self.api_status = self._hint_label("支持粘贴 CARTESIA_API_KEY=...；输入内容只在应用时提交。", TEXT_MUTED)
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
        self._apply_voices(state.voices_cache, state.voice_id)
        if state.voices_loading:
            self.refresh_btn.setEnabled(False)
            self.refresh_btn.setText("加载中...")

    def on_selected(self) -> None:
        self._update_status()

    def _update_status(self) -> None:
        pass  # voice_status is set by set_voices_* methods

    # ── voice helpers ────────────────────────────────────────────────

    def _apply_voices(self, cache: list[dict], selected_id: str | None) -> None:
        maps = build_voice_label_maps(cache, selected_id)
        self.voice_combo.blockSignals(True)
        self.voice_combo.clear()
        self.voice_combo.addItems(maps.labels)
        if maps.selected_label:
            self.voice_combo.setCurrentText(maps.selected_label)
        elif maps.labels:
            self.voice_combo.setCurrentText(maps.labels[0])
        self.voice_combo.blockSignals(False)
        self.voice_label_to_id = maps.label_to_id
        self.voice_label_to_name = maps.label_to_name
        # 同步 pending 音色到当前选中项
        current_label = self.voice_combo.currentText()
        if current_label:
            self._pending_voice_id = self.voice_label_to_id.get(current_label)
            self._pending_voice_name = self.voice_label_to_name.get(current_label, current_label)

    def _on_voice_selected(self, label: str) -> None:
        vid = self.voice_label_to_id.get(label)
        vname = self.voice_label_to_name.get(label, label)
        if vid:
            self._pending_voice_id = vid
            self._pending_voice_name = vname

    @property
    def _pending_voice_id(self) -> str | None:
        return getattr(self, "_v_id", None)
    @_pending_voice_id.setter
    def _pending_voice_id(self, v: str | None) -> None:
        self._v_id = v

    @property
    def _pending_voice_name(self) -> str | None:
        return getattr(self, "_v_name", None)
    @_pending_voice_name.setter
    def _pending_voice_name(self, v: str | None) -> None:
        self._v_name = v

    # ── API key ──────────────────────────────────────────────────────

    def _on_key_text_changed(self, _text: str) -> None:
        self.pending_api_key_action = "unchanged"
        self.pending_api_key_value = None
        self._set_label(self.api_status, "输入新密钥后点击应用；留空保持不变。", TEXT_MUTED)

    def _on_clear_clicked(self, _checked: bool = False) -> None:
        self.api_key_input.blockSignals(True)
        self.api_key_input.clear()
        self.api_key_input.blockSignals(False)
        self.pending_api_key_action = "clear"
        self.pending_api_key_value = None
        self._set_label(self.api_status, "点击应用后清除已保存密钥。", TEXT_WARNING)

    # ── sync / collect ───────────────────────────────────────────────

    def sync_pending(self) -> None:
        # 用户已通过按钮选择了清楚，保持 clear 操作
        if self.pending_api_key_action in ("clear", "set"):
            return
        raw = self.api_key_input.text()
        normalized = wordy.secret.normalize_api_key_input(raw) if raw else ""
        if normalized:
            self.pending_api_key_action = "set"
            self.pending_api_key_value = normalized
            return
        self.pending_api_key_action = "unchanged"
        self.pending_api_key_value = None

    def collect_pending(self, p: Any) -> None:
        p.cartesia_api_key_action = self.pending_api_key_action
        p.cartesia_api_key_value = self.pending_api_key_value
        # 仅在用户实际选择音色后才覆盖，避免空值冲掉已保存的配置
        if self._pending_voice_id is not None:
            p.voice_id = self._pending_voice_id
            p.voice_name = self._pending_voice_name

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
        self.pending_access_key_action = "unchanged"
        self.pending_access_key_value: str | None = None
        self.pending_speaker_id: str | None = None

    # ── build ────────────────────────────────────────────────────────

    def build(self, parent: QVBoxLayout, state, on_refresh_voices, on_apply) -> None:
        # API Key
        sec = self._create_section(parent)
        sec.addWidget(self._section_title("火山引擎 API Key（X-Api-Key）"))
        saved = "已保存" if state.volcengine_access_key_saved else "未保存"
        sec.addWidget(self._body_label(f"当前密钥：{saved}", TEXT_MUTED))
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        ph = "已保存；留空保持不变" if state.volcengine_access_key_saved else "未保存；粘贴新密钥后应用"
        self.api_key_input.setPlaceholderText(ph)
        self.api_key_input.textChanged.connect(self._on_key_text_changed)
        key_row = QHBoxLayout()
        key_row.setContentsMargins(0, 0, 0, 0)
        key_row.setSpacing(INLINE_GAP)
        key_row.addWidget(self.api_key_input, 1)
        self.clear_key_btn = QPushButton("清除已保存密钥")
        self.clear_key_btn.clicked.connect(self._on_clear_clicked)
        key_row.addWidget(self.clear_key_btn)
        sec.addLayout(key_row)
        self.api_status = self._hint_label("输入火山引擎 API Key；只在应用时提交。", TEXT_MUTED)
        sec.addWidget(self.api_status)
        parent.addSpacing(12)

        # Speaker ID
        sec2 = self._create_section(parent)
        sec2.addWidget(self._section_title("Speaker ID"))
        self.voice_status = self._hint_label("输入火山引擎控制台中的 Speaker ID", TEXT_MUTED)
        sec2.addWidget(self.voice_status)
        self.speaker_input = QLineEdit()
        self.speaker_input.setPlaceholderText("Speaker ID，如 BV001_streaming")
        # 优先用 Volcengine 专属的 voice_id，回退到通用 voice_id
        ve_vid = getattr(state, "volcengine_voice_id", None) or state.voice_id
        if ve_vid:
            self.speaker_input.setText(str(ve_vid))
        self.speaker_input.textChanged.connect(self._on_speaker_changed)
        sec2.addWidget(self.speaker_input)

    def on_selected(self) -> None:
        pass

    # ── handlers ─────────────────────────────────────────────────────

    def _on_key_text_changed(self, _text: str) -> None:
        self.pending_access_key_action = "unchanged"
        self.pending_access_key_value = None
        self._set_label(self.api_status, "输入新密钥后点击应用；留空保持不变。", TEXT_MUTED)

    def _on_clear_clicked(self, _checked: bool = False) -> None:
        self.api_key_input.blockSignals(True)
        self.api_key_input.clear()
        self.api_key_input.blockSignals(False)
        self.pending_access_key_action = "clear"
        self.pending_access_key_value = None
        self._set_label(self.api_status, "点击应用后清除已保存密钥。", TEXT_WARNING)

    def _on_speaker_changed(self, text: str) -> None:
        sid = text.strip()
        self.pending_speaker_id = sid if sid else None

    # ── sync / collect ───────────────────────────────────────────────

    def sync_pending(self) -> None:
        if self.pending_access_key_action in ("clear", "set"):
            pass  # 保持用户选择的操作
        else:
            raw = self.api_key_input.text()
            normalized = raw.strip() if raw else ""
            if normalized:
                self.pending_access_key_action = "set"
                self.pending_access_key_value = normalized
            else:
                self.pending_access_key_action = "unchanged"
                self.pending_access_key_value = None
        # speaker ID
        self.pending_speaker_id = self.speaker_input.text().strip() or None

    def collect_pending(self, p: Any) -> None:
        p.volcengine_access_key_action = self.pending_access_key_action
        p.volcengine_access_key_value = self.pending_access_key_value
        if self.pending_speaker_id is not None:
            p.voice_id = self.pending_speaker_id
            p.voice_name = self.pending_speaker_id

    # ── status ───────────────────────────────────────────────────────

    def set_voices_loading(self) -> None:
        pass  # Volcengine 无音色加载

    def set_voices_error(self, error: Exception | str) -> None:
        pass

    def set_voices_loaded(self, voices: list[dict], selected_voice_id: str | None) -> list[str]:
        return []  # Volcengine 无在线音色列表


# ── 面板注册表 ────────────────────────────────────────────────────────

from wordy.tts.constants import TTS_API_PROVIDER_CARTESIA, TTS_API_PROVIDER_VOLCENGINE

PROVIDER_PANELS: dict[str, Any] = {
    TTS_API_PROVIDER_CARTESIA: CartesiaPanel(),
    TTS_API_PROVIDER_VOLCENGINE: VolcenginePanel(),
}
