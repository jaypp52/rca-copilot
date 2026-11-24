"""Judge calibration: how often does the LLM judge agree with a human?

1. `python -m eval.judge_agreement sample`  -> writes eval/judge_calibration.jsonl with the
   judge's claim verdicts for 20 answers from the latest detailed full-suite run, each with
   `"human_supported": null`.
2. A human (Jay) reads each claim against the cited source and fills `human_supported`
   with true/false (and optionally `human_note`).
3. `python -m eval.judge_agreement score`   -> claim-level agreement + Cohen's kappa,
   stored under `judge_agreement` in eval/results/latest.json (shown in the README).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CAL_FILE = HERE / "judge_calibration.jsonl"
LATEST = HERE / "results" / "latest.json"


def latest_detail(suite: str = "full") -> Path:
    files = sorted((HERE / "results").glob(f"{suite}-*.json"))
    for f in reversed(files):
        if json.loads(f.read_text()).get("answers_per_item"):
            return f
    raise SystemExit(f"no detailed {suite} run with answers found; run `make eval` with an API key first")


def sample(n: int = 20, seed: int = 7) -> None:
    detail = json.loads(latest_detail().read_text())
    judged = [r for r in detail["answers_per_item"] if r.get("claims")]
    random.Random(seed).shuffle(judged)
    with CAL_FILE.open("w") as f:
        for r in judged[:n]:
            for i, c in enumerate(r["claims"]):
                f.write(json.dumps({
                    "item_id": r["id"], "claim_idx": i, "answer": r["answer"], "claim": c["text"],
                    "judge_supported": c["supported"], "judge_reason": c["reason"],
                    "cited_chunk_ids": r["cited_chunk_ids"], "human_supported": None,
                }) + "\n")
    print(f"wrote {CAL_FILE}: fill `human_supported` (true/false) for every line, then run `score`")


def cohen_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    n = len(pairs)
    if n == 0:
        return None
    po = sum(a == b for a, b in pairs) / n
    pa = sum(a for a, _ in pairs) / n
    pb = sum(b for _, b in pairs) / n
    pe = pa * pb + (1 - pa) * (1 - pb)
    return None if pe == 1 else (po - pe) / (1 - pe)


def score() -> int:
    rows = [json.loads(line) for line in CAL_FILE.read_text().splitlines() if line.strip()]
    labelled = [r for r in rows if r["human_supported"] is not None]
    if not labelled:
        print("no human labels yet")
        return 1
    pairs = [(bool(r["judge_supported"]), bool(r["human_supported"])) for r in labelled]
    kappa = cohen_kappa(pairs)
    out = {
        "answers": len({r["item_id"] for r in labelled}),
        "claims": len(labelled),
        "agreement": round(sum(a == b for a, b in pairs) / len(pairs), 4),
        "cohen_kappa": round(kappa, 4) if kappa is not None else None,
        "unlabelled_claims": len(rows) - len(labelled),
    }
    print(json.dumps(out, indent=2))
    latest = json.loads(LATEST.read_text())
    latest["judge_agreement"] = out
    LATEST.write_text(json.dumps(latest, indent=2) + "\n")
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("command", choices=["sample", "score"])
    args = p.parse_args()
    if args.command == "sample":
        sample()
        return 0
    return score()


if __name__ == "__main__":
    sys.exit(main())
