"""工具循环守卫 (Tool Call Guardrails)



无副作用的纯控制器：跟踪每轮工具调用状态，返回决策。
运行时代码决定如何处理这些决策（注入警告/合成结果/终止循环）。

核心检测维度：
1. exact_failure: 完全相同的工具+参数反复失败
2. same_tool_failure: 同一工具（不同参数）反复失败
3. no_progress: 幂等工具反复返回相同结果（没有进展）

决策类型：
- allow: 正常执行
- warn: 允许执行但附加警告（注入到 tool result 末尾）
- block: 拒绝执行（返回合成错误结果）
- halt: 终止整个循环
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from ..logging import get_logger

logger = get_logger("tool_guardrails")

# 幂等工具（只读，重复调用结果相同）
IDEMPOTENT_TOOLS = frozenset({
    "read_file",
    "list_directory",
    "recall",
    "screenshot",
    "ocr_screen",
    "find_text_on_screen",
    "load_skill",
})

# 变异工具（有副作用，结果可能不同）
MUTATING_TOOLS = frozenset({
    "terminal",
    "write_file",
    "edit_file",
    "click",
    "type_text",
    "hotkey",
    "press_key",
    "scroll",
    "remember",
    "forget",
})


@dataclass(frozen=True)
class GuardrailConfig:
    """守卫阈值配置。

    warnings 默认开启且不阻止执行。
    hard_stop 需显式启用（防止过于激进的中断）。
    """
    warnings_enabled: bool = True
    hard_stop_enabled: bool = False
    # 精确重复失败
    exact_failure_warn_after: int = 2
    exact_failure_block_after: int = 5
    # 同工具失败
    same_tool_failure_warn_after: int = 3
    same_tool_failure_halt_after: int = 8
    # 幂等无进展
    no_progress_warn_after: int = 2
    no_progress_block_after: int = 5


@dataclass(frozen=True)
class ToolSignature:
    """工具调用的稳定标识（工具名 + 参数 hash）。"""
    tool_name: str
    args_hash: str

    @classmethod
    def from_call(cls, tool_name: str, args: Mapping[str, Any]) -> "ToolSignature":
        canonical = json.dumps(args, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
        h = hashlib.sha256(canonical.encode()).hexdigest()[:16]
        return cls(tool_name=tool_name, args_hash=h)


@dataclass(frozen=True)
class GuardrailDecision:
    """守卫决策。"""
    action: str = "allow"  # allow | warn | block | halt
    code: str = ""
    message: str = ""
    tool_name: str = ""
    count: int = 0

    @property
    def allows_execution(self) -> bool:
        return self.action in ("allow", "warn")

    @property
    def should_halt(self) -> bool:
        return self.action in ("block", "halt")


class ToolGuardrailController:
    """工具循环守卫控制器。

    每轮（turn）开始时 reset，追踪该轮内的所有工具调用模式。

    用法：
        guardrail = ToolGuardrailController()
        # 每轮开始
        guardrail.reset_for_turn()
        # 执行前检查
        decision = guardrail.before_call(name, args)
        if decision.should_halt:
            # 注入合成结果，不执行
            ...
        # 执行后记录
        decision = guardrail.after_call(name, args, result, failed=...)
        if decision.action == "warn":
            # 将警告附加到 tool result 末尾
            result += f"\\n[{decision.message}]"
    """

    def __init__(self, config: Optional[GuardrailConfig] = None):
        self.config = config or GuardrailConfig()
        self.reset_for_turn()

    def reset_for_turn(self):
        """重置每轮计数器。"""
        self._exact_failure_counts: dict[ToolSignature, int] = {}
        self._same_tool_failure_counts: dict[str, int] = {}
        self._no_progress: dict[ToolSignature, tuple[str, int]] = {}  # sig → (result_hash, repeat_count)

    def before_call(self, tool_name: str, args: Mapping[str, Any]) -> GuardrailDecision:
        """执行前检查：是否应阻止此调用。"""
        if not self.config.hard_stop_enabled:
            return GuardrailDecision(tool_name=tool_name)

        sig = ToolSignature.from_call(tool_name, args)

        # 精确重复失败阻止
        exact_count = self._exact_failure_counts.get(sig, 0)
        if exact_count >= self.config.exact_failure_block_after:
            return GuardrailDecision(
                action="block",
                code="exact_failure_block",
                message=(
                    f"已阻止 {tool_name}: 相同参数已失败 {exact_count} 次。"
                    "请更换策略而非原样重试。"
                ),
                tool_name=tool_name,
                count=exact_count,
            )

        # 幂等工具无进展阻止
        if tool_name in IDEMPOTENT_TOOLS:
            record = self._no_progress.get(sig)
            if record is not None:
                _, repeat_count = record
                if repeat_count >= self.config.no_progress_block_after:
                    return GuardrailDecision(
                        action="block",
                        code="no_progress_block",
                        message=(
                            f"已阻止 {tool_name}: 相同调用返回相同结果 {repeat_count} 次。"
                            "请使用已有结果或更换查询方式。"
                        ),
                        tool_name=tool_name,
                        count=repeat_count,
                    )

        return GuardrailDecision(tool_name=tool_name)

    def after_call(
        self,
        tool_name: str,
        args: Mapping[str, Any],
        result: str,
        *,
        failed: bool = False,
    ) -> GuardrailDecision:
        """执行后记录：更新计数器并返回决策。"""
        sig = ToolSignature.from_call(tool_name, args)

        if failed:
            # 精确失败计数
            exact_count = self._exact_failure_counts.get(sig, 0) + 1
            self._exact_failure_counts[sig] = exact_count
            self._no_progress.pop(sig, None)

            # 同工具失败计数
            same_count = self._same_tool_failure_counts.get(tool_name, 0) + 1
            self._same_tool_failure_counts[tool_name] = same_count

            # halt 检查
            if self.config.hard_stop_enabled and same_count >= self.config.same_tool_failure_halt_after:
                return GuardrailDecision(
                    action="halt",
                    code="same_tool_failure_halt",
                    message=(
                        f"已停止 {tool_name}: 本轮失败 {same_count} 次。"
                        "请停止重试并换一种方式。"
                    ),
                    tool_name=tool_name,
                    count=same_count,
                )

            # warn 检查
            if self.config.warnings_enabled:
                if exact_count >= self.config.exact_failure_warn_after:
                    return GuardrailDecision(
                        action="warn",
                        code="exact_failure_warn",
                        message=(
                            f"{tool_name} 相同参数已失败 {exact_count} 次，"
                            "疑似循环。请检查错误并更换策略。"
                        ),
                        tool_name=tool_name,
                        count=exact_count,
                    )
                if same_count >= self.config.same_tool_failure_warn_after:
                    return GuardrailDecision(
                        action="warn",
                        code="same_tool_failure_warn",
                        message=(
                            f"{tool_name} 本轮已失败 {same_count} 次，"
                            "疑似循环。请换一种方式。"
                        ),
                        tool_name=tool_name,
                        count=same_count,
                    )

            return GuardrailDecision(tool_name=tool_name, count=exact_count)

        # 成功 → 清除失败计数
        self._exact_failure_counts.pop(sig, None)
        self._same_tool_failure_counts.pop(tool_name, None)

        # 幂等工具 → 检查结果是否有变化
        if tool_name not in IDEMPOTENT_TOOLS:
            self._no_progress.pop(sig, None)
            return GuardrailDecision(tool_name=tool_name)

        result_hash = hashlib.sha256((result or "").encode()).hexdigest()[:16]
        previous = self._no_progress.get(sig)
        repeat_count = 1
        if previous is not None and previous[0] == result_hash:
            repeat_count = previous[1] + 1
        self._no_progress[sig] = (result_hash, repeat_count)

        if self.config.warnings_enabled and repeat_count >= self.config.no_progress_warn_after:
            return GuardrailDecision(
                action="warn",
                code="no_progress_warn",
                message=(
                    f"{tool_name} 返回相同结果 {repeat_count} 次。"
                    "请使用已有结果或更换查询方式。"
                ),
                tool_name=tool_name,
                count=repeat_count,
            )

        return GuardrailDecision(tool_name=tool_name, count=repeat_count)


def append_guardrail_guidance(result: str, decision: GuardrailDecision) -> str:
    """将守卫警告附加到工具结果末尾。"""
    if decision.action not in ("warn", "halt") or not decision.message:
        return result
    label = "工具循环终止" if decision.action == "halt" else "工具循环警告"
    return (result or "") + f"\n\n[{label}: {decision.message}]"


def synthetic_block_result(decision: GuardrailDecision) -> str:
    """为被阻止的工具调用生成合成结果。"""
    return json.dumps(
        {"error": decision.message, "guardrail_code": decision.code, "count": decision.count},
        ensure_ascii=False,
    )
