"""Ingestion CLI.

    python -m app.ingest.run                       # fetch every URL in data/urls.txt
    python -m app.ingest.run --fixtures tests/fixtures/postmortems   # synthetic CI corpus
    python -m app.ingest.run stats                 # print document / chunk counts

Idempotent: documents are keyed by sha1(url)[:12]; a document whose cleaned-text hash
is unchanged is skipped, so a second run writes nothing.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from psycopg_pool import ConnectionPool

from app.config import Settings, get_settings
from app.db import counts, get_pool
from app.ingest.chunk import chunk_document, display_title, embedding_text
from app.ingest.fetch import FetchedDoc, Fetcher, Skip, content_hash, doc_id_for, fetch_document

log = logging.getLogger("ingest")


@dataclass
class IngestReport:
    seen: int = 0
    inserted: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "seen": self.seen,
            "inserted": self.inserted,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "skipped": len(self.skipped),
        }


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------
def read_urls(path: Path) -> list[tuple[str, str | None]]:
    """urls.txt: `url<TAB>company<TAB>category` (extra columns optional, `#` comments)."""
    out = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cols = line.split("\t")
        out.append((cols[0].strip(), cols[1].strip() if len(cols) > 1 and cols[1].strip() else None))
    return out


FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n", re.S)


def read_fixture(path: Path) -> FetchedDoc:
    """Fixture postmortems are markdown with a tiny `key: value` front-matter block."""
    raw = path.read_text()
    m = FRONTMATTER_RE.match(raw)
    if not m:
        raise ValueError(f"{path} has no front-matter")
    meta = dict(line.split(":", 1) for line in m.group(1).splitlines() if ":" in line)
    meta = {k.strip(): v.strip() for k, v in meta.items()}
    url = meta["url"]
    return FetchedDoc(
        doc_id=doc_id_for(url),
        url=url,
        title=meta.get("title", path.stem),
        company=meta.get("company"),
        published=date.fromisoformat(meta["published"]) if meta.get("published") else None,
        text=raw[m.end():].strip(),
    )


# ---------------------------------------------------------------------------
# near-duplicate detection (the same postmortem under two URLs, e.g. archive.org copies)
# ---------------------------------------------------------------------------
WORD_RE = re.compile(r"[a-z0-9]+")


def shingles(text: str, n_words: int = 600, k: int = 5) -> set[str]:
    words = WORD_RE.findall(text.lower())[:n_words]
    return {" ".join(words[i : i + k]) for i in range(max(0, len(words) - k + 1))}


def jaccard(a: set[str], b: set[str]) -> float:
    return len(a & b) / len(a | b) if a and b else 0.0


class DuplicateDetector:
    def __init__(self, pool: ConnectionPool, threshold: float = 0.8):
        self.threshold = threshold
        with pool.connection() as conn:
            rows = conn.execute(
                "SELECT doc_id, string_agg(text, ' ' ORDER BY ordinal) FROM chunks GROUP BY doc_id"
            ).fetchall()
        self.known = {doc_id: shingles(text) for doc_id, text in rows}

    def duplicate_of(self, doc: FetchedDoc) -> str | None:
        sh = shingles(doc.text)
        for other, osh in self.known.items():
            if other != doc.doc_id and jaccard(sh, osh) >= self.threshold:
                return other
        return None

    def add(self, doc: FetchedDoc) -> None:
        self.known[doc.doc_id] = shingles(doc.text)


# ---------------------------------------------------------------------------
# storage
# ---------------------------------------------------------------------------
def upsert_document(pool: ConnectionPool, doc: FetchedDoc, s: Settings, report: IngestReport,
                    force: bool = False) -> None:
    from app.retrieval.dense import embed_passages

    h = content_hash(doc.text)
    with pool.connection() as conn:
        row = conn.execute(
            "SELECT content_hash FROM documents WHERE doc_id = %s", (doc.doc_id,)
        ).fetchone()
    if row and row[0] == h and not force:
        report.unchanged += 1
        return

    chunks = chunk_document(doc.text, s.chunk_tokens, s.chunk_overlap_tokens, s.min_section_tokens)
    if not chunks:
        report.skipped.append({"url": doc.url, "reason": "no chunks produced"})
        return
    title = display_title(doc.company, doc.title)
    vecs = embed_passages([embedding_text(title, c.heading, c.text) for c in chunks])

    with pool.connection() as conn, conn.transaction():
        conn.execute(
            """
            INSERT INTO documents (doc_id, url, title, company, published, n_chars, content_hash)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (doc_id) DO UPDATE SET
              title = EXCLUDED.title, company = EXCLUDED.company, published = EXCLUDED.published,
              n_chars = EXCLUDED.n_chars, content_hash = EXCLUDED.content_hash, fetched_at = now()
            """,
            (doc.doc_id, doc.url, doc.title, doc.company, doc.published, len(doc.text), h),
        )
        conn.execute("DELETE FROM chunks WHERE doc_id = %s", (doc.doc_id,))
        with conn.cursor() as cur:
            cur.executemany(
                """
                INSERT INTO chunks (chunk_id, doc_id, ordinal, heading, text, n_tokens, embedding)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                [
                    (f"{doc.doc_id}-{c.ordinal}", doc.doc_id, c.ordinal, c.heading or None, c.text,
                     c.n_tokens, v)
                    for c, v in zip(chunks, vecs, strict=True)
                ],
            )
    if row:
        report.updated += 1
    else:
        report.inserted += 1


def ingest_docs(pool: ConnectionPool, docs, s: Settings, force: bool = False) -> IngestReport:
    report = IngestReport()
    dupes = DuplicateDetector(pool)
    for item in docs:
        report.seen += 1
        if isinstance(item, Skip | dict):
            report.skipped.append(item if isinstance(item, dict) else {"reason": str(item)})
            continue
        if (other := dupes.duplicate_of(item)) is not None:
            log.info("skip  %s  (near-duplicate of %s)", item.url, other)
            report.skipped.append({"url": item.url, "reason": f"near-duplicate of {other}"})
            continue
        upsert_document(pool, item, s, report, force=force)
        dupes.add(item)
    return report


def _fetch_all(urls: list[tuple[str, str | None]], s: Settings):
    fetcher = Fetcher(s.raw_dir, s.user_agent, s.fetch_interval_s)
    try:
        for i, (url, company) in enumerate(urls, start=1):
            try:
                doc = fetch_document(fetcher, url, company, s.min_doc_chars)
                log.info("[%d/%d] ok    %s", i, len(urls), url)
                yield doc
            except Skip as e:
                log.info("[%d/%d] skip  %s  (%s)", i, len(urls), url, e)
                yield {"url": url, "reason": str(e)}
            except Exception as e:  # never let one bad page kill the run
                log.warning("[%d/%d] error %s  (%s: %s)", i, len(urls), url, type(e).__name__, e)
                yield {"url": url, "reason": f"error: {type(e).__name__}: {e}"}
    finally:
        fetcher.close()


def run_ingest(
    s: Settings | None = None,
    urls_file: Path | None = None,
    fixtures: Path | None = None,
    limit: int | None = None,
    pool: ConnectionPool | None = None,
    force: bool = False,
) -> dict:
    from app.retrieval.pipeline import rebuild_bm25

    s = s or get_settings()
    pool = pool or get_pool()
    if fixtures:
        docs = [read_fixture(p) for p in sorted(fixtures.glob("*.md"))][:limit]
    else:
        urls = read_urls(urls_file or s.urls_file)[:limit]
        docs = _fetch_all(urls, s)
    report = ingest_docs(pool, docs, s, force=force)

    if report.skipped:
        skip_log = s.data_dir / "skipped.jsonl"
        skip_log.parent.mkdir(parents=True, exist_ok=True)
        skip_log.write_text("".join(json.dumps(x) + "\n" for x in report.skipped))
        log.info("skip reasons written to %s", skip_log)

    if report.inserted or report.updated or _bm25_missing(s):
        rebuild_bm25(pool, s)
        log.info("BM25 index rebuilt")
    with pool.connection() as conn:
        totals = counts(conn)
    return {**report.as_dict(), **{f"total_{k}": v for k, v in totals.items()}}


def _bm25_missing(s: Settings) -> bool:
    return not (s.bm25_dir / "chunk_ids.json").exists()


def stats(pool: ConnectionPool | None = None) -> dict:
    pool = pool or get_pool()
    with pool.connection() as conn:
        c = counts(conn)
        row = conn.execute(
            "SELECT avg(n_tokens)::int, min(n_tokens), max(n_tokens) FROM chunks"
        ).fetchone()
        n_companies = conn.execute("SELECT count(DISTINCT company) FROM documents").fetchone()[0]
    return {**c, "companies": n_companies, "avg_chunk_tokens": row[0], "min_chunk_tokens": row[1],
            "max_chunk_tokens": row[2]}


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    for noisy in ("httpx", "httpcore", "huggingface_hub", "sentence_transformers", "bm25s", "trafilatura"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    p = argparse.ArgumentParser(prog="python -m app.ingest.run")
    p.add_argument("command", nargs="?", default="ingest", choices=["ingest", "stats"])
    p.add_argument("--urls", type=Path, help="URL list (default: data/urls.txt)")
    p.add_argument("--fixtures", type=Path, help="ingest local markdown fixtures instead of URLs")
    p.add_argument("--limit", type=int)
    p.add_argument("--force", action="store_true", help="re-chunk and re-embed even if unchanged")
    args = p.parse_args(argv)

    if args.command == "stats":
        print(json.dumps(stats(), indent=2))
        return 0
    result = run_ingest(urls_file=args.urls, fixtures=args.fixtures, limit=args.limit, force=args.force)
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
