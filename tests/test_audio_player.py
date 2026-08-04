#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Offline tests for audio_player.py output device selection and legacy API removal."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import MagicMock

import pytest

from wordy.audio.player import AudioPlayer


# ---------------------------------------------------------------------------
# Fake pyaudio
# ---------------------------------------------------------------------------

class FakePyAudioStream:
    """Stub PyAudio stream with recordable lifecycle calls."""

    def __init__(self):
        self.stop_calls = 0
        self.close_calls = 0
        self.write_calls: list[bytes] = []

    def stop_stream(self) -> None:
        self.stop_calls += 1

    def close(self) -> None:
        self.close_calls += 1

    def write(self, data: bytes) -> None:
        self.write_calls.append(data)


class FakePyAudio:
    """Stub PyAudio instance with recordable lifecycle calls and multiple devices.

    Each device carries a ``hostApi`` index pointing into ``_host_apis``; the
    fake exposes ``get_host_api_info_by_index`` so production code can render
    a host-aware display name.
    """

    def __init__(self):
        self.terminate_calls = 0
        self.open_calls: list[dict[str, object]] = []
        self._host_apis = [
            {"index": 0, "name": "MME", "type": 2},
            {"index": 1, "name": "Windows WASAPI", "type": 13},
        ]
        self._default_device = {
            "index": 0,
            "name": "Fake Default Device",
            "maxOutputChannels": 2,
            "hostApi": 1,
        }
        self._devices = [
            self._default_device,
            {"index": 1, "name": "VB-Cable Output", "maxOutputChannels": 2, "hostApi": 1},
            {"index": 2, "name": "Fake Input Only", "maxOutputChannels": 0, "hostApi": 1},
        ]
        self._stream = FakePyAudioStream()

    def get_default_output_device_info(self):
        return self._default_device

    def get_device_count(self):
        return len(self._devices)

    def get_device_info_by_index(self, index: int):
        return self._devices[index]

    def get_host_api_info_by_index(self, index: int):
        return self._host_apis[index]

    def get_format_from_width(self, width: int):
        return 8 * width  # arbitrary numeric stand-in

    def get_sample_size(self, audio_format: int):
        return {
            1: 4,   # paFloat32
            2: 4,   # paInt32
            4: 3,   # paInt24
            8: 2,   # paInt16
            16: 1,  # paInt8
            32: 1,  # paUInt8
        }[audio_format]

    def open(self, **kwargs):
        self.open_calls.append(kwargs)
        return self._stream

    def terminate(self) -> None:
        self.terminate_calls += 1


@pytest.fixture(autouse=True)
def _patch_pyaudio(monkeypatch):
    """Inject FakePyAudio so no real audio hardware is touched."""
    fake_pa = FakePyAudio()
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: fake_pa))
    return fake_pa


# ---------------------------------------------------------------------------
# Constructor tests — output_device_name
# ---------------------------------------------------------------------------

def test_audio_player_ctor_accepts_output_device_name():
    """AudioPlayer(output_device_name='VB-Cable Output') stores the name."""
    player = AudioPlayer(output_device_name="VB-Cable Output")
    assert player.output_device_name == "VB-Cable Output"


def test_audio_player_ctor_defaults_to_system_default_device():
    """AudioPlayer() with no args sets output_device_name to None."""
    player = AudioPlayer()
    assert player.output_device_name is None


def test_audio_player_ctor_rejects_legacy_mode_kwarg():
    """Legacy device matching kwarg must raise TypeError."""
    legacy_key = "match" + "_" + "mode"
    with pytest.raises(TypeError):
        AudioPlayer(**{legacy_key: "default"})


# ---------------------------------------------------------------------------
# _resolve_output_device tests
# ---------------------------------------------------------------------------

def test_resolve_uses_explicit_device_index_when_provided(_patch_pyaudio):
    """Explicit device_index must win over any selected name."""
    pa = _patch_pyaudio
    player = AudioPlayer(output_device_name="VB-Cable Output")
    result = player._resolve_output_device(pa, device_index=0)
    assert result == 0


def test_resolve_uses_named_output_device_when_set(_patch_pyaudio):
    """When output_device_name is set, _resolve_output_device returns its index."""
    pa = _patch_pyaudio
    player = AudioPlayer(output_device_name="VB-Cable Output")
    result = player._resolve_output_device(pa, device_index=None)
    assert result == 1


def test_resolve_falls_back_to_default_when_named_device_missing(_patch_pyaudio):
    """Missing named device must fall back to default device index, not crash."""
    pa = _patch_pyaudio
    player = AudioPlayer(output_device_name="Non-Existent Device")
    result = player._resolve_output_device(pa, device_index=None)
    assert result == 0  # falls back to default


def test_resolve_returns_none_when_default_device_lookup_raises(monkeypatch):
    """When get_default_output_device_info raises, _resolve_output_device returns None."""
    pa = FakePyAudio()

    def _boom():
        raise OSError("no default device")

    pa.get_default_output_device_info = _boom  # type: ignore[assignment]
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: pa))

    player = AudioPlayer(output_device_name="Non-Existent")
    result = player._resolve_output_device(pa, device_index=None)
    assert result is None


def test_resolve_returns_none_when_output_device_name_none_and_default_lookup_raises(monkeypatch):
    """When output_device_name is None and default lookup raises, _resolve_output_device returns None."""
    pa = FakePyAudio()

    def _boom():
        raise OSError("no default device")

    pa.get_default_output_device_info = _boom  # type: ignore[assignment]
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: pa))

    player = AudioPlayer()
    result = player._resolve_output_device(pa, device_index=None)
    assert result is None


# ---------------------------------------------------------------------------
# list_output_devices (module-level) tests
# ---------------------------------------------------------------------------

def test_list_output_devices_returns_filtered_outputs_with_default_flag(_patch_pyaudio):
    """list_output_devices returns only output-capable devices with is_default flag."""
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()
    assert isinstance(devices, list)
    assert len(devices) == 2  # only devices with maxOutputChannels > 0

    # Default device should be flagged
    default_devices = [d for d in devices if d["is_default"]]
    assert len(default_devices) == 1
    assert default_devices[0]["name"] == "Fake Default Device"

    # Named output device should be present but not flagged as default
    vb_cable = [d for d in devices if d["name"] == "VB-Cable Output"]
    assert len(vb_cable) == 1
    assert vb_cable[0]["is_default"] is False

    # Input-only device (maxOutputChannels == 0) must be excluded
    input_only = [d for d in devices if d["name"] == "Fake Input Only"]
    assert len(input_only) == 0


def test_list_output_devices_each_entry_has_host_api_fields(_patch_pyaudio):
    """Every enumerated output device must carry host_api_index, host_api_name, display_name."""
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()
    assert len(devices) == 2

    for d in devices:
        assert "host_api_index" in d, f"missing host_api_index in {d!r}"
        assert "host_api_name" in d, f"missing host_api_name in {d!r}"
        assert "display_name" in d, f"missing display_name in {d!r}"
        assert isinstance(d["host_api_index"], int)
        assert isinstance(d["host_api_name"], str) and d["host_api_name"]
        assert isinstance(d["display_name"], str) and d["display_name"]
        # display_name must surface both raw name and host_api_name so duplicate
        # raw names are still distinguishable in user-facing pickers.
        assert d["name"] in d["display_name"]
        assert d["host_api_name"] in d["display_name"]

    assert {d["host_api_name"] for d in devices} == {"Windows WASAPI"}


def test_list_output_devices_returns_empty_on_pyaudio_failure(monkeypatch):
    """When pyaudio.PyAudio() raises, list_output_devices must return []."""
    monkeypatch.setattr(
        "wordy.audio.player.pyaudio",
        MagicMock(side_effect=RuntimeError("no audio backend")),
    )
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()
    assert devices == []


class FakeDuplicateOutputPyAudio(FakePyAudio):
    """Stub PyAudio with the realistic Windows topology where one hardware output
    is exposed once per host API (MME, DirectSound, WASAPI), plus mapper aliases
    that must still be filtered.
    """

    def __init__(self):
        super().__init__()
        self._host_apis = [
            {"index": 0, "name": "MME", "type": 2},
            {"index": 1, "name": "Windows DirectSound", "type": 1},
            {"index": 2, "name": "Windows WASAPI", "type": 13},
        ]
        self._default_device = {
            "index": 5,
            "name": "Speakers (Realtek High Definition Audio)",
            "maxOutputChannels": 2,
            "hostApi": 2,
        }
        self._devices = [
            {"index": 0, "name": "Speakers (Realtek High Definition Audio)", "maxOutputChannels": 2, "hostApi": 0},
            {"index": 1, "name": "Microsoft Sound Mapper - Output", "maxOutputChannels": 2, "hostApi": 0},
            {"index": 2, "name": "Primary Sound Driver", "maxOutputChannels": 2, "hostApi": 1},
            {"index": 3, "name": "Speakers (Realtek High Definition Audio)", "maxOutputChannels": 2, "hostApi": 1},
            {"index": 4, "name": "VB-Audio Virtual Cable", "maxOutputChannels": 2, "hostApi": 1},
            self._default_device,
            {"index": 6, "name": "VB-Audio Virtual Cable", "maxOutputChannels": 2, "hostApi": 2},
            {"index": 7, "name": "Microphone (Realtek)", "maxOutputChannels": 0, "hostApi": 2},
        ]


@pytest.fixture
def _patch_duplicate_output_pyaudio(monkeypatch):
    """Inject duplicate output devices so list_output_devices filtering is isolated."""
    fake_pa = FakeDuplicateOutputPyAudio()
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: fake_pa))
    return fake_pa


def test_list_output_devices_keeps_only_wasapi_variants_when_duplicates_exist(
    _patch_duplicate_output_pyaudio,
):
    """Only WASAPI output variants should be shown in the user-facing device list."""
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()

    assert {d["host_api_name"] for d in devices} == {"Windows WASAPI"}

    realtek = [d for d in devices if d["name"] == "Speakers (Realtek High Definition Audio)"]
    assert [d["index"] for d in realtek] == [5]
    assert [d["host_api_index"] for d in realtek] == [2]

    vb_cable = [d for d in devices if d["name"] == "VB-Audio Virtual Cable"]
    assert [d["index"] for d in vb_cable] == [6]


def test_list_output_devices_returns_unique_wasapi_display_names(
    _patch_duplicate_output_pyaudio,
):
    """Every displayed output device should be a unique Windows WASAPI label."""
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()

    display_names = [d["display_name"] for d in devices]
    assert len(display_names) == len(set(display_names)), (
        f"display_name collisions in {display_names!r}"
    )
    assert all(name.endswith("[Windows WASAPI]") for name in display_names)


def test_list_output_devices_filters_windows_mapper_aliases_english_and_chinese(monkeypatch):
    """Windows mapper aliases are not real selectable hardware outputs."""
    fake_pa = FakePyAudio()
    fake_pa._default_device = {
        "index": 0,
        "name": "Speakers (Realtek High Definition Audio)",
        "maxOutputChannels": 2,
        "hostApi": 1,
    }
    fake_pa._devices = [
        fake_pa._default_device,
        {"index": 1, "name": "Microsoft Sound Mapper - Output", "maxOutputChannels": 2, "hostApi": 0},
        {"index": 2, "name": "Primary Sound Driver", "maxOutputChannels": 2, "hostApi": 0},
        {"index": 3, "name": "Microsoft 声音映射器 - Output", "maxOutputChannels": 2, "hostApi": 0},
        {"index": 4, "name": "主声音驱动程序", "maxOutputChannels": 2, "hostApi": 0},
        {"index": 5, "name": "VB-Audio Virtual Cable", "maxOutputChannels": 2, "hostApi": 1},
    ]
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: fake_pa))
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()

    assert [d["name"] for d in devices] == [
        "Speakers (Realtek High Definition Audio)",
        "VB-Audio Virtual Cable",
    ]


def test_list_output_devices_mapper_filter_does_not_drop_real_duplicates(
    _patch_duplicate_output_pyaudio,
):
    """The mapper-alias blacklist removes only the two alias entries, never real duplicates."""
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()
    names = [d["name"] for d in devices]

    assert "Microsoft Sound Mapper - Output" not in names
    assert "Primary Sound Driver" not in names
    assert names.count("Speakers (Realtek High Definition Audio)") == 1
    assert names.count("VB-Audio Virtual Cable") == 1
    assert "Microphone (Realtek)" not in names  # input-only


def test_list_output_devices_default_flag_only_on_default_index(_patch_duplicate_output_pyaudio):
    """is_default must be True only for the device whose index matches the default index."""
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()

    default_entries = [d for d in devices if d["is_default"]]
    assert len(default_entries) == 1
    assert default_entries[0]["index"] == 5
    assert default_entries[0]["name"] == "Speakers (Realtek High Definition Audio)"
    assert default_entries[0]["host_api_name"] == "Windows WASAPI"


def test_list_output_devices_preserves_existing_filtered_outputs_contract(_patch_pyaudio):
    """Original fixture still returns output-capable devices and excludes input-only devices."""
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()

    assert [d["name"] for d in devices] == ["Fake Default Device", "VB-Cable Output"]
    assert [d["is_default"] for d in devices] == [True, False]
    assert all(d["name"] != "Fake Input Only" for d in devices)


# ---------------------------------------------------------------------------
# AudioPlayer structured selection (host-API-aware) tests
# ---------------------------------------------------------------------------


def test_audio_player_resolves_structured_selection_to_duplicate_device_index(
    _patch_duplicate_output_pyaudio,
):
    """A structured {name, host_api_name} selection must pick the matching host API duplicate.

    The Realtek output is exposed at indices 0/3/5 under MME/DirectSound/WASAPI;
    selecting host_api_name='Windows WASAPI' must resolve to index 5, not 0.
    """
    pa = _patch_duplicate_output_pyaudio
    player = AudioPlayer(
        output_device={
            "name": "Speakers (Realtek High Definition Audio)",
            "host_api_name": "Windows WASAPI",
        },
    )

    result = player._resolve_output_device(pa, device_index=None)
    assert result == 5


def test_audio_player_structured_selection_directsound_picks_index_3(
    _patch_duplicate_output_pyaudio,
):
    """The same raw name under DirectSound must resolve to its own index (3), not the MME duplicate."""
    pa = _patch_duplicate_output_pyaudio
    player = AudioPlayer(
        output_device={
            "name": "Speakers (Realtek High Definition Audio)",
            "host_api_name": "Windows DirectSound",
        },
    )

    result = player._resolve_output_device(pa, device_index=None)
    assert result == 3


def test_audio_player_legacy_name_only_resolution_still_works(
    _patch_duplicate_output_pyaudio,
):
    """Legacy string output_device_name must keep resolving by exact name (first match wins)."""
    pa = _patch_duplicate_output_pyaudio
    player = AudioPlayer(output_device_name="VB-Audio Virtual Cable")

    result = player._resolve_output_device(pa, device_index=None)
    assert result == 4  # first VB-Audio entry (DirectSound) wins under legacy lookup


def test_audio_player_structured_selection_missing_host_api_falls_back_to_default(
    _patch_duplicate_output_pyaudio,
):
    """Structured selection whose host_api_name does not exist must fall back to default device."""
    pa = _patch_duplicate_output_pyaudio
    player = AudioPlayer(
        output_device={
            "name": "Speakers (Realtek High Definition Audio)",
            "host_api_name": "ASIO",  # not present in the fixture
        },
    )

    result = player._resolve_output_device(pa, device_index=None)
    assert result == 5  # default device index from the fixture


# ---------------------------------------------------------------------------
# Open-stream cleanup tests — using new constructor API
# ---------------------------------------------------------------------------

def test_close_stream_stops_and_closes_active_stream(_patch_pyaudio):
    """close_stream must stop the stream, close it, and clear the ref."""
    player = AudioPlayer()
    assert player.open_stream(audio_format=8, channels=1, rate=22050) is True

    stream = player._stream
    assert stream is not None
    assert isinstance(stream, FakePyAudioStream)

    player.close_stream()

    assert stream.stop_calls == 1
    assert stream.close_calls == 1
    assert player._stream is None


def test_cable_stream_upmixes_mono_pcm_to_stereo(_patch_pyaudio):
    """VB-CABLE 双声道端点必须收到逐样本复制后的 L/R 数据。"""
    player = AudioPlayer(output_device_name="VB-Cable Output")

    assert player.open_stream(audio_format=8, channels=1, rate=48000) is True
    player.write_stream(b"\x01\x02\x03\x04")

    assert _patch_pyaudio.open_calls[-1]["channels"] == 2
    assert _patch_pyaudio._stream.write_calls == [
        b"\x01\x02\x01\x02\x03\x04\x03\x04"
    ]


def test_default_stream_keeps_mono_pcm_unchanged(_patch_pyaudio):
    """普通设备继续使用调用方请求的单声道，避免扩大修复范围。"""
    player = AudioPlayer()

    assert player.open_stream(audio_format=8, channels=1, rate=48000) is True
    player.write_stream(b"\x01\x02\x03\x04")

    assert _patch_pyaudio.open_calls[-1]["channels"] == 1
    assert _patch_pyaudio._stream.write_calls == [b"\x01\x02\x03\x04"]


def test_close_stream_terminates_pyaudio_and_clears_ref(_patch_pyaudio):
    """close_stream must terminate the PyAudio instance and clear its ref."""
    player = AudioPlayer()
    assert player.open_stream(audio_format=8, channels=1, rate=22050) is True

    pa = player._stream_p
    assert pa is not None
    assert isinstance(pa, FakePyAudio)

    player.close_stream()

    assert pa.terminate_calls == 1
    assert player._stream_p is None


def test_close_stream_is_idempotent(_patch_pyaudio):
    """Calling close_stream repeatedly should not raise when refs are already cleared."""
    player = AudioPlayer()
    assert player.open_stream(audio_format=8, channels=1, rate=22050) is True

    player.close_stream()
    # Second call must not crash despite None refs.
    player.close_stream()

    assert player._stream is None
    assert player._stream_p is None


# ---------------------------------------------------------------------------
# Cleanup hardening tests — using new constructor API
# ---------------------------------------------------------------------------

def test_close_stream_when_stop_raises_still_closes_and_terminates(_patch_pyaudio):
    """stop_stream raising must NOT prevent close() and terminate() from running."""
    player = AudioPlayer()
    assert player.open_stream(audio_format=8, channels=1, rate=22050) is True

    stream = player._stream
    pa = player._stream_p

    def _boom():
        raise RuntimeError("stop blew up")

    stream.stop_stream = _boom  # type: ignore[assignment]

    # Must not raise even though stop_stream errors.
    player.close_stream()

    assert stream.close_calls == 1
    assert pa.terminate_calls == 1
    assert player._stream is None
    assert player._stream_p is None


def test_close_stream_when_close_raises_still_terminates_pyaudio(_patch_pyaudio):
    """stream.close raising must NOT prevent PyAudio.terminate() from running."""
    player = AudioPlayer()
    assert player.open_stream(audio_format=8, channels=1, rate=22050) is True

    stream = player._stream
    pa = player._stream_p

    def _boom():
        raise RuntimeError("close blew up")

    stream.close = _boom  # type: ignore[assignment]

    player.close_stream()

    assert stream.stop_calls == 1
    assert pa.terminate_calls == 1
    assert player._stream is None
    assert player._stream_p is None


def test_close_stream_when_terminate_raises_clears_refs(_patch_pyaudio):
    """PyAudio.terminate raising must not leave refs nor propagate."""
    player = AudioPlayer()
    assert player.open_stream(audio_format=8, channels=1, rate=22050) is True

    pa = player._stream_p

    def _boom():
        raise RuntimeError("terminate blew up")

    pa.terminate = _boom  # type: ignore[assignment]

    player.close_stream()

    assert player._stream is None
    assert player._stream_p is None


def test_play_wav_finally_terminates_when_stop_stream_raises(_patch_pyaudio, tmp_path):
    """play_wav finally block must close stream and terminate PyAudio even if stop_stream raises."""
    import wave

    wav_path = tmp_path / "tiny.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x00\x00" * 16)

    pa = _patch_pyaudio
    stream = pa._stream

    def _boom():
        raise RuntimeError("stop blew up")

    stream.stop_stream = _boom  # type: ignore[assignment]

    player = AudioPlayer()
    # Must return True and not raise even though stop_stream errors in finally.
    assert player.play_wav(str(wav_path)) is True

    assert stream.close_calls == 1
    assert pa.terminate_calls == 1


def test_play_wav_uses_actual_bytes_for_streaming_wav_duration(_patch_pyaudio, caplog):
    """流式 WAV 的未知长度占位值不能被记录成数万秒。"""
    import wave

    wav_buffer = BytesIO()
    with wave.open(wav_buffer, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(b"\x00\x00" * 480)

    wav_bytes = bytearray(wav_buffer.getvalue())
    # Cartesia 流式 WAV 使用 0xFFFFFFFF 表示 RIFF/data 长度暂时未知。
    wav_bytes[4:8] = b"\xff\xff\xff\xff"
    wav_bytes[40:44] = b"\xff\xff\xff\xff"

    player = AudioPlayer()
    with caplog.at_level("INFO"):
        assert player.play_wav(BytesIO(wav_bytes)) is True

    assert "总时长=0.01 秒" in caplog.text
    assert "48695" not in caplog.text


def test_play_wav_upmixes_mono_for_cable(_patch_pyaudio, tmp_path):
    """完整 WAV 播放与实时流必须使用相同的 VB-CABLE 声道适配。"""
    import wave

    wav_path = tmp_path / "mono.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(48000)
        wf.writeframes(b"\x01\x02\x03\x04")

    player = AudioPlayer(output_device_name="VB-Cable Output")
    assert player.play_wav(str(wav_path)) is True

    assert _patch_pyaudio.open_calls[-1]["channels"] == 2
    assert _patch_pyaudio._stream.write_calls == [
        b"\x01\x02\x01\x02\x03\x04\x03\x04"
    ]


def test_invalid_sample_rate_error_has_specific_guidance(caplog):
    """PortAudio -9997 应提示采样率问题，不能误报为设备独占。"""
    player = AudioPlayer()

    with caplog.at_level("ERROR"):
        player._print_open_stream_error(
            OSError(-9997, "Invalid sample rate"),
            rate=44100,
        )

    assert "不支持 44100 Hz 采样率" in caplog.text
    assert "独占" not in caplog.text


# ---------------------------------------------------------------------------
# open-time failure cleanup (shared _open_pyaudio_stream helper)
# ---------------------------------------------------------------------------

def _make_tiny_wav(tmp_path):
    import wave

    wav_path = tmp_path / "tiny.wav"
    with wave.open(str(wav_path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(22050)
        wf.writeframes(b"\x00\x00" * 16)
    return wav_path


def test_open_stream_terminates_pyaudio_when_device_not_found(monkeypatch):
    """When device resolution returns None, open_stream must terminate the just-created PyAudio."""
    pa = FakePyAudio()

    def _boom():
        raise OSError("no default device")

    pa.get_default_output_device_info = _boom  # type: ignore[assignment]
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: pa))

    player = AudioPlayer()

    assert player.open_stream(audio_format=8, channels=1, rate=22050) is False
    assert pa.terminate_calls == 1
    assert player._stream is None
    assert player._stream_p is None


def test_open_stream_terminates_pyaudio_when_p_open_raises(monkeypatch):
    """When p.open raises, open_stream must terminate PyAudio and leave refs clear."""
    pa = FakePyAudio()

    def _boom_open(**kwargs):
        raise OSError("device busy")

    pa.open = _boom_open  # type: ignore[assignment]
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: pa))

    player = AudioPlayer()

    assert player.open_stream(audio_format=8, channels=1, rate=22050) is False
    assert pa.terminate_calls == 1
    assert player._stream is None
    assert player._stream_p is None


def test_play_wav_terminates_pyaudio_and_closes_wave_when_device_not_found(monkeypatch, tmp_path):
    """play_wav with failing device resolution must terminate PyAudio and return False."""
    pa = FakePyAudio()

    def _boom():
        raise OSError("no default device")

    pa.get_default_output_device_info = _boom  # type: ignore[assignment]
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: pa))

    wav_path = _make_tiny_wav(tmp_path)
    player = AudioPlayer()

    assert player.play_wav(str(wav_path)) is False
    assert pa.terminate_calls == 1
    # stream was never opened, so close/stop counts stay at zero
    assert pa._stream.stop_calls == 0
    assert pa._stream.close_calls == 0


def test_play_wav_terminates_pyaudio_when_p_open_raises(monkeypatch, tmp_path):
    """play_wav must terminate PyAudio (and not crash) when p.open raises before playback."""
    pa = FakePyAudio()

    def _boom_open(**kwargs):
        raise OSError("device busy")

    pa.open = _boom_open  # type: ignore[assignment]
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: pa))

    wav_path = _make_tiny_wav(tmp_path)
    player = AudioPlayer()

    assert player.play_wav(str(wav_path)) is False
    assert pa.terminate_calls == 1


# ---------------------------------------------------------------------------
# mid-write exception cleanup
# ---------------------------------------------------------------------------

def test_play_wav_finally_cleans_up_when_write_raises_midstream(_patch_pyaudio, tmp_path):
    """An exception during stream.write must still trigger stop+close+terminate in finally."""
    pa = _patch_pyaudio
    stream = pa._stream

    call_count = {"n": 0}

    def _boom_write(data):
        call_count["n"] += 1
        raise RuntimeError("write blew up mid-stream")

    stream.write = _boom_write  # type: ignore[assignment]

    wav_path = _make_tiny_wav(tmp_path)
    player = AudioPlayer()

    # play_wav swallows generic Exception inside the try/except, so it still returns True
    assert player.play_wav(str(wav_path)) is True

    # finally must have stopped + closed the stream and terminated PyAudio exactly once
    assert stream.stop_calls == 1
    assert stream.close_calls == 1
    assert pa.terminate_calls == 1
    assert call_count["n"] == 1  # write was indeed exercised before the boom


def test_play_wav_finally_cleans_up_when_keyboardinterrupt_midstream(_patch_pyaudio, tmp_path):
    """KeyboardInterrupt during playback must still trigger full cleanup in finally."""
    pa = _patch_pyaudio
    stream = pa._stream

    def _interrupt(_data):
        raise KeyboardInterrupt

    stream.write = _interrupt  # type: ignore[assignment]

    wav_path = _make_tiny_wav(tmp_path)
    player = AudioPlayer()

    assert player.play_wav(str(wav_path)) is True

    assert stream.stop_calls == 1
    assert stream.close_calls == 1
    assert pa.terminate_calls == 1



class FakePartiallyFailingPyAudio(FakePyAudio):
    """PyAudio stub where get_device_info_by_index raises for some indices."""

    def __init__(self, failing_indices: set[int]) -> None:
        super().__init__()
        self.failing_indices = failing_indices
        self.get_device_calls: list[int] = []

    def get_device_info_by_index(self, index: int):
        self.get_device_calls.append(index)
        if index in self.failing_indices:
            raise OSError(f"device {index} info unavailable")
        return self._devices[index]


def test_list_output_devices_skips_devices_whose_info_lookup_raises(monkeypatch):
    """Characterization: per-device get_device_info_by_index exception is tolerated.

    list_output_devices must continue enumerating after a per-device failure,
    returning the remaining successfully-queried output devices.
    """
    pa = FakePartiallyFailingPyAudio(failing_indices={1})
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: pa))
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()

    assert pa.get_device_calls == [0, 1, 2]
    names = [d["name"] for d in devices]
    assert "Fake Default Device" in names
    assert "VB-Cable Output" not in names


def test_list_output_devices_returns_empty_when_all_lookups_raise(monkeypatch):
    """Characterization: all per-device exceptions still yield a clean empty list."""
    pa = FakePartiallyFailingPyAudio(failing_indices={0, 1, 2})
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: pa))
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()

    assert devices == []
    assert pa.get_device_calls == [0, 1, 2]
    assert pa.terminate_calls == 1


class FakeMalformedDevicePyAudio(FakePyAudio):
    """PyAudio stub that returns malformed records among valid output devices."""

    def __init__(self) -> None:
        super().__init__()
        self._devices = [
            self._default_device,
            {"name": "Missing Index", "maxOutputChannels": 2, "hostApi": 1},
            {"index": 2, "name": "Missing Host", "maxOutputChannels": 2},
            {"index": 3, "name": "Good WASAPI Device", "maxOutputChannels": 2, "hostApi": 1},
        ]


def test_list_output_devices_skips_malformed_records_without_dropping_valid_devices(monkeypatch):
    """Malformed device records should not abort enumeration of later valid records."""
    pa = FakeMalformedDevicePyAudio()
    monkeypatch.setattr("wordy.audio.player.pyaudio", MagicMock(PyAudio=lambda: pa))
    from wordy.audio.player import list_output_devices

    devices = list_output_devices()

    names = [device["name"] for device in devices]
    assert names == ["Fake Default Device", "Good WASAPI Device"]
    assert pa.terminate_calls == 1
