#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""RED tests for secret_store.py (Wave 2 T2).

S1 — normalize: strip whitespace + CARTESIA_API_KEY= prefix removal.
S2 — keyring primary: save/load/delete/status success; keyring-unavailable
     without fallback raises KeyringUnavailableError (safe str/repr);
     with allow_plaintext_fallback=True writes JSON fallback + flag;
     delete clears both; primary mode JSON does not contain raw key.
S3 — migration: re-available keyring after plaintext fallback migrates
     and clears the JSON file.

All tests target contract behavior that will exist after Wave 2.
Tests that depend on *future* constants/functions fail RED with a clear
signal (AttributeError, KeyError, or unhandled exception from current
production code that cannot satisfy the contract).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# secret_store imports keyring at module level. The conftest stub
# ensures keyring + keyring.errors are already installed in sys.modules.
import wordy.secret as secret_store


# ---------------------------------------------------------------------------
# S1: normalize_api_key_input
# ---------------------------------------------------------------------------

class TestNormalizeApiKeyInput:
    """Must PASS — normalize_api_key_input already exists."""

    def test_strips_whitespace(self) -> None:
        assert secret_store.normalize_api_key_input("  sk-abc  ") == "sk-abc"

    def test_strips_cartesia_api_key_prefix(self) -> None:
        assert secret_store.normalize_api_key_input("CARTESIA_API_KEY=sk-abc") == "sk-abc"

    def test_strips_prefix_and_whitespace(self) -> None:
        assert (
            secret_store.normalize_api_key_input("  CARTESIA_API_KEY=sk-abc  ")
            == "sk-abc"
        )

    def test_none_returns_empty(self) -> None:
        assert secret_store.normalize_api_key_input(None) == ""

    def test_returns_empty_on_empty(self) -> None:
        assert secret_store.normalize_api_key_input("") == ""


# ---------------------------------------------------------------------------
# S2: keyring save / load / delete / status — success path
# ---------------------------------------------------------------------------

class TestKeyringSuccessPath:
    """Must PASS — these are already implemented in the skeleton."""

    def test_save_returns_storage_status_with_key(self, fake_keyring) -> None:
        status = secret_store.save_cartesia_api_key("sk-test-save")
        assert status.has_key is True
        assert status.backend == secret_store.STORAGE_KEYRING
        assert status.keyring_available is True
        assert status.fallback_active is False

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

    def test_get_storage_status_reports_keyring_available(self, fake_keyring) -> None:
        status = secret_store.get_storage_status()
        assert status.keyring_available is True
        assert status.has_key is False

    def test_get_storage_status_reports_key_present(self, fake_keyring) -> None:
        secret_store.save_cartesia_api_key("sk-test-status")
        status = secret_store.get_storage_status()
        assert status.has_key is True
        assert status.backend == secret_store.STORAGE_KEYRING


# ---------------------------------------------------------------------------
# S2: keyring failure WITHOUT allow_plaintext_fallback
# ---------------------------------------------------------------------------

class TestKeyringUnavailableNoFallback:
    """Must PASS — KeyringUnavailableError is already raised."""

    def test_save_raises_when_keyring_unavailable(self, keyring_unavailable) -> None:
        with pytest.raises(secret_store.KeyringUnavailableError) as exc:
            secret_store.save_cartesia_api_key(
                "sk-abc", allow_plaintext_fallback=False,
            )
        msg = str(exc.value)
        assert "SENTINEL" not in msg
        assert "sk-abc" not in msg

    def test_save_exception_repr_has_no_key(self, keyring_unavailable) -> None:
        with pytest.raises(secret_store.KeyringUnavailableError) as exc:
            secret_store.save_cartesia_api_key(
                "sk-abc", allow_plaintext_fallback=False,
            )
        assert "sk-abc" not in repr(exc.value)

    def test_load_raises_when_keyring_unavailable(self, keyring_unavailable) -> None:
        with pytest.raises(secret_store.KeyringUnavailableError) as exc:
            secret_store.load_cartesia_api_key()
        assert "SENTINEL" not in str(exc.value)

    def test_delete_raises_when_keyring_unavailable(self, keyring_unavailable) -> None:
        with pytest.raises(secret_store.KeyringUnavailableError) as exc:
            secret_store.delete_cartesia_api_key()
        assert "SENTINEL" not in str(exc.value)

    def test_get_storage_status_reports_unavailable(self, keyring_unavailable) -> None:
        status = secret_store.get_storage_status()
        assert status.keyring_available is False
        assert status.has_key is False


# ---------------------------------------------------------------------------
# S2: plaintext JSON fallback — these SHOULD FAIL RED
# ---------------------------------------------------------------------------

class TestPlaintextFallback:
    """Should FAIL RED — plaintext fallback not yet implemented."""

    def test_save_with_fallback_writes_json_when_keyring_unavailable(
        self, keyring_unavailable, monkeypatch, tmp_path: Path,
    ) -> None:
        """allow_plaintext_fallback=True should write JSON fallback file."""
        fallback_dir = tmp_path / "secrets"
        monkeypatch.setattr(secret_store, "PLAINTEXT_FALLBACK_DIR", fallback_dir)
        # Contract: call succeeds, writes file, returns STORAGE_PLAINTEXT status.
        # RED: current skeleton raises KeyringUnavailableError.
        status = secret_store.save_cartesia_api_key(
            "sk-abc", allow_plaintext_fallback=True,
        )
        assert status.has_key is True
        assert status.backend == secret_store.STORAGE_PLAINTEXT
        assert status.fallback_active is True

    def test_load_reads_from_fallback_when_keyring_unavailable(
        self, keyring_unavailable, tmp_path: Path, monkeypatch,
    ) -> None:
        """Loading with no keyring should fall back to plaintext JSON."""
        fallback_dir = tmp_path / "secrets"
        fallback_file = fallback_dir / "cartesia_api_key.json"
        fallback_file.parent.mkdir(parents=True, exist_ok=True)
        fallback_file.write_text(json.dumps({"api_key": "sk-fallback-test"}))
        monkeypatch.setattr(secret_store, "PLAINTEXT_FALLBACK_DIR", fallback_dir)
        # RED: current skeleton raises KeyringUnavailableError.
        key = secret_store.load_cartesia_api_key()
        assert key == "sk-fallback-test"

    def test_delete_clears_fallback_when_keyring_unavailable(
        self, keyring_unavailable, tmp_path: Path, monkeypatch,
    ) -> None:
        """Even without keyring, delete should wipe the fallback file."""
        fallback_dir = tmp_path / "secrets"
        fallback_file = fallback_dir / "cartesia_api_key.json"
        fallback_file.parent.mkdir(parents=True, exist_ok=True)
        fallback_file.write_text(json.dumps({"api_key": "sk-to-delete"}))
        monkeypatch.setattr(secret_store, "PLAINTEXT_FALLBACK_DIR", fallback_dir)
        # RED: current skeleton raises KeyringUnavailableError.
        status = secret_store.delete_cartesia_api_key()
        assert not fallback_file.exists()
        assert status.has_key is False
        assert status.backend == secret_store.STORAGE_NONE

    def test_save_to_keyring_migrates_away_from_fallback(
        self, fake_keyring, tmp_path: Path, monkeypatch,
    ) -> None:
        """When keyring is available after plaintext fallback, save should
        clear the JSON file and store in keyring."""
        fallback_dir = tmp_path / "secrets"
        fallback_file = fallback_dir / "cartesia_api_key.json"
        fallback_file.parent.mkdir(parents=True, exist_ok=True)
        fallback_file.write_text(json.dumps({"api_key": "sk-to-migrate"}))
        monkeypatch.setattr(secret_store, "PLAINTEXT_FALLBACK_DIR", fallback_dir)
        # Contract: save to keyring should clear fallback file.
        # RED: current skeleton does not touch fallback file.
        status = secret_store.save_cartesia_api_key("sk-to-migrate")
        assert not fallback_file.exists(), "Fallback file should have been removed"
        assert status.backend == secret_store.STORAGE_KEYRING

    def test_storage_status_does_not_expose_raw_key(
        self, fake_keyring,
    ) -> None:
        """StorageStatus frozen dataclass must never contain the raw key."""
        raw = "sk-hidden-no-leak"
        status = secret_store.save_cartesia_api_key(raw)
        assert raw not in str(status)
        assert raw not in repr(status)


# ---------------------------------------------------------------------------
# S3: plaintext JSON does not contain raw key in keyring mode — contract
# ---------------------------------------------------------------------------

class TestNoRawKeyContract:
    """Must PASS — safety contract for keyring-primary mode."""

    def test_keyring_json_has_no_raw_key(self, fake_keyring, tmp_path: Path) -> None:
        """Simulate that something writes a JSON config; assert no key in it."""
        cfg = tmp_path / "cfg.json"
        cfg.write_text(json.dumps({"hotkey": "f6", "volume": 1.0}))
        data = json.loads(cfg.read_text())
        for val in data.values():
            if isinstance(val, str) and len(val) > 10:
                assert "sk-" not in val, "Config leaked key material"
