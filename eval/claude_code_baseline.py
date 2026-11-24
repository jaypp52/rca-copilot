"""Score a Claude Code baseline of the answer side (no API key needed).

The API pipeline (app/service.py) has not run yet. As a baseline, each golden question's
context was built with the REAL retriever (`Retriever.search`, the same top-6 chunks and
not-found threshold `ask()` uses). Claude answered through Claude Code sessions with
RCA's own system prompt, writing every sentence with the document numbers it relies on.
A separate Claude Code judge session, with this repo's JUDGE_SYSTEM prompt, split each
answer into atomic claims and checked each against the cited chunks. Metrics are computed
with this repo's code: citation coverage from app.generation.cite_check, abstention from
the same rules as eval/faithfulness_eval.py.

    python -m eval.claude_code_baseline --ctx <dir> --run haiku=<dir> --run sonnet=<dir> ... [--readme]

<dir> holds out/*.jsonl (answers) and judge/*.jsonl (judgements). Only summary metrics
are committed (eval/results/claude_code_baseline.json/.md); answers and judgements quote
third-party postmortems and stay local.
"""

from __future__ import annotations

import argparse
import json
import re
import statistics
from datetime import UTC, datetime
from pathlib import Path

from app.generation.cite_check import Segment, check, is_abstention
from eval.golden import GOLDEN_FULL, load

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "eval" / "results"
README = ROOT / "README.md"
START, END = "<!-- baseline:start -->", "<!-- baseline:end -->"


def _mean(xs):
    return round(statistics.fmean(xs), 4) if xs else None


def answer_text(rec: dict) -> str:
    """The answer with [n] markers, as the API pipeline would render it."""
    parts = []
    for s in rec["sentences"]:
        marks = "".join(f"[{n}]" for n in sorted(set(s.get("cites", []))))
        parts.append(s["text"].rstrip() + marks)
    return " ".join(parts)


def read_jsonl(paths) -> dict:
    out = {}
    for f in paths:
        for line in Path(f).read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                out[r["id"]] = r
    return out


def score(items, ctx_dir: Path, run_dir: Path) -> dict:
    answers = read_jsonl(sorted((run_dir / "out").glob("*.jsonl")))
    judged = read_jsonl(sorted((run_dir / "judge").glob("*.jsonl"))) if (run_dir / "judge").exists() else {}
    rows = []
    for it in items:
        ctx = json.loads((ctx_dir / f"{it.id}.json").read_text())
        a = answers.get(it.id)
        row = {"id": it.id, "answerable": it.answerable, "submitted": a is not None}
        if a is None:
            rows.append(row)
            continue
        text = answer_text(a)
        cited = sorted({n for s in a["sentences"] for n in s.get("cites", [])})
        segs = [Segment(s["text"].rstrip() + " ", bool(s.get("cites"))) for s in a["sentences"]]
        rep = check(segs)
        abstained = bool(a.get("not_found")) or (not cited and is_abstention(text)) or not ctx["found"]
        row.update(abstained=abstained, citation_coverage=rep.coverage, n_cited_docs=len(cited),
                   retrieved_found=ctx["found"])
        j = judged.get(it.id)
        if j and cited and not abstained:
            claims = j.get("claims", [])
            row["n_claims"] = len(claims)
            row["supported"] = sum(bool(c.get("supported")) for c in claims)
            row["faithfulness"] = row["supported"] / len(claims) if claims else None
        rows.append(row)
    ok = [r for r in rows if r["submitted"]]
    ans = [r for r in ok if r["answerable"]]
    una = [r for r in ok if not r["answerable"]]
    faith = [r["faithfulness"] for r in ok if r.get("faithfulness") is not None]
    claims = sum(r.get("n_claims", 0) for r in ok)
    return {
        "n_questions": len(items), "n_submitted": len(ok),
        "n_answerable": len(ans), "n_unanswerable": len(una),
        "faithfulness": _mean(faith),
        "faithfulness_micro": round(sum(r.get("supported", 0) for r in ok) / claims, 4) if claims else None,
        "n_judged_answers": len(faith), "n_claims": claims,
        "citation_coverage": _mean([r["citation_coverage"] for r in ans
                                    if r.get("citation_coverage") is not None and not r["abstained"]]),
        "abstention_accuracy": _mean([1.0 if r["abstained"] else 0.0 for r in una]),
        "false_abstention_rate": _mean([1.0 if r["abstained"] else 0.0 for r in ans]),
    }


def _p(v):
    return "n/a" if v is None else f"{100 * v:.1f}%"


def markdown(rep: dict) -> str:
    runs = rep["runs"]
    names = list(runs)
    rows = [("Faithfulness (mean per answer)", "faithfulness", _p),
            ("Faithfulness (all claims pooled)", "faithfulness_micro", _p),
            ("Judged answers / claims", None, lambda s: f"{s['n_judged_answers']} / {s['n_claims']}"),
            ("Citation coverage (answerable)", "citation_coverage", _p),
            ("Abstention accuracy (unanswerable)", "abstention_accuracy", _p),
            ("False-abstention rate (answerable)", "false_abstention_rate", _p)]
    out = [f"Generated {rep['generated_at']} by `python -m eval.claude_code_baseline` · "
           f"{rep['n_questions']} golden questions ({rep['n_answerable']} answerable / "
           f"{rep['n_unanswerable']} unanswerable) · contexts from the real retriever · judge: "
           f"Claude Code · {rep['judge']} with this repo's JUDGE_SYSTEM. **Answers written by "
           "Claude through Claude Code sessions with RCA's system prompt, not by the API "
           "pipeline** (no token, cost or latency figures).", "",
           "| Metric | " + " | ".join(f"Claude Code · {n}" for n in names) + " |",
           "|---|" + "---|" * len(names)]
    for label, key, fmt in rows:
        out.append(f"| {label} | " + " | ".join(
            (fmt(runs[n][key]) if key else fmt(runs[n])) for n in names) + " |")
    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ctx", required=True)
    ap.add_argument("--run", action="append", required=True, help="name=dir")
    ap.add_argument("--judge", default="sonnet")
    ap.add_argument("--readme", action="store_true")
    a = ap.parse_args()
    items = load(GOLDEN_FULL)
    runs = {}
    for spec in a.run:
        name, d = spec.split("=", 1)
        runs[name] = score(items, Path(a.ctx), Path(d))
        print(name, runs[name])
    first = next(iter(runs.values()))
    rep = {"generated_at": datetime.now(UTC).isoformat(timespec="seconds"), "kind": "claude-code-baseline",
           "n_questions": first["n_questions"], "n_answerable": first["n_answerable"],
           "n_unanswerable": first["n_unanswerable"], "judge": a.judge,
           "verified_by_human": sum(it.verified_by not in ("", "pending", None) for it in items)
           if hasattr(items[0], "verified_by") else None, "runs": runs}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "claude_code_baseline.json").write_text(json.dumps(rep, indent=1) + "\n")
    md = markdown(rep)
    (RESULTS / "claude_code_baseline.md").write_text("# Claude Code baseline (answer side)\n\n" + md)
    print("wrote eval/results/claude_code_baseline.json and .md")
    if a.readme and START in README.read_text():
        README.write_text(re.sub(re.escape(START) + r".*?" + re.escape(END),
                                 lambda _: f"{START}\n{md}{END}", README.read_text(), flags=re.DOTALL))
        print("updated README")


if __name__ == "__main__":
    main()
