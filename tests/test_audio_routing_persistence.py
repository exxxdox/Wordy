#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""音频路由配置持久化往返测试。"""

from pathlib import Path

import pytest

import easy_tts.config
from easy_tts.config import AppSettings, MIN_GAIN, MAX_GAIN


class TestAudioRoutingPersistence:
    """验证所有音频路由字段能正确保存并在重新加载后恢复。"""

    def test_audio_routing_fields_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """所有 5 个音频路由字段保存后应能完整恢复。"""
        config_file = tmp_path / "test_config.json"
        monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", config_file)

        # 初始默认值
        s = AppSettings.load(config_file=config_file)
        assert s.audio_routing_enabled is False
        assert s.mic_input_device is None
        assert s.virtual_output_device is None
        assert s.mic_gain == 1.0
        assert s.tts_gain == 1.0

        # 保存一组非默认的音频路由配置
        s.update(
            audio_routing_enabled=True,
            mic_input_device="麦克风 (Realtek Audio)",
            virtual_output_device="CABLE Input",
            mic_gain=1.5,
            tts_gain=2.0,
            config_file=config_file,
        )

        # 重新加载，验证所有字段被正确恢复
        s2 = AppSettings.load(config_file=config_file)
        assert s2.audio_routing_enabled is True
        assert s2.mic_input_device == "麦克风 (Realtek Audio)"
        assert s2.virtual_output_device == "CABLE Input"
        assert s2.mic_gain == 1.5
        assert s2.tts_gain == 2.0

    def test_audio_routing_disable_persists(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """禁用音频路由后，再次加载应仍为禁用。"""
        config_file = tmp_path / "test_config.json"
        monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", config_file)

        # 先启用
        s = AppSettings.load(config_file=config_file)
        s.update(audio_routing_enabled=True, config_file=config_file)
        s2 = AppSettings.load(config_file=config_file)
        assert s2.audio_routing_enabled is True

        # 再禁用
        s2.update(audio_routing_enabled=False, config_file=config_file)
        s3 = AppSettings.load(config_file=config_file)
        assert s3.audio_routing_enabled is False

    def test_audio_routing_gain_clamping(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """增益值超出范围时应被钳制到 [MIN_GAIN, MAX_GAIN]。"""
        config_file = tmp_path / "test_config.json"
        monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", config_file)

        s = AppSettings.load(config_file=config_file)
        s.update(
            mic_gain=3.0,
            tts_gain=2.5,
            config_file=config_file,
        )

        s2 = AppSettings.load(config_file=config_file)
        assert s2.mic_gain == MAX_GAIN
        assert s2.tts_gain == MAX_GAIN

    def test_audio_routing_empty_strings_become_none(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """空字符串设备名应被解析为 None。"""
        config_file = tmp_path / "test_config.json"
        monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", config_file)

        s = AppSettings.load(config_file=config_file)
        s.update(
            mic_input_device="  ",
            config_file=config_file,
        )

        s2 = AppSettings.load(config_file=config_file)
        assert s2.mic_input_device is None

    def test_audio_routing_partial_save_preserves_others(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """部分更新音频路由字段时，其他字段应保持不变。"""
        config_file = tmp_path / "test_config.json"
        monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", config_file)

        # 先保存完整配置
        s = AppSettings.load(config_file=config_file)
        s.update(
            audio_routing_enabled=True,
            mic_input_device="麦克风 A",
            mic_gain=1.2,
            tts_gain=1.1,
            config_file=config_file,
        )

        # 只更新 mic_gain
        s.update(mic_gain=1.8, config_file=config_file)

        s2 = AppSettings.load(config_file=config_file)
        assert s2.audio_routing_enabled is True
        assert s2.mic_input_device == "麦克风 A"
        assert s2.mic_gain == 1.8
        assert s2.tts_gain == 1.1

    def test_save_audio_routing_config_helper(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """update() 可替代原 save_audio_routing_config 辅助函数。"""
        config_file = tmp_path / "test_config.json"
        monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", config_file)

        s = AppSettings.load(config_file=config_file)
        s.update(
            audio_routing_enabled=True,
            mic_input_device="Mic Test",
            mic_gain=1.3,
            config_file=config_file,
        )

        s2 = AppSettings.load(config_file=config_file)
        assert s2.audio_routing_enabled is True
        assert s2.mic_input_device == "Mic Test"
        assert s2.mic_gain == 1.3
        # 未提供的字段应保持默认值
        assert s2.tts_gain == 1.0

    def test_load_audio_routing_config_helper(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """AppSettings.load() 即可获取所有音频路由字段，替代原 load_audio_routing_config。"""
        config_file = tmp_path / "test_config.json"
        monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", config_file)

        s = AppSettings.load(config_file=config_file)
        s.update(
            audio_routing_enabled=True,
            mic_input_device="Test Mic",
            virtual_output_device="Test Virtual",
            mic_gain=1.1,
            tts_gain=1.2,
            config_file=config_file,
        )

        s2 = AppSettings.load(config_file=config_file)
        assert s2.audio_routing_enabled is True
        assert s2.mic_input_device == "Test Mic"
        assert s2.virtual_output_device == "Test Virtual"
        assert s2.mic_gain == 1.1
        assert s2.tts_gain == 1.2

    def test_sidetone_enabled_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """返听开关保存后应能完整恢复。"""
        config_file = tmp_path / "test_config.json"
        monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", config_file)

        s = AppSettings.load(config_file=config_file)
        assert s.sidetone_enabled is False

        s.update(sidetone_enabled=True, config_file=config_file)
        s2 = AppSettings.load(config_file=config_file)
        assert s2.sidetone_enabled is True

        s2.update(sidetone_enabled=False, config_file=config_file)
        s3 = AppSettings.load(config_file=config_file)
        assert s3.sidetone_enabled is False

    def test_sidetone_enabled_invalid_type_falls_back(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """非法类型应回退到默认值 False。"""
        import json
        config_file = tmp_path / "test_config.json"
        monkeypatch.setattr(easy_tts.config, "USER_CONFIG_FILE", config_file)

        config_file.write_text(json.dumps({"sidetone_enabled": "not_a_bool"}), encoding="utf-8")
        s = AppSettings.load(config_file=config_file)
        assert s.sidetone_enabled is False
