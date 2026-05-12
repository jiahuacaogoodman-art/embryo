"""Hybrid Retrieval 管线

搬运自 PythonClaw/core/retrieval/ (MIT License) 并适配。

管线流程:
    corpus → BM25 (稀疏) + Embedding (密集)
                    ↓
         Reciprocal Rank Fusion
                    ↓
          LLM Reranker (可选)
                    ↓
               top_k 结果

用于 Memory 语义检索和 Knowledge RAG。
"""

from .retriever import HybridRetriever

__all__ = ["HybridRetriever"]
