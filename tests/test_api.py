"""FastAPI contract tests. Retrieval is faked and the Claude client is mocked."""

from fastapi.testclient import TestClient

from app.api import AskResponse, SearchResponse, create_app
from tests.conftest import FakeAnthropic, FakeRetriever, fake_message, make_chunk, text_block

CHUNKS = [
    make_chunk(0, "The client certificate used for mutual TLS expired at 09:00 UTC.",
               doc_title="Lumenpay outage", heading="Root cause"),
    make_chunk(1, "Fernhill's CDN certificate expired because uploads were manual.",
               doc_title="Fernhill outage", heading="Root cause"),
]

ANSWER_BLOCKS = [
    text_block("Lumenpay's outage happened because ", None),
    text_block("its mutual TLS client certificate expired at 09:00 UTC",
               [(0, "The client certificate used for mutual TLS expired at 09:00 UTC.")]),
    text_block(". ", None),
    text_block("Fernhill's CDN certificate also expired because uploading it was manual",
               [(1, "Fernhill's CDN certificate expired because uploads were manual.")]),
    text_block(". Some engineers first blamed an unrelated deploy for the errors.", None),
]


def client_for(retriever, anthropic_client):
    return TestClient(create_app(retriever=retriever, anthropic_client=anthropic_client))


def test_ask_returns_answer_with_inline_citations_and_uncited():
    fake = FakeAnthropic(fake_message(ANSWER_BLOCKS))
    with client_for(FakeRetriever(CHUNKS), fake) as c:
        r = c.post("/ask", json={"question": "Which outages involved expired certificates?"})
    assert r.status_code == 200, r.text
    body = AskResponse.model_validate(r.json())  # schema check
    assert "09:00 UTC[1]" in body.answer
    assert "manual[2]" in body.answer
    assert [x.n for x in body.citations] == [1, 2]
    assert body.citations[0].url == CHUNKS[0].url
    assert body.citations[0].chunk_id == CHUNKS[0].chunk_id
    assert body.uncited == ["Some engineers first blamed an unrelated deploy for the errors."]
    assert 0 < body.citation_coverage < 1
    assert body.not_found is False
    assert body.usage["input_tokens"] == 1200 and body.usage["cost_usd"] > 0
    assert len(body.retrieval) == 2 and body.retrieval[0].rerank_score is not None


def test_ask_request_uses_documents_with_citations_and_effort():
    fake = FakeAnthropic(fake_message(ANSWER_BLOCKS))
    with client_for(FakeRetriever(CHUNKS), fake) as c:
        c.post("/ask", json={"question": "Which outages involved expired certificates?", "k": 2})
    (call,) = fake.all_calls
    docs = [b for b in call["messages"][0]["content"] if b["type"] == "document"]
    assert len(docs) == 2
    assert docs[0]["title"] == "Lumenpay outage §Root cause"
    assert call["output_config"] == {"effort": "medium"}
    for banned in ("temperature", "top_p", "top_k", "thinking"):
        assert banned not in call
    # Opus 5.5 -> server-side refusal fallback through the beta endpoint
    assert fake.beta.messages.calls and call["fallbacks"] == "default"
    assert call["betas"] == ["server-side-fallback-2026-07-01"]


def test_not_found_path_skips_claude():
    fake = FakeAnthropic(fake_message(ANSWER_BLOCKS))
    with client_for(FakeRetriever(CHUNKS, found=False), fake) as c:
        r = c.post("/ask", json={"question": "What caused the Zephyr Airlines outage?"})
    assert r.status_code == 200
    body = r.json()
    assert body["not_found"] is True
    assert body["answer"] == "I couldn't find this in the corpus."
    assert body["citations"] == []
    assert fake.all_calls == []


def test_model_abstention_is_reported_as_not_found():
    fake = FakeAnthropic(fake_message([text_block("I couldn't find this in the corpus.")]))
    with client_for(FakeRetriever(CHUNKS), fake) as c:
        body = c.post("/ask", json={"question": "How much compensation was paid?"}).json()
    assert body["not_found"] is True and body["uncited"] == []


def test_refusal_maps_to_clear_error():
    from types import SimpleNamespace

    msg = fake_message([], stop_reason="refusal",
                       stop_details=SimpleNamespace(category="cyber", explanation="..."))
    with client_for(FakeRetriever(CHUNKS), FakeAnthropic(msg)) as c:
        r = c.post("/ask", json={"question": "something declined"})
    assert r.status_code == 422
    assert r.json()["detail"]["error"] == "refusal"


def test_max_tokens_maps_to_error():
    msg = fake_message([text_block("partial")], stop_reason="max_tokens")
    with client_for(FakeRetriever(CHUNKS), FakeAnthropic(msg)) as c:
        r = c.post("/ask", json={"question": "a long question"})
    assert r.status_code == 502
    assert r.json()["detail"]["error"] == "max_tokens"


def test_ask_validates_input():
    with client_for(FakeRetriever(CHUNKS), FakeAnthropic(fake_message([]))) as c:
        assert c.post("/ask", json={"question": ""}).status_code == 422
        assert c.post("/ask", json={"question": "valid question", "k": 50}).status_code == 422


def test_search_returns_stages_without_llm():
    fake = FakeAnthropic(fake_message([]))
    with client_for(FakeRetriever(CHUNKS), fake) as c:
        r = c.get("/search", params={"q": "expired certificate", "k": 2})
    assert r.status_code == 200
    body = SearchResponse.model_validate(r.json())
    assert body.found and len(body.results) == 2
    assert fake.all_calls == []


def test_health_reports_models_even_without_db():
    with client_for(FakeRetriever(CHUNKS), FakeAnthropic(fake_message([]))) as c:
        r = c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["db_ok"] is False and body["status"] == "degraded"
    assert body["models"]["generator"] == "claude-opus-5-5"


def test_ingest_requires_admin_token():
    with client_for(FakeRetriever(CHUNKS), FakeAnthropic(fake_message([]))) as c:
        assert c.post("/ingest").status_code in (401, 403)


def test_missing_credentials_is_a_clear_503():
    class NoCreds(FakeAnthropic):
        def __init__(self):
            super().__init__(None)

            def boom(**kwargs):
                raise TypeError("Could not resolve authentication method.")

            self.messages.create = boom
            self.beta.messages.create = boom

    with client_for(FakeRetriever(CHUNKS), NoCreds()) as c:
        r = c.post("/ask", json={"question": "What caused the S3 outage?"})
    assert r.status_code == 503
    assert r.json()["detail"]["error"] == "not_configured"
