"""retrieve(question) -> ranked chunks, with ranks/scores from every stage for debugging.

    BM25 top-50 ─┐
                 ├─ RRF (k=60) top-30 ── cross-encoder ── top-6
    dense top-50 ┘

The same code path serves the ablation modes used by the eval:
`bm25`, `dense`, `hybrid` (RRF only) and `hybrid_rerank` (the production path).
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass

import numpy as np
from psycopg_pool import ConnectionPool

from app.config import Settings, get_settings
from app.ingest.chunk import display_title, embedding_text
from app.retrieval import dense as dense_mod
from app.retrieval import rerank as rerank_mod
from app.retrieval.bm25 import BM25Index
from app.retrieval.fusion import rrf

log = logging.getLogger(__name__)

MODES = ("bm25", "dense", "hybrid", "hybrid_rerank")


@dataclass
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    url: str
    doc_title: str
    company: str | None
    heading: str
    text: str
    rank: int = 0
    bm25_rank: int | None = None
    bm25_score: float | None = None
    dense_rank: int | None = None
    dense_score: float | None = None
    rrf_score: float | None = None
    rerank_score: float | None = None

    def debug(self) -> dict:
        d = asdict(self)
        d.pop("text")
        return d


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    best_rerank_score: float | None
    found: bool


def diversify(chunks: list[RetrievedChunk], max_per_doc: int) -> list[RetrievedChunk]:
    """Keep at most `max_per_doc` chunks per document, preserving order (0 disables).

    Without it one long postmortem can fill the whole top-k, starving questions that
    need several incidents ("which outages were caused by ..."). Ranks are renumbered.
    """
    if max_per_doc <= 0:
        return chunks
    seen: dict[str, int] = {}
    out = []
    for c in chunks:
        if seen.get(c.doc_id, 0) < max_per_doc:
            seen[c.doc_id] = seen.get(c.doc_id, 0) + 1
            out.append(c)
    for i, c in enumerate(out, start=1):
        c.rank = i
    return out


class Retriever:
    def __init__(
        self,
        pool: ConnectionPool,
        settings: Settings | None = None,
        embed_query: Callable[[str], np.ndarray] | None = None,
        rerank: Callable[[str, list[str]], list[float]] | None = None,
    ):
        self.pool = pool
        self.s = settings or get_settings()
        self._embed_query = embed_query or dense_mod.embed_query
        self._rerank = rerank or rerank_mod.rerank_scores
        self._bm25: BM25Index | None = None
        self._bm25_mtime: float | None = None
        self._lock = threading.Lock()

    # -- BM25 index lifecycle ----------------------------------------------
    @property
    def bm25(self) -> BM25Index:
        """Load (or reload, if ingestion rewrote it) the on-disk BM25 index; build it if missing."""
        with self._lock:
            mtime = BM25Index.mtime(self.s.bm25_dir)
            if mtime is None:
                self._bm25 = rebuild_bm25(self.pool, self.s)
                self._bm25_mtime = BM25Index.mtime(self.s.bm25_dir)
            elif self._bm25 is None or mtime != self._bm25_mtime:
                self._bm25 = BM25Index.load(self.s.bm25_dir)
                self._bm25_mtime = mtime
                log.info("loaded BM25 index (%d chunks)", len(self._bm25.chunk_ids))
            return self._bm25

    def bm25_loaded(self) -> bool:
        return self._bm25 is not None

    # -- stages ---------------------------------------------------------------
    def _first_stage(self, question: str) -> tuple[list[tuple[str, float]], list[tuple[str, float]]]:
        bm = self.bm25.search(question, self.s.top_k_bm25)
        dn = dense_mod.dense_search(self.pool, self._embed_query(question), self.s.top_k_dense)
        return bm, dn

    def _fetch(self, chunk_ids: list[str]) -> dict[str, RetrievedChunk]:
        if not chunk_ids:
            return {}
        with self.pool.connection() as conn:
            rows = conn.execute(
                """
                SELECT c.chunk_id, c.doc_id, d.url, coalesce(d.title, ''), d.company,
                       coalesce(c.heading, ''), c.text
                FROM chunks c JOIN documents d USING (doc_id)
                WHERE c.chunk_id = ANY(%s)
                """,
                (chunk_ids,),
            ).fetchall()
        return {
            r[0]: RetrievedChunk(r[0], r[1], r[2], display_title(r[4], r[3]) or r[2], r[4], r[5], r[6])
            for r in rows
        }

    def retrieve_all_modes(self, question: str, depth: int = 10) -> dict[str, list[RetrievedChunk]]:
        """Run every ablation mode off a single first stage. Each list is truncated to `depth`."""
        bm, dn = self._first_stage(question)
        fused = rrf(
            {"bm25": [c for c, _ in bm], "dense": [c for c, _ in dn]},
            k=self.s.rrf_k,
            top_n=self.s.fused_k,
        )
        ids = {c for c, _ in bm} | {c for c, _ in dn} | {f.id for f in fused}
        meta = self._fetch(list(ids))
        bm_rank = {c: (i, s) for i, (c, s) in enumerate(bm, start=1)}
        dn_rank = {c: (i, s) for i, (c, s) in enumerate(dn, start=1)}

        def make(cid: str, rank: int, **extra) -> RetrievedChunk:
            base = meta[cid]
            ch = RetrievedChunk(**{**asdict(base), "rank": rank})
            if cid in bm_rank:
                ch.bm25_rank, ch.bm25_score = bm_rank[cid]
            if cid in dn_rank:
                ch.dense_rank, ch.dense_score = dn_rank[cid]
            for key, val in extra.items():
                setattr(ch, key, val)
            return ch

        cap = self.s.max_chunks_per_doc
        # the same diversity cap is applied to every mode, so the ablation stays like-for-like
        out: dict[str, list[RetrievedChunk]] = {
            "bm25": diversify([make(c, i) for i, (c, _) in enumerate(bm, start=1) if c in meta], cap)[:depth],
            "dense": diversify([make(c, i) for i, (c, _) in enumerate(dn, start=1) if c in meta], cap)[:depth],
        }
        fused_chunks = [make(f.id, i, rrf_score=f.score) for i, f in enumerate(fused, start=1) if f.id in meta]
        out["hybrid"] = diversify([make(c.chunk_id, c.rank, rrf_score=c.rrf_score) for c in fused_chunks], cap)[:depth]

        scores = self._rerank(
            question, [embedding_text(c.doc_title, c.heading, c.text) for c in fused_chunks]
        )
        for c, sc in zip(fused_chunks, scores, strict=True):
            c.rerank_score = sc
        reranked = sorted(fused_chunks, key=lambda c: -(c.rerank_score or 0.0))
        out["hybrid_rerank"] = diversify(
            [make(c.chunk_id, i, rrf_score=c.rrf_score, rerank_score=c.rerank_score)
             for i, c in enumerate(reranked, start=1)],
            cap,
        )[:depth]
        return out

    def search(self, question: str, k: int | None = None) -> RetrievalResult:
        """Production path: hybrid + rerank, keep top-k, decide found / not found."""
        k = k or self.s.rerank_k
        ranked = self.retrieve_all_modes(question, depth=max(k, 1))["hybrid_rerank"][:k]
        best = ranked[0].rerank_score if ranked else None
        found = best is not None and best >= self.s.rerank_threshold
        return RetrievalResult(chunks=ranked, best_rerank_score=best, found=found)


def rebuild_bm25(pool: ConnectionPool, settings: Settings | None = None) -> BM25Index:
    """Rebuild the BM25 index from every chunk in Postgres and persist it."""
    s = settings or get_settings()
    with pool.connection() as conn:
        rows = conn.execute(
            """
            SELECT c.chunk_id, coalesce(d.title, ''), coalesce(c.heading, ''), c.text, d.company
            FROM chunks c JOIN documents d USING (doc_id)
            ORDER BY c.chunk_id
            """
        ).fetchall()
    if not rows:
        raise RuntimeError("no chunks in the database yet: run `make ingest` first")
    index = BM25Index.build(
        [r[0] for r in rows], [embedding_text(display_title(r[4], r[1]), r[2], r[3]) for r in rows]
    )
    index.save(s.bm25_dir)
    return index
