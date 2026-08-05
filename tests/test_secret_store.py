#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for secret_store.py — keyring-only API key storage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import wordy.secret as secret_store


# ---------------------------------------------------------------------------
# S1: normalize_api_key_input
# ---------------------------------------------------------------------------

class TestNormalizeApiKeyInput:

    def test_strips_whitespace(self) -> None:
        assert secret_store.normalize_api_key_input("  sk-abc  ") == "sk-abc"

    def test_strips_cartesia_api_key_prefix(self) -> None:
        assert secret_store.normalize_api_key_input("CARTESIA_API_KEY=sk-abc") == "sk-abc"

    def test_strips_prefix_and_whitespace(self) -> None:
        assert secret_store.normalize_api_key_input("  CARTESIA_API_KEY=sk-abc  ") == "sk-abc"

    def test_none_returns_empty(self) -> None:
        assert secret_store.normalize_api_key_input(None) == ""

    def test_returns_empty_on_empty(self) -> None:
        assert secret_store.normalize_api_key_input("") == ""


# ---------------------------------------------------------------------------
# S2: keyring save / load / delete / status — success path
# ---------------------------------------------------------------------------

class TestKeyringSuccessPath:

    def test_save_returns_storage_status(self, fake_keyring) -> None:
        status = secret_store.save_cartesia_api_key("sk-test-save")
        assert status.has_key is True
        assert status.backend == secret_store.STORAGE_KEYRING
        assert status.keyring_available is True

    def test_load_after_save_returns_value(self, fake_keyring) -> None:
        secret_store.save_cartesia_api_key("sk-test-load")
        assert secret_store.load_cartesia_api_key() == "sk-test-load"

    def test_load_returns_none_when_absent(self, fake_keyring) -> None:
        assert secret_store.load_cartesia_api_key() is None

    def test_delete_clears_keyring(self, fake_keyring) -> None:
        secret_store.save_cartesia_api_key("sk-test-del")
        status = secret_store.delete_cartesia_api_key()
        assert status.has_key is False
        assert status.backend == secret_store.STORAGE_NONE
        assert secret_store.load_cartesia_api_key() is None

    def test_api_key_status_reports_key_present(self, fake_keyring) -> None:
        secret_store.save_cartesia_api_key("sk-test-status")
        s = secret_store.get_cartesia_api_key_status()
        assert s["cartesia_api_key_set"] is True
        assert s["cartesia_api_key_storage"] == secret_store.STORAGE_KEYRING

    def test_api_key_status_reports_no_key(self, fake_keyring) -> None:
        s = secret_store.get_cartesia_api_key_status()
        assert s["cartesia_api_key_set"] is False


# ---------------------------------------------------------------------------
# S2: keyring unavailable
# ---------------------------------------------------------------------------

class TestKeyringUnavailable:

    def test_save_raises_when_keyring_unavailable(self, keyring_unavailable) -> None:
        with pytest.raises(secret_store.KeyringUnavailableError) as exc:
            secret_store.save_cartesia_api_key("sk-abc")
        msg = str(exc.value)
        assert "SENTINEL" not in msg
        assert "sk-abc" not in msg

    def test_load_raises_when_keyring_unavailable(self, keyring_unavailable) -> None:
        with pytest.raises(secret_store.KeyringUnavailableError) as exc:
            secret_store.load_cartesia_api_key()
        assert "SENTINEL" not in str(exc.value)

    def test_delete_does_not_raise_when_keyring_unavailable(self, keyring_unavailable) -> None:
        status = secret_store.delete_cartesia_api_key()
        assert status.has_key is False

    def test_api_key_status_reports_no_key_when_unavailable(self, keyring_unavailable) -> None:
        s = secret_store.get_cartesia_api_key_status()
        assert s["cartesia_api_key_set"] is False
        assert s["cartesia_api_key_storage"] == secret_store.STORAGE_NONE


# ---------------------------------------------------------------------------
# StorageStatus safety
# ---------------------------------------------------------------------------

class TestStorageStatusSafety:

    def test_storage_status_does_not_expose_raw_key(self, fake_keyring) -> None:
        raw = "sk-hidden-no-leak"
        status = secret_store.save_cartesia_api_key(raw)
        assert raw not in str(status)
        assert raw not in repr(status)

    def test_config_never_contains_key_material(self, tmp_path: Path) -> None:
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({"hotkey": "f6", "volume": 1.0}))
        data = json.loads(cfg.read_text())
        for val in data.values():
            if isinstance(val, str) and len(val) > 10:
                assert "sk-" not in val, "Config leaked key material"
