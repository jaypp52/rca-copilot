"""Metric maths, threshold calibration, the CI gate and BM25 tokenisation."""

import pytest

from app.retrieval.bm25 import BM25Index, tokenize
from eval.calibrate import choose_threshold
from eval.gate import check
from eval.judge_agreement import cohen_kappa
from eval.retrieval_eval import evidence_recall_at, mrr_at, recall_at
from tests.conftest import make_chunk


def ranked(*doc_ids):
    return [make_chunk(i, "t", doc_id=d, chunk_id=f"{d}-{i}") for i, d in enumerate(doc_ids)]


def test_recall_and_mrr():
    r = ranked("x", "a", "y", "z", "q", "b")
    assert recall_at(r, {"a", "b"}, 5) == 0.5
    assert recall_at(r, {"a", "b"}, 10) == 1.0
    assert mrr_at(r, {"a", "b"}) == 0.5
    assert mrr_at(r, {"nope"}) == 0.0
    # doc "a" has evidence in a-1 (retrieved) and a-7 (not); doc "b" only in b-9 (not retrieved)
    assert evidence_recall_at(r, {"a-1", "a-7", "b-9"}, 10) == 0.5
    assert evidence_recall_at(r, {"a-1"}, 1) == 0.0
    assert evidence_recall_at(r, set(), 10) is None


def test_choose_threshold_separates_classes():
    scores = [(True, 6.0), (True, 3.0), (True, 1.0), (False, -4.0), (False, -2.0)]
    best = choose_threshold(scores)
    assert best["threshold"] == -0.5  # halfway between -2.0 and the lowest answerable (1.0)
    assert best["answerable_kept"] == 1.0 and best["unanswerable_rejected"] == 1.0


def test_choose_threshold_never_drops_answerable_by_default():
    # a near-miss unanswerable question scores above an answerable one
    scores = [(True, 6.0), (True, -1.0), (False, 2.5), (False, -5.0)]
    best = choose_threshold(scores)
    assert best["answerable_kept"] == 1.0
    assert best["unanswerable_rejected"] == 0.5
    assert -5.0 < best["threshold"] < -1.0


def _result(r5: dict, faith=None):
    return {"suite": "fixture", "answers": {"faithfulness": faith} if faith is not None else None,
            "retrieval": {"ablation": {m: {"recall@5": v} for m, v in r5.items()}}}


def test_gate_detects_recall_regression_in_any_mode():
    modes = {"bm25": 0.9, "dense": 0.8, "hybrid": 0.9, "hybrid_rerank": 1.0}
    base = {"fixture": {"recall@5": modes, "faithfulness": 0.95}}
    assert check(_result({**modes, "bm25": 0.89}), base) == []  # within 2 points
    assert len(check(_result({**modes, "hybrid": 0.04}), base)) == 1  # broken fusion, rerank masks it
    assert len(check(_result({**modes, "hybrid_rerank": 0.95}), base)) == 1
    assert len(check(_result(modes, faith=0.90), base)) == 1
    assert check(_result(modes, faith=0.93), base) == []
    assert check(_result(modes), {}) != []  # missing baseline is a failure, not a silent pass


def test_cohen_kappa():
    assert cohen_kappa([(True, True), (False, False)]) == pytest.approx(1.0)
    assert cohen_kappa([]) is None


def test_bm25_tokenize_and_search():
    assert tokenize("The TLS certificate, expired!") == ["tls", "certificate", "expired"]
    idx = BM25Index.build(
        ["c1", "c2", "c3"],
        ["expired tls certificate on gateway", "dns zone push removed records", "leap second clock"],
    )
    hits = idx.search("which certificate expired?", k=10)
    assert hits[0][0] == "c1"
    assert all(cid != "c2" for cid, _ in hits)  # zero-score docs are dropped
    assert idx.search("the of and", k=5) == []  # stopwords only


def test_bm25_roundtrip(tmp_path):
    idx = BM25Index.build(["a", "b"], ["kafka disk full", "redis failover stampede"])
    idx.save(tmp_path)
    loaded = BM25Index.load(tmp_path)
    assert loaded.search("kafka", 5)[0][0] == "a"
