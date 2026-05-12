"""三层记忆系统

User Memory  - 用户偏好、环境信息、禁止事项（持久、跨任务）
Task Memory  - 某类任务的失败经验、网站操作经验（按任务类型组织）
Skill Memory - 可复用流程、参数模板、验证规则（绑定到 Skill）

关键设计：
- scope 分离，避免记忆交叉污染
- 检索按 scope 独立检索再合并
- should_recall() 自主决策是否需要回忆
- 每层有不同的过期策略和重要性权重
"""

from .store import MemoryStore, MemoryEntry
from .schemas import (
    MemoryScope,
    MemoryRecord,
    RetrievalQuery,
    RetrievalResult,
    UserMemoryType,
    TaskMemoryType,
    SkillMemoryType,
)
from .retriever import MemoryRetriever

__all__ = [
    "MemoryStore",
    "MemoryEntry",
    "MemoryRetriever",
    "MemoryScope",
    "MemoryRecord",
    "RetrievalQuery",
    "RetrievalResult",
    "UserMemoryType",
    "TaskMemoryType",
    "SkillMemoryType",
]
