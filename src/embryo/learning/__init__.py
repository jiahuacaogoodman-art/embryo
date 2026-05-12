"""学习循环 - 自我改进机制

1. 任务完成后自动提取经验
2. 从失败中学习，存储教训到 Memory
3. 从成功模式中生成可复用的 Skill
4. 渐进式优化已有 Skill

这不是重训练模型，而是基于经验积累的行为改进。
"""

from .learner import LearningEngine

__all__ = ["LearningEngine"]
