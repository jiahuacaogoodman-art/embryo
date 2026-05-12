"""Computer Use 工具 - GUI 桌面操作

工具层适配器：将 ToolRegistry 的函数调用接口适配到 ComputerBackend 接口。
实际 GUI 操作由 action/ 模块的 backend 实现完成。

设计：
- 工具函数提供字符串返回值（兼容 LLM function calling）
- 底层通过 ComputerBackend 接口执行，支持多后端切换
- 默认使用 PyAutoGUIBackend，可通过 set_backend() 切换
"""

from __future__ import annotations

from typing import Optional

from ..action import ComputerBackend, ActionResult, PyAutoGUIBackend
from ..logging import get_logger

logger = get_logger(__name__)

# ============================================================
# 后端管理
# ============================================================

_active_backend: Optional[ComputerBackend] = None


def get_backend() -> ComputerBackend:
    """获取当前活跃的 ComputerBackend 实例

    如果尚未初始化，默认创建 PyAutoGUIBackend。
    """
    global _active_backend
    if _active_backend is None:
        _active_backend = PyAutoGUIBackend()
        _active_backend.setup()
    return _active_backend


def set_backend(backend: ComputerBackend) -> None:
    """切换 GUI 操作后端

    Args:
        backend: 新的后端实例（已初始化或将自动调用 setup）
    """
    global _active_backend
    if _active_backend is not None:
        _active_backend.teardown()
    _active_backend = backend
    _active_backend.setup()


def _result_to_str(result: ActionResult) -> str:
    """将 ActionResult 转换为工具返回字符串"""
    return result.message


# ============================================================
# 工具函数（适配层，委托给 backend）
# ============================================================


def screenshot(region: str = "", save_path: str = "") -> str:
    """截取当前屏幕

    Args:
        region: 截取区域 "x1,y1,x2,y2"（空=全屏）
        save_path: 保存路径（空=自动生成）

    Returns:
        截图保存路径和尺寸信息
    """
    backend = get_backend()
    parsed_region = None
    if region:
        parts = [int(x.strip()) for x in region.split(",")]
        parsed_region = (parts[0], parts[1], parts[2], parts[3])

    result = backend.screenshot(region=parsed_region)
    return _result_to_str(result)


def click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """点击指定坐标（带验证和重试）

    Args:
        x: 横坐标
        y: 纵坐标
        button: 按键类型 (left/right/middle)
        clicks: 点击次数 (1=单击, 2=双击)

    Returns:
        操作结果
    """
    backend = get_backend()
    result = backend.click(x=x, y=y, button=button, clicks=clicks)
    return _result_to_str(result)


def mouse_move(x: int, y: int) -> str:
    """移动鼠标到指定坐标

    Args:
        x: 横坐标
        y: 纵坐标

    Returns:
        操作结果
    """
    backend = get_backend()
    result = backend.mouse_move(x=x, y=y)
    return _result_to_str(result)


def type_text(text: str, interval: float = 0.02) -> str:
    """在当前焦点位置输入文字

    Args:
        text: 要输入的文字
        interval: 按键间隔

    Returns:
        操作结果
    """
    backend = get_backend()
    result = backend.type_text(text=text, interval=interval)
    return _result_to_str(result)


def hotkey(*keys: str) -> str:
    """执行键盘快捷键

    Args:
        keys: 按键组合

    Returns:
        操作结果
    """
    backend = get_backend()
    result = backend.hotkey(keys=list(keys))
    return _result_to_str(result)


def press_key(key: str) -> str:
    """按下单个键

    Args:
        key: 按键名

    Returns:
        操作结果
    """
    backend = get_backend()
    result = backend.press_key(key=key)
    return _result_to_str(result)


def scroll(direction: str = "down", amount: int = 3, x: int = 0, y: int = 0) -> str:
    """滚动页面

    Args:
        direction: 方向 (up/down)
        amount: 滚动量
        x: 滚动位置横坐标
        y: 滚动位置纵坐标

    Returns:
        操作结果
    """
    backend = get_backend()
    result = backend.scroll(direction=direction, amount=amount, x=x, y=y)
    return _result_to_str(result)


def ocr_screen(region: str = "", language: str = "chi_sim+eng") -> str:
    """对屏幕进行 OCR 文字识别

    Args:
        region: 识别区域 "x1,y1,x2,y2"（空=全屏）
        language: OCR 语言

    Returns:
        识别到的文字内容
    """
    backend = get_backend()
    parsed_region = None
    if region:
        parts = [int(x.strip()) for x in region.split(",")]
        parsed_region = (parts[0], parts[1], parts[2], parts[3])

    result = backend.ocr(region=parsed_region, language=language)
    if result.success:
        text = result.metadata.get("text", "")
        if not text:
            return "(未识别到文字)"
        return f"OCR 识别结果:\n{text}"
    return _result_to_str(result)


def find_text_on_screen(target_text: str, language: str = "chi_sim+eng") -> str:
    """在屏幕上查找指定文字的位置

    Args:
        target_text: 要查找的文字
        language: OCR 语言

    Returns:
        文字位置信息
    """
    backend = get_backend()
    result = backend.find_text(target_text=target_text, language=language)
    return _result_to_str(result)


# ============================================================
# 工具定义（注册到 ToolRegistry 用）
# ============================================================

from .registry import Tool

SCREENSHOT_TOOL = Tool(
    name="screenshot",
    description="截取当前屏幕截图。可指定区域。用于观察当前界面状态。",
    parameters={
        "type": "object",
        "properties": {
            "region": {
                "type": "string",
                "description": "截取区域 'x1,y1,x2,y2'（留空=全屏）",
                "default": "",
            },
        },
        "required": [],
    },
    handler=screenshot,
    category="computer_use",
)

CLICK_TOOL = Tool(
    name="click",
    description="在屏幕指定坐标点击。支持左键/右键/双击。",
    parameters={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "横坐标"},
            "y": {"type": "integer", "description": "纵坐标"},
            "button": {"type": "string", "description": "按键 (left/right/middle)", "default": "left"},
            "clicks": {"type": "integer", "description": "点击次数 (1=单击, 2=双击)", "default": 1},
        },
        "required": ["x", "y"],
    },
    handler=click,
    category="computer_use",
)

TYPE_TEXT_TOOL = Tool(
    name="type_text",
    description="在当前焦点位置输入文字。支持中文（通过粘贴方式）。",
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "要输入的文字"},
        },
        "required": ["text"],
    },
    handler=type_text,
    category="computer_use",
)

HOTKEY_TOOL = Tool(
    name="hotkey",
    description="执行键盘快捷键组合，如 Ctrl+C, Alt+Tab 等。",
    parameters={
        "type": "object",
        "properties": {
            "keys": {
                "type": "array",
                "items": {"type": "string"},
                "description": "按键列表，如 ['ctrl', 'c'] 表示 Ctrl+C",
            },
        },
        "required": ["keys"],
    },
    handler=lambda keys: hotkey(*keys),
    category="computer_use",
)

PRESS_KEY_TOOL = Tool(
    name="press_key",
    description="按下单个键（enter/tab/escape/backspace/up/down 等）。",
    parameters={
        "type": "object",
        "properties": {
            "key": {"type": "string", "description": "按键名"},
        },
        "required": ["key"],
    },
    handler=press_key,
    category="computer_use",
)

SCROLL_TOOL = Tool(
    name="scroll",
    description="滚动页面。用于查看屏幕外的内容。",
    parameters={
        "type": "object",
        "properties": {
            "direction": {"type": "string", "description": "方向 (up/down)", "default": "down"},
            "amount": {"type": "integer", "description": "滚动量", "default": 3},
            "x": {"type": "integer", "description": "滚动位置横坐标（0=当前位置）", "default": 0},
            "y": {"type": "integer", "description": "滚动位置纵坐标（0=当前位置）", "default": 0},
        },
        "required": [],
    },
    handler=scroll,
    category="computer_use",
)

OCR_SCREEN_TOOL = Tool(
    name="ocr_screen",
    description="对屏幕进行 OCR 文字识别。用于读取界面上的文字内容。",
    parameters={
        "type": "object",
        "properties": {
            "region": {
                "type": "string",
                "description": "识别区域 'x1,y1,x2,y2'（留空=全屏）",
                "default": "",
            },
        },
        "required": [],
    },
    handler=ocr_screen,
    category="computer_use",
)

FIND_TEXT_TOOL = Tool(
    name="find_text_on_screen",
    description="在屏幕上查找指定文字的位置坐标。用于定位按钮、输入框等。",
    parameters={
        "type": "object",
        "properties": {
            "target_text": {"type": "string", "description": "要查找的文字"},
        },
        "required": ["target_text"],
    },
    handler=find_text_on_screen,
    category="computer_use",
)
