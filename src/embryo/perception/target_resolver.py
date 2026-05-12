"""TargetResolver - 从语义目标到具体坐标/元素

Planner 生成语义化的 Target（如 {"type": "text", "value": "登录"}），
TargetResolver 根据当前 Observation 将其解析为可执行的坐标或 DOM 元素。

解析策略优先级：
1. css_selector / xpath → 直接走 DOM（浏览器后端）
2. text → OCR boxes 匹配
3. role / label → accessibility tree 匹配
4. placeholder → accessibility/DOM 匹配
5. image → 模板匹配（TODO）
6. coordinate → 直接使用
7. description → LLM 辅助定位（fallback）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Optional

from ..logging import get_logger
from ..planning.schema import Target, TargetType
from .observation import Observation, OCRBox, UIElement

logger = get_logger(__name__)


class ResolveStatus(str, Enum):
    """定位结果状态"""

    FOUND = "found"  # 唯一匹配
    AMBIGUOUS = "ambiguous"  # 多个匹配
    NOT_FOUND = "not_found"  # 未找到
    FALLBACK = "fallback"  # 通过 fallback 策略找到


@dataclass
class ResolvedTarget:
    """解析后的目标位置"""

    status: ResolveStatus
    x: int = 0
    y: int = 0
    width: int = 0
    height: int = 0
    confidence: float = 0.0  # 0-100
    source: str = ""  # 定位来源：ocr / accessibility / dom / coordinate / llm
    element: Optional[UIElement] = None  # 如果从 accessibility tree 解析
    ocr_box: Optional[OCRBox] = None  # 如果从 OCR 解析
    all_matches: list[Any] = field(default_factory=list)  # 所有候选
    message: str = ""  # 人类可读信息

    @property
    def cx(self) -> int:
        """中心点 x"""
        return self.x + self.width // 2 if self.width else self.x

    @property
    def cy(self) -> int:
        """中心点 y"""
        return self.y + self.height // 2 if self.height else self.y

    @property
    def found(self) -> bool:
        return self.status in (ResolveStatus.FOUND, ResolveStatus.FALLBACK)


class TargetResolver:
    """目标解析器

    根据 Target 定义和当前 Observation，解析出具体的屏幕坐标。

    用法：
        resolver = TargetResolver()
        resolved = resolver.resolve(target, observation)
        if resolved.found:
            backend.click(resolved.cx, resolved.cy)
    """

    def __init__(self, llm_call: Optional[Callable[[str], str]] = None):
        """
        Args:
            llm_call: 可选的 LLM 调用函数，用于 description 类型的 fallback 定位
        """
        self._llm_call = llm_call

    def resolve(self, target: Target, observation: Observation) -> ResolvedTarget:
        """解析目标

        根据 target.type 选择对应的解析策略。

        Args:
            target: 语义化目标定义
            observation: 当前感知快照

        Returns:
            ResolvedTarget 包含坐标和状态
        """
        # 直接坐标
        if target.type == TargetType.COORDINATE:
            return self._resolve_coordinate(target)

        # CSS / XPath → DOM
        if target.type in (TargetType.CSS_SELECTOR, TargetType.XPATH):
            return self._resolve_dom_selector(target, observation)

        # text → OCR
        if target.type == TargetType.TEXT:
            return self._resolve_by_text(target, observation)

        # role → accessibility tree
        if target.type == TargetType.ROLE:
            return self._resolve_by_role(target, observation)

        # label → accessibility tree name
        if target.type == TargetType.LABEL:
            return self._resolve_by_label(target, observation)

        # placeholder → accessibility/DOM
        if target.type == TargetType.PLACEHOLDER:
            return self._resolve_by_placeholder(target, observation)

        # image → template match (TODO)
        if target.type == TargetType.IMAGE:
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message="图像匹配暂未实现",
            )

        # description → 综合策略
        if target.type == TargetType.DESCRIPTION:
            return self._resolve_by_description(target, observation)

        return ResolvedTarget(
            status=ResolveStatus.NOT_FOUND,
            message=f"未知目标类型: {target.type}",
        )

    # --------------------------------------------------
    # 具体解析策略
    # --------------------------------------------------

    def _resolve_coordinate(self, target: Target) -> ResolvedTarget:
        """直接坐标定位"""
        if target.x is not None and target.y is not None:
            return ResolvedTarget(
                status=ResolveStatus.FOUND,
                x=target.x,
                y=target.y,
                confidence=100.0,
                source="coordinate",
                message=f"直接坐标 ({target.x}, {target.y})",
            )
        return ResolvedTarget(
            status=ResolveStatus.NOT_FOUND,
            message="坐标类型目标缺少 x/y",
        )

    def _resolve_by_text(self, target: Target, obs: Observation) -> ResolvedTarget:
        """通过 OCR 文字定位"""
        if not target.value:
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message="text 类型目标缺少 value",
            )

        matches = obs.find_text_boxes(target.value, min_confidence=40.0)

        # 如果有 near_text 限定，进一步过滤
        if target.near_text and len(matches) > 1:
            near_boxes = obs.find_text_boxes(target.near_text, min_confidence=40.0)
            if near_boxes:
                matches = self._filter_by_proximity(matches, near_boxes)

        if not matches:
            # 尝试模糊匹配：检查 OCR 全文
            if obs.has_text(target.value):
                return ResolvedTarget(
                    status=ResolveStatus.NOT_FOUND,
                    message=f"文字 '{target.value}' 存在于 OCR 全文但无精确框定位",
                )
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message=f"未找到文字: '{target.value}'",
            )

        if len(matches) == 1 or target.index == 0:
            box = matches[min(target.index, len(matches) - 1)]
            return ResolvedTarget(
                status=ResolveStatus.FOUND,
                x=box.x,
                y=box.y,
                width=box.width,
                height=box.height,
                confidence=box.confidence,
                source="ocr",
                ocr_box=box,
                all_matches=matches,
                message=f"OCR 定位: '{box.text}' @ ({box.cx}, {box.cy})",
            )

        # 多个匹配
        box = matches[min(target.index, len(matches) - 1)]
        return ResolvedTarget(
            status=ResolveStatus.AMBIGUOUS,
            x=box.x,
            y=box.y,
            width=box.width,
            height=box.height,
            confidence=box.confidence,
            source="ocr",
            ocr_box=box,
            all_matches=matches,
            message=f"找到 {len(matches)} 处匹配 '{target.value}'，使用第 {target.index} 个",
        )

    def _resolve_by_role(self, target: Target, obs: Observation) -> ResolvedTarget:
        """通过 UI role 定位（accessibility tree）"""
        if not target.value:
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message="role 类型目标缺少 value",
            )

        elements = obs.find_elements_by_role(target.value)

        # 如果 near_text 有值，用名称再过滤
        if target.near_text and elements:
            elements = [e for e in elements if target.near_text.lower() in e.name.lower()]

        if not elements:
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message=f"未找到 role='{target.value}' 的元素",
            )

        el = elements[min(target.index, len(elements) - 1)]
        status = ResolveStatus.FOUND if len(elements) == 1 else ResolveStatus.AMBIGUOUS

        return ResolvedTarget(
            status=status,
            x=el.x,
            y=el.y,
            width=el.width,
            height=el.height,
            confidence=90.0,
            source="accessibility",
            element=el,
            all_matches=elements,
            message=f"Accessibility 定位: role='{target.value}' name='{el.name}' @ ({el.cx}, {el.cy})",
        )

    def _resolve_by_label(self, target: Target, obs: Observation) -> ResolvedTarget:
        """通过 label/name 定位"""
        if not target.value:
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message="label 类型目标缺少 value",
            )

        elements = obs.find_elements_by_name(target.value)

        if not elements:
            # 回退到 OCR 文字匹配
            return self._resolve_by_text(
                Target(type=TargetType.TEXT, value=target.value, near_text=target.near_text, index=target.index),
                obs,
            )

        el = elements[min(target.index, len(elements) - 1)]
        status = ResolveStatus.FOUND if len(elements) == 1 else ResolveStatus.AMBIGUOUS

        return ResolvedTarget(
            status=status,
            x=el.x,
            y=el.y,
            width=el.width,
            height=el.height,
            confidence=85.0,
            source="accessibility",
            element=el,
            all_matches=elements,
            message=f"Label 定位: '{el.name}' @ ({el.cx}, {el.cy})",
        )

    def _resolve_by_placeholder(self, target: Target, obs: Observation) -> ResolvedTarget:
        """通过 placeholder 定位（通常是输入框）"""
        if not target.value:
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message="placeholder 类型目标缺少 value",
            )

        # 在 accessibility tree 中查找 value 匹配 placeholder 的元素
        results = []
        self._search_placeholder(obs.accessibility_tree, target.value, results)

        if not results:
            # 回退到 OCR
            return self._resolve_by_text(
                Target(type=TargetType.TEXT, value=target.value, index=target.index),
                obs,
            )

        el = results[min(target.index, len(results) - 1)]
        return ResolvedTarget(
            status=ResolveStatus.FOUND if len(results) == 1 else ResolveStatus.AMBIGUOUS,
            x=el.x,
            y=el.y,
            width=el.width,
            height=el.height,
            confidence=80.0,
            source="accessibility",
            element=el,
            all_matches=results,
            message=f"Placeholder 定位: '{el.name}' @ ({el.cx}, {el.cy})",
        )

    def _resolve_dom_selector(self, target: Target, obs: Observation) -> ResolvedTarget:
        """通过 CSS selector / XPath 定位（需要 DOM 快照）"""
        if obs.dom_snapshot is None:
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message="DOM 快照不可用（非浏览器后端或未启用 DOM 采集）",
            )

        # DOM selector 解析需要 PlaywrightBackend 配合
        # 这里标记为 fallback，实际执行时由 PlaywrightBackend 直接操作
        return ResolvedTarget(
            status=ResolveStatus.FALLBACK,
            source="dom",
            message=f"DOM selector '{target.value}' 将由浏览器后端直接执行",
        )

    def _resolve_by_description(self, target: Target, obs: Observation) -> ResolvedTarget:
        """通过自然语言描述定位（综合策略）

        尝试顺序：
        1. 将描述当作 text 去 OCR 匹配
        2. 将描述当作 name 去 accessibility tree 匹配
        3. 调用 LLM 辅助定位（如果可用）
        """
        if not target.value:
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message="description 类型目标缺少 value",
            )

        # 策略 1: 当作文字去 OCR 匹配
        text_result = self._resolve_by_text(
            Target(type=TargetType.TEXT, value=target.value, near_text=target.near_text, index=target.index),
            obs,
        )
        if text_result.found:
            text_result.source = "ocr+description"
            return text_result

        # 策略 2: 当作 name 去 accessibility tree 匹配
        label_result = self._resolve_by_label(
            Target(type=TargetType.LABEL, value=target.value, near_text=target.near_text, index=target.index),
            obs,
        )
        if label_result.found:
            label_result.source = "accessibility+description"
            return label_result

        # 策略 3: LLM 辅助
        if self._llm_call:
            return self._resolve_with_llm(target, obs)

        return ResolvedTarget(
            status=ResolveStatus.NOT_FOUND,
            message=f"无法通过描述 '{target.value}' 定位目标",
        )

    def _resolve_with_llm(self, target: Target, obs: Observation) -> ResolvedTarget:
        """用 LLM 辅助定位

        将 OCR boxes 和 accessibility tree 信息发给 LLM，
        让 LLM 判断哪个元素最匹配描述。
        """
        # 构建候选列表
        candidates = []
        for i, box in enumerate(obs.ocr_boxes[:30]):  # 最多 30 个候选
            candidates.append(f"[{i}] text='{box.text}' @ ({box.cx},{box.cy}) conf={box.confidence:.0f}%")

        if not candidates:
            return ResolvedTarget(
                status=ResolveStatus.NOT_FOUND,
                message=f"屏幕上无 OCR 候选，无法定位 '{target.value}'",
            )

        prompt = (
            f"在以下 OCR 识别结果中，找到最匹配 \"{target.value}\" 的元素。\n"
            f"只输出元素编号（数字），如果都不匹配输出 -1。\n\n"
            f"候选列表:\n" + "\n".join(candidates) + "\n\n"
            f"答案:"
        )

        try:
            response = self._llm_call(prompt).strip()
            # 提取数字
            import re
            match = re.search(r"(-?\d+)", response)
            if match:
                idx = int(match.group(1))
                if 0 <= idx < len(obs.ocr_boxes):
                    box = obs.ocr_boxes[idx]
                    return ResolvedTarget(
                        status=ResolveStatus.FALLBACK,
                        x=box.x,
                        y=box.y,
                        width=box.width,
                        height=box.height,
                        confidence=box.confidence * 0.8,  # LLM 定位打折
                        source="llm",
                        ocr_box=box,
                        message=f"LLM 定位: '{box.text}' @ ({box.cx},{box.cy})",
                    )
        except Exception as e:
            logger.warning("target_resolver_llm_failed", error=str(e))

        return ResolvedTarget(
            status=ResolveStatus.NOT_FOUND,
            message=f"LLM 辅助定位失败: '{target.value}'",
        )

    # --------------------------------------------------
    # 辅助方法
    # --------------------------------------------------

    def _filter_by_proximity(
        self,
        matches: list[OCRBox],
        reference_boxes: list[OCRBox],
        max_distance: int = 200,
    ) -> list[OCRBox]:
        """根据与参考框的距离过滤候选"""
        if not reference_boxes:
            return matches

        ref_cx = sum(b.cx for b in reference_boxes) // len(reference_boxes)
        ref_cy = sum(b.cy for b in reference_boxes) // len(reference_boxes)

        scored = []
        for box in matches:
            dist = ((box.cx - ref_cx) ** 2 + (box.cy - ref_cy) ** 2) ** 0.5
            if dist <= max_distance:
                scored.append((dist, box))

        scored.sort(key=lambda x: x[0])
        return [box for _, box in scored] if scored else matches

    def _search_placeholder(
        self,
        elements: list[UIElement],
        placeholder: str,
        results: list[UIElement],
    ) -> None:
        """递归搜索 placeholder 匹配的元素"""
        for el in elements:
            # 检查 attributes 中是否有 placeholder
            el_placeholder = el.attributes.get("placeholder", "")
            if placeholder.lower() in el_placeholder.lower():
                results.append(el)
            # 也检查 description
            if placeholder.lower() in el.description.lower():
                results.append(el)
            if el.children:
                self._search_placeholder(el.children, placeholder, results)
