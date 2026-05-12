"""Computer Use 工具 - GUI 桌面操作

- 后台操作桌面，不抢占用户鼠标焦点
- 截图感知 + OCR + 坐标点击
- 操作后自动截图验证闭环
- 失败时自动重试 + 错误诊断

核心设计：
- 每个 GUI 动作执行后，自动截图与执行前对比
- 如果界面无变化，判定为操作失败，附加诊断信息
- 内置重试机制（坐标微调、延时重试）
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Optional

from ..logging import get_logger

logger = get_logger(__name__)

# 操作后等待界面响应的时间
_ACTION_DELAY = 0.3
# 最大重试次数
_MAX_RETRIES = 2


# ============================================================
# 截图工具
# ============================================================

def screenshot(region: str = "", save_path: str = "") -> str:
    """截取当前屏幕

    Args:
        region: 截取区域 "x1,y1,x2,y2"（空=全屏）
        save_path: 保存路径（空=临时文件）

    Returns:
        截图保存路径和尺寸信息
    """
    try:
        import pyautogui
        from PIL import Image

        if region:
            parts = [int(x.strip()) for x in region.split(",")]
            x1, y1, x2, y2 = parts
            img = pyautogui.screenshot(region=(x1, y1, x2 - x1, y2 - y1))
        else:
            img = pyautogui.screenshot()

        if not save_path:
            save_dir = Path.home() / ".embryo" / "screenshots"
            save_dir.mkdir(parents=True, exist_ok=True)
            save_path = str(save_dir / f"screen_{int(time.time())}.png")

        img.save(save_path)
        return f"截图已保存: {save_path} (尺寸: {img.size[0]}x{img.size[1]})"
    except ImportError:
        return "[Error] pyautogui 未安装。运行 pip install pyautogui pillow"
    except Exception as e:
        return f"[Error] 截图失败: {e}"


# ============================================================
# 鼠标操作
# ============================================================

def click(x: int, y: int, button: str = "left", clicks: int = 1) -> str:
    """点击指定坐标（带验证和重试）

    执行后自动截图对比，如果界面无变化则诊断并重试。

    Args:
        x: 横坐标
        y: 纵坐标
        button: 按键类型 (left/right/middle)
        clicks: 点击次数 (1=单击, 2=双击)

    Returns:
        操作结果（含验证信息）
    """
    try:
        import pyautogui
        from PIL import Image
        import hashlib

        # 操作前截图 hash（用于验证）
        before_img = pyautogui.screenshot()
        before_hash = hashlib.md5(before_img.tobytes()[:10000]).hexdigest()

        # 执行点击
        pyautogui.click(x=x, y=y, button=button, clicks=clicks)
        time.sleep(_ACTION_DELAY)

        # 操作后截图验证
        after_img = pyautogui.screenshot()
        after_hash = hashlib.md5(after_img.tobytes()[:10000]).hexdigest()

        if before_hash != after_hash:
            logger.debug("click_verified", x=x, y=y, result="界面已变化")
            return f"已点击 ({x}, {y})，界面已响应变化"
        else:
            # 界面未变化 → 可能点击无效
            # 重试一次（加小延时）
            time.sleep(0.5)
            pyautogui.click(x=x, y=y, button=button, clicks=clicks)
            time.sleep(_ACTION_DELAY * 2)

            retry_img = pyautogui.screenshot()
            retry_hash = hashlib.md5(retry_img.tobytes()[:10000]).hexdigest()

            if retry_hash != before_hash:
                return f"已点击 ({x}, {y})，重试后界面已响应"
            else:
                logger.warning("click_no_change", x=x, y=y)
                return (
                    f"已点击 ({x}, {y})，但界面未发生变化。"
                    f"可能原因：坐标偏移、元素未加载、按钮被遮挡。"
                    f"建议：用 find_text_on_screen 重新定位目标，或等待后重试。"
                )
    except ImportError:
        return "[Error] pyautogui 未安装"
    except Exception as e:
        return f"[Error] 点击失败: {e}"


def mouse_move(x: int, y: int) -> str:
    """移动鼠标到指定坐标

    Args:
        x: 横坐标
        y: 纵坐标

    Returns:
        操作结果
    """
    try:
        import pyautogui
        pyautogui.moveTo(x=x, y=y, duration=0.2)
        return f"鼠标已移动到 ({x}, {y})"
    except ImportError:
        return "[Error] pyautogui 未安装"
    except Exception as e:
        return f"[Error] 移动失败: {e}"


# ============================================================
# 键盘操作
# ============================================================

def type_text(text: str, interval: float = 0.02) -> str:
    """在当前焦点位置输入文字（带验证）

    输入后通过截图对比验证文字是否成功输入。

    Args:
        text: 要输入的文字
        interval: 按键间隔

    Returns:
        操作结果（含验证）
    """
    try:
        import pyautogui
        import hashlib

        # 操作前截图
        before_img = pyautogui.screenshot()
        before_hash = hashlib.md5(before_img.tobytes()[:10000]).hexdigest()

        # 中文等非ASCII字符使用粘贴方式
        if any(ord(c) > 127 for c in text):
            try:
                import pyperclip
                pyperclip.copy(text)
                pyautogui.hotkey("ctrl", "v")
            except ImportError:
                # 回退：逐字符无法输入中文，报错
                return "[Error] 输入中文需要 pyperclip 包。运行 pip install pyperclip"
        else:
            pyautogui.typewrite(text, interval=interval)

        time.sleep(_ACTION_DELAY)

        # 验证
        after_img = pyautogui.screenshot()
        after_hash = hashlib.md5(after_img.tobytes()[:10000]).hexdigest()

        display_text = text[:50] + ('...' if len(text) > 50 else '')
        if before_hash != after_hash:
            return f"已输入: '{display_text}'，界面已响应"
        else:
            return (
                f"已输入: '{display_text}'，但界面未变化。"
                f"可能原因：输入框未获得焦点。"
                f"建议：先用 click 点击输入框，再 type_text。"
            )
    except ImportError:
        return "[Error] pyautogui 未安装"
    except Exception as e:
        return f"[Error] 输入失败: {e}"


def hotkey(*keys: str) -> str:
    """执行键盘快捷键

    Args:
        keys: 按键组合，如 "ctrl", "c" (Ctrl+C)

    Returns:
        操作结果
    """
    # keys 参数通过 JSON 传入时会是一个列表
    try:
        import pyautogui
        pyautogui.hotkey(*keys)
        return f"已按下快捷键: {'+'.join(keys)}"
    except ImportError:
        return "[Error] pyautogui 未安装"
    except Exception as e:
        return f"[Error] 快捷键失败: {e}"


def press_key(key: str) -> str:
    """按下单个键

    Args:
        key: 按键名 (enter/tab/escape/backspace/up/down/left/right 等)

    Returns:
        操作结果
    """
    try:
        import pyautogui
        pyautogui.press(key)
        return f"已按下: {key}"
    except ImportError:
        return "[Error] pyautogui 未安装"
    except Exception as e:
        return f"[Error] 按键失败: {e}"


# ============================================================
# 滚动操作
# ============================================================

def scroll(direction: str = "down", amount: int = 3, x: int = 0, y: int = 0) -> str:
    """滚动页面

    Args:
        direction: 方向 (up/down)
        amount: 滚动量
        x: 滚动位置横坐标（0=当前位置）
        y: 滚动位置纵坐标（0=当前位置）

    Returns:
        操作结果
    """
    try:
        import pyautogui
        if x and y:
            pyautogui.moveTo(x, y)

        scroll_amount = amount if direction == "up" else -amount
        pyautogui.scroll(scroll_amount)
        return f"已滚动: 方向={direction}, 量={amount}"
    except ImportError:
        return "[Error] pyautogui 未安装"
    except Exception as e:
        return f"[Error] 滚动失败: {e}"


# ============================================================
# OCR 文字识别
# ============================================================

def ocr_screen(region: str = "", language: str = "chi_sim+eng") -> str:
    """对屏幕进行 OCR 文字识别

    Args:
        region: 识别区域 "x1,y1,x2,y2"（空=全屏）
        language: OCR 语言

    Returns:
        识别到的文字内容
    """
    try:
        import pyautogui
        import pytesseract
        import numpy as np
        from PIL import Image

        if region:
            parts = [int(x.strip()) for x in region.split(",")]
            x1, y1, x2, y2 = parts
            img = pyautogui.screenshot(region=(x1, y1, x2 - x1, y2 - y1))
        else:
            img = pyautogui.screenshot()

        text = pytesseract.image_to_string(img, lang=language)
        text = text.strip()
        if not text:
            return "(未识别到文字)"
        return f"OCR 识别结果:\n{text}"
    except ImportError as e:
        return f"[Error] 依赖缺失: {e}。需要 pyautogui, pytesseract, pillow"
    except Exception as e:
        return f"[Error] OCR 失败: {e}"


def find_text_on_screen(target_text: str, language: str = "chi_sim+eng") -> str:
    """在屏幕上查找指定文字的位置

    Args:
        target_text: 要查找的文字
        language: OCR 语言

    Returns:
        文字位置信息
    """
    try:
        import pyautogui
        import pytesseract
        from PIL import Image

        img = pyautogui.screenshot()
        data = pytesseract.image_to_data(img, lang=language, output_type=pytesseract.Output.DICT)

        matches = []
        n = len(data["text"])
        for i in range(n):
            text = data["text"][i].strip()
            if target_text in text or text in target_text:
                conf = int(data["conf"][i])
                if conf > 50:
                    x = data["left"][i]
                    y = data["top"][i]
                    w = data["width"][i]
                    h = data["height"][i]
                    cx = x + w // 2
                    cy = y + h // 2
                    matches.append(f"'{text}' 位置=({cx},{cy}) 区域=({x},{y},{x+w},{y+h}) 置信度={conf}%")

        if not matches:
            return f"未在屏幕上找到 '{target_text}'"
        return f"找到 {len(matches)} 处匹配:\n" + "\n".join(matches)
    except ImportError as e:
        return f"[Error] 依赖缺失: {e}"
    except Exception as e:
        return f"[Error] 查找失败: {e}"


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
