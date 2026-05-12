"""Skill 管理器

负责 Skill 的索引、加载、匹配和创建。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime.session import Session


@dataclass
class SkillMeta:
    """Skill 元数据（从 SKILL.md frontmatter 解析）"""
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    version: str = "1.0"
    path: Optional[Path] = None

    @property
    def summary(self) -> str:
        """一行摘要，用于渐进式加载的第一层"""
        return f"[{self.name}] {self.description}"


class SkillManager:
    """Skill 管理器

    职责：
    1. 扫描 skills 目录，索引所有可用 Skill
    2. 根据用户输入/任务上下文匹配相关 Skill
    3. 渐进式加载（先摘要，按需全文）
    4. 从成功会话中自动生成新 Skill
    """

    def __init__(self, skills_dir: Path, bundled_dir: Optional[Path] = None):
        self.skills_dir = skills_dir
        self.bundled_dir = bundled_dir
        self._index: list[SkillMeta] = []
        self._cache: dict[str, str] = {}  # name → full content
        self._build_index()

    def _build_index(self):
        """扫描目录，构建 Skill 索引"""
        self._index = []

        dirs_to_scan = [self.skills_dir]
        if self.bundled_dir and self.bundled_dir.exists():
            dirs_to_scan.append(self.bundled_dir)

        for base_dir in dirs_to_scan:
            if not base_dir.exists():
                continue

            # 支持两种布局：
            # 1. skills_dir/skill_name/SKILL.md
            # 2. skills_dir/skill_name.md
            for item in sorted(base_dir.iterdir()):
                skill_path = None
                if item.is_dir():
                    candidate = item / "SKILL.md"
                    if candidate.exists():
                        skill_path = candidate
                elif item.suffix == ".md" and item.name != "README.md":
                    skill_path = item

                if skill_path:
                    meta = self._parse_skill_meta(skill_path)
                    if meta:
                        self._index.append(meta)

    def _parse_skill_meta(self, path: Path) -> Optional[SkillMeta]:
        """解析 Skill 文件的 frontmatter 和内容"""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return None

        # 解析 YAML frontmatter（--- ... --- 之间的内容）
        name = path.stem if path.name != "SKILL.md" else path.parent.name
        description = ""
        tags: list[str] = []
        triggers: list[str] = []

        fm_match = re.match(r"^---\s*\n(.+?)\n---\s*\n", content, re.DOTALL)
        if fm_match:
            fm_text = fm_match.group(1)
            for line in fm_text.split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("tags:"):
                    tags_str = line.split(":", 1)[1].strip()
                    tags = [t.strip().strip("\"'") for t in tags_str.strip("[]").split(",")]
                elif line.startswith("triggers:"):
                    triggers_str = line.split(":", 1)[1].strip()
                    triggers = [t.strip().strip("\"'") for t in triggers_str.strip("[]").split(",")]
        else:
            # 没有 frontmatter，从内容第一行提取描述
            first_line = content.split("\n")[0].strip().lstrip("#").strip()
            description = first_line

        return SkillMeta(
            name=name,
            description=description,
            tags=tags,
            triggers=triggers,
            path=path,
        )

    def get_relevant_skills(self, query: str, max_count: int = 3) -> list[str]:
        """根据查询匹配相关 Skills，返回内容列表

        匹配逻辑：
        1. 精确触发词匹配
        2. 标签匹配
        3. 名称/描述关键词匹配

        Args:
            query: 用户输入或任务描述
            max_count: 最多返回数量

        Returns:
            匹配到的 Skill 内容列表
        """
        if not query:
            return []

        query_lower = query.lower()
        scored: list[tuple[float, SkillMeta]] = []

        for meta in self._index:
            score = 0.0

            # 触发词精确匹配
            for trigger in meta.triggers:
                if trigger.lower() in query_lower:
                    score += 10.0
                    break

            # 标签匹配
            for tag in meta.tags:
                if tag.lower() in query_lower:
                    score += 3.0

            # 名称匹配
            if meta.name.lower() in query_lower:
                score += 5.0

            # 描述关键词匹配
            desc_words = set(meta.description.lower().split())
            query_words = set(query_lower.split())
            overlap = desc_words & query_words
            if overlap:
                score += len(overlap) * 1.5

            if score > 0:
                scored.append((score, meta))

        # 按分数降序
        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, meta in scored[:max_count]:
            content = self._load_skill_content(meta)
            if content:
                results.append(content)

        return results

    def _load_skill_content(self, meta: SkillMeta) -> Optional[str]:
        """加载 Skill 完整内容"""
        if meta.name in self._cache:
            return self._cache[meta.name]

        if meta.path and meta.path.exists():
            content = meta.path.read_text(encoding="utf-8")
            # 去掉 frontmatter
            content = re.sub(r"^---\s*\n.+?\n---\s*\n", "", content, flags=re.DOTALL)
            self._cache[meta.name] = content.strip()
            return self._cache[meta.name]

        return None

    def list_skills(self) -> list[SkillMeta]:
        """列出所有可用 Skill"""
        return list(self._index)

    def get_skill_summaries(self) -> str:
        """获取所有 Skill 的摘要列表（用于系统提示）"""
        if not self._index:
            return "（暂无可用 Skill）"
        lines = [meta.summary for meta in self._index]
        return "\n".join(lines)

    def maybe_create_from_session(self, session: "Session"):
        """从成功完成的会话中提取经验，创建新 Skill

        策略：
        - 会话步骤数 >= 3
        - 有清晰的任务模式（多次类似操作）
        - 不与已有 Skill 重复
        """
        # 提取会话中的工具调用序列
        tool_sequence = [
            f"{tc.name}({list(tc.arguments.keys())})"
            for tc in session.tool_calls
            if tc.success
        ]

        if len(tool_sequence) < 3:
            return

        # 检查是否已有类似 Skill
        task_desc = session.context.get("task", "")
        existing = self.get_relevant_skills(task_desc, max_count=1)
        if existing:
            return  # 已有类似 Skill，不重复创建

        # 生成 Skill 文件
        skill_name = self._generate_skill_name(task_desc)
        skill_content = self._generate_skill_content(task_desc, tool_sequence, session)

        # 保存
        skill_dir = self.skills_dir / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(skill_content, encoding="utf-8")

        # 刷新索引
        self._build_index()

    def create_skill(self, name: str, content: str):
        """手动创建 Skill

        Args:
            name: Skill 名称
            content: Markdown 内容
        """
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(content, encoding="utf-8")
        self._build_index()

    def _generate_skill_name(self, task_desc: str) -> str:
        """从任务描述生成 Skill 目录名"""
        # 简单处理：取前几个词，用连字符连接
        words = re.findall(r"[\w\u4e00-\u9fff]+", task_desc)
        name = "-".join(words[:4]).lower()
        return name or "auto-skill"

    def _generate_skill_content(
        self, task_desc: str, tool_sequence: list[str], session: "Session"
    ) -> str:
        """生成 Skill Markdown 内容"""
        steps_text = "\n".join(f"{i+1}. {step}" for i, step in enumerate(tool_sequence))

        content = f"""---
name: "{task_desc[:50]}"
description: "自动从会话 {session.id} 中生成的 Skill"
tags: [auto-generated]
triggers: []
---

# {task_desc[:80]}

## 执行步骤

{steps_text}

## 注意事项

- 此 Skill 由系统自动生成，可能需要手动优化
- 生成时间: 会话 {session.id}
- 工具调用成功率: {sum(1 for tc in session.tool_calls if tc.success)}/{len(session.tool_calls)}
"""
        return content

    def reload(self):
        """重新扫描并刷新索引"""
        self._cache.clear()
        self._build_index()
