import pytest

from app.retrieval.fusion import rrf


def test_rrf_expected_order():
    rankings = {"bm25": ["a", "b", "c"], "dense": ["b", "c", "d"]}
    fused = rrf(rankings, k=60)
    # b: 1/62 + 1/61, c: 1/63 + 1/62, a: 1/61, d: 1/63
    assert [f.id for f in fused] == ["b", "c", "a", "d"]
    assert fused[0].score == pytest.approx(1 / 62 + 1 / 61)
    assert fused[0].ranks == {"bm25": 2, "dense": 1}
    assert fused[3].ranks == {"dense": 3}


def test_rrf_ties_are_stable():
    # x and y have identical scores (rank 1 in one list each): first-seen order wins
    fused = rrf({"bm25": ["x"], "dense": ["y"]}, k=60)
    assert [f.id for f in fused] == ["x", "y"]
    fused = rrf({"dense": ["y"], "bm25": ["x"]}, k=60)
    assert [f.id for f in fused] == ["y", "x"]


def test_rrf_top_n_and_empty():
    assert rrf({"a": [], "b": []}) == []
    fused = rrf({"a": list("abcdef")}, top_n=3)
    assert [f.id for f in fused] == ["a", "b", "c"]


def test_rrf_ignores_duplicates_within_a_list():
    fused = rrf({"bm25": ["a", "a", "b"]}, k=60)
    assert fused[0].score == pytest.approx(1 / 61)
    assert fused[1].ranks == {"bm25": 3}


def test_item_in_both_lists_beats_item_top_of_one():
    fused = rrf({"bm25": ["solo", "both"], "dense": ["x", "both"]}, k=60)
    assert fused[0].id == "both"


def test_diversify_caps_chunks_per_doc_and_renumbers():
    from app.retrieval.pipeline import diversify
    from tests.conftest import make_chunk

    chunks = [make_chunk(i, "t", doc_id=d, chunk_id=f"{d}-{i}") for i, d in enumerate("aaabab c".replace(" ", ""))]
    out = diversify(chunks, 2)
    assert [c.doc_id for c in out] == ["a", "a", "b", "b", "c"]
    assert [c.rank for c in out] == [1, 2, 3, 4, 5]
    assert len(diversify(chunks, 0)) == len(chunks)
