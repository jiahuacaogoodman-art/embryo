"""持久记忆系统

跨会话记忆存储，参考 Hermes Agent 的 MEMORY 设计：
- 用户偏好 (preference)
- 环境信息 (environment)
- 经验教训 (lesson)
- 项目知识 (project)
- 事实 (fact)

记忆是有界的、可策管的，不会无限增长。
"""

from .store import MemoryStore, MemoryEntry

__all__ = ["MemoryStore", "MemoryEntry"]
