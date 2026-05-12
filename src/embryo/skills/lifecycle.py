"""Skill 生命周期管理 — LLM 辅助生成、使用中优化、版本对比、社区导入

参考 Hermes Agent 的 Skill 自我改进机制：
1. 自动生成：任务完成后，LLM 总结步骤 → 生成高质量 SKILL.md
2. 使用中优化：Skill 被加载但任务失败时，LLM 分析并修订 Skill 内容
3. 版本对比：每次修改前保留旧版本，可回滚
4. 社区导入：从 URL 或本地路径导入外部 Skill
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from ..logging import get_logger

if TYPE_CHECKING:
    from ..config import Config
    from ..runtime.session import Session
    from .manager import SkillManager

logger = get_logger("skill_lifecycle")

# LLM 生成 Skill 的提示词
SKILL_GENERATION_PROMPT = """你是一个 Skill 文档生成器。根据以下会话记录，生成一个可复用的 SKILL.md 文档。

## 会话信息
- 任务描述: {task_description}
- 总步骤数: {total_steps}
- 工具调用序列:
{tool_sequence}

## 输出要求
生成一个标准的 SKILL.md 文件，包含以下结构：

```
---
name: "简洁的技能名称"
description: "一句话描述这个 Skill 做什么"
tags: [相关标签]
triggers: [触发这个 Skill 的关键词]
version: "1.0"
---

# 技能名称

## 何时使用
描述什么情况下应该使用这个技能。

## 执行步骤
1. 步骤一
2. 步骤二
...

## 注意事项
- 常见错误和规避方法
- 前置条件
- 风险提示
```

请基于会话中的实际操作步骤生成，确保步骤具体可执行，不要泛泛而谈。
只输出 SKILL.md 的完整内容，不要附加任何解释。
"""

SKILL_OPTIMIZATION_PROMPT = """你是一个 Skill 优化器。以下 Skill 在使用中遇到了问题，请优化它。

## 当前 Skill 内容
{current_skill}

## 问题描述
- 使用此 Skill 执行任务: {task_description}
- 执行结果: 失败
- 失败的工具调用:
{failures}

## 优化要求
1. 分析失败原因
2. 在相应步骤中添加防错措施
3. 补充"注意事项"部分
4. 保持原有结构不变，只修改需要改进的部分

输出完整的优化后 SKILL.md 内容。
"""


class SkillLifecycle:
    """Skill 生命周期管理器

    负责 Skill 的创建、优化、版本控制和外部导入。
    """

    def __init__(self, config: "Config", skill_manager: "SkillManager"):
        self.config = config
        self.manager = skill_manager
        self._versions_dir = config.skills.skills_dir / ".versions"
        self._versions_dir.mkdir(parents=True, exist_ok=True)

    # ===== 1. LLM 辅助生成 =====

    def generate_from_session(self, session: "Session") -> Optional[str]:
        """从成功会话中用 LLM 生成高质量 Skill

        与 SkillManager.maybe_create_from_session 的区别：
        - 那个是模板填充，这个调用 LLM 生成更自然的文档
        - 需要 LLM API 可用

        Args:
            session: 已完成的会话

        Returns:
            生成的 Skill 名称，失败返回 None
        """
        successful_calls = [tc for tc in session.tool_calls if tc.success]
        if len(successful_calls) < 3:
            return None

        task_desc = session.context.get("task", "")
        if not task_desc:
            user_msgs = [m for m in session.messages if m.role == "user"]
            if user_msgs:
                task_desc = user_msgs[0].content[:200]

        if not task_desc:
            return None

        # 检查重复
        existing = self.manager.get_relevant_skills(task_desc, max_count=1)
        if existing:
            return None

        # 构建工具调用序列描述
        tool_sequence = ""
        for i, tc in enumerate(successful_calls, 1):
            args_brief = json.dumps(tc.arguments, ensure_ascii=False)[:100]
            result_brief = tc.result[:80]
            tool_sequence += f"  {i}. {tc.name}({args_brief}) → {result_brief}\n"

        # 调用 LLM 生成
        prompt = SKILL_GENERATION_PROMPT.format(
            task_description=task_desc,
            total_steps=len(successful_calls),
            tool_sequence=tool_sequence,
        )

        skill_content = self._call_llm(prompt)
        if not skill_content or len(skill_content) < 50:
            logger.warning("skill_generation_failed", task=task_desc[:50])
            return None

        # 提取名称
        import re
        name_match = re.search(r'name:\s*"(.+?)"', skill_content)
        skill_name = name_match.group(1) if name_match else task_desc[:30]

        # 保存
        safe_name = re.sub(r"[^\w\u4e00-\u9fff-]", "-", skill_name).strip("-").lower()
        self.manager.create_skill(safe_name, skill_content)

        logger.info("skill_generated_by_llm", name=safe_name, task=task_desc[:50])
        return safe_name

    # ===== 2. 使用中优化 =====

    def optimize_skill(self, skill_name: str, session: "Session") -> bool:
        """优化 Skill：当 Skill 被使用但任务失败时

        Args:
            skill_name: 要优化的 Skill 名称
            session: 失败的会话

        Returns:
            是否成功优化
        """
        # 加载当前内容
        current_content = self.manager.load_skill_full(skill_name)
        if not current_content or "Error" in current_content:
            return False

        # 提取失败信息
        failures = [tc for tc in session.tool_calls if not tc.success]
        if not failures:
            return False

        failure_desc = ""
        for f in failures[:5]:
            failure_desc += f"  - {f.name}: {f.result[:100]}\n"

        task_desc = session.context.get("task", "")

        # 调用 LLM 优化
        prompt = SKILL_OPTIMIZATION_PROMPT.format(
            current_skill=current_content,
            task_description=task_desc,
            failures=failure_desc,
        )

        optimized = self._call_llm(prompt)
        if not optimized or len(optimized) < 50:
            logger.warning("skill_optimization_failed", skill=skill_name)
            return False

        # 保存旧版本
        self._save_version(skill_name, current_content)

        # 更新 Skill
        self.manager.create_skill(skill_name, optimized)
        self.manager.reload()

        logger.info("skill_optimized", skill=skill_name)
        return True

    # ===== 3. 版本管理 =====

    def _save_version(self, skill_name: str, content: str):
        """保存 Skill 的历史版本"""
        skill_versions_dir = self._versions_dir / skill_name
        skill_versions_dir.mkdir(parents=True, exist_ok=True)

        timestamp = time.strftime("%Y%m%d_%H%M%S")
        version_file = skill_versions_dir / f"v_{timestamp}.md"
        version_file.write_text(content, encoding="utf-8")

        # 只保留最近 10 个版本
        versions = sorted(skill_versions_dir.glob("v_*.md"), reverse=True)
        for old in versions[10:]:
            old.unlink()

    def list_versions(self, skill_name: str) -> list[dict[str, Any]]:
        """列出 Skill 的历史版本

        Returns:
            版本列表 [{"filename": ..., "timestamp": ..., "size": ...}, ...]
        """
        skill_versions_dir = self._versions_dir / skill_name
        if not skill_versions_dir.exists():
            return []

        versions = []
        for f in sorted(skill_versions_dir.glob("v_*.md"), reverse=True):
            versions.append({
                "filename": f.name,
                "timestamp": f.stem.replace("v_", ""),
                "size": f.stat().st_size,
                "path": str(f),
            })
        return versions

    def rollback(self, skill_name: str, version_filename: str) -> bool:
        """回滚 Skill 到指定版本

        Args:
            skill_name: Skill 名称
            version_filename: 版本文件名（如 v_20240101_120000.md）

        Returns:
            是否成功
        """
        version_path = self._versions_dir / skill_name / version_filename
        if not version_path.exists():
            return False

        # 当前版本先存档
        current = self.manager.load_skill_full(skill_name)
        if current:
            self._save_version(skill_name, current)

        # 回滚
        old_content = version_path.read_text(encoding="utf-8")
        self.manager.create_skill(skill_name, old_content)
        self.manager.reload()

        logger.info("skill_rollback", skill=skill_name, version=version_filename)
        return True

    def diff_versions(self, skill_name: str, version_a: str, version_b: str) -> str:
        """对比两个版本的差异

        Returns:
            简单的差异描述
        """
        dir_path = self._versions_dir / skill_name
        path_a = dir_path / version_a
        path_b = dir_path / version_b

        if not path_a.exists() or not path_b.exists():
            return "[Error] 版本文件不存在"

        content_a = path_a.read_text(encoding="utf-8").splitlines()
        content_b = path_b.read_text(encoding="utf-8").splitlines()

        # 简单行级对比
        added = [l for l in content_b if l not in content_a]
        removed = [l for l in content_a if l not in content_b]

        result = f"版本对比: {version_a} → {version_b}\n"
        result += f"新增 {len(added)} 行, 删除 {len(removed)} 行\n\n"

        if removed:
            result += "删除:\n" + "\n".join(f"- {l}" for l in removed[:10]) + "\n\n"
        if added:
            result += "新增:\n" + "\n".join(f"+ {l}" for l in added[:10]) + "\n"

        return result

    # ===== 4. 社区导入 =====

    def import_from_url(self, url: str, skill_name: Optional[str] = None) -> Optional[str]:
        """从 URL 导入 Skill

        支持：
        - GitHub raw file URL
        - 任何返回 Markdown 内容的 URL

        Args:
            url: Skill 文件 URL
            skill_name: 自定义名称（None=从内容提取）

        Returns:
            导入后的 Skill 名称
        """
        try:
            import urllib.request
            response = urllib.request.urlopen(url, timeout=15)
            content = response.read().decode("utf-8")
        except Exception as e:
            logger.error("skill_import_url_failed", url=url, error=str(e))
            return None

        return self._import_content(content, skill_name, source=url)

    def import_from_file(self, filepath: str, skill_name: Optional[str] = None) -> Optional[str]:
        """从本地文件导入 Skill

        Args:
            filepath: 本地 Markdown 文件路径
            skill_name: 自定义名称

        Returns:
            导入后的 Skill 名称
        """
        path = Path(filepath)
        if not path.exists():
            logger.error("skill_import_file_not_found", path=filepath)
            return None

        content = path.read_text(encoding="utf-8")
        default_name = skill_name or path.stem
        return self._import_content(content, default_name, source=filepath)

    def import_from_registry(self, registry_name: str) -> Optional[str]:
        """从 Skill 注册表导入（兼容 agentskills.io 标准）

        目前简化为从 GitHub 仓库路径导入。

        Args:
            registry_name: 格式如 "owner/skill-name"

        Returns:
            导入后的 Skill 名称
        """
        # 尝试 GitHub raw 路径
        url = f"https://raw.githubusercontent.com/{registry_name}/main/SKILL.md"
        return self.import_from_url(url, skill_name=registry_name.split("/")[-1])

    def _import_content(self, content: str, skill_name: Optional[str], source: str = "") -> Optional[str]:
        """导入 Skill 内容"""
        import re

        if not content or len(content) < 20:
            return None

        # 从 frontmatter 提取名称
        if not skill_name:
            name_match = re.search(r'name:\s*"(.+?)"', content)
            if name_match:
                skill_name = name_match.group(1)
            else:
                skill_name = f"imported-{int(time.time()) % 10000}"

        safe_name = re.sub(r"[^\w\u4e00-\u9fff-]", "-", skill_name).strip("-").lower()

        # 添加来源元数据
        if source and "---" in content:
            content = content.replace("---\n", f"---\n# imported_from: {source}\n", 1)

        self.manager.create_skill(safe_name, content)
        logger.info("skill_imported", name=safe_name, source=source[:80])
        return safe_name

    # ===== 工具方法 =====

    def _call_llm(self, prompt: str) -> Optional[str]:
        """调用 LLM 生成内容"""
        try:
            from openai import OpenAI

            kwargs: dict[str, Any] = {"api_key": self.config.llm.api_key}
            if self.config.llm.base_url:
                kwargs["base_url"] = self.config.llm.base_url

            client = OpenAI(**kwargs)
            response = client.chat.completions.create(
                model=self.config.llm.model,
                messages=[
                    {"role": "system", "content": "你是一个技术文档生成器，输出规范的 Markdown 格式。"},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.3,
                max_tokens=2000,
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            logger.error("skill_llm_call_failed", error=str(e))
            return None

    def get_stats(self) -> dict[str, Any]:
        """获取 Skill 系统统计信息"""
        skills = self.manager.list_skills()
        total_versions = sum(
            len(list((self._versions_dir / s.name).glob("v_*.md")))
            for s in skills
            if (self._versions_dir / s.name).exists()
        )

        return {
            "total_skills": len(skills),
            "total_versions": total_versions,
            "auto_generated": sum(1 for s in skills if "auto-generated" in s.tags),
            "most_used": sorted(skills, key=lambda s: s.use_count, reverse=True)[:3],
        }
