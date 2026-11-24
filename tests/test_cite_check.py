from app.generation.cite_check import Segment, check, is_abstention, split_sentences


def test_cited_and_uncited_sentences():
    segs = [
        Segment("The outage was caused by an expired TLS certificate on the gateway", True),
        Segment(". ", False),
        Segment("Engineers also suspected a recent deploy of the frontend service.", False),
    ]
    r = check(segs)
    assert r.factual_sentences == 2
    assert r.cited_sentences == 1
    assert r.coverage == 0.5
    assert r.uncited == ["Engineers also suspected a recent deploy of the frontend service."]


def test_short_connective_sentences_are_ignored():
    segs = [
        Segment("In short: ", False),
        Segment("the DNS zone push removed the storefront A records entirely.", True),
        Segment(" That's it.", False),
    ]
    r = check(segs)
    assert r.factual_sentences == 1
    assert r.coverage == 1.0
    assert r.uncited == []


def test_abstention_is_not_a_factual_claim():
    r = check([Segment("I couldn't find this in the corpus.", False)])
    assert r.factual_sentences == 0
    assert r.coverage is None
    assert is_abstention("The documents do not contain the compensation amount.")


def test_sentence_partially_cited_counts_as_cited():
    segs = [
        Segment("According to the postmortem, ", False),
        Segment("the lock service used wall-clock leases", True),
        Segment(" and lost quorum for 38 minutes.", False),
    ]
    assert check(segs).coverage == 1.0


def test_whitespace_of_cited_block_does_not_leak_into_next_sentence():
    segs = [
        Segment("The generator starter battery had degraded badly. ", True),
        Segment("Nobody had tested the transfer switch under full load for years.", False),
    ]
    r = check(segs)
    assert r.cited_sentences == 1 and len(r.uncited) == 1


def test_newlines_split_list_items():
    text = "- first item has enough words to count here\n- second item also has enough words"
    assert len(split_sentences(text)) == 2
