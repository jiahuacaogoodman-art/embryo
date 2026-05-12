"""会话管理

每个对话/任务都在一个 Session 中执行，Session 持有上下文、历史和状态。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SessionStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


@dataclass
class Message:
    """对话消息"""
    role: str  # system / user / assistant / tool
    content: str
    name: str = ""  # tool name
    tool_call_id: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """工具调用记录"""
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str = ""
    arguments: dict[str, Any] = field(default_factory=dict)
    result: str = ""
    success: bool = True
    duration: float = 0.0
    timestamp: float = field(default_factory=time.time)


@dataclass
class Session:
    """会话上下文

    持有完整对话历史、工具调用记录、已加载的 Skills 等状态。
    """
    id: str = field(default_factory=lambda: str(uuid.uuid4())[:8])
    status: SessionStatus = SessionStatus.ACTIVE
    messages: list[Message] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    loaded_skills: list[str] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    # 统计
    total_steps: int = 0
    total_tokens: int = 0

    def add_message(self, role: str, content: str, **kwargs) -> Message:
        msg = Message(role=role, content=content, **kwargs)
        self.messages.append(msg)
        self.updated_at = time.time()
        return msg

    def add_tool_call(self, tool_call: ToolCall):
        self.tool_calls.append(tool_call)
        self.total_steps += 1
        self.updated_at = time.time()

    def get_conversation_for_llm(self) -> list[dict[str, str]]:
        """导出为 LLM API 格式的消息列表"""
        result = []
        for msg in self.messages:
            entry = {"role": msg.role, "content": msg.content}
            if msg.name:
                entry["name"] = msg.name
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            result.append(entry)
        return result

    def get_recent_history(self, n: int = 10) -> list[Message]:
        """获取最近 n 条消息"""
        return self.messages[-n:]
