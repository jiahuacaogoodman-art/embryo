"""LLM 重排序器


"""

from __future__ import annotations

import json
import re
from typing import Callable

from ..logging import get_logger

logger = get_logger("reranker")

_RERANK_PROMPT = """\
你是一个相关性排序助手。给定搜索查询和文本段落，按相关性从高到低排序。

查询: {query}

段落:
{passages}

只返回 JSON 数组（段落编号，0-based，从高到低）。例: [2, 0, 3, 1]"""


class LLMReranker:
    """LLM 重排序器。"""

    def __init__(self, llm_call: Callable[[str], str], max_chars: int = 300):
        self._llm_call = llm_call
        self._max_chars = max_chars

    def rerank(self, query: str, candidates: list[dict], top_k: int) -> list[dict]:
        if not candidates or len(candidates) <= 1:
            return candidates[:top_k]

        passages_text = "\n\n".join(
            f"[{i}] {c['content'][:self._max_chars]}" for i, c in enumerate(candidates)
        )
        prompt = _RERANK_PROMPT.format(query=query, passages=passages_text)

        try:
            raw = self._llm_call(prompt).strip()
            match = re.search(r"\[[\d,\s]+\]", raw)
            if not match:
                raise ValueError(f"No JSON array: {raw[:100]}")
            indices: list[int] = json.loads(match.group())
            reranked = [candidates[i] for i in indices if 0 <= i < len(candidates)]
            seen = set(indices)
            for i, c in enumerate(candidates):
                if i not in seen:
                    reranked.append(c)
            return reranked[:top_k]
        except Exception as e:
            logger.warning("rerank_failed", error=str(e))
            return candidates[:top_k]
