"""会话管理 - 持久化、可恢复、可审计

每个对话/任务在 Session 中执行。Session 可序列化到磁盘，进程重启后可恢复。
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Any, Optional


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
    name: str = ""  # tool name (for role=tool)
    tool_call_id: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = {"role": self.role, "content": self.content, "timestamp": self.timestamp}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Message":
        return cls(
            role=data["role"],
            content=data["content"],
            name=data.get("name", ""),
            tool_call_id=data.get("tool_call_id", ""),
            timestamp=data.get("timestamp", 0.0),
            metadata=data.get("metadata", {}),
        )


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

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "arguments": self.arguments,
            "result": self.result[:500],  # 序列化时截断长输出
            "success": self.success,
            "duration": round(self.duration, 3),
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ToolCall":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class Session:
    """会话上下文

    持有完整对话历史、工具调用记录、已加载的 Skills 等状态。
    支持序列化到 JSON 文件以实现持久化。
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

    def get_conversation_for_llm(self) -> list[dict[str, Any]]:
        """导出为 LLM API 格式的消息列表"""
        result = []
        for msg in self.messages:
            entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
            if msg.name:
                entry["name"] = msg.name
            if msg.tool_call_id:
                entry["tool_call_id"] = msg.tool_call_id
            result.append(entry)
        return result

    def get_recent_history(self, n: int = 10) -> list[Message]:
        """获取最近 n 条消息"""
        return self.messages[-n:]

    # ===== 持久化 =====

    def save(self, sessions_dir: Path):
        """保存会话到磁盘"""
        sessions_dir.mkdir(parents=True, exist_ok=True)
        filepath = sessions_dir / f"{self.id}.json"
        data = {
            "id": self.id,
            "status": self.status.value,
            "context": self.context,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "total_steps": self.total_steps,
            "total_tokens": self.total_tokens,
            "loaded_skills": self.loaded_skills,
            "messages": [m.to_dict() for m in self.messages],
            "tool_calls": [tc.to_dict() for tc in self.tool_calls],
        }
        filepath.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, filepath: Path) -> "Session":
        """从磁盘加载会话"""
        data = json.loads(filepath.read_text(encoding="utf-8"))
        session = cls(
            id=data["id"],
            status=SessionStatus(data["status"]),
            context=data.get("context", {}),
            created_at=data.get("created_at", 0),
            updated_at=data.get("updated_at", 0),
            total_steps=data.get("total_steps", 0),
            total_tokens=data.get("total_tokens", 0),
            loaded_skills=data.get("loaded_skills", []),
        )
        session.messages = [Message.from_dict(m) for m in data.get("messages", [])]
        session.tool_calls = [ToolCall.from_dict(tc) for tc in data.get("tool_calls", [])]
        return session

    @staticmethod
    def list_sessions(sessions_dir: Path) -> list[dict[str, Any]]:
        """列出所有历史会话（摘要信息）"""
        results = []
        if not sessions_dir.exists():
            return results

        for f in sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                # 提取第一条用户消息作为标题
                title = ""
                for msg in data.get("messages", []):
                    if msg.get("role") == "user":
                        title = msg["content"][:80]
                        break
                results.append({
                    "id": data["id"],
                    "status": data.get("status", "unknown"),
                    "title": title,
                    "steps": data.get("total_steps", 0),
                    "created_at": data.get("created_at", 0),
                    "updated_at": data.get("updated_at", 0),
                })
            except Exception:
                continue

        return results
