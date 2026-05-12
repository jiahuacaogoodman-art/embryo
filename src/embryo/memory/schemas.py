"""Memory Schemas - 三层记忆数据模型

三层设计：
1. User Memory - 用户偏好、环境信息、禁止事项（持久、跨任务）
2. Task Memory - 某类任务的失败经验、某网站的操作经验（按任务类型组织）
3. Skill Memory - 可复用流程、参数模板、验证规则（绑定到 Skill）

关键区别于旧版：
- 明确的 scope 分离，避免"某次失败经验"污染所有任务
- 检索时按 scope 独立检索，再合并
- 每层有不同的过期策略和重要性权重
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class MemoryScope(str, Enum):
    """记忆作用域"""

    USER = "user"  # 用户级：偏好、环境、禁止事项
    TASK = "task"  # 任务级：某类任务/网站的操作经验
    SKILL = "skill"  # 技能级：可复用流程、参数模板


class UserMemoryType(str, Enum):
    """用户记忆子类型"""

    PREFERENCE = "preference"  # 偏好（"喜欢用中文回复"）
    ENVIRONMENT = "environment"  # 环境信息（"macOS + Chrome"）
    PROHIBITION = "prohibition"  # 禁止事项（"不要操作 /etc"）
    IDENTITY = "identity"  # 身份信息（"用户名 xxx"）
    CORRECTION = "correction"  # 纠正（"上次说错了，应该是..."）


class TaskMemoryType(str, Enum):
    """任务记忆子类型"""

    FAILURE_EXPERIENCE = "failure_experience"  # 失败经验
    SUCCESS_PATTERN = "success_pattern"  # 成功模式
    SITE_KNOWLEDGE = "site_knowledge"  # 某网站/应用的操作经验
    WORKAROUND = "workaround"  # 变通方案
    TIMING = "timing"  # 时序信息（"这个页面加载需要 5 秒"）


class SkillMemoryType(str, Enum):
    """技能记忆子类型"""

    WORKFLOW = "workflow"  # 可复用流程
    PARAMETER_TEMPLATE = "parameter_template"  # 参数模板
    VERIFICATION_RULE = "verification_rule"  # 验证规则
    PREREQUISITE = "prerequisite"  # 前置条件


class MemoryRecord(BaseModel):
    """通用记忆记录"""

    id: str = ""
    scope: MemoryScope
    type: str  # 子类型（UserMemoryType / TaskMemoryType / SkillMemoryType）
    content: str  # 记忆内容
    tags: list[str] = Field(default_factory=list)
    importance: float = 1.0  # 重要性权重

    # 关联信息
    task_type: str = ""  # 关联的任务类型（仅 TASK scope）
    skill_name: str = ""  # 关联的 skill 名称（仅 SKILL scope）
    site_or_app: str = ""  # 关联的网站/应用（如 "taobao.com"）

    # 时间信息
    created_at: float = Field(default_factory=time.time)
    accessed_at: float = Field(default_factory=time.time)
    expires_at: Optional[float] = None  # None = 不过期
    access_count: int = 0

    # 来源
    source: str = ""  # 来源（"user_told", "auto_learned", "skill_generated"）
    source_task_id: str = ""  # 产生这条记忆的任务 ID

    # 元数据
    metadata: dict[str, Any] = Field(default_factory=dict)

    def is_expired(self) -> bool:
        """检查记忆是否过期"""
        if self.expires_at is None:
            return False
        return time.time() > self.expires_at

    def touch(self) -> None:
        """更新访问时间和计数"""
        self.accessed_at = time.time()
        self.access_count += 1


class RetrievalQuery(BaseModel):
    """检索查询"""

    query: str  # 查询文本
    scopes: list[MemoryScope] = Field(
        default_factory=lambda: [MemoryScope.USER, MemoryScope.TASK, MemoryScope.SKILL]
    )
    max_items_per_scope: int = 5
    task_type: str = ""  # 限定任务类型
    skill_name: str = ""  # 限定 skill
    site_or_app: str = ""  # 限定网站/应用
    min_importance: float = 0.0
    include_expired: bool = False


class RetrievalResult(BaseModel):
    """检索结果"""

    user_memories: list[MemoryRecord] = Field(default_factory=list)
    task_memories: list[MemoryRecord] = Field(default_factory=list)
    skill_memories: list[MemoryRecord] = Field(default_factory=list)

    @property
    def all_memories(self) -> list[MemoryRecord]:
        return self.user_memories + self.task_memories + self.skill_memories

    @property
    def total_count(self) -> int:
        return len(self.user_memories) + len(self.task_memories) + len(self.skill_memories)

    def to_prompt_context(self, max_chars: int = 2000) -> str:
        """转为 LLM prompt 可用的上下文"""
        parts = []

        if self.user_memories:
            parts.append("## 用户信息")
            for m in self.user_memories:
                parts.append(f"- [{m.type}] {m.content}")

        if self.task_memories:
            parts.append("## 任务经验")
            for m in self.task_memories:
                prefix = f"[{m.site_or_app}] " if m.site_or_app else ""
                parts.append(f"- {prefix}{m.content}")

        if self.skill_memories:
            parts.append("## 技能知识")
            for m in self.skill_memories:
                prefix = f"[{m.skill_name}] " if m.skill_name else ""
                parts.append(f"- {prefix}{m.content}")

        text = "\n".join(parts)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n... (已截断)"
        return text
