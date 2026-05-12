"""记忆存储后端

支持多种后端：
- JSON 文件（默认，轻量）
- SQLite（结构化查询）

参考 Hermes 的设计：记忆有类别、有时效、有关联检索能力。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class MemoryCategory(str, Enum):
    """记忆类别"""
    PREFERENCE = "preference"  # 用户偏好（"用户喜欢简洁的代码风格"）
    ENVIRONMENT = "environment"  # 环境信息（"用户的系统是 macOS，Python 3.11"）
    LESSON = "lesson"  # 经验教训（"点击登录按钮时需要先等待 2s"）
    PROJECT = "project"  # 项目知识（"这个项目使用 FastAPI + PostgreSQL"）
    FACT = "fact"  # 一般事实（"用户名是 admin"）
    CORRECTION = "correction"  # 用户纠正（"不要用 print，用 logger"）


@dataclass
class MemoryEntry:
    """单条记忆"""
    id: str = ""
    category: str = "fact"
    content: str = ""
    tags: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    accessed_at: float = field(default_factory=time.time)
    access_count: int = 0
    source: str = ""  # 来源会话 ID 或手动输入
    importance: float = 1.0  # 重要性权重

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


class MemoryStore:
    """持久记忆存储

    提供存储、检索、淘汰等功能。
    """

    def __init__(self, storage_path: Path, max_entries: int = 1000):
        self.storage_path = storage_path
        self.max_entries = max_entries
        self._entries: list[MemoryEntry] = []
        self._next_id = 1
        self._load()

    def store(self, category: str, content: str, **kwargs) -> MemoryEntry:
        """存储一条新记忆

        Args:
            category: 类别（preference/environment/lesson/project/fact/correction）
            content: 记忆内容
            **kwargs: 其他字段（tags, source, importance）

        Returns:
            创建的记忆条目
        """
        entry = MemoryEntry(
            id=f"mem_{self._next_id:04d}",
            category=category,
            content=content,
            tags=kwargs.get("tags", []),
            source=kwargs.get("source", ""),
            importance=kwargs.get("importance", 1.0),
        )
        self._next_id += 1
        self._entries.append(entry)

        # 超出上限时淘汰
        if len(self._entries) > self.max_entries:
            self._evict()

        self._save()
        return entry

    def recall_relevant(self, query: str, max_count: int = 10) -> list[str]:
        """检索与查询相关的记忆

        使用关键词匹配（未来可扩展为向量检索）。

        Args:
            query: 查询文本
            max_count: 最大返回数量

        Returns:
            相关记忆内容列表
        """
        if not query or not self._entries:
            return []

        query_lower = query.lower()
        query_words = set(query_lower.split())

        scored: list[tuple[float, MemoryEntry]] = []
        for entry in self._entries:
            content_lower = entry.content.lower()
            content_words = set(content_lower.split())

            # 关键词重叠
            overlap = query_words & content_words
            score = len(overlap) * 2.0

            # query 作为子串出现在 content 中
            for word in query_words:
                if len(word) >= 2 and word in content_lower:
                    score += 1.5

            # 标签匹配
            for tag in entry.tags:
                if tag.lower() in query_lower:
                    score += 3.0

            # 重要性加权
            score *= entry.importance

            # 近期访问加分
            age_days = (time.time() - entry.accessed_at) / 86400
            if age_days < 1:
                score *= 1.5
            elif age_days < 7:
                score *= 1.2

            if score > 0:
                scored.append((score, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, entry in scored[:max_count]:
            entry.accessed_at = time.time()
            entry.access_count += 1
            results.append(f"[{entry.category}] {entry.content}")

        if results:
            self._save()

        return results

    def recall_by_category(self, category: str) -> list[MemoryEntry]:
        """按类别检索记忆"""
        return [e for e in self._entries if e.category == category]

    def recall_all(self) -> list[MemoryEntry]:
        """获取全部记忆"""
        return list(self._entries)

    def forget(self, entry_id: str):
        """删除一条记忆"""
        self._entries = [e for e in self._entries if e.id != entry_id]
        self._save()

    def clear(self):
        """清空所有记忆"""
        self._entries = []
        self._next_id = 1
        self._save()

    def _evict(self):
        """淘汰策略：移除最不重要且最久未访问的记忆"""
        # 按 (重要性 * 访问频率 / 年龄) 排序，移除末尾的
        now = time.time()
        self._entries.sort(
            key=lambda e: e.importance * (e.access_count + 1) / max(now - e.created_at, 1),
            reverse=True,
        )
        self._entries = self._entries[: self.max_entries]

    def _load(self):
        """从文件加载记忆"""
        mem_file = self.storage_path / "memory.json"
        if mem_file.exists():
            try:
                data = json.loads(mem_file.read_text(encoding="utf-8"))
                self._entries = [MemoryEntry.from_dict(d) for d in data.get("entries", [])]
                self._next_id = data.get("next_id", len(self._entries) + 1)
            except (json.JSONDecodeError, Exception):
                self._entries = []
                self._next_id = 1

    def _save(self):
        """持久化到文件"""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        mem_file = self.storage_path / "memory.json"
        data = {
            "next_id": self._next_id,
            "entries": [e.to_dict() for e in self._entries],
        }
        mem_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    @property
    def count(self) -> int:
        return len(self._entries)
