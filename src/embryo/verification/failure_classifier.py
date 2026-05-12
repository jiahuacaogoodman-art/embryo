"""Failure Classifier - 统一失败分类

不再让所有失败都叫"目标不可见"。
根据 Observation 和 ActionResult 精确分类失败类型，
让 replan prompt 有针对性。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ..action.backend import ActionResult, ActionStatus
from ..perception.observation import Observation
from ..perception.target_resolver import ResolvedTarget, ResolveStatus
from ..logging import get_logger

logger = get_logger(__name__)


class FailureType(str, Enum):
    """失败类型枚举"""

    TARGET_NOT_FOUND = "target_not_found"  # 目标元素不存在
    TARGET_AMBIGUOUS = "target_ambiguous"  # 目标匹配到多个
    WINDOW_NOT_ACTIVE = "window_not_active"  # 目标窗口不在前台
    INPUT_NOT_FOCUSED = "input_not_focused"  # 输入框未获焦
    PAGE_LOADING = "page_loading"  # 页面/应用正在加载
    POPUP_BLOCKING = "popup_blocking"  # 弹窗阻挡了操作
    PERMISSION_REQUIRED = "permission_required"  # 需要权限/登录
    ACTION_NO_EFFECT = "action_no_effect"  # 操作执行了但无效果
    VERIFICATION_FAILED = "verification_failed"  # 验证规则未通过
    POLICY_BLOCKED = "policy_blocked"  # 被安全策略阻止
    BACKEND_ERROR = "backend_error"  # 后端执行异常
    TIMEOUT = "timeout"  # 超时
    UNKNOWN = "unknown"  # 无法分类


@dataclass
class ClassifiedFailure:
    """分类后的失败信息"""

    type: FailureType
    message: str
    suggestion: str = ""  # 建议的恢复策略
    is_retryable: bool = True  # 是否值得重试
    details: dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}

    def to_replan_context(self) -> str:
        """生成供 replan prompt 使用的上下文"""
        lines = [
            f"失败类型: {self.type.value}",
            f"具体信息: {self.message}",
        ]
        if self.suggestion:
            lines.append(f"建议策略: {self.suggestion}")
        lines.append(f"可重试: {'是' if self.is_retryable else '否'}")
        return "\n".join(lines)


class FailureClassifier:
    """失败分类器

    根据各种信号（ActionResult、Observation、ResolvedTarget）
    判断失败的具体类型，提供针对性的恢复建议。
    """

    # 常见弹窗/对话框关键词
    POPUP_KEYWORDS = [
        "确定", "取消", "是", "否", "关闭", "保存", "不保存",
        "ok", "cancel", "yes", "no", "close", "save", "don't save",
        "allow", "deny", "accept", "decline",
    ]

    # 加载中关键词
    LOADING_KEYWORDS = [
        "加载中", "loading", "请稍候", "please wait",
        "正在处理", "processing",
    ]

    # 权限相关关键词
    PERMISSION_KEYWORDS = [
        "登录", "login", "sign in", "权限", "permission",
        "unauthorized", "forbidden", "access denied",
        "请先登录", "需要授权",
    ]

    def classify(
        self,
        action_result: Optional[ActionResult] = None,
        observation: Optional[Observation] = None,
        resolved_target: Optional[ResolvedTarget] = None,
        error_message: str = "",
    ) -> ClassifiedFailure:
        """分类失败

        Args:
            action_result: 动作执行结果
            observation: 当前观测快照
            resolved_target: 目标解析结果
            error_message: 原始错误信息

        Returns:
            ClassifiedFailure 包含类型、消息、建议
        """
        # 优先级 1: 目标解析失败
        if resolved_target is not None:
            if resolved_target.status == ResolveStatus.NOT_FOUND:
                return self._classify_target_not_found(observation, error_message)
            if resolved_target.status == ResolveStatus.AMBIGUOUS:
                return ClassifiedFailure(
                    type=FailureType.TARGET_AMBIGUOUS,
                    message=f"目标匹配到 {len(resolved_target.all_matches)} 个候选",
                    suggestion="添加 near_text 约束或使用更精确的选择器来消除歧义",
                    is_retryable=True,
                    details={"match_count": len(resolved_target.all_matches)},
                )

        # 优先级 2: ActionResult 状态
        if action_result is not None:
            if action_result.status == ActionStatus.TIMEOUT:
                return ClassifiedFailure(
                    type=FailureType.TIMEOUT,
                    message="操作超时",
                    suggestion="增加等待时间或检查应用是否卡死",
                    is_retryable=True,
                )
            if action_result.status == ActionStatus.BLOCKED:
                return self._classify_blocked(observation, action_result)
            if action_result.status == ActionStatus.NO_EFFECT:
                return self._classify_no_effect(observation, error_message)
            if action_result.status == ActionStatus.TARGET_NOT_FOUND:
                return self._classify_target_not_found(observation, error_message)
            if action_result.status == ActionStatus.FAILED:
                return ClassifiedFailure(
                    type=FailureType.BACKEND_ERROR,
                    message=action_result.message,
                    suggestion="检查后端环境是否正常",
                    is_retryable=False,
                )

        # 优先级 3: 观测分析
        if observation is not None:
            classified = self._classify_from_observation(observation, error_message)
            if classified:
                return classified

        # 优先级 4: 错误消息分析
        if error_message:
            return self._classify_from_error_message(error_message)

        return ClassifiedFailure(
            type=FailureType.UNKNOWN,
            message=error_message or "未知失败",
            suggestion="尝试重新截图观察当前界面状态",
            is_retryable=True,
        )

    def _classify_target_not_found(
        self, obs: Optional[Observation], error_msg: str
    ) -> ClassifiedFailure:
        """细分目标未找到的原因"""
        if obs is not None:
            # 检查是否在加载
            if self._text_contains_keywords(obs.ocr_text, self.LOADING_KEYWORDS):
                return ClassifiedFailure(
                    type=FailureType.PAGE_LOADING,
                    message="页面正在加载，目标可能尚未渲染",
                    suggestion="等待 2-3 秒后重试",
                    is_retryable=True,
                )

            # 检查是否需要登录
            if self._text_contains_keywords(obs.ocr_text, self.PERMISSION_KEYWORDS):
                return ClassifiedFailure(
                    type=FailureType.PERMISSION_REQUIRED,
                    message="检测到登录/权限页面，目标不在当前页面",
                    suggestion="先完成登录或授权流程",
                    is_retryable=False,
                )

            # 检查是否有弹窗遮挡
            if self._text_contains_keywords(obs.ocr_text, self.POPUP_KEYWORDS):
                return ClassifiedFailure(
                    type=FailureType.POPUP_BLOCKING,
                    message="检测到弹窗/对话框，可能遮挡了目标",
                    suggestion="先关闭弹窗（按 Escape 或点击关闭按钮），再恢复操作",
                    is_retryable=True,
                )

        return ClassifiedFailure(
            type=FailureType.TARGET_NOT_FOUND,
            message=error_msg or "目标元素不存在于当前界面",
            suggestion="使用 screenshot 观察界面，确认目标是否在当前视图，可能需要滚动或切换页面",
            is_retryable=True,
        )

    def _classify_blocked(
        self, obs: Optional[Observation], result: ActionResult
    ) -> ClassifiedFailure:
        """细分被阻止的原因"""
        if obs and self._text_contains_keywords(obs.ocr_text, self.POPUP_KEYWORDS):
            return ClassifiedFailure(
                type=FailureType.POPUP_BLOCKING,
                message="操作被弹窗阻止",
                suggestion="先处理弹窗，再恢复原操作",
                is_retryable=True,
            )

        return ClassifiedFailure(
            type=FailureType.POLICY_BLOCKED,
            message=result.message,
            suggestion="检查安全策略配置",
            is_retryable=False,
        )

    def _classify_no_effect(
        self, obs: Optional[Observation], error_msg: str
    ) -> ClassifiedFailure:
        """细分无效果的原因"""
        if obs is not None:
            # 检查是否输入框未获焦
            if "输入" in error_msg or "type" in error_msg.lower():
                return ClassifiedFailure(
                    type=FailureType.INPUT_NOT_FOCUSED,
                    message="输入操作无效果，输入框可能未获焦",
                    suggestion="先 click 点击输入框获取焦点，再输入",
                    is_retryable=True,
                )

            # 检查窗口是否活跃
            if obs.active_window_title is None:
                return ClassifiedFailure(
                    type=FailureType.WINDOW_NOT_ACTIVE,
                    message="无法确认目标窗口是否在前台",
                    suggestion="用 hotkey Alt+Tab 切换窗口或重新定位目标窗口",
                    is_retryable=True,
                )

        return ClassifiedFailure(
            type=FailureType.ACTION_NO_EFFECT,
            message=error_msg or "操作执行了但界面未变化",
            suggestion="检查坐标是否正确、元素是否可交互（enabled），或尝试不同的定位方式",
            is_retryable=True,
        )

    def _classify_from_observation(
        self, obs: Observation, error_msg: str
    ) -> Optional[ClassifiedFailure]:
        """从观测数据中推断失败类型"""
        # 加载中
        if self._text_contains_keywords(obs.ocr_text, self.LOADING_KEYWORDS):
            return ClassifiedFailure(
                type=FailureType.PAGE_LOADING,
                message="当前界面处于加载状态",
                suggestion="等待加载完成后重试",
                is_retryable=True,
            )

        # 弹窗
        if self._text_contains_keywords(obs.ocr_text, self.POPUP_KEYWORDS):
            return ClassifiedFailure(
                type=FailureType.POPUP_BLOCKING,
                message="界面存在弹窗/对话框",
                suggestion="先处理弹窗再恢复操作",
                is_retryable=True,
            )

        return None

    def _classify_from_error_message(self, error_msg: str) -> ClassifiedFailure:
        """从错误消息文本推断类型"""
        lower = error_msg.lower()

        if "timeout" in lower or "超时" in error_msg:
            return ClassifiedFailure(
                type=FailureType.TIMEOUT,
                message=error_msg,
                suggestion="增加超时时间或检查网络/应用响应",
                is_retryable=True,
            )

        if "policy" in lower or "策略" in error_msg or "blocked" in lower:
            return ClassifiedFailure(
                type=FailureType.POLICY_BLOCKED,
                message=error_msg,
                suggestion="检查安全策略配置",
                is_retryable=False,
            )

        if "验证" in error_msg or "verification" in lower:
            return ClassifiedFailure(
                type=FailureType.VERIFICATION_FAILED,
                message=error_msg,
                suggestion="检查验证规则是否正确，或界面状态是否符合预期",
                is_retryable=True,
            )

        return ClassifiedFailure(
            type=FailureType.UNKNOWN,
            message=error_msg,
            suggestion="尝试重新观察界面状态",
            is_retryable=True,
        )

    @staticmethod
    def _text_contains_keywords(text: str, keywords: list[str]) -> bool:
        """检查文本是否包含任一关键词"""
        text_lower = text.lower()
        return any(kw.lower() in text_lower for kw in keywords)
