"""retrieve -> (abstain | generate): shared by the API and the eval so they measure the same thing."""

from __future__ import annotations

import time
from dataclasses import dataclass

from app.generation.answer import Answer, generate, not_found_answer
from app.retrieval.pipeline import RetrievalResult, Retriever


@dataclass
class AskResult:
    answer: Answer
    retrieval: RetrievalResult
    latency_ms: float


def ask(retriever: Retriever, question: str, k: int | None = None, client=None) -> AskResult:
    t0 = time.perf_counter()
    retrieval = retriever.search(question, k)
    if not retrieval.found:
        # Best cross-encoder score is below the calibrated threshold: don't spend a Claude call.
        answer = not_found_answer()
    else:
        answer = generate(question, retrieval.chunks, client=client, settings=retriever.s)
    return AskResult(answer, retrieval, (time.perf_counter() - t0) * 1000)
