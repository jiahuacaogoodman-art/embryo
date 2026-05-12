"""ReAct Loop - 短视高频决策循环

核心思路（对标 OpenClaw）：
- 不做长 plan，每步看一次截图、选一个动作
- 循环：observe → annotate (SoM) → think (Vision LLM) → act (Backend) → repeat
- 直到 LLM 判断任务完成（is_done）或失败（is_failed）或超步数

为什么比 plan-then-execute 更可靠：
- GUI 状态每次操作后都变，5 步之后的 plan 基本作废
- 每步都看真实截图决策，不会基于过期状态操作
- LLM 能通过截图自然验证上一步是否成功

循环结构：
    while not done and step < max_steps:
        screenshot = backend.screenshot()
        som_result = annotator.annotate(screenshot, ocr_boxes)
        decision = vision.decide_action(som_result.annotated_image, task, history)
        if decision.is_done: break
        if decision.is_failed: break
        result = execute(decision, som_result)
        history.append(result)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

from ..action.backend import ActionResult, ActionStatus, ComputerBackend
from ..core.trace import StepRecord, TaskTrace
from ..logging import get_logger
from ..perception.observation import Observation
from ..perception.observer import Observer, ObserverConfig
from ..perception.som import SoMAnnotator, SoMElement, SoMResult
from ..vision.llm import ActionDecision, VisionConfig, VisionLLM

logger = get_logger(__name__)


# ============================================================
# 数据模型
# ============================================================


class LoopStatus(str, Enum):
    """循环结束状态"""
    SUCCESS = "success"  # LLM 判断任务完成
    FAILED = "failed"  # LLM 判断任务失败
    MAX_STEPS = "max_steps"  # 超过最大步数
    ERROR = "error"  # 系统错误
    ABORTED = "aborted"  # 外部终止


@dataclass
class StepOutcome:
    """单步执行结果"""
    step_index: int
    decision: ActionDecision
    action_result: Optional[ActionResult] = None
    target_element: Optional[SoMElement] = None
    screenshot_path: str = ""
    som_path: str = ""
    duration_ms: int = 0
    error: str = ""

    @property
    def success(self) -> bool:
        if self.action_result:
            return self.action_result.success
        return self.decision.action in ("done", "wait")

    def to_history_line(self) -> str:
        """生成供 LLM 下一步参考的历史记录"""
        action = self.decision.action
        target = self.decision.target_text or f"element[{self.decision.target_id}]"

        if action == "click":
            result = "成功" if self.success else "失败"
            return f"点击 {target} → {result}"
        elif action == "type":
            text = self.decision.parameters.get("text", "")[:30]
            return f"输入 '{text}' → {'成功' if self.success else '失败'}"
        elif action == "scroll":
            direction = self.decision.parameters.get("direction", "down")
            return f"滚动 {direction}"
        elif action == "hotkey":
            keys = self.decision.parameters.get("keys", [])
            return f"快捷键 {'+'.join(keys)}"
        elif action == "wait":
            return "等待"
        else:
            return f"{action} {target}"


@dataclass
class LoopResult:
    """ReAct Loop 完整执行结果"""
    status: LoopStatus
    task: str
    steps: list[StepOutcome] = field(default_factory=list)
    total_duration_sec: float = 0.0
    failure_reason: str = ""
    trace_id: str = ""

    @property
    def step_count(self) -> int:
        return len(self.steps)

    @property
    def success(self) -> bool:
        return self.status == LoopStatus.SUCCESS

    def summary(self) -> str:
        status_icon = {"success": "✓", "failed": "✗", "max_steps": "⏱", "error": "⚠"}.get(
            self.status.value, "?"
        )
        return (
            f"[{status_icon}] {self.task[:60]} | "
            f"{self.step_count} steps | {self.total_duration_sec:.1f}s"
        )


# ============================================================
# ReAct Loop 配置
# ============================================================


@dataclass
class ReactConfig:
    """ReAct Loop 配置"""
    max_steps: int = 30  # 最大步数
    action_delay_sec: float = 0.5  # 每步操作后等待时间
    screenshot_delay_sec: float = 0.3  # 截图前等待（让界面渲染）
    enable_som: bool = True  # 是否启用 SoM 标注
    enable_trace: bool = True  # 是否记录 trace
    traces_dir: Path = field(default_factory=lambda: Path.home() / ".embryo" / "traces")
    confirm_fn: Optional[Callable[[ActionDecision], bool]] = None  # supervised 模式确认回调
    # 高风险关键词（supervised 模式需要确认）
    confirm_keywords: list[str] = field(default_factory=lambda: [
        "submit", "delete", "payment", "send", "purchase", "transfer",
    ])


# ============================================================
# ReAct Loop 主类
# ============================================================


class ReactLoop:
    """ReAct 决策循环

    核心执行引擎。每步：
    1. 截图
    2. SoM 标注（给元素编号）
    3. 发给 Vision LLM 决策下一步动作
    4. 执行动作
    5. 记录 trace
    6. 重复直到完成/失败/超步数

    用法：
        loop = ReactLoop(backend=my_backend, vision=my_vision)
        result = loop.run("在浏览器中登录 example.com")
        if result.success:
            print("任务完成")
    """

    def __init__(
        self,
        backend: ComputerBackend,
        vision: VisionLLM,
        config: Optional[ReactConfig] = None,
        annotator: Optional[SoMAnnotator] = None,
    ):
        self._backend = backend
        self._vision = vision
        self._config = config or ReactConfig()
        self._annotator = annotator or SoMAnnotator()
        self._observer = Observer(backend, ObserverConfig(enable_ocr=True, enable_screenshot=True))

    def run(self, task: str) -> LoopResult:
        """执行 ReAct 循环

        Args:
            task: 用户任务描述（自然语言）

        Returns:
            LoopResult 包含执行结果和所有步骤
        """
        start_time = time.time()
        history: list[str] = []
        steps: list[StepOutcome] = []
        trace: Optional[TaskTrace] = None

        # 初始化 trace
        if self._config.enable_trace:
            trace = TaskTrace(
                traces_dir=self._config.traces_dir,
                task_description=task,
            )

        logger.info("react_loop_start", task=task[:80], max_steps=self._config.max_steps)

        status = LoopStatus.MAX_STEPS
        failure_reason = ""

        try:
            for step_idx in range(1, self._config.max_steps + 1):
                outcome = self._execute_one_step(task, history, step_idx, trace)
                steps.append(outcome)

                # 检查终止条件
                if outcome.decision.is_done:
                    status = LoopStatus.SUCCESS
                    logger.info("react_loop_done", step=step_idx, reason="task_complete")
                    break

                if outcome.decision.is_failed:
                    status = LoopStatus.FAILED
                    failure_reason = outcome.decision.failure_reason
                    logger.warning("react_loop_failed", step=step_idx, reason=failure_reason)
                    break

                # 添加到历史
                history.append(outcome.to_history_line())

                # 步间延迟
                time.sleep(self._config.action_delay_sec)

        except KeyboardInterrupt:
            status = LoopStatus.ABORTED
            failure_reason = "用户中断"
        except Exception as e:
            status = LoopStatus.ERROR
            failure_reason = f"系统错误: {e}"
            logger.error("react_loop_error", error=str(e))

        total_duration = time.time() - start_time

        # 最终化 trace
        if trace:
            trace.finalize(
                success=(status == LoopStatus.SUCCESS),
                summary=failure_reason or "任务完成",
                metrics={
                    "total_steps": len(steps),
                    "status": status.value,
                },
            )

        result = LoopResult(
            status=status,
            task=task,
            steps=steps,
            total_duration_sec=total_duration,
            failure_reason=failure_reason,
            trace_id=trace.task_id if trace else "",
        )

        logger.info(
            "react_loop_complete",
            status=status.value,
            steps=len(steps),
            duration=f"{total_duration:.1f}s",
        )

        return result

    def _execute_one_step(
        self,
        task: str,
        history: list[str],
        step_idx: int,
        trace: Optional[TaskTrace],
    ) -> StepOutcome:
        """执行循环中的一步

        流程：截图 → SoM → Vision LLM → 执行动作
        """
        step_start = time.time()

        # 1. 等待界面稳定 + 截图
        time.sleep(self._config.screenshot_delay_sec)
        screenshot_result = self._backend.screenshot()
        screenshot_path = screenshot_result.metadata.get("path", "")

        if not screenshot_path:
            return StepOutcome(
                step_index=step_idx,
                decision=ActionDecision(action="fail", is_failed=True, failure_reason="截图失败"),
                error="截图失败",
            )

        # 2. SoM 标注
        som_result: Optional[SoMResult] = None
        som_path = ""
        elements_summary = ""

        if self._config.enable_som:
            try:
                # 用 OCR 获取文字框
                obs = self._observer.observe()
                if obs.ocr_boxes:
                    som_result = self._annotator.annotate_from_ocr(screenshot_path, obs.ocr_boxes)
                else:
                    # 没有 OCR 结果，让 Vision LLM 先分析一次
                    analysis = self._vision.analyze_screen(screenshot_path)
                    if analysis.elements:
                        som_result = self._annotator.annotate_from_analysis(screenshot_path, analysis)

                if som_result:
                    som_path = som_result.annotated_image_path
                    elements_summary = som_result.elements_summary()
            except Exception as e:
                logger.warning("som_annotation_failed", error=str(e))

        # 用于 Vision LLM 的图片：优先 SoM 标注图，否则原始截图
        decision_image = som_path if som_path else screenshot_path

        # 3. Vision LLM 决策
        decision = self._vision.decide_action(
            image_path=decision_image,
            task=task,
            history=history,
            elements_summary=elements_summary,
        )

        # 4. Supervised 模式确认
        if self._config.confirm_fn and self._needs_confirmation(decision):
            approved = self._config.confirm_fn(decision)
            if not approved:
                decision = ActionDecision(
                    action="fail",
                    is_failed=True,
                    failure_reason="用户拒绝执行此操作",
                )

        # 5. 执行动作
        action_result = None
        target_element = None

        if not decision.is_done and not decision.is_failed:
            target_element, action_result = self._execute_action(decision, som_result)

        duration_ms = int((time.time() - step_start) * 1000)

        outcome = StepOutcome(
            step_index=step_idx,
            decision=decision,
            action_result=action_result,
            target_element=target_element,
            screenshot_path=screenshot_path,
            som_path=som_path,
            duration_ms=duration_ms,
        )

        # 6. 记录 trace
        if trace:
            record = StepRecord(
                step_id=f"s{step_idx}",
                action=decision.action,
                target={
                    "id": decision.target_id,
                    "text": decision.target_text,
                },
                resolved_target={
                    "x": target_element.cx if target_element else 0,
                    "y": target_element.cy if target_element else 0,
                    "source": "som" if target_element else "none",
                },
                parameters=decision.parameters,
                result=action_result.status.value if action_result else decision.action,
                result_message=action_result.message if action_result else "",
                duration_ms=duration_ms,
                metadata={"reasoning": decision.reasoning},
            )
            trace.record_step(record)

            if screenshot_path:
                trace.save_screenshot(screenshot_path, f"{step_idx:03d}_screen")
            if som_path:
                trace.save_screenshot(som_path, f"{step_idx:03d}_som")

        logger.info(
            "react_step",
            step=step_idx,
            action=decision.action,
            target_id=decision.target_id,
            reasoning=decision.reasoning[:60],
            duration_ms=duration_ms,
        )

        return outcome

    def _execute_action(
        self,
        decision: ActionDecision,
        som_result: Optional[SoMResult],
    ) -> tuple[Optional[SoMElement], Optional[ActionResult]]:
        """将 LLM 决策转化为 backend 调用

        通过 SoM 编号查表得到坐标，然后调用 backend。
        """
        target_element: Optional[SoMElement] = None
        action = decision.action
        params = decision.parameters

        # 通过 SoM 编号定位目标
        if decision.target_id > 0 and som_result:
            target_element = som_result.get_element(decision.target_id)

        if action == "click":
            if target_element:
                result = self._backend.click(x=target_element.cx, y=target_element.cy)
            elif "x" in params and "y" in params:
                result = self._backend.click(x=params["x"], y=params["y"])
            else:
                result = ActionResult(
                    status=ActionStatus.TARGET_NOT_FOUND,
                    message=f"无法定位点击目标: target_id={decision.target_id}",
                )
            return target_element, result

        elif action == "type":
            text = params.get("text", "")
            if not text:
                return None, ActionResult(status=ActionStatus.FAILED, message="type 动作缺少 text 参数")
            # 如果有目标，先点击获取焦点
            if target_element:
                self._backend.click(x=target_element.cx, y=target_element.cy)
                time.sleep(0.2)
            result = self._backend.type_text(text=text)
            return target_element, result

        elif action == "scroll":
            direction = params.get("direction", "down")
            amount = params.get("amount", 3)
            result = self._backend.scroll(direction=direction, amount=amount)
            return None, result

        elif action == "hotkey":
            keys = params.get("keys", [])
            if not keys:
                return None, ActionResult(status=ActionStatus.FAILED, message="hotkey 动作缺少 keys")
            result = self._backend.hotkey(keys=keys)
            return None, result

        elif action == "wait":
            seconds = params.get("seconds", 2)
            time.sleep(seconds)
            return None, ActionResult(status=ActionStatus.SUCCESS, message=f"等待 {seconds} 秒")

        else:
            return None, ActionResult(
                status=ActionStatus.FAILED,
                message=f"未知动作类型: {action}",
            )

    def _needs_confirmation(self, decision: ActionDecision) -> bool:
        """判断是否需要用户确认（supervised 模式）"""
        combined = f"{decision.action} {decision.target_text} {decision.reasoning}".lower()
        for keyword in self._config.confirm_keywords:
            if keyword.lower() in combined:
                return True
        return False
