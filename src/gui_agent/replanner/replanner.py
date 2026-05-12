"""动态重规划模块

根据错误诊断结果生成新的操作策略，实现当前任务内的自适应纠错。
这里的"学习"不是训练大模型，而是基于当前任务反馈的在线纠错能力。
"""

from __future__ import annotations

import time
from typing import Optional

from loguru import logger

from ..config import ReplanningConfig
from ..models import (
    Action,
    ActionType,
    Diagnosis,
    FailureReason,
    ReplanStrategy,
    RiskLevel,
    ScreenState,
)


class Replanner:
    """动态重规划器

    根据错误诊断结果调整操作策略：
    - 调整坐标
    - 延长等待
    - 切换识别方式
    - 修改操作顺序
    - 升级为人工接管
    """

    def __init__(self, config: ReplanningConfig):
        self.config = config
        self._consecutive_failures: int = 0
        self._retry_counts: dict[str, int] = {}  # action_key -> retry_count

    def replan(
        self,
        original_action: Action,
        diagnosis: Diagnosis,
        screen_state: ScreenState,
    ) -> ReplanStrategy:
        """根据诊断结果生成重规划策略

        Args:
            original_action: 失败的原始动作
            diagnosis: 错误诊断结果
            screen_state: 当前界面状态

        Returns:
            重规划策略
        """
        self._consecutive_failures += 1
        action_key = f"{original_action.action_type.value}_{original_action.target}"
        self._retry_counts[action_key] = self._retry_counts.get(action_key, 0) + 1

        logger.info(
            f"生成重规划策略: 失败原因={diagnosis.failure_reason.value}, "
            f"连续失败={self._consecutive_failures}, "
            f"当前动作重试={self._retry_counts[action_key]}"
        )

        # 连续失败次数过多，直接升级为人工接管
        if self._consecutive_failures >= self.config.max_consecutive_failures:
            return self._escalate_to_human("连续失败次数过多，风险升高")

        # 单步重试过多
        if self._retry_counts[action_key] > self.config.max_retries:
            return self._try_alternative_path(original_action, screen_state)

        # 根据不同失败原因选择策略
        strategy_map = {
            FailureReason.COORDINATE_OFFSET: self._handle_coordinate_offset,
            FailureReason.ELEMENT_NOT_LOADED: self._handle_element_not_loaded,
            FailureReason.FOCUS_ERROR: self._handle_focus_error,
            FailureReason.POPUP_BLOCKING: self._handle_popup_blocking,
            FailureReason.NETWORK_DELAY: self._handle_network_delay,
            FailureReason.OCR_FAILURE: self._handle_ocr_failure,
            FailureReason.STATE_MISMATCH: self._handle_state_mismatch,
            FailureReason.ELEMENT_DISABLED: self._handle_element_disabled,
            FailureReason.UNEXPECTED_DIALOG: self._handle_unexpected_dialog,
            FailureReason.UNKNOWN: self._handle_unknown,
        }

        handler = strategy_map.get(diagnosis.failure_reason, self._handle_unknown)
        return handler(original_action, diagnosis, screen_state)

    def report_success(self):
        """操作成功时调用，重置连续失败计数"""
        self._consecutive_failures = 0

    def reset(self):
        """完全重置状态"""
        self._consecutive_failures = 0
        self._retry_counts.clear()

    def _handle_coordinate_offset(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理坐标偏移：微调坐标或重新定位"""
        adj = self.config.coordinate_adjustment
        retry = self._retry_counts.get(f"{action.action_type.value}_{action.target}", 1)

        # 根据重试次数尝试不同方向的偏移
        offsets = [(0, -adj), (0, adj), (-adj, 0), (adj, 0)]
        offset = offsets[(retry - 1) % len(offsets)]

        new_x = (action.x or 0) + offset[0]
        new_y = (action.y or 0) + offset[1]

        new_action = Action(
            action_type=action.action_type,
            target=action.target,
            x=new_x,
            y=new_y,
            text=action.text,
            expected_result=action.expected_result,
            reason=f"坐标微调: 偏移({offset[0]}, {offset[1]})",
            risk_level=action.risk_level,
        )

        return ReplanStrategy(
            strategy_type="adjust_coord",
            description=f"坐标偏移修正: ({action.x},{action.y}) -> ({new_x},{new_y})",
            new_action=new_action,
            adjustments={"offset_x": offset[0], "offset_y": offset[1]},
        )

    def _handle_element_not_loaded(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理元素未加载：等待后重试"""
        wait_time = 2.0 * self.config.wait_multiplier

        new_action = Action(
            action_type=ActionType.WAIT,
            target="",
            expected_result="等待页面加载完成",
            reason="目标元素未加载，等待后重新尝试",
            risk_level=RiskLevel.LOW,
            parameters={"duration": wait_time},
        )

        return ReplanStrategy(
            strategy_type="wait_longer",
            description=f"等待 {wait_time}s 后重新定位目标",
            new_action=new_action,
            adjustments={"wait_duration": wait_time, "then_retry": True},
        )

    def _handle_focus_error(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理焦点错误：先点击输入框获取焦点，再重新输入"""
        # 生成一个先点击再输入的动作
        new_action = Action(
            action_type=ActionType.CLICK,
            target=action.target,
            x=action.x,
            y=action.y,
            expected_result="输入框获得焦点",
            reason="先点击输入框确保焦点，随后再输入",
            risk_level=RiskLevel.LOW,
        )

        return ReplanStrategy(
            strategy_type="retry",
            description="先点击输入框获取焦点，然后重新输入",
            new_action=new_action,
            adjustments={"follow_up_action": "type", "text": action.text},
        )

    def _handle_popup_blocking(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理弹窗遮挡：先关闭/处理弹窗"""
        # 尝试找到关闭或确定按钮
        close_keywords = ["关闭", "确定", "ok", "close", "取消", "cancel", "×"]

        for elem in state.elements:
            for kw in close_keywords:
                if kw in elem.label.lower():
                    center = elem.bbox.center
                    new_action = Action(
                        action_type=ActionType.CLICK,
                        target=elem.label,
                        x=center[0],
                        y=center[1],
                        expected_result="关闭弹窗",
                        reason="弹窗遮挡目标操作，先关闭弹窗",
                        risk_level=RiskLevel.LOW,
                    )
                    return ReplanStrategy(
                        strategy_type="alternative_path",
                        description=f"先关闭弹窗（点击'{elem.label}'），再继续原操作",
                        new_action=new_action,
                        adjustments={"resume_original": True},
                    )

        # 找不到关闭按钮，尝试 Esc
        new_action = Action(
            action_type=ActionType.HOTKEY,
            target="弹窗",
            text="escape",
            expected_result="关闭弹窗",
            reason="尝试按Esc关闭弹窗",
            risk_level=RiskLevel.LOW,
        )

        return ReplanStrategy(
            strategy_type="alternative_path",
            description="按Esc尝试关闭弹窗",
            new_action=new_action,
            adjustments={"resume_original": True},
        )

    def _handle_network_delay(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理网络延迟：延长等待时间"""
        wait_time = 3.0 * self.config.wait_multiplier

        new_action = Action(
            action_type=ActionType.WAIT,
            target="",
            expected_result="页面加载完成",
            reason="网络延迟，等待加载",
            risk_level=RiskLevel.LOW,
            parameters={"duration": wait_time},
        )

        return ReplanStrategy(
            strategy_type="wait_longer",
            description=f"网络延迟，等待 {wait_time}s",
            new_action=new_action,
            adjustments={"wait_duration": wait_time, "then_retry": True},
        )

    def _handle_ocr_failure(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理 OCR 识别失败：切换识别方式"""
        return ReplanStrategy(
            strategy_type="alternative_path",
            description="OCR识别失败，建议切换到控件树或模板匹配定位",
            new_action=None,
            adjustments={
                "switch_strategy": "template_match",
                "retry_with_preprocessing": True,
            },
        )

    def _handle_state_mismatch(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理状态不匹配：回退重试"""
        return ReplanStrategy(
            strategy_type="retry",
            description="状态不匹配，可能前置步骤未成功，建议回退重新执行",
            new_action=None,
            adjustments={"rollback_steps": 1},
        )

    def _handle_element_disabled(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理元素不可用：检查前置条件"""
        return ReplanStrategy(
            strategy_type="alternative_path",
            description="目标元素被禁用，需要先满足前置条件",
            new_action=None,
            adjustments={"check_prerequisites": True},
        )

    def _handle_unexpected_dialog(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理意外对话框"""
        return self._handle_popup_blocking(action, diagnosis, state)

    def _handle_unknown(
        self, action: Action, diagnosis: Diagnosis, state: ScreenState
    ) -> ReplanStrategy:
        """处理未知错误：简单重试"""
        retry_count = self._retry_counts.get(
            f"{action.action_type.value}_{action.target}", 0
        )

        if retry_count >= 2:
            return self._escalate_to_human("多次重试后仍无法确定失败原因")

        return ReplanStrategy(
            strategy_type="retry",
            description="未知错误，尝试直接重试",
            new_action=action,
            adjustments={},
        )

    def _try_alternative_path(
        self, action: Action, state: ScreenState
    ) -> ReplanStrategy:
        """尝试替代路径"""
        return ReplanStrategy(
            strategy_type="alternative_path",
            description=f"动作 '{action.target}' 重试次数超限，需要尝试替代方案",
            new_action=None,
            adjustments={"need_replanning": True},
        )

    def _escalate_to_human(self, reason: str) -> ReplanStrategy:
        """升级为人工接管"""
        new_action = Action(
            action_type=ActionType.ASK_HUMAN,
            target="",
            reason=reason,
            risk_level=RiskLevel.HIGH,
        )

        return ReplanStrategy(
            strategy_type="escalate",
            description=f"请求人工接管: {reason}",
            new_action=new_action,
            adjustments={},
        )
