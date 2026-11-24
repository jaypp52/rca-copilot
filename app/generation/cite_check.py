"""Is every claim cited?

Claude's Citations feature returns the answer as a sequence of text blocks; blocks
backed by a source carry a `citations` array. We stitch the blocks back together,
split the result into sentences, and call a sentence *cited* if its character span
overlaps any cited block.

citation_coverage = cited factual sentences / factual sentences

Non-factual sentences are ignored: abstentions ("I couldn't find ...") and short
connective one-liners (< 6 words). Uncited sentences are returned, never dropped.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MIN_FACTUAL_WORDS = 6
ABSTAIN_PATTERNS = re.compile(
    r"couldn['’]t find|could not find|not (?:in|covered by|mentioned in) the (?:corpus|provided|documents|sources)"
    r"|(?:documents|sources|excerpts|corpus) (?:do|does) not (?:contain|say|mention|cover|describe)"
    r"|no information (?:about|on)|isn['’]t (?:covered|mentioned)",
    re.I,
)
# sentence boundaries: ., !, ? followed by whitespace, or any newline
_BOUNDARY = re.compile(r"(?<=[.!?])\s+|\n+")


@dataclass
class Segment:
    text: str
    cited: bool


@dataclass
class CiteReport:
    factual_sentences: int
    cited_sentences: int
    uncited: list[str] = field(default_factory=list)

    @property
    def coverage(self) -> float | None:
        if self.factual_sentences == 0:
            return None
        return self.cited_sentences / self.factual_sentences


def split_sentences(text: str) -> list[tuple[int, int]]:
    """Return (start, end) character spans of sentences in `text`, whitespace-trimmed."""
    spans = []
    pos = 0
    for m in _BOUNDARY.finditer(text):
        spans.append((pos, m.start()))
        pos = m.end()
    spans.append((pos, len(text)))
    out = []
    for s, e in spans:
        seg = text[s:e]
        lstrip = len(seg) - len(seg.lstrip())
        rstrip = len(seg) - len(seg.rstrip())
        s2, e2 = s + lstrip, e - rstrip
        if e2 > s2:
            out.append((s2, e2))
    return out


def is_abstention(sentence: str) -> bool:
    return bool(ABSTAIN_PATTERNS.search(sentence))


def is_factual(sentence: str) -> bool:
    words = re.findall(r"\w+", sentence)
    return len(words) >= MIN_FACTUAL_WORDS and not is_abstention(sentence)


def check(segments: list[Segment]) -> CiteReport:
    text = ""
    cited_spans: list[tuple[int, int]] = []
    for seg in segments:
        start = len(text)
        text += seg.text
        if seg.cited and seg.text.strip():
            lead = len(seg.text) - len(seg.text.lstrip())
            trail = len(seg.text) - len(seg.text.rstrip())
            cited_spans.append((start + lead, len(text) - trail))

    report = CiteReport(0, 0)
    for s, e in split_sentences(text):
        sentence = text[s:e]
        if not is_factual(sentence):
            continue
        report.factual_sentences += 1
        if any(cs < e and s < ce for cs, ce in cited_spans):
            report.cited_sentences += 1
        else:
            report.uncited.append(sentence)
    return report
