"""Grounded generation with the Claude API Citations feature.

Each retrieved chunk is its own `document` block with citations enabled. Claude's
reply comes back as text blocks; cited blocks carry `citations[*].document_index`,
which we map back to chunk ids / URLs and render as inline `[n]` markers.

Citations can't be combined with structured outputs, so the JSON response is built
here from the content blocks.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from functools import lru_cache

import anthropic

from app.config import PRICES_PER_MTOK, SERVER_FALLBACK_MODELS, Settings, get_settings
from app.generation.cite_check import CiteReport, Segment, check, is_abstention
from app.generation.prompt import NOT_FOUND_ANSWER, SYSTEM_PROMPT, build_messages
from app.retrieval.pipeline import RetrievedChunk

log = logging.getLogger(__name__)


class GenerationError(Exception):
    def __init__(self, kind: str, message: str, status_code: int):
        super().__init__(message)
        self.kind = kind
        self.status_code = status_code


@dataclass
class Citation:
    n: int
    chunk_id: str
    doc_title: str
    url: str
    cited_text: str


@dataclass
class Answer:
    answer: str
    citations: list[Citation]
    segments: list[Segment]
    cite_report: CiteReport
    not_found: bool
    model: str | None = None
    usage: dict = field(default_factory=dict)
    latency_ms: float = 0.0
    fallback_used: bool = False


@lru_cache
def get_client() -> anthropic.Anthropic:
    # Credentials resolve from the environment (ANTHROPIC_API_KEY, or an `ant auth` profile).
    return anthropic.Anthropic(max_retries=3)


def cost_usd(model: str | None, input_tokens: int, output_tokens: int) -> float | None:
    price = PRICES_PER_MTOK.get(model or "")
    if price is None:
        return None
    return (input_tokens * price[0] + output_tokens * price[1]) / 1_000_000


def call_claude(client, s: Settings, system: str, messages: list[dict]):
    kwargs = {
        "model": s.claude_model,
        "max_tokens": s.max_tokens,
        "system": system,
        "messages": messages,
        # Explicit effort documents the choice (Opus 5.5 defaults to medium). Thinking is
        # adaptive by default and can't be disabled on Opus 5.5, so no `thinking` key.
        # No temperature / top_p / top_k: rejected on current models.
        "output_config": {"effort": s.effort},
    }
    if s.use_server_fallback and s.claude_model in SERVER_FALLBACK_MODELS:
        # On a safety-classifier decline the API re-runs the request on Anthropic's
        # recommended fallback model inside the same call.
        return client.beta.messages.create(
            **kwargs, betas=[s.server_fallback_beta], fallbacks="default"
        )
    return client.messages.create(**kwargs)


def not_found_answer() -> Answer:
    return Answer(
        answer=NOT_FOUND_ANSWER,
        citations=[],
        segments=[Segment(NOT_FOUND_ANSWER, False)],
        cite_report=CiteReport(0, 0),
        not_found=True,
    )


def parse_response(response, chunks: list[RetrievedChunk]) -> tuple[str, list[Citation], list[Segment]]:
    """Turn content blocks into (answer with [n] markers, numbered citations, raw segments)."""
    numbers: dict[str, int] = {}
    spans: dict[str, list[str]] = {}
    parts: list[str] = []
    segments: list[Segment] = []

    for block in response.content:
        if getattr(block, "type", None) != "text":
            continue  # thinking / fallback blocks carry no answer text
        cits = getattr(block, "citations", None) or []
        segments.append(Segment(block.text, cited=bool(cits)))
        marks: list[int] = []
        for c in cits:
            idx = getattr(c, "document_index", None)
            if idx is None or not 0 <= idx < len(chunks):
                continue
            ch = chunks[idx]
            if ch.chunk_id not in numbers:
                numbers[ch.chunk_id] = len(numbers) + 1
                spans[ch.chunk_id] = []
            cited = (getattr(c, "cited_text", "") or "").strip()
            if cited and cited not in spans[ch.chunk_id]:
                spans[ch.chunk_id].append(cited)
            if numbers[ch.chunk_id] not in marks:
                marks.append(numbers[ch.chunk_id])
        if marks:
            body = block.text.rstrip()
            trailing = block.text[len(body):]
            parts.append(body + "".join(f"[{n}]" for n in sorted(marks)) + trailing)
        else:
            parts.append(block.text)

    by_id = {c.chunk_id: c for c in chunks}
    citations = [
        Citation(
            n=n,
            chunk_id=cid,
            doc_title=by_id[cid].doc_title,
            url=by_id[cid].url,
            cited_text=" … ".join(spans[cid]),
        )
        for cid, n in sorted(numbers.items(), key=lambda kv: kv[1])
    ]
    return "".join(parts).strip(), citations, segments


def generate(
    question: str,
    chunks: list[RetrievedChunk],
    client=None,
    settings: Settings | None = None,
) -> Answer:
    s = settings or get_settings()
    client = client or get_client()
    messages = build_messages(question, chunks)

    t0 = time.perf_counter()
    try:
        response = call_claude(client, s, SYSTEM_PROMPT, messages)
    except anthropic.RateLimitError as e:
        raise GenerationError("rate_limited", "Claude API rate limit hit; retry later", 429) from e
    except anthropic.APIStatusError as e:
        raise GenerationError("upstream_error", f"Claude API error {e.status_code}", 502) from e
    except anthropic.APIConnectionError as e:
        raise GenerationError("upstream_unreachable", "could not reach the Claude API", 503) from e
    except TypeError as e:
        # The SDK raises TypeError at request time when no credential source resolves.
        raise GenerationError(
            "not_configured", "no Anthropic credentials: set ANTHROPIC_API_KEY in .env", 503
        ) from e
    latency_ms = (time.perf_counter() - t0) * 1000

    u = response.usage
    usage = {
        "input_tokens": u.input_tokens,
        "output_tokens": u.output_tokens,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
    usage["cost_usd"] = cost_usd(response.model, u.input_tokens, u.output_tokens)
    fallback_used = any(
        getattr(it, "type", None) == "fallback_message" for it in (getattr(u, "iterations", None) or [])
    )
    log.info(json.dumps({
        "event": "claude_call", "model": response.model, "stop_reason": response.stop_reason,
        "latency_ms": round(latency_ms), "fallback_used": fallback_used, **usage,
    }))

    # Check stop_reason before reading content.
    if response.stop_reason == "refusal":
        details = getattr(response, "stop_details", None)
        category = getattr(details, "category", None) if details else None
        raise GenerationError("refusal", f"Claude declined to answer (category: {category})", 422)
    if response.stop_reason == "max_tokens":
        raise GenerationError("max_tokens", "answer was cut off at max_tokens", 502)

    text, citations, segments = parse_response(response, chunks)
    report = check(segments)
    not_found = not citations and (not text or is_abstention(text))
    return Answer(
        answer=text or NOT_FOUND_ANSWER,
        citations=citations,
        segments=segments,
        cite_report=report,
        not_found=not_found,
        model=response.model,
        usage=usage,
        latency_ms=latency_ms,
        fallback_used=fallback_used,
    )
