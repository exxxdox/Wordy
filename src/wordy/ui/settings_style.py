#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口 QSS 样式表。"""

from __future__ import annotations

from wordy.ui.theme import (
    ACCENT_HOVER,
    ACCENT_PRESSED,
    BUTTON_ACTIVE_BG,
    BUTTON_BG,
    ELEVATED_BG,
    GREEN_ACCENT,
    UI_FONT,
    SCROLLBAR_HANDLE,
    SCROLLBAR_HANDLE_HOVER,
    SEPARATOR_COLOR,
    SURFACE_BG,
    TEXT_MUTED,
    TEXT_PRIMARY,
    WINDOW_BG,
)


def build_settings_stylesheet() -> str:
    """构建设置窗口的完整 QSS 样式表。"""
    # 常态用色面和间距形成层次；仅输入焦点使用底部提示线，保留键盘可见反馈。
    return f'''
        QWidget {{
            font-family: {UI_FONT};
            font-size: 13px;
        }}
        QDialog {{
            background: transparent;
            color: {TEXT_PRIMARY};
        }}
        QFrame#dialogShell {{
            background: {SURFACE_BG};
            border: none;
            border-radius: 16px;
        }}
        QLabel#dialogTitle {{
            color: {TEXT_PRIMARY};
            font-size: 18px;
            font-weight: 700;
            padding: 0;
            border: none;
        }}
        QLabel#sectionTitle {{
            color: {TEXT_PRIMARY};
            font-size: 14px;
            font-weight: 700;
            border: none;
            padding: 0 0 3px 0;
        }}
        QLabel#bodyLabel, QLabel#bodyLabelEmphasis {{
            font-size: 13px;
            border: none;
        }}
        QLabel#bodyLabelEmphasis {{
            font-size: 13px;
            font-weight: 600;
        }}
        QLabel#hintLabel {{
            font-size: 12px;
            border: none;
        }}
        QScrollArea, QScrollArea > QWidget > QWidget {{
            background: transparent;
            border: none;
        }}
        QTabWidget#settingsTabs {{
            background: transparent;
            border: none;
            padding: 0 0 4px 0;
        }}
        QTabWidget#settingsTabs::pane {{
            background: transparent;
            border: none;
            margin-top: 10px;
        }}
        QTabWidget#settingsTabs::tab-bar {{
            alignment: center;
        }}
        QTabWidget#settingsTabs QTabBar {{
            background: transparent;
            border: none;
            border-radius: 10px;
            padding: 3px;
        }}
        QTabWidget#settingsTabs QTabBar::tab {{
            background: transparent;
            color: {TEXT_MUTED};
            border: none;
            border-radius: 7px;
            padding: 9px 14px;
            margin: 0 1px;
            min-width: 90px;
            font-weight: 600;
            font-size: 13px;
        }}
        QTabWidget#settingsTabs QTabBar::tab:hover {{
            background: {ELEVATED_BG};
            color: {TEXT_PRIMARY};
        }}
        QTabWidget#settingsTabs QTabBar::tab:pressed {{
            background: {ACCENT_PRESSED};
            color: {TEXT_PRIMARY};
        }}
        QTabWidget#settingsTabs QTabBar::tab:selected {{
            background: {BUTTON_ACTIVE_BG};
            color: {GREEN_ACCENT};
        }}
        QTabWidget#settingsTabs QTabBar::tab:selected:hover {{
            background: {BUTTON_ACTIVE_BG};
            color: {ACCENT_HOVER};
        }}
        QTabWidget#settingsTabs QTabBar::tab:focus {{
            outline: none;
            color: {ACCENT_HOVER};
            background: {BUTTON_ACTIVE_BG};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 8px;
            margin: 8px 3px 8px 0;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {SCROLLBAR_HANDLE};
            min-height: 30px;
            border-radius: 3px;
        }}
        QScrollBar::handle:vertical:hover {{
            background: {SCROLLBAR_HANDLE_HOVER};
        }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
            background: transparent;
            border: none;
            height: 0;
        }}
        QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
            background: transparent;
            border: none;
        }}
        QFrame#separator {{
            background: {SEPARATOR_COLOR};
            border: none;
        }}
        QFrame#ttsProviderContainer {{
            border: none;
            background: transparent;
            border-radius: 8px;
        }}
        QPushButton {{
            background: {BUTTON_BG};
            color: {TEXT_PRIMARY};
            border: none;
            border-radius: 8px;
            padding: 7px 16px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {BUTTON_ACTIVE_BG};
            color: {TEXT_PRIMARY};
        }}
        QPushButton:pressed {{
            background: {ELEVATED_BG};
        }}
        QPushButton:focus {{
            background: {BUTTON_ACTIVE_BG};
            color: {GREEN_ACCENT};
        }}
        QPushButton#dialogCloseButton {{
            background: transparent;
            color: {TEXT_MUTED};
            border: none;
            border-radius: 9px;
            padding: 0;
            font-size: 23px;
            font-weight: 400;
        }}
        QPushButton#dialogCloseButton:hover, QPushButton#dialogCloseButton:focus {{
            background: {BUTTON_ACTIVE_BG};
            color: {GREEN_ACCENT};
        }}
        QPushButton#dialogCloseButton:pressed {{
            background: {ELEVATED_BG};
        }}
        QPushButton:disabled {{
            background: {SEPARATOR_COLOR};
            color: {TEXT_MUTED};
        }}
        QPushButton#applyButton {{
            background: {GREEN_ACCENT};
            color: {WINDOW_BG};
        }}
        QPushButton#applyButton:hover {{
            background: {ACCENT_HOVER};
            color: {WINDOW_BG};
        }}
        QPushButton#applyButton:pressed {{
            background: {ACCENT_PRESSED};
            color: {TEXT_PRIMARY};
        }}
        QPushButton#cancelButton {{
            background: transparent;
            color: {TEXT_MUTED};
        }}
        QPushButton#cancelButton:hover {{
            background: {ELEVATED_BG};
            color: {TEXT_PRIMARY};
        }}
        QPushButton#cancelButton:pressed {{
            background: {SEPARATOR_COLOR};
            color: {TEXT_PRIMARY};
        }}
        QComboBox {{
            background: {ELEVATED_BG};
            color: {TEXT_PRIMARY};
            border: none;
            border-radius: 8px;
            padding: 7px 28px 7px 10px;
            selection-background-color: {BUTTON_ACTIVE_BG};
        }}
        QComboBox:hover {{
            background: {BUTTON_ACTIVE_BG};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: top right;
            width: 24px;
            border: none;
        }}
        QComboBox::down-arrow {{
            width: 0;
            height: 0;
            border-left: 4px solid transparent;
            border-right: 4px solid transparent;
            border-top: 5px solid {TEXT_MUTED};
        }}
        QComboBox QAbstractItemView {{
            background: {ELEVATED_BG};
            color: {TEXT_PRIMARY};
            border: none;
            border-radius: 8px;
            padding: 4px;
            outline: none;
            selection-background-color: {BUTTON_ACTIVE_BG};
            selection-color: {TEXT_PRIMARY};
        }}
        QLineEdit {{
            background: {ELEVATED_BG};
            color: {TEXT_PRIMARY};
            border: none;
            border-radius: 8px;
            padding: 7px 10px;
            selection-background-color: {BUTTON_ACTIVE_BG};
            selection-color: {TEXT_PRIMARY};
        }}
        QLineEdit:hover {{
            background: {BUTTON_ACTIVE_BG};
        }}
        QLineEdit:focus, QComboBox:focus {{
            background: {BUTTON_ACTIVE_BG};
            border-bottom: 2px solid {GREEN_ACCENT};
        }}
        QSlider::groove:horizontal {{
            height: 4px;
            background: {SEPARATOR_COLOR};
            border-radius: 2px;
        }}
        QSlider::sub-page:horizontal {{
            background: {GREEN_ACCENT};
            border-radius: 2px;
        }}
        QSlider::handle:horizontal {{
            width: 14px;
            height: 14px;
            margin: -5px 0;
            border-radius: 7px;
            background: {TEXT_PRIMARY};
            border: none;
        }}
        QSlider::handle:horizontal:hover {{
            background: {ACCENT_HOVER};
        }}
        QCheckBox {{
            color: {TEXT_PRIMARY};
            spacing: 8px;
            border: none;
        }}
    '''
