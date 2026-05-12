"""学习引擎 - 从经验中提取知识并持久化

核心流程：
1. 会话结束时分析执行过程
2. 提取失败模式 → 存入 Memory 作为教训
3. 提取成功模式 → 判断是否值得生成 Skill
4. 对已有 Skill 进行补充/修正
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..memory.store import MemoryStore
    from ..runtime.session import Session
    from ..skills.manager import SkillManager


class LearningEngine:
    """学习引擎

    在每次会话结束后被调用，分析会话内容，提取可持久化的知识。
    """

    def __init__(self, memory: "MemoryStore", skills: "SkillManager"):
        self.memory = memory
        self.skills = skills

    def learn_from_session(self, session: "Session"):
        """从会话中学习

        Args:
            session: 已完成的会话
        """
        self._extract_failures(session)
        self._extract_corrections(session)
        self._consider_skill_creation(session)

    def _extract_failures(self, session: "Session"):
        """提取失败经验作为教训"""
        failures = [tc for tc in session.tool_calls if not tc.success]
        if not failures:
            return

        # 对每个失败的工具调用，记录教训
        for fail in failures:
            lesson = (
                f"工具 {fail.name} 调用失败: "
                f"参数={fail.arguments}, "
                f"错误={fail.result}"
            )
            # 检查是否已有类似教训
            existing = self.memory.recall_relevant(fail.name, max_count=3)
            if not any(fail.result[:30] in e for e in existing):
                self.memory.store(
                    "lesson",
                    lesson,
                    tags=[fail.name, "failure"],
                    importance=1.5,
                )

    def _extract_corrections(self, session: "Session"):
        """提取用户纠正

        如果用户消息中包含"不要"、"应该"、"改为"等纠正性关键词，
        提取为 correction 类型的记忆。
        """
        correction_keywords = [
            "不要", "不对", "应该", "改为", "换成", "别用",
            "用这个", "记住", "以后", "注意",
        ]

        for msg in session.messages:
            if msg.role != "user":
                continue
            content = msg.content.lower()
            if any(kw in content for kw in correction_keywords):
                # 这可能是一个纠正
                self.memory.store(
                    "correction",
                    f"用户纠正: {msg.content[:200]}",
                    tags=["user_correction"],
                    importance=2.0,
                    source=session.id,
                )

    def _consider_skill_creation(self, session: "Session"):
        """考虑是否从会话中创建 Skill

        条件：
        - 会话有 3+ 次成功工具调用
        - 有明确的任务描述
        - 不与已有 Skill 高度重复
        """
        successful_calls = [tc for tc in session.tool_calls if tc.success]
        if len(successful_calls) < 3:
            return

        task_desc = session.context.get("task", "")
        if not task_desc:
            # 尝试从第一条用户消息提取
            user_msgs = [m for m in session.messages if m.role == "user"]
            if user_msgs:
                task_desc = user_msgs[0].content[:100]

        if not task_desc:
            return

        # 委托给 SkillManager 判断和创建
        self.skills.maybe_create_from_session(session)

    def store_user_preference(self, preference: str, source: str = ""):
        """显式存储用户偏好"""
        self.memory.store(
            "preference",
            preference,
            tags=["user_preference"],
            importance=2.0,
            source=source,
        )

    def store_environment_info(self, info: str, source: str = ""):
        """存储环境信息"""
        self.memory.store(
            "environment",
            info,
            tags=["environment"],
            importance=1.0,
            source=source,
        )
