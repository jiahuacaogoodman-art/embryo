"""Set-of-Mark (SoM) 标注器

在截图上给每个可交互元素画彩色编号标注。
标注后的图片发给 Vision LLM，LLM 通过编号精确指向元素。

工作流程：
1. 拿到原始截图
2. 通过 OCR boxes / accessibility tree / Vision LLM 初步分析，得到元素列表
3. 在截图上给每个元素画带编号的标注框
4. 把标注后的图片 + 元素列表摘要发给 Vision LLM 做决策
5. LLM 返回 target_id=3，我们通过编号查表拿到坐标

为什么这样做：
- 直接让 LLM 输出坐标 → 不靠谱，LLM 不擅长像素级定位
- 给元素编号让 LLM 选号 → 非常靠谱，选择题比填空题简单
- OpenClaw/OmniParser 都用这个思路

依赖：Pillow（必需）
"""

from __future__ import annotations

import colorsys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..logging import get_logger

logger = get_logger(__name__)


# ============================================================
# 数据模型
# ============================================================


@dataclass
class SoMElement:
    """一个标注后的 UI 元素"""

    id: int  # 标注编号（从 1 开始）
    x: int  # 边界框左上角 x
    y: int  # 边界框左上角 y
    width: int
    height: int
    text: str = ""  # 可见文字
    type: str = ""  # button / input / link / etc
    source: str = ""  # ocr / accessibility / vision

    @property
    def cx(self) -> int:
        """中心点 x"""
        return self.x + self.width // 2

    @property
    def cy(self) -> int:
        """中心点 y"""
        return self.y + self.height // 2

    @property
    def bbox(self) -> tuple[int, int, int, int]:
        """(x1, y1, x2, y2)"""
        return (self.x, self.y, self.x + self.width, self.y + self.height)

    def summary(self) -> str:
        """一行摘要供 LLM prompt 使用"""
        type_str = f"[{self.type}]" if self.type else ""
        text_str = f'"{self.text}"' if self.text else "(no text)"
        return f"[{self.id}] {type_str} {text_str} @ ({self.cx},{self.cy})"


@dataclass
class SoMResult:
    """SoM 标注结果"""

    annotated_image_path: str  # 标注后的截图路径
    original_image_path: str  # 原始截图路径
    elements: list[SoMElement] = field(default_factory=list)
    screen_width: int = 0
    screen_height: int = 0

    def get_element(self, element_id: int) -> Optional[SoMElement]:
        """通过编号查找元素"""
        for el in self.elements:
            if el.id == element_id:
                return el
        return None

    def elements_summary(self, max_elements: int = 40) -> str:
        """生成元素摘要文本（发给 LLM）

        格式：
        [1] [button] "登录" @ (500,300)
        [2] [input] "用户名" @ (400,200)
        ...
        """
        lines = []
        for el in self.elements[:max_elements]:
            lines.append(el.summary())
        if len(self.elements) > max_elements:
            lines.append(f"... 共 {len(self.elements)} 个元素")
        return "\n".join(lines)


# ============================================================
# 颜色生成（给每个元素不同颜色）
# ============================================================


def _generate_colors(n: int) -> list[tuple[int, int, int]]:
    """生成 N 个视觉上容易区分的颜色

    使用 HSV 色环均匀分布，饱和度和亮度固定在高值。
    """
    colors = []
    for i in range(n):
        hue = i / max(n, 1)
        # 高饱和度 + 高亮度 = 在截图上醒目
        r, g, b = colorsys.hsv_to_rgb(hue, 0.9, 0.95)
        colors.append((int(r * 255), int(g * 255), int(b * 255)))
    return colors


# ============================================================
# SoM 标注器
# ============================================================


class SoMAnnotator:
    """Set-of-Mark 标注器

    在截图上给 UI 元素画编号标注。

    用法：
        annotator = SoMAnnotator()

        # 方式 1：从 OCR boxes 标注
        result = annotator.annotate_from_ocr(screenshot_path, ocr_boxes)

        # 方式 2：从 ScreenAnalysis 标注（Vision LLM 初步分析结果）
        result = annotator.annotate_from_analysis(screenshot_path, screen_analysis)

        # 方式 3：从 Observation 标注（综合 OCR + accessibility）
        result = annotator.annotate_from_observation(screenshot_path, observation)

        # 使用结果
        print(result.elements_summary())  # 给 LLM 的文本
        # LLM 返回 target_id=3
        element = result.get_element(3)
        backend.click(element.cx, element.cy)
    """

    def __init__(
        self,
        font_size: int = 14,
        box_thickness: int = 2,
        label_padding: int = 2,
        min_element_size: int = 10,
        output_dir: Optional[Path] = None,
    ):
        """
        Args:
            font_size: 标注文字大小
            box_thickness: 边框线宽
            label_padding: 标签内边距
            min_element_size: 最小元素尺寸（过小的忽略）
            output_dir: 标注图保存目录
        """
        self._font_size = font_size
        self._box_thickness = box_thickness
        self._label_padding = label_padding
        self._min_element_size = min_element_size
        self._output_dir = output_dir or Path.home() / ".embryo" / "som"

    def annotate_from_boxes(
        self,
        image_path: str | Path,
        boxes: list[dict[str, Any]],
    ) -> SoMResult:
        """从 bounding box 列表标注

        Args:
            image_path: 原始截图路径
            boxes: 元素列表，每项至少包含 {x, y, width/w, height/h}
                   可选: text, type, source

        Returns:
            SoMResult 包含标注图路径和元素列表
        """
        from PIL import Image, ImageDraw, ImageFont

        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(f"截图不存在: {image_path}")

        img = Image.open(image_path).convert("RGB")
        draw = ImageDraw.Draw(img)
        screen_width, screen_height = img.size

        # 准备字体
        font = self._get_font()

        # 过滤太小的元素
        valid_boxes = []
        for box in boxes:
            w = box.get("width", box.get("w", 0))
            h = box.get("height", box.get("h", 0))
            if w >= self._min_element_size and h >= self._min_element_size:
                valid_boxes.append(box)

        # 去重：合并高度重叠的框
        valid_boxes = self._deduplicate_boxes(valid_boxes)

        # 生成颜色
        colors = _generate_colors(len(valid_boxes))

        # 标注
        elements = []
        for idx, (box, color) in enumerate(zip(valid_boxes, colors), start=1):
            x = int(box.get("x", 0))
            y = int(box.get("y", 0))
            w = int(box.get("width", box.get("w", 0)))
            h = int(box.get("height", box.get("h", 0)))

            # 画边框
            draw.rectangle(
                [x, y, x + w, y + h],
                outline=color,
                width=self._box_thickness,
            )

            # 画编号标签
            label = str(idx)
            label_bbox = self._get_text_bbox(draw, label, font)
            label_w = label_bbox[2] - label_bbox[0] + self._label_padding * 2
            label_h = label_bbox[3] - label_bbox[1] + self._label_padding * 2

            # 标签位置：左上角外侧（如果空间不够就放内侧）
            lx = x - 1
            ly = y - label_h - 1
            if ly < 0:
                ly = y + 1
            if lx + label_w > screen_width:
                lx = x + w - label_w

            # 标签背景
            draw.rectangle(
                [lx, ly, lx + label_w, ly + label_h],
                fill=color,
            )
            # 标签文字（黑色或白色，取决于背景亮度）
            text_color = self._contrast_color(color)
            draw.text(
                (lx + self._label_padding, ly + self._label_padding),
                label,
                fill=text_color,
                font=font,
            )

            elements.append(SoMElement(
                id=idx,
                x=x,
                y=y,
                width=w,
                height=h,
                text=box.get("text", ""),
                type=box.get("type", ""),
                source=box.get("source", "ocr"),
            ))

        # 保存标注图
        self._output_dir.mkdir(parents=True, exist_ok=True)
        output_name = f"som_{image_path.stem}.png"
        output_path = self._output_dir / output_name
        img.save(output_path)

        logger.info(
            "som_annotated",
            elements=len(elements),
            output=str(output_path),
        )

        return SoMResult(
            annotated_image_path=str(output_path),
            original_image_path=str(image_path),
            elements=elements,
            screen_width=screen_width,
            screen_height=screen_height,
        )

    def annotate_from_ocr(
        self,
        image_path: str | Path,
        ocr_boxes: list,
    ) -> SoMResult:
        """从 OCR boxes 标注

        Args:
            image_path: 截图路径
            ocr_boxes: OCRBox 对象列表（来自 Observation.ocr_boxes）
        """
        boxes = []
        for box in ocr_boxes:
            if hasattr(box, "x"):
                boxes.append({
                    "x": box.x,
                    "y": box.y,
                    "width": box.width,
                    "height": box.height,
                    "text": box.text if hasattr(box, "text") else "",
                    "type": "text",
                    "source": "ocr",
                })
            elif isinstance(box, dict):
                boxes.append(box)

        return self.annotate_from_boxes(image_path, boxes)

    def annotate_from_analysis(
        self,
        image_path: str | Path,
        analysis,
    ) -> SoMResult:
        """从 ScreenAnalysis（Vision LLM 初步分析）标注

        Args:
            image_path: 截图路径
            analysis: ScreenAnalysis 对象
        """
        boxes = []
        for el in analysis.elements:
            if el.bounds and len(el.bounds) >= 4:
                boxes.append({
                    "x": el.bounds[0],
                    "y": el.bounds[1],
                    "width": el.bounds[2],
                    "height": el.bounds[3],
                    "text": el.text,
                    "type": el.type,
                    "source": "vision",
                })

        return self.annotate_from_boxes(image_path, boxes)

    def annotate_from_observation(
        self,
        image_path: str | Path,
        observation,
    ) -> SoMResult:
        """从 Observation（综合感知）标注

        合并 OCR boxes + accessibility tree 元素。
        """
        boxes = []

        # OCR boxes
        for box in observation.ocr_boxes:
            boxes.append({
                "x": box.x,
                "y": box.y,
                "width": box.width,
                "height": box.height,
                "text": box.text,
                "type": "text",
                "source": "ocr",
            })

        # Accessibility tree 元素
        self._flatten_accessibility(observation.accessibility_tree, boxes)

        return self.annotate_from_boxes(image_path, boxes)

    # ============================================================
    # 辅助方法
    # ============================================================

    def _flatten_accessibility(self, elements: list, boxes: list[dict]) -> None:
        """递归展平 accessibility tree 为 box 列表"""
        for el in elements:
            if el.width > 0 and el.height > 0 and el.is_visible:
                boxes.append({
                    "x": el.x,
                    "y": el.y,
                    "width": el.width,
                    "height": el.height,
                    "text": el.name,
                    "type": el.role,
                    "source": "accessibility",
                })
            if hasattr(el, "children") and el.children:
                self._flatten_accessibility(el.children, boxes)

    def _deduplicate_boxes(
        self,
        boxes: list[dict[str, Any]],
        iou_threshold: float = 0.7,
    ) -> list[dict[str, Any]]:
        """去除高度重叠的框（保留面积较大的）"""
        if len(boxes) <= 1:
            return boxes

        # 按面积降序排
        sorted_boxes = sorted(
            boxes,
            key=lambda b: b.get("width", b.get("w", 0)) * b.get("height", b.get("h", 0)),
            reverse=True,
        )

        kept = []
        for box in sorted_boxes:
            x1 = box.get("x", 0)
            y1 = box.get("y", 0)
            w1 = box.get("width", box.get("w", 0))
            h1 = box.get("height", box.get("h", 0))

            is_duplicate = False
            for kept_box in kept:
                x2 = kept_box.get("x", 0)
                y2 = kept_box.get("y", 0)
                w2 = kept_box.get("width", kept_box.get("w", 0))
                h2 = kept_box.get("height", kept_box.get("h", 0))

                iou = self._compute_iou(x1, y1, w1, h1, x2, y2, w2, h2)
                if iou > iou_threshold:
                    is_duplicate = True
                    break

            if not is_duplicate:
                kept.append(box)

        return kept

    @staticmethod
    def _compute_iou(
        x1: int, y1: int, w1: int, h1: int,
        x2: int, y2: int, w2: int, h2: int,
    ) -> float:
        """计算两个框的 IoU"""
        # 交集
        ix1 = max(x1, x2)
        iy1 = max(y1, y2)
        ix2 = min(x1 + w1, x2 + w2)
        iy2 = min(y1 + h1, y2 + h2)

        if ix2 <= ix1 or iy2 <= iy1:
            return 0.0

        intersection = (ix2 - ix1) * (iy2 - iy1)
        area1 = w1 * h1
        area2 = w2 * h2
        union = area1 + area2 - intersection

        if union <= 0:
            return 0.0
        return intersection / union

    def _get_font(self):
        """获取标注字体"""
        from PIL import ImageFont
        try:
            # 尝试系统字体
            return ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", self._font_size)
        except (OSError, IOError):
            try:
                return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", self._font_size)
            except (OSError, IOError):
                try:
                    return ImageFont.truetype("arial.ttf", self._font_size)
                except (OSError, IOError):
                    return ImageFont.load_default()

    @staticmethod
    def _get_text_bbox(draw, text: str, font) -> tuple[int, int, int, int]:
        """获取文字边界框"""
        bbox = draw.textbbox((0, 0), text, font=font)
        return bbox

    @staticmethod
    def _contrast_color(bg_color: tuple[int, int, int]) -> tuple[int, int, int]:
        """根据背景色选择对比文字色（黑或白）"""
        r, g, b = bg_color
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return (0, 0, 0) if luminance > 0.5 else (255, 255, 255)
