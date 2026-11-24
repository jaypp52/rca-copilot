"""Contract tests against the real `anthropic` SDK, with HTTP mocked at the transport.

These catch what hand-rolled fakes can't: that the SDK accepts our request parameters
(`fallbacks`, `betas`, `output_config`, document blocks) and that our parsing works on
real SDK response objects (text blocks with `char_location` citations, parsed output).
No network, no API key.
"""

import json

import anthropic
import httpx2

from app.config import Settings
from app.generation.answer import generate
from eval.faithfulness_eval import Judgement, judge
from tests.conftest import make_chunk


def client_returning(body: dict, captured: list):
    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append({"url": str(request.url), "headers": dict(request.headers),
                         "json": json.loads(request.content)})
        return httpx2.Response(200, json=body)

    return anthropic.Anthropic(
        api_key="test-key",
        max_retries=0,
        http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)),
    )


def message(content: list[dict], model="claude-opus-5-5", stop_reason="end_turn") -> dict:
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": model,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 2000, "output_tokens": 120},
    }


def cite(doc_index: int, text: str) -> dict:
    return {"type": "char_location", "cited_text": text, "document_index": doc_index,
            "document_title": None, "start_char_index": 0, "end_char_index": len(text)}


CHUNKS = [
    make_chunk(0, "The client certificate used for mutual TLS expired at 09:00 UTC."),
    make_chunk(1, "Responders first rolled back an unrelated gateway deploy."),
]


def test_generate_request_and_response_through_real_sdk():
    captured: list = []
    body = message([
        {"type": "thinking", "thinking": "", "signature": "sig"},
        {"type": "text", "text": "The outage happened because "},
        {"type": "text", "text": "the mutual TLS client certificate expired at 09:00 UTC",
         "citations": [cite(0, "The client certificate used for mutual TLS expired at 09:00 UTC.")]},
        {"type": "text", "text": "."},
    ])
    client = client_returning(body, captured)
    ans = generate("Why did payments fail?", CHUNKS, client=client, settings=Settings(_env_file=None))

    (req,) = captured
    assert req["url"].endswith("/v1/messages")
    assert "server-side-fallback-2026-07-01" in req["headers"]["anthropic-beta"]
    sent = req["json"]
    assert sent["model"] == "claude-opus-5-5"
    assert sent["fallbacks"] == "default"
    assert sent["output_config"] == {"effort": "medium"}
    assert not {"temperature", "top_p", "top_k", "thinking"} & sent.keys()
    docs = [b for b in sent["messages"][0]["content"] if b["type"] == "document"]
    assert docs[0]["source"] == {"type": "text", "media_type": "text/plain", "data": CHUNKS[0].text}
    assert docs[0]["citations"] == {"enabled": True}

    assert ans.answer == "The outage happened because the mutual TLS client certificate expired at 09:00 UTC[1]."
    assert [(c.n, c.chunk_id) for c in ans.citations] == [(1, CHUNKS[0].chunk_id)]
    assert ans.cite_report.coverage == 1.0
    assert ans.usage["input_tokens"] == 2000
    assert ans.usage["cost_usd"] == (2000 * 4 + 120 * 20) / 1_000_000


def test_generate_without_fallback_uses_stable_endpoint():
    captured: list = []
    client = client_returning(message([{"type": "text", "text": "I couldn't find this in the corpus."}],
                                      model="claude-haiku-5-5"), captured)
    s = Settings(_env_file=None, claude_model="claude-haiku-5-5")
    ans = generate("q?", CHUNKS, client=client, settings=s)
    assert "server-side-fallback" not in captured[0]["headers"].get("anthropic-beta", "")
    assert "fallbacks" not in captured[0]["json"]
    assert ans.not_found


def test_judge_structured_output_through_real_sdk():
    captured: list = []
    verdict = {"claims": [
        {"text": "The certificate expired at 09:00 UTC.", "supported": True, "reason": "Stated in [1]."},
        {"text": "It was renewed in 5 minutes.", "supported": False, "reason": "Not in the source."},
    ]}
    client = client_returning(message([{"type": "text", "text": json.dumps(verdict)}],
                                      model="claude-sonnet-5-5"), captured)
    result, usage = judge("answer [1]", {1: CHUNKS[0].text}, Settings(_env_file=None), client=client)
    assert isinstance(result, Judgement)
    assert [c.supported for c in result.claims] == [True, False]
    sent = captured[0]["json"]
    assert sent["model"] == "claude-sonnet-5-5"
    assert sent["output_config"]["format"]["type"] == "json_schema"
    assert usage["cost_usd"] > 0
