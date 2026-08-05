"""Secret storage for TTS API keys (Cartesia, Volcengine, etc.).

Keyring-primary storage with an explicit, opt-in plaintext JSON fallback
used only when the OS keyring backend is unavailable.

Public API (generic):
    load_api_key(username) → str | None
    save_api_key(username, value, allow_plaintext_fallback=False) → StorageStatus
    delete_api_key(username) → StorageStatus
    get_api_key_status(username) → dict[str, bool | str]
    normalize_api_key_input(raw) → str  (strips CARTESIA_API_KEY= prefix)

Public API (backward-compat wrappers):
    load_cartesia_api_key() / save_cartesia_api_key() / delete_cartesia_api_key() / get_storage_status()
    load_volcengine_access_key() / save_volcengine_access_key() / delete_volcengine_access_key() / get_volcengine_storage_status()

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
VOLCENGINE_USERNAME = "volcengine_access_key"

STORAGE_KEYRING = "keyring"
STORAGE_PLAINTEXT = "plaintext_json"
STORAGE_NONE = "none"

_ENV_PREFIX = "CARTESIA_API_KEY="

PLAINTEXT_FALLBACK_FILENAME = "cartesia_api_key.json"  # kept for test compat


def _default_fallback_dir() -> Path:
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
    try:
        keyring.get_keyring()
        return True
    except Exception:
        return False


# ── 通用接口 ──────────────────────────────────────────────────────────


def _fallback_path(username: str) -> Path:
    return Path(PLAINTEXT_FALLBACK_DIR) / f"{username}.json"


def _read_fallback(username: str) -> str | None:
    path = _fallback_path(username)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    key = data.get("api_key") if isinstance(data, dict) else None
    return key if isinstance(key, str) and key else None


def _write_fallback(username: str, value: str) -> None:
    path = _fallback_path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"api_key": value, "backend": STORAGE_PLAINTEXT, "status": "plaintext_fallback_active"},
        ensure_ascii=False, indent=2,
    )
    path.write_text(payload, encoding="utf-8")
    if os.name == "posix":
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _delete_fallback(username: str) -> bool:
    path = _fallback_path(username)
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


def load_api_key(username: str) -> str | None:
    """加载密钥：keyring → fallback file → None/raise。"""
    if _keyring_available():
        try:
            return keyring.get_password(SERVICE_NAME, username)
        except Exception:
            fallback = _read_fallback(username)
            if fallback is not None:
                return fallback
            # keyring 可用但读取失败且无 fallback → 报错，不静默返回 None
            raise SecretStoreError("Failed to read secret from keyring")
    fallback = _read_fallback(username)
    if fallback is not None:
        return fallback
    raise KeyringUnavailableError("OS keyring backend is unavailable")


def save_api_key(username: str, value: str, allow_plaintext_fallback: bool = False) -> StorageStatus:
    normalized = value.strip()
    if not normalized:
        raise SecretStoreError("Refusing to store an empty API key")
    if _keyring_available():
        try:
            keyring.set_password(SERVICE_NAME, username, normalized)
        except Exception as exc:
            if not allow_plaintext_fallback:
                raise SecretStoreError("Failed to write secret to keyring") from exc
        else:
            _delete_fallback(username)
            return StorageStatus(backend=STORAGE_KEYRING, has_key=True, keyring_available=True, fallback_active=False)
    if not allow_plaintext_fallback:
        raise KeyringUnavailableError("OS keyring backend is unavailable")
    try:
        _write_fallback(username, normalized)
    except OSError as exc:
        raise SecretStoreError("Failed to write plaintext fallback file") from exc
    return StorageStatus(backend=STORAGE_PLAINTEXT, has_key=True, keyring_available=False, fallback_active=True)


def delete_api_key(username: str) -> StorageStatus:
    fallback_existed = _delete_fallback(username)
    if _keyring_available():
        try:
            keyring.delete_password(SERVICE_NAME, username)
        except Exception:
            pass
        return StorageStatus(backend=STORAGE_NONE, has_key=False, keyring_available=True, fallback_active=False)
    if not fallback_existed:
        raise KeyringUnavailableError("OS keyring backend is unavailable")
    return StorageStatus(backend=STORAGE_NONE, has_key=False, keyring_available=False, fallback_active=False)


def get_api_key_status(username: str) -> dict[str, bool | str]:
    if not _keyring_available():
        fb = _read_fallback(username)
        return {"key_set": fb is not None, "storage": STORAGE_PLAINTEXT if fb else STORAGE_NONE}
    try:
        present = keyring.get_password(SERVICE_NAME, username) is not None
    except Exception:
        present = False
    return {"key_set": present, "storage": STORAGE_KEYRING if present else STORAGE_NONE}


# ── 向后兼容 wrapper ──────────────────────────────────────────────────


def normalize_api_key_input(raw: str | None) -> str:
    if raw is None:
        return ""
    value = raw.strip()
    if value.startswith(_ENV_PREFIX):
        value = value[len(_ENV_PREFIX):].strip()
    return value


def load_cartesia_api_key() -> str | None:
    return load_api_key(CARTESIA_USERNAME)


def save_cartesia_api_key(value: str, allow_plaintext_fallback: bool = False) -> StorageStatus:
    return save_api_key(CARTESIA_USERNAME, value, allow_plaintext_fallback)


def delete_cartesia_api_key() -> StorageStatus:
    return delete_api_key(CARTESIA_USERNAME)


def get_storage_status() -> StorageStatus:
    s = get_api_key_status(CARTESIA_USERNAME)
    return StorageStatus(
        backend=str(s["storage"]), has_key=bool(s["key_set"]),
        keyring_available=_keyring_available(), fallback_active=s["storage"] == STORAGE_PLAINTEXT,
    )


def get_cartesia_api_key_status() -> dict[str, bool | str]:
    s = get_api_key_status(CARTESIA_USERNAME)
    return {"cartesia_api_key_set": s["key_set"], "cartesia_api_key_storage": s["storage"]}


def load_volcengine_access_key() -> str | None:
    return load_api_key(VOLCENGINE_USERNAME)


def save_volcengine_access_key(value: str, allow_plaintext_fallback: bool = False) -> StorageStatus:
    return save_api_key(VOLCENGINE_USERNAME, value, allow_plaintext_fallback)


def delete_volcengine_access_key() -> StorageStatus:
    return delete_api_key(VOLCENGINE_USERNAME)


def get_volcengine_storage_status() -> StorageStatus:
    s = get_api_key_status(VOLCENGINE_USERNAME)
    return StorageStatus(
        backend=str(s["storage"]), has_key=bool(s["key_set"]),
        keyring_available=_keyring_available(), fallback_active=s["storage"] == STORAGE_PLAINTEXT,
    )


def get_volcengine_access_key_status() -> dict[str, bool | str]:
    s = get_api_key_status(VOLCENGINE_USERNAME)
    return {"volcengine_access_key_set": s["key_set"], "volcengine_access_key_storage": s["storage"]}
