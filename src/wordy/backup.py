"""完整本地备份：密码认证加密配置、轮盘音频与凭据。"""

from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from dataclasses import fields
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sys
import tempfile
import tomllib
from uuid import uuid4
from typing import BinaryIO, Iterator, cast
from zipfile import ZipFile, ZIP_DEFLATED

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.ciphers.base import AEADEncryptionContext

from wordy import config, secret
from wordy._toml_serializer import to_toml
from wordy.audio.clips import AUDIO_EXTENSIONS, _validated_clips_directory, resolve_audio_clip
from wordy.config import AppSettings
from wordy.hotkey.native import parse_hotkey

_USERNAMES = (secret.CARTESIA_USERNAME, secret.VOLCENGINE_USERNAME)
_MAX_TOTAL_BYTES = 2 * 1024**3
_MAX_CONFIG_BYTES = 4 * 1024**2
_MAGIC = b"WORDYBK\x02"
_HEADER_BYTES = len(_MAGIC) + 16 + 12
_TAG_BYTES = 16
_CHUNK_BYTES = 1024**2
# 固定参数属于文件版本，不能由不可信文件控制计算成本。
_KDF_ITERATIONS = 1_200_000
_MAX_ENCRYPTED_BYTES = _MAX_TOTAL_BYTES + 16 * 1024**2


def _password_bytes(password: str) -> bytes:
    if not isinstance(password, str) or not password.strip():
        raise ValueError("备份密码不能为空")
    return password.encode("utf-8")


def _derive_key(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", _password_bytes(password), salt, _KDF_ITERATIONS, dklen=32)


class _EncryptedWriter:
    """让 ZIP 直接流入密文，避免导出过程落下明文备份。"""

    def __init__(self, output: BinaryIO, password: str):
        salt, nonce = os.urandom(16), os.urandom(12)
        header = _MAGIC + salt + nonce
        self._output = output
        self._position = 0
        self._encryptor = cast(AEADEncryptionContext, Cipher(
            algorithms.AES(_derive_key(password, salt)), modes.GCM(nonce)).encryptor())
        self._encryptor.authenticate_additional_data(header)
        output.write(header)

    def write(self, data: bytes) -> int:
        self._position += len(data)
        if self._position > _MAX_ENCRYPTED_BYTES - _HEADER_BYTES - _TAG_BYTES:
            raise ValueError("备份过大")
        self._output.write(self._encryptor.update(data))
        return len(data)

    def tell(self) -> int:
        return self._position

    def flush(self) -> None:
        self._output.flush()

    def finish(self) -> None:
        self._output.write(self._encryptor.finalize())
        self._output.write(self._encryptor.tag)
        self.flush()


@contextmanager
def _decrypted_backup(source: str | Path, password: str, directory: Path | None = None) -> Iterator[BinaryIO]:
    """认证完成前不向 ZIP 或配置解析器交出任何解密内容。"""
    _password_bytes(password)
    with Path(source).open("rb") as input_file:
        size = input_file.seek(0, os.SEEK_END)
        if not _HEADER_BYTES + _TAG_BYTES < size <= _MAX_ENCRYPTED_BYTES:
            raise ValueError("备份文件大小无效")
        input_file.seek(0)
        header = input_file.read(_HEADER_BYTES)
        if not header.startswith(_MAGIC):
            raise ValueError("不是受支持的加密备份，旧明文 ZIP 无法导入")
        salt, nonce = header[len(_MAGIC):len(_MAGIC) + 16], header[-12:]
        input_file.seek(-_TAG_BYTES, os.SEEK_END)
        tag = input_file.read(_TAG_BYTES)
        decryptor = Cipher(algorithms.AES(_derive_key(password, salt)), modes.GCM(nonce, tag)).decryptor()
        decryptor.authenticate_additional_data(header)
        input_file.seek(_HEADER_BYTES)
        remaining = size - _HEADER_BYTES - _TAG_BYTES
        # 匿名临时文件只用于流式解密，认证失败或导入结束都立即关闭清理。
        with tempfile.TemporaryFile(dir=directory) as plaintext:
            try:
                while remaining:
                    block = input_file.read(min(_CHUNK_BYTES, remaining))
                    if not block:
                        raise ValueError("备份文件不完整")
                    remaining -= len(block)
                    plaintext.write(decryptor.update(block))
                plaintext.write(decryptor.finalize())
            except InvalidTag as error:
                raise ValueError("密码错误或备份已损坏") from error
            plaintext.seek(0)
            yield cast(BinaryIO, plaintext)


def default_backup_directory() -> Path:
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def _values(settings: AppSettings) -> dict[str, object]:
    return {field.name: deepcopy(getattr(settings, field.name))
            for field in fields(settings) if not field.name.startswith("_")}


def _secrets() -> dict[str, str | None]:
    return {username: secret.load_api_key(username) for username in _USERNAMES}


def _set_secret(username: str, value: str | None) -> None:
    if value is None:
        secret.delete_api_key(username)
    else:
        secret.save_api_key(username, value)


def _audio_names(settings: AppSettings) -> set[str]:
    return {name for index in range(settings.wheel_count)
            for name in settings.get_wheel(index)["slots"] if name}


def export_backup(settings: AppSettings, destination: str | Path, password: str) -> Path:
    """完整导出；失败时保留目标位置原有备份。"""
    _password_bytes(password)
    destination = Path(destination)
    config_file = settings._config_file or config.USER_CONFIG_FILE
    # 用户可在保存对话框手输任意扩展名，不能让备份覆盖运行数据。
    resolved = destination.resolve()
    if resolved == config_file.resolve() or resolved.is_relative_to((config_file.parent / "audio_clips").resolve()):
        raise ValueError("备份不能覆盖配置文件或保存到音频托管目录")
    settings.flush()
    credentials = _secrets()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=destination.parent, prefix=".wordy-backup-", suffix=".zip", delete=False) as handle:
        temporary = Path(handle.name)
    try:
        with settings._save_lock, temporary.open("wb") as output:
            writer = _EncryptedWriter(output, password)
            # 配置与音频清单必须取自同一快照，后台操作可能随后改动 live 实例。
            snapshot = AppSettings()
            for name, value in _values(settings).items():
                object.__setattr__(snapshot, name, value)
            with ZipFile(cast(BinaryIO, writer), "w", compression=ZIP_DEFLATED) as archive:
                archive.writestr("manifest.json", json.dumps({"format": "wordy-backup", "version": 1}))
                archive.writestr("config.toml", to_toml(_values(snapshot)))
                archive.writestr("secrets.json", json.dumps(credentials))
                for name in sorted(_audio_names(snapshot)):
                    archive.write(resolve_audio_clip(name, settings._config_file), f"audio_clips/{name}")
                # ZIP 写入器保有完整目录，可在加密流关闭前检查导入边界。
                _validate_archive(archive)
            writer.finish()
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)
    return destination


def _validate_archive(archive: ZipFile) -> None:
    entries = archive.infolist()
    names = [entry.filename for entry in entries]
    if len(entries) > 10000 or len(names) != len(set(names)) or sum(entry.file_size for entry in entries) > _MAX_TOTAL_BYTES:
        raise ValueError("备份过大或包含重复文件")
    for entry in entries:
        name = entry.filename
        if name in ("manifest.json", "config.toml", "secrets.json"):
            if entry.file_size > _MAX_CONFIG_BYTES:
                raise ValueError("备份配置过大")
            continue
        if not name.startswith("audio_clips/"):
            raise ValueError("备份包含未知文件")
        filename = name[len("audio_clips/"):]
        reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
        if (not filename or any(char in filename for char in '/\\:<>"|?*')
                or any(ord(char) < 32 for char in filename) or filename.endswith((".", " "))
                or filename.split(".")[0].upper() in reserved
                or Path(filename).suffix.lower() not in AUDIO_EXTENSIONS):
            raise ValueError("备份包含无效音频路径")
    if len({name.casefold() for name in names}) != len(names):
        raise ValueError("备份文件名大小写冲突")
    if not {"manifest.json", "config.toml", "secrets.json"}.issubset(names):
        raise ValueError("备份缺少配置或密钥")
    if archive.mode != "w" and json.loads(archive.read("manifest.json")) != {"format": "wordy-backup", "version": 1}:
        raise ValueError("不支持此备份版本")


def _validate_value(value: object, template: object) -> None:
    """导入边界严格检查结构，避免宽容加载器把损坏配置静默变成默认值。"""
    if template is None:
        valid = value is None or isinstance(value, str)
    elif isinstance(template, bool):
        valid = isinstance(value, bool)
    elif isinstance(template, (float, int)):
        valid = type(value) in (int, float) and math.isfinite(value)  # type: ignore[arg-type]
    elif isinstance(template, str):
        valid = isinstance(value, str)
    elif isinstance(template, list):
        valid = isinstance(value, list)
        if valid and isinstance(value, list):
            for item in value:
                _validate_value(item, template[0] if template else {"id": "", "name": ""})
    elif isinstance(template, dict):
        valid = isinstance(value, dict) and set(value).issubset(template)
        if valid and isinstance(value, dict):
            for key, item in value.items():
                _validate_value(item, template[key])
    else:
        valid = False
    if not valid:
        raise ValueError("备份配置字段类型无效")


def _validate_config(raw: dict[str, object]) -> None:
    template = _values(AppSettings())
    if not {name for name, value in template.items() if value is not None}.issubset(raw):
        raise ValueError("备份配置不完整")
    template["additional_chat_wheels"] = [config.new_chat_wheel()]
    template["window_position"] = {"x": 0, "y": 0}
    template["audio_output_device"] = {"name": "", "host_api_name": None}
    _validate_value(raw, template)


def import_backup(settings: AppSettings, source: str | Path, password: str) -> Path:
    """校验后恢复全部配置；调用方成功后退出，重启重建硬件与热键。"""
    _password_bytes(password)
    target = settings._config_file or config.USER_CONFIG_FILE
    target.parent.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    with (_decrypted_backup(source, password, target.parent) as plaintext,
          ZipFile(plaintext) as archive,
          tempfile.TemporaryDirectory(prefix="wordy-import-", dir=target.parent) as temporary):
        _validate_archive(archive)
        credentials = json.loads(archive.read("secrets.json"))
        if not isinstance(credentials, dict) or set(credentials) != set(_USERNAMES) or any(
                value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 65536)
                for value in credentials.values()):
            raise ValueError("备份密钥格式无效")
        staged_config = Path(temporary) / "config.toml"
        config_bytes = archive.read("config.toml")
        _validate_config(tomllib.loads(config_bytes.decode("utf-8")))
        staged_config.write_bytes(config_bytes)
        # 复用现有配置清洗，临时加载不能替换应用使用的缓存实例。
        cached = config._cached_settings, config._cached_config_file
        try:
            restored = AppSettings.load(config_file=staged_config)
        finally:
            config._cached_settings, config._cached_config_file = cached
        restored._loaded = False
        bindings = [parse_hotkey(restored.hotkey), parse_hotkey(restored.rolling_buffer_hotkey)]
        bindings += [parse_hotkey(restored.get_wheel(index)["hotkey"]) for index in range(restored.wheel_count)]
        if len(bindings) != len(set(bindings)):
            raise ValueError("备份中的快捷键互相冲突")
        names = _audio_names(restored)
        if {f"audio_clips/{name}" for name in names} != {name for name in archive.namelist() if name.startswith("audio_clips/")}:
            raise ValueError("备份音频与配置不匹配")
        directory = _validated_clips_directory(target)
        replacements = {name: f"{uuid4().hex}{Path(name).suffix.lower()}" for name in names}
        previous_secrets = _secrets()
        changed_secrets: list[str] = []
        try:
            directory.mkdir(parents=True, exist_ok=True)
            for name, replacement in replacements.items():
                destination = directory / replacement
                created.append(destination)
                with archive.open(f"audio_clips/{name}") as input_file, destination.open("xb") as output_file:
                    shutil.copyfileobj(input_file, output_file)
            restored.chat_wheel_slots = [replacements.get(name, name) for name in restored.chat_wheel_slots]
            for wheel in restored.additional_chat_wheels:
                wheel["slots"] = [replacements.get(name, name) for name in wheel["slots"]]
            for username in _USERNAMES:
                if credentials[username] != previous_secrets[username]:
                    _set_secret(username, credentials[username])
                    changed_secrets.append(username)
            for provider, username in zip(("Cartesia", "Volcengine"), _USERNAMES):
                restored.tts_providers[provider]["api_key_set"] = credentials[username] is not None
                restored.tts_providers[provider]["api_key_storage"] = "keyring" if credentials[username] is not None else "none"
            # 与计时保存共用锁；同步同一实例，退出 flush 不会把旧配置写回来。
            with settings._save_lock:
                if settings._save_timer is not None:
                    settings._save_timer.cancel()
                    settings._save_timer = None
                previous_values = _values(settings)
                try:
                    for name, value in _values(restored).items():
                        object.__setattr__(settings, name, value)
                    settings._save_to_disk_inner(target)
                except Exception:
                    for name, value in previous_values.items():
                        object.__setattr__(settings, name, value)
                    raise
        except Exception as error:
            failures = []
            for username in reversed(changed_secrets):
                try:
                    _set_secret(username, previous_secrets[username])
                except Exception:
                    failures.append(username)
            for path in created:
                path.unlink(missing_ok=True)
            if failures:
                raise RuntimeError("导入失败，部分密钥未能回滚；请重新检查服务商密钥") from error
            raise
    return target
