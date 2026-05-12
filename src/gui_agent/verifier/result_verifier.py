"""结果验证模块

每步操作执行后，重新采集界面状态并判断操作是否达到预期结果。
验证方式包括：截图差分、OCR文本变化、页面跳转、控件状态变化、弹窗检测等。
"""

from __future__ import annotations

import time
from typing import Optional

import numpy as np
from loguru import logger

from ..config import VerificationConfig
from ..models import (
    Action,
    ActionType,
    ScreenState,
    VerificationResult,
    VerificationStatus,
)
from ..perception.perception_engine import PerceptionEngine
from ..perception.screen_capture import ScreenCapture


class ResultVerifier:
    """结果验证器

    核心理念：每一步都要验证是否真的达到预期结果，
    而不是"点完就算"。
    """

    def __init__(self, config: VerificationConfig, perception: PerceptionEngine):
        self.config = config
        self.perception = perception
        self._screen_capture = perception.screen_capture

    def verify(
        self,
        action: Action,
        state_before: ScreenState,
        screenshot_before: Optional[np.ndarray] = None,
    ) -> VerificationResult:
        """验证操作结果

        流程：
        1. 等待界面响应
        2. 重新采集界面状态
        3. 多维度验证操作效果

        Args:
            action: 执行的动作
            state_before: 操作前的界面状态
            screenshot_before: 操作前的截图

        Returns:
            验证结果
        """
        logger.info(f"开始验证操作结果: {action.action_type.value} -> '{action.expected_result}'")

        # 1. 等待界面响应
        time.sleep(self.config.verify_delay)

        # 2. 重新感知界面
        state_after = self.perception.perceive()

        # 3. 截取操作后的屏幕
        screenshot_after = self._screen_capture.capture_full_screen()

        # 4. 多维度验证
        checks = []

        # 对于 stop 和 ask_human 不需要验证界面变化
        if action.action_type in (ActionType.STOP, ActionType.ASK_HUMAN):
            return VerificationResult(
                status=VerificationStatus.SUCCESS,
                message="特殊动作，无需验证界面变化",
            )

        # 对于 wait 动作，只要没有错误弹窗就算成功
        if action.action_type == ActionType.WAIT:
            popup_check = self._check_error_popup(state_after)
            if popup_check:
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    message=f"等待期间出现错误弹窗: {popup_check}",
                    changes_detected=[popup_check],
                )
            return VerificationResult(
                status=VerificationStatus.SUCCESS,
                message="等待完成，无错误弹窗",
            )

        # 检查1: 截图差分（界面是否发生变化）
        if screenshot_before is not None:
            diff_result = self._check_screen_diff(screenshot_before, screenshot_after)
            checks.append(("screen_diff", diff_result))

        # 检查2: OCR 文本变化
        text_result = self._check_text_changes(state_before, state_after, action)
        checks.append(("text_change", text_result))

        # 检查3: 窗口标题变化（页面跳转）
        title_result = self._check_title_change(state_before, state_after)
        checks.append(("title_change", title_result))

        # 检查4: 错误弹窗检测
        error_popup = self._check_error_popup(state_after)
        if error_popup:
            checks.append(("error_popup", {"status": "failed", "message": error_popup}))

        # 检查5: 预期结果验证
        if action.expected_result:
            expected_result = self._check_expected_result(
                action.expected_result, state_after
            )
            checks.append(("expected_result", expected_result))

        # 综合判断
        return self._aggregate_results(checks, state_before, state_after)

    def quick_verify(
        self,
        screenshot_before: np.ndarray,
        expected_change: bool = True,
    ) -> VerificationResult:
        """快速验证（仅通过截图差分判断）

        Args:
            screenshot_before: 操作前截图
            expected_change: 是否期望界面变化

        Returns:
            验证结果
        """
        time.sleep(self.config.verify_delay * 0.5)
        screenshot_after = self._screen_capture.capture_full_screen()

        diff_score = self._screen_capture.get_screen_diff(screenshot_before, screenshot_after)

        if expected_change:
            if diff_score > self.config.diff_threshold:
                return VerificationResult(
                    status=VerificationStatus.SUCCESS,
                    message=f"界面发生变化，差异度={diff_score:.3f}",
                    similarity_score=1.0 - diff_score,
                )
            else:
                return VerificationResult(
                    status=VerificationStatus.FAILED,
                    message=f"界面未发生预期变化，差异度={diff_score:.3f}",
                    similarity_score=1.0 - diff_score,
                )
        else:
            # 期望不变化
            if diff_score <= self.config.diff_threshold:
                return VerificationResult(
                    status=VerificationStatus.SUCCESS,
                    message="界面保持稳定",
                    similarity_score=1.0 - diff_score,
                )
            else:
                return VerificationResult(
                    status=VerificationStatus.UNCERTAIN,
                    message=f"界面发生了意外变化，差异度={diff_score:.3f}",
                    similarity_score=1.0 - diff_score,
                )

    def wait_for_stable(self, timeout: float = 5.0, interval: float = 0.5) -> bool:
        """等待界面稳定（不再变化）

        Args:
            timeout: 最大等待时间
            interval: 检测间隔

        Returns:
            是否在超时前稳定
        """
        start_time = time.time()
        prev_screenshot = self._screen_capture.capture_full_screen()

        while time.time() - start_time < timeout:
            time.sleep(interval)
            curr_screenshot = self._screen_capture.capture_full_screen()

            diff = self._screen_capture.get_screen_diff(prev_screenshot, curr_screenshot)
            if diff < self.config.diff_threshold:
                logger.debug(f"界面已稳定，等待时长={time.time()-start_time:.1f}s")
                return True

            prev_screenshot = curr_screenshot

        logger.warning(f"等待界面稳定超时 ({timeout}s)")
        return False

    def _check_screen_diff(
        self, before: np.ndarray, after: np.ndarray
    ) -> dict:
        """检查截图差分"""
        diff_score = self._screen_capture.get_screen_diff(before, after)
        changed = diff_score > self.config.diff_threshold

        # 获取变化区域
        regions = []
        if changed:
            regions = self._screen_capture.get_diff_regions(before, after)

        return {
            "status": "changed" if changed else "unchanged",
            "diff_score": diff_score,
            "changed_regions": len(regions),
        }

    def _check_text_changes(
        self, before: ScreenState, after: ScreenState, action: Action
    ) -> dict:
        """检查 OCR 文本变化"""
        texts_before = set(before.detected_text)
        texts_after = set(after.detected_text)

        new_texts = texts_after - texts_before
        removed_texts = texts_before - texts_after

        # 对于输入操作，检查输入文字是否出现
        if action.action_type == ActionType.TYPE and action.text:
            input_visible = any(action.text in t for t in texts_after)
            return {
                "status": "success" if input_visible else "uncertain",
                "new_texts": list(new_texts),
                "removed_texts": list(removed_texts),
                "input_visible": input_visible,
            }

        has_changes = bool(new_texts or removed_texts)
        return {
            "status": "changed" if has_changes else "unchanged",
            "new_texts": list(new_texts),
            "removed_texts": list(removed_texts),
        }

    def _check_title_change(self, before: ScreenState, after: ScreenState) -> dict:
        """检查窗口标题变化（判断页面跳转）"""
        changed = before.window_title != after.window_title
        return {
            "status": "changed" if changed else "unchanged",
            "before": before.window_title,
            "after": after.window_title,
        }

    def _check_error_popup(self, state: ScreenState) -> Optional[str]:
        """检测错误弹窗"""
        error_keywords = [
            "错误", "失败", "error", "failed", "异常", "无法",
            "超时", "timeout", "拒绝", "denied", "不正确",
            "账号或密码错误", "网络异常", "服务器错误",
        ]

        for text in state.detected_text:
            text_lower = text.lower()
            for keyword in error_keywords:
                if keyword in text_lower:
                    return text

        return None

    def _check_expected_result(self, expected: str, state: ScreenState) -> dict:
        """验证预期结果是否出现"""
        expected_lower = expected.lower()

        # 检查预期文字是否出现在界面中
        for text in state.detected_text:
            if expected_lower in text.lower() or text.lower() in expected_lower:
                return {
                    "status": "success",
                    "message": f"预期结果已出现: '{text}'",
                }

        # 检查窗口标题
        if expected_lower in state.window_title.lower():
            return {
                "status": "success",
                "message": f"窗口标题匹配: '{state.window_title}'",
            }

        return {
            "status": "uncertain",
            "message": f"未明确检测到预期结果: '{expected}'",
        }

    def _aggregate_results(
        self,
        checks: list[tuple[str, dict]],
        state_before: ScreenState,
        state_after: ScreenState,
    ) -> VerificationResult:
        """综合所有验证维度的结果"""
        changes_detected = []
        has_success_signal = False
        has_failure_signal = False

        for check_name, result in checks:
            status = result.get("status", "")

            if status == "success":
                has_success_signal = True
                msg = result.get("message", check_name)
                changes_detected.append(f"✓ {check_name}: {msg}")

            elif status == "failed":
                has_failure_signal = True
                msg = result.get("message", check_name)
                changes_detected.append(f"✗ {check_name}: {msg}")

            elif status == "changed":
                has_success_signal = True
                changes_detected.append(f"△ {check_name}: 检测到变化")

            elif status == "unchanged":
                changes_detected.append(f"○ {check_name}: 无变化")

        # 判断最终状态
        if has_failure_signal:
            final_status = VerificationStatus.FAILED
            message = "检测到失败信号"
        elif has_success_signal:
            final_status = VerificationStatus.SUCCESS
            message = "操作验证通过"
        else:
            final_status = VerificationStatus.UNCERTAIN
            message = "无法确定操作是否成功"

        return VerificationResult(
            status=final_status,
            message=message,
            changes_detected=changes_detected,
        )
