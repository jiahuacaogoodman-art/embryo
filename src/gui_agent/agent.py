"""GUI Agent 主控调度器

串联所有模块，实现"感知—规划—执行—验证—纠错"的闭环流程。
这是整个系统的入口和核心调度逻辑。
"""

from __future__ import annotations

import time
import uuid
from typing import Optional

from loguru import logger

from .config import AgentConfig
from .environment.sandbox_manager import SandboxManager
from .executor.action_executor import ActionExecutor
from .locator.target_locator import TargetLocator
from .models import (
    Action,
    ActionType,
    ScreenState,
    Task,
    TaskStatus,
    TaskStep,
    VerificationStatus,
)
from .perception.perception_engine import PerceptionEngine
from .planner.action_planner import ActionPlanner
from .replanner.error_diagnoser import ErrorDiagnoser
from .replanner.replanner import Replanner
from .verifier.result_verifier import ResultVerifier


class GUIAgent:
    """GUI 智能体主控调度器

    闭环流程：
    1. 感知当前界面状态
    2. LLM 规划下一步动作
    3. 执行动作
    4. 验证执行结果
    5. 如果失败 → 诊断原因 → 重规划 → 重新执行
    6. 如果成功 → 继续下一步
    7. 任务完成或触发人工接管

    核心原则：
    - 每一步都要验证是否真的达到预期结果
    - 失败后分析原因并重新规划，而不是盲目重试
    - 风险升高时主动停止并请求人工接管
    """

    def __init__(self, config: Optional[AgentConfig] = None):
        """初始化 GUI Agent

        Args:
            config: 配置对象，为None时使用默认配置
        """
        self.config = config or AgentConfig()

        # 初始化各模块
        self.environment = SandboxManager(self.config.environment)
        self.perception = PerceptionEngine(self.config.perception)
        self.locator = TargetLocator(
            self.config.perception,
            self.perception.ocr_engine,
            self.perception.element_detector,
        )
        self.planner = ActionPlanner(self.config.llm)
        self.executor = ActionExecutor(self.config.execution)
        self.verifier = ResultVerifier(self.config.verification, self.perception)
        self.diagnoser = ErrorDiagnoser()
        self.replanner = Replanner(self.config.replanning)

        # 任务状态
        self._current_task: Optional[Task] = None
        self._is_running: bool = False

    def run_task(self, task_description: str, max_steps: int = 50) -> Task:
        """执行完整任务

        Args:
            task_description: 任务描述（自然语言）
            max_steps: 最大步骤数（防止无限循环）

        Returns:
            任务执行记录
        """
        logger.info(f"========== 开始任务: {task_description} ==========")

        # 创建任务
        task = Task(
            task_id=str(uuid.uuid4())[:8],
            description=task_description,
            status=TaskStatus.RUNNING,
        )
        self._current_task = task
        self._is_running = True

        # 启动隔离环境
        env_ready = self.environment.start()
        if not env_ready:
            logger.warning("隔离环境启动失败，使用本地模式继续")

        try:
            step_index = 0

            while self._is_running and step_index < max_steps:
                step_index += 1
                logger.info(f"---------- 步骤 {step_index}/{max_steps} ----------")

                # 执行一步闭环
                step_result = self._execute_one_step(task_description, step_index)
                task.steps.append(step_result)

                # 检查是否完成
                if step_result.action.action_type == ActionType.STOP:
                    task.status = TaskStatus.COMPLETED
                    logger.info("✓ 任务完成")
                    break

                # 检查是否需要人工接管
                if step_result.action.action_type == ActionType.ASK_HUMAN:
                    task.status = TaskStatus.PAUSED
                    logger.warning(f"⚠ 请求人工接管: {step_result.action.reason}")
                    break

            else:
                if step_index >= max_steps:
                    task.status = TaskStatus.FAILED
                    task.error_log.append(f"达到最大步骤数 {max_steps}")
                    logger.error(f"✗ 任务失败: 超过最大步骤数")

        except KeyboardInterrupt:
            task.status = TaskStatus.CANCELLED
            logger.info("任务被用户中断")

        except Exception as e:
            task.status = TaskStatus.FAILED
            task.error_log.append(str(e))
            logger.error(f"✗ 任务异常: {e}")

        finally:
            self._is_running = False
            self.environment.stop()

        logger.info(
            f"========== 任务结束: 状态={task.status.value}, "
            f"步骤数={len(task.steps)}, 重试={task.total_retries} =========="
        )
        return task

    def _execute_one_step(self, task_description: str, step_index: int) -> TaskStep:
        """执行一步完整闭环：感知→规划→执行→验证→(诊断→重规划)

        Args:
            task_description: 任务描述
            step_index: 当前步骤索引

        Returns:
            步骤记录
        """
        start_time = time.time()

        # 1. 感知
        logger.debug("阶段1: 界面感知")
        state_before = self.perception.perceive(
            save_screenshot=self.config.save_screenshots,
            screenshot_dir=self.config.screenshot_dir,
        )
        screenshot_before = self.perception.screen_capture.last_screenshot

        # 2. 规划
        logger.debug("阶段2: 动作规划")
        action = self.planner.plan_next_action(
            task_description=task_description,
            screen_state=state_before,
            history=self._current_task.steps if self._current_task else None,
        )

        # 对于终止类动作，无需执行和验证
        if action.action_type in (ActionType.STOP, ActionType.ASK_HUMAN):
            return TaskStep(
                step_index=step_index,
                action=action,
                screen_before=state_before,
                duration=time.time() - start_time,
            )

        # 3. 执行
        logger.debug(f"阶段3: 执行动作 {action.action_type.value}")
        exec_success = self.executor.execute(action)

        if not exec_success:
            logger.warning("动作执行失败（操作本身未完成）")

        # 4. 验证
        logger.debug("阶段4: 结果验证")
        verification = self.verifier.verify(
            action=action,
            state_before=state_before,
            screenshot_before=screenshot_before,
        )

        state_after = self.perception.last_state

        # 5. 根据验证结果决定是否需要诊断和重规划
        diagnosis = None
        if verification.status == VerificationStatus.FAILED:
            logger.warning(f"验证失败: {verification.message}")

            # 诊断
            logger.debug("阶段5: 错误诊断")
            diagnosis = self.diagnoser.diagnose(
                action=action,
                state_before=state_before,
                state_after=state_after or state_before,
                verification=verification,
            )
            logger.info(f"诊断结果: {diagnosis.failure_reason.value} - {diagnosis.description}")

            # 重规划
            strategy = self.replanner.replan(
                original_action=action,
                diagnosis=diagnosis,
                screen_state=state_after or state_before,
            )
            logger.info(f"重规划策略: {strategy.strategy_type} - {strategy.description}")

            # 如果重规划给出了新动作，执行它
            if strategy.new_action:
                action = strategy.new_action
                # 重新执行新动作
                self.executor.execute(action)
                self._current_task.total_retries += 1

        elif verification.status == VerificationStatus.SUCCESS:
            self.replanner.report_success()

        step = TaskStep(
            step_index=step_index,
            action=action,
            screen_before=state_before,
            screen_after=state_after,
            verification=verification,
            diagnosis=diagnosis,
            duration=time.time() - start_time,
        )

        return step

    def stop(self):
        """停止当前任务"""
        self._is_running = False
        if self._current_task:
            self._current_task.status = TaskStatus.CANCELLED

    @property
    def current_task(self) -> Optional[Task]:
        return self._current_task

    @property
    def is_running(self) -> bool:
        return self._is_running
