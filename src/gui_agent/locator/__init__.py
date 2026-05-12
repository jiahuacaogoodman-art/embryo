"""目标定位模块

负责将用户任务转化为具体可操作目标，综合使用OCR、控件树和图像定位。
"""

from .target_locator import TargetLocator

__all__ = ["TargetLocator"]
