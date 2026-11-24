"""Regenerate data/urls.txt from the public list at github.com/danluu/post-mortems.

Output format (tab-separated): url, company, category. Only the URL list is committed;
the postmortem text itself is fetched at ingest time and never committed.

    python scripts/build_urls.py [--readme path/to/README.md]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import httpx

SOURCE = "https://raw.githubusercontent.com/danluu/post-mortems/master/README.md"
SKIP_SECTIONS = {"table of contents", "other lists of postmortems", "analysis", "contributors"}
LINK_AT_START = re.compile(r"^\[([^\]]+)\]\((https?://[^)\s]+)\)")
# Extra well-known public postmortems not (yet) in the danluu list.
# (Ingestion also drops near-duplicates, e.g. an archive.org copy of a page listed twice.)
EXTRA = [
    ("https://aws.amazon.com/message/101925/", "Amazon", "Uncategorized"),
    ("https://aws.amazon.com/message/12721/", "Amazon", "Uncategorized"),
    ("https://aws.amazon.com/message/41926/", "Amazon", "Config Errors"),
    ("https://slack.engineering/slacks-outage-on-january-4th-2021/", "Slack", "Uncategorized"),
    ("https://engineering.fb.com/2021/10/05/networking-traffic/outage-details/", "Facebook", "Config Errors"),
    ("https://www.fastly.com/blog/summary-of-june-8-outage", "Fastly", "Config Errors"),
    ("https://status.cloud.google.com/incident/cloud-networking/19009", "Google", "Config Errors"),
    ("https://blog.roblox.com/2022/01/roblox-return-to-service-10-28-10-31-2021/", "Roblox", "Database"),
    ("https://www.atlassian.com/engineering/post-incident-review-april-2022-outage", "Atlassian", "Uncategorized"),
    ("https://discord.com/blog/authentication-outage", "Discord", "Uncategorized"),
    ("https://blog.heroku.com/incident-review-api-outage", "Heroku", "Uncategorized"),
]


def parse(readme: str) -> list[tuple[str, str, str]]:
    out, section = [], ""
    for line in readme.splitlines():
        if line.startswith("## "):
            section = line[3:].strip()
            continue
        if section.lower() in SKIP_SECTIONS:
            continue
        m = LINK_AT_START.match(line.strip())
        if m:
            company = re.sub(r"\s+", " ", m.group(1)).strip()
            out.append((m.group(2), company, section))
    return out


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--readme", type=Path, help="local copy of the danluu README")
    p.add_argument("--out", type=Path, default=Path(__file__).resolve().parent.parent / "data" / "urls.txt")
    args = p.parse_args()

    text = args.readme.read_text() if args.readme else httpx.get(SOURCE, timeout=30).text
    rows = parse(text) + EXTRA
    seen, unique = set(), []
    for url, company, cat in rows:
        if url in seen:
            continue
        seen.add(url)
        unique.append((url, company, cat))

    header = (
        "# Public incident postmortems. Source: github.com/danluu/post-mortems (+ a few extras).\n"
        "# Regenerate with: python scripts/build_urls.py\n"
        "# url\tcompany\tcategory\n"
    )
    args.out.write_text(header + "".join(f"{u}\t{c}\t{k}\n" for u, c, k in unique))
    print(f"wrote {len(unique)} urls to {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
