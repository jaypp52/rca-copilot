"""Heading-aware chunker.

1. Split the (markdown) document into sections at heading lines.
2. Fold sections that are too small into the next one, so we don't index 20-word stubs.
3. Inside each section, pack paragraphs up to ~`max_tokens`, carrying the last
   ~`overlap_tokens` of the previous chunk into the next one (overlap never crosses
   a section boundary).

Token counts are approximate (1 token ~= 0.75 words): exact counts don't matter here
and this avoids pulling in a second tokenizer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.+?)\s*#*\s*$")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def n_tokens(text: str) -> int:
    """Approximate token count from words (1 token ~= 0.75 words)."""
    return round(len(text.split()) / 0.75)


def words_for_tokens(tokens: int) -> int:
    return max(1, round(tokens * 0.75))


@dataclass(frozen=True)
class Chunk:
    ordinal: int
    heading: str
    text: str
    n_tokens: int


@dataclass
class _Section:
    heading: str
    paragraphs: list[str]

    @property
    def tokens(self) -> int:
        return sum(n_tokens(p) for p in self.paragraphs)


def split_sections(markdown: str) -> list[_Section]:
    sections: list[_Section] = []
    current = _Section(heading="", paragraphs=[])
    para: list[str] = []

    def flush_para() -> None:
        if para:
            text = " ".join(line.strip() for line in para).strip()
            if text:
                current.paragraphs.append(text)
            para.clear()

    for line in markdown.splitlines():
        m = HEADING_RE.match(line)
        if m:
            flush_para()
            if current.paragraphs or current.heading:
                sections.append(current)
            current = _Section(heading=_clean_heading(m.group(2)), paragraphs=[])
        elif not line.strip():
            flush_para()
        elif re.match(r"^\s*([-*+]|\d+[.)])\s+", line):
            # list items: keep each item as its own "paragraph" so packing can break between them
            flush_para()
            para.append(line)
            flush_para()
        else:
            para.append(line)
    flush_para()
    if current.paragraphs or current.heading:
        sections.append(current)
    return [s for s in sections if s.paragraphs]


def _clean_heading(h: str) -> str:
    return re.sub(r"[*_`]+", "", h).strip()


def _join_headings(a: str, b: str) -> str:
    return " / ".join(h for h in (a, b) if h)


def _merge_small(sections: list[_Section], min_tokens: int) -> list[_Section]:
    """Fold sections under `min_tokens` into the following section (the last one folds backwards)."""
    out: list[_Section] = []
    carry: _Section | None = None
    for sec in sections:
        if carry is not None:
            sec = _Section(
                _join_headings(carry.heading, sec.heading), [*carry.paragraphs, *sec.paragraphs]
            )
            carry = None
        if sec.tokens < min_tokens:
            carry = sec
        else:
            out.append(sec)
    if carry is not None:
        if out:
            last = out[-1]
            out[-1] = _Section(
                _join_headings(last.heading, carry.heading), [*last.paragraphs, *carry.paragraphs]
            )
        else:
            out.append(carry)
    return out


def _split_long(paragraph: str, max_tokens: int) -> list[str]:
    """Split a paragraph that alone exceeds max_tokens: by sentence, then by words."""
    if n_tokens(paragraph) <= max_tokens:
        return [paragraph]
    pieces: list[str] = []
    buf: list[str] = []
    for sent in SENTENCE_SPLIT_RE.split(paragraph):
        if n_tokens(sent) > max_tokens:
            if buf:
                pieces.append(" ".join(buf))
                buf = []
            words = sent.split()
            step = words_for_tokens(max_tokens)
            pieces.extend(" ".join(words[i : i + step]) for i in range(0, len(words), step))
            continue
        if buf and n_tokens(" ".join([*buf, sent])) > max_tokens:
            pieces.append(" ".join(buf))
            buf = []
        buf.append(sent)
    if buf:
        pieces.append(" ".join(buf))
    return pieces


def _tail_words(text: str, tokens: int) -> str:
    words = text.split()
    return " ".join(words[-words_for_tokens(tokens) :])


def chunk_document(
    markdown: str,
    max_tokens: int = 400,
    overlap_tokens: int = 60,
    min_section_tokens: int = 60,
) -> list[Chunk]:
    """Chunk a markdown document. Returns chunks with consecutive ordinals starting at 0."""
    if overlap_tokens >= max_tokens:
        raise ValueError("overlap_tokens must be smaller than max_tokens")
    sections = _merge_small(split_sections(markdown), min_section_tokens)
    chunks: list[Chunk] = []

    for sec in sections:
        paragraphs = [p for para in sec.paragraphs for p in _split_long(para, max_tokens - overlap_tokens)]
        buf: list[str] = []
        overlap = ""
        for para in paragraphs:
            candidate = " ".join([*buf, para]) if buf else para
            body = f"{overlap} {candidate}".strip() if overlap else candidate
            if buf and n_tokens(body) > max_tokens:
                text = f"{overlap} {' '.join(buf)}".strip() if overlap else " ".join(buf)
                chunks.append(Chunk(len(chunks), sec.heading, text, n_tokens(text)))
                overlap = _tail_words(" ".join(buf), overlap_tokens)
                buf = [para]
            else:
                buf.append(para)
        if buf:
            text = f"{overlap} {' '.join(buf)}".strip() if overlap else " ".join(buf)
            chunks.append(Chunk(len(chunks), sec.heading, text, n_tokens(text)))

    return [c for c in chunks if c.text.strip()]


def display_title(company: str | None, title: str) -> str:
    """Status pages often have generic titles ("Service Health"): prefix the company."""
    title = (title or "").strip()
    if company and company.lower() not in title.lower():
        return f"{company}: {title}" if title else company
    return title


def embedding_text(title: str, heading: str, text: str) -> str:
    """What gets embedded: title and heading prepended (stored display text stays clean)."""
    head = f"{title} — {heading}" if heading else title
    return f"{head}\n{text}"
