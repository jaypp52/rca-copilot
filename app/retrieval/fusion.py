"""Reciprocal Rank Fusion.

score(c) = sum over lists i of 1 / (k + rank_i(c)),  rank is 1-based.

RRF only looks at ranks, so BM25 scores (unbounded) and cosine similarities (-1..1)
never need to be normalised against each other.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Fused:
    id: str
    score: float
    ranks: dict[str, int] = field(default_factory=dict)  # list name -> 1-based rank


def rrf(rankings: dict[str, list[str]], k: int = 60, top_n: int | None = None) -> list[Fused]:
    """Fuse several ranked id lists.

    Ties are broken deterministically by first appearance (lists in the given order,
    then position), so equal-scoring items keep a stable order.
    """
    fused: dict[str, Fused] = {}
    first_seen: dict[str, int] = {}
    for name, ids in rankings.items():
        seen_here: set[str] = set()
        for rank, item in enumerate(ids, start=1):
            if item in seen_here:  # ignore duplicates within one list
                continue
            seen_here.add(item)
            if item not in fused:
                fused[item] = Fused(item, 0.0)
                first_seen[item] = len(first_seen)
            fused[item].score += 1.0 / (k + rank)
            fused[item].ranks[name] = rank
    ordered = sorted(fused.values(), key=lambda f: (-f.score, first_seen[f.id]))
    return ordered[:top_n] if top_n is not None else ordered
