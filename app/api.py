"""FastAPI app: /health, /search (retrieval only), /ask (RAG with citations), /ingest (admin)."""

from __future__ import annotations

import hmac
import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config import get_settings
from app.generation.answer import GenerationError
from app.retrieval.pipeline import Retriever
from app.service import ask

log = logging.getLogger("api")


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------
class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=1000)
    k: int = Field(default=6, ge=1, le=10)


class CitationOut(BaseModel):
    n: int
    chunk_id: str
    doc_title: str
    url: str
    cited_text: str


class StageDebug(BaseModel):
    rank: int
    chunk_id: str
    doc_id: str
    doc_title: str
    company: str | None
    heading: str
    url: str
    bm25_rank: int | None
    bm25_score: float | None
    dense_rank: int | None
    dense_score: float | None
    rrf_score: float | None
    rerank_score: float | None


class AskResponse(BaseModel):
    answer: str
    citations: list[CitationOut]
    uncited: list[str]
    citation_coverage: float | None
    not_found: bool
    retrieval: list[StageDebug]
    usage: dict
    model: str | None
    latency_ms: float


class SearchResponse(BaseModel):
    query: str
    found: bool
    best_rerank_score: float | None
    results: list[StageDebug]


# ---------------------------------------------------------------------------
# app
# ---------------------------------------------------------------------------
def create_app(retriever: Retriever | None = None, anthropic_client=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if app.state.retriever is None:
            from app.db import get_pool

            app.state.retriever = Retriever(get_pool())
            try:  # warm up: load BM25 + both models so the first request isn't slow
                app.state.retriever.search("warm up the retrieval models", 1)
            except Exception as e:  # e.g. empty database before the first ingest
                log.warning("warm-up skipped: %s", e)
        yield

    app = FastAPI(
        title="RCA Copilot",
        description="Hybrid RAG over public incident postmortems with inline citations.",
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.retriever = retriever
    app.state.anthropic_client = anthropic_client

    def get_retriever(request: Request) -> Retriever:
        return request.app.state.retriever

    @app.get("/health")
    def health(r: Retriever = Depends(get_retriever)):
        s = r.s
        db_ok, counts = True, None
        try:
            from app.db import counts as db_counts

            with r.pool.connection() as conn:
                counts = db_counts(conn)
        except Exception as e:  # report, don't crash
            db_ok = False
            log.warning("health: db check failed: %s", e)
        return {
            "status": "ok" if db_ok else "degraded",
            "db_ok": db_ok,
            "corpus": counts,
            "index_loaded": r.bm25_loaded(),
            "models": {
                "generator": s.claude_model,
                "judge": s.judge_model,
                "embedding": s.embed_model,
                "reranker": s.rerank_model,
            },
        }

    @app.get("/search", response_model=SearchResponse)
    def search(
        q: str = Query(min_length=3, max_length=1000),
        k: int = Query(default=6, ge=1, le=30),
        r: Retriever = Depends(get_retriever),
    ):
        res = r.search(q, k)
        return SearchResponse(
            query=q,
            found=res.found,
            best_rerank_score=res.best_rerank_score,
            results=[StageDebug(**c.debug()) for c in res.chunks],
        )

    @app.post("/ask", response_model=AskResponse)
    def ask_endpoint(body: AskRequest, request: Request, r: Retriever = Depends(get_retriever)):
        try:
            res = ask(r, body.question, body.k, client=request.app.state.anthropic_client)
        except GenerationError as e:
            raise HTTPException(status_code=e.status_code, detail={"error": e.kind, "message": str(e)}) from e
        a = res.answer
        return AskResponse(
            answer=a.answer,
            citations=[CitationOut(**c.__dict__) for c in a.citations],
            uncited=a.cite_report.uncited,
            citation_coverage=a.cite_report.coverage,
            not_found=a.not_found,
            retrieval=[StageDebug(**c.debug()) for c in res.retrieval.chunks],
            usage=a.usage,
            model=a.model,
            latency_ms=round(res.latency_ms, 1),
        )

    @app.post("/ingest")
    def ingest(x_admin_token: str | None = Header(default=None), r: Retriever = Depends(get_retriever)):
        expected = get_settings().admin_token
        if not expected:
            raise HTTPException(403, "ingestion over HTTP is disabled (set ADMIN_TOKEN)")
        if not x_admin_token or not hmac.compare_digest(x_admin_token, expected):
            raise HTTPException(401, "bad admin token")
        from app.ingest.run import run_ingest

        return run_ingest(pool=r.pool)

    return app


app = create_app()
