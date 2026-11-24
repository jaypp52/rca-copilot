"""Cross-encoder reranking.

The bi-encoder embeds question and chunk separately; the cross-encoder reads them
together (full attention across both), which is far more precise but O(n) model calls,
so it only sees the ~30 fused candidates.
"""

from __future__ import annotations

from functools import lru_cache

from app.config import get_settings


@lru_cache
def get_cross_encoder():
    from sentence_transformers import CrossEncoder

    return CrossEncoder(get_settings().rerank_model, device="cpu")


def rerank_scores(question: str, passages: list[str]) -> list[float]:
    """Raw cross-encoder logits for (question, passage) pairs; higher = more relevant."""
    if not passages:
        return []
    scores = get_cross_encoder().predict(
        [(question, p) for p in passages], batch_size=32, show_progress_bar=False
    )
    return [float(s) for s in scores]
