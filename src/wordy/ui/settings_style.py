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
    TEXT_ERROR,
    TEXT_MUTED,
    TEXT_PRIMARY,
    UI_FONT,
    rgba,
)

# 全窗基准字号（px）。字体下拉框要按它折算点值字号，两处必须同一个数。
BASE_FONT_PX = 13


def build_settings_stylesheet(font_stack: str | None = None) -> str:
    """构建设置窗口的完整 QSS 样式表。font_stack 为空时用内置字体栈。"""
    # 强调色只在少数地方需要半透明版本，先换算一次，省得逐处拼字符串。
    accent_soft = rgba(GREEN_ACCENT, 40)
    accent_ring = rgba(GREEN_ACCENT, 150)
    return f'''
        QWidget {{
            font-family: {font_stack or UI_FONT};
            font-size: {BASE_FONT_PX}px;
            color: {TEXT_PRIMARY};
        }}
        QDialog {{
            /* 玻璃底、投影与圆角都由 _SettingsDialog.paintEvent 手绘：
               这里必须透明，否则不透明底会整块盖住玻璃与投影。 */
            background: transparent;
            color: {TEXT_PRIMARY};
        }}
        QFrame#dialogShell {{
            background: transparent;
            border: none;
        }}
        QWidget#settingsTitleBar {{
            background: transparent;
            border: none;
        }}
        QLabel#dialogTitle {{
            color: {TEXT_PRIMARY};
            font-size: 14px;
            font-weight: 600;
            background: transparent;
            border: none;
        }}
        /* 三个窗口按钮默认只画字形，悬停才浮起一层玻璃；关闭键悬停用错误色。 */
        QPushButton#dialogMinimizeButton,
        QPushButton#dialogMaximizeButton,
        QPushButton#dialogCloseButton {{
            min-width: 0;
            min-height: 0;
            padding: 0;
            background: transparent;
            border: none;
            border-radius: {RADIUS_CONTROL}px;
        }}
        QPushButton#dialogMinimizeButton:hover,
        QPushButton#dialogMaximizeButton:hover {{
            background: {GLASS_FILL_STRONG};
        }}
        QPushButton#dialogMinimizeButton:pressed,
        QPushButton#dialogMaximizeButton:pressed {{
            background: {GLASS_HAIRLINE};
        }}
        QPushButton#dialogCloseButton:hover {{
            background: {rgba(TEXT_ERROR, 219)};
        }}
        QPushButton#dialogCloseButton:pressed {{
            background: {rgba(TEXT_ERROR, 180)};
        }}
        QLabel#pageTitle {{
            color: {TEXT_PRIMARY};
            font-size: 24px;
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
        QLabel#fieldLabel {{
            color: {TEXT_MUTED};
            min-width: 72px;
            font-size: 12px;
            border: none;
        }}
        QLabel#voiceIdValue {{
            color: {TEXT_MUTED};
            font-size: 12px;
            border: none;
            padding: 1px 2px;
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
            /* 透明底，只留一条分隔细线，不再整块盖住玻璃。 */
            background: transparent;
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
            left: 36px;
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
            /* 分栏靠轻分隔线区分，避免右栏的外框压过服务商选择。 */
            background: transparent;
            border: none;
            border-left: 1px solid {GLASS_HAIRLINE};
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
        QPushButton#dangerButton {{
            background: transparent;
            color: {TEXT_MUTED};
        }}
        QPushButton#dangerButton:hover {{
            background: {rgba(TEXT_ERROR, 35)};
            border-color: {rgba(TEXT_ERROR, 100)};
            color: {TEXT_ERROR};
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
