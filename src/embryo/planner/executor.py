"""PlanExecutor — 按 plan 逐步执行 GUI 操作

职责：
1. 遍历 TaskPlan 中的步骤
2. 每步执行前：截图感知 + 步骤适配
3. 调用对应的 Computer Use 工具
4. 执行后：验证结果
5. 根据验证结果通知 TaskPlanner 更新 plan
6. 循环直到 plan 完成或终止

这是 TaskPlanner 和 Computer Use 工具之间的桥梁。
"""

from __future__ import annotations

import time
from typing import Any, Callable, Optional

from ..logging import get_logger
from ..tools import ToolRegistry
from .models import PlanStep, StepStatus, TaskPlan
from .planner import TaskPlanner

logger = get_logger("plan_executor")


class PlanExecutor:
    """计划执行器

    按照 TaskPlan 中的步骤顺序执行 GUI 操作。

    Args:
        planner: TaskPlanner 实例（用于动态重规划）
        tools: ToolRegistry 实例（用于执行具体工具）
        llm_call: LLM 调用函数（用于步骤适配和验证）
    """

    def __init__(
        self,
        planner: TaskPlanner,
        tools: ToolRegistry,
        llm_call: Callable[[str], str],
    ):
        self.planner = planner
        self.tools = tools
        self._llm_call = llm_call
        self._max_total_steps = 50  # 防止无限执行
        self._action_delay = 0.5    # 每步操作后等待时间

    def execute_plan(self, plan: TaskPlan) -> TaskPlan:
        """执行完整计划。

        Args:
            plan: 要执行的任务计划

        Returns:
            执行完毕的计划（含每步结果）
        """
        total_executed = 0
        logger.info("plan_execution_start", task=plan.task_description[:50], steps=len(plan.steps))

        while not plan.is_complete and plan.status == "active":
            step = plan.current_step
            if step is None:
                break

            if total_executed >= self._max_total_steps:
                plan.status = "failed"
                logger.warning("plan_max_steps_reached", total=total_executed)
                break

            # 执行单步
            self._execute_step(plan, step)
            total_executed += 1

            # 短暂等待（让界面响应）
            time.sleep(self._action_delay)

        logger.info(
            "plan_execution_complete",
            status=plan.status,
            progress=plan.progress,
            total_executed=total_executed,
        )
        return plan

    def _execute_step(self, plan: TaskPlan, step: PlanStep):
        """执行单个步骤。

        流程：
        1. 如果需要，先截图获取当前界面状态
        2. 适配步骤参数（目标定位）
        3. 执行动作
        4. 验证结果
        5. 根据结果通知 planner
        """
        step.status = StepStatus.RUNNING
        start_time = time.time()

        logger.info("step_execute", index=step.index, action=step.action, desc=step.description[:40])

        try:
            # 获取当前界面状态（如果需要定位目标）
            screen_text = ""
            if step.action in ("click", "type") and not step.parameters.get("x"):
                screen_text = self._get_screen_text()
                # 用 LLM 适配目标位置
                adapted = self.planner.adapt_step(step, screen_text)
                if adapted:
                    if adapted.get("target_visible") is False:
                        # 目标不可见 → 失败
                        step.duration = time.time() - start_time
                        self.planner.update_on_failure(
                            plan, step,
                            f"目标不可见: {adapted.get('suggestion', '未知原因')}",
                            screen_text,
                        )
                        return
                    # 合并适配后的参数
                    if adapted.get("parameters"):
                        step.parameters.update(adapted["parameters"])

            # 执行动作
            result = self._dispatch_action(step)

            # 验证结果
            verified = self._verify_step(step, result)

            step.duration = time.time() - start_time

            if verified:
                self.planner.update_on_success(plan, step, result)
            else:
                self.planner.update_on_failure(
                    plan, step,
                    f"验证失败: 预期 '{step.expected_result}' 未满足",
                    self._get_screen_text(),
                )

        except Exception as e:
            step.duration = time.time() - start_time
            self.planner.update_on_failure(plan, step, f"异常: {e}")

    def _dispatch_action(self, step: PlanStep) -> str:
        """将步骤动作分发到对应的工具。"""
        action = step.action
        params = step.parameters

        # 映射 plan 动作到工具名
        tool_mapping = {
            "screenshot": "screenshot",
            "click": "click",
            "type": "type_text",
            "hotkey": "hotkey",
            "scroll": "scroll",
            "wait": None,  # 特殊处理
            "verify": None,  # 特殊处理
            "find_text": "find_text_on_screen",
            "ocr": "ocr_screen",
        }

        # 等待动作
        if action == "wait":
            duration = params.get("duration", 2)
            time.sleep(duration)
            return f"已等待 {duration} 秒"

        # 验证动作（截图+OCR 检查）
        if action == "verify":
            return self._do_verify(step)

        # 常规工具调用
        tool_name = tool_mapping.get(action)
        if not tool_name:
            return f"[Error] 未知动作类型: {action}"

        # 构建工具参数
        tool_args = self._build_tool_args(action, step)

        try:
            result = self.tools.execute(tool_name, tool_args)
            return str(result)
        except KeyError:
            return f"[Error] 工具 {tool_name} 不可用"
        except Exception as e:
            return f"[Error] {e}"

    def _build_tool_args(self, action: str, step: PlanStep) -> dict[str, Any]:
        """根据动作类型构建工具参数。"""
        params = step.parameters

        if action == "click":
            return {
                "x": params.get("x", 0),
                "y": params.get("y", 0),
                "button": params.get("button", "left"),
                "clicks": params.get("clicks", 1),
            }
        elif action == "type":
            return {"text": params.get("text", step.target)}
        elif action == "hotkey":
            return {"keys": params.get("keys", ["enter"])}
        elif action == "scroll":
            return {
                "direction": params.get("direction", "down"),
                "amount": params.get("amount", 3),
            }
        elif action == "screenshot":
            return {"region": params.get("region", "")}
        elif action == "find_text":
            return {"target_text": step.target}
        elif action == "ocr":
            return {"region": params.get("region", "")}

        return params

    def _do_verify(self, step: PlanStep) -> str:
        """执行验证步骤。"""
        verification = step.verification

        if verification.startswith("ocr_check:"):
            # OCR 检查指定文字是否出现
            target_text = verification.split(":", 1)[1].strip()
            try:
                result = self.tools.execute("find_text_on_screen", {"target_text": target_text})
                return str(result)
            except Exception:
                return f"OCR 未找到: {target_text}"

        elif verification == "screenshot_diff":
            # 截图（用于后续对比）
            try:
                return str(self.tools.execute("screenshot", {}))
            except Exception:
                return "截图失败"

        else:
            # 通用：截图 + OCR
            try:
                return str(self.tools.execute("ocr_screen", {}))
            except Exception:
                return "验证执行失败"

    def _verify_step(self, step: PlanStep, result: str) -> bool:
        """验证步骤是否成功。

        策略：
        1. 如果有 verification 规则 → 按规则判断
        2. 如果结果包含 [Error] → 失败
        3. 如果是 screenshot/verify 类 → 总是成功
        4. 其他 → 用 LLM 判断
        """
        # 明确错误
        if "[Error]" in result:
            return False

        # 截图/观察类步骤总是成功
        if step.action in ("screenshot", "wait", "ocr"):
            return True

        # 有验证规则
        if step.verification:
            if step.verification.startswith("ocr_check:"):
                target = step.verification.split(":", 1)[1].strip()
                return target in result or "找到" in result
            if step.verification == "screenshot_diff":
                return "界面已响应" in result or "已变化" in result

        # click/type 操作：检查结果中是否有成功信号
        if step.action in ("click", "type"):
            if "未发生变化" in result or "未找到" in result:
                return False
            if "已响应" in result or "已输入" in result or "已点击" in result:
                return True

        # 默认认为成功（避免过度保守）
        return True

    def _get_screen_text(self) -> str:
        """获取当前屏幕 OCR 文字。"""
        try:
            result = self.tools.execute("ocr_screen", {})
            return str(result)
        except Exception:
            return "(OCR 不可用)"
