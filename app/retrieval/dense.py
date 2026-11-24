"""Dense retrieval: bge-small embeddings stored in pgvector, searched with the HNSW index."""

from __future__ import annotations

from functools import lru_cache

import numpy as np
from psycopg_pool import ConnectionPool

from app.config import get_settings


@lru_cache
def get_embedder():
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(get_settings().embed_model, device="cpu")


def embed_passages(texts: list[str], batch_size: int = 32) -> np.ndarray:
    """Embed passages (no instruction prefix for bge passages). Returns L2-normalised float32."""
    vecs = get_embedder().encode(
        texts, batch_size=batch_size, normalize_embeddings=True, show_progress_bar=False
    )
    return np.asarray(vecs, dtype=np.float32)


def embed_query(question: str) -> np.ndarray:
    """Embed a query with the bge query-instruction prefix."""
    s = get_settings()
    vec = get_embedder().encode(
        [s.query_prefix + question], normalize_embeddings=True, show_progress_bar=False
    )[0]
    return np.asarray(vec, dtype=np.float32)


def dense_search(pool: ConnectionPool, query_vec: np.ndarray, k: int) -> list[tuple[str, float]]:
    """Top-k (chunk_id, cosine similarity) via `ORDER BY embedding <=> q`."""
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT chunk_id, 1 - (embedding <=> %(q)s) AS sim
            FROM chunks
            ORDER BY embedding <=> %(q)s
            LIMIT %(k)s
            """,
            {"q": query_vec, "k": k},
        ).fetchall()
    return [(r[0], float(r[1])) for r in rows]
