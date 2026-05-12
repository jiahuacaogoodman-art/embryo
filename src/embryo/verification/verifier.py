"""Verifier - 规则化操作结果验证

不再"默认成功"。每个步骤的验证规则由 VerificationRule 定义，
Verifier 逐条执行并返回明确的通过/失败结果。

验证流程：
1. 执行动作
2. 重新 observe()
3. 对每条 VerificationRule 执行验证
4. 所有规则通过 → 成功
5. 任一规则失败 → 明确失败 + 分类
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional

from ..logging import get_logger
from ..perception.observation import Observation
from ..planning.schema import VerificationRule, VerificationType

logger = get_logger(__name__)


class VerifyStatus(str, Enum):
    """验证结果状态"""

    PASSED = "passed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    SKIPPED = "skipped"  # 没有验证规则时
    ERROR = "error"  # 验证执行本身出错


@dataclass
class VerifyResult:
    """验证结果"""

    status: VerifyStatus
    rule: Optional[VerificationRule] = None
    message: str = ""
    elapsed_sec: float = 0.0
    details: dict = field(default_factory=dict)

    @property
    def passed(self) -> bool:
        return self.status == VerifyStatus.PASSED

    @property
    def failed(self) -> bool:
        return self.status in (VerifyStatus.FAILED, VerifyStatus.TIMEOUT)


@dataclass
class VerificationReport:
    """多条规则的验证报告"""

    results: list[VerifyResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        if not self.results:
            return False  # 没有规则 ≠ 通过
        return all(r.passed for r in self.results)

    @property
    def has_failure(self) -> bool:
        return any(r.failed for r in self.results)

    @property
    def first_failure(self) -> Optional[VerifyResult]:
        for r in self.results:
            if r.failed:
                return r
        return None

    def summary(self) -> str:
        parts = []
        for r in self.results:
            icon = "✓" if r.passed else "✗"
            rule_desc = r.rule.type.value if r.rule else "none"
            parts.append(f"  {icon} [{rule_desc}] {r.message}")
        return "\n".join(parts)


class Verifier:
    """规则化验证器

    根据 VerificationRule 列表，对 Observation 执行逐条验证。
    不做"默认成功"——没有规则时返回 SKIPPED（由调用方决定策略）。

    用法：
        verifier = Verifier()
        report = verifier.verify_all(rules, observation)
        if report.all_passed:
            ...
    """

    def __init__(self, llm_call: Optional[Callable[[str], str]] = None):
        """
        Args:
            llm_call: 可选 LLM 调用函数，用于 custom_llm_judge 类型验证
        """
        self._llm_call = llm_call

    def verify_all(
        self,
        rules: list[VerificationRule],
        observation: Observation,
        observation_before: Optional[Observation] = None,
    ) -> VerificationReport:
        """验证所有规则

        Args:
            rules: 验证规则列表
            observation: 操作后的观测快照
            observation_before: 操作前的观测快照（用于 screenshot_changed）

        Returns:
            VerificationReport
        """
        if not rules:
            return VerificationReport(
                results=[
                    VerifyResult(
                        status=VerifyStatus.SKIPPED,
                        message="没有验证规则",
                    )
                ]
            )

        report = VerificationReport()
        for rule in rules:
            result = self.verify_single(rule, observation, observation_before)
            report.results.append(result)
            # 遇到失败立即停止（fail-fast）
            if result.failed:
                logger.info(
                    "verification_failed",
                    rule_type=rule.type.value,
                    target=rule.target,
                    message=result.message,
                )
                break

        return report

    def verify_single(
        self,
        rule: VerificationRule,
        observation: Observation,
        observation_before: Optional[Observation] = None,
    ) -> VerifyResult:
        """验证单条规则

        Args:
            rule: 验证规则
            observation: 操作后的观测快照
            observation_before: 操作前的观测快照

        Returns:
            VerifyResult
        """
        start = time.time()

        try:
            if rule.type == VerificationType.TEXT_VISIBLE:
                result = self._verify_text_visible(rule, observation)
            elif rule.type == VerificationType.TEXT_ABSENT:
                result = self._verify_text_absent(rule, observation)
            elif rule.type == VerificationType.ELEMENT_VISIBLE:
                result = self._verify_element_visible(rule, observation)
            elif rule.type == VerificationType.ELEMENT_ABSENT:
                result = self._verify_element_absent(rule, observation)
            elif rule.type == VerificationType.URL_CONTAINS:
                result = self._verify_url_contains(rule, observation)
            elif rule.type == VerificationType.SCREENSHOT_CHANGED:
                result = self._verify_screenshot_changed(rule, observation, observation_before)
            elif rule.type == VerificationType.CUSTOM_LLM_JUDGE:
                result = self._verify_llm_judge(rule, observation)
            else:
                result = VerifyResult(
                    status=VerifyStatus.ERROR,
                    rule=rule,
                    message=f"未知验证类型: {rule.type}",
                )
        except Exception as e:
            result = VerifyResult(
                status=VerifyStatus.ERROR,
                rule=rule,
                message=f"验证执行异常: {e}",
            )

        result.elapsed_sec = time.time() - start
        result.rule = rule
        return result

    # --------------------------------------------------
    # 各类型验证实现
    # --------------------------------------------------

    def _verify_text_visible(self, rule: VerificationRule, obs: Observation) -> VerifyResult:
        """验证指定文字出现在屏幕上"""
        if not rule.target:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message="text_visible 规则缺少 target",
            )

        if obs.has_text(rule.target):
            return VerifyResult(
                status=VerifyStatus.PASSED,
                message=f"文字 '{rule.target}' 已出现",
            )

        # 模糊匹配：检查部分包含
        matches = obs.find_text_boxes(rule.target, min_confidence=40.0)
        if matches:
            return VerifyResult(
                status=VerifyStatus.PASSED,
                message=f"文字 '{rule.target}' 在 OCR box 中找到 ({len(matches)} 处)",
                details={"match_count": len(matches)},
            )

        return VerifyResult(
            status=VerifyStatus.FAILED,
            message=f"文字 '{rule.target}' 未在屏幕上找到",
            details={"ocr_text_length": len(obs.ocr_text)},
        )

    def _verify_text_absent(self, rule: VerificationRule, obs: Observation) -> VerifyResult:
        """验证指定文字不在屏幕上"""
        if not rule.target:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message="text_absent 规则缺少 target",
            )

        if not obs.has_text(rule.target):
            matches = obs.find_text_boxes(rule.target, min_confidence=40.0)
            if not matches:
                return VerifyResult(
                    status=VerifyStatus.PASSED,
                    message=f"文字 '{rule.target}' 已不在屏幕上",
                )

        return VerifyResult(
            status=VerifyStatus.FAILED,
            message=f"文字 '{rule.target}' 仍然存在于屏幕上",
        )

    def _verify_element_visible(self, rule: VerificationRule, obs: Observation) -> VerifyResult:
        """验证指定 UI 元素可见（accessibility tree）"""
        if not rule.target:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message="element_visible 规则缺少 target",
            )

        elements = obs.find_elements_by_name(rule.target)
        visible_elements = [e for e in elements if e.is_visible]

        if visible_elements:
            return VerifyResult(
                status=VerifyStatus.PASSED,
                message=f"元素 '{rule.target}' 可见 ({len(visible_elements)} 个)",
                details={"count": len(visible_elements)},
            )

        # 回退到 OCR 检查
        if obs.has_text(rule.target):
            return VerifyResult(
                status=VerifyStatus.PASSED,
                message=f"元素 '{rule.target}' 在 OCR 中检测到（无 accessibility 数据）",
            )

        return VerifyResult(
            status=VerifyStatus.FAILED,
            message=f"元素 '{rule.target}' 不可见",
        )

    def _verify_element_absent(self, rule: VerificationRule, obs: Observation) -> VerifyResult:
        """验证指定 UI 元素已消失"""
        if not rule.target:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message="element_absent 规则缺少 target",
            )

        elements = obs.find_elements_by_name(rule.target)
        visible_elements = [e for e in elements if e.is_visible]

        if not visible_elements and not obs.has_text(rule.target):
            return VerifyResult(
                status=VerifyStatus.PASSED,
                message=f"元素 '{rule.target}' 已消失",
            )

        return VerifyResult(
            status=VerifyStatus.FAILED,
            message=f"元素 '{rule.target}' 仍然存在",
        )

    def _verify_url_contains(self, rule: VerificationRule, obs: Observation) -> VerifyResult:
        """验证浏览器 URL 包含指定内容"""
        if not rule.target:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message="url_contains 规则缺少 target",
            )

        if obs.browser_url is None:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message="无浏览器 URL 数据（非浏览器后端或未采集 DOM）",
            )

        if rule.target in obs.browser_url:
            return VerifyResult(
                status=VerifyStatus.PASSED,
                message=f"URL 包含 '{rule.target}': {obs.browser_url}",
            )

        return VerifyResult(
            status=VerifyStatus.FAILED,
            message=f"URL 不包含 '{rule.target}': {obs.browser_url}",
            details={"current_url": obs.browser_url},
        )

    def _verify_screenshot_changed(
        self,
        rule: VerificationRule,
        obs: Observation,
        obs_before: Optional[Observation],
    ) -> VerifyResult:
        """验证截图有变化

        注意：截图变化只能证明界面动了，不能证明任务做对了。
        这是最弱的验证，仅作为其他验证不可用时的 fallback。
        """
        if obs_before is None:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message="screenshot_changed 需要操作前的 Observation",
            )

        # 简单比较：路径不同 = 有变化（实际应比较 hash）
        if obs.screenshot_path and obs_before.screenshot_path:
            if obs.screenshot_path != obs_before.screenshot_path:
                # TODO: 实际应该比较图片 hash
                return VerifyResult(
                    status=VerifyStatus.PASSED,
                    message="截图已变化（注意：这不能证明操作正确）",
                    details={"warning": "screenshot_changed 是最弱的验证"},
                )

        # 比较 OCR 文字变化
        if obs.ocr_text != obs_before.ocr_text:
            return VerifyResult(
                status=VerifyStatus.PASSED,
                message="界面文字已变化",
            )

        return VerifyResult(
            status=VerifyStatus.FAILED,
            message="截图和 OCR 文字均无变化",
        )

    def _verify_llm_judge(self, rule: VerificationRule, obs: Observation) -> VerifyResult:
        """LLM 判断验证"""
        if self._llm_call is None:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message="custom_llm_judge 需要 LLM 但未配置",
            )

        if not rule.target:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message="custom_llm_judge 规则缺少 target（判断条件描述）",
            )

        prompt = (
            f"请判断以下条件是否满足。只回答 YES 或 NO。\n\n"
            f"条件: {rule.target}\n\n"
            f"当前界面信息:\n"
            f"- 窗口: {obs.active_window_title or '未知'}\n"
            f"- URL: {obs.browser_url or '无'}\n"
            f"- 屏幕文字: {obs.ocr_text[:500]}\n\n"
            f"答案:"
        )

        try:
            response = self._llm_call(prompt).strip().upper()
            if "YES" in response:
                return VerifyResult(
                    status=VerifyStatus.PASSED,
                    message=f"LLM 判断通过: {rule.target}",
                )
            elif "NO" in response:
                return VerifyResult(
                    status=VerifyStatus.FAILED,
                    message=f"LLM 判断未通过: {rule.target}",
                    details={"llm_response": response},
                )
            else:
                return VerifyResult(
                    status=VerifyStatus.ERROR,
                    message=f"LLM 返回无法解析: {response[:50]}",
                )
        except Exception as e:
            return VerifyResult(
                status=VerifyStatus.ERROR,
                message=f"LLM 调用失败: {e}",
            )
