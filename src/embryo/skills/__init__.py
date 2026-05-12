"""Skills 系统

Skills 是 Markdown 定义的可复用工作流文档，Agent 按需加载到上下文中。

核心概念：
- 一个 Skill = 一个目录，包含 SKILL.md（主文档）和可选附件
- Skills 不是代码，而是告诉 Agent "如何使用工具完成某类任务"的指令
- 渐进式加载：先加载摘要，需要时再加载完整内容（节省 token）
- Agent 可以从经验中自动创建新 Skill
"""

from .manager import SkillManager

__all__ = ["SkillManager"]
