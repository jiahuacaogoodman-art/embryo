"""Perception 模块 - 统一感知层

将屏幕截图、OCR、DOM 快照、Accessibility Tree、窗口状态等
统一为 Observation 对象，供 Planner/Verifier/TargetResolver 使用。
"""

from .observation import (
    Observation,
    OCRBox,
    UIElement,
    ObservationSource,
)
from .observer import Observer

__all__ = [
    "Observation",
    "OCRBox",
    "UIElement",
    "ObservationSource",
    "Observer",
]
