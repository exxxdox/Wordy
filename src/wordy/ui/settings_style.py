#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""设置窗口 QSS 样式表。

Apple 式玻璃：层次靠半透明白叠层 + 1px 细高光描边 + 圆角阶梯表达，而不是大面积
实色卡片和加粗底边。焦点态用描边提亮而不是底边加粗，控件高度因此不会跳变。
"""

from __future__ import annotations

from wordy.ui.theme import (
    ACCENT_HOVER,
    ACCENT_PRESSED,
    ELEVATED_BG,
    GLASS_FILL,
    GLASS_FILL_STRONG,
    GLASS_HAIRLINE,
    GLASS_HAIRLINE_STRONG,
    GLASS_SHEEN,
    GREEN_ACCENT,
    RADIUS_CARD,
    RADIUS_CHIP,
    RADIUS_CONTROL,
    SCROLLBAR_HANDLE,
    SCROLLBAR_HANDLE_HOVER,
    SURFACE_BG,
    TEXT_MUTED,
    TEXT_PRIMARY,
    UI_FONT,
    WINDOW_BG,
    rgba,
)


def build_settings_stylesheet() -> str:
    """构建设置窗口的完整 QSS 样式表。"""
    # 强调色只在少数地方需要半透明版本，先换算一次，省得逐处拼字符串。
    accent_soft = rgba(GREEN_ACCENT, 40)
    accent_ring = rgba(GREEN_ACCENT, 150)
    return f'''
        QWidget {{
            font-family: {UI_FONT};
            font-size: 13px;
        }}
        QDialog {{
            /* 顶亮底暗的极浅渐变：窗口有玻璃的受光方向，而不是一整块死色。 */
            background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                stop:0 {SURFACE_BG}, stop:1 {WINDOW_BG});
            color: {TEXT_PRIMARY};
        }}
        QFrame#dialogShell {{
            background: transparent;
            border: none;
        }}
        QLabel#pageTitle {{
            color: {TEXT_PRIMARY};
            font-size: 18px;
            font-weight: 600;
            border: none;
        }}
        QLabel#pageDescription {{
            color: {TEXT_MUTED};
            font-size: 12px;
            border: none;
        }}
        QLabel#sectionTitle {{
            color: {TEXT_PRIMARY};
            font-size: 13px;
            font-weight: 600;
            border: none;
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
        QLabel#settingValue {{
            background: {accent_soft};
            color: {GREEN_ACCENT};
            font-size: 13px;
            font-weight: 600;
            border: none;
            border-radius: {RADIUS_CHIP}px;
            padding: 2px 8px;
        }}
        QWidget#settingsFooter {{
            background: {WINDOW_BG};
            border: none;
            border-top: 1px solid {GLASS_HAIRLINE};
        }}
        QLabel#autoSaveLabel {{
            color: {TEXT_MUTED};
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
            margin-top: 0;
        }}
        QTabWidget#settingsTabs::tab-bar {{
            alignment: left;
            left: 20px;
        }}
        QTabWidget#settingsTabs QTabBar {{
            background: transparent;
            border: none;
            padding: 0;
        }}
        /* 分段控件：选中态是玻璃胶囊，而不是底边加粗的标签下划线。 */
        QTabWidget#settingsTabs QTabBar::tab {{
            background: transparent;
            color: {TEXT_MUTED};
            border: none;
            border-radius: {RADIUS_CONTROL}px;
            padding: 6px 14px;
            margin: 0 6px 0 0;
            font-weight: 600;
            font-size: 13px;
        }}
        QTabWidget#settingsTabs QTabBar::tab:hover {{
            background: {GLASS_FILL};
            color: {TEXT_PRIMARY};
        }}
        QTabWidget#settingsTabs QTabBar::tab:pressed {{
            background: {GLASS_FILL_STRONG};
            color: {TEXT_PRIMARY};
        }}
        QTabWidget#settingsTabs QTabBar::tab:selected {{
            background: {GLASS_FILL_STRONG};
            color: {TEXT_PRIMARY};
        }}
        QTabWidget#settingsTabs QTabBar::tab:selected:hover {{
            background: {GLASS_HAIRLINE};
            color: {TEXT_PRIMARY};
        }}
        QTabWidget#settingsTabs QTabBar::tab:focus {{
            outline: none;
            color: {ACCENT_HOVER};
            background: {GLASS_FILL_STRONG};
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
            background: {GLASS_HAIRLINE};
            border: none;
        }}
        QFrame#ttsProviderContainer {{
            background: {GLASS_SHEEN};
            border: 1px solid {GLASS_HAIRLINE};
            border-radius: {RADIUS_CARD}px;
        }}
        QPushButton {{
            min-height: 20px;
            background: {GLASS_FILL};
            color: {TEXT_PRIMARY};
            border: 1px solid {GLASS_HAIRLINE};
            border-radius: {RADIUS_CONTROL}px;
            padding: 6px 14px;
            font-weight: 600;
        }}
        QPushButton:hover {{
            background: {GLASS_FILL_STRONG};
            border: 1px solid {GLASS_HAIRLINE_STRONG};
            color: {TEXT_PRIMARY};
        }}
        QPushButton:pressed {{
            background: {rgba(ELEVATED_BG, 235)};
            border: 1px solid {GLASS_HAIRLINE};
        }}
        QPushButton:focus {{
            background: {GLASS_FILL_STRONG};
            border: 1px solid {accent_ring};
            color: {TEXT_PRIMARY};
        }}
        QPushButton:disabled {{
            background: {GLASS_SHEEN};
            border: 1px solid transparent;
            color: {TEXT_MUTED};
        }}
        /* 退出按钮是最次要的动作：默认没有底色，悬停时才浮起一层玻璃。 */
        QPushButton#cancelButton {{
            background: transparent;
            border: 1px solid transparent;
            color: {TEXT_MUTED};
        }}
        QPushButton#cancelButton:hover {{
            background: {GLASS_FILL};
            border: 1px solid {GLASS_HAIRLINE};
            color: {TEXT_PRIMARY};
        }}
        QPushButton#cancelButton:focus {{
            background: {GLASS_FILL};
            border: 1px solid {accent_ring};
            color: {TEXT_PRIMARY};
        }}
        QPushButton#cancelButton:pressed {{
            background: {GLASS_FILL_STRONG};
            color: {TEXT_PRIMARY};
        }}
        QComboBox {{
            min-height: 20px;
            background: {GLASS_FILL};
            color: {TEXT_PRIMARY};
            border: 1px solid {GLASS_HAIRLINE};
            border-radius: {RADIUS_CONTROL}px;
            padding: 5px 28px 5px 10px;
            selection-background-color: {accent_soft};
        }}
        QComboBox:hover {{
            background: {GLASS_FILL_STRONG};
            border: 1px solid {GLASS_HAIRLINE_STRONG};
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
            border: 1px solid {GLASS_HAIRLINE};
            border-radius: {RADIUS_CARD}px;
            padding: 4px;
            outline: none;
            selection-background-color: {accent_soft};
            selection-color: {TEXT_PRIMARY};
        }}
        QComboBox QAbstractItemView::item {{
            padding: 5px 8px;
        }}
        QLineEdit {{
            min-height: 20px;
            background: {GLASS_FILL};
            color: {TEXT_PRIMARY};
            border: 1px solid {GLASS_HAIRLINE};
            border-radius: {RADIUS_CONTROL}px;
            padding: 5px 10px;
            selection-background-color: {accent_soft};
            selection-color: {TEXT_PRIMARY};
        }}
        QLineEdit:hover {{
            background: {GLASS_FILL_STRONG};
        }}
        /* 焦点靠描边提亮表达，控件高度不随焦点变化。 */
        QLineEdit:focus, QComboBox:focus {{
            background: {GLASS_FILL_STRONG};
            border: 1px solid {accent_ring};
        }}
        QSlider::groove:horizontal {{
            height: 4px;
            background: {GLASS_HAIRLINE};
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
        QSlider::handle:horizontal:pressed {{
            background: {ACCENT_PRESSED};
        }}
        QSlider::handle:horizontal:focus {{
            background: {GREEN_ACCENT};
        }}
        QCheckBox {{
            color: {TEXT_PRIMARY};
            spacing: 8px;
            border: none;
        }}
    '''
