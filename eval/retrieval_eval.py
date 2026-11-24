"""Retrieval eval: Recall@5, Recall@10 (doc level), MRR@10 and Evidence Recall@5, for every
ablation mode (bm25, dense, hybrid, hybrid_rerank). No LLM, no API key.

Evidence Recall@k: per relevant document, a hit only if one of the retrieved chunks is a
chunk that contains the labelled evidence (not just any chunk of that document). This is
what grounding needs: the passage with the answer must reach the model.

    python -m eval.retrieval_eval --golden eval/golden.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from app.retrieval.pipeline import MODES, RetrievedChunk, Retriever
from eval.golden import GOLDEN_FULL, GoldenItem, load


def recall_at(ranked: list[RetrievedChunk], relevant: set[str], k: int) -> float:
    docs = {c.doc_id for c in ranked[:k]}
    return len(docs & relevant) / len(relevant)


def mrr_at(ranked: list[RetrievedChunk], relevant: set[str], k: int = 10) -> float:
    for i, c in enumerate(ranked[:k], start=1):
        if c.doc_id in relevant:
            return 1.0 / i
    return 0.0


def evidence_recall_at(ranked: list[RetrievedChunk], relevant_chunks: set[str], k: int) -> float | None:
    """Fraction of relevant docs for which an evidence-bearing chunk is in the top k."""
    if not relevant_chunks:
        return None
    by_doc: dict[str, set[str]] = {}
    for cid in relevant_chunks:
        by_doc.setdefault(cid.rsplit("-", 1)[0], set()).add(cid)
    got = {c.chunk_id for c in ranked[:k]}
    return sum(bool(ids & got) for ids in by_doc.values()) / len(by_doc)


def _mean(xs: list[float | None]) -> float | None:
    vals = [x for x in xs if x is not None]
    return round(statistics.fmean(vals), 4) if vals else None


def evaluate(retriever: Retriever, items: list[GoldenItem]) -> dict:
    per_mode: dict[str, dict[str, list]] = {
        m: {"recall@5": [], "recall@10": [], "mrr@10": [], "evidence_recall@5": []} for m in MODES
    }
    per_item = []
    best_scores = []  # (answerable, best rerank score) for threshold calibration
    for it in items:
        runs = retriever.retrieve_all_modes(it.question, depth=10)
        top = runs["hybrid_rerank"]
        best_scores.append({"id": it.id, "answerable": it.answerable,
                            "best_rerank": top[0].rerank_score if top else None})
        if not it.answerable:
            continue
        rel = set(it.relevant_doc_ids)
        rel_chunks = set(it.relevant_chunk_ids)
        row = {"id": it.id}
        for mode in MODES:
            ranked = runs[mode]
            m = per_mode[mode]
            m["recall@5"].append(recall_at(ranked, rel, 5))
            m["recall@10"].append(recall_at(ranked, rel, 10))
            m["mrr@10"].append(mrr_at(ranked, rel, 10))
            m["evidence_recall@5"].append(evidence_recall_at(ranked, rel_chunks, 5))
            row[mode] = {"recall@5": m["recall@5"][-1], "top_docs": [c.doc_id for c in ranked[:5]]}
        per_item.append(row)

    ablation = {mode: {metric: _mean(vals) for metric, vals in metrics.items()}
                for mode, metrics in per_mode.items()}
    return {
        "n_answerable": sum(it.answerable for it in items),
        "ablation": ablation,
        "headline": ablation["hybrid_rerank"],
        "best_rerank_scores": best_scores,
        "per_item": per_item,
    }


def main() -> int:
    from app.db import get_pool

    p = argparse.ArgumentParser()
    p.add_argument("--golden", type=Path, default=GOLDEN_FULL)
    args = p.parse_args()
    res = evaluate(Retriever(get_pool()), load(args.golden))
    print(json.dumps(res["ablation"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
