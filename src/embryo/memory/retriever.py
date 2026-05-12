"""Memory Retriever - 三层分离检索

按 scope 独立检索，避免记忆交叉污染。
每层有独立的过滤和排序逻辑。

用法：
    retriever = MemoryRetriever(store)
    result = retriever.retrieve(RetrievalQuery(
        query="登录淘宝",
        scopes=[MemoryScope.USER, MemoryScope.TASK],
        site_or_app="taobao.com",
        max_items_per_scope=5,
    ))
"""

from __future__ import annotations

import math
import time
from typing import Optional

from ..logging import get_logger
from .schemas import (
    MemoryRecord,
    MemoryScope,
    RetrievalQuery,
    RetrievalResult,
)
from .store import MemoryStore, _tokenize

logger = get_logger(__name__)


class MemoryRetriever:
    """三层记忆检索器

    与 MemoryStore 协作：
    - MemoryStore 负责底层存储和 TF-IDF 计算
    - MemoryRetriever 负责分层检索和结果组装

    记忆记录存储在 MemoryStore 中，通过 tags 和 category 区分 scope：
    - category="user:preference" → USER scope, type=preference
    - category="task:failure_experience" → TASK scope
    - category="skill:workflow" → SKILL scope
    """

    def __init__(self, store: MemoryStore):
        self._store = store

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        """按 scope 分层检索

        Args:
            query: 检索查询

        Returns:
            RetrievalResult 包含各层结果
        """
        result = RetrievalResult()

        all_entries = self._store.recall_all()

        # 按 scope 过滤和检索
        if MemoryScope.USER in query.scopes:
            user_entries = [e for e in all_entries if self._is_scope(e, MemoryScope.USER)]
            result.user_memories = self._search_and_rank(
                user_entries, query, query.max_items_per_scope
            )

        if MemoryScope.TASK in query.scopes:
            task_entries = [e for e in all_entries if self._is_scope(e, MemoryScope.TASK)]
            # 如果指定了 site_or_app，优先过滤
            if query.site_or_app:
                task_entries = [
                    e for e in task_entries
                    if query.site_or_app.lower() in (self._get_tag(e, "site") or "").lower()
                    or query.site_or_app.lower() in e.content.lower()
                ] or task_entries  # 过滤为空时回退到全部
            result.task_memories = self._search_and_rank(
                task_entries, query, query.max_items_per_scope
            )

        if MemoryScope.SKILL in query.scopes:
            skill_entries = [e for e in all_entries if self._is_scope(e, MemoryScope.SKILL)]
            # 如果指定了 skill_name，优先过滤
            if query.skill_name:
                skill_entries = [
                    e for e in skill_entries
                    if query.skill_name.lower() in (self._get_tag(e, "skill") or "").lower()
                    or query.skill_name.lower() in e.content.lower()
                ] or skill_entries
            result.skill_memories = self._search_and_rank(
                skill_entries, query, query.max_items_per_scope
            )

        logger.debug(
            "memory_retrieved",
            query=query.query[:50],
            user=len(result.user_memories),
            task=len(result.task_memories),
            skill=len(result.skill_memories),
        )

        return result

    def store_user_memory(
        self,
        content: str,
        memory_type: str = "preference",
        importance: float = 1.0,
        **kwargs,
    ) -> None:
        """存储用户记忆"""
        self._store.store(
            category=f"user:{memory_type}",
            content=content,
            importance=importance,
            tags=kwargs.get("tags", []) + [f"scope:user", f"type:{memory_type}"],
            **{k: v for k, v in kwargs.items() if k != "tags"},
        )

    def store_task_memory(
        self,
        content: str,
        memory_type: str = "failure_experience",
        task_type: str = "",
        site_or_app: str = "",
        importance: float = 1.0,
        **kwargs,
    ) -> None:
        """存储任务记忆"""
        tags = kwargs.get("tags", []) + [f"scope:task", f"type:{memory_type}"]
        if task_type:
            tags.append(f"task_type:{task_type}")
        if site_or_app:
            tags.append(f"site:{site_or_app}")

        self._store.store(
            category=f"task:{memory_type}",
            content=content,
            importance=importance,
            tags=tags,
            **{k: v for k, v in kwargs.items() if k != "tags"},
        )

    def store_skill_memory(
        self,
        content: str,
        memory_type: str = "workflow",
        skill_name: str = "",
        importance: float = 1.0,
        **kwargs,
    ) -> None:
        """存储技能记忆"""
        tags = kwargs.get("tags", []) + [f"scope:skill", f"type:{memory_type}"]
        if skill_name:
            tags.append(f"skill:{skill_name}")

        self._store.store(
            category=f"skill:{memory_type}",
            content=content,
            importance=importance,
            tags=tags,
            **{k: v for k, v in kwargs.items() if k != "tags"},
        )

    def should_recall(self, task_description: str) -> bool:
        """自主决策是否需要回忆记忆

        不是每次都需要检索记忆。只有在以下情况才主动回忆：
        1. 任务涉及特定网站/应用（有相关经验）
        2. 任务类型之前有失败记录
        3. 任务描述中包含用户偏好关键词

        Args:
            task_description: 任务描述

        Returns:
            是否建议执行记忆检索
        """
        all_entries = self._store.recall_all()
        if not all_entries:
            return False

        # 检查是否有相关经验
        task_lower = task_description.lower()

        # 策略 1: 是否有相关网站/应用的经验
        site_tags = set()
        for e in all_entries:
            for tag in e.tags:
                if tag.startswith("site:"):
                    site_tags.add(tag.split(":", 1)[1].lower())
        if any(site in task_lower for site in site_tags):
            return True

        # 策略 2: 是否有相关失败经验
        failure_entries = [
            e for e in all_entries
            if "failure" in e.category or "lesson" in e.category
        ]
        if failure_entries:
            # 快速相关性检查
            task_tokens = set(_tokenize(task_description))
            for e in failure_entries:
                entry_tokens = set(_tokenize(e.content))
                overlap = task_tokens & entry_tokens
                if len(overlap) >= 3:  # 至少 3 个词重叠
                    return True

        # 策略 3: 用户偏好总是可能相关（但仅在有足够偏好时）
        user_prefs = [e for e in all_entries if e.category.startswith("user:")]
        if len(user_prefs) >= 3:
            return True

        return False

    # --------------------------------------------------
    # 内部方法
    # --------------------------------------------------

    def _is_scope(self, entry, scope: MemoryScope) -> bool:
        """判断记忆条目属于哪个 scope"""
        # 通过 category 前缀判断
        if entry.category.startswith(f"{scope.value}:"):
            return True
        # 通过 tags 判断
        return any(f"scope:{scope.value}" in tag for tag in entry.tags)

    def _get_tag(self, entry, prefix: str) -> Optional[str]:
        """获取特定前缀的 tag 值"""
        for tag in entry.tags:
            if tag.startswith(f"{prefix}:"):
                return tag.split(":", 1)[1]
        return None

    def _search_and_rank(
        self, entries, query: RetrievalQuery, max_items: int
    ) -> list[MemoryRecord]:
        """在给定条目中搜索和排序"""
        if not entries:
            return []

        query_tokens = _tokenize(query.query)
        if not query_tokens:
            # 无查询词时返回最近的
            entries_sorted = sorted(entries, key=lambda e: e.accessed_at, reverse=True)
            return [self._entry_to_record(e) for e in entries_sorted[:max_items]]

        now = time.time()
        scored = []

        for entry in entries:
            # 过滤过期
            if not query.include_expired and hasattr(entry, "expires_at"):
                # MemoryEntry 没有 expires_at，跳过
                pass

            # 重要性过滤
            if entry.importance < query.min_importance:
                continue

            # TF-IDF 相似度
            entry_tokens = _tokenize(entry.content)
            sim = self._compute_similarity(query_tokens, entry_tokens)

            if sim <= 0:
                continue

            # 重要性加权
            sim *= entry.importance

            # 时间衰减：半衰期 30 天
            age_days = (now - entry.accessed_at) / 86400
            decay = math.exp(-0.023 * age_days)
            sim *= decay

            scored.append((sim, entry))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._entry_to_record(e) for _, e in scored[:max_items]]

    def _compute_similarity(self, tokens_a: list[str], tokens_b: list[str]) -> float:
        """简单的 token 重叠相似度"""
        if not tokens_a or not tokens_b:
            return 0.0
        set_a = set(tokens_a)
        set_b = set(tokens_b)
        overlap = set_a & set_b
        if not overlap:
            return 0.0
        # Jaccard-like + 长度加权
        return len(overlap) / (len(set_a | set_b))

    def _entry_to_record(self, entry) -> MemoryRecord:
        """将旧 MemoryEntry 转为新 MemoryRecord"""
        # 解析 scope 和 type
        scope = MemoryScope.USER
        mem_type = "fact"

        if ":" in entry.category:
            scope_str, mem_type = entry.category.split(":", 1)
            try:
                scope = MemoryScope(scope_str)
            except ValueError:
                scope = MemoryScope.USER
        else:
            # 旧格式兼容
            category_to_scope = {
                "preference": MemoryScope.USER,
                "environment": MemoryScope.USER,
                "correction": MemoryScope.USER,
                "lesson": MemoryScope.TASK,
                "project": MemoryScope.TASK,
                "fact": MemoryScope.USER,
            }
            scope = category_to_scope.get(entry.category, MemoryScope.USER)
            mem_type = entry.category

        return MemoryRecord(
            id=entry.id,
            scope=scope,
            type=mem_type,
            content=entry.content,
            tags=entry.tags,
            importance=entry.importance,
            created_at=entry.created_at,
            accessed_at=entry.accessed_at,
            access_count=entry.access_count,
            source=entry.source,
            site_or_app=self._get_tag(entry, "site") or "",
            skill_name=self._get_tag(entry, "skill") or "",
            task_type=self._get_tag(entry, "task_type") or "",
        )
