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
    WINDOW_BG,
    rgba,
)

# 全窗基准字号（px）。字体下拉框要按它折算点值字号，两处必须同一个数。
BASE_FONT_PX = 13
# 弹窗和设置页共用控件节奏，页边距同时用于导航和正文对齐。
CONTENT_INSET = 36
INLINE_GAP = 8


def build_settings_stylesheet(font_stack: str | None = None) -> str:
    """构建设置窗口的完整 QSS 样式表。font_stack 为空时用内置字体栈。"""
    # 强调色只在少数地方需要半透明版本，先换算一次，省得逐处拼字符串。
    accent_soft = rgba(GREEN_ACCENT, 44)
    accent_ring = rgba(ACCENT_HOVER, 205)
    glass_button = (
        f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
        f"stop:0 {GLASS_FILL_STRONG}, stop:1 {GLASS_FILL})"
    )
    glass_selected = (
        f"qlineargradient(x1:0, y1:0, x2:1, y2:1, "
        f"stop:0 {rgba(ACCENT_HOVER, 72)}, stop:1 {accent_soft})"
    )
    # Qt 在自定义子控件后可能不绘制原生箭头，用锥形渐变填出上下三角。
    arrow = (
        f"qconicalgradient(cx:0.5, cy:0, angle:{{angle}}, stop:0 transparent, "
        f"stop:0.624 transparent, stop:0.625 {TEXT_MUTED}, "
        f"stop:0.875 {TEXT_MUTED}, stop:0.876 transparent, stop:1 transparent)"
    )
    up_arrow = arrow.format(angle=0)
    down_arrow = arrow.replace("cy:0", "cy:1").format(angle=180)
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
            font-weight: 700;
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
            font-size: 30px;
            font-weight: 700;
            border: none;
        }}
        QLabel#pageDescription {{
            color: {TEXT_MUTED};
            font-size: 13px;
            border: none;
        }}
        QLabel#sectionTitle {{
            color: {TEXT_PRIMARY};
            font-size: 13px;
            font-weight: 600;
            border: none;
        }}
        QLabel#columnTitle {{
            color: {TEXT_PRIMARY};
            font-size: 16px;
            font-weight: 600;
            background: transparent;
            border: none;
            border-bottom: 1px solid {rgba(ACCENT_HOVER, 76)};
            padding-bottom: 12px;
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
            color: #b9dcff;
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
            left: {CONTENT_INSET}px;
        }}
        QTabWidget#settingsTabs QTabBar {{
            background: transparent;
            border: none;
            padding: 0;
        }}
        /* 页签与内容类型共用蓝色玻璃选中态。 */
        QTabWidget#settingsTabs QTabBar::tab {{
            background: transparent;
            color: {TEXT_MUTED};
            border: 1px solid transparent;
            border-radius: {RADIUS_CONTROL}px;
            padding: 7px 16px;
            margin: 0 4px 0 0;
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
            background: {glass_selected};
            border-color: {rgba(ACCENT_HOVER, 112)};
            color: #dceeff;
        }}
        QTabWidget#settingsTabs QTabBar::tab:selected:hover {{
            border-color: {accent_ring};
        }}
        QTabWidget#settingsTabs QTabBar::tab:selected:focus {{
            outline: none;
            border-color: {accent_ring};
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
        QFrame#wheelEditor {{
            background: {glass_button};
            border: 1px solid {GLASS_HAIRLINE};
            border-radius: {RADIUS_CARD}px;
        }}
        QLabel#wheelEditorTitle {{
            font-size: 17px;
            font-weight: 700;
        }}
        QPushButton {{
            min-height: 20px;
            background: {glass_button};
            color: {TEXT_PRIMARY};
            border: 1px solid {GLASS_HAIRLINE};
            border-radius: {RADIUS_CONTROL}px;
            padding: 5px 12px;
            font-weight: 600;
        }}
        QFrame#wheelEditor QPushButton {{
            /* 录制时同一行会额外出现取消键，保留最小窗口的右侧留白。 */
            padding-left: 7px;
            padding-right: 7px;
        }}
        QPushButton:hover {{
            background: {GLASS_FILL_STRONG};
            border: 1px solid {GLASS_HAIRLINE_STRONG};
            color: {TEXT_PRIMARY};
        }}
        QPushButton:focus {{
            border-color: {accent_ring};
        }}
        /* 槽内容类型用成对按钮表达：选中项用强调描边，避免和普通动作按钮混淆。 */
        QPushButton#slotModeButton:checked {{
            background: {glass_selected};
            border-color: {rgba(ACCENT_HOVER, 112)};
            color: #dceeff;
        }}
        QPushButton#slotModeButton:checked:focus {{
            border-color: {accent_ring};
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
        QPushButton#dangerButton:pressed {{
            background: {rgba(TEXT_ERROR, 65)};
            color: {TEXT_PRIMARY};
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
        QPushButton:pressed, QPushButton#slotModeButton:pressed {{
            background: {rgba(WINDOW_BG, 154)};
        }}
        QPushButton:disabled, QPushButton#dangerButton:disabled,
        QPushButton#slotModeButton:disabled, QPushButton#cancelButton:disabled {{
            background: {GLASS_SHEEN};
            border-color: {GLASS_FILL};
            color: {TEXT_MUTED};
        }}
        /* 快捷键输入由内部的 QLineEdit 绘制，因此也走同一组输入框规则。 */
        QLineEdit, QComboBox, QSpinBox {{
            min-height: 20px;
            background: {GLASS_FILL};
            color: {TEXT_PRIMARY};
            border: 1px solid {GLASS_HAIRLINE};
            border-radius: {RADIUS_CONTROL}px;
            padding: 5px 10px;
            selection-background-color: {accent_soft};
            selection-color: {TEXT_PRIMARY};
            placeholder-text-color: {TEXT_MUTED};
        }}
        QComboBox {{
            padding-right: 30px;
        }}
        QSpinBox {{
            padding-right: 22px;
            min-width: 56px;
        }}
        QComboBox QLineEdit {{
            min-height: 0;
            background: transparent;
            border: none;
            padding: 0;
        }}
        QLineEdit:hover, QComboBox:hover, QSpinBox:hover {{
            background: {GLASS_FILL_STRONG};
            border-color: {GLASS_HAIRLINE_STRONG};
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: center right;
            width: 24px;
            border: none;
        }}
        QSpinBox::up-button, QSpinBox::down-button {{
            subcontrol-origin: border;
            width: 22px;
            border: none;
        }}
        QSpinBox::up-button {{ subcontrol-position: top right; }}
        QSpinBox::down-button {{ subcontrol-position: bottom right; }}
        QSpinBox::up-button:hover, QSpinBox::down-button:hover {{
            background: {GLASS_FILL_STRONG};
            border-radius: 4px;
        }}
        QComboBox::down-arrow, QSpinBox::down-arrow {{
            width: 10px;
            height: 5px;
            background: {down_arrow};
        }}
        QSpinBox::up-arrow {{
            width: 10px;
            height: 5px;
            background: {up_arrow};
        }}
        QComboBox::down-arrow:disabled, QSpinBox::down-arrow:disabled, QSpinBox::up-arrow:disabled,
        QSpinBox::down-arrow:off, QSpinBox::up-arrow:off {{
            background: transparent;
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
        /* 焦点靠描边提亮表达，控件高度不随焦点变化。 */
        QLineEdit:focus, QComboBox:focus, QComboBox:on, QSpinBox:focus {{
            background: {GLASS_FILL_STRONG};
            border-color: {accent_ring};
        }}
        QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled {{
            background: {GLASS_SHEEN};
            border-color: {GLASS_FILL};
            color: {TEXT_MUTED};
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
        QSlider::handle:horizontal:hover,
        QSlider::handle:horizontal:focus {{
            background: {ACCENT_HOVER};
        }}
        QSlider::handle:horizontal:pressed {{
            background: {ACCENT_PRESSED};
        }}
        QSlider::handle:horizontal:disabled, QSlider::sub-page:horizontal:disabled {{
            background: {GLASS_HAIRLINE_STRONG};
        }}
        QCheckBox {{
            color: {TEXT_PRIMARY};
            spacing: 8px;
            border: none;
        }}
    '''
