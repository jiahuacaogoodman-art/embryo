"""ComputerBackend 抽象基类

定义 GUI 自动化执行层的统一接口。
所有后端（pyautogui / playwright / accessibility / VNC）都实现这个接口。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class ActionStatus(str, Enum):
    """动作执行状态"""

    SUCCESS = "success"
    FAILED = "failed"
    NO_EFFECT = "no_effect"  # 动作执行了，但界面无变化
    TIMEOUT = "timeout"
    TARGET_NOT_FOUND = "target_not_found"
    BLOCKED = "blocked"  # 被弹窗/权限等阻止


@dataclass
class ActionResult:
    """动作执行结果

    每个 backend 方法都返回这个结构，提供统一的结果描述。
    """

    status: ActionStatus
    message: str = ""
    screenshot_before: str | None = None  # 执行前截图路径
    screenshot_after: str | None = None  # 执行后截图路径
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.status == ActionStatus.SUCCESS

    def __str__(self) -> str:
        return f"[{self.status.value}] {self.message}"


@dataclass
class ScreenInfo:
    """屏幕/视口基本信息"""

    width: int
    height: int
    scale_factor: float = 1.0
    platform: str = "unknown"


class ComputerBackend(ABC):
    """GUI 自动化后端抽象基类

    所有 GUI 操作后端必须实现以下方法。
    执行层通过这个接口调用，不关心具体实现。

    设计原则：
    - 每个方法都返回 ActionResult，包含状态和诊断信息
    - observe() 返回当前屏幕状态，用于规划和验证
    - 各 backend 可以自行实现最优的定位策略
    """

    @abstractmethod
    def get_screen_info(self) -> ScreenInfo:
        """获取屏幕/视口基本信息"""
        ...

    @abstractmethod
    def screenshot(self, region: tuple[int, int, int, int] | None = None) -> ActionResult:
        """截取屏幕

        Args:
            region: 截取区域 (x1, y1, x2, y2)，None=全屏

        Returns:
            ActionResult，metadata 中包含:
              - path: 截图保存路径
              - width: 图片宽度
              - height: 图片高度
        """
        ...

    @abstractmethod
    def click(
        self,
        x: int,
        y: int,
        button: str = "left",
        clicks: int = 1,
    ) -> ActionResult:
        """点击指定坐标

        Args:
            x: 横坐标
            y: 纵坐标
            button: 按键类型 (left/right/middle)
            clicks: 点击次数

        Returns:
            ActionResult
        """
        ...

    @abstractmethod
    def type_text(self, text: str, interval: float = 0.02) -> ActionResult:
        """在当前焦点位置输入文字

        Args:
            text: 要输入的文字（支持中文）
            interval: 按键间隔（秒）

        Returns:
            ActionResult
        """
        ...

    @abstractmethod
    def hotkey(self, keys: list[str]) -> ActionResult:
        """执行键盘快捷键组合

        Args:
            keys: 按键列表，如 ["ctrl", "c"]

        Returns:
            ActionResult
        """
        ...

    @abstractmethod
    def press_key(self, key: str) -> ActionResult:
        """按下单个键

        Args:
            key: 按键名 (enter/tab/escape/backspace 等)

        Returns:
            ActionResult
        """
        ...

    @abstractmethod
    def scroll(
        self,
        direction: str = "down",
        amount: int = 3,
        x: int = 0,
        y: int = 0,
    ) -> ActionResult:
        """滚动页面

        Args:
            direction: 方向 (up/down/left/right)
            amount: 滚动量
            x: 滚动位置横坐标（0=当前位置）
            y: 滚动位置纵坐标（0=当前位置）

        Returns:
            ActionResult
        """
        ...

    @abstractmethod
    def mouse_move(self, x: int, y: int) -> ActionResult:
        """移动鼠标到指定坐标

        Args:
            x: 横坐标
            y: 纵坐标

        Returns:
            ActionResult
        """
        ...

    @abstractmethod
    def ocr(
        self,
        region: tuple[int, int, int, int] | None = None,
        language: str = "chi_sim+eng",
    ) -> ActionResult:
        """对屏幕进行 OCR 文字识别

        Args:
            region: 识别区域 (x1, y1, x2, y2)，None=全屏
            language: OCR 语言

        Returns:
            ActionResult，metadata 中包含:
              - text: 识别到的全文
              - boxes: OCR 识别框列表 [{text, x, y, w, h, confidence}]
        """
        ...

    @abstractmethod
    def find_text(
        self,
        target_text: str,
        language: str = "chi_sim+eng",
    ) -> ActionResult:
        """在屏幕上查找指定文字的位置

        Args:
            target_text: 要查找的文字
            language: OCR 语言

        Returns:
            ActionResult，metadata 中包含:
              - matches: [{text, cx, cy, x, y, w, h, confidence}]
        """
        ...

    # ========================================
    # 生命周期
    # ========================================

    def setup(self) -> None:
        """后端初始化，连接建立等"""
        pass

    def teardown(self) -> None:
        """后端清理，连接释放等"""
        pass

    def __enter__(self):
        self.setup()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.teardown()
        return False
