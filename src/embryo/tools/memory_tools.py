"""记忆工具 - 让 Agent 主动管理自己的记忆

Agent 可以主动存储重要信息到持久记忆中。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .registry import Tool

if TYPE_CHECKING:
    from ..runtime.session import Session
    from ..memory.store import MemoryStore

# 全局引用，在 Agent 初始化时绑定
_memory_store: "MemoryStore | None" = None


def bind_memory_store(store: "MemoryStore"):
    """绑定记忆存储实例（Agent 初始化时调用）"""
    global _memory_store
    _memory_store = store


def remember(category: str, content: str, tags: str = "") -> str:
    """存储一条记忆

    Args:
        category: 类别 (preference/environment/lesson/project/fact/correction)
        content: 记忆内容
        tags: 标签（逗号分隔）

    Returns:
        存储结果
    """
    if _memory_store is None:
        return "[Error] 记忆系统未初始化"

    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    entry = _memory_store.store(category, content, tags=tag_list)
    return f"已记住: [{entry.category}] {entry.content} (id={entry.id})"


def recall(query: str, max_count: int = 5) -> str:
    """检索相关记忆

    Args:
        query: 检索关键词
        max_count: 最大返回数量

    Returns:
        匹配的记忆内容
    """
    if _memory_store is None:
        return "[Error] 记忆系统未初始化"

    results = _memory_store.recall_relevant(query, max_count=max_count)
    if not results:
        return f"未找到与 '{query}' 相关的记忆"
    return f"找到 {len(results)} 条相关记忆:\n" + "\n".join(f"  - {r}" for r in results)


def forget(entry_id: str) -> str:
    """删除一条记忆

    Args:
        entry_id: 记忆 ID

    Returns:
        操作结果
    """
    if _memory_store is None:
        return "[Error] 记忆系统未初始化"

    _memory_store.forget(entry_id)
    return f"已删除记忆: {entry_id}"


# 工具定义
REMEMBER_TOOL = Tool(
    name="remember",
    description="存储重要信息到持久记忆中。用于记住用户偏好、经验教训、环境信息等。",
    parameters={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "类别: preference(偏好)/environment(环境)/lesson(教训)/project(项目)/fact(事实)/correction(纠正)",
                "enum": ["preference", "environment", "lesson", "project", "fact", "correction"],
            },
            "content": {"type": "string", "description": "要记住的内容"},
            "tags": {"type": "string", "description": "标签（逗号分隔）", "default": ""},
        },
        "required": ["category", "content"],
    },
    handler=remember,
    category="memory",
)

RECALL_TOOL = Tool(
    name="recall",
    description="检索过去存储的记忆。用于回忆用户偏好、项目信息、之前的经验教训。",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "检索关键词"},
            "max_count": {"type": "integer", "description": "最大返回数量", "default": 5},
        },
        "required": ["query"],
    },
    handler=recall,
    category="memory",
)

FORGET_TOOL = Tool(
    name="forget",
    description="删除一条记忆。",
    parameters={
        "type": "object",
        "properties": {
            "entry_id": {"type": "string", "description": "记忆条目 ID"},
        },
        "required": ["entry_id"],
    },
    handler=forget,
    category="memory",
)
