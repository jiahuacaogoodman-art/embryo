"""Planning Schema - Pydantic 模型定义

所有计划相关的数据结构都用 Pydantic 严格校验。
LLM 输出必须经过 repair → validate 管道才能进入执行器。

核心思想：
- ActionType 枚举限定合法动作类型
- VerificationType 枚举限定验证方式
- Target 定义操作目标（语义 > 坐标）
- PlanStep 是单个可执行步骤
- TaskPlan 是完整的任务计划
"""

from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class ActionType(str, Enum):
    """合法的动作类型"""

    OBSERVE = "observe"  # 截图观察
    CLICK = "click"
    TYPE_TEXT = "type_text"
    HOTKEY = "hotkey"
    SCROLL = "scroll"
    WAIT = "wait"
    VERIFY = "verify"
    PRESS_KEY = "press_key"
    MOUSE_MOVE = "mouse_move"
    FIND_TEXT = "find_text"


class TargetType(str, Enum):
    """目标定位类型（语义优先，坐标兜底）"""

    TEXT = "text"  # 按可见文字定位
    ROLE = "role"  # 按 UI 角色定位 (button, textfield, link)
    LABEL = "label"  # 按 label/aria-label 定位
    PLACEHOLDER = "placeholder"  # 按 placeholder 定位
    IMAGE = "image"  # 按图像匹配定位
    CSS_SELECTOR = "css_selector"  # CSS 选择器（浏览器）
    XPATH = "xpath"  # XPath（浏览器）
    COORDINATE = "coordinate"  # 坐标（最后 fallback）
    DESCRIPTION = "description"  # 自然语言描述（交给 resolver）


class Target(BaseModel):
    """操作目标定义

    Planner 生成语义化目标，TargetResolver 负责解析为具体坐标/元素。
    """

    type: TargetType = TargetType.DESCRIPTION
    value: str = ""
    # 坐标（仅 type=coordinate 时使用）
    x: int | None = None
    y: int | None = None
    # 额外匹配条件
    near_text: str = ""  # 目标附近应有的文字（辅助定位）
    index: int = 0  # 多个匹配时取第几个（0-based）


class VerificationType(str, Enum):
    """验证规则类型"""

    TEXT_VISIBLE = "text_visible"  # 指定文字出现在屏幕上
    TEXT_ABSENT = "text_absent"  # 指定文字不在屏幕上
    ELEMENT_VISIBLE = "element_visible"  # 指定 UI 元素可见
    ELEMENT_ABSENT = "element_absent"  # 指定 UI 元素消失
    URL_CONTAINS = "url_contains"  # 浏览器 URL 包含指定内容
    SCREENSHOT_CHANGED = "screenshot_changed"  # 截图有变化
    CUSTOM_LLM_JUDGE = "custom_llm_judge"  # LLM 判断


class VerificationRule(BaseModel):
    """单条验证规则"""

    type: VerificationType
    target: str = ""  # 验证目标（文字内容、元素名称、URL 片段等）
    timeout_sec: float = 5.0  # 超时时间
    description: str = ""  # 人类可读描述


class PlanStep(BaseModel):
    """单个计划步骤（Pydantic 严格校验版）

    每个步骤必须有明确的 action 和 target。
    verification 列表定义如何判断步骤是否成功。
    """

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    description: str  # 必须有步骤描述
    action: ActionType  # 必须是合法动作类型
    target: Target = Field(default_factory=Target)
    parameters: dict[str, Any] = Field(default_factory=dict)
    expected_result: str = ""
    verification: list[VerificationRule] = Field(default_factory=list)
    fallback: str = ""
    max_retries: int = 2
    depends_on: list[str] = Field(default_factory=list)  # 依赖的步骤 id

    @field_validator("description")
    @classmethod
    def description_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("步骤描述不能为空")
        return v.strip()

    @field_validator("action", mode="before")
    @classmethod
    def normalize_action(cls, v: str) -> str:
        """兼容旧格式：type → type_text, screenshot → observe"""
        mapping = {
            "type": "type_text",
            "screenshot": "observe",
            "ocr": "observe",
            "find_text": "find_text",
        }
        if isinstance(v, str):
            v = v.lower().strip()
            return mapping.get(v, v)
        return v


class TaskPlan(BaseModel):
    """完整的任务计划（Pydantic 版）"""

    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    task: str  # 原始任务描述
    steps: list[PlanStep]
    created_at: float = Field(default_factory=time.time)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("task")
    @classmethod
    def task_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("任务描述不能为空")
        return v.strip()

    @field_validator("steps")
    @classmethod
    def steps_not_empty(cls, v: list[PlanStep]) -> list[PlanStep]:
        if not v:
            raise ValueError("计划必须至少包含一个步骤")
        return v

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def get_step_by_id(self, step_id: str) -> PlanStep | None:
        for step in self.steps:
            if step.id == step_id:
                return step
        return None
