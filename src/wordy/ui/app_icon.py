from PySide6.QtGui import QIcon

from wordy.paths import APP_ICON_PATH


def app_icon() -> QIcon:
    return QIcon(str(APP_ICON_PATH))
