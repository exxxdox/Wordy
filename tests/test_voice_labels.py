#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for layer-neutral voice_labels module and import boundary."""

import ast
from pathlib import Path

from wordy.tts.labels import VoiceLabelMaps, build_voice_label_maps


class TestBuildVoiceLabelMaps:
    def test_unique_names_no_suffix(self):
        voices = [
            {"id": "id1", "name": "Alice"},
            {"id": "id2", "name": "Bob"},
        ]
        result = build_voice_label_maps(voices, selected_voice_id=None)

        assert isinstance(result, VoiceLabelMaps)
        assert result.labels == ["Alice", "Bob"]
        assert result.label_to_id == {"Alice": "id1", "Bob": "id2"}
        assert result.label_to_name == {"Alice": "Alice", "Bob": "Bob"}
        assert result.selected_label is None

    def test_duplicate_names_suffixed_with_id_prefix(self):
        voices = [
            {"id": "abcdef12345", "name": "Alice"},
            {"id": "abcdef67890", "name": "Alice"},
        ]
        result = build_voice_label_maps(voices, selected_voice_id="abcdef67890")

        assert result.labels == ["Alice", "Alice (abcdef67)"]
        assert result.label_to_id["Alice (abcdef67)"] == "abcdef67890"
        assert result.label_to_name["Alice (abcdef67)"] == "Alice"
        assert result.selected_label == "Alice (abcdef67)"

    def test_non_string_entries_skipped(self):
        voices = [
            {"id": "id1", "name": "Alice"},
            {"id": 123, "name": "Bad"},
            {"id": "id3", "name": None},
        ]
        result = build_voice_label_maps(voices, selected_voice_id=None)

        assert result.labels == ["Alice"]
        assert result.label_to_id == {"Alice": "id1"}

    def test_selected_voice_id_not_present(self):
        voices = [{"id": "id1", "name": "Alice"}]
        result = build_voice_label_maps(voices, selected_voice_id="missing")

        assert result.selected_label is None


class TestImportLayering:
    """settings_window.py 不应再依赖 cartesia_connect 拿到 voice label 辅助。"""

    def test_settings_window_does_not_import_from_cartesia_connect(self):
        repo_root = Path(__file__).resolve().parent.parent
        source = (repo_root / "src" / "wordy" / "ui" / "settings.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        offending: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "wordy.tts.cartesia":
                names = [alias.name for alias in node.names]
                if "build_voice_label_maps" in names or "VoiceLabelMaps" in names:
                    offending.append(", ".join(names))

        assert not offending, (
            "settings.py must import voice label helpers from "
            "wordy.tts.labels, not wordy.tts.cartesia. "
            f"Offending imports: {offending}"
        )
