"""Secret storage for TTS API keys via OS keyring (Cartesia, Volcengine)."""

from __future__ import annotations

from dataclasses import dataclass

import keyring

SERVICE_NAME = "Wordy"
CARTESIA_USERNAME = "cartesia_api_key"
VOLCENGINE_USERNAME = "volcengine_access_key"

STORAGE_KEYRING = "keyring"
STORAGE_NONE = "none"

_ENV_PREFIX = "CARTESIA_API_KEY="


class SecretStoreError(RuntimeError):
    """Base error for secret store failures."""


class KeyringUnavailableError(SecretStoreError):
    """Raised when the OS keyring backend is unavailable."""


@dataclass(frozen=True)
class StorageStatus:
    backend: str
    has_key: bool
    keyring_available: bool


def _keyring_available() -> bool:
    try:
        keyring.get_keyring()
        return True
    except Exception:
        return False


def load_api_key(username: str) -> str | None:
    """从 keyring 加载密钥，不可用时返回 None。"""
    if _keyring_available():
        try:
            return keyring.get_password(SERVICE_NAME, username)
        except Exception:
            raise SecretStoreError("Failed to read secret from keyring")
    raise KeyringUnavailableError("OS keyring backend is unavailable")


def save_api_key(username: str, value: str) -> StorageStatus:
    """保存密钥到 keyring。"""
    normalized = value.strip()
    if not normalized:
        raise SecretStoreError("Refusing to store an empty API key")
    if not _keyring_available():
        raise KeyringUnavailableError("OS keyring backend is unavailable")
    try:
        keyring.set_password(SERVICE_NAME, username, normalized)
    except Exception as exc:
        raise SecretStoreError("Failed to write secret to keyring") from exc
    return StorageStatus(backend=STORAGE_KEYRING, has_key=True, keyring_available=True)


def delete_api_key(username: str) -> StorageStatus:
    """从 keyring 删除密钥。"""
    if _keyring_available():
        try:
            keyring.delete_password(SERVICE_NAME, username)
        except Exception:
            pass
    return StorageStatus(backend=STORAGE_NONE, has_key=False, keyring_available=_keyring_available())


def get_api_key_status(username: str) -> dict[str, bool | str]:
    """查询 keyring 中是否存在密钥。"""
    if not _keyring_available():
        return {"key_set": False, "storage": STORAGE_NONE}
    try:
        present = keyring.get_password(SERVICE_NAME, username) is not None
    except Exception:
        present = False
    return {"key_set": present, "storage": STORAGE_KEYRING if present else STORAGE_NONE}


def normalize_api_key_input(raw: str | None) -> str:
    """去掉 CARTESIA_API_KEY= 前缀 + 首尾空白。"""
    if raw is None:
        return ""
    value = raw.strip()
    if value.startswith(_ENV_PREFIX):
        value = value[len(_ENV_PREFIX):].strip()
    return value


# ── provider-specific wrappers ────────────────────────────────────────────


def load_cartesia_api_key() -> str | None:
    return load_api_key(CARTESIA_USERNAME)


def save_cartesia_api_key(value: str) -> StorageStatus:
    return save_api_key(CARTESIA_USERNAME, value)


def delete_cartesia_api_key() -> StorageStatus:
    return delete_api_key(CARTESIA_USERNAME)


def get_cartesia_api_key_status() -> dict[str, bool | str]:
    s = get_api_key_status(CARTESIA_USERNAME)
    return {"cartesia_api_key_set": s["key_set"], "cartesia_api_key_storage": s["storage"]}


def load_volcengine_access_key() -> str | None:
    return load_api_key(VOLCENGINE_USERNAME)


def save_volcengine_access_key(value: str) -> StorageStatus:
    return save_api_key(VOLCENGINE_USERNAME, value)


def delete_volcengine_access_key() -> StorageStatus:
    return delete_api_key(VOLCENGINE_USERNAME)


def get_volcengine_access_key_status() -> dict[str, bool | str]:
    s = get_api_key_status(VOLCENGINE_USERNAME)
    return {"volcengine_access_key_set": s["key_set"], "volcengine_access_key_storage": s["storage"]}
