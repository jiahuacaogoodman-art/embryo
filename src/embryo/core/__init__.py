"""Core 模块 - 核心基础设施

- trace: 任务执行 trace 记录（plan.json, steps.jsonl, screenshots, observations）
"""

from .trace import TaskTrace, StepRecord

__all__ = ["TaskTrace", "StepRecord"]
