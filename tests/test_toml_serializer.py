"""TOML 序列化器测试。"""

from pathlib import Path

import pytest

from wordy._toml_serializer import _is_inline_table, _toml_value, read_toml, to_toml, write_toml_atomic


class TestReadToml:
    """read_toml 测试。"""

    def test_returns_none_for_missing_file(self, tmp_path: Path):
        assert read_toml(tmp_path / "nonexistent.toml") is None

    def test_empty_file_returns_empty_dict(self, tmp_path: Path):
        """空文件是合法 TOML，tomllib 返回 {}。"""
        p = tmp_path / "empty.toml"
        p.write_text("", encoding="utf-8")
        assert read_toml(p) == {}

    def test_returns_none_for_invalid_toml(self, tmp_path: Path):
        p = tmp_path / "bad.toml"
        p.write_text("{{{", encoding="utf-8")
        assert read_toml(p) is None

    def test_toml_table_array_parsed_as_dict(self, tmp_path: Path):
        """[[items]] 被 tomllib 解析为 dict（非 list），read_toml 通过。"""
        p = tmp_path / "array.toml"
        p.write_text("[[items]]\nname = 'x'", encoding="utf-8")
        # tomllib 将 TOML 文档根始终解析为 dict
        result = read_toml(p)
        assert isinstance(result, dict)
        assert "items" in result

    def test_reads_flat_dict(self, tmp_path: Path):
        p = tmp_path / "flat.toml"
        p.write_text('key = "value"\ncount = 42\n', encoding="utf-8")
        result = read_toml(p)
        assert result == {"key": "value", "count": 42}

    def test_reads_nested_section(self, tmp_path: Path):
        p = tmp_path / "nested.toml"
        p.write_text('name = "root"\n\n[audio]\nvolume = 0.8\n', encoding="utf-8")
        result = read_toml(p)
        assert result == {"name": "root", "audio": {"volume": 0.8}}


class TestToToml:
    """to_toml 序列化测试。"""

    def test_empty_dict_produces_newline_only(self):
        assert to_toml({}) == "\n"

    def test_flat_scalars(self):
        out = to_toml({"name": "test", "count": 42, "enabled": True, "ratio": 1.5})
        assert 'name = "test"' in out
        assert "count = 42" in out
        assert "enabled = true" in out
        assert "ratio = 1.5" in out

    def test_none_values_omitted(self):
        out = to_toml({"keep": "val", "skip": None})
        assert 'keep = "val"' in out
        assert "skip" not in out.split("\n")[0]

    def test_nested_section(self):
        """含嵌套 dict 值的表不会被内联，始终输出 [section]。"""
        out = to_toml({"name": "root", "audio": {"volume": 0.8, "enabled": True, "channels": 2, "rate": 44100}})
        assert 'name = "root"' in out
        assert "[audio]" in out
        assert "volume = 0.8" in out
        assert "enabled = true" in out

    def test_nested_section_none_values_omitted(self):
        """None 不出现在 section 输出中。"""
        out = to_toml({"table": {"a": 1, "b": None, "c": 2, "d": 3, "e": 4}})
        assert "[table]" in out
        assert "a = 1" in out
        assert "b =" not in out

    def test_boolean_false(self):
        out = to_toml({"flag": False})
        assert "flag = false" in out

    def test_inline_table_small_dict(self):
        """≤3 键、无嵌套 dict 值 → 内联表。"""
        out = to_toml({"point": {"x": 1, "y": 2}})
        assert "point = {x = 1, y = 2}" in out

    def test_large_dict_becomes_section(self):
        """>3 键 → [section]。"""
        out = to_toml({"cfg": {"a": 1, "b": 2, "c": 3, "d": 4}})
        assert "[cfg]" in out
        assert "a = 1" in out

    def test_string_escaping(self):
        out = to_toml({"path": 'C:\\Users\\"name"'})
        assert 'C:\\\\Users\\\\\\"name\\"' in out


class TestIsInlineTable:
    """_is_inline_table 判断逻辑。"""

    def test_small_flat_dict_is_inline(self):
        assert _is_inline_table({"a": 1}) is True
        assert _is_inline_table({"a": 1, "b": 2, "c": 3}) is True

    def test_large_dict_is_not_inline(self):
        assert _is_inline_table({"a": 1, "b": 2, "c": 3, "d": 4}) is False

    def test_dict_with_nested_dict_is_not_inline(self):
        assert _is_inline_table({"a": {"nested": 1}}) is False


class TestTomlValue:
    """_toml_value 字面量生成。"""

    def test_bool(self):
        assert _toml_value(True) == "true"
        assert _toml_value(False) == "false"

    def test_int(self):
        assert _toml_value(42) == "42"
        assert _toml_value(-1) == "-1"

    def test_float(self):
        assert _toml_value(3.14) == "3.14"

    def test_string(self):
        assert _toml_value("hello") == '"hello"'

    def test_string_with_quotes_escaped(self):
        result = _toml_value('say "hi"')
        assert result == '"say \\"hi\\""'

    def test_string_with_backslash_escaped(self):
        result = _toml_value("a\\b")
        assert result == '"a\\\\b"'

    def test_none(self):
        assert _toml_value(None) == '""'

    def test_inline_dict(self):
        result = _toml_value({"x": 1, "y": 2})
        assert result == "{x = 1, y = 2}"

    def test_unknown_type_falls_back_to_string(self):
        result = _toml_value([1, 2, 3])
        assert result == '"[1, 2, 3]"'


class TestWriteTomlAtomic:
    """write_toml_atomic 原子写入测试。"""

    def test_creates_file_with_content(self, tmp_path: Path):
        p = tmp_path / "sub" / "config.toml"
        write_toml_atomic(p, {"key": "value"})
        assert p.exists()
        content = p.read_text(encoding="utf-8")
        assert 'key = "value"' in content

    def test_creates_parent_directories(self, tmp_path: Path):
        p = tmp_path / "deep" / "nested" / "cfg.toml"
        write_toml_atomic(p, {"a": 1})
        assert p.exists()

    def test_overwrites_existing_file(self, tmp_path: Path):
        p = tmp_path / "cfg.toml"
        p.write_text("old = true\n", encoding="utf-8")
        write_toml_atomic(p, {"new": "data"})
        content = p.read_text(encoding="utf-8")
        assert 'new = "data"' in content
        assert "old =" not in content

    def test_cleanup_temp_file_on_error(self, tmp_path: Path, monkeypatch):
        p = tmp_path / "cfg.toml"
        import os

        def failing_replace(_src, _dst):
            raise OSError("simulated failure")

        monkeypatch.setattr(os, "replace", failing_replace)
        before = set(tmp_path.iterdir())
        with pytest.raises(OSError):
            write_toml_atomic(p, {"key": "val"})
        assert not p.exists()
        after = set(tmp_path.iterdir())
        assert before == after
