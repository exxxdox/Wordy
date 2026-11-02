from pathlib import Path
from typing import BinaryIO, cast
from zipfile import ZipFile

import pytest

from wordy import backup, config, secret

PASSWORD = "test-password-测试"


def _contents(path):
    with backup._decrypted_backup(path, PASSWORD, path.parent) as plaintext, ZipFile(plaintext) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def _encrypted_zip(path, contents):
    with path.open("wb") as output:
        writer = backup._EncryptedWriter(output, PASSWORD)
        with ZipFile(cast(BinaryIO, writer), "w") as archive:
            for name, value in contents.items():
                archive.writestr(name, value)
        writer.finish()


def test_backup_roundtrip_includes_audio_and_secrets(tmp_path, fake_keyring):
    settings = config.AppSettings.load(config_file=tmp_path / "source" / "config.toml")
    assert settings._config_file is not None
    settings.update(volume=1.4, overlay_placeholder="备份测试",
                    audio_output_device={"name": "扬声器", "host_api_name": None},
                    window_position={"x": -120, "y": 240})
    settings.volcengine_voice_list = [{"id": "speaker", "name": "自定义音色"}]
    clips = settings._config_file.parent / "audio_clips"
    clips.mkdir()
    (clips / "sample.wav").write_bytes(b"audio payload")
    settings.update_wheel(0, slots=["sample.wav"] + [""] * 7, labels=["声音"] + [""] * 7)
    secret.save_api_key(secret.CARTESIA_USERNAME, "fake-key")
    archive = tmp_path / "backup.zip"
    backup.export_backup(settings, archive, PASSWORD)
    target = config.AppSettings.load(config_file=tmp_path / "target" / "config.toml")
    assert target._config_file is not None
    secret.save_api_key(secret.CARTESIA_USERNAME, "different-key")
    backup.import_backup(target, archive, PASSWORD)
    assert target.volume == 1.4
    assert target.overlay_placeholder == "备份测试"
    assert target.audio_output_device == {"name": "扬声器", "host_api_name": None}
    assert target.window_position == {"x": -120, "y": 240}
    assert target.volcengine_voice_list == [{"id": "speaker", "name": "自定义音色"}]
    filename = target.get_wheel(0)["slots"][0]
    assert (target._config_file.parent / "audio_clips" / filename).read_bytes() == b"audio payload"
    assert secret.load_api_key(secret.CARTESIA_USERNAME) == "fake-key"
    assert b"fake-key" not in target._config_file.read_bytes()
    target.flush()
    assert b"\xe5\xa4\x87\xe4\xbb\xbd\xe6\xb5\x8b\xe8\xaf\x95" in target._config_file.read_bytes()


def test_export_missing_audio_preserves_existing_backup(tmp_path, fake_keyring):
    settings = config.AppSettings.load(config_file=tmp_path / "config.toml")
    settings.update_wheel(0, slots=["missing.wav"] + [""] * 7)
    archive = tmp_path / "backup.zip"
    archive.write_bytes(b"previous backup")
    with pytest.raises(FileNotFoundError):
        backup.export_backup(settings, archive, PASSWORD)
    assert archive.read_bytes() == b"previous backup"


def test_import_rejects_path_traversal_without_changes(tmp_path, fake_keyring):
    settings = config.AppSettings.load(config_file=tmp_path / "config.toml")
    assert settings._config_file is not None
    settings.save()
    previous = settings._config_file.read_bytes()
    archive = tmp_path / "bad.zip"
    _encrypted_zip(archive, {"../outside.wav": b"bad"})
    with pytest.raises(ValueError):
        backup.import_backup(settings, archive, PASSWORD)
    assert settings._config_file.read_bytes() == previous


def test_import_save_failure_restores_secrets_and_audio(tmp_path, fake_keyring, monkeypatch):
    settings = config.AppSettings.load(config_file=tmp_path / "config.toml")
    assert settings._config_file is not None
    secret.save_api_key(secret.CARTESIA_USERNAME, "backup-key")
    archive = tmp_path / "backup.zip"
    backup.export_backup(settings, archive, PASSWORD)
    secret.save_api_key(secret.CARTESIA_USERNAME, "current-key")
    settings.update(volume=0.5)
    previous = settings._config_file.read_bytes()
    def fail(*args, **kwargs):
        raise OSError("disk full")
    monkeypatch.setattr(config.AppSettings, "_save_to_disk_inner", fail)
    with pytest.raises(OSError, match="disk full"):
        backup.import_backup(settings, archive, PASSWORD)
    assert settings.volume == 0.5
    assert settings._config_file.read_bytes() == previous
    assert secret.load_api_key(secret.CARTESIA_USERNAME) == "current-key"


def test_default_directory_uses_executable(tmp_path, monkeypatch):
    monkeypatch.setattr(backup.sys, "frozen", True, raising=False)
    monkeypatch.setattr(backup.sys, "executable", str(tmp_path / "Wordy.exe"))
    assert backup.default_backup_directory() == tmp_path


@pytest.mark.parametrize("replacement", [
    ('hotkey = "f6"', 'hotkey = "f8"'),
    ('voice_list = []', 'voice_list = [42]'),
    ('overlay_placeholder = "Hello World~"', 'overlay_placeholder = ["invalid"]'),
])
def test_import_rejects_invalid_config_before_writing(tmp_path, fake_keyring, replacement):
    settings = config.AppSettings.load(config_file=tmp_path / "config.toml")
    assert settings._config_file is not None
    archive = tmp_path / "backup.zip"
    backup.export_backup(settings, archive, PASSWORD)
    previous = settings._config_file.read_bytes()
    contents = _contents(archive)
    old, new = replacement
    assert old in contents["config.toml"].decode("utf-8")
    contents["config.toml"] = contents["config.toml"].decode("utf-8").replace(old, new).encode("utf-8")
    _encrypted_zip(archive, contents)
    with pytest.raises(ValueError):
        backup.import_backup(settings, archive, PASSWORD)
    assert settings._config_file.read_bytes() == previous


@pytest.mark.parametrize("name", ["audio_clips/CON.wav", "audio_clips/test.wav.", "audio_clips/x:y.wav"])
def test_archive_rejects_windows_alias_paths(tmp_path, name):
    archive = tmp_path / "bad.zip"
    with ZipFile(archive, "w") as output:
        output.writestr(name, b"invalid")
    with ZipFile(archive) as input_file, pytest.raises(ValueError):
        backup._validate_archive(input_file)


@pytest.mark.parametrize("location", ["config.toml", "audio_clips/recording.wav"])
def test_export_cannot_overwrite_live_data(tmp_path, fake_keyring, location):
    settings = config.AppSettings.load(config_file=tmp_path / "config.toml")
    settings.save()
    destination = tmp_path / location
    destination.parent.mkdir(parents=True, exist_ok=True)
    if location != "config.toml":
        destination.write_bytes(b"original audio")
    previous = destination.read_bytes()
    with pytest.raises(ValueError):
        backup.export_backup(settings, destination, PASSWORD)
    assert destination.read_bytes() == previous


@pytest.mark.parametrize("contents", ["", "volume = 0.5\n"])
def test_incomplete_config_is_rejected(contents):
    import tomllib
    with pytest.raises(ValueError, match="不完整"):
        backup._validate_config(tomllib.loads(contents))


def test_export_audio_list_uses_same_snapshot_as_config(tmp_path, fake_keyring, monkeypatch):
    settings = config.AppSettings.load(config_file=tmp_path / "config.toml")
    directory = tmp_path / "audio_clips"
    directory.mkdir()
    (directory / "before.wav").write_bytes(b"snapshot audio")
    settings.update_wheel(0, slots=["before.wav"] + [""] * 7)
    serialize = backup.to_toml
    def mutate_live_after_snapshot(values):
        object.__setattr__(settings, "chat_wheel_slots", [""] * 8)
        return serialize(values)
    monkeypatch.setattr(backup, "to_toml", mutate_live_after_snapshot)
    archive = tmp_path / "backup.zip"
    backup.export_backup(settings, archive, PASSWORD)
    result = _contents(archive)
    assert "before.wav" in result["config.toml"].decode("utf-8")
    assert result["audio_clips/before.wav"] == b"snapshot audio"


@pytest.mark.parametrize("password", ["", "   ", "\t\n"])
def test_empty_password_is_rejected_without_io(tmp_path, monkeypatch, password):
    settings = config.AppSettings()
    monkeypatch.setattr(backup, "_secrets", lambda: pytest.fail("must not access credentials"))
    path = tmp_path / "backup.wordybackup"
    for operation in (backup.export_backup, backup.import_backup):
        with pytest.raises(ValueError, match="密码"):
            operation(settings, path, password)
    assert not path.exists()


def test_backup_is_encrypted_and_randomized(tmp_path, fake_keyring):
    settings = config.AppSettings.load(config_file=tmp_path / "config.toml")
    secret.save_api_key(secret.CARTESIA_USERNAME, "secret-sentinel-not-plaintext")
    paths = [tmp_path / "first.wordybackup", tmp_path / "second.wordybackup"]
    for path in paths:
        backup.export_backup(settings, path, PASSWORD)
        data = path.read_bytes()
        assert b"secret-sentinel-not-plaintext" not in data
        assert b"config.toml" not in data
        with pytest.raises(Exception):
            ZipFile(path)
    assert paths[0].read_bytes() != paths[1].read_bytes()


@pytest.mark.parametrize("damage", ["password", "header", "salt", "nonce", "ciphertext", "tag", "truncated", "plaintext"])
def test_authentication_failure_changes_nothing(tmp_path, fake_keyring, monkeypatch, damage):
    settings = config.AppSettings.load(config_file=tmp_path / "config.toml")
    assert settings._config_file is not None
    path = tmp_path / "backup.wordybackup"
    backup.export_backup(settings, path, PASSWORD)
    before = settings._config_file.read_bytes()
    data = bytearray(path.read_bytes())
    password = PASSWORD
    if damage == "password":
        password += "wrong"
    elif damage == "truncated":
        del data[-20:]
    elif damage == "plaintext":
        data = bytearray(b"PK\x03\x04plain zip")
    else:
        offsets = {"header": 0, "salt": 8, "nonce": 24, "ciphertext": 40, "tag": -1}
        data[offsets[damage]] ^= 1
    path.write_bytes(data)
    monkeypatch.setattr(backup, "_secrets", lambda: pytest.fail("must authenticate before credentials"))
    with pytest.raises(ValueError):
        backup.import_backup(settings, path, password)
    assert settings._config_file.read_bytes() == before
    assert not list(tmp_path.glob("wordy-import-*"))


def test_password_whitespace_is_not_trimmed(tmp_path, fake_keyring):
    settings = config.AppSettings.load(config_file=tmp_path / "config.toml")
    path = tmp_path / "backup.wordybackup"
    backup.export_backup(settings, path, " password ")
    with pytest.raises(ValueError):
        backup.import_backup(settings, path, "password")
    backup.import_backup(settings, path, " password ")
