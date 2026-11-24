"""Real BM25 (Okapi, via `bm25s`) over chunk text, persisted to disk.

Postgres `ts_rank` is not BM25 (no IDF saturation / length normalisation), so the
lexical side lives here. Tokenisation: lowercase, strip punctuation, drop English stopwords.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import bm25s
from bm25s.stopwords import STOPWORDS_EN

TOKEN_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(STOPWORDS_EN)


def tokenize(text: str) -> list[str]:
    return [t for t in TOKEN_RE.findall(text.lower()) if t not in STOPWORDS]


class BM25Index:
    def __init__(self, retriever: bm25s.BM25, chunk_ids: list[str]):
        self.retriever = retriever
        self.chunk_ids = chunk_ids

    @classmethod
    def build(cls, chunk_ids: list[str], texts: list[str]) -> BM25Index:
        if not chunk_ids:
            raise ValueError("cannot build a BM25 index over zero chunks")
        retriever = bm25s.BM25()  # Lucene variant, k1=1.5, b=0.75
        retriever.index([tokenize(t) for t in texts], show_progress=False)
        return cls(retriever, list(chunk_ids))

    def search(self, query: str, k: int) -> list[tuple[str, float]]:
        tokens = tokenize(query)
        if not tokens:
            return []
        k = min(k, len(self.chunk_ids))
        docs, scores = self.retriever.retrieve([tokens], k=k, show_progress=False)
        out = []
        for idx, score in zip(docs[0], scores[0], strict=True):
            if score <= 0:  # no query term matched
                continue
            out.append((self.chunk_ids[int(idx)], float(score)))
        return out

    # -- persistence --------------------------------------------------------
    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.retriever.save(str(directory))
        (directory / "chunk_ids.json").write_text(json.dumps(self.chunk_ids))

    @classmethod
    def load(cls, directory: Path) -> BM25Index:
        retriever = bm25s.BM25.load(str(directory))
        chunk_ids = json.loads((directory / "chunk_ids.json").read_text())
        return cls(retriever, chunk_ids)

    @staticmethod
    def mtime(directory: Path) -> float | None:
        p = directory / "chunk_ids.json"
        return p.stat().st_mtime if p.exists() else None
