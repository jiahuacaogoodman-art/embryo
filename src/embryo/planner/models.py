"""TaskPlanner 数据模型"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class StepStatus(str, Enum):
    """步骤状态"""
    PENDING = "pending"        # 等待执行
    RUNNING = "running"        # 正在执行
    SUCCESS = "success"        # 执行成功
    FAILED = "failed"          # 执行失败
    SKIPPED = "skipped"        # 被跳过（动态重规划后判定不需要）
    BLOCKED = "blocked"        # 被阻塞（前置条件不满足）
    REPLANNED = "replanned"    # 已被重规划替换


@dataclass
class PlanStep:
    """单个计划步骤"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:6])
    index: int = 0
    description: str = ""          # 人类可读的步骤描述
    action: str = ""               # 要执行的动作类型 (click/type/screenshot/verify/etc)
    target: str = ""               # 操作目标描述（"登录按钮"、"用户名输入框"）
    parameters: dict[str, Any] = field(default_factory=dict)  # 动作参数
    expected_result: str = ""      # 预期结果描述（供验证用）
    verification: str = ""         # 验证方式（"ocr_check:登录成功" / "title_change" / "element_visible:首页"）
    precondition: str = ""         # 前置条件（"步骤5成功" / "界面显示登录页"）
    fallback: str = ""             # 失败后的备选策略
    status: StepStatus = StepStatus.PENDING
    result: str = ""               # 执行结果
    error: str = ""                # 失败原因
    attempts: int = 0              # 已尝试次数
    max_attempts: int = 3          # 最大尝试次数
    duration: float = 0.0          # 执行耗时
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "index": self.index,
            "description": self.description,
            "action": self.action,
            "target": self.target,
            "parameters": self.parameters,
            "expected_result": self.expected_result,
            "verification": self.verification,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "attempts": self.attempts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PlanStep":
        step = cls(
            id=data.get("id", ""),
            index=data.get("index", 0),
            description=data.get("description", ""),
            action=data.get("action", ""),
            target=data.get("target", ""),
            parameters=data.get("parameters", {}),
            expected_result=data.get("expected_result", ""),
            verification=data.get("verification", ""),
            precondition=data.get("precondition", ""),
            fallback=data.get("fallback", ""),
        )
        if "status" in data:
            step.status = StepStatus(data["status"])
        return step

    @property
    def is_done(self) -> bool:
        return self.status in (StepStatus.SUCCESS, StepStatus.SKIPPED, StepStatus.REPLANNED)

    @property
    def can_retry(self) -> bool:
        return self.status == StepStatus.FAILED and self.attempts < self.max_attempts


@dataclass
class TaskPlan:
    """完整的任务计划"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    task_description: str = ""     # 原始用户任务描述
    steps: list[PlanStep] = field(default_factory=list)
    status: str = "active"         # active / completed / failed / cancelled
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    replan_count: int = 0          # 被重规划次数
    context: dict[str, Any] = field(default_factory=dict)  # 规划时的上下文信息

    @property
    def current_step(self) -> Optional[PlanStep]:
        """获取当前需要执行的步骤"""
        for step in self.steps:
            if step.status == StepStatus.PENDING:
                return step
        return None

    @property
    def progress(self) -> str:
        """进度描述"""
        done = sum(1 for s in self.steps if s.is_done)
        total = len(self.steps)
        failed = sum(1 for s in self.steps if s.status == StepStatus.FAILED)
        return f"{done}/{total} 完成" + (f", {failed} 失败" if failed else "")

    @property
    def is_complete(self) -> bool:
        return all(s.is_done for s in self.steps)

    @property
    def has_failed(self) -> bool:
        """是否有不可重试的失败步骤"""
        return any(
            s.status == StepStatus.FAILED and not s.can_retry
            for s in self.steps
        )

    def get_completed_summary(self) -> str:
        """获取已完成步骤的摘要（供 LLM 重规划时参考）"""
        lines = []
        for s in self.steps:
            icon = {"success": "✓", "failed": "✗", "skipped": "⊘", "pending": "○", "running": "→"}.get(s.status.value, "?")
            lines.append(f"  {icon} [{s.index}] {s.description}")
            if s.status == StepStatus.FAILED and s.error:
                lines.append(f"      失败原因: {s.error}")
            if s.status == StepStatus.SUCCESS and s.result:
                lines.append(f"      结果: {s.result[:80]}")
        return "\n".join(lines)

    def insert_step_after(self, after_index: int, new_step: PlanStep):
        """在指定步骤后插入新步骤"""
        new_step.index = after_index + 1
        insert_pos = 0
        for i, s in enumerate(self.steps):
            if s.index == after_index:
                insert_pos = i + 1
                break

        self.steps.insert(insert_pos, new_step)
        # 重新编号后续步骤
        for i in range(insert_pos + 1, len(self.steps)):
            self.steps[i].index = self.steps[i - 1].index + 1
        self.updated_at = time.time()

    def mark_remaining_skipped(self, from_index: int, reason: str = ""):
        """将指定步骤之后的所有待定步骤标记为跳过"""
        for s in self.steps:
            if s.index > from_index and s.status == StepStatus.PENDING:
                s.status = StepStatus.SKIPPED
                s.error = reason or "前置步骤失败，后续跳过"
