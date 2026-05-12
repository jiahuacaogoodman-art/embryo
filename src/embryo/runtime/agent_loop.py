"""Agent Loop - ReAct 递归循环

核心循环逻辑：
1. 将当前会话上下文 + 可用工具描述 发送给 LLM
2. LLM 返回文本响应或工具调用请求
3. 如果有工具调用 → 执行工具 → 将结果注入回消息 → 回到 1
4. 如果是纯文本响应（无工具调用）→ 循环结束，返回结果
5. 每轮结束后执行 reflect（学习循环）

参考:
- OpenClaw 的 Pi Agent Core (LLM → tool calls → execute → inject results → loop)
- Hermes 的学习循环 (complete task → extract experience → generate/update skill)
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, Optional

from ..config import Config
from .session import Message, Session, SessionStatus, ToolCall

if TYPE_CHECKING:
    from ..skills import SkillManager
    from ..memory import MemoryStore
    from ..tools import ToolRegistry


class AgentLoop:
    """ReAct Agent Loop

    递归执行 LLM 调用和工具执行，直到 LLM 给出最终回答或达到步数上限。
    """

    def __init__(
        self,
        config: Config,
        tool_registry: "ToolRegistry",
        skill_manager: "SkillManager",
        memory_store: "MemoryStore",
    ):
        self.config = config
        self.tools = tool_registry
        self.skills = skill_manager
        self.memory = memory_store
        self._client = None
        self._max_iterations = 30

    def run(self, user_input: str, session: Optional[Session] = None) -> Session:
        """执行一轮完整的 Agent Loop

        Args:
            user_input: 用户输入
            session: 现有会话（None 则创建新会话）

        Returns:
            更新后的 Session
        """
        if session is None:
            session = Session()

        # 注入系统提示（含 Skills 和 Memory）
        if not session.messages:
            system_prompt = self._build_system_prompt(session)
            session.add_message("system", system_prompt)

        # 添加用户消息
        session.add_message("user", user_input)

        # ReAct 循环
        iteration = 0
        while iteration < self._max_iterations and session.status == SessionStatus.ACTIVE:
            iteration += 1

            # 调用 LLM
            response = self._call_llm(session)

            if response is None:
                session.add_message("assistant", "抱歉，LLM 调用失败。")
                session.status = SessionStatus.FAILED
                break

            # 解析响应
            text_content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])

            if not tool_calls:
                # 无工具调用 → 最终回答
                session.add_message("assistant", text_content)
                break

            # 有工具调用 → 执行后继续循环
            if text_content:
                session.add_message("assistant", text_content)

            for tc in tool_calls:
                tool_result = self._execute_tool(tc, session)
                session.add_message(
                    "tool",
                    tool_result.result,
                    name=tool_result.name,
                    tool_call_id=tool_result.id,
                )

        else:
            if iteration >= self._max_iterations:
                session.add_message(
                    "assistant",
                    f"已达到最大循环次数 ({self._max_iterations})，停止执行。"
                )

        # Reflect: 学习循环
        self._reflect(session)

        return session

    def _build_system_prompt(self, session: Session) -> str:
        """构建系统提示词

        组合：基础人设 + 可用工具说明 + 相关 Skills + 相关 Memory
        """
        parts = [SYSTEM_BASE_PROMPT]

        # 加载相关记忆
        memories = self.memory.recall_relevant(session.context.get("task", ""))
        if memories:
            parts.append("\n## 你的记忆\n")
            for mem in memories[:10]:
                parts.append(f"- {mem}")

        # 加载相关 Skills
        loaded = self.skills.get_relevant_skills(session.context.get("task", ""))
        for skill_content in loaded[:3]:
            parts.append(f"\n## Skill\n{skill_content}")
            session.loaded_skills.append(skill_content[:50])

        return "\n".join(parts)

    def _call_llm(self, session: Session) -> Optional[dict[str, Any]]:
        """调用 LLM，返回解析后的响应"""
        try:
            client = self._get_client()
            messages = session.get_conversation_for_llm()
            tools_schema = self.tools.get_openai_tools_schema()

            kwargs: dict[str, Any] = {
                "model": self.config.llm.model,
                "messages": messages,
                "temperature": self.config.llm.temperature,
                "max_tokens": self.config.llm.max_tokens,
            }
            if tools_schema:
                kwargs["tools"] = tools_schema
                kwargs["tool_choice"] = "auto"

            response = client.chat.completions.create(**kwargs)
            choice = response.choices[0]
            message = choice.message

            result: dict[str, Any] = {"content": message.content or ""}

            if message.tool_calls:
                result["tool_calls"] = []
                for tc in message.tool_calls:
                    result["tool_calls"].append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": json.loads(tc.function.arguments),
                    })

            session.total_tokens += response.usage.total_tokens if response.usage else 0
            return result

        except Exception as e:
            print(f"[AgentLoop] LLM 调用失败: {e}")
            return None

    def _execute_tool(self, tool_call: dict[str, Any], session: Session) -> ToolCall:
        """执行单个工具调用"""
        start = time.time()
        name = tool_call["name"]
        arguments = tool_call.get("arguments", {})
        tc_id = tool_call.get("id", "")

        record = ToolCall(id=tc_id, name=name, arguments=arguments)

        try:
            result = self.tools.execute(name, arguments, session=session)
            record.result = str(result)
            record.success = True
        except Exception as e:
            record.result = f"Error: {e}"
            record.success = False

        record.duration = time.time() - start
        session.add_tool_call(record)
        return record

    def _reflect(self, session: Session):
        """学习循环 - 任务完成后提取经验

        检查本次会话中的失败和成功模式，决定是否：
        1. 保存新的记忆条目
        2. 创建/更新 Skill
        """
        # 统计失败的工具调用
        failures = [tc for tc in session.tool_calls if not tc.success]
        if failures:
            lesson = f"会话 {session.id} 中有 {len(failures)} 次工具调用失败: "
            lesson += "; ".join(f"{f.name}({f.arguments}) → {f.result}" for f in failures[:3])
            self.memory.store("lesson", lesson)

        # 如果会话成功完成且步骤较多，考虑生成 Skill
        if (
            session.status != SessionStatus.FAILED
            and session.total_steps >= 3
            and self.config.skills.auto_create
        ):
            self.skills.maybe_create_from_session(session)

    def _get_client(self):
        """获取 OpenAI 兼容客户端"""
        if self._client is None:
            from openai import OpenAI
            kwargs = {"api_key": self.config.llm.api_key}
            if self.config.llm.base_url:
                kwargs["base_url"] = self.config.llm.base_url
            self._client = OpenAI(**kwargs)
        return self._client


# 基础系统提示词
SYSTEM_BASE_PROMPT = """你是 Embryo，一个自主 AI 智能体。你能持久运行、跨会话记忆、自我改进。

## 核心能力
- 通过工具与环境交互（文件读写、终端执行、网页浏览、GUI 操作）
- 记住用户偏好和过去的经验教训
- 从经验中学习，自动生成可复用的 Skill 文档
- 操作桌面 GUI（点击、输入、滚动、截图识别）

## 行为原则
1. 每次行动前先观察和思考，不要盲目执行
2. 遇到不确定的情况主动询问，不要猜测
3. 操作后验证结果，失败则分析原因并调整策略
4. 重要的经验教训主动存入记忆
5. 复杂任务拆解为小步骤，逐步执行
6. 高风险操作前告知用户并确认

## 工具使用
你可以使用提供的工具完成任务。每次只调用必要的工具，避免冗余操作。
工具调用结果会返回给你，你据此决定下一步行动。
"""
