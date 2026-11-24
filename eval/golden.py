"""Golden-set loading and chunk-label derivation.

Each item carries `evidence`: short verbatim phrases from the relevant postmortem(s).
`relevant_chunk_ids` are derived from them (the chunks that contain an evidence
phrase), which makes chunk labels reproducible after re-chunking and easy to audit.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from psycopg_pool import ConnectionPool

ROOT = Path(__file__).resolve().parent.parent
GOLDEN_FULL = ROOT / "eval" / "golden.jsonl"
GOLDEN_FIXTURE = ROOT / "tests" / "fixtures" / "golden.jsonl"


@dataclass
class GoldenItem:
    id: str
    question: str
    relevant_doc_ids: list[str]
    answerable: bool
    reference_answer: str
    relevant_chunk_ids: list[str] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    verified_by: str | None = None
    kind: str | None = None  # factual | cross_document | paraphrase | unanswerable
    drafted_by: str | None = None

    @classmethod
    def from_dict(cls, d: dict) -> GoldenItem:
        known = cls.__dataclass_fields__.keys()
        return cls(**{k: v for k, v in d.items() if k in known})


def load(path: Path) -> list[GoldenItem]:
    items = [GoldenItem.from_dict(json.loads(line)) for line in path.read_text().splitlines() if line.strip()]
    ids = [i.id for i in items]
    if len(ids) != len(set(ids)):
        raise ValueError(f"duplicate ids in {path}")
    for it in items:
        if it.answerable and not it.relevant_doc_ids:
            raise ValueError(f"{it.id}: answerable item without relevant_doc_ids")
        if not it.answerable and it.relevant_doc_ids:
            raise ValueError(f"{it.id}: unanswerable item with relevant_doc_ids")
    return items


def save(path: Path, items: list[GoldenItem]) -> None:
    path.write_text("".join(json.dumps(it.__dict__, ensure_ascii=False) + "\n" for it in items))


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("“", '"').replace("”", '"')).strip().lower()


def derive_chunk_ids(pool: ConnectionPool, items: list[GoldenItem]) -> list[str]:
    """Fill relevant_chunk_ids from evidence phrases. Returns a list of problems found."""
    problems = []
    with pool.connection() as conn:
        for it in items:
            if not it.answerable:
                it.relevant_chunk_ids = []
                continue
            rows = conn.execute(
                "SELECT chunk_id, doc_id, text FROM chunks WHERE doc_id = ANY(%s) ORDER BY doc_id, ordinal",
                (it.relevant_doc_ids,),
            ).fetchall()
            if not rows:
                problems.append(f"{it.id}: none of {it.relevant_doc_ids} is in the database")
                continue
            found: list[str] = []
            for ev in it.evidence:
                hits = [cid for cid, _, text in rows if _norm(ev) in _norm(text)]
                if not hits:
                    problems.append(f"{it.id}: evidence not found in relevant docs: {ev!r}")
                for h in hits:
                    if h not in found:
                        found.append(h)
            it.relevant_chunk_ids = found
    return problems


def stats(items: list[GoldenItem]) -> dict:
    return {
        "questions": len(items),
        "answerable": sum(i.answerable for i in items),
        "unanswerable": sum(not i.answerable for i in items),
        "cross_document": sum(len(i.relevant_doc_ids) > 1 for i in items),
        "verified": sum(bool(i.verified_by) and i.verified_by not in ("", "pending") for i in items),
    }
