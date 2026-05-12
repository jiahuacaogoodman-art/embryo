"""Observation 数据模型

每次操作前后产生一个 Observation 快照，聚合所有感知数据。
用于：
- Planner 规划下一步（理解当前界面状态）
- Verifier 验证操作结果
- TargetResolver 定位目标元素
- Trace 记录完整执行过程
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class ObservationSource(str, Enum):
    """感知数据来源"""

    SCREENSHOT = "screenshot"
    OCR = "ocr"
    ACCESSIBILITY = "accessibility"
    DOM = "dom"
    WINDOW_MANAGER = "window_manager"


class OCRBox(BaseModel):
    """OCR 识别框"""

    text: str
    x: int  # 左上角 x
    y: int  # 左上角 y
    width: int
    height: int
    confidence: float = 0.0  # 0-100

    @property
    def cx(self) -> int:
        """中心点 x"""
        return self.x + self.width // 2

    @property
    def cy(self) -> int:
        """中心点 y"""
        return self.y + self.height // 2

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """(x1, y1, x2, y2)"""
        return (self.x, self.y, self.x + self.width, self.y + self.height)


class UIElement(BaseModel):
    """Accessibility / DOM UI 元素"""

    role: str = ""  # button, textfield, link, etc.
    name: str = ""  # 可见文本 / accessible name
    value: str = ""  # 当前值（输入框内容等）
    description: str = ""  # accessible description
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    is_enabled: bool = True
    is_focused: bool = False
    is_visible: bool = True
    children: list[UIElement] = Field(default_factory=list)
    attributes: dict[str, str] = Field(default_factory=dict)

    @property
    def cx(self) -> int:
        return self.x + self.width // 2

    @property
    def cy(self) -> int:
        return self.y + self.height // 2


class Observation(BaseModel):
    """统一感知快照

    聚合当前时刻的所有感知信息。
    不是所有字段都必须填充——根据后端能力和配置，
    部分字段可能为空。
    """

    # 元信息
    timestamp: float = Field(default_factory=time.time)
    sources: list[ObservationSource] = Field(default_factory=list)

    # 屏幕基本信息
    screen_width: int = 0
    screen_height: int = 0

    # 截图
    screenshot_path: str | None = None

    # 窗口信息
    active_window_title: str | None = None
    active_window_bounds: tuple[int, int, int, int] | None = None  # x, y, w, h

    # OCR 结果
    ocr_text: str = ""
    ocr_boxes: list[OCRBox] = Field(default_factory=list)

    # Accessibility Tree
    accessibility_tree: list[UIElement] = Field(default_factory=list)

    # DOM 快照（浏览器场景）
    dom_snapshot: dict[str, Any] | None = None
    browser_url: str | None = None
    page_title: str | None = None

    # 额外元数据
    metadata: dict[str, Any] = Field(default_factory=dict)

    # --------------------------------------------------
    # 便捷查询方法
    # --------------------------------------------------

    def has_text(self, text: str, case_sensitive: bool = False) -> bool:
        """检查 OCR 文本中是否包含指定文字"""
        if case_sensitive:
            return text in self.ocr_text
        return text.lower() in self.ocr_text.lower()

    def find_text_boxes(self, text: str, min_confidence: float = 50.0) -> list[OCRBox]:
        """在 OCR boxes 中查找包含指定文字的框"""
        results = []
        for box in self.ocr_boxes:
            if box.confidence >= min_confidence:
                if text in box.text or box.text in text:
                    results.append(box)
        return results

    def find_elements_by_role(self, role: str) -> list[UIElement]:
        """在 accessibility tree 中查找指定角色的元素"""
        results = []
        self._search_elements(self.accessibility_tree, lambda el: el.role == role, results)
        return results

    def find_elements_by_name(self, name: str) -> list[UIElement]:
        """在 accessibility tree 中查找指定名称的元素"""
        results = []
        self._search_elements(
            self.accessibility_tree,
            lambda el: name.lower() in el.name.lower(),
            results,
        )
        return results

    def _search_elements(
        self,
        elements: list[UIElement],
        predicate,
        results: list[UIElement],
    ) -> None:
        """递归搜索 UI 元素"""
        for el in elements:
            if predicate(el):
                results.append(el)
            if el.children:
                self._search_elements(el.children, predicate, results)

    def summary(self, max_ocr_chars: int = 500) -> str:
        """生成可读的观测摘要（供 LLM prompt 使用）"""
        parts = []
        if self.active_window_title:
            parts.append(f"窗口: {self.active_window_title}")
        if self.browser_url:
            parts.append(f"URL: {self.browser_url}")
        if self.page_title:
            parts.append(f"页面: {self.page_title}")
        if self.screenshot_path:
            parts.append(f"截图: {self.screenshot_path}")
        if self.ocr_text:
            truncated = self.ocr_text[:max_ocr_chars]
            if len(self.ocr_text) > max_ocr_chars:
                truncated += "..."
            parts.append(f"屏幕文字: {truncated}")
        if self.accessibility_tree:
            parts.append(f"UI 元素: {len(self.accessibility_tree)} 个顶层节点")
        return "\n".join(parts)
