"""OCR 文字识别引擎

负责从屏幕截图中提取文字信息，支持中英文混合识别。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from loguru import logger

from ..config import PerceptionConfig


@dataclass
class OCRResult:
    """OCR 识别结果"""
    text: str
    confidence: float
    bbox: tuple[int, int, int, int]  # (x1, y1, x2, y2)
    level: int = 5  # tesseract level: 1=page, 2=block, 3=para, 4=line, 5=word


class OCREngine:
    """OCR 文字识别引擎"""

    def __init__(self, config: PerceptionConfig):
        self.config = config
        self._language = config.ocr_language
        self._confidence_threshold = config.ocr_confidence_threshold

    def recognize_full(self, image: np.ndarray) -> list[OCRResult]:
        """全图文字识别

        Args:
            image: BGR格式图像数组

        Returns:
            识别到的文字列表（含位置和置信度）
        """
        try:
            import pytesseract
            import cv2

            # BGR转RGB
            rgb_image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)

            # 使用pytesseract获取详细结果
            data = pytesseract.image_to_data(
                rgb_image, lang=self._language, output_type=pytesseract.Output.DICT
            )

            results = []
            n_boxes = len(data["text"])

            for i in range(n_boxes):
                text = data["text"][i].strip()
                conf = float(data["conf"][i])

                if text and conf >= self._confidence_threshold * 100:
                    x = data["left"][i]
                    y = data["top"][i]
                    w = data["width"][i]
                    h = data["height"][i]

                    results.append(
                        OCRResult(
                            text=text,
                            confidence=conf / 100.0,
                            bbox=(x, y, x + w, y + h),
                            level=data["level"][i],
                        )
                    )

            logger.debug(f"OCR识别完成，共 {len(results)} 个文本块")
            return results

        except Exception as e:
            logger.error(f"OCR识别失败: {e}")
            return []

    def recognize_region(
        self, image: np.ndarray, x1: int, y1: int, x2: int, y2: int
    ) -> list[OCRResult]:
        """区域文字识别

        Args:
            image: 完整截图
            x1, y1, x2, y2: 识别区域坐标

        Returns:
            该区域内的文字列表
        """
        region = image[y1:y2, x1:x2]
        results = self.recognize_full(region)

        # 坐标偏移回全图坐标系
        adjusted_results = []
        for r in results:
            adjusted_results.append(
                OCRResult(
                    text=r.text,
                    confidence=r.confidence,
                    bbox=(r.bbox[0] + x1, r.bbox[1] + y1, r.bbox[2] + x1, r.bbox[3] + y1),
                    level=r.level,
                )
            )

        return adjusted_results

    def find_text(self, image: np.ndarray, target_text: str) -> list[OCRResult]:
        """在图像中查找指定文字

        Args:
            image: 截图
            target_text: 目标文字

        Returns:
            匹配的文字结果列表
        """
        all_results = self.recognize_full(image)
        matches = []

        for result in all_results:
            if target_text in result.text or result.text in target_text:
                matches.append(result)

        # 也尝试行级别拼接匹配
        lines = self._merge_to_lines(all_results)
        for line in lines:
            if target_text in line.text:
                matches.append(line)

        logger.debug(f"查找文字 '{target_text}'，找到 {len(matches)} 个匹配")
        return matches

    def get_all_text(self, image: np.ndarray) -> list[str]:
        """获取图像中所有可见文字（去重）

        Args:
            image: 截图

        Returns:
            文字列表
        """
        results = self.recognize_full(image)
        texts = list(set(r.text for r in results if len(r.text) > 0))
        return texts

    def _merge_to_lines(self, results: list[OCRResult]) -> list[OCRResult]:
        """将单词级结果合并为行级结果

        根据垂直位置将同一行的词合并。
        """
        if not results:
            return []

        # 按y坐标排序分组（同一行y坐标接近）
        sorted_results = sorted(results, key=lambda r: (r.bbox[1], r.bbox[0]))
        lines: list[list[OCRResult]] = []
        current_line: list[OCRResult] = [sorted_results[0]]

        for i in range(1, len(sorted_results)):
            prev = sorted_results[i - 1]
            curr = sorted_results[i]

            # 如果y坐标差距不大，认为是同一行
            prev_center_y = (prev.bbox[1] + prev.bbox[3]) // 2
            curr_center_y = (curr.bbox[1] + curr.bbox[3]) // 2

            if abs(curr_center_y - prev_center_y) < 15:
                current_line.append(curr)
            else:
                lines.append(current_line)
                current_line = [curr]

        lines.append(current_line)

        # 合并每行
        merged = []
        for line_words in lines:
            if not line_words:
                continue
            text = " ".join(w.text for w in line_words)
            x1 = min(w.bbox[0] for w in line_words)
            y1 = min(w.bbox[1] for w in line_words)
            x2 = max(w.bbox[2] for w in line_words)
            y2 = max(w.bbox[3] for w in line_words)
            avg_conf = sum(w.confidence for w in line_words) / len(line_words)

            merged.append(
                OCRResult(text=text, confidence=avg_conf, bbox=(x1, y1, x2, y2), level=4)
            )

        return merged

    def preprocess_for_ocr(self, image: np.ndarray) -> np.ndarray:
        """图像预处理以提高OCR准确率

        Args:
            image: 原始图像

        Returns:
            预处理后的图像
        """
        import cv2

        # 转灰度
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        # 自适应阈值二值化
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 11, 2
        )
        # 去噪
        denoised = cv2.fastNlMeansDenoising(binary, h=10)

        return denoised
