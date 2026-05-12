"""BM25 稀疏检索


支持 rank-bm25 包（pip install rank-bm25），无则回退到 TF 词频匹配。
增强：支持中文分词（bigram）。
"""

from __future__ import annotations

import re
from typing import Any

try:
    from rank_bm25 import BM25Okapi
    _HAS_BM25 = True
except ImportError:
    _HAS_BM25 = False


def _tokenize(text: str) -> list[str]:
    """中英文混合分词：英文按词，中文按字+bigram。"""
    tokens = []
    parts = re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]+", text.lower())
    for part in parts:
        if re.match(r"[\u4e00-\u9fff]", part):
            for ch in part:
                tokens.append(ch)
            for i in range(len(part) - 1):
                tokens.append(part[i:i+2])
        else:
            if len(part) >= 2:
                tokens.append(part)
    return tokens


class BM25Retriever:
    """BM25 稀疏检索器。"""

    def __init__(self):
        self._corpus: list[dict] = []
        self._bm25: Any = None
        self._tokenized: list[list[str]] = []

    def fit(self, corpus: list[dict]):
        self._corpus = corpus
        self._tokenized = [_tokenize(c["content"]) for c in corpus]
        if _HAS_BM25 and corpus:
            self._bm25 = BM25Okapi(self._tokenized)

    def retrieve(self, query: str, top_k: int) -> list[tuple[float, dict]]:
        if not self._corpus:
            return []
        tokens = _tokenize(query)
        if _HAS_BM25 and self._bm25:
            raw_scores = self._bm25.get_scores(tokens)
            pairs = [(float(s), c) for s, c in zip(raw_scores, self._corpus) if s > 0]
        else:
            query_set = set(tokens)
            pairs = []
            for chunk in self._corpus:
                chunk_tokens = _tokenize(chunk["content"])
                if not chunk_tokens:
                    continue
                tf = sum(1 for t in chunk_tokens if t in query_set)
                if tf > 0:
                    pairs.append((float(tf) / len(chunk_tokens), chunk))
        pairs.sort(key=lambda x: x[0], reverse=True)
        return pairs[:top_k]
