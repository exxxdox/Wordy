"""通过 Windows 内核对象拒绝同一会话内的 Wordy 重复启动。"""

import ctypes
from ctypes import wintypes

_MUTEX_NAME = r"Local\Wordy.SingleInstance"
_instance_handle: int | None = None


def show_native_message(text: str, title: str = "Wordy") -> None:
    """启动阶段的可见反馈：此时还没有 QApplication，只能用原生弹窗。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    # MB_OK | MB_ICONINFORMATION | MB_SETFOREGROUND，确保提示在前台可见。
    user32.MessageBoxW(None, text, title, 0x00010040)


def show_duplicate_instance_message() -> None:
    # 启动拦截发生在 QApplication 创建前，使用原生弹窗避免初始化第二套 UI。
    show_native_message("Wordy 已在运行，请勿重复启动。")


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
