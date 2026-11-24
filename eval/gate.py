"""CI regression gate: compare a results file against eval/baseline.json.

Fails (exit 1) if, for the results' suite:
  - Recall@5 of any retrieval mode (bm25, dense, hybrid, hybrid_rerank) is more than
    2 points below baseline. Every mode is gated, not just the headline: on a small
    corpus the reranker sees every chunk and can mask a broken fusion step.
  - faithfulness is more than 3 points below baseline (only when both are present).

    python -m eval.gate eval/results/latest.json
    python -m eval.gate eval/results/latest.json --update   # record current numbers as the baseline
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

BASELINE = Path(__file__).resolve().parent / "baseline.json"
MAX_RECALL_DROP = 0.02
MAX_FAITH_DROP = 0.03


def current_metrics(result: dict) -> dict:
    ablation = result["retrieval"]["ablation"]
    answers = result.get("answers") or {}
    return {"recall@5": {mode: m["recall@5"] for mode, m in ablation.items()},
            "faithfulness": answers.get("faithfulness")}


def check(result: dict, baseline: dict) -> list[str]:
    suite = result["suite"]
    base = baseline.get(suite)
    if not base:
        return [f"no baseline recorded for suite {suite!r}"]
    cur = current_metrics(result)
    failures = []
    for mode, base_r5 in base["recall@5"].items():
        r5 = cur["recall@5"].get(mode)
        if r5 is None or r5 < base_r5 - MAX_RECALL_DROP:
            failures.append(f"Recall@5[{mode}] {r5} < baseline {base_r5:.3f} - {MAX_RECALL_DROP}")
    if (
        cur["faithfulness"] is not None
        and base.get("faithfulness") is not None
        and cur["faithfulness"] < base["faithfulness"] - MAX_FAITH_DROP
    ):
        failures.append(
            f"faithfulness {cur['faithfulness']:.3f} < baseline {base['faithfulness']:.3f} - {MAX_FAITH_DROP}"
        )
    return failures


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("results", type=Path)
    p.add_argument("--update", action="store_true")
    args = p.parse_args()

    result = json.loads(args.results.read_text())
    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else {}
    cur = current_metrics(result)

    if args.update:
        entry = baseline.get(result["suite"], {})
        entry["recall@5"] = cur["recall@5"]
        if cur["faithfulness"] is not None:
            entry["faithfulness"] = cur["faithfulness"]
        entry.setdefault("faithfulness", None)
        entry["config"] = result.get("config")
        baseline[result["suite"]] = entry
        BASELINE.write_text(json.dumps(baseline, indent=2) + "\n")
        print(f"baseline for {result['suite']!r} updated: {entry}")
        return 0

    base = baseline.get(result["suite"], {})
    print(f"suite={result['suite']}  current={cur}  baseline={{'recall@5': {base.get('recall@5')}, "
          f"'faithfulness': {base.get('faithfulness')}}}")
    if cur["faithfulness"] is None or base.get("faithfulness") is None:
        print("note: faithfulness not gated in this run (no answer eval or no faithfulness baseline)")
    failures = check(result, baseline)
    for f in failures:
        print("REGRESSION:", f)
    if failures:
        return 1
    print("gate passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
