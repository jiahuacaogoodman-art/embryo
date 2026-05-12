"""Skill 管理器 - 索引、渐进式加载、token 预算、版本管理

参考:
- OpenClaw: Skill = Markdown 文件，non-engineer 可编写
- Hermes: 渐进式 disclosure（先摘要，需要时展开），token 预算控制

设计要点:
1. 渐进式加载：默认只加载摘要到上下文，Agent 可用 load_skill 工具展开全文
2. Token 预算：加载 Skills 时不超过配置的 token 上限
3. 版本管理：Skill 内容 hash 追踪变更
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from ..runtime.session import Session

from ..logging import get_logger

logger = get_logger(__name__)


def _estimate_tokens(text: str) -> int:
    """粗略估算 token 数（中文1字≈1.5token，英文1词≈1.3token）"""
    chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
    ascii_words = len(re.findall(r"[a-zA-Z]+", text))
    return int(chinese_chars * 1.5 + ascii_words * 1.3 + len(text) * 0.1)


@dataclass
class SkillMeta:
    """Skill 元数据"""
    name: str
    description: str = ""
    tags: list[str] = field(default_factory=list)
    triggers: list[str] = field(default_factory=list)
    version: str = "1.0"
    path: Optional[Path] = None
    content_hash: str = ""  # 内容 hash，用于变更检测
    token_estimate: int = 0  # 预估 token 数
    last_used: float = 0.0  # 最后使用时间
    use_count: int = 0  # 使用次数

    @property
    def summary(self) -> str:
        """一行摘要（渐进式加载第一层）"""
        return f"[{self.name}] {self.description} (v{self.version}, ~{self.token_estimate}tok)"

    @property
    def brief(self) -> str:
        """简短摘要（用于目录列表）"""
        tags_str = ", ".join(self.tags[:3]) if self.tags else ""
        return f"- **{self.name}**: {self.description}" + (f" [{tags_str}]" if tags_str else "")


class SkillManager:
    """Skill 管理器

    核心改进：
    - 渐进式加载：get_relevant_skills 默认只返回摘要+首段，
      Agent 通过 load_skill_full 工具按需获取完整内容
    - Token 预算：总加载量不超过 max_skill_tokens
    - 版本追踪：content_hash 检测 Skill 文件变更
    """

    def __init__(
        self,
        skills_dir: Path,
        bundled_dir: Optional[Path] = None,
        max_skill_tokens: int = 2000,
        max_loaded: int = 3,
    ):
        self.skills_dir = skills_dir
        self.bundled_dir = bundled_dir
        self.max_skill_tokens = max_skill_tokens
        self.max_loaded = max_loaded
        self._index: list[SkillMeta] = []
        self._cache: dict[str, str] = {}  # name → full content (stripped)
        self._build_index()

    # ===== 公开 API =====

    def get_relevant_skills(self, query: str, max_count: int = 0) -> list[str]:
        """渐进式加载：返回匹配 Skill 的精简版本

        策略：
        1. 匹配得分排序
        2. 在 token 预算内逐个加载
        3. 超预算的 Skill 只返回摘要

        Args:
            query: 用户输入或任务描述
            max_count: 最大数量（0=使用配置值）

        Returns:
            Skill 内容列表（可能是精简版或完整版）
        """
        if not query:
            return []

        max_count = max_count or self.max_loaded
        scored = self._score_skills(query)

        results = []
        remaining_tokens = self.max_skill_tokens

        for score, meta in scored[:max_count]:
            if score <= 0:
                break

            full_content = self._load_full(meta)
            if not full_content:
                continue

            content_tokens = _estimate_tokens(full_content)

            if content_tokens <= remaining_tokens:
                # 预算内：加载完整内容
                results.append(full_content)
                remaining_tokens -= content_tokens
            elif remaining_tokens > 200:
                # 预算紧张：只加载摘要+首段
                brief = self._get_progressive_content(meta, full_content, remaining_tokens)
                results.append(brief)
                remaining_tokens = 0
            else:
                # 预算耗尽：只添加一行摘要
                results.append(f"[Skill 可用但未加载] {meta.summary}")
                break

            # 更新使用统计
            meta.last_used = time.time()
            meta.use_count += 1

        return results

    def load_skill_full(self, skill_name: str) -> str:
        """按需加载完整 Skill 内容（Agent 主动调用）

        Args:
            skill_name: Skill 名称

        Returns:
            完整 Skill 内容
        """
        for meta in self._index:
            if meta.name.lower() == skill_name.lower():
                content = self._load_full(meta)
                if content:
                    meta.last_used = time.time()
                    meta.use_count += 1
                    return content
                return f"[Error] Skill '{skill_name}' 文件读取失败"

        available = ", ".join(m.name for m in self._index[:10])
        return f"[Error] Skill '{skill_name}' 不存在。可用: {available}"

    def list_skills(self) -> list[SkillMeta]:
        """列出所有可用 Skill"""
        return list(self._index)

    def get_skill_summaries(self) -> str:
        """获取所有 Skill 的摘要目录"""
        if not self._index:
            return "（暂无可用 Skill）"
        lines = [meta.brief for meta in self._index]
        return "\n".join(lines)

    def maybe_create_from_session(self, session: "Session"):
        """从成功会话中自动生成 Skill"""
        tool_sequence = [
            f"{tc.name}({list(tc.arguments.keys())})"
            for tc in session.tool_calls
            if tc.success
        ]

        if len(tool_sequence) < 3:
            return

        task_desc = session.context.get("task", "")
        if not task_desc:
            user_msgs = [m for m in session.messages if m.role == "user"]
            if user_msgs:
                task_desc = user_msgs[0].content[:100]

        if not task_desc:
            return

        # 检查重复
        existing = self.get_relevant_skills(task_desc, max_count=1)
        if existing:
            return

        # 生成
        skill_name = self._generate_skill_name(task_desc)
        skill_content = self._generate_skill_content(task_desc, tool_sequence, session)

        skill_dir = self.skills_dir / skill_name
        skill_dir.mkdir(parents=True, exist_ok=True)
        skill_file = skill_dir / "SKILL.md"
        skill_file.write_text(skill_content, encoding="utf-8")

        logger.info("skill_created", name=skill_name, steps=len(tool_sequence))
        self._build_index()

    def create_skill(self, name: str, content: str):
        """手动创建 Skill"""
        skill_dir = self.skills_dir / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(content, encoding="utf-8")
        self._build_index()

    def reload(self):
        """刷新索引"""
        self._cache.clear()
        self._build_index()

    # ===== 内部方法 =====

    def _build_index(self):
        """扫描目录构建索引"""
        self._index = []

        dirs_to_scan = [self.skills_dir]
        if self.bundled_dir and self.bundled_dir.exists():
            dirs_to_scan.append(self.bundled_dir)

        for base_dir in dirs_to_scan:
            if not base_dir.exists():
                continue

            for item in sorted(base_dir.iterdir()):
                skill_path = None
                if item.is_dir():
                    candidate = item / "SKILL.md"
                    if candidate.exists():
                        skill_path = candidate
                elif item.suffix == ".md" and item.name != "README.md":
                    skill_path = item

                if skill_path:
                    meta = self._parse_meta(skill_path)
                    if meta:
                        self._index.append(meta)

    def _parse_meta(self, path: Path) -> Optional[SkillMeta]:
        """解析 Skill 文件元数据"""
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            return None

        name = path.stem if path.name != "SKILL.md" else path.parent.name
        description = ""
        tags: list[str] = []
        triggers: list[str] = []
        version = "1.0"

        # 解析 YAML frontmatter
        fm_match = re.match(r"^---\s*\n(.+?)\n---\s*\n", content, re.DOTALL)
        if fm_match:
            for line in fm_match.group(1).split("\n"):
                line = line.strip()
                if line.startswith("name:"):
                    name = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("description:"):
                    description = line.split(":", 1)[1].strip().strip("\"'")
                elif line.startswith("tags:"):
                    tags_str = line.split(":", 1)[1].strip()
                    tags = [t.strip().strip("\"'") for t in tags_str.strip("[]").split(",") if t.strip()]
                elif line.startswith("triggers:"):
                    triggers_str = line.split(":", 1)[1].strip()
                    triggers = [t.strip().strip("\"'") for t in triggers_str.strip("[]").split(",") if t.strip()]
                elif line.startswith("version:"):
                    version = line.split(":", 1)[1].strip().strip("\"'")
        else:
            first_line = content.split("\n")[0].strip().lstrip("#").strip()
            description = first_line

        # 内容 hash（版本追踪）
        content_hash = hashlib.md5(content.encode()).hexdigest()[:8]

        # Token 估算（去掉 frontmatter）
        body = re.sub(r"^---\s*\n.+?\n---\s*\n", "", content, flags=re.DOTALL)
        token_estimate = _estimate_tokens(body)

        return SkillMeta(
            name=name,
            description=description,
            tags=tags,
            triggers=triggers,
            version=version,
            path=path,
            content_hash=content_hash,
            token_estimate=token_estimate,
        )

    def _score_skills(self, query: str) -> list[tuple[float, SkillMeta]]:
        """对所有 Skill 按相关性打分"""
        query_lower = query.lower()
        scored: list[tuple[float, SkillMeta]] = []

        for meta in self._index:
            score = 0.0

            # 触发词匹配（最高优先级）
            for trigger in meta.triggers:
                if trigger and trigger.lower() in query_lower:
                    score += 10.0
                    break

            # 名称匹配
            if meta.name.lower() in query_lower:
                score += 5.0

            # 标签匹配
            for tag in meta.tags:
                if tag and tag.lower() in query_lower:
                    score += 3.0

            # 描述关键词匹配
            desc_words = set(meta.description.lower().split())
            query_words = set(query_lower.split())
            overlap = desc_words & query_words
            if overlap:
                score += len(overlap) * 1.5

            if score > 0:
                scored.append((score, meta))

        scored.sort(key=lambda x: x[0], reverse=True)
        return scored

    def _load_full(self, meta: SkillMeta) -> Optional[str]:
        """加载完整内容（去掉 frontmatter）"""
        if meta.name in self._cache:
            return self._cache[meta.name]

        if meta.path and meta.path.exists():
            content = meta.path.read_text(encoding="utf-8")
            content = re.sub(r"^---\s*\n.+?\n---\s*\n", "", content, flags=re.DOTALL)
            self._cache[meta.name] = content.strip()
            return self._cache[meta.name]

        return None

    def _get_progressive_content(self, meta: SkillMeta, full: str, budget: int) -> str:
        """渐进式截取：在 budget token 内尽可能多地展示内容

        策略：
        1. 包含标题和第一个 section
        2. 如果还有余量，继续加载后续 section
        3. 末尾标注 "[已截断，用 load_skill_full 获取完整内容]"
        """
        sections = re.split(r"\n(?=## )", full)
        result_parts = []
        used_tokens = 0

        for section in sections:
            section_tokens = _estimate_tokens(section)
            if used_tokens + section_tokens <= budget:
                result_parts.append(section)
                used_tokens += section_tokens
            else:
                # 预算不够了
                break

        result = "\n".join(result_parts)
        if len(result_parts) < len(sections):
            result += f"\n\n---\n*[内容已截断。完整内容约 {meta.token_estimate} tokens，" \
                      f"使用 load_skill_full(\"{meta.name}\") 获取]*"

        return result

    def _generate_skill_name(self, task_desc: str) -> str:
        words = re.findall(r"[\w\u4e00-\u9fff]+", task_desc)
        name = "-".join(words[:4]).lower()
        return name or f"auto-{int(time.time()) % 10000}"

    def _generate_skill_content(
        self, task_desc: str, tool_sequence: list[str], session: "Session"
    ) -> str:
        steps_text = "\n".join(f"{i+1}. {step}" for i, step in enumerate(tool_sequence))
        success_rate = sum(1 for tc in session.tool_calls if tc.success)
        total = len(session.tool_calls)

        return f"""---
name: "{task_desc[:50]}"
description: "自动从经验中生成"
tags: [auto-generated]
triggers: []
version: "1.0"
---

# {task_desc[:80]}

## 执行步骤

{steps_text}

## 注意事项

- 此 Skill 由系统从会话 {session.id} 中自动生成
- 工具调用成功率: {success_rate}/{total}
- 生成时间: {time.strftime('%Y-%m-%d %H:%M')}
- 如需优化，直接编辑此文件即可
"""
