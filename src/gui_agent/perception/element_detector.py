"""UI 元素检测模块

综合使用 OCR、控件树和图像识别技术检测界面中的可操作元素。
"""

from __future__ import annotations

from typing import Optional

import numpy as np
from loguru import logger

from ..config import PerceptionConfig
from ..models import BoundingBox, ElementType, UIElement
from .ocr_engine import OCREngine, OCRResult


class ElementDetector:
    """UI 元素检测器"""

    def __init__(self, config: PerceptionConfig, ocr_engine: OCREngine):
        self.config = config
        self.ocr = ocr_engine

    def detect_elements(self, image: np.ndarray) -> list[UIElement]:
        """检测图像中的所有可操作 UI 元素

        综合使用多种方法进行元素检测：
        1. 基于 OCR 的文字按钮检测
        2. 基于图像特征的输入框检测
        3. 基于控件树（如果可用）

        Args:
            image: 当前屏幕截图 (BGR)

        Returns:
            检测到的UI元素列表
        """
        elements: list[UIElement] = []

        # 方法1: 基于OCR检测文字元素
        ocr_elements = self._detect_from_ocr(image)
        elements.extend(ocr_elements)

        # 方法2: 基于图像特征检测输入框和按钮
        visual_elements = self._detect_from_visual(image)
        elements.extend(visual_elements)

        # 方法3: 尝试使用控件树
        accessibility_elements = self._detect_from_accessibility()
        elements.extend(accessibility_elements)

        # 去重合并
        elements = self._merge_duplicates(elements)

        logger.debug(f"共检测到 {len(elements)} 个UI元素")
        return elements

    def find_element_by_text(self, image: np.ndarray, text: str) -> list[UIElement]:
        """根据文字查找元素

        Args:
            image: 屏幕截图
            text: 目标文字

        Returns:
            匹配的元素列表
        """
        ocr_results = self.ocr.find_text(image, text)
        elements = []

        for result in ocr_results:
            element_type = self._infer_element_type(result, image)
            elements.append(
                UIElement(
                    element_type=element_type,
                    label=result.text,
                    bbox=BoundingBox(
                        x1=result.bbox[0],
                        y1=result.bbox[1],
                        x2=result.bbox[2],
                        y2=result.bbox[3],
                    ),
                    confidence=result.confidence,
                )
            )

        return elements

    def find_element_by_template(
        self, image: np.ndarray, template: np.ndarray
    ) -> Optional[UIElement]:
        """通过模板匹配查找元素

        Args:
            image: 屏幕截图
            template: 模板图像

        Returns:
            匹配的元素，未找到返回None
        """
        import cv2

        result = cv2.matchTemplate(image, template, cv2.TM_CCOEFF_NORMED)
        min_val, max_val, min_loc, max_loc = cv2.minMaxLoc(result)

        if max_val >= self.config.template_match_threshold:
            h, w = template.shape[:2]
            x, y = max_loc
            return UIElement(
                element_type=ElementType.UNKNOWN,
                label="template_match",
                bbox=BoundingBox(x1=x, y1=y, x2=x + w, y2=y + h),
                confidence=float(max_val),
            )

        logger.debug(f"模板匹配未达阈值，最高匹配度: {max_val:.3f}")
        return None

    def _detect_from_ocr(self, image: np.ndarray) -> list[UIElement]:
        """通过 OCR 检测文字类元素"""
        ocr_results = self.ocr.recognize_full(image)
        elements = []

        for result in ocr_results:
            element_type = self._infer_element_type(result, image)
            elements.append(
                UIElement(
                    element_type=element_type,
                    label=result.text,
                    bbox=BoundingBox(
                        x1=result.bbox[0],
                        y1=result.bbox[1],
                        x2=result.bbox[2],
                        y2=result.bbox[3],
                    ),
                    confidence=result.confidence,
                )
            )

        return elements

    def _detect_from_visual(self, image: np.ndarray) -> list[UIElement]:
        """通过视觉特征检测元素（输入框、按钮等矩形区域）"""
        import cv2

        elements = []
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        # 边缘检测
        edges = cv2.Canny(gray, 50, 150)
        # 膨胀连接边缘
        kernel = np.ones((3, 3), np.uint8)
        dilated = cv2.dilate(edges, kernel, iterations=1)

        # 查找矩形轮廓
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

        for contour in contours:
            # 近似为矩形
            epsilon = 0.02 * cv2.arcLength(contour, True)
            approx = cv2.approxPolyDP(contour, epsilon, True)

            if len(approx) == 4:  # 四边形
                x, y, w, h = cv2.boundingRect(contour)

                # 过滤不合理的尺寸
                if w < 30 or h < 15 or w > image.shape[1] * 0.9 or h > image.shape[0] * 0.9:
                    continue

                # 根据宽高比和尺寸推断类型
                aspect_ratio = w / h
                if aspect_ratio > 3 and h < 50:
                    elem_type = ElementType.INPUT
                elif 1.5 < aspect_ratio < 5 and h < 60:
                    elem_type = ElementType.BUTTON
                else:
                    continue  # 跳过不明确的

                elements.append(
                    UIElement(
                        element_type=elem_type,
                        label="",
                        bbox=BoundingBox(x1=x, y1=y, x2=x + w, y2=y + h),
                        confidence=0.5,  # 视觉检测置信度较低
                    )
                )

        return elements

    def _detect_from_accessibility(self) -> list[UIElement]:
        """通过系统辅助功能（控件树）检测元素

        不同平台使用不同方式：
        - Windows: UI Automation / pywinauto
        - Linux: AT-SPI
        - macOS: AXUIElement
        """
        elements = []

        try:
            import platform

            if platform.system() == "Windows":
                elements = self._detect_windows_controls()
            elif platform.system() == "Linux":
                elements = self._detect_linux_controls()
        except Exception as e:
            logger.debug(f"控件树检测不可用: {e}")

        return elements

    def _detect_windows_controls(self) -> list[UIElement]:
        """Windows 平台控件树检测"""
        try:
            from pywinauto import Desktop

            desktop = Desktop(backend="uia")
            foreground = desktop.top_window()
            elements = []

            for ctrl in foreground.descendants():
                try:
                    rect = ctrl.rectangle()
                    ctrl_type = ctrl.element_info.control_type

                    type_mapping = {
                        "Button": ElementType.BUTTON,
                        "Edit": ElementType.INPUT,
                        "ComboBox": ElementType.DROPDOWN,
                        "CheckBox": ElementType.CHECKBOX,
                        "RadioButton": ElementType.RADIO,
                        "MenuItem": ElementType.MENU,
                        "Hyperlink": ElementType.LINK,
                        "TabItem": ElementType.TAB,
                    }

                    elem_type = type_mapping.get(ctrl_type, ElementType.UNKNOWN)
                    if elem_type == ElementType.UNKNOWN:
                        continue

                    elements.append(
                        UIElement(
                            element_type=elem_type,
                            label=ctrl.element_info.name or "",
                            bbox=BoundingBox(
                                x1=rect.left, y1=rect.top, x2=rect.right, y2=rect.bottom
                            ),
                            confidence=1.0,
                            is_enabled=ctrl.is_enabled(),
                            is_visible=ctrl.is_visible(),
                        )
                    )
                except Exception:
                    continue

            return elements

        except ImportError:
            return []

    def _detect_linux_controls(self) -> list[UIElement]:
        """Linux 平台控件树检测（AT-SPI）"""
        # AT-SPI 支持，当前为占位实现
        return []

    def _infer_element_type(self, ocr_result: OCRResult, image: np.ndarray) -> ElementType:
        """根据文字内容和周围视觉特征推断元素类型"""
        text = ocr_result.text.lower()

        # 关键词推断
        button_keywords = [
            "登录", "提交", "确定", "取消", "保存", "删除", "搜索", "查询",
            "login", "submit", "ok", "cancel", "save", "delete", "search",
            "confirm", "next", "back", "close", "sign in", "register",
        ]
        link_keywords = ["http", "www", "点击这里", "查看详情", "了解更多"]

        for kw in button_keywords:
            if kw in text:
                return ElementType.BUTTON

        for kw in link_keywords:
            if kw in text:
                return ElementType.LINK

        # 默认为文本
        return ElementType.TEXT

    def _merge_duplicates(self, elements: list[UIElement]) -> list[UIElement]:
        """合并重叠的重复检测结果"""
        if len(elements) <= 1:
            return elements

        merged = []
        used = set()

        for i, elem_a in enumerate(elements):
            if i in used:
                continue

            best = elem_a
            for j, elem_b in enumerate(elements):
                if j <= i or j in used:
                    continue

                # 计算IoU
                iou = self._calculate_iou(elem_a.bbox, elem_b.bbox)
                if iou > 0.5:
                    used.add(j)
                    # 保留置信度更高的
                    if elem_b.confidence > best.confidence:
                        best = elem_b

            merged.append(best)

        return merged

    def _calculate_iou(self, box_a: BoundingBox, box_b: BoundingBox) -> float:
        """计算两个边界框的 IoU"""
        x1 = max(box_a.x1, box_b.x1)
        y1 = max(box_a.y1, box_b.y1)
        x2 = min(box_a.x2, box_b.x2)
        y2 = min(box_a.y2, box_b.y2)

        if x2 <= x1 or y2 <= y1:
            return 0.0

        intersection = (x2 - x1) * (y2 - y1)
        area_a = box_a.width * box_a.height
        area_b = box_b.width * box_b.height
        union = area_a + area_b - intersection

        return intersection / union if union > 0 else 0.0
