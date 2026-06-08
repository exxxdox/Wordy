"""Secret storage for Cartesia API key.

Keyring-primary storage with an explicit, opt-in plaintext JSON fallback
used only when the OS keyring backend is unavailable.

Public API:
    Constants:
        SERVICE_NAME, CARTESIA_USERNAME,
        STORAGE_KEYRING, STORAGE_PLAINTEXT, STORAGE_NONE,
        PLAINTEXT_FALLBACK_DIR, PLAINTEXT_FALLBACK_FILENAME
    Exceptions:
        SecretStoreError, KeyringUnavailableError
    Types:
        StorageStatus (frozen dataclass)
    Functions:
        normalize_api_key_input(raw)
        load_cartesia_api_key()
        save_cartesia_api_key(value, allow_plaintext_fallback=False)
        delete_cartesia_api_key()
        get_storage_status()

Safety invariants:
    - Never log or print secret values.
    - Exception messages are static; they never embed key material.
    - StorageStatus never carries the key itself.
    - Plaintext fallback file is created only when the caller explicitly
      opts in AND the keyring is unavailable.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

import keyring

SERVICE_NAME = "Wav_Trans"
CARTESIA_USERNAME = "cartesia_api_key"

STORAGE_KEYRING = "keyring"
STORAGE_PLAINTEXT = "plaintext_json"
STORAGE_NONE = "none"

_ENV_PREFIX = "CARTESIA_API_KEY="

PLAINTEXT_FALLBACK_FILENAME = "cartesia_api_key.json"


def _default_fallback_dir() -> Path:
    """Best-effort per-user directory for the plaintext fallback file.

    Tests always monkeypatch ``PLAINTEXT_FALLBACK_DIR``, so this value is
    only used in production when the user explicitly opts into the
    plaintext fallback.
    """
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA")
        if base:
            return Path(base) / SERVICE_NAME
        return Path.home() / "AppData" / "Roaming" / SERVICE_NAME
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / SERVICE_NAME
    return Path.home() / ".config" / SERVICE_NAME


PLAINTEXT_FALLBACK_DIR: Path = _default_fallback_dir()


class SecretStoreError(RuntimeError):
    """Base error for secret store failures."""


class KeyringUnavailableError(SecretStoreError):
    """Raised when the OS keyring backend is unavailable."""


@dataclass(frozen=True)
class StorageStatus:
    backend: str
    has_key: bool
    keyring_available: bool
    fallback_active: bool


def _keyring_available() -> bool:
    """Probe keyring backend without raising."""
    try:
        keyring.get_keyring()
        return True
    except Exception:
        return False


def _fallback_file_path() -> Path:
    """Resolve the fallback file path from the current module constant."""
    return Path(PLAINTEXT_FALLBACK_DIR) / PLAINTEXT_FALLBACK_FILENAME


def _read_fallback_key() -> str | None:
    """Return the key stored in the plaintext fallback file, or None."""
    path = _fallback_file_path()
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # Corrupt or unreadable fallback: treat as absent. Avoid raising
        # so callers can recover by re-saving or deleting.
        return None
    key = data.get("api_key") if isinstance(data, dict) else None
    if isinstance(key, str) and key:
        return key
    return None


def _write_fallback_key(value: str) -> None:
    """Write the key to the plaintext fallback file with 0o600 on POSIX."""
    path = _fallback_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {
            "api_key": value,
            "backend": STORAGE_PLAINTEXT,
            "status": "plaintext_fallback_active",
        },
        ensure_ascii=False,
        indent=2,
    )
    path.write_text(payload, encoding="utf-8")
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            # Best-effort: filesystem may not support chmod (e.g. some
            # mounted volumes). Permission hardening failure must not
            # break the save path itself.
            pass


def _delete_fallback_file() -> bool:
    """Remove the fallback file if present. Return True if it existed."""
    path = _fallback_file_path()
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def normalize_api_key_input(raw: str) -> str:
    """Strip whitespace and an optional leading 'CARTESIA_API_KEY=' prefix."""
    if raw is None:
        return ""
    value = raw.strip()
    if value.startswith(_ENV_PREFIX):
        value = value[len(_ENV_PREFIX):].strip()
    return value


def load_cartesia_api_key() -> str | None:
    """Return the stored API key.

    Order:
      1. If keyring is available, return its value (which may be ``None``).
      2. If keyring is unavailable but a plaintext fallback file exists,
         return the key it contains.
      3. Otherwise raise ``KeyringUnavailableError``.
    """
    if _keyring_available():
        try:
            return keyring.get_password(SERVICE_NAME, CARTESIA_USERNAME)
        except Exception as exc:
            fallback_key = _read_fallback_key()
            if fallback_key is not None:
                return fallback_key
            raise SecretStoreError(
                "Failed to read secret from keyring"
            ) from exc

    fallback_key = _read_fallback_key()
    if fallback_key is not None:
        return fallback_key
    raise KeyringUnavailableError("OS keyring backend is unavailable")


def save_cartesia_api_key(
    value: str,
    allow_plaintext_fallback: bool = False,
) -> StorageStatus:
    """Save the API key.

    Primary path stores to the OS keyring. If the keyring is unavailable
    and the caller explicitly opts in via ``allow_plaintext_fallback``,
    the key is written to a plaintext JSON file instead.

    On successful keyring save, any pre-existing plaintext fallback file
    is removed so the system migrates back to the secure backend.
    """
    normalized = normalize_api_key_input(value)
    if not normalized:
        raise SecretStoreError("Refusing to store an empty API key")

    keyring_available = _keyring_available()
    if keyring_available:
        try:
            keyring.set_password(SERVICE_NAME, CARTESIA_USERNAME, normalized)
        except Exception as exc:
            if not allow_plaintext_fallback:
                raise SecretStoreError(
                    "Failed to write secret to keyring"
                ) from exc
        else:
            _delete_fallback_file()
            return StorageStatus(
                backend=STORAGE_KEYRING,
                has_key=True,
                keyring_available=True,
                fallback_active=False,
            )

    if not allow_plaintext_fallback:
        raise KeyringUnavailableError("OS keyring backend is unavailable")

    try:
        _write_fallback_key(normalized)
    except OSError as exc:
        raise SecretStoreError(
            "Failed to write plaintext fallback file"
        ) from exc
    return StorageStatus(
        backend=STORAGE_PLAINTEXT,
        has_key=True,
        keyring_available=False,
        fallback_active=True,
    )


def delete_cartesia_api_key() -> StorageStatus:
    """Delete the API key from keyring and the plaintext fallback.

    If the keyring is unavailable and no fallback file exists, this
    raises ``KeyringUnavailableError`` to mirror load/save semantics.
    """
    keyring_ok = _keyring_available()
    fallback_existed = _delete_fallback_file()

    if not keyring_ok:
        if not fallback_existed:
            raise KeyringUnavailableError("OS keyring backend is unavailable")
        return StorageStatus(
            backend=STORAGE_NONE,
            has_key=False,
            keyring_available=False,
            fallback_active=False,
        )

    try:
        keyring.delete_password(SERVICE_NAME, CARTESIA_USERNAME)
    except keyring.errors.PasswordDeleteError:
        # Already absent: not an error for delete semantics.
        pass
    except Exception as exc:
        raise SecretStoreError(
            "Failed to delete secret from keyring"
        ) from exc

    return StorageStatus(
        backend=STORAGE_NONE,
        has_key=False,
        keyring_available=True,
        fallback_active=False,
    )


def get_storage_status() -> StorageStatus:
    """Report the current storage backend status without exposing the key."""
    if not _keyring_available():
        fallback_key = _read_fallback_key()
        if fallback_key is not None:
            return StorageStatus(
                backend=STORAGE_PLAINTEXT,
                has_key=True,
                keyring_available=False,
                fallback_active=True,
            )
        return StorageStatus(
            backend=STORAGE_NONE,
            has_key=False,
            keyring_available=False,
            fallback_active=False,
        )

    try:
        present = keyring.get_password(SERVICE_NAME, CARTESIA_USERNAME) is not None
    except Exception:
        present = False
    return StorageStatus(
        backend=STORAGE_KEYRING if present else STORAGE_NONE,
        has_key=present,
        keyring_available=True,
        fallback_active=False,
    )
