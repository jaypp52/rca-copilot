"""Download + clean public postmortems.

Polite by construction: one request per second, a descriptive User-Agent, robots.txt
respected, and raw HTML cached under data/raw/ (git-ignored) so re-runs never refetch.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.robotparser
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

import httpx
import trafilatura

log = logging.getLogger(__name__)


def doc_id_for(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:12]


def content_hash(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


@dataclass
class FetchedDoc:
    doc_id: str
    url: str
    title: str
    company: str | None
    published: date | None
    text: str  # cleaned markdown


class Skip(Exception):
    """Raised when a URL should not be ingested; the message is the logged reason."""


class Fetcher:
    def __init__(self, raw_dir: Path, user_agent: str, interval_s: float = 1.0, timeout_s: float = 20.0):
        self.raw_dir = raw_dir
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        self.user_agent = user_agent
        self.interval_s = interval_s
        self._last_request = 0.0
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._client = httpx.Client(
            headers={"User-Agent": user_agent},
            follow_redirects=True,
            timeout=timeout_s,
        )

    # -- politeness ---------------------------------------------------------
    def _throttle(self) -> None:
        wait = self.interval_s - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _allowed(self, url: str) -> bool:
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._robots:
            rp = urllib.robotparser.RobotFileParser()
            try:
                self._throttle()
                resp = self._client.get(f"{origin}/robots.txt")
                if resp.status_code >= 400:
                    rp = None  # no robots.txt => allowed
                else:
                    rp.parse(resp.text.splitlines())
            except httpx.HTTPError:
                rp = None
            self._robots[origin] = rp
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(self.user_agent, url)

    # -- cache --------------------------------------------------------------
    def _paths(self, doc_id: str) -> tuple[Path, Path]:
        return self.raw_dir / f"{doc_id}.html", self.raw_dir / f"{doc_id}.json"

    def get_html(self, url: str) -> str:
        doc_id = doc_id_for(url)
        html_path, meta_path = self._paths(doc_id)
        if meta_path.exists():
            meta = json.loads(meta_path.read_text())
            if meta.get("skip"):
                raise Skip(meta["skip"])
            return html_path.read_text(encoding="utf-8", errors="replace")

        def remember_skip(reason: str) -> Skip:
            meta_path.write_text(json.dumps({"url": url, "skip": reason}))
            return Skip(reason)

        if not self._allowed(url):
            raise remember_skip("disallowed by robots.txt")
        self._throttle()
        try:
            resp = self._client.get(url)
        except httpx.HTTPError as e:
            # network errors are not cached: they may be transient
            raise Skip(f"network error: {type(e).__name__}") from e
        if resp.status_code >= 400:
            raise remember_skip(f"HTTP {resp.status_code}")
        ctype = resp.headers.get("content-type", "")
        if "html" not in ctype and "text" not in ctype:
            raise remember_skip(f"unsupported content-type {ctype!r}")
        html = resp.text
        final_url = str(resp.url)
        target = meta_refresh_target(html)
        if target and target != url:  # client-side redirect stub (static-site hosts do this)
            if not self._allowed(target):
                raise remember_skip("redirect target disallowed by robots.txt")
            self._throttle()
            try:
                resp = self._client.get(target)
            except httpx.HTTPError as e:
                raise Skip(f"network error: {type(e).__name__}") from e
            if resp.status_code >= 400:
                raise remember_skip(f"HTTP {resp.status_code} after meta refresh")
            html, final_url = resp.text, str(resp.url)
        html_path.write_text(html, encoding="utf-8")
        meta_path.write_text(json.dumps({"url": url, "final_url": final_url, "status": resp.status_code}))
        return html

    def close(self) -> None:
        self._client.close()


META_REFRESH_RE = re.compile(
    r"""<meta[^>]+http-equiv=["']?refresh["']?[^>]+content=["']?\d+\s*;\s*url=([^"'>\s]+)""", re.I
)
BOILERPLATE_RE = re.compile(
    r"did you find what you were looking for|subscribe to|sign up for our|share this (post|article)"
    r"|related (posts|articles)|leave a (comment|reply)|all rights reserved|we use cookies|cookie (policy|settings)"
    r"|follow us on|posted by|written by|^tags?:|^categories:",
    re.I,
)


def meta_refresh_target(html: str) -> str | None:
    if len(html) > 20_000:  # real pages that merely contain a refresh tag aren't stubs
        return None
    m = META_REFRESH_RE.search(html)
    return m.group(1) if m else None


def _mostly_non_latin(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    if len(letters) < 20:
        return False
    return sum(1 for ch in letters if ch.isascii()) / len(letters) < 0.6


def postprocess(markdown: str) -> str:
    """Drop non-English paragraphs (bilingual pages) and short boilerplate lines."""
    kept = []
    for block in re.split(r"\n\s*\n", markdown):
        b = block.strip()
        if not b or _mostly_non_latin(b):
            continue
        if len(b) < 200 and BOILERPLATE_RE.search(b):
            continue
        kept.append(b)
    return "\n\n".join(kept)


def clean_html(html: str, url: str) -> tuple[str, str | None, date | None]:
    """Return (markdown text, title, published date). Headings survive as `#` lines."""
    text = trafilatura.extract(
        html,
        url=url,
        output_format="markdown",
        include_comments=False,
        include_tables=True,
        include_links=False,
        include_images=False,
        favor_recall=True,
    )
    meta = trafilatura.extract_metadata(html, default_url=url)
    title = meta.title if meta and meta.title else None
    published = None
    if meta and meta.date:
        try:
            published = date.fromisoformat(meta.date[:10])
        except ValueError:
            published = None
    return postprocess(text or ""), title, published


def fetch_document(fetcher: Fetcher, url: str, company: str | None, min_chars: int) -> FetchedDoc:
    html = fetcher.get_html(url)
    text, title, published = clean_html(html, url)
    if len(text) < min_chars:
        raise Skip(f"too short after cleaning ({len(text)} chars < {min_chars})")
    return FetchedDoc(
        doc_id=doc_id_for(url),
        url=url,
        title=(title or company or url)[:300],
        company=company,
        published=published,
        text=text,
    )
