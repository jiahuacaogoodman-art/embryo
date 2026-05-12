"""错误诊断模块

当操作失败时，根据界面反馈分析失败原因。
不依赖长期记忆，而是基于当前上下文进行在线分析。
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from ..models import (
    Action,
    ActionType,
    Diagnosis,
    FailureReason,
    ScreenState,
    VerificationResult,
    VerificationStatus,
)


class ErrorDiagnoser:
    """错误诊断器

    分析操作失败的原因，为重规划提供依据。
    """

    def diagnose(
        self,
        action: Action,
        state_before: ScreenState,
        state_after: ScreenState,
        verification: VerificationResult,
    ) -> Diagnosis:
        """诊断操作失败原因

        根据操作前后界面状态、验证结果，综合分析失败原因。

        Args:
            action: 执行的动作
            state_before: 操作前状态
            state_after: 操作后状态
            verification: 验证结果

        Returns:
            诊断结果
        """
        logger.info(f"开始诊断失败原因: {action.action_type.value} -> '{action.target}'")

        # 按优先级逐一检查可能的失败原因
        diagnosis = None

        # 检查1: 弹窗遮挡
        diagnosis = self._check_popup_blocking(state_after)
        if diagnosis:
            return diagnosis

        # 检查2: 元素未加载
        diagnosis = self._check_element_not_loaded(action, state_after)
        if diagnosis:
            return diagnosis

        # 检查3: 焦点错误（输入类操作）
        if action.action_type == ActionType.TYPE:
            diagnosis = self._check_focus_error(action, state_before, state_after)
            if diagnosis:
                return diagnosis

        # 检查4: 坐标偏移
        if action.x is not None and action.y is not None:
            diagnosis = self._check_coordinate_offset(action, state_before, state_after)
            if diagnosis:
                return diagnosis

        # 检查5: 网络延迟 / 加载中
        diagnosis = self._check_network_delay(state_after)
        if diagnosis:
            return diagnosis

        # 检查6: 状态不匹配
        diagnosis = self._check_state_mismatch(action, state_before, state_after)
        if diagnosis:
            return diagnosis

        # 检查7: 元素不可用
        diagnosis = self._check_element_disabled(action, state_after)
        if diagnosis:
            return diagnosis

        # 无法确定原因
        return Diagnosis(
            failure_reason=FailureReason.UNKNOWN,
            confidence=0.3,
            description="无法确定具体失败原因",
            suggested_fix="尝试重新执行或更换操作策略",
        )

    def _check_popup_blocking(self, state_after: ScreenState) -> Optional[Diagnosis]:
        """检查是否有弹窗遮挡"""
        popup_keywords = [
            "确定", "取消", "关闭", "是", "否",
            "ok", "cancel", "close", "yes", "no",
            "提示", "警告", "确认", "alert", "confirm",
        ]

        # 检查是否有对话框类元素
        from ..models import ElementType
        dialogs = [
            e for e in state_after.elements
            if e.element_type == ElementType.DIALOG
        ]
        if dialogs:
            return Diagnosis(
                failure_reason=FailureReason.POPUP_BLOCKING,
                confidence=0.9,
                description=f"检测到对话框遮挡: {dialogs[0].label}",
                suggested_fix="先处理弹窗（点击确定/关闭），再继续原操作",
            )

        # 通过文字判断是否有弹窗类提示
        popup_text_count = sum(
            1 for text in state_after.detected_text
            if any(kw in text.lower() for kw in popup_keywords)
        )
        if popup_text_count >= 2:
            return Diagnosis(
                failure_reason=FailureReason.POPUP_BLOCKING,
                confidence=0.7,
                description="检测到疑似弹窗（多个弹窗关键词出现）",
                suggested_fix="尝试关闭弹窗或点击确定按钮",
            )

        return None

    def _check_element_not_loaded(
        self, action: Action, state_after: ScreenState
    ) -> Optional[Diagnosis]:
        """检查目标元素是否未加载"""
        if not action.target:
            return None

        # 在操作后的界面中查找目标
        target_lower = action.target.lower()
        found = any(
            target_lower in text.lower()
            for text in state_after.detected_text
        )

        if not found:
            # 目标文字不在界面中
            return Diagnosis(
                failure_reason=FailureReason.ELEMENT_NOT_LOADED,
                confidence=0.75,
                description=f"目标元素 '{action.target}' 未在界面中找到，可能尚未加载",
                suggested_fix="等待页面加载完成后重新定位目标",
            )

        return None

    def _check_focus_error(
        self, action: Action, state_before: ScreenState, state_after: ScreenState
    ) -> Optional[Diagnosis]:
        """检查输入焦点错误"""
        if action.action_type != ActionType.TYPE or not action.text:
            return None

        # 检查输入内容是否出现在界面中
        input_visible = any(
            action.text in text for text in state_after.detected_text
        )

        if not input_visible:
            return Diagnosis(
                failure_reason=FailureReason.FOCUS_ERROR,
                confidence=0.7,
                description=f"输入文字 '{action.text}' 未出现在界面中，可能输入框未获得焦点",
                suggested_fix="先点击目标输入框确保获得焦点，然后重新输入",
            )

        return None

    def _check_coordinate_offset(
        self, action: Action, state_before: ScreenState, state_after: ScreenState
    ) -> Optional[Diagnosis]:
        """检查坐标偏移问题"""
        # 界面完全没有变化，可能点击位置不对
        texts_before = set(state_before.detected_text)
        texts_after = set(state_after.detected_text)

        no_change = (
            texts_before == texts_after
            and state_before.window_title == state_after.window_title
        )

        if no_change and action.action_type in (
            ActionType.CLICK, ActionType.DOUBLE_CLICK
        ):
            return Diagnosis(
                failure_reason=FailureReason.COORDINATE_OFFSET,
                confidence=0.6,
                description=(
                    f"点击坐标 ({action.x}, {action.y}) 后界面无变化，"
                    f"可能坐标偏移或点击了空白区域"
                ),
                suggested_fix="重新定位目标元素，计算新的点击坐标",
            )

        return None

    def _check_network_delay(self, state_after: ScreenState) -> Optional[Diagnosis]:
        """检查网络延迟/加载中"""
        loading_keywords = [
            "加载中", "loading", "请稍候", "please wait",
            "处理中", "正在", "connecting", "载入",
        ]

        for text in state_after.detected_text:
            text_lower = text.lower()
            for kw in loading_keywords:
                if kw in text_lower:
                    return Diagnosis(
                        failure_reason=FailureReason.NETWORK_DELAY,
                        confidence=0.8,
                        description=f"页面仍在加载中: '{text}'",
                        suggested_fix="延长等待时间，等待加载完成后重新操作",
                    )

        return None

    def _check_state_mismatch(
        self, action: Action, state_before: ScreenState, state_after: ScreenState
    ) -> Optional[Diagnosis]:
        """检查状态不匹配（上一步操作可能未成功）"""
        # 如果窗口标题没有变化但预期应该跳转
        if action.expected_result and "进入" in action.expected_result:
            if state_before.window_title == state_after.window_title:
                return Diagnosis(
                    failure_reason=FailureReason.STATE_MISMATCH,
                    confidence=0.6,
                    description="预期页面跳转但窗口标题未变化",
                    suggested_fix="检查前置步骤是否成功，可能需要回退一步重新执行",
                )

        return None

    def _check_element_disabled(
        self, action: Action, state_after: ScreenState
    ) -> Optional[Diagnosis]:
        """检查目标元素是否不可用"""
        if not action.target:
            return None

        target_lower = action.target.lower()
        for elem in state_after.elements:
            if target_lower in elem.label.lower() and not elem.is_enabled:
                return Diagnosis(
                    failure_reason=FailureReason.ELEMENT_DISABLED,
                    confidence=0.85,
                    description=f"目标元素 '{elem.label}' 当前处于禁用状态",
                    suggested_fix="检查前置条件是否满足（如必填字段是否已填写）",
                )

        return None
