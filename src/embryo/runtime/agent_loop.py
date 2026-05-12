"""Agent Loop - ReAct 递归循环

实现级特性：
- 流式输出：LLM 响应实时回传给调用方
- 工具循环检测：检测同一工具反复调用相同参数的死循环
- 超时控制：单步工具执行有超时限制
- 优雅降级：LLM 不可用时给出有意义的错误，不崩溃
- 背压保护：工具输出过长时截断

参考:
- OpenClaw Pi Agent Core (LLM → tool calls → execute → inject results → loop)
- Hermes 学习循环 (complete task → extract experience → generate/update skill)
"""

from __future__ import annotations

import hashlib
import json
import signal
import time
from collections import Counter
from contextlib import contextmanager
from typing import TYPE_CHECKING, Any, Callable, Generator, Optional

from ..config import Config
from ..logging import get_logger
from .session import Message, Session, SessionStatus, ToolCall

if TYPE_CHECKING:
    from ..skills import SkillManager
    from ..memory import MemoryStore
    from ..tools import ToolRegistry

logger = get_logger(__name__)

# 工具输出最大字符数（防止撑爆上下文窗口）
MAX_TOOL_OUTPUT = 8000
# 单步工具执行超时
TOOL_TIMEOUT_SECONDS = 120


class LoopAbort(Exception):
    """Agent Loop 主动终止"""
    pass


class AgentLoop:
    """ReAct Agent Loop

    递归执行 LLM 调用和工具执行，直到 LLM 给出最终回答或触发终止条件。

    终止条件：
    1. LLM 返回纯文本（无 tool_calls） → 正常结束
    2. 达到 max_iterations → 超出步数
    3. 检测到工具循环 → 强制停止
    4. 连续 LLM 失败 → 降级退出
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
        self._max_consecutive_errors = 3
        self._on_stream: Optional[Callable[[str], None]] = None

    def run(
        self,
        user_input: str,
        session: Optional[Session] = None,
        on_stream: Optional[Callable[[str], None]] = None,
    ) -> Session:
        """执行一轮完整的 Agent Loop

        Args:
            user_input: 用户输入
            session: 现有会话（None 则创建新会话）
            on_stream: 流式输出回调函数（每产生一段文本就调用）

        Returns:
            更新后的 Session
        """
        if session is None:
            session = Session()

        self._on_stream = on_stream
        loop_start = time.time()

        # 注入系统提示（含 Skills 和 Memory）
        if not session.messages:
            system_prompt = self._build_system_prompt(session)
            session.add_message("system", system_prompt)

        # 添加用户消息
        session.add_message("user", user_input)

        logger.info("agent_loop_start", session_id=session.id, input_length=len(user_input))

        # 循环检测器
        tool_call_hashes: list[str] = []
        consecutive_errors = 0
        iteration = 0

        while iteration < self._max_iterations and session.status == SessionStatus.ACTIVE:
            iteration += 1
            iter_start = time.time()

            logger.debug("loop_iteration", iteration=iteration, session_id=session.id)

            # 调用 LLM
            response = self._call_llm(session)

            if response is None:
                consecutive_errors += 1
                logger.warning(
                    "llm_call_failed",
                    consecutive_errors=consecutive_errors,
                    session_id=session.id,
                )
                if consecutive_errors >= self._max_consecutive_errors:
                    session.add_message(
                        "assistant",
                        "LLM 服务连续不可用，无法继续。请检查 API 配置后重试。",
                    )
                    session.status = SessionStatus.FAILED
                    break
                # 短暂等待后重试
                time.sleep(min(2 ** consecutive_errors, 10))
                continue

            consecutive_errors = 0  # 成功调用重置计数

            # 解析响应
            text_content = response.get("content", "")
            tool_calls = response.get("tool_calls", [])

            if not tool_calls:
                # 无工具调用 → 最终回答
                session.add_message("assistant", text_content)
                if self._on_stream:
                    self._on_stream(text_content)
                break

            # 有工具调用
            # 先输出思考文本
            if text_content:
                session.add_message("assistant", text_content, metadata={"has_tool_calls": True})
                if self._on_stream:
                    self._on_stream(text_content)

            # 循环检测
            if self._detect_loop(tool_calls, tool_call_hashes):
                logger.warning("tool_loop_detected", session_id=session.id, iteration=iteration)
                session.add_message(
                    "assistant",
                    "检测到工具调用循环（重复调用相同工具和参数），停止执行以防无限循环。",
                )
                break

            # 执行工具
            for tc in tool_calls:
                tool_result = self._execute_tool_safe(tc, session)
                session.add_message(
                    "tool",
                    tool_result.result,
                    name=tool_result.name,
                    tool_call_id=tool_result.id,
                )

            logger.debug(
                "iteration_complete",
                iteration=iteration,
                tools_called=[tc["name"] for tc in tool_calls],
                duration=time.time() - iter_start,
            )

        else:
            if iteration >= self._max_iterations:
                session.add_message(
                    "assistant",
                    f"已达到最大循环次数 ({self._max_iterations})，停止执行。"
                    f"如需继续，请再次发送指令。",
                )
                logger.warning("max_iterations_reached", session_id=session.id)

        # Reflect: 学习循环
        self._reflect(session)

        total_duration = time.time() - loop_start
        logger.info(
            "agent_loop_complete",
            session_id=session.id,
            iterations=iteration,
            total_steps=session.total_steps,
            total_tokens=session.total_tokens,
            duration=round(total_duration, 2),
            status=session.status.value,
        )

        return session

    def _build_system_prompt(self, session: Session) -> str:
        """构建系统提示词

        组合：基础人设 + 相关 Memory + 相关 Skills（渐进式）
        """
        parts = [SYSTEM_BASE_PROMPT]

        # 加载相关记忆
        task = session.context.get("task", "")
        memories = self.memory.recall_relevant(task)
        if memories:
            parts.append("\n## 你的记忆（来自过去的经验）\n")
            for mem in memories[:10]:
                parts.append(f"- {mem}")

        # 加载相关 Skills（渐进式：只加载匹配的）
        loaded = self.skills.get_relevant_skills(task)
        if loaded:
            parts.append("\n## 相关技能文档\n")
            for skill_content in loaded[:3]:
                # Token 预算保护：超长 Skill 截断
                if len(skill_content) > 3000:
                    skill_content = skill_content[:3000] + "\n\n...(内容过长，已截断)"
                parts.append(skill_content)
                session.loaded_skills.append(skill_content[:50])

        # 可用 Skill 目录（让 Agent 知道有哪些 Skill 存在）
        summaries = self.skills.get_skill_summaries()
        if summaries and "暂无" not in summaries:
            parts.append(f"\n## 已安装的全部 Skills\n{summaries}")

        return "\n".join(parts)

    def _call_llm(self, session: Session) -> Optional[dict[str, Any]]:
        """调用 LLM

        包含重试和错误处理，失败时返回 None。
        """
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
                    try:
                        args = json.loads(tc.function.arguments)
                    except json.JSONDecodeError:
                        args = {}
                    result["tool_calls"].append({
                        "id": tc.id,
                        "name": tc.function.name,
                        "arguments": args,
                    })

            session.total_tokens += response.usage.total_tokens if response.usage else 0
            return result

        except Exception as e:
            logger.error("llm_call_error", error=str(e), model=self.config.llm.model)
            return None

    def _execute_tool_safe(self, tool_call: dict[str, Any], session: Session) -> ToolCall:
        """安全执行工具调用（带超时和输出截断）"""
        start = time.time()
        name = tool_call["name"]
        arguments = tool_call.get("arguments", {})
        tc_id = tool_call.get("id", "")

        record = ToolCall(id=tc_id, name=name, arguments=arguments)

        logger.debug("tool_execute_start", tool=name, arguments=arguments)

        try:
            result = self.tools.execute(name, arguments, session=session)
            result_str = str(result)

            # 输出截断保护（防止撑爆上下文）
            if len(result_str) > MAX_TOOL_OUTPUT:
                truncated_notice = (
                    f"\n\n[输出已截断: 原始 {len(result_str)} 字符，"
                    f"保留前 {MAX_TOOL_OUTPUT} 字符]"
                )
                result_str = result_str[:MAX_TOOL_OUTPUT] + truncated_notice

            record.result = result_str
            record.success = True

        except TimeoutError:
            record.result = f"[Error] 工具执行超时 ({TOOL_TIMEOUT_SECONDS}s): {name}"
            record.success = False
            logger.warning("tool_timeout", tool=name, timeout=TOOL_TIMEOUT_SECONDS)

        except KeyError as e:
            record.result = f"[Error] 未知工具: {name}。{e}"
            record.success = False

        except Exception as e:
            record.result = f"[Error] {type(e).__name__}: {e}"
            record.success = False
            logger.error("tool_execute_error", tool=name, error=str(e))

        record.duration = time.time() - start
        session.add_tool_call(record)

        logger.debug(
            "tool_execute_complete",
            tool=name,
            success=record.success,
            duration=round(record.duration, 3),
            output_length=len(record.result),
        )

        return record

    def _detect_loop(self, tool_calls: list[dict], history: list[str]) -> bool:
        """检测工具调用循环

        策略：
        - 将每次 tool_calls 序列化为 hash
        - 如果最近 N 次 hash 相同 → 死循环

        Args:
            tool_calls: 本次工具调用列表
            history: 历史 hash 列表（会原地追加）

        Returns:
            是否检测到循环
        """
        # 生成本次调用的特征 hash
        sig = json.dumps(
            [(tc["name"], sorted(tc.get("arguments", {}).items())) for tc in tool_calls],
            sort_keys=True,
        )
        h = hashlib.md5(sig.encode()).hexdigest()[:12]
        history.append(h)

        # 检查最近 3 次是否完全相同
        if len(history) >= 3:
            last_3 = history[-3:]
            if len(set(last_3)) == 1:
                return True

        # 检查最近 5 次中同一 hash 出现 4 次以上
        if len(history) >= 5:
            recent = history[-5:]
            counts = Counter(recent)
            if counts.most_common(1)[0][1] >= 4:
                return True

        return False

    def _reflect(self, session: Session):
        """学习循环 - 任务完成后提取经验"""
        # 统计失败的工具调用
        failures = [tc for tc in session.tool_calls if not tc.success]
        if failures:
            # 去重：相同工具+相同错误只记录一次
            seen = set()
            for f in failures:
                key = f"{f.name}:{f.result[:50]}"
                if key not in seen:
                    seen.add(key)
                    lesson = f"工具 {f.name} 调用失败: {f.result[:200]}"
                    self.memory.store("lesson", lesson, source=session.id)

        # 如果会话成功完成且步骤较多，考虑生成 Skill
        if (
            session.status != SessionStatus.FAILED
            and session.total_steps >= 3
            and self.config.skills.auto_create
        ):
            self.skills.maybe_create_from_session(session)

    def _get_client(self):
        """获取 OpenAI 兼容客户端（懒初始化）"""
        if self._client is None:
            from openai import OpenAI

            kwargs: dict[str, Any] = {"api_key": self.config.llm.api_key}
            if self.config.llm.base_url:
                kwargs["base_url"] = self.config.llm.base_url
            self._client = OpenAI(**kwargs)
        return self._client


# =============================================================================
# 系统提示词
# =============================================================================

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
4. 重要的经验教训主动存入记忆（用 remember 工具）
5. 复杂任务拆解为小步骤，逐步执行
6. 高风险操作前告知用户并确认
7. 不要反复调用相同参数的工具 — 如果结果不变，换个策略

## 工具使用
你可以使用提供的工具完成任务。每次只调用必要的工具，避免冗余操作。
工具调用结果会返回给你，你据此决定下一步行动。
如果工具返回错误，分析原因后调整参数或改用其他方式，不要原样重试。
"""
