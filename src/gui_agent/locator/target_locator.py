"""目标定位模块

负责将用户任务中的操作目标转化为具体的屏幕坐标，
综合使用 OCR 文本匹配、控件树查询、图像模板匹配等多种策略。
"""

from __future__ import annotations

from enum import Enum
from typing import Optional

import numpy as np
from loguru import logger

from ..config import PerceptionConfig
from ..models import BoundingBox, ElementType, ScreenState, UIElement
from ..perception.element_detector import ElementDetector
from ..perception.ocr_engine import OCREngine


class LocateStrategy(str, Enum):
    """定位策略"""
    TEXT_MATCH = "text_match"          # OCR 文字匹配
    ACCESSIBILITY = "accessibility"    # 控件树查询
    TEMPLATE = "template"              # 图像模板匹配
    SPATIAL = "spatial"                # 空间关系定位
    FUZZY = "fuzzy"                    # 模糊匹配


class LocateResult:
    """定位结果"""

    def __init__(
        self,
        element: UIElement,
        strategy: LocateStrategy,
        click_point: tuple[int, int],
        confidence: float,
    ):
        self.element = element
        self.strategy = strategy
        self.click_point = click_point
        self.confidence = confidence

    def __repr__(self) -> str:
        return (
            f"LocateResult(target='{self.element.label}', "
            f"point={self.click_point}, "
            f"strategy={self.strategy.value}, "
            f"conf={self.confidence:.2f})"
        )


class TargetLocator:
    """目标定位器

    给定用户目标描述和当前屏幕状态，定位出具体可操作的坐标位置。
    采用多策略优先级机制，确保定位准确性。
    """

    def __init__(self, config: PerceptionConfig, ocr_engine: OCREngine, element_detector: ElementDetector):
        self.config = config
        self.ocr = ocr_engine
        self.detector = element_detector

        # 策略优先级（从高到低）
        self._strategy_priority = [
            LocateStrategy.ACCESSIBILITY,
            LocateStrategy.TEXT_MATCH,
            LocateStrategy.TEMPLATE,
            LocateStrategy.SPATIAL,
            LocateStrategy.FUZZY,
        ]

    def locate(
        self,
        target_description: str,
        screen_state: ScreenState,
        image: Optional[np.ndarray] = None,
        preferred_type: Optional[ElementType] = None,
    ) -> Optional[LocateResult]:
        """定位目标元素

        按策略优先级依次尝试，返回最高置信度的结果。

        Args:
            target_description: 目标描述（如"登录按钮"、"用户名输入框"）
            screen_state: 当前界面状态
            image: 当前屏幕截图（用于OCR和模板匹配）
            preferred_type: 优先匹配的元素类型

        Returns:
            定位结果，未找到返回None
        """
        logger.info(f"开始定位目标: '{target_description}'")

        candidates: list[LocateResult] = []

        # 策略1: 从已检测的元素列表中文字匹配
        text_results = self._locate_by_text(target_description, screen_state, preferred_type)
        candidates.extend(text_results)

        # 策略2: 通过模糊匹配查找
        fuzzy_results = self._locate_by_fuzzy(target_description, screen_state, preferred_type)
        candidates.extend(fuzzy_results)

        # 策略3: 通过空间关系定位
        spatial_results = self._locate_by_spatial(target_description, screen_state)
        candidates.extend(spatial_results)

        # 如果有图像，可以尝试模板匹配
        if image is not None:
            template_results = self._locate_by_template(target_description, image)
            candidates.extend(template_results)

        if not candidates:
            logger.warning(f"目标 '{target_description}' 未找到任何匹配")
            return None

        # 选择最佳结果
        best = max(candidates, key=lambda r: r.confidence)
        logger.info(f"定位结果: {best}")
        return best

    def locate_all(
        self,
        target_description: str,
        screen_state: ScreenState,
        image: Optional[np.ndarray] = None,
    ) -> list[LocateResult]:
        """定位所有匹配目标

        Args:
            target_description: 目标描述
            screen_state: 当前界面状态
            image: 屏幕截图

        Returns:
            所有匹配结果列表（按置信度降序）
        """
        candidates: list[LocateResult] = []

        text_results = self._locate_by_text(target_description, screen_state)
        candidates.extend(text_results)

        fuzzy_results = self._locate_by_fuzzy(target_description, screen_state)
        candidates.extend(fuzzy_results)

        # 按置信度排序
        candidates.sort(key=lambda r: r.confidence, reverse=True)
        return candidates

    def locate_by_coordinates(self, x: int, y: int, screen_state: ScreenState) -> Optional[UIElement]:
        """根据坐标查找该位置的元素

        Args:
            x, y: 目标坐标
            screen_state: 当前界面状态

        Returns:
            该坐标处的UI元素
        """
        for element in screen_state.elements:
            bbox = element.bbox
            if bbox.x1 <= x <= bbox.x2 and bbox.y1 <= y <= bbox.y2:
                return element
        return None

    def refine_click_point(self, element: UIElement) -> tuple[int, int]:
        """精确计算元素的点击坐标

        不同类型元素的点击位置策略不同：
        - 按钮：中心点
        - 输入框：左侧偏中（方便从头输入）
        - 复选框/单选框：中心点
        - 下拉框：右侧箭头区域

        Args:
            element: 目标UI元素

        Returns:
            最佳点击坐标 (x, y)
        """
        bbox = element.bbox
        center_x, center_y = bbox.center

        if element.element_type == ElementType.INPUT:
            # 输入框：点击左侧 1/4 处
            click_x = bbox.x1 + bbox.width // 4
            click_y = center_y
            return (click_x, click_y)

        elif element.element_type == ElementType.DROPDOWN:
            # 下拉框：点击右侧箭头
            click_x = bbox.x2 - 15
            click_y = center_y
            return (click_x, click_y)

        elif element.element_type == ElementType.CHECKBOX:
            # 复选框：中心偏左（勾选区域）
            click_x = bbox.x1 + min(15, bbox.width // 3)
            click_y = center_y
            return (click_x, click_y)

        else:
            # 默认：中心点
            return (center_x, center_y)

    def _locate_by_text(
        self,
        target: str,
        state: ScreenState,
        preferred_type: Optional[ElementType] = None,
    ) -> list[LocateResult]:
        """通过精确/包含文字匹配"""
        results = []
        target_lower = target.lower()

        for element in state.elements:
            label_lower = element.label.lower()

            # 计算匹配得分
            score = 0.0
            if label_lower == target_lower:
                score = 1.0  # 精确匹配
            elif target_lower in label_lower:
                score = 0.8  # 目标是子串
            elif label_lower in target_lower:
                score = 0.6  # 标签是子串
            else:
                continue

            # 如果指定了类型优先，匹配类型加分
            if preferred_type and element.element_type == preferred_type:
                score = min(score + 0.1, 1.0)

            # 元素置信度影响最终分数
            final_score = score * element.confidence

            click_point = self.refine_click_point(element)
            results.append(
                LocateResult(
                    element=element,
                    strategy=LocateStrategy.TEXT_MATCH,
                    click_point=click_point,
                    confidence=final_score,
                )
            )

        return results

    def _locate_by_fuzzy(
        self,
        target: str,
        state: ScreenState,
        preferred_type: Optional[ElementType] = None,
    ) -> list[LocateResult]:
        """模糊匹配（容许错别字或部分匹配）"""
        results = []
        target_chars = set(target.lower())

        for element in state.elements:
            if not element.label:
                continue

            label_chars = set(element.label.lower())

            # 字符集交集比例
            if not target_chars:
                continue
            overlap = len(target_chars & label_chars) / len(target_chars)

            if overlap >= 0.6:
                # 计算编辑距离相似度
                similarity = self._calculate_similarity(target.lower(), element.label.lower())

                if similarity >= 0.5:
                    score = similarity * 0.7  # 模糊匹配的基础分较低

                    if preferred_type and element.element_type == preferred_type:
                        score = min(score + 0.1, 1.0)

                    click_point = self.refine_click_point(element)
                    results.append(
                        LocateResult(
                            element=element,
                            strategy=LocateStrategy.FUZZY,
                            click_point=click_point,
                            confidence=score,
                        )
                    )

        return results

    def _locate_by_spatial(self, target: str, state: ScreenState) -> list[LocateResult]:
        """通过空间关系定位

        例如："用户名输入框" → 找到"用户名"文字标签，然后定位其右侧/下方的输入框
        """
        results = []

        # 解析目标描述中的标签和元素类型
        label_text, elem_type = self._parse_target_description(target)

        if not label_text or not elem_type:
            return results

        # 先找到标签文字
        label_elements = [
            e for e in state.elements
            if label_text.lower() in e.label.lower()
        ]

        if not label_elements:
            return results

        # 找目标类型的元素
        type_elements = [
            e for e in state.elements
            if e.element_type == elem_type
        ]

        # 对每个标签，找最近的目标类型元素
        for label_elem in label_elements:
            label_center = label_elem.bbox.center
            nearest = None
            min_dist = float("inf")

            for type_elem in type_elements:
                type_center = type_elem.bbox.center
                # 优先找右侧和下方的元素
                dx = type_center[0] - label_center[0]
                dy = type_center[1] - label_center[1]

                # 右侧或下方优先
                if dx >= -20 and dy >= -20:
                    dist = (dx ** 2 + dy ** 2) ** 0.5
                    if dist < min_dist and dist < 300:  # 距离阈值
                        min_dist = dist
                        nearest = type_elem

            if nearest:
                click_point = self.refine_click_point(nearest)
                # 距离越近置信度越高
                confidence = max(0.3, 1.0 - min_dist / 300.0) * 0.8
                results.append(
                    LocateResult(
                        element=nearest,
                        strategy=LocateStrategy.SPATIAL,
                        click_point=click_point,
                        confidence=confidence,
                    )
                )

        return results

    def _locate_by_template(self, target: str, image: np.ndarray) -> list[LocateResult]:
        """通过模板匹配定位（需预先加载模板库）

        当前为占位实现，实际使用时需要维护一个模板图像库。
        """
        # TODO: 实现模板库加载和匹配
        return []

    def _parse_target_description(self, description: str) -> tuple[Optional[str], Optional[ElementType]]:
        """解析目标描述，提取标签文字和元素类型

        例如：
        - "用户名输入框" → ("用户名", INPUT)
        - "登录按钮" → ("登录", BUTTON)
        - "确定按钮" → ("确定", BUTTON)
        """
        type_keywords = {
            ElementType.BUTTON: ["按钮", "button", "btn"],
            ElementType.INPUT: ["输入框", "文本框", "input", "textbox", "编辑框"],
            ElementType.CHECKBOX: ["复选框", "勾选", "checkbox"],
            ElementType.DROPDOWN: ["下拉框", "下拉", "select", "dropdown"],
            ElementType.LINK: ["链接", "link"],
            ElementType.MENU: ["菜单", "menu"],
            ElementType.TAB: ["标签页", "选项卡", "tab"],
        }

        desc_lower = description.lower()

        for elem_type, keywords in type_keywords.items():
            for kw in keywords:
                if kw in desc_lower:
                    # 去除类型关键词，剩余部分为标签
                    label = desc_lower.replace(kw, "").strip()
                    if label:
                        return (label, elem_type)

        return (None, None)

    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """计算两个字符串的相似度（基于最长公共子序列）"""
        m, n = len(s1), len(s2)
        if m == 0 or n == 0:
            return 0.0

        # LCS 动态规划
        dp = [[0] * (n + 1) for _ in range(m + 1)]
        for i in range(1, m + 1):
            for j in range(1, n + 1):
                if s1[i - 1] == s2[j - 1]:
                    dp[i][j] = dp[i - 1][j - 1] + 1
                else:
                    dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])

        lcs_len = dp[m][n]
        return 2.0 * lcs_len / (m + n)
