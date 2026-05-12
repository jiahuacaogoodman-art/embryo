"""TaskPlanner — 初始规划 + 动态重规划

职责：
1. 接收用户任务 → 调用 LLM 生成结构化 todo list (TaskPlan)
2. 执行过程中接收反馈 → 动态更新 plan (插入/修改/跳过步骤)
3. 关键失败时触发全量重规划（保留已成功步骤）
"""

from __future__ import annotations

import json
import re
from typing import Any, Callable, Optional

from ..logging import get_logger
from .models import PlanStep, StepStatus, TaskPlan

logger = get_logger("task_planner")

# === LLM Prompts ===

PLAN_GENERATION_PROMPT = """\
你是一个 GUI 操作任务规划器。给定用户的任务描述，生成一个结构化的执行计划。

## 任务
{task_description}

## 当前界面状态
{screen_state}

## 要求
将任务拆解为具体的 GUI 操作步骤。每个步骤必须是一个具体可执行的动作。

输出严格的 JSON 数组，每个元素格式如下：
{{
  "description": "步骤的人类可读描述",
  "action": "动作类型: screenshot/click/type/hotkey/scroll/wait/verify",
  "target": "操作目标描述",
  "parameters": {{}},
  "expected_result": "执行后预期看到什么",
  "verification": "如何验证成功: ocr_check:文字 / screenshot_diff / title_change",
  "fallback": "失败后备选方案"
}}

## 动作类型说明
- screenshot: 截图观察当前界面
- click: 点击指定目标
- type: 在当前焦点输入文字
- hotkey: 按快捷键
- scroll: 滚动页面
- wait: 等待指定时间
- verify: 验证当前状态是否符合预期

## 规则
1. 第一步总是 screenshot（先观察再操作）
2. 每个 click/type 操作前应有 screenshot 或 verify 确认目标存在
3. 关键操作后加 verify 步骤
4. 不要一步做太多事，拆细
5. 只输出 JSON 数组，不要解释

输出:"""

REPLAN_PROMPT = """\
你是一个 GUI 操作任务规划器。当前计划执行到一半遇到了问题，需要调整后续步骤。

## 原始任务
{task_description}

## 当前计划执行情况
{plan_summary}

## 当前界面状态
{screen_state}

## 失败信息
步骤 [{failed_step_index}] "{failed_step_desc}" 失败。
原因: {failure_reason}

## 要求
基于当前实际情况，输出**修改后的后续步骤**（从失败步骤开始）。
已成功的步骤不要重复。

输出格式同上（JSON 数组）。只输出需要执行的剩余步骤。"""

STEP_ADAPT_PROMPT = """\
GUI 操作步骤需要适配。当前步骤描述: "{step_description}"

当前界面 OCR 识别到的文字:
{screen_text}

请判断:
1. 目标是否在当前界面可见？
2. 如果可见，给出具体的操作参数（坐标或文字）
3. 如果不可见，建议怎么做（滚动？等待？换页面？）

输出 JSON:
{{
  "target_visible": true/false,
  "action": "具体动作",
  "parameters": {{}},
  "suggestion": "如果不可见的建议"
}}"""


class TaskPlanner:
    """任务规划器

    核心方法:
    - create_plan(): 从任务描述生成初始 plan
    - update_on_success(): 步骤成功后更新
    - update_on_failure(): 步骤失败后决定重试/修改/重规划
    - replan(): 全量重规划后续步骤
    """

    def __init__(self, llm_call: Callable[[str], str]):
        """
        Args:
            llm_call: LLM 调用函数 (prompt → response_text)
        """
        self._llm_call = llm_call

    def create_plan(self, task_description: str, screen_state: str = "") -> TaskPlan:
        """从任务描述创建初始执行计划。

        Args:
            task_description: 用户的自然语言任务描述
            screen_state: 当前界面状态描述（OCR 文字等）

        Returns:
            结构化的 TaskPlan
        """
        prompt = PLAN_GENERATION_PROMPT.format(
            task_description=task_description,
            screen_state=screen_state or "(尚未截图，界面状态未知)",
        )

        raw = self._llm_call(prompt)
        steps = self._parse_steps(raw)

        plan = TaskPlan(
            task_description=task_description,
            steps=steps,
            context={"initial_screen": screen_state},
        )

        logger.info("plan_created", task=task_description[:50], steps=len(steps))
        return plan

    def update_on_success(self, plan: TaskPlan, step: PlanStep, result: str):
        """步骤执行成功后更新 plan。

        Args:
            plan: 当前计划
            step: 成功的步骤
            result: 执行结果
        """
        step.status = StepStatus.SUCCESS
        step.result = result
        plan.updated_at = __import__("time").time()

        logger.info("step_success", step_index=step.index, desc=step.description[:30])

        # 检查是否所有步骤完成
        if plan.is_complete:
            plan.status = "completed"
            logger.info("plan_completed", task=plan.task_description[:50])

    def update_on_failure(
        self,
        plan: TaskPlan,
        step: PlanStep,
        error: str,
        screen_state: str = "",
    ) -> str:
        """步骤执行失败后决定下一步行动。

        Args:
            plan: 当前计划
            step: 失败的步骤
            error: 失败原因
            screen_state: 当前界面状态

        Returns:
            决策: "retry" / "replan" / "abort"
        """
        step.status = StepStatus.FAILED
        step.error = error
        step.attempts += 1
        plan.updated_at = __import__("time").time()

        logger.warning(
            "step_failed",
            step_index=step.index,
            desc=step.description[:30],
            error=error[:50],
            attempts=step.attempts,
        )

        # 决策逻辑
        if step.can_retry:
            # 还有重试次数 → 重试（可能先插入修复步骤）
            step.status = StepStatus.PENDING  # 重置为待执行
            if "弹窗" in error or "遮挡" in error:
                # 插入处理弹窗的步骤
                fix_step = PlanStep(
                    description="处理弹窗：尝试关闭遮挡的弹窗",
                    action="hotkey",
                    target="关闭弹窗",
                    parameters={"keys": ["escape"]},
                    expected_result="弹窗关闭",
                    verification="screenshot_diff",
                )
                plan.insert_step_after(step.index - 1, fix_step)
                logger.info("step_inserted", desc="处理弹窗", before_step=step.index)
            return "retry"

        # 重试次数耗尽
        if plan.replan_count < 3:
            # 触发重规划
            self.replan(plan, step, error, screen_state)
            return "replan"

        # 重规划次数也耗尽 → 放弃
        plan.status = "failed"
        plan.mark_remaining_skipped(step.index, "重规划次数耗尽")
        return "abort"

    def replan(
        self,
        plan: TaskPlan,
        failed_step: PlanStep,
        failure_reason: str,
        screen_state: str = "",
    ):
        """全量重规划后续步骤。

        保留已成功的步骤，从失败点开始重新生成后续计划。
        """
        plan.replan_count += 1

        prompt = REPLAN_PROMPT.format(
            task_description=plan.task_description,
            plan_summary=plan.get_completed_summary(),
            screen_state=screen_state or "(未知)",
            failed_step_index=failed_step.index,
            failed_step_desc=failed_step.description,
            failure_reason=failure_reason,
        )

        raw = self._llm_call(prompt)
        new_steps = self._parse_steps(raw)

        if not new_steps:
            logger.warning("replan_failed_no_steps")
            return

        # 标记旧的待定步骤为 REPLANNED
        for s in plan.steps:
            if s.index >= failed_step.index and s.status == StepStatus.PENDING:
                s.status = StepStatus.REPLANNED

        # 追加新步骤（编号从失败步骤开始）
        start_index = failed_step.index
        for i, new_step in enumerate(new_steps):
            new_step.index = start_index + i
            plan.steps.append(new_step)

        plan.updated_at = __import__("time").time()
        logger.info("plan_replanned", new_steps=len(new_steps), replan_count=plan.replan_count)

    def adapt_step(self, step: PlanStep, screen_text: str) -> Optional[dict[str, Any]]:
        """根据实际界面状态适配步骤参数。

        当步骤的 target 是描述性的（"登录按钮"）时，
        用 LLM 结合当前 OCR 结果确定具体操作参数。

        Args:
            step: 要适配的步骤
            screen_text: 当前界面 OCR 文字

        Returns:
            适配后的参数字典，或 None（无法适配）
        """
        prompt = STEP_ADAPT_PROMPT.format(
            step_description=step.description,
            screen_text=screen_text[:1000],
        )

        try:
            raw = self._llm_call(prompt)
            data = self._parse_json(raw)
            return data
        except Exception as e:
            logger.warning("step_adapt_failed", step=step.description[:30], error=str(e))
            return None

    def _parse_steps(self, raw: str) -> list[PlanStep]:
        """从 LLM 输出解析步骤列表。"""
        data = self._parse_json_array(raw)
        if not data:
            return []

        steps = []
        for i, item in enumerate(data):
            if not isinstance(item, dict):
                continue
            step = PlanStep(
                index=i + 1,
                description=item.get("description", f"步骤{i+1}"),
                action=item.get("action", ""),
                target=item.get("target", ""),
                parameters=item.get("parameters", {}),
                expected_result=item.get("expected_result", ""),
                verification=item.get("verification", ""),
                fallback=item.get("fallback", ""),
            )
            steps.append(step)

        return steps

    def _parse_json(self, raw: str) -> Optional[dict]:
        """从 LLM 输出中提取 JSON 对象。"""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:raw.rfind("```")]

        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            try:
                return json.loads(raw[start:end+1])
            except json.JSONDecodeError:
                pass
        return None

    def _parse_json_array(self, raw: str) -> list:
        """从 LLM 输出中提取 JSON 数组。"""
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:raw.rfind("```")]

        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1:
            try:
                return json.loads(raw[start:end+1])
            except json.JSONDecodeError:
                pass

        # 尝试逐行解析
        logger.warning("json_array_parse_failed", raw_length=len(raw))
        return []
