#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""Windows "倾听此设备" 策略管理。"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from ctypes import (
    POINTER,
    OleDLL,
    Structure,
    Union,
    byref,
    c_byte,
    c_short,
    c_uint16,
    c_uint32,
    c_void_p,
    c_wchar_p,
    sizeof,
)
from ctypes.wintypes import DWORD
from typing import Any, cast as type_cast

from comtypes import CLSCTX_INPROC_SERVER, GUID, IUnknown, STDMETHOD, CoCreateInstance

logger = logging.getLogger(__name__)

VT_BOOL = 11
VT_LPWSTR = 31
STGM_READ = 0
STGM_READWRITE = 2


@dataclass(frozen=True)
class ListenPolicyState:
    """从 Windows 端点属性仓库读回的侦听状态。"""

    enabled: bool
    output_id: str | None


# ── PROPERTYKEY ctypes struct ─────────────────────────────────────────

class PROPERTYKEY(Structure):
    """PROPERTYKEY = {fmtid: GUID, pid: DWORD} (20 bytes)."""
    _fields_ = [("fmtid", GUID), ("pid", DWORD)]


class _PROPVARIANT_VALUE(Union):
    """PROPVARIANT 的值联合体，仅暴露本模块需要的成员。

    保留原生联合体的完整空间，避免 GetValue 在 64 位进程中写越界。
    """

    _fields_ = [
        ("boolVal", c_short),
        ("pwszVal", c_wchar_p),
        ("_storage", c_byte * (16 if sizeof(c_void_p) == 8 else 8)),
    ]


class PROPVARIANT(Structure):
    """与 Windows PROPVARIANT ABI 一致的最小 ctypes 定义。"""

    _anonymous_ = ("value",)
    _fields_ = [
        ("vt", c_uint16),
        ("wReserved1", c_uint16),
        ("wReserved2", c_uint16),
        ("wReserved3", c_uint16),
        ("value", _PROPVARIANT_VALUE),
    ]


# GetValue/GetId 返回的内存由调用方释放，集中声明可避免每次调用重复配置 ABI。
_ole32 = OleDLL("ole32")
_prop_variant_clear = _ole32.PropVariantClear
_prop_variant_clear.argtypes = [POINTER(PROPVARIANT)]
_prop_variant_clear.restype = c_uint32
_co_task_mem_free = _ole32.CoTaskMemFree
_co_task_mem_free.argtypes = [c_void_p]
_co_task_mem_free.restype = None


# ── COM interface definitions ───────────────────────────────────────────

class IPropertyStore(IUnknown):
    _iid_ = GUID("{886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99}")
    _methods_ = [
        STDMETHOD(c_uint32, "GetCount", [POINTER(c_uint32)]),
        STDMETHOD(c_uint32, "GetAt", [c_uint32, POINTER(PROPERTYKEY)]),
        STDMETHOD(c_uint32, "GetValue", [POINTER(PROPERTYKEY), POINTER(PROPVARIANT)]),
        STDMETHOD(c_uint32, "SetValue", [POINTER(PROPERTYKEY), POINTER(PROPVARIANT)]),
        STDMETHOD(c_uint32, "Commit", []),
    ]


class IMMDevice(IUnknown):
    _iid_ = GUID("{D666063F-1587-4E43-81F1-B948E807363F}")
    _methods_ = [
        STDMETHOD(c_uint32, "Activate",
                  [POINTER(GUID), c_uint32, POINTER(PROPVARIANT), POINTER(POINTER(IUnknown))]),
        STDMETHOD(c_uint32, "OpenPropertyStore", [c_uint32, POINTER(POINTER(IPropertyStore))]),
        STDMETHOD(c_uint32, "GetId", [POINTER(c_wchar_p)]),
        STDMETHOD(c_uint32, "GetState", [POINTER(c_uint32)]),
    ]


class IMMDeviceCollection(IUnknown):
    _iid_ = GUID("{0BD7A1BE-7A1A-44DB-8397-CC5392387B5E}")
    _methods_ = [
        STDMETHOD(c_uint32, "GetCount", [POINTER(c_uint32)]),
        STDMETHOD(c_uint32, "Item", [c_uint32, POINTER(POINTER(IMMDevice))]),
    ]


class IMMDeviceEnumerator(IUnknown):
    _iid_ = GUID("{A95664D2-9614-4F35-A746-DE8DB63617E6}")
    _methods_ = [
        STDMETHOD(c_uint32, "EnumAudioEndpoints",
                  [c_uint32, c_uint32, POINTER(POINTER(IMMDeviceCollection))]),
        STDMETHOD(c_uint32, "GetDefaultAudioEndpoint",
                  [c_uint32, c_uint32, POINTER(POINTER(IMMDevice))]),
        STDMETHOD(c_uint32, "GetDevice",
                  [c_wchar_p, POINTER(POINTER(IMMDevice))]),
        STDMETHOD(c_uint32, "RegisterEndpointNotificationCallback",
                  [POINTER(IUnknown)]),
        STDMETHOD(c_uint32, "UnregisterEndpointNotificationCallback",
                  [POINTER(IUnknown)]),
    ]


# ── Pre-built GUIDs ─────────────────────────────────────────────────────

CLSID_MMDeviceEnumerator = GUID("{BCDE0395-E52F-467C-8E3D-C4579291692E}")
PKEY_FriendlyName = GUID("{A45C254E-DF1C-4EFD-8020-67D146A850E0}")
PKEY_ListenTo = GUID("{24DBB0FC-9311-4B3D-9CF0-18FF155639D4}")


# ── Public API ──────────────────────────────────────────────────────────

def set_listen_policy(input_device_name: str, output_device_name: str,
                      enabled: bool = True) -> bool:
    """设置指定输入设备的"倾听此设备"策略。"""
    try:
        # comtypes 运行时生成 COM 方法，静态类型系统无法从 _methods_ 推导属性。
        enumerator = type_cast(Any, CoCreateInstance(
            CLSID_MMDeviceEnumerator, IMMDeviceEnumerator, CLSCTX_INPROC_SERVER,
        ))

        # 1. 查找输入设备
        input_id = _find_input_device_id(enumerator, input_device_name)
        if input_id is None:
            logger.warning("未找到输入设备: %s", input_device_name)
            return False

        # 禁用不需要目标端点；启用时必须解析目标，避免误送到默认扬声器。
        output_id = None
        if enabled:
            output_id = _find_output_device_id(enumerator, output_device_name)
            if output_id is None:
                logger.warning("未找到输出设备: %s", output_device_name)
                return False

        # 3. 获取输入设备 IMMDevice
        imm_device = POINTER(IMMDevice)()
        hr = enumerator.GetDevice(c_wchar_p(input_id), byref(imm_device))
        if not _hresult_succeeded(hr) or not imm_device:
            logger.warning("GetDevice failed: 0x%08X", int(hr) & 0xFFFFFFFF)
            return False

        # 4. 设置倾听属性
        return _set_listen(imm_device, output_id, enabled)

    except Exception:
        logger.exception("设置倾听策略失败")
        return False


def get_listen_policy(input_device_name: str) -> ListenPolicyState | None:
    """读取指定输入设备当前状态，供调用方在接管前保存快照。"""
    try:
        enumerator = type_cast(Any, CoCreateInstance(
            CLSID_MMDeviceEnumerator,
            IMMDeviceEnumerator,
            CLSCTX_INPROC_SERVER,
        ))
        input_id = _find_input_device_id(enumerator, input_device_name)
        if input_id is None:
            logger.warning("无法读取侦听状态，未找到输入设备: %s", input_device_name)
            return None

        imm_device = POINTER(IMMDevice)()
        hr = enumerator.GetDevice(c_wchar_p(input_id), byref(imm_device))
        if not _hresult_succeeded(hr) or not imm_device:
            logger.warning("读取侦听状态时 GetDevice failed: 0x%08X", int(hr) & 0xFFFFFFFF)
            return None
        return _read_listen_policy_state(imm_device)
    except Exception:
        logger.exception("读取输入设备侦听状态失败: %s", input_device_name)
        return None


def restore_listen_policy(
    input_device_name: str,
    state: ListenPolicyState,
) -> bool:
    """恢复接管前的启用状态，并在存在历史目标时一并恢复目标端点。"""
    try:
        enumerator = type_cast(Any, CoCreateInstance(
            CLSID_MMDeviceEnumerator,
            IMMDeviceEnumerator,
            CLSCTX_INPROC_SERVER,
        ))
        input_id = _find_input_device_id(enumerator, input_device_name)
        if input_id is None:
            logger.warning("无法恢复侦听状态，未找到输入设备: %s", input_device_name)
            return False

        imm_device = POINTER(IMMDevice)()
        hr = enumerator.GetDevice(c_wchar_p(input_id), byref(imm_device))
        if not _hresult_succeeded(hr) or not imm_device:
            logger.warning("恢复侦听状态时 GetDevice failed: 0x%08X", int(hr) & 0xFFFFFFFF)
            return False
        return _set_listen(
            imm_device,
            state.output_id,
            state.enabled,
            restore_output=state.output_id is not None,
        )
    except Exception:
        logger.exception("恢复输入设备侦听状态失败: %s", input_device_name)
        return False


# ── Internals ───────────────────────────────────────────────────────────

def _hresult_succeeded(hr: int) -> bool:
    """按 HRESULT 最高位判断成功，兼容 S_OK 之外的成功状态。"""
    return int(hr) & 0x80000000 == 0


def _normalize_device_name(name: str) -> str:
    """只忽略首尾空白和大小写，避免把不同端点归一化为同一设备。"""
    return name.strip().casefold()


def _take_device_id(item: Any) -> str | None:
    """读取并复制 IMMDevice ID，随后释放 COM 分配的字符串。"""
    dev_id = c_wchar_p()
    hr = item.GetId(byref(dev_id))
    if not _hresult_succeeded(hr) or not dev_id.value:
        return None

    value = dev_id.value
    _co_task_mem_free(c_void_p.from_buffer(dev_id))
    return value


def _find_device_id(enumerator: Any, data_flow: int, name: str) -> str | None:
    """按友好名查找端点；仅在唯一候选时允许兼容性的子串匹配。"""
    query = _normalize_device_name(name)
    if not query:
        return None

    coll = POINTER(IMMDeviceCollection)()
    hr = enumerator.EnumAudioEndpoints(data_flow, 1, byref(coll))
    if not _hresult_succeeded(hr) or not coll:
        return None

    count = c_uint32()
    collection = type_cast(Any, coll)
    if not _hresult_succeeded(collection.GetCount(byref(count))):
        return None

    partial_matches: list[tuple[str, str]] = []
    for i in range(count.value):
        item = POINTER(IMMDevice)()
        if not _hresult_succeeded(collection.Item(i, byref(item))) or not item:
            continue

        friendly_name = _get_friendly_name(item)
        if not friendly_name:
            continue

        normalized = _normalize_device_name(friendly_name)
        if normalized == query:
            return _take_device_id(item)
        if query in normalized:
            device_id = _take_device_id(item)
            if device_id:
                partial_matches.append((friendly_name, device_id))

    if len(partial_matches) == 1:
        return partial_matches[0][1]
    if len(partial_matches) > 1:
        logger.warning("设备名称匹配不唯一: %s → %s", name, [match[0] for match in partial_matches])
    return None


def _find_input_device_id(enumerator: Any, name: str) -> str | None:
    """EnumAudioEndpoints(eCapture=1, active=1) → 按名匹配 → GetId。"""
    return _find_device_id(enumerator, 1, name)


def _find_output_device_id(enumerator: Any, name_hint: str) -> str | None:
    """查找显式输出设备；只有未指定名称时才允许回退默认端点。"""
    if name_hint:
        return _find_device_id(enumerator, 0, name_hint)

    dev = POINTER(IMMDevice)()
    if _hresult_succeeded(enumerator.GetDefaultAudioEndpoint(0, 0, byref(dev))) and dev:
        return _take_device_id(dev)
    return None

def _get_friendly_name(imm_device: Any) -> str | None:
    """OpenPropertyStore → GetValue(PKEY_FriendlyName, pid=14)。"""
    ps = POINTER(IPropertyStore)()
    if not _hresult_succeeded(imm_device.OpenPropertyStore(STGM_READ, byref(ps))) or not ps:
        return None

    pk = PROPERTYKEY(fmtid=PKEY_FriendlyName, pid=14)
    pv = PROPVARIANT()
    property_store = type_cast(Any, ps)
    try:
        hr = property_store.GetValue(byref(pk), byref(pv))
        if _hresult_succeeded(hr) and pv.vt == VT_LPWSTR and pv.pwszVal:
            # 先复制为 Python 字符串，再由 PropVariantClear 释放原生缓冲区。
            return str(pv.pwszVal)
        return None
    finally:
        _prop_variant_clear(byref(pv))


def _read_lpwstr_property(ps: Any, pid: int) -> str | None:
    """读取字符串属性并立即释放 Windows 分配的 PROPVARIANT 内容。"""
    key = PROPERTYKEY(fmtid=PKEY_ListenTo, pid=pid)
    value = PROPVARIANT()
    try:
        hr = ps.GetValue(byref(key), byref(value))
        if not _hresult_succeeded(hr) or value.vt != VT_LPWSTR:
            return None
        return str(value.pwszVal) if value.pwszVal else None
    finally:
        _prop_variant_clear(byref(value))


def _read_bool_property(ps: Any, pid: int) -> bool | None:
    """读取 VT_BOOL 属性；类型不匹配时返回 None，避免误判为 False。"""
    key = PROPERTYKEY(fmtid=PKEY_ListenTo, pid=pid)
    value = PROPVARIANT()
    try:
        hr = ps.GetValue(byref(key), byref(value))
        if not _hresult_succeeded(hr) or value.vt != VT_BOOL:
            return None
        return value.boolVal != 0
    finally:
        _prop_variant_clear(byref(value))


def _read_listen_policy_state(imm_device: Any) -> ListenPolicyState | None:
    """重新打开只读属性仓库，读取 Windows 实际持久化的侦听状态。"""
    ps = POINTER(IPropertyStore)()
    hr = imm_device.OpenPropertyStore(STGM_READ, byref(ps))
    if not _hresult_succeeded(hr) or not ps:
        logger.warning("无法读回输入设备侦听属性: 0x%08X", int(hr) & 0xFFFFFFFF)
        return None

    property_store = type_cast(Any, ps)
    enabled = _read_bool_property(property_store, 1)
    if enabled is None:
        logger.warning("输入设备不支持预期的侦听启用属性（pid=1, VT_BOOL）")
        return None
    return ListenPolicyState(
        enabled=enabled,
        output_id=_read_lpwstr_property(property_store, 0),
    )


def _listen_policy_matches(
    state: ListenPolicyState | None,
    output_id: str | None,
    enabled: bool,
    *,
    require_output_match: bool = False,
) -> bool:
    """验证读回状态；普通禁用不要求清空 Windows 保留的历史目标。"""
    if state is None or state.enabled != enabled:
        return False
    return not (enabled or require_output_match) or state.output_id == output_id


def _write_listen_properties(
    ps: Any,
    output_id: str | None,
    enabled: bool,
    *,
    restore_output: bool = False,
) -> bool:
    """写入倾听属性并提交；restore_output 用于恢复接管前的目标端点。"""
    should_write_output = enabled or restore_output
    if should_write_output and not output_id:
        logger.warning("写入麦克风侦听时缺少目标输出设备 ID")
        return False

    # pid 0: 目标输出设备 ID（Windows 控制面板使用的 VT_LPWSTR 属性）。
    if should_write_output:
        target_key = PROPERTYKEY(fmtid=PKEY_ListenTo, pid=0)
        target_value = PROPVARIANT()
        target_value.vt = VT_LPWSTR
        target_value.pwszVal = output_id
        target_hr = ps.SetValue(byref(target_key), byref(target_value))
        if not _hresult_succeeded(target_hr):
            logger.warning("SetValue(target) failed: 0x%08X", int(target_hr) & 0xFFFFFFFF)
            return False

    # pid 1: 是否启用侦听（VT_BOOL）。禁用时不改动已保存的目标端点。
    enabled_key = PROPERTYKEY(fmtid=PKEY_ListenTo, pid=1)
    enabled_value = PROPVARIANT()
    enabled_value.vt = VT_BOOL
    enabled_value.boolVal = -1 if enabled else 0  # VARIANT_TRUE 是 16 位有符号值 -1。
    enabled_hr = ps.SetValue(byref(enabled_key), byref(enabled_value))
    if not _hresult_succeeded(enabled_hr):
        logger.warning("SetValue(enabled) failed: 0x%08X", int(enabled_hr) & 0xFFFFFFFF)
        return False

    hr_commit = ps.Commit()
    if not _hresult_succeeded(hr_commit):
        logger.warning("Commit failed: 0x%08X", int(hr_commit) & 0xFFFFFFFF)
        return False

    return True

def _set_listen(
    imm_device: Any,
    output_id: str | None,
    enabled: bool,
    *,
    restore_output: bool = False,
) -> bool:
    """提交侦听配置并读回验证，避免把静默失败报告为成功。"""
    ps = POINTER(IPropertyStore)()
    open_hr = imm_device.OpenPropertyStore(STGM_READWRITE, byref(ps))
    if not _hresult_succeeded(open_hr) or not ps:
        logger.warning(
            "无法以读写模式打开输入设备属性仓库: 0x%08X；请检查进程权限或系统策略",
            int(open_hr) & 0xFFFFFFFF,
        )
        return False

    if not _write_listen_properties(
        type_cast(Any, ps),
        output_id,
        enabled,
        restore_output=restore_output,
    ):
        return False

    state = _read_listen_policy_state(imm_device)
    if not _listen_policy_matches(
        state,
        output_id,
        enabled,
        require_output_match=restore_output,
    ):
        logger.warning(
            "侦听策略写入后验证失败: expected=(enabled=%s, output_id=%r), actual=%r",
            enabled,
            output_id,
            state,
        )
        return False

    logger.info("倾听策略已更新并验证: enabled=%s", enabled)
    return True
