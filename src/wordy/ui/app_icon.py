from pathlib import Path

from PySide6.QtGui import QIcon


def app_icon() -> QIcon:
    # 源码与打包版都从包内加载，和 EXE 嵌入的图标共用一份资源。
    return QIcon(str(Path(__file__).with_name("icons") / "wordy.ico"))
