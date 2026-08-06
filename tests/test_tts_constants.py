"""TTS 常量模块测试。"""

from wordy.tts.constants import (
    DEFAULT_TTS_API_PROVIDER,
    DEFAULT_TTS_BACKEND,
    TTS_API_PROVIDER_CARTESIA,
    TTS_API_PROVIDER_VOLCENGINE,
    TTS_API_PROVIDERS,
    TTS_BACKEND_CARTESIA_BYTES,
    TTS_BACKEND_CARTESIA_REALTIME,
    TTS_BACKEND_VOLCENGINE_STREAMING,
    TTS_BACKENDS,
    TTS_BACKENDS_BY_PROVIDER,
)


class TestTTSConstants:
    """验证 TTS 常量值不变。"""

    def test_provider_identifiers_are_strings(self):
        assert isinstance(TTS_API_PROVIDER_CARTESIA, str)
        assert isinstance(TTS_API_PROVIDER_VOLCENGINE, str)
        assert TTS_API_PROVIDER_CARTESIA != TTS_API_PROVIDER_VOLCENGINE

    def test_providers_tuple_contains_both(self):
        assert TTS_API_PROVIDER_CARTESIA in TTS_API_PROVIDERS
        assert TTS_API_PROVIDER_VOLCENGINE in TTS_API_PROVIDERS

    def test_default_provider_is_volcengine(self):
        assert DEFAULT_TTS_API_PROVIDER == TTS_API_PROVIDER_VOLCENGINE

    def test_backend_identifiers_are_unique_strings(self):
        backends = [
            TTS_BACKEND_CARTESIA_BYTES,
            TTS_BACKEND_CARTESIA_REALTIME,
            TTS_BACKEND_VOLCENGINE_STREAMING,
        ]
        assert len(set(backends)) == len(backends)
        for b in backends:
            assert isinstance(b, str)

    def test_backends_tuple_matches_flat_list(self):
        assert set(TTS_BACKENDS) == {
            TTS_BACKEND_CARTESIA_BYTES,
            TTS_BACKEND_CARTESIA_REALTIME,
            TTS_BACKEND_VOLCENGINE_STREAMING,
        }

    def test_default_backend_is_volcengine_streaming(self):
        assert DEFAULT_TTS_BACKEND == TTS_BACKEND_VOLCENGINE_STREAMING

    def test_backends_by_provider_maps_correctly(self):
        assert TTS_BACKENDS_BY_PROVIDER[TTS_API_PROVIDER_CARTESIA] == (
            TTS_BACKEND_CARTESIA_BYTES,
            TTS_BACKEND_CARTESIA_REALTIME,
        )
        assert TTS_BACKENDS_BY_PROVIDER[TTS_API_PROVIDER_VOLCENGINE] == (
            TTS_BACKEND_VOLCENGINE_STREAMING,
        )

    def test_backends_by_provider_keys_match_providers(self):
        """每个 provider 都在 TTS_BACKENDS_BY_PROVIDER 中有关联后端。"""
        for provider in TTS_API_PROVIDERS:
            assert provider in TTS_BACKENDS_BY_PROVIDER
            assert len(TTS_BACKENDS_BY_PROVIDER[provider]) >= 1

    def test_backends_by_provider_values_are_subset_of_all_backends(self):
        all_backends = set(TTS_BACKENDS)
        for backends in TTS_BACKENDS_BY_PROVIDER.values():
            for backend in backends:
                assert backend in all_backends
