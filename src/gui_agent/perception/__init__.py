"""界面感知模块 - GUI Agent 的"眼睛"

负责采集当前屏幕状态，将图像、文字、控件信息转化为结构化输入。
"""

from .screen_capture import ScreenCapture
from .ocr_engine import OCREngine
from .element_detector import ElementDetector
from .perception_engine import PerceptionEngine

__all__ = ["ScreenCapture", "OCREngine", "ElementDetector", "PerceptionEngine"]
