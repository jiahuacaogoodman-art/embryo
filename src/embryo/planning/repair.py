"""JSON Repair & Validation Pipeline

LLM 输出 → JSON 提取 → 修复 → Pydantic 校验 → 合法 Plan / 报错

处理以下常见 LLM 输出问题：
- JSON 外有解释文字
- markdown 代码块包裹
- 字段名拼写错误 / 缺失
- 多余逗号 / 缺少引号
- action 类型不在枚举内
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError

from ..logging import get_logger
from .schema import ActionType, PlanStep, Target, TargetType, TaskPlan, VerificationRule, VerificationType

logger = get_logger(__name__)


class PlanValidationError(Exception):
    """计划验证失败"""

    def __init__(self, message: str, raw_output: str = "", errors: list[dict] = None):
        super().__init__(message)
        self.raw_output = raw_output
        self.errors = errors or []


def extract_json_from_llm(raw: str) -> str:
    """从 LLM 输出中提取 JSON 内容

    处理：
    - markdown 代码块 ```json ... ```
    - 前后多余文字
    - 混合文字和 JSON
    """
    raw = raw.strip()

    # 去除 markdown 代码块
    code_block_match = re.search(r"```(?:json)?\s*\n?(.*?)```", raw, re.DOTALL)
    if code_block_match:
        raw = code_block_match.group(1).strip()

    # 提取 JSON 数组
    array_start = raw.find("[")
    array_end = raw.rfind("]")
    if array_start != -1 and array_end != -1 and array_end > array_start:
        return raw[array_start : array_end + 1]

    # 提取 JSON 对象
    obj_start = raw.find("{")
    obj_end = raw.rfind("}")
    if obj_start != -1 and obj_end != -1 and obj_end > obj_start:
        return raw[obj_start : obj_end + 1]

    return raw


def repair_json(raw_json: str) -> str:
    """修复常见的 JSON 格式问题

    - 去除尾部多余逗号
    - 修复单引号 → 双引号
    - 去除注释
    """
    # 去除单行注释
    text = re.sub(r"//[^\n]*", "", raw_json)
    # 去除多行注释
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    # 尾部逗号 (array): ,]
    text = re.sub(r",\s*]", "]", text)
    # 尾部逗号 (object): ,}
    text = re.sub(r",\s*}", "}", text)
    # 单引号 → 双引号（简单替换，不处理嵌套）
    # 仅当整个 JSON 都用单引号时才替换
    if "'" in text and '"' not in text:
        text = text.replace("'", '"')

    return text


def _normalize_step_dict(raw_step: dict[str, Any]) -> dict[str, Any]:
    """标准化单个步骤字典，修复常见字段问题

    - action 字段映射（type → type_text 等）
    - target 字段标准化
    - 缺失字段补默认值
    """
    step = dict(raw_step)

    # 确保 description 存在
    if "description" not in step or not step["description"]:
        step["description"] = step.get("desc", step.get("name", "未命名步骤"))

    # action 标准化
    action = step.get("action", "observe")
    action_aliases = {
        "type": "type_text",
        "input": "type_text",
        "write": "type_text",
        "screenshot": "observe",
        "capture": "observe",
        "ocr": "observe",
        "key": "press_key",
        "press": "press_key",
        "keys": "hotkey",
        "shortcut": "hotkey",
        "move": "mouse_move",
        "hover": "mouse_move",
        "search": "find_text",
        "find": "find_text",
        "check": "verify",
        "assert": "verify",
    }
    if isinstance(action, str):
        action = action.lower().strip()
        action = action_aliases.get(action, action)
    step["action"] = action

    # target 标准化
    raw_target = step.get("target", "")
    if isinstance(raw_target, str):
        # 旧格式：target 是纯字符串
        step["target"] = {"type": "description", "value": raw_target}
    elif isinstance(raw_target, dict):
        # 新格式：target 是对象
        if "type" not in raw_target:
            raw_target["type"] = "description"
        step["target"] = raw_target
    else:
        step["target"] = {"type": "description", "value": str(raw_target)}

    # verification 标准化
    raw_verif = step.get("verification", [])
    if isinstance(raw_verif, str):
        # 旧格式："ocr_check:文字" → VerificationRule
        step["verification"] = _parse_verification_string(raw_verif)
    elif isinstance(raw_verif, list):
        normalized = []
        for v in raw_verif:
            if isinstance(v, str):
                normalized.extend(_parse_verification_string(v))
            elif isinstance(v, dict):
                normalized.append(v)
        step["verification"] = normalized

    # parameters 确保是 dict
    if "parameters" not in step or not isinstance(step.get("parameters"), dict):
        step["parameters"] = step.get("parameters", {}) or {}

    return step


def _parse_verification_string(verif_str: str) -> list[dict[str, Any]]:
    """将旧格式验证字符串转换为 VerificationRule 列表

    Examples:
        "ocr_check:登录成功" → [{"type": "text_visible", "target": "登录成功"}]
        "screenshot_diff" → [{"type": "screenshot_changed"}]
        "title_change" → [{"type": "screenshot_changed"}]
    """
    verif_str = verif_str.strip()
    if not verif_str:
        return []

    if verif_str.startswith("ocr_check:"):
        target = verif_str.split(":", 1)[1].strip()
        return [{"type": "text_visible", "target": target}]
    elif verif_str in ("screenshot_diff", "title_change"):
        return [{"type": "screenshot_changed"}]
    elif verif_str.startswith("element_visible:"):
        target = verif_str.split(":", 1)[1].strip()
        return [{"type": "element_visible", "target": target}]
    elif verif_str.startswith("url_contains:"):
        target = verif_str.split(":", 1)[1].strip()
        return [{"type": "url_contains", "target": target}]
    else:
        # 默认作为 text_visible
        return [{"type": "text_visible", "target": verif_str}]


def repair_and_validate_plan(
    raw_output: str,
    task_description: str,
    strict: bool = False,
) -> TaskPlan:
    """完整的 LLM 输出 → TaskPlan 管道

    流程：
    1. 从 LLM 原始输出提取 JSON
    2. 修复 JSON 格式
    3. 解析为 Python 对象
    4. 标准化每个步骤字典
    5. Pydantic 验证
    6. 丢弃无效步骤（非 strict）或抛异常（strict）

    Args:
        raw_output: LLM 的原始文本输出
        task_description: 任务描述（填入 TaskPlan.task）
        strict: 严格模式下，任何步骤校验失败都抛异常

    Returns:
        验证通过的 TaskPlan

    Raises:
        PlanValidationError: 无法解析或所有步骤都无效
    """
    # Step 1: 提取 JSON
    json_str = extract_json_from_llm(raw_output)
    if not json_str:
        raise PlanValidationError(
            "无法从 LLM 输出中提取 JSON",
            raw_output=raw_output,
        )

    # Step 2: 修复 JSON
    json_str = repair_json(json_str)

    # Step 3: 解析
    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise PlanValidationError(
            f"JSON 解析失败: {e}",
            raw_output=raw_output,
        )

    # 确保是列表
    if isinstance(parsed, dict):
        # 可能是 {"steps": [...]} 格式
        if "steps" in parsed:
            parsed = parsed["steps"]
        else:
            parsed = [parsed]

    if not isinstance(parsed, list):
        raise PlanValidationError(
            f"期望 JSON 数组，得到 {type(parsed).__name__}",
            raw_output=raw_output,
        )

    # Step 4 & 5: 标准化 + 验证每个步骤
    valid_steps: list[PlanStep] = []
    errors: list[dict] = []

    for i, raw_step in enumerate(parsed):
        if not isinstance(raw_step, dict):
            errors.append({"index": i, "error": f"步骤不是对象: {type(raw_step).__name__}"})
            if strict:
                raise PlanValidationError(
                    f"步骤 {i} 不是有效对象",
                    raw_output=raw_output,
                    errors=errors,
                )
            continue

        try:
            normalized = _normalize_step_dict(raw_step)
            step = PlanStep(**normalized)
            valid_steps.append(step)
        except (ValidationError, ValueError, TypeError) as e:
            error_msg = str(e)
            errors.append({"index": i, "error": error_msg, "raw": raw_step})
            logger.warning(
                "plan_step_validation_failed",
                step_index=i,
                error=error_msg[:100],
            )
            if strict:
                raise PlanValidationError(
                    f"步骤 {i} 验证失败: {error_msg}",
                    raw_output=raw_output,
                    errors=errors,
                )

    # Step 6: 检查是否有有效步骤
    if not valid_steps:
        raise PlanValidationError(
            f"所有 {len(parsed)} 个步骤验证失败",
            raw_output=raw_output,
            errors=errors,
        )

    if errors:
        logger.info(
            "plan_validation_partial",
            valid=len(valid_steps),
            invalid=len(errors),
            total=len(parsed),
        )

    # 构建 TaskPlan
    plan = TaskPlan(
        task=task_description,
        steps=valid_steps,
        metadata={"raw_step_count": len(parsed), "validation_errors": errors},
    )

    logger.info(
        "plan_validated",
        task=task_description[:50],
        steps=len(valid_steps),
        errors=len(errors),
    )

    return plan
