"""通过 Windows 内核对象拒绝同一会话内的 Wordy 重复启动。"""

import ctypes
from ctypes import wintypes

from wordy.paths import APP_ICON_PATH

_MUTEX_NAME = r"Local\Wordy.SingleInstance"
_instance_handle: int | None = None


class _TaskDialogConfig(ctypes.Structure):
    # TASKDIALOGCONFIG 在 Win32 头文件中按 1 字节对齐。
    _pack_ = 1
    _fields_ = [
        ("size", wintypes.UINT),
        ("parent", wintypes.HWND),
        ("instance", wintypes.HINSTANCE),
        ("flags", wintypes.UINT),
        ("common_buttons", wintypes.UINT),
        ("title", wintypes.LPCWSTR),
        ("icon", wintypes.HICON),
        ("main_instruction", wintypes.LPCWSTR),
        ("text", wintypes.LPCWSTR),
        ("button_count", wintypes.UINT),
        ("buttons", ctypes.c_void_p),
        ("default_button", ctypes.c_int),
        ("radio_button_count", wintypes.UINT),
        ("radio_buttons", ctypes.c_void_p),
        ("default_radio_button", ctypes.c_int),
        ("verification_text", wintypes.LPCWSTR),
        ("expanded_text", wintypes.LPCWSTR),
        ("expanded_label", wintypes.LPCWSTR),
        ("collapsed_label", wintypes.LPCWSTR),
        ("footer_icon", wintypes.HICON),
        ("footer_text", wintypes.LPCWSTR),
        ("callback", ctypes.c_void_p),
        ("callback_data", wintypes.LPARAM),
        ("width", wintypes.UINT),
    ]


def show_native_message(text: str, title: str = "Wordy") -> None:
    """在 QApplication 初始化前显示带应用图标的原生弹窗。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    try:
        task_dialog = ctypes.WinDLL("comctl32").TaskDialogIndirect
    except (AttributeError, OSError):
        task_dialog = None

    if task_dialog is not None:
        task_dialog.argtypes = [ctypes.POINTER(_TaskDialogConfig), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
        # 读取原始 HRESULT，让失败时仍能走下方的原生提示。
        task_dialog.restype = wintypes.LONG
        user32.LoadImageW.argtypes = [
            wintypes.HINSTANCE, wintypes.LPCWSTR, wintypes.UINT, ctypes.c_int, ctypes.c_int, wintypes.UINT,
        ]
        user32.LoadImageW.restype = wintypes.HICON
        user32.DestroyIcon.argtypes = [wintypes.HICON]
        user32.DestroyIcon.restype = wintypes.BOOL
        # IMAGE_ICON | LR_LOADFROMFILE，源码和单文件包都能直接从 ICO 取图。
        icon = user32.LoadImageW(None, str(APP_ICON_PATH), 1, 32, 32, 0x0010)
        if icon:
            try:
                config = _TaskDialogConfig(
                    size=ctypes.sizeof(_TaskDialogConfig),
                    flags=0x0002 | 0x0008,  # TDF_USE_HICON_MAIN | TDF_ALLOW_DIALOG_CANCELLATION
                    common_buttons=0x0001,  # TDCBF_OK_BUTTON
                    title=title,
                    icon=icon,
                    text=text,
                )
                if task_dialog(ctypes.byref(config), None, None, None) >= 0:
                    return
            finally:
                user32.DestroyIcon(icon)

    # 图标缺失或任务对话框不可用时，仍要把启动失败原因显示出来。
    user32.MessageBoxW.argtypes = [wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.UINT]
    user32.MessageBoxW.restype = ctypes.c_int
    user32.MessageBoxW(None, text, title, 0x00010000)  # MB_OK | MB_SETFOREGROUND


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
