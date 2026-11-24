"""Postgres + pgvector: connection pool and idempotent schema bootstrap."""

from __future__ import annotations

from functools import lru_cache

from pgvector.psycopg import register_vector
from psycopg import Connection
from psycopg_pool import ConnectionPool

from app.config import get_settings

SCHEMA = """
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS documents (
  doc_id       TEXT PRIMARY KEY,          -- sha1(url)[:12]
  url          TEXT NOT NULL UNIQUE,
  title        TEXT,
  company      TEXT,
  published    DATE,
  fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  n_chars      INT,
  content_hash TEXT                       -- sha1 of cleaned text; unchanged => skip on re-ingest
);

CREATE TABLE IF NOT EXISTS chunks (
  chunk_id    TEXT PRIMARY KEY,           -- doc_id || '-' || ordinal
  doc_id      TEXT NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
  ordinal     INT  NOT NULL,
  heading     TEXT,
  text        TEXT NOT NULL,
  n_tokens    INT,
  embedding   vector({dim}) NOT NULL
);

CREATE INDEX IF NOT EXISTS chunks_embedding_hnsw
  ON chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id);
"""


def _configure(conn: Connection) -> None:
    register_vector(conn)
    s = get_settings()
    conn.execute(f"SET hnsw.ef_search = {int(s.hnsw_ef_search)}")
    conn.commit()


def bootstrap(conn: Connection) -> None:
    """Create the extension, tables and indexes if they don't exist."""
    conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
    conn.commit()
    conn.execute(SCHEMA.format(dim=get_settings().embed_dim))
    conn.commit()


@lru_cache
def get_pool() -> ConnectionPool:
    s = get_settings()
    # Bootstrap once on a plain connection so register_vector() can find the type.
    import psycopg

    with psycopg.connect(s.database_url) as conn:
        bootstrap(conn)
    pool = ConnectionPool(s.database_url, min_size=1, max_size=8, configure=_configure, open=True)
    return pool


def counts(conn: Connection) -> dict[str, int]:
    n_docs = conn.execute("SELECT count(*) FROM documents").fetchone()[0]
    n_chunks = conn.execute("SELECT count(*) FROM chunks").fetchone()[0]
    return {"documents": int(n_docs), "chunks": int(n_chunks)}
