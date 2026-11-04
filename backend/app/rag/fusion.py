"""Reciprocal Rank Fusion: merge several ranked lists into one.

Used twice: hybrid search (dense list + keyword list) and multi-query rewriting (one list per
query variant). Scores from different searches aren't comparable (a cosine 0.7 and a BM25 12.3
mean nothing to each other), but ranks are. Each item gets 1 / (K + rank) from every list it
appears in; items high in several lists win.

K = 60 is the constant from the original paper (Cormack et al., 2009): it damps the difference
between rank 1 and rank 2, so one list can't dominate.
"""

from collections.abc import Callable, Hashable, Sequence
from typing import TypeVar

T = TypeVar("T")
K = 60


def reciprocal_rank_fusion(rankings: Sequence[Sequence[T]], key: Callable[[T], Hashable], limit: int) -> list[T]:
    """The fused top `limit` items, best first. `key` says when two items are the same (a chunk id)."""
    scores: dict[Hashable, float] = {}
    first_seen: dict[Hashable, T] = {}
    for ranking in rankings:
        for rank, item in enumerate(ranking):
            k = key(item)
            scores[k] = scores.get(k, 0.0) + 1.0 / (K + rank + 1)
            first_seen.setdefault(k, item)
    best = sorted(scores, key=scores.__getitem__, reverse=True)[:limit]
    return [first_seen[k] for k in best]
