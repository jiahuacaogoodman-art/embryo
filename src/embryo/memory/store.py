"""记忆存储 - TF-IDF 相似度检索 + 时间衰减 + Bounded Curation

不会无限增长 — 有上限、有淘汰策略、有重要性衰减。
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass, field, asdict
from enum import Enum
from pathlib import Path
from typing import Optional


class MemoryCategory(str, Enum):
    """记忆类别"""
    PREFERENCE = "preference"
    ENVIRONMENT = "environment"
    LESSON = "lesson"
    PROJECT = "project"
    FACT = "fact"
    CORRECTION = "correction"


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
    source: str = ""
    importance: float = 1.0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryEntry":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


def _tokenize(text: str) -> list[str]:
    """分词：中英文混合分词

    英文：按单词分割
    中文：单字 + bigram（提升短语匹配能力）
    """
    tokens = []
    # 提取英文单词和连续中文段
    parts = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower())
    for part in parts:
        if re.match(r"[\u4e00-\u9fff]", part):
            # 中文：单字 + bigram
            for ch in part:
                tokens.append(ch)
            for i in range(len(part) - 1):
                tokens.append(part[i:i+2])
        else:
            # 英文单词
            if len(part) >= 2:
                tokens.append(part)
    return tokens


class MemoryStore:
    """持久记忆存储

    特性：
    - TF-IDF 向量相似度检索（无需外部依赖）
    - 时间衰减：越久远的记忆分数越低
    - Bounded curation：超出上限时智能淘汰
    - 去重：内容高度相似的记忆不重复存储
    """

    def __init__(self, storage_path: Path, max_entries: int = 1000):
        self.storage_path = storage_path
        self.max_entries = max_entries
        self._entries: list[MemoryEntry] = []
        self._next_id = 1
        self._idf_cache: dict[str, float] = {}
        self._dirty = False
        self._load()
        self._rebuild_idf()

    def store(self, category: str, content: str, **kwargs) -> MemoryEntry:
        """存储一条新记忆（带去重）"""
        # 去重检测
        if self._is_duplicate(content):
            # 找到相似的条目，更新访问时间
            for e in self._entries:
                if self._content_similarity(content, e.content) > 0.8:
                    e.accessed_at = time.time()
                    e.access_count += 1
                    self._save()
                    return e

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

        # Bounded curation
        if len(self._entries) > self.max_entries:
            self._evict()

        self._rebuild_idf()
        self._save()
        return entry

    def recall_relevant(self, query: str, max_count: int = 10) -> list[str]:
        """TF-IDF 向量相似度检索 + 时间衰减"""
        if not query or not self._entries:
            return []

        query_tokens = _tokenize(query)
        if not query_tokens:
            return []

        query_vec = self._compute_tfidf(query_tokens)

        scored: list[tuple[float, MemoryEntry]] = []
        now = time.time()

        for entry in self._entries:
            entry_tokens = _tokenize(entry.content)
            entry_vec = self._compute_tfidf(entry_tokens)

            # 余弦相似度
            sim = self._cosine_similarity(query_vec, entry_vec)

            if sim <= 0:
                continue

            # 标签 boost
            for tag in entry.tags:
                if tag.lower() in query.lower():
                    sim += 0.3

            # 重要性加权
            sim *= entry.importance

            # 时间衰减：半衰期 30 天
            age_days = (now - entry.accessed_at) / 86400
            decay = math.exp(-0.023 * age_days)  # ln(2)/30 ≈ 0.023
            sim *= decay

            scored.append((sim, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, entry in scored[:max_count]:
            entry.accessed_at = now
            entry.access_count += 1
            results.append(f"[{entry.category}] {entry.content}")

        if results:
            self._save()

        return results

    def recall_by_category(self, category: str) -> list[MemoryEntry]:
        return [e for e in self._entries if e.category == category]

    def recall_all(self) -> list[MemoryEntry]:
        return list(self._entries)

    def forget(self, entry_id: str):
        self._entries = [e for e in self._entries if e.id != entry_id]
        self._rebuild_idf()
        self._save()

    def clear(self):
        self._entries = []
        self._next_id = 1
        self._idf_cache = {}
        self._save()

    @property
    def count(self) -> int:
        return len(self._entries)

    # ===== 内部方法 =====

    def _compute_tfidf(self, tokens: list[str]) -> dict[str, float]:
        """计算 TF-IDF 向量"""
        tf = Counter(tokens)
        total = len(tokens) or 1
        vec = {}
        for token, count in tf.items():
            idf = self._idf_cache.get(token, 1.0)
            vec[token] = (count / total) * idf
        return vec

    def _cosine_similarity(self, vec_a: dict[str, float], vec_b: dict[str, float]) -> float:
        """计算两个稀疏向量的余弦相似度"""
        if not vec_a or not vec_b:
            return 0.0

        # 点积
        dot = sum(vec_a.get(k, 0) * vec_b.get(k, 0) for k in set(vec_a) & set(vec_b))
        # 模
        norm_a = math.sqrt(sum(v * v for v in vec_a.values()))
        norm_b = math.sqrt(sum(v * v for v in vec_b.values()))

        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

    def _content_similarity(self, text_a: str, text_b: str) -> float:
        """计算两段文本的相似度"""
        tokens_a = _tokenize(text_a)
        tokens_b = _tokenize(text_b)
        vec_a = self._compute_tfidf(tokens_a)
        vec_b = self._compute_tfidf(tokens_b)
        return self._cosine_similarity(vec_a, vec_b)

    def _is_duplicate(self, content: str, threshold: float = 0.8) -> bool:
        """检测是否与已有记忆重复"""
        for entry in self._entries:
            if self._content_similarity(content, entry.content) > threshold:
                return True
        return False

    def _rebuild_idf(self):
        """重建 IDF 索引"""
        doc_count = len(self._entries) or 1
        doc_freq: Counter = Counter()

        for entry in self._entries:
            tokens = set(_tokenize(entry.content))
            for token in tokens:
                doc_freq[token] += 1

        self._idf_cache = {
            token: math.log(doc_count / (freq + 1)) + 1
            for token, freq in doc_freq.items()
        }

    def _evict(self):
        """Bounded curation：智能淘汰

        策略：综合重要性、访问频率、时间衰减打分，淘汰末位。
        保护规则：correction 和 preference 类型不轻易淘汰。
        """
        now = time.time()
        protection = {"correction": 2.0, "preference": 1.5}

        def score(e: MemoryEntry) -> float:
            age_days = (now - e.created_at) / 86400 + 1
            recency_days = (now - e.accessed_at) / 86400 + 1
            category_boost = protection.get(e.category, 1.0)

            return (
                e.importance
                * (e.access_count + 1)
                * category_boost
                / (recency_days ** 0.5)
            )

        self._entries.sort(key=score, reverse=True)
        self._entries = self._entries[: self.max_entries]

    def _load(self):
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
        self.storage_path.mkdir(parents=True, exist_ok=True)
        mem_file = self.storage_path / "memory.json"
        data = {
            "version": 2,
            "next_id": self._next_id,
            "count": len(self._entries),
            "entries": [e.to_dict() for e in self._entries],
        }
        mem_file.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
