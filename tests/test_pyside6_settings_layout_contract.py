#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""静态布局契约测试（RED）：检查 easy_tts/ui/settings.py 的宽度/排布约束。

仅使用 Python 标准库（ast、pathlib），不导入 PySide6 或 pytest。
当生产代码尚未修复时，本测试预期失败（RED）。
"""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from typing import NoReturn

SETTINGS_DIR = Path(__file__).resolve().parent.parent / "src" / "easy_tts" / "ui"
SETTINGS_PATH = SETTINGS_DIR / "settings.py"
SETTINGS_WIDGETS_PATH = SETTINGS_DIR / "settings_widgets.py"
SETTINGS_STYLE_PATH = SETTINGS_DIR / "settings_style.py"
SETTINGS_STATE_PATH = SETTINGS_DIR / "settings_state.py"
# 所有设置相关源文件，合并后用于静态检查
_SETTINGS_PACKAGE_PATHS = (
    SETTINGS_PATH,
    SETTINGS_WIDGETS_PATH,
    SETTINGS_STYLE_PATH,
    SETTINGS_STATE_PATH,
)

# ----- 期望阈值 -----
MIN_DIALOG_WIDTH = 560
MIN_DIALOG_HEIGHT = 580  # 标签页布局下，默认打开高度只需容纳当前分类内容与底部按钮
MAX_CONTENT_MARGIN = 20
MAX_SECTION_GAP = 12
MIN_COMBO_SIZE_ADJUST_CALLS = 3
MIN_COMBO_MIN_CONTENTS_LENGTH = 3
MIN_COMBO_MIN_CONTENTS_LENGTH_INT = 24
MIN_COMBO_ELIDE_MODE_CALLS = 3

# ----- 可调整大小对话框契约 -----
DIALOG_MIN_WIDTH_THRESHOLD = 480
DIALOG_MIN_HEIGHT_THRESHOLD = 580

RECEIVER_AWARE_FORBIDDEN_RESIZE_METHODS = (
    "setFixedSize",
    "setFixedWidth",
    "setFixedHeight",
    "setMaximumSize",
    "setMaximumWidth",
    "setMaximumHeight",
)

FORBIDDEN_RAW_SOURCE_SUBSTRINGS = (
    "MSWindowsFixedSizeDialogHint",
)

FORBIDDEN_QSS_SUBSTRINGS = [
    "linear-gradient",
    "radial-gradient",
    "qradialgradient",
    "qconicalgradient",
    "box-shadow",
    "border-image",
    "text-shadow",
    "caret-color",
    "transition",
    "var(--",
    "data:image",
]


# ----- 工具函数 -----

def _load_source() -> str:
    """加载 settings 包中所有源文件，合并为单一字符串供静态检查。"""
    parts: list[str] = []
    for path in _SETTINGS_PACKAGE_PATHS:
        if not path.exists():
            raise AssertionError(f"源文件不存在: {path}")
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)


def _load_tree() -> ast.Module:
    return ast.parse(_load_source())


def _module_int_constants(tree: ast.Module) -> dict[str, int]:
    """收集模块顶层 `NAME = <int>` 常量。"""
    constants: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, int) and not isinstance(value.value, bool):
                constants[node.targets[0].id] = value.value
    return constants


def _iter_calls(tree: ast.Module):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            yield node


def _method_name(call: ast.Call) -> str | None:
    func = call.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _call_receiver_text(call: ast.Call) -> str | None:
    func = call.func
    if not isinstance(func, ast.Attribute):
        return None
    if hasattr(ast, "unparse"):
        try:
            return ast.unparse(func.value)
        except Exception:  # noqa: BLE001
            return None
    return None


def _find_stylesheet_string(tree: ast.Module) -> str:
    """从 build_settings_stylesheet 函数中提取静态/可静态推断的样式表内容。"""
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in ("_build_stylesheet", "build_settings_stylesheet"):
            for ret in ast.walk(node):
                if isinstance(ret, ast.Return) and ret.value is not None:
                    value = ret.value
                    if isinstance(value, ast.Constant) and isinstance(value.value, str):
                        return value.value
                    if isinstance(value, ast.JoinedStr):
                        # 拼接 f-string 中的常量字符串部分
                        parts: list[str] = []
                        for child in value.values:
                            if isinstance(child, ast.Constant) and isinstance(child.value, str):
                                parts.append(child.value)
                            elif isinstance(child, ast.FormattedValue):
                                # 占位符以占位标记返回，不影响关键字检测
                                parts.append("<EXPR>")
                        return "".join(parts)
    raise AssertionError("未找到 build_settings_stylesheet 函数或其返回值")


# ----- 断言断点（不依赖 pytest） -----

class TestFailure(AssertionError):
    pass


def _fail(msg: str) -> NoReturn:
    raise TestFailure(msg)


# ----- 测试用例 -----

def test_settings_dialog_static_width_budget_invariants() -> None:
    """对话框宽/高/边距/段间距必须满足新布局预算。"""
    tree = _load_tree()
    consts = _module_int_constants(tree)

    missing = [name for name in ("DIALOG_WIDTH", "DIALOG_HEIGHT", "CONTENT_MARGIN", "SECTION_GAP") if name not in consts]
    if missing:
        _fail(f"easy_tts/ui/settings.py 缺少顶层常量: {missing}")

    width = consts["DIALOG_WIDTH"]
    height = consts["DIALOG_HEIGHT"]
    margin = consts["CONTENT_MARGIN"]
    gap = consts["SECTION_GAP"]

    if width < MIN_DIALOG_WIDTH:
        _fail(f"DIALOG_WIDTH={width} 必须 >= {MIN_DIALOG_WIDTH}（更宽以容纳音色/设备名）")
    if height < MIN_DIALOG_HEIGHT:
        _fail(f"DIALOG_HEIGHT={height} 必须 >= {MIN_DIALOG_HEIGHT}（默认打开需显示全部配置项）")
    if margin > MAX_CONTENT_MARGIN:
        _fail(f"CONTENT_MARGIN={margin} 必须 <= {MAX_CONTENT_MARGIN}（释放横向空间给内容）")
    if gap > MAX_SECTION_GAP:
        _fail(f"SECTION_GAP={gap} 必须 <= {MAX_SECTION_GAP}（更紧凑的纵向节段）")


def test_settings_combo_static_sizing_invariants() -> None:
    """所有 QComboBox 必须显式设置 sizeAdjust/minimumContentsLength/textElideMode。"""
    tree = _load_tree()

    size_adjust_calls: list[ast.Call] = []
    min_contents_calls: list[ast.Call] = []
    elide_calls: list[ast.Call] = []

    for call in _iter_calls(tree):
        name = _method_name(call)
        if name == "setSizeAdjustPolicy":
            size_adjust_calls.append(call)
        elif name == "setMinimumContentsLength":
            min_contents_calls.append(call)
        elif name == "setTextElideMode":
            elide_calls.append(call)

    if len(size_adjust_calls) < MIN_COMBO_SIZE_ADJUST_CALLS:
        _fail(
            f"setSizeAdjustPolicy 调用数 {len(size_adjust_calls)} < 期望 {MIN_COMBO_SIZE_ADJUST_CALLS}"
            "（voice/tts_backend/audio_output 三个 QComboBox 都需要设置）"
        )

    # 至少 3 个 setMinimumContentsLength 调用，且参数为 int 且 >= 24
    qualifying_min_contents = 0
    for call in min_contents_calls:
        if len(call.args) >= 1 and isinstance(call.args[0], ast.Constant) and isinstance(call.args[0].value, int):
            if call.args[0].value >= MIN_COMBO_MIN_CONTENTS_LENGTH_INT:
                qualifying_min_contents += 1
    if qualifying_min_contents < MIN_COMBO_MIN_CONTENTS_LENGTH:
        _fail(
            f"setMinimumContentsLength(>= {MIN_COMBO_MIN_CONTENTS_LENGTH_INT}) 调用数 "
            f"{qualifying_min_contents} < 期望 {MIN_COMBO_MIN_CONTENTS_LENGTH}（总 {len(min_contents_calls)} 个调用）"
        )

    # 至少 3 个 setTextElideMode 调用，参数应为 ElideRight
    qualifying_elide = 0
    for call in elide_calls:
        if len(call.args) >= 1:
            arg = call.args[0]
            arg_text = ast.unparse(arg) if hasattr(ast, "unparse") else ""
            if "ElideRight" in arg_text:
                qualifying_elide += 1
    if qualifying_elide < MIN_COMBO_ELIDE_MODE_CALLS:
        _fail(
            f"setTextElideMode(Qt.ElideRight 等价) 调用数 {qualifying_elide} < "
            f"期望 {MIN_COMBO_ELIDE_MODE_CALLS}（总 {len(elide_calls)} 个调用）"
        )


def test_settings_qss_static_forbidden_substrings() -> None:
    """设置页样式表不得包含禁用的 QSS/CSS 关键字。"""
    stylesheet = _find_stylesheet_string(_load_tree())
    lower = stylesheet.lower()
    found: list[str] = []
    for token in FORBIDDEN_QSS_SUBSTRINGS:
        if token.lower() in lower:
            found.append(token)
    if found:
        _fail(f"settings 样式表包含禁用关键字: {found}")


def test_settings_dialog_is_resizable_with_min_size() -> None:
    """对话框必须暴露 DIALOG_MIN_WIDTH/HEIGHT 常量并通过 setMinimumSize+resize 声明可调尺寸。"""
    tree = _load_tree()
    consts = _module_int_constants(tree)

    missing = [name for name in ("DIALOG_MIN_WIDTH", "DIALOG_MIN_HEIGHT") if name not in consts]
    if missing:
        _fail(f"easy_tts/ui/settings.py 缺少顶层常量: {missing}")

    min_width = consts["DIALOG_MIN_WIDTH"]
    min_height = consts["DIALOG_MIN_HEIGHT"]
    if min_width < DIALOG_MIN_WIDTH_THRESHOLD:
        _fail(f"DIALOG_MIN_WIDTH={min_width} 必须 >= {DIALOG_MIN_WIDTH_THRESHOLD}")
    if min_height < DIALOG_MIN_HEIGHT_THRESHOLD:
        _fail(f"DIALOG_MIN_HEIGHT={min_height} 必须 >= {DIALOG_MIN_HEIGHT_THRESHOLD}")

    min_size_calls: list[ast.Call] = []
    resize_calls: list[ast.Call] = []
    for call in _iter_calls(tree):
        name = _method_name(call)
        receiver = _call_receiver_text(call)
        if receiver != "self.window":
            continue
        if name == "setMinimumSize":
            min_size_calls.append(call)
        elif name == "resize":
            resize_calls.append(call)

    if not min_size_calls:
        _fail("缺少 self.window.setMinimumSize(...) 调用（应使用 DIALOG_MIN_WIDTH/HEIGHT）")
    if not resize_calls:
        _fail("缺少 self.window.resize(...) 调用（应使用 DIALOG_WIDTH/HEIGHT 作为初始尺寸）")

    grip_calls = 0
    for call in _iter_calls(tree):
        if _method_name(call) != "setSizeGripEnabled":
            continue
        if _call_receiver_text(call) != "self.window":
            continue
        if len(call.args) >= 1 and isinstance(call.args[0], ast.Constant) and call.args[0].value is True:
            grip_calls += 1
    if grip_calls != 1:
        _fail(f"self.window.setSizeGripEnabled(True) 调用数 {grip_calls} != 1")


def test_settings_dialog_does_not_lock_size() -> None:
    """self.window 不得调用任何固定/最大尺寸方法；原始源码不得包含 MSWindowsFixedSizeDialogHint。"""
    tree = _load_tree()
    offenders: list[str] = []
    for call in _iter_calls(tree):
        name = _method_name(call)
        if name not in RECEIVER_AWARE_FORBIDDEN_RESIZE_METHODS:
            continue
        receiver = _call_receiver_text(call)
        if receiver == "self.window":
            offenders.append(f"{receiver}.{name}(...) @line {call.lineno}")
    if offenders:
        _fail(f"self.window 上禁止使用尺寸锁死方法: {offenders}")

    source = _load_source()
    raw_hits: list[str] = []
    for token in FORBIDDEN_RAW_SOURCE_SUBSTRINGS:
        if token in source:
            raw_hits.append(token)
    if raw_hits:
        _fail(f"easy_tts/ui/settings.py 原始源码包含禁用窗口标志: {raw_hits}")


def test_settings_dialog_preserves_existing_contract() -> None:
    """新增的可调整大小契约不得破坏原有 DIALOG_WIDTH/HEIGHT/CONTENT_MARGIN/SECTION_GAP/center_window 用法。"""
    tree = _load_tree()
    consts = _module_int_constants(tree)
    required = ("DIALOG_WIDTH", "DIALOG_HEIGHT", "CONTENT_MARGIN", "SECTION_GAP")
    missing = [name for name in required if name not in consts]
    if missing:
        _fail(f"既有顶层常量缺失: {missing}")

    center_calls = 0
    for call in _iter_calls(tree):
        if _method_name(call) == "center_window":
            center_calls += 1
    if center_calls < 1:
        _fail("center_window(...) 调用必须保留至少一次")


def test_settings_combos_use_no_wheel_subclass() -> None:
    """设置页三个下拉框必须使用禁用滚轮切换的 NoWheelComboBox。"""
    source = _load_source()
    if "class NoWheelComboBox" not in source:
        _fail("easy_tts/ui/settings.py 必须定义 NoWheelComboBox")
    if source.count("NoWheelComboBox()") < 6:
        _fail("voice/backend/audio output 的属性初始化和构建处都应使用 NoWheelComboBox()")


def test_settings_sliders_use_no_wheel_subclass() -> None:
    """音量和透明度滑动条必须使用禁用滚轮调整的 NoWheelSlider。"""
    source = _load_source()
    if "class NoWheelSlider" not in source:
        _fail("easy_tts/ui/settings.py 必须定义 NoWheelSlider")
    if source.count("NoWheelSlider(Qt.Orientation.Horizontal)") < 4:
        _fail("volume/opacity 的属性初始化和构建处都应使用 NoWheelSlider(Qt.Orientation.Horizontal)")


def test_settings_buttons_row_is_right_aligned() -> None:
    """底部按钮应右对齐，且取消在左、应用在右，更符合设置对话框习惯。"""
    source = _load_source()
    start = source.index("    def _build_buttons")
    end = source.index("    def _add_inner_gap", start)
    body = source[start:end]
    if body.count("button_row.addStretch(1)") != 1:
        _fail("_build_buttons 应只保留一个左侧 addStretch(1) 以右对齐按钮")
    cancel_pos = body.find("cancel_button = QPushButton")
    apply_pos = body.find("apply_button = QPushButton")
    if cancel_pos == -1 or apply_pos == -1 or cancel_pos > apply_pos:
        _fail("_build_buttons 中取消按钮应位于应用按钮之前")


def test_settings_dialog_static_window_flags_include_frameless_and_topmost() -> None:
    """设置窗口必须声明无边框，同时保留置顶窗口标志。"""
    source = _load_source()
    if "Qt.WindowType.FramelessWindowHint" not in source:
        _fail("SettingsWindow 必须使用 Qt.WindowType.FramelessWindowHint")
    if "Qt.WindowType.WindowStaysOnTopHint" not in source:
        _fail("SettingsWindow 必须保留 Qt.WindowType.WindowStaysOnTopHint")


def test_settings_dialog_static_uses_translucent_background_attribute() -> None:
    """圆角无边框设置窗口必须启用 WA_TranslucentBackground，避免圆角外出现方形底色。"""
    tree = _load_tree()
    matching_calls = 0
    for call in _iter_calls(tree):
        if _method_name(call) != "setAttribute":
            continue
        if _call_receiver_text(call) != "self.window":
            continue
        if len(call.args) < 2:
            continue
        attr_text = ast.unparse(call.args[0]) if hasattr(ast, "unparse") else ""
        if (
            "WA_TranslucentBackground" in attr_text
            and isinstance(call.args[1], ast.Constant)
            and call.args[1].value is True
        ):
            matching_calls += 1
    if matching_calls != 1:
        _fail("必须调用 self.window.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True) 且仅一次")


def test_settings_dialog_qss_background_is_transparent() -> None:
    """QDialog 自身背景必须透明，由 QFrame#dialogShell 提供可见圆角背景。"""
    stylesheet = _find_stylesheet_string(_load_tree())
    match = re.search(r"QDialog\s*\{(?P<body>.*?)\}", stylesheet, flags=re.DOTALL)
    if not match:
        _fail("样式表必须包含 QDialog 规则")
    body = match.group("body")
    if not re.search(r"background(?:-color)?\s*:\s*transparent\s*;", body, flags=re.IGNORECASE):
        _fail("QDialog QSS 必须设置 background/background-color: transparent;")


def test_settings_hint_label_stylesheet_uses_12px_font_size() -> None:
    """hintLabel QSS 契约：提示文字字号必须为 12px。"""
    stylesheet = _find_stylesheet_string(_load_tree())
    match = re.search(r"QLabel#hintLabel\s*\{(?P<body>.*?)\}", stylesheet, flags=re.DOTALL)
    if not match:
        _fail("样式表必须包含 QLabel#hintLabel 规则")
    body = match.group("body")
    if not re.search(r"font-size\s*:\s*12px\s*;", body):
        _fail("QLabel#hintLabel 必须设置 font-size: 12px;")


def test_settings_audio_output_status_label_uses_hint_label_object_name() -> None:
    """音频输出状态标签必须复用 hintLabel 样式。"""
    source = _load_source()
    pattern = (
        r"self\.audio_output_status_label(?:\s*:\s*QLabel)?\s*=\s*QLabel\([^\n]*\).*?"
        r"self\.audio_output_status_label\.setObjectName\(\s*[\"']hintLabel[\"']\s*\)"
    )
    if not re.search(pattern, source, flags=re.DOTALL):
        _fail('audio_output_status_label 必须调用 setObjectName("hintLabel")')


def test_settings_dialog_static_title_area_supports_drag_event_filter() -> None:
    """无边框设置窗口必须在内部标题区域安装 eventFilter 以支持拖动。"""
    source = _load_source()
    if "def eventFilter" not in source:
        _fail("_SettingsDialog 必须实现 eventFilter(...) 处理标题栏拖动")
    if "installEventFilter(self.window)" not in source:
        _fail("dialogTitle 对应 title_label 必须调用 title_label.installEventFilter(self.window)")
    if "setCursor(Qt.CursorShape.OpenHandCursor)" not in source:
        _fail("dialogTitle 对应 title_label 必须调用 title_label.setCursor(Qt.CursorShape.OpenHandCursor)")


def test_settings_dialog_static_drag_state_fields_exist() -> None:
    """拖动实现必须显式维护按下状态和偏移量，释放后可清理状态。"""
    source = _load_source()
    required_tokens = (
        "_drag_active",
        "_drag_position",
    )
    missing = [token for token in required_tokens if token not in source]
    if missing:
        _fail(f"_SettingsDialog 缺少拖动状态字段: {missing}")


def test_settings_dialog_static_drag_position_is_not_persisted() -> None:
    """标题拖动只移动当前无边框窗口，不得引入位置持久化/配置保存。"""
    source = _load_source()
    forbidden = (
        "save_app_config",
        "window_position",
    )
    found = [token for token in forbidden if token in source]
    if found:
        _fail(f"easy_tts/ui/settings.py 不得包含拖动位置持久化/配置保存代码: {found}")


def test_audio_output_is_owned_by_routing_tab_and_locks_to_cable() -> None:
    """音频输出只能在路由页构建，路由启用时必须禁用下拉框并显示 CABLE Input。"""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    if "_build_audio_output_section(local_layout" in source:
        _fail("音频输出仍位于本地设置页")
    if "_build_audio_output_section(route_layout" not in source:
        _fail("音频输出未移动到音频路由页")
    if "def _sync_audio_output_control" not in source or "combo.setEnabled(False)" not in source:
        _fail("缺少路由启用时锁定音频输出下拉框的实现")
    if "CABLE Input" not in source or "Windows WASAPI" not in source:
        _fail("锁定输出未明确指向 Windows WASAPI CABLE Input")


def test_audio_route_effect_test_module_is_removed() -> None:
    """设置 UI 不得残留路由效果测试按钮、状态或回调。"""
    source = SETTINGS_PATH.read_text(encoding="utf-8")
    forbidden = ("效果测试", "route_test", "set_test_recording_available")
    found = [token for token in forbidden if token in source]
    if found:
        _fail(f"音频路由效果测试模块仍有残留: {found}")


# ----- 运行器 -----

def main() -> int:
    tests = [
        ("test_settings_dialog_static_width_budget_invariants", test_settings_dialog_static_width_budget_invariants),
        ("test_settings_combo_static_sizing_invariants", test_settings_combo_static_sizing_invariants),
        ("test_settings_qss_static_forbidden_substrings", test_settings_qss_static_forbidden_substrings),
        ("test_settings_dialog_is_resizable_with_min_size", test_settings_dialog_is_resizable_with_min_size),
        ("test_settings_dialog_does_not_lock_size", test_settings_dialog_does_not_lock_size),
        ("test_settings_dialog_preserves_existing_contract", test_settings_dialog_preserves_existing_contract),
        ("test_settings_combos_use_no_wheel_subclass", test_settings_combos_use_no_wheel_subclass),
        ("test_settings_sliders_use_no_wheel_subclass", test_settings_sliders_use_no_wheel_subclass),
        ("test_settings_buttons_row_is_right_aligned", test_settings_buttons_row_is_right_aligned),
        (
            "test_settings_dialog_static_window_flags_include_frameless_and_topmost",
            test_settings_dialog_static_window_flags_include_frameless_and_topmost,
        ),
        (
            "test_settings_dialog_static_uses_translucent_background_attribute",
            test_settings_dialog_static_uses_translucent_background_attribute,
        ),
        ("test_settings_dialog_qss_background_is_transparent", test_settings_dialog_qss_background_is_transparent),
        ("test_settings_hint_label_stylesheet_uses_12px_font_size", test_settings_hint_label_stylesheet_uses_12px_font_size),
        (
            "test_settings_audio_output_status_label_uses_hint_label_object_name",
            test_settings_audio_output_status_label_uses_hint_label_object_name,
        ),
        (
            "test_settings_dialog_static_title_area_supports_drag_event_filter",
            test_settings_dialog_static_title_area_supports_drag_event_filter,
        ),
        (
            "test_settings_dialog_static_drag_state_fields_exist",
            test_settings_dialog_static_drag_state_fields_exist,
        ),
        (
            "test_settings_dialog_static_drag_position_is_not_persisted",
            test_settings_dialog_static_drag_position_is_not_persisted,
        ),
        (
            "test_audio_output_is_owned_by_routing_tab_and_locks_to_cable",
            test_audio_output_is_owned_by_routing_tab_and_locks_to_cable,
        ),
        (
            "test_audio_route_effect_test_module_is_removed",
            test_audio_route_effect_test_module_is_removed,
        ),
    ]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except TestFailure as exc:
            failures += 1
            print(f"FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures} passed, {failures} failed of {len(tests)}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
