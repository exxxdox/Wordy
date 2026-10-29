"""通过 Windows 内核对象拒绝同一会话内的 Wordy 重复启动。"""

import ctypes
from ctypes import wintypes

_MUTEX_NAME = r"Local\Wordy.SingleInstance"
_instance_handle: int | None = None


def acquire_single_instance() -> bool:
    """首实例保留句柄到进程结束，系统同时负责异常退出后的回收。"""
    global _instance_handle
    if _instance_handle is not None:
        return True

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    handle = kernel32.CreateMutexW(None, False, _MUTEX_NAME)
    error = ctypes.get_last_error()
    if not handle:
        raise ctypes.WinError(error)
    if error == 183:  # ERROR_ALREADY_EXISTS：只关闭新进程持有的句柄。
        kernel32.CloseHandle(handle)
        return False
    # 不在 main() 返回时释放：日志 flush 和音频清理完成前仍须阻止新实例。
    _instance_handle = handle
    return True
