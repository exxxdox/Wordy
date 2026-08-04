#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Tests for AudioIdentity TypedDict and normalize_identity helper."""

from wordy.identity import AudioIdentity, normalize_identity


class TestAudioIdentityTypedDict:
    """Verify the shape of the AudioIdentity TypedDict."""

    def test_keys_and_types(self) -> None:
        identity: AudioIdentity = {"name": "Speakers", "host_api_name": "MME"}
        assert identity["name"] == "Speakers"
        assert identity["host_api_name"] == "MME"
        # host_api_name may be None per the type definition
        identity_none: AudioIdentity = {"name": "Default", "host_api_name": None}
        assert identity_none["host_api_name"] is None


class TestNormalizeIdentity:
    """Tests for normalize_identity(name, host_api_name)."""

    def test_normal_path(self) -> None:
        """Both name and host_api_name are passed through."""
        result = normalize_identity("Speakers", "MME")
        assert result == {"name": "Speakers", "host_api_name": "MME"}

    def test_host_api_name_none(self) -> None:
        """host_api_name=None stays None."""
        result = normalize_identity("Speakers", None)
        assert result == {"name": "Speakers", "host_api_name": None}

    def test_host_api_name_empty_string_becomes_none(self) -> None:
        """Empty string is falsy, so it becomes None."""
        result = normalize_identity("Speakers", "")
        assert result == {"name": "Speakers", "host_api_name": None}

    def test_host_api_name_mme(self) -> None:
        """Truthy host_api_name is preserved as-is."""
        result = normalize_identity("MyDevice", "MME")
        assert result == {"name": "MyDevice", "host_api_name": "MME"}

    def test_host_api_name_default_omitted(self) -> None:
        """Default parameter (None) produces host_api_name=None."""
        result = normalize_identity("Speakers")
        assert result == {"name": "Speakers", "host_api_name": None}

    def test_return_type_is_audio_identity(self) -> None:
        """Return value satisfies the AudioIdentity shape."""
        result = normalize_identity("Speakers", "WASAPI")
        # TypedDict is just a dict at runtime; validate keys
        assert isinstance(result, dict)
        assert set(result.keys()) == {"name", "host_api_name"}
