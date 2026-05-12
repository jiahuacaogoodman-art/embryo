"""Reciprocal Rank Fusion (RRF)


"""

from __future__ import annotations

from collections import defaultdict


def reciprocal_rank_fusion(
    ranked_lists: list[list[tuple[float, dict]]],
    k: int = 60,
) -> list[tuple[float, dict]]:
    """融合多个排序列表。

    Args:
        ranked_lists: [(score, chunk_dict), ...] 按分数降序，chunk 需有 '_idx'
        k: 平滑常数（默认 60）

    Returns:
        [(rrf_score, chunk_dict)] 降序
    """
    rrf_scores: dict[int, float] = defaultdict(float)
    chunk_by_idx: dict[int, dict] = {}

    for ranked in ranked_lists:
        for rank, (_, chunk) in enumerate(ranked):
            idx = chunk.get("_idx", id(chunk))
            rrf_scores[idx] += 1.0 / (k + rank + 1)
            chunk_by_idx[idx] = chunk

    fused = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)
    return [(score, chunk_by_idx[idx]) for idx, score in fused]
