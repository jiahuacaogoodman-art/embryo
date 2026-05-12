"""Action 模块 - 多后端 GUI 执行层

提供统一的 ComputerBackend 接口，支持多种 GUI 自动化后端：
- PyAutoGUIBackend: 前台坐标操作（fallback）
- PlaywrightBackend: 浏览器 DOM 操作（计划中）
- AccessibilityBackend: 系统 Accessibility API（计划中）
- RemoteVNCBackend: Docker/远程桌面操作（计划中）
"""

from .backend import ComputerBackend, ActionResult, ActionStatus
from .pyautogui_backend import PyAutoGUIBackend

__all__ = [
    "ComputerBackend",
    "ActionResult",
    "ActionStatus",
    "PyAutoGUIBackend",
]
