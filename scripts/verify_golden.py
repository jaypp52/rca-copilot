"""Walk through eval/golden.jsonl and confirm each item by hand.

For every unverified item it prints the question, the reference answer, the evidence
phrases and the text of the chunks that contain them, then asks:

    [y] correct  [n] reject (removed)  [e] edit reference answer  [s] skip  [q] quit

Confirmed items get `"verified_by": "<your name>"`. Progress is saved after every item.

    python scripts/verify_golden.py [--who jay] [--golden eval/golden.jsonl]
"""

from __future__ import annotations

import argparse
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_pool  # noqa: E402
from eval.golden import GOLDEN_FULL, derive_chunk_ids, load, save  # noqa: E402


def show_chunks(pool, chunk_ids: list[str]) -> None:
    if not chunk_ids:
        return
    with pool.connection() as conn:
        rows = conn.execute(
            "SELECT c.chunk_id, d.title, d.url, c.text FROM chunks c JOIN documents d USING (doc_id) "
            "WHERE chunk_id = ANY(%s)", (chunk_ids,)
        ).fetchall()
    for cid, title, url, text in rows:
        print(f"\n  --- {cid} | {title}\n  {url}")
        print(textwrap.indent(textwrap.fill(text, 100), "  | "))


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--golden", type=Path, default=GOLDEN_FULL)
    p.add_argument("--who", default="jay")
    args = p.parse_args()

    items = load(args.golden)
    pool = get_pool()
    for prob in derive_chunk_ids(pool, items):
        print("warning:", prob)

    todo = [it for it in items if not it.verified_by or it.verified_by == "pending"]
    print(f"{len(todo)} of {len(items)} items need verification\n")
    for n, it in enumerate(todo, 1):
        print("=" * 100)
        print(f"[{n}/{len(todo)}] {it.id} ({it.kind}, answerable={it.answerable})")
        print(f"Q: {it.question}")
        print(f"Reference: {it.reference_answer}")
        print(f"Evidence: {it.evidence}")
        show_chunks(pool, it.relevant_chunk_ids)
        while True:
            choice = input("\n[y]es / [n]o-remove / [e]dit answer / [s]kip / [q]uit > ").strip().lower()
            if choice in {"y", "n", "e", "s", "q"}:
                break
        if choice == "q":
            break
        if choice == "y":
            it.verified_by = args.who
        elif choice == "n":
            items.remove(it)
        elif choice == "e":
            it.reference_answer = input("new reference answer: ").strip() or it.reference_answer
            it.verified_by = args.who
        save(args.golden, items)
    remaining = sum(1 for it in items if not it.verified_by or it.verified_by == "pending")
    print(f"\nsaved {args.golden}; {remaining} items still unverified")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
