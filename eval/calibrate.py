"""Calibrate RERANK_THRESHOLD (the "not in corpus" cut-off) on the golden set.

For every question we take the best cross-encoder score after reranking. The
threshold is a cheap pre-filter for *clearly off-topic* questions: we choose the
highest threshold that still keeps at least `min_keep` of the answerable questions
(default: all of them), placed halfway into the score gap below them. "Near-miss"
unanswerable questions (right company, missing detail) score like answerable ones;
the threshold can't catch those, so Claude's own abstention instruction does.

    python -m eval.calibrate [--golden eval/golden.jsonl] [--min-keep 1.0]
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

from eval.golden import GOLDEN_FULL, load


def choose_threshold(scores: list[tuple[bool, float]], min_keep: float = 1.0) -> dict:
    answerable = sorted(s for a, s in scores if a)
    unanswerable = sorted(s for a, s in scores if not a)
    if not answerable or not unanswerable:
        raise ValueError("need both answerable and unanswerable questions")
    # lowest answerable score we must keep
    n_drop = math.floor((1 - min_keep) * len(answerable) + 1e-9)
    floor_score = answerable[n_drop]
    below = [s for _, s in scores if s < floor_score]
    thr = (max(below) + floor_score) / 2 if below else floor_score - 0.5
    tpr = sum(s >= thr for s in answerable) / len(answerable)
    tnr = sum(s < thr for s in unanswerable) / len(unanswerable)
    return {"threshold": round(thr, 3), "answerable_kept": round(tpr, 3),
            "unanswerable_rejected": round(tnr, 3), "balanced_accuracy": round((tpr + tnr) / 2, 3)}


def main() -> int:
    from app.db import get_pool
    from app.retrieval.pipeline import Retriever

    p = argparse.ArgumentParser()
    p.add_argument("--golden", type=Path, default=GOLDEN_FULL)
    p.add_argument("--min-keep", type=float, default=1.0)
    args = p.parse_args()
    retriever = Retriever(get_pool())
    scores = []
    for it in load(args.golden):
        top = retriever.retrieve_all_modes(it.question, depth=1)["hybrid_rerank"]
        scores.append((it.answerable, top[0].rerank_score if top else float("-inf")))
        print(f"{it.id:8s} answerable={it.answerable!s:5s} best_rerank={scores[-1][1]:7.3f}  {it.question[:70]}")
    best = choose_threshold(scores, args.min_keep)
    print(json.dumps(best, indent=2))
    print(f"-> set RERANK_THRESHOLD={best['threshold']} (in .env / app/config.py)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
