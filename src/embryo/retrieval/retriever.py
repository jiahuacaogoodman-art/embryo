"""HybridRetriever — BM25 + Embedding + RRF + LLM Reranker

搬运自 PythonClaw (MIT License) 并适配 Embryo。
"""

from __future__ import annotations

from typing import Callable, Optional

from ..logging import get_logger
from .dense import EmbeddingRetriever
from .fusion import reciprocal_rank_fusion
from .reranker import LLMReranker
from .sparse import BM25Retriever

logger = get_logger("retriever")


class HybridRetriever:
    """混合检索器：稀疏 + 密集 + RRF + 可选 LLM 重排。

    Args:
        llm_call: LLM 调用函数（用于 reranker），None 则禁用
        use_sparse: 启用 BM25
        use_dense: 启用向量嵌入
        use_reranker: 启用 LLM 重排
        dense_model: sentence-transformers 模型名
    """

    def __init__(
        self,
        llm_call: Optional[Callable[[str], str]] = None,
        use_sparse: bool = True,
        use_dense: bool = True,
        use_reranker: bool = True,
        dense_model: str = "all-MiniLM-L6-v2",
    ):
        self.use_sparse = use_sparse
        self.use_dense = use_dense
        self.use_reranker = use_reranker and llm_call is not None

        self._sparse = BM25Retriever() if use_sparse else None
        self._dense = EmbeddingRetriever(dense_model) if use_dense else None
        self._reranker = LLMReranker(llm_call) if self.use_reranker else None
        self._corpus: list[dict] = []

        if use_dense and self._dense:
            logger.info("retriever_init", backend=self._dense.backend_name)

    def fit(self, corpus: list[dict]) -> "HybridRetriever":
        """索引语料库。每个 chunk 必须有 'content' 字段。"""
        for i, chunk in enumerate(corpus):
            chunk["_idx"] = i
        self._corpus = corpus

        if self._sparse:
            self._sparse.fit(corpus)
        if self._dense:
            self._dense.fit(corpus)

        logger.info("retriever_indexed", chunks=len(corpus))
        return self

    def retrieve(self, query: str, top_k: int = 5) -> list[dict]:
        """检索 top_k 最相关的 chunks。"""
        if not self._corpus or not query.strip():
            return []

        fetch_k = max(top_k * 3, top_k + 5)
        ranked_lists: list[list[tuple[float, dict]]] = []

        if self._sparse:
            r = self._sparse.retrieve(query, top_k=fetch_k)
            if r:
                ranked_lists.append(r)

        if self._dense:
            r = self._dense.retrieve(query, top_k=fetch_k)
            if r:
                ranked_lists.append(r)

        if not ranked_lists:
            return []

        if len(ranked_lists) == 1:
            fused = ranked_lists[0]
        else:
            fused = reciprocal_rank_fusion(ranked_lists)

        candidates = [c for _, c in fused[:fetch_k if self._reranker else top_k]]

        if self._reranker and candidates:
            candidates = self._reranker.rerank(query, candidates, top_k)
        else:
            candidates = candidates[:top_k]

        return [{k: v for k, v in c.items() if k != "_idx"} for c in candidates]

    def __len__(self) -> int:
        return len(self._corpus)
