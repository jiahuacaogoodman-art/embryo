"""Vision 模块 - 多模态视觉理解

让 AI 真正"看见"屏幕，而不是靠 OCR 字符串猜。

核心组件：
- VisionLLM: 将截图作为 image message 发给 GPT-4o/Claude，获取结构化理解
- ScreenAnalysis: 视觉分析结果（元素列表、布局、状态判断）
"""

from .llm import VisionLLM, VisionConfig, ScreenAnalysis, UIElementInfo

__all__ = ["VisionLLM", "VisionConfig", "ScreenAnalysis", "UIElementInfo"]
