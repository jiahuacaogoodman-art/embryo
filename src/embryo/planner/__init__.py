"""TaskPlanner — 动态 Todo List 任务规划器

这是 Embryo 区别于 OpenClaw/Hermes 的核心差异化模块。

核心理念：AI 应该有"脑子"
- 不是每步问一次 LLM "下一步干啥"（响应式）
- 而是先分析出完整的 todo list，然后逐步执行
- 每步执行后根据反馈动态更新 plan（插入/修改/跳过/重新规划）

架构：
    TaskPlanner: 初始规划 + 动态更新 plan
    PlanExecutor: 按 plan 逐步执行 + 调用 Computer Use
    PlanStep: 单步数据结构（状态/前置条件/验证方式）
"""

from .planner import TaskPlanner
from .executor import PlanExecutor
from .models import TaskPlan, PlanStep, StepStatus

__all__ = ["TaskPlanner", "PlanExecutor", "TaskPlan", "PlanStep", "StepStatus"]
