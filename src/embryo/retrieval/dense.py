"""密集检索 — 向量嵌入语义相似度


"""

from __future__ import annotations

from typing import Any

try:
    import numpy as np
    from sentence_transformers import SentenceTransformer  # type: ignore
    _HAS_ST = True
except ImportError:
    _HAS_ST = False

try:
    import numpy as np  # type: ignore
    from sklearn.feature_extraction.text import TfidfVectorizer  # type: ignore
    from sklearn.metrics.pairwise import cosine_similarity as _sklearn_cos  # type: ignore
    _HAS_SKLEARN = True
except ImportError:
    _HAS_SKLEARN = False


def _char_bigrams(text: str) -> set[str]:
    t = text.lower()
    return {t[i:i+2] for i in range(len(t) - 1)}


def _jaccard(a: set, b: set) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


class _SentenceTransformersBackend:
    def __init__(self, model_name: str):
        self._model = SentenceTransformer(model_name)
        self._embeddings: Any = None
        self._corpus: list[dict] = []

    def fit(self, corpus: list[dict]):
        self._corpus = corpus
        texts = [c["content"] for c in corpus]
        self._embeddings = self._model.encode(texts, show_progress_bar=False, convert_to_numpy=True)

    def retrieve(self, query: str, top_k: int) -> list[tuple[float, dict]]:
        if self._embeddings is None or not self._corpus:
            return []
        q_emb = self._model.encode([query], convert_to_numpy=True)
        sims = (self._embeddings @ q_emb.T).flatten()
        ranked = sorted(zip(sims.tolist(), self._corpus), key=lambda x: x[0], reverse=True)
        return [(float(s), c) for s, c in ranked[:top_k] if s > 0]


class _TfidfBackend:
    def __init__(self):
        self._vec: Any = None
        self._matrix: Any = None
        self._corpus: list[dict] = []

    def fit(self, corpus: list[dict]):
        self._corpus = corpus
        if not corpus:
            return
        texts = [c["content"] for c in corpus]
        self._vec = TfidfVectorizer(analyzer="word", min_df=1)
        self._matrix = self._vec.fit_transform(texts)

    def retrieve(self, query: str, top_k: int) -> list[tuple[float, dict]]:
        if self._vec is None or not self._corpus:
            return []
        q_vec = self._vec.transform([query])
        sims = _sklearn_cos(q_vec, self._matrix).flatten()
        ranked = sorted(zip(sims.tolist(), self._corpus), key=lambda x: x[0], reverse=True)
        return [(float(s), c) for s, c in ranked[:top_k] if s > 0]


class _BigramBackend:
    def __init__(self):
        self._bigrams: list[set] = []
        self._corpus: list[dict] = []

    def fit(self, corpus: list[dict]):
        self._corpus = corpus
        self._bigrams = [_char_bigrams(c["content"]) for c in corpus]

    def retrieve(self, query: str, top_k: int) -> list[tuple[float, dict]]:
        q_bg = _char_bigrams(query)
        pairs = [(_jaccard(q_bg, bg), chunk) for bg, chunk in zip(self._bigrams, self._corpus)]
        pairs.sort(key=lambda x: x[0], reverse=True)
        return [(s, c) for s, c in pairs[:top_k] if s > 0]


class EmbeddingRetriever:
    """统一的密集检索器，自动选择最佳可用后端。"""

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        if _HAS_ST:
            self._backend = _SentenceTransformersBackend(model_name)
            self.backend_name = f"sentence-transformers({model_name})"
        elif _HAS_SKLEARN:
            self._backend = _TfidfBackend()
            self.backend_name = "sklearn-tfidf"
        else:
            self._backend = _BigramBackend()
            self.backend_name = "bigram-jaccard"

    def fit(self, corpus: list[dict]):
        self._backend.fit(corpus)

    def retrieve(self, query: str, top_k: int) -> list[tuple[float, dict]]:
        return self._backend.retrieve(query, top_k)
