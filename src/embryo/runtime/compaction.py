"""上下文压缩 (Context Compaction)

搬运自 PythonClaw (MIT License) 并适配 Embryo 架构。

当对话历史超过 token 阈值时，自动：
1. 将旧消息中的重要事实提取到 Memory（memory flush）
2. 用 LLM 将旧消息总结为简短摘要
3. 用摘要替换旧消息，保留近期消息完整
4. 记录压缩日志（可审计）

这解决了长对话会超出 context window 导致报错的问题。
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, TYPE_CHECKING

from ..logging import get_logger

if TYPE_CHECKING:
    from ..memory.store import MemoryStore

logger = get_logger("compaction")

# 1 token ≈ 4 characters（保守估计）
CHARS_PER_TOKEN = 4
# 默认触发阈值
DEFAULT_THRESHOLD_TOKENS = 6000
# 默认保留最近消息数
DEFAULT_RECENT_KEEP = 6


def estimate_tokens(messages: list[dict[str, Any]]) -> int:
    """粗略估算消息列表的 token 数。

    不使用外部 tokenizer，用字符数 / 4 近似。
    """
    total_chars = sum(len(str(m.get("content") or "")) for m in messages)
    return total_chars // CHARS_PER_TOKEN


def messages_to_transcript(messages: list[dict[str, Any]]) -> str:
    """将消息列表转为可读文本（供 LLM 总结用）。"""
    lines = []
    for m in messages:
        role = m.get("role", "?")
        content = m.get("content") or ""

        # assistant 无文本但有 tool_calls 的情况
        if role == "assistant" and not content and m.get("tool_calls"):
            tool_names = []
            for tc in m.get("tool_calls", []):
                if isinstance(tc, dict):
                    tool_names.append(tc.get("function", {}).get("name", "?"))
                else:
                    tool_names.append(getattr(tc, "name", "?"))
            content = f"[调用工具: {', '.join(tool_names)}]"

        # tool 结果截断
        if role == "tool":
            content = f"[工具结果]: {content[:300]}{'...' if len(content) > 300 else ''}"

        if content:
            lines.append(f"{role.upper()}: {content}")

    return "\n".join(lines)


def memory_flush(
    messages_to_flush: list[dict[str, Any]],
    memory: "MemoryStore",
    llm_call: Any,  # callable: (prompt: str) -> str
) -> int:
    """从即将丢弃的消息中提取重要事实存入 Memory。

    Args:
        messages_to_flush: 要丢弃的旧消息
        memory: MemoryStore 实例
        llm_call: LLM 调用函数 (prompt → response_text)

    Returns:
        保存的事实数量
    """
    if not messages_to_flush:
        return 0

    transcript = messages_to_transcript(messages_to_flush)
    prompt = (
        "你是一个记忆提取助手。"
        "从以下对话记录中识别所有重要的事实、决定、偏好和上下文。"
        "返回 JSON 数组，每项包含 'category' 和 'content' 字段。"
        "category 可选: preference/environment/lesson/project/fact。"
        "如果没有重要内容则返回 []。\n\n"
        f"对话记录:\n{transcript}\n\n"
        "只返回 JSON，不要解释。"
    )

    try:
        raw = llm_call(prompt)
        # 清理 markdown code fence
        raw = raw.strip()
        if raw.startswith("```"):
            raw = "\n".join(raw.split("\n")[1:])
        if raw.endswith("```"):
            raw = raw[:raw.rfind("```")]

        facts: list[dict] = json.loads(raw.strip())
        saved = 0
        for fact in facts:
            category = str(fact.get("category", "fact")).strip()
            content = str(fact.get("content", "")).strip()
            if content:
                memory.store(category, content, source="compaction")
                saved += 1

        logger.info("memory_flush_complete", saved=saved)
        return saved

    except Exception as e:
        logger.warning("memory_flush_failed", error=str(e))
        return 0


def compact(
    messages: list[dict[str, Any]],
    llm_call: Any,  # callable: (prompt: str) -> str
    memory: Optional["MemoryStore"] = None,
    recent_keep: int = DEFAULT_RECENT_KEEP,
    log_dir: Optional[Path] = None,
) -> tuple[list[dict[str, Any]], str]:
    """执行上下文压缩。

    Args:
        messages: 完整消息列表（含 system）
        llm_call: LLM 调用函数 (prompt → response_text)
        memory: 可选 MemoryStore（用于 flush）
        recent_keep: 保留最近 N 条消息
        log_dir: 压缩日志目录

    Returns:
        (压缩后的消息列表, 摘要文本)
    """
    # 分离 system 消息和对话消息
    system_msgs = [m for m in messages if m.get("role") == "system"]
    chat_msgs = [m for m in messages if m.get("role") != "system"]

    if len(chat_msgs) <= recent_keep:
        logger.info("compaction_skip", reason="消息数不足", count=len(chat_msgs))
        return messages, ""

    to_summarise = chat_msgs[:-recent_keep]
    to_keep = chat_msgs[-recent_keep:]

    logger.info(
        "compaction_start",
        summarising=len(to_summarise),
        keeping=len(to_keep),
        estimated_tokens=estimate_tokens(to_summarise),
    )

    # 1. Memory flush — 在丢弃消息前保存重要事实
    if memory is not None:
        memory_flush(to_summarise, memory, llm_call)

    # 2. 总结
    transcript = messages_to_transcript(to_summarise)
    summarise_prompt = (
        "请简洁总结以下对话历史。"
        "重点关注：做出的决定、学到的事实、完成的任务、待解决的问题。\n\n"
        f"对话内容:\n{transcript}\n\n"
        "用3-8句话或要点总结："
    )

    try:
        summary = llm_call(summarise_prompt).strip()
    except Exception as e:
        logger.error("compaction_summarise_failed", error=str(e))
        raise

    # 3. 持久化日志
    if log_dir:
        _persist_log(log_dir, summary, len(to_summarise))

    # 4. 构建压缩后的消息列表
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary_msg = {
        "role": "system",
        "content": f"[上下文压缩摘要 — {ts}]\n{summary}",
    }

    new_messages = system_msgs + [summary_msg] + to_keep

    logger.info(
        "compaction_complete",
        original_count=len(messages),
        new_count=len(new_messages),
        summary_length=len(summary),
    )

    return new_messages, summary


def should_compact(messages: list[dict[str, Any]], threshold: int = DEFAULT_THRESHOLD_TOKENS) -> bool:
    """判断是否应该触发压缩。"""
    return estimate_tokens(messages) > threshold


def _persist_log(log_dir: Path, summary: str, message_count: int):
    """写入压缩审计日志。"""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / "compaction.jsonl"
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "summarised_messages": message_count,
        "summary": summary,
    }
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")
