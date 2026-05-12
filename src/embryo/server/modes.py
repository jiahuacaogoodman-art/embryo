"""Execution Modes - 三种执行模式

Mode 1: Tool Mode
  外部系统直接调用 embryo.click / embryo.type_text 等单个工具。
  Embryo 不做规划，只执行单次动作。

Mode 2: Plan Mode
  用户给任务描述，Embryo 生成 plan 并自主执行全部步骤。
  执行过程中自动验证和重规划。

Mode 3: Supervised Mode
  与 Plan Mode 类似，但高风险步骤执行前需要用户确认。
  由 require_confirmation_for 配置哪些操作需要确认。
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Callable, Optional

from pydantic import BaseModel, Field


class ExecutionMode(str, Enum):
    """执行模式"""

    TOOL = "tool"  # 工具模式：单次调用，不做规划
    PLAN = "plan"  # 规划模式：自主规划和执行
    SUPERVISED = "supervised"  # 监督模式：高风险步骤需确认


class ModeConfig(BaseModel):
    """执行模式配置"""

    mode: ExecutionMode = ExecutionMode.SUPERVISED

    # Supervised 模式配置
    require_confirmation_for: list[str] = Field(
        default_factory=lambda: [
            "submit",
            "delete",
            "payment",
            "send_message",
            "send",
            "purchase",
            "transfer",
            "login",
            "logout",
        ]
    )

    # Plan 模式配置
    max_plan_steps: int = 50
    max_replan_count: int = 3
    auto_observe_before_action: bool = True

    # Tool 模式配置
    tool_mode_require_auth: bool = True

    def needs_confirmation(self, action: str, context: str = "") -> bool:
        """检查当前模式下，某个操作是否需要用户确认

        Args:
            action: 操作类型
            context: 操作上下文（目标描述等）

        Returns:
            True = 需要确认
        """
        if self.mode != ExecutionMode.SUPERVISED:
            return False

        # 检查 action 本身
        combined = f"{action} {context}".lower()
        for keyword in self.require_confirmation_for:
            if keyword.lower() in combined:
                return True

        return False


class ConfirmationRequest(BaseModel):
    """确认请求（发给用户）"""

    step_description: str
    action: str
    target: dict[str, Any] = Field(default_factory=dict)
    reason: str = ""  # 为什么需要确认
    risk_level: str = "medium"


class ConfirmationResponse(BaseModel):
    """确认响应（用户返回）"""

    approved: bool
    modified_params: dict[str, Any] = Field(default_factory=dict)  # 用户可修改参数
    message: str = ""
