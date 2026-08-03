#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口 QSS 样式表。"""

from __future__ import annotations

from easy_tts.ui.theme import (
    ACCENT_HOVER,
    ACCENT_PRESSED,
    BUTTON_ACTIVE_BG,
    BUTTON_BG,
    BUTTON_GHOST_BORDER,
    ELEVATED_BG,
    GREEN_ACCENT,
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
    return f'''
        QDialog {{
            background: transparent;
            color: {TEXT_PRIMARY};
        }}
        QFrame#dialogShell {{
            background: {SURFACE_BG};
            border: 1px solid {BUTTON_GHOST_BORDER};
            border-radius: 18px;
        }}
        QLabel#dialogTitle {{
            color: {TEXT_PRIMARY};
            font-size: 16px;
            font-weight: 700;
            padding: 18px 0 12px 0;
            border: none;
        }}
        QLabel#sectionTitle {{
            color: {TEXT_PRIMARY};
            font-size: 12px;
            font-weight: 700;
            border: none;
        }}
        QLabel#bodyLabel, QLabel#bodyLabelEmphasis {{
            font-size: 10px;
            border: none;
        }}
        QLabel#bodyLabelEmphasis {{
            font-size: 11px;
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
            background: {SURFACE_BG};
            border: 1px solid {BUTTON_GHOST_BORDER};
            border-radius: 12px;
            padding: 4px;
        }}
        QTabWidget#settingsTabs QTabBar::tab {{
            background: transparent;
            color: {TEXT_MUTED};
            border: 1px solid transparent;
            border-radius: 9px;
            padding: 8px 12px;
            margin: 0 2px;
            min-width: 90px;
            font-weight: 600;
        }}
        QTabWidget#settingsTabs QTabBar::tab:hover {{
            background: {ELEVATED_BG};
            color: {TEXT_PRIMARY};
            border-color: {BUTTON_GHOST_BORDER};
        }}
        QTabWidget#settingsTabs QTabBar::tab:pressed {{
            background: {ACCENT_PRESSED};
            color: {TEXT_PRIMARY};
            border-color: {ACCENT_PRESSED};
        }}
        QTabWidget#settingsTabs QTabBar::tab:selected {{
            background: {BUTTON_ACTIVE_BG};
            color: {TEXT_PRIMARY};
            border-color: {GREEN_ACCENT};
        }}
        QTabWidget#settingsTabs QTabBar::tab:selected:hover {{
            background: {BUTTON_ACTIVE_BG};
            border-color: {ACCENT_HOVER};
        }}
        QTabWidget#settingsTabs QTabBar::tab:focus {{
            outline: none;
            border-color: {ACCENT_HOVER};
        }}
        QScrollBar:vertical {{
            background: transparent;
            width: 10px;
            margin: 8px 3px 8px 0;
            border: none;
        }}
        QScrollBar::handle:vertical {{
            background: {SCROLLBAR_HANDLE};
            min-height: 30px;
            border-radius: 4px;
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
        QPushButton {{
            background: {BUTTON_BG};
            color: {TEXT_PRIMARY};
            border: 1px solid {BUTTON_GHOST_BORDER};
            border-radius: 10px;
            padding: 7px 16px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {BUTTON_ACTIVE_BG};
            border-color: {ACCENT_HOVER};
            color: {TEXT_PRIMARY};
        }}
        QPushButton:pressed {{
            background: {ELEVATED_BG};
            border-color: {ACCENT_PRESSED};
        }}
        QPushButton:disabled {{
            background: {SEPARATOR_COLOR};
            color: {TEXT_MUTED};
            border-color: {SEPARATOR_COLOR};
        }}
        QPushButton#applyButton {{
            background: {GREEN_ACCENT};
            color: {WINDOW_BG};
            border-color: {GREEN_ACCENT};
        }}
        QPushButton#applyButton:hover {{
            background: {ACCENT_HOVER};
            border-color: {ACCENT_HOVER};
            color: {WINDOW_BG};
        }}
        QPushButton#applyButton:pressed {{
            background: {ACCENT_PRESSED};
            border-color: {ACCENT_PRESSED};
            color: {TEXT_PRIMARY};
        }}
        QPushButton#cancelButton {{
            background: transparent;
            color: {TEXT_MUTED};
            border-color: {BUTTON_GHOST_BORDER};
        }}
        QPushButton#cancelButton:hover {{
            background: {ELEVATED_BG};
            color: {TEXT_PRIMARY};
            border-color: {SCROLLBAR_HANDLE_HOVER};
        }}
        QPushButton#cancelButton:pressed {{
            background: {SEPARATOR_COLOR};
            color: {TEXT_PRIMARY};
        }}
        QComboBox {{
            background: {ELEVATED_BG};
            color: {TEXT_PRIMARY};
            border: 1px solid {BUTTON_GHOST_BORDER};
            border-radius: 10px;
            padding: 7px 28px 7px 10px;
            selection-background-color: {BUTTON_ACTIVE_BG};
        }}
        QComboBox:hover {{
            border-color: {ACCENT_HOVER};
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
            border: 1px solid {BUTTON_GHOST_BORDER};
            border-radius: 10px;
            padding: 4px;
            outline: none;
            selection-background-color: {BUTTON_ACTIVE_BG};
            selection-color: {TEXT_PRIMARY};
        }}
        QLineEdit {{
            background: {ELEVATED_BG};
            color: {TEXT_PRIMARY};
            border: 1px solid {BUTTON_GHOST_BORDER};
            border-radius: 10px;
            padding: 7px 10px;
            selection-background-color: {BUTTON_ACTIVE_BG};
            selection-color: {TEXT_PRIMARY};
        }}
        QLineEdit:hover {{
            border-color: {ACCENT_HOVER};
        }}
        QLineEdit:focus {{
            border-color: {GREEN_ACCENT};
        }}
        QSlider::groove:horizontal {{
            height: 6px;
            background: {SEPARATOR_COLOR};
            border-radius: 3px;
        }}
        QSlider::sub-page:horizontal {{
            background: {GREEN_ACCENT};
            border-radius: 3px;
        }}
        QSlider::handle:horizontal {{
            width: 16px;
            height: 16px;
            margin: -5px 0;
            border-radius: 8px;
            background: {TEXT_PRIMARY};
            border: 2px solid {GREEN_ACCENT};
        }}
        QSlider::handle:horizontal:hover {{
            background: {ACCENT_HOVER};
            border-color: {ACCENT_HOVER};
        }}
        QCheckBox {{
            color: {TEXT_PRIMARY};
            spacing: 8px;
            border: none;
        }}
    '''
