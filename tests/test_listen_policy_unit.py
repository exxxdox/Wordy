#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Windows 侦听策略的纯单元测试，不访问或修改真实音频设备。"""

from __future__ import annotations

from ctypes import POINTER, c_void_p, cast, sizeof
from unittest.mock import MagicMock, patch

from wordy.audio.listen_policy import (
    ListenPolicyState,
    PROPERTYKEY,
    PROPVARIANT,
    VT_BOOL,
    VT_LPWSTR,
    _find_output_device_id,
    _hresult_succeeded,
    _listen_policy_matches,
    _write_listen_properties,
)


class _FakePropertyStore:
    """立即复制传入值，避免测试依赖 ctypes 临时对象的生命周期。"""

    def __init__(self, *, failures: dict[int, int] | None = None, commit_hr: int = 0):
        self.failures = failures or {}
        self.commit_hr = commit_hr
        self.values: list[tuple[int, int, int | str | None]] = []
        self.commit_count = 0

    def SetValue(self, key_ptr, value_ptr) -> int:  # noqa: N802 - COM 方法命名
        key = cast(key_ptr, POINTER(PROPERTYKEY)).contents
        value = cast(value_ptr, POINTER(PROPVARIANT)).contents
        if value.vt == VT_BOOL:
            copied_value: int | str | None = value.boolVal
        elif value.vt == VT_LPWSTR:
            copied_value = value.pwszVal
        else:
            copied_value = None
        self.values.append((key.pid, value.vt, copied_value))
        return self.failures.get(key.pid, 0)

    def Commit(self) -> int:  # noqa: N802 - COM 方法命名
        self.commit_count += 1
        return self.commit_hr


def test_propvariant_matches_windows_abi() -> None:
    """头部占 8 字节，值联合体必须从偏移 8 开始并保留完整原生空间。"""
    assert PROPVARIANT.value.offset == 8
    assert PROPVARIANT.boolVal.offset == 8
    assert PROPVARIANT.pwszVal.offset == 8
    assert sizeof(PROPVARIANT) == (24 if sizeof(c_void_p) == 8 else 16)


def test_propvariant_bool_and_string_share_union() -> None:
    enabled = PROPVARIANT()
    enabled.vt = VT_BOOL
    enabled.boolVal = -1
    assert enabled.boolVal == -1

    target = PROPVARIANT()
    target.vt = VT_LPWSTR
    target.pwszVal = "device-id"
    assert target.pwszVal == "device-id"


def test_hresult_uses_success_bit() -> None:
    assert _hresult_succeeded(0)
    assert _hresult_succeeded(1)
    assert not _hresult_succeeded(0x80004005)


def test_listen_policy_readback_match_contract() -> None:
    expected = ListenPolicyState(enabled=True, output_id="render-device-id")
    assert _listen_policy_matches(expected, "render-device-id", True)
    assert not _listen_policy_matches(expected, "other-device-id", True)
    assert not _listen_policy_matches(None, "render-device-id", True)

    # Windows 禁用侦听时会保留上一次目标，因此禁用只校验 enabled。
    disabled = ListenPolicyState(enabled=False, output_id="stale-device-id")
    assert _listen_policy_matches(disabled, None, False)
    assert _listen_policy_matches(
        disabled,
        "stale-device-id",
        False,
        require_output_match=True,
    )
    assert not _listen_policy_matches(
        disabled,
        "other-device-id",
        False,
        require_output_match=True,
    )


def test_write_enabled_listen_properties_and_commit() -> None:
    store = _FakePropertyStore()

    assert _write_listen_properties(store, "render-device-id", True)
    assert store.values == [
        (0, VT_LPWSTR, "render-device-id"),
        (1, VT_BOOL, -1),
    ]
    assert store.commit_count == 1


def test_write_disabled_listen_property_without_target() -> None:
    store = _FakePropertyStore()

    assert _write_listen_properties(store, None, False)
    assert store.values == [(1, VT_BOOL, 0)]
    assert store.commit_count == 1


def test_restore_disabled_policy_also_restores_historical_target() -> None:
    store = _FakePropertyStore()

    assert _write_listen_properties(
        store,
        "original-render-id",
        False,
        restore_output=True,
    )
    assert store.values == [
        (0, VT_LPWSTR, "original-render-id"),
        (1, VT_BOOL, 0),
    ]
    assert store.commit_count == 1


def test_enable_without_target_fails_closed() -> None:
    store = _FakePropertyStore()

    assert not _write_listen_properties(store, None, True)
    assert store.values == []
    assert store.commit_count == 0


def test_target_write_failure_is_reported_without_commit() -> None:
    store = _FakePropertyStore(failures={0: 0x80004005})

    assert not _write_listen_properties(store, "render-device-id", True)
    assert [value[0] for value in store.values] == [0]
    assert store.commit_count == 0


def test_enabled_flag_write_failure_is_reported_without_commit() -> None:
    store = _FakePropertyStore(failures={1: 0x80004005})

    assert not _write_listen_properties(store, "render-device-id", True)
    assert [value[0] for value in store.values] == [0, 1]
    assert store.commit_count == 0


def test_commit_failure_is_reported() -> None:
    store = _FakePropertyStore(commit_hr=0x80004005)

    assert not _write_listen_properties(store, None, False)
    assert store.commit_count == 1


def test_explicit_output_name_never_falls_back_to_default() -> None:
    enumerator = MagicMock()
    with patch("wordy.audio.listen_policy._find_device_id", return_value=None):
        assert _find_output_device_id(enumerator, "CABLE Input") is None

    enumerator.GetDefaultAudioEndpoint.assert_not_called()
