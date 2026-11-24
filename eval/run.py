"""Run the whole eval and write eval/results/<timestamp>.json + eval/results/latest.json.

    python -m eval.run                         # full corpus, golden.jsonl, retrieval + faithfulness
    python -m eval.run --retrieval-only        # no API key needed
    python -m eval.run --suite fixture --faith-limit 10   # what CI runs

Faithfulness is skipped (with a note in the output) when no Anthropic credentials exist.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from app.config import get_settings
from app.db import get_pool
from app.ingest.run import stats as corpus_stats
from app.retrieval.pipeline import Retriever
from eval import faithfulness_eval, retrieval_eval
from eval.golden import GOLDEN_FIXTURE, GOLDEN_FULL, derive_chunk_ids, load
from eval.golden import stats as golden_stats

RESULTS = Path(__file__).resolve().parent / "results"


def _git_sha() -> str | None:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True,
                              check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return None


def have_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.WARNING)
    p = argparse.ArgumentParser(prog="python -m eval.run")
    p.add_argument("--suite", choices=["full", "fixture"], default="full")
    p.add_argument("--golden", type=Path)
    p.add_argument("--retrieval-only", action="store_true")
    p.add_argument("--faith-limit", type=int, help="only run faithfulness on the first N questions")
    p.add_argument("--out", type=Path, default=RESULTS / "latest.json")
    args = p.parse_args(argv)

    s = get_settings()
    golden_path = args.golden or (GOLDEN_FULL if args.suite == "full" else GOLDEN_FIXTURE)
    items = load(golden_path)
    pool = get_pool()
    problems = derive_chunk_ids(pool, items)
    for prob in problems:
        print("golden warning:", prob)

    retriever = Retriever(pool)
    print(f"retrieval eval on {len(items)} questions ...")
    retrieval = retrieval_eval.evaluate(retriever, items)

    answer_eval, note = None, None
    if args.retrieval_only:
        note = "skipped (--retrieval-only)"
    elif not have_credentials():
        note = "skipped (no ANTHROPIC_API_KEY)"
    else:
        subset = items[: args.faith_limit] if args.faith_limit else items
        print(f"answer eval on {len(subset)} questions with {s.claude_model} (judge {s.judge_model}) ...")
        answer_eval = faithfulness_eval.evaluate(retriever, subset, s)

    result = {
        "suite": args.suite,
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "git_sha": _git_sha(),
        "golden_file": str(golden_path.relative_to(Path.cwd())) if golden_path.is_relative_to(Path.cwd()) else str(golden_path),
        "corpus": corpus_stats(pool),
        "golden": golden_stats(items),
        "golden_warnings": problems,
        "config": {
            "embed_model": s.embed_model, "rerank_model": s.rerank_model,
            "claude_model": s.claude_model, "judge_model": s.judge_model, "effort": s.effort,
            "top_k_bm25": s.top_k_bm25, "top_k_dense": s.top_k_dense, "fused_k": s.fused_k,
            "rerank_k": s.rerank_k, "rrf_k": s.rrf_k, "rerank_threshold": s.rerank_threshold,
            "max_chunks_per_doc": s.max_chunks_per_doc,
            "chunk_tokens": s.chunk_tokens, "chunk_overlap_tokens": s.chunk_overlap_tokens,
        },
        "retrieval": {k: v for k, v in retrieval.items() if k != "per_item"},
        "answers": answer_eval["summary"] if answer_eval else None,
        "answers_note": note,
    }
    # keep a previously recorded judge-human agreement, if any
    if args.out.exists():
        prev = json.loads(args.out.read_text())
        if prev.get("judge_agreement") and prev.get("suite") == args.suite:
            result["judge_agreement"] = prev["judge_agreement"]

    RESULTS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    detail = {**result, "retrieval_per_item": retrieval["per_item"],
              "answers_per_item": answer_eval["per_item"] if answer_eval else None}
    (RESULTS / f"{args.suite}-{stamp}.json").write_text(json.dumps(detail, indent=2))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result, indent=2) + "\n")

    print(json.dumps({"ablation": result["retrieval"]["ablation"], "answers": result["answers"],
                      "note": note}, indent=2))
    print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
