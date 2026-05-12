"""向量嵌入 Memory 后端

支持 sentence-transformers 本地嵌入或 OpenAI Embedding API。
提供语义级检索能力（比 TF-IDF 关键词匹配更强）。

使用方式：
- 安装: pip install sentence-transformers
- 首次使用自动下载模型（~90MB）
- 也可以通过 OpenAI API 使用 text-embedding-3-small
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ..logging import get_logger
from .store import MemoryEntry, MemoryStore

logger = get_logger("embeddings")


class EmbeddingBackend:
    """嵌入计算后端（抽象）"""

    def embed(self, text: str) -> list[float]:
        raise NotImplementedError

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]

    @property
    def dimension(self) -> int:
        raise NotImplementedError


class LocalEmbeddingBackend(EmbeddingBackend):
    """本地嵌入（sentence-transformers）

    默认使用 all-MiniLM-L6-v2（轻量、384维、多语言尚可）。
    中文优化推荐 shibing624/text2vec-base-chinese。
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = None
        self._dim = 0

    def _load_model(self):
        if self._model is None:
            try:
                from sentence_transformers import SentenceTransformer
                self._model = SentenceTransformer(self._model_name)
                # 获取维度
                test = self._model.encode(["test"])
                self._dim = len(test[0])
                logger.info("embedding_model_loaded", model=self._model_name, dim=self._dim)
            except ImportError:
                raise ImportError(
                    "向量嵌入需要 sentence-transformers: "
                    "pip install sentence-transformers"
                )

    def embed(self, text: str) -> list[float]:
        self._load_model()
        vec = self._model.encode([text])[0]
        return vec.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self._load_model()
        vecs = self._model.encode(texts)
        return [v.tolist() for v in vecs]

    @property
    def dimension(self) -> int:
        if self._dim == 0:
            self._load_model()
        return self._dim


class OpenAIEmbeddingBackend(EmbeddingBackend):
    """OpenAI Embedding API 后端

    使用 text-embedding-3-small（1536维）。
    """

    def __init__(self, api_key: str = "", base_url: Optional[str] = None, model: str = "text-embedding-3-small"):
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            kwargs = {"api_key": self._api_key}
            if self._base_url:
                kwargs["base_url"] = self._base_url
            self._client = OpenAI(**kwargs)
        return self._client

    def embed(self, text: str) -> list[float]:
        client = self._get_client()
        response = client.embeddings.create(input=[text], model=self._model)
        return response.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        client = self._get_client()
        response = client.embeddings.create(input=texts, model=self._model)
        return [d.embedding for d in response.data]

    @property
    def dimension(self) -> int:
        return 1536  # text-embedding-3-small


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """计算两个向量的余弦相似度"""
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class VectorMemoryStore(MemoryStore):
    """向量嵌入增强的 Memory Store

    继承 MemoryStore 的所有功能，额外提供语义检索。
    每条记忆存储时自动计算嵌入向量，检索时用余弦相似度排序。
    """

    def __init__(
        self,
        storage_path: Path,
        max_entries: int = 1000,
        backend: Optional[EmbeddingBackend] = None,
    ):
        self._embedding_backend = backend
        self._vectors: dict[str, list[float]] = {}  # entry_id → embedding vector
        super().__init__(storage_path, max_entries)
        self._load_vectors()

    def store(self, category: str, content: str, **kwargs) -> MemoryEntry:
        """存储记忆（同时计算嵌入向量）"""
        entry = super().store(category, content, **kwargs)

        # 计算并缓存嵌入
        if self._embedding_backend:
            try:
                vec = self._embedding_backend.embed(content)
                self._vectors[entry.id] = vec
                self._save_vectors()
            except Exception as e:
                logger.warning("embedding_compute_failed", entry_id=entry.id, error=str(e))

        return entry

    def recall_relevant(self, query: str, max_count: int = 10) -> list[str]:
        """语义检索（向量相似度 + TF-IDF 混合）

        策略：
        1. 如果有嵌入向量 → 用余弦相似度排序
        2. 回退到 TF-IDF 基线
        3. 最终结果是两者的加权融合
        """
        if not query or not self._entries:
            return []

        # 如果嵌入可用，使用向量检索
        if self._embedding_backend and self._vectors:
            return self._vector_recall(query, max_count)

        # 否则回退到 TF-IDF
        return super().recall_relevant(query, max_count)

    def _vector_recall(self, query: str, max_count: int) -> list[str]:
        """向量语义检索"""
        try:
            query_vec = self._embedding_backend.embed(query)
        except Exception as e:
            logger.warning("query_embedding_failed", error=str(e))
            return super().recall_relevant(query, max_count)

        now = time.time()
        scored: list[tuple[float, MemoryEntry]] = []

        for entry in self._entries:
            entry_vec = self._vectors.get(entry.id)
            if entry_vec is None:
                continue

            # 余弦相似度
            sim = cosine_similarity(query_vec, entry_vec)

            if sim <= 0.1:  # 过滤低相关度
                continue

            # 重要性加权
            sim *= entry.importance

            # 时间衰减
            age_days = (now - entry.accessed_at) / 86400
            decay = math.exp(-0.023 * age_days)
            sim *= decay

            scored.append((sim, entry))

        scored.sort(key=lambda x: x[0], reverse=True)

        results = []
        for _, entry in scored[:max_count]:
            entry.accessed_at = now
            entry.access_count += 1
            results.append(f"[{entry.category}] {entry.content}")

        if results:
            self._save()

        return results

    def rebuild_embeddings(self):
        """重新计算所有记忆的嵌入向量（用于模型更换后）"""
        if not self._embedding_backend:
            return

        contents = [e.content for e in self._entries]
        if not contents:
            return

        try:
            vectors = self._embedding_backend.embed_batch(contents)
            self._vectors = {}
            for entry, vec in zip(self._entries, vectors):
                self._vectors[entry.id] = vec
            self._save_vectors()
            logger.info("embeddings_rebuilt", count=len(self._vectors))
        except Exception as e:
            logger.error("embeddings_rebuild_failed", error=str(e))

    def forget(self, entry_id: str):
        """删除记忆（包括向量）"""
        self._vectors.pop(entry_id, None)
        super().forget(entry_id)
        self._save_vectors()

    def _load_vectors(self):
        """从文件加载向量缓存"""
        vec_file = self.storage_path / "vectors.json"
        if vec_file.exists():
            try:
                data = json.loads(vec_file.read_text(encoding="utf-8"))
                self._vectors = data.get("vectors", {})
            except Exception:
                self._vectors = {}

    def _save_vectors(self):
        """持久化向量缓存"""
        self.storage_path.mkdir(parents=True, exist_ok=True)
        vec_file = self.storage_path / "vectors.json"
        data = {"vectors": self._vectors, "count": len(self._vectors)}
        vec_file.write_text(json.dumps(data), encoding="utf-8")
