"""Windows 通知中心使用独立的应用标识渲染发送者图标。"""

import json
import os
from pathlib import Path
import subprocess
from threading import Lock
import winreg
from xml.etree import ElementTree

from wordy.paths import APP_ICON_PATH, config_directory

APP_ID = "Wordy.Desktop"
_registration_lock = Lock()

_SHOW_TOAST = """
$ErrorActionPreference = 'Stop'
$payload = [Console]::In.ReadToEnd() | ConvertFrom-Json
$xml = [Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom, ContentType=WindowsRuntime]::new()
$xml.LoadXml($payload.xml)
$toast = [Windows.UI.Notifications.ToastNotification, Windows.UI.Notifications, ContentType=WindowsRuntime]::new($xml)
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime]::CreateToastNotifier($payload.app_id).Show($toast)
"""


def register_notification_sender() -> str:
    # Windows 在进程退出后仍可读取发送者图标，不能指向单文件 EXE 的临时解包目录。
    icon_path = config_directory() / "notification-icon.ico"
    icon = APP_ICON_PATH.read_bytes()
    # 连续通知可能同时到达，避免并发替换同一个图标文件。
    with _registration_lock:
        icon_path.parent.mkdir(parents=True, exist_ok=True)
        if not icon_path.exists() or icon_path.read_bytes() != icon:
            temporary = icon_path.with_suffix(".tmp")
            temporary.write_bytes(icon)
            temporary.replace(icon_path)
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\AppUserModelId\{APP_ID}") as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "Wordy")
            winreg.SetValueEx(key, "IconUri", 0, winreg.REG_SZ, str(icon_path))
    return APP_ID


def show_notification(title: str, message: str) -> None:
    app_id = register_notification_sender()
    toast = ElementTree.Element("toast")
    visual = ElementTree.SubElement(toast, "visual")
    binding = ElementTree.SubElement(visual, "binding", template="ToastGeneric")
    for text in (title, message):
        ElementTree.SubElement(binding, "text").text = text
    ElementTree.SubElement(toast, "audio", silent="true")
    # 消息只通过 stdin 传递，避免把标题或内容当作 PowerShell 代码。
    payload = json.dumps({"app_id": app_id, "xml": ElementTree.tostring(toast, encoding="unicode")})
    powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    subprocess.run(
        [str(powershell), "-NoProfile", "-NonInteractive", "-Command", _SHOW_TOAST],
        input=payload, encoding="ascii", stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW, timeout=5, check=True,
    )
