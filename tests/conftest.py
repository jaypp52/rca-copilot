"""Shared fakes: no network, no API key, no database needed for unit tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.config import Settings
from app.retrieval.pipeline import RetrievalResult, RetrievedChunk


def make_chunk(i: int, text: str, **kw) -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=kw.get("chunk_id", f"doc{i}-0"),
        doc_id=kw.get("doc_id", f"doc{i}"),
        url=kw.get("url", f"https://example.invalid/pm/{i}"),
        doc_title=kw.get("doc_title", f"Postmortem {i}"),
        company=kw.get("company", f"Company{i}"),
        heading=kw.get("heading", "Root cause"),
        text=text,
        rank=i + 1,
        rerank_score=kw.get("rerank_score", 5.0 - i),
    )


def text_block(text: str, cites: list[tuple[int, str]] | None = None):
    citations = [SimpleNamespace(type="char_location", document_index=d, cited_text=t,
                                 document_title=None, start_char_index=0, end_char_index=len(t))
                 for d, t in (cites or [])]
    return SimpleNamespace(type="text", text=text, citations=citations or None)


def fake_message(blocks, stop_reason="end_turn", model="claude-opus-5-5", stop_details=None):
    return SimpleNamespace(
        content=blocks,
        stop_reason=stop_reason,
        stop_details=stop_details,
        model=model,
        usage=SimpleNamespace(input_tokens=1200, output_tokens=150, cache_read_input_tokens=0,
                              cache_creation_input_tokens=0, iterations=None),
    )


class FakeMessages:
    def __init__(self, response):
        self.response = response
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeAnthropic:
    """Mimics `client.messages.create` and `client.beta.messages.create`."""

    def __init__(self, response):
        self.messages = FakeMessages(response)
        self.beta = SimpleNamespace(messages=FakeMessages(response))

    @property
    def all_calls(self) -> list[dict]:
        return self.messages.calls + self.beta.messages.calls


class FakePool:
    def connection(self):
        raise RuntimeError("no database in unit tests")


class FakeRetriever:
    def __init__(self, chunks: list[RetrievedChunk], found: bool = True, settings: Settings | None = None):
        self.chunks = chunks
        self.found = found
        self.s = settings or Settings(_env_file=None)
        self.pool = FakePool()
        self.queries: list[str] = []

    def search(self, question: str, k: int | None = None) -> RetrievalResult:
        self.queries.append(question)
        top = self.chunks[: k or self.s.rerank_k]
        return RetrievalResult(top, top[0].rerank_score if top else None, self.found)

    def bm25_loaded(self) -> bool:
        return True


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None)
