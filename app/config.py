"""All runtime configuration. Values come from environment variables / `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent.parent

# USD per million tokens (input, output). Used only for the cost column in the eval.
PRICES_PER_MTOK: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-opus-5": (5.00, 25.00),
    "claude-opus-4-8": (5.00, 25.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-sonnet-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}

# Models for which the server-side refusal fallback (`fallbacks: "default"`) is enabled.
SERVER_FALLBACK_MODELS = {
    "claude-fable-5-2",
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    database_url: str = "postgresql://rca:rca@localhost:5432/rca"

    # --- Claude -----------------------------------------------------------
    claude_model: str = "claude-opus-5-5"
    judge_model: str = "claude-sonnet-5-5"
    effort: str = "medium"
    max_tokens: int = 16000
    use_server_fallback: bool = True
    server_fallback_beta: str = "server-side-fallback-2026-07-01"

    # --- Local models -----------------------------------------------------
    embed_model: str = "BAAI/bge-small-en-v1.5"
    embed_dim: int = 384
    query_prefix: str = "Represent this sentence for searching relevant passages: "
    rerank_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # --- Retrieval knobs (reported by the eval next to the numbers) --------
    top_k_bm25: int = 50
    top_k_dense: int = 50
    fused_k: int = 30
    rerank_k: int = 6
    rrf_k: int = 60
    # Diversity cap on every final ranked list (0 = off). Measured: it raises doc-level recall
    # slightly but lowers evidence recall (the answer passage is often a doc's 3rd chunk).
    max_chunks_per_doc: int = 0
    hnsw_ef_search: int = 100  # must be >= top_k_dense or pgvector silently returns fewer rows
    # Cross-encoder logit below which we answer "not in corpus" without calling Claude.
    # Calibrated with `python -m eval.calibrate` on the golden set.
    rerank_threshold: float = -7.4

    # --- Chunking ---------------------------------------------------------
    chunk_tokens: int = 400
    chunk_overlap_tokens: int = 60
    min_section_tokens: int = 60

    # --- Paths ------------------------------------------------------------
    data_dir: Path = ROOT / "data"
    urls_file: Path = ROOT / "data" / "urls.txt"

    # --- Ingestion --------------------------------------------------------
    user_agent: str = (
        "rca-copilot/0.1 (research RAG demo over public postmortems; "
        "+https://github.com/jaypp52/rca-copilot)"
    )
    min_doc_chars: int = 1500
    fetch_interval_s: float = 1.0

    admin_token: str = ""  # POST /ingest is disabled unless this is set

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def bm25_dir(self) -> Path:
        return self.data_dir / "bm25"


@lru_cache
def get_settings() -> Settings:
    return Settings()
