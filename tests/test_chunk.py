from app.ingest.chunk import chunk_document, embedding_text, n_tokens, split_sections


def para(word: str, n: int) -> str:
    return " ".join(f"{word}{i}" for i in range(n))


def test_splits_on_headings_and_keeps_heading():
    md = f"# Summary\n\n{para('s', 120)}\n\n## Root cause\n\n{para('r', 120)}\n"
    chunks = chunk_document(md, max_tokens=400, overlap_tokens=60, min_section_tokens=10)
    assert [c.heading for c in chunks] == ["Summary", "Root cause"]
    assert chunks[0].text.startswith("s0") and chunks[1].text.startswith("r0")
    assert all("r0" not in c.text for c in chunks if c.heading == "Summary")


def test_ordinals_are_consecutive_and_no_empty_chunks():
    md = "\n\n".join(f"# H{i}\n\n{para(f'w{i}_', 200)}" for i in range(4))
    chunks = chunk_document(md, max_tokens=150, overlap_tokens=20, min_section_tokens=10)
    assert [c.ordinal for c in chunks] == list(range(len(chunks)))
    assert all(c.text.strip() for c in chunks)
    assert all(c.n_tokens > 0 for c in chunks)


def test_chunk_size_bounded_and_overlap_size():
    # 10 paragraphs of 60 words (~80 tokens) in one section -> several chunks with overlap
    paragraphs = [para(f"p{j}_", 60) for j in range(10)]
    md = "# Timeline\n\n" + "\n\n".join(paragraphs)
    chunks = chunk_document(md, max_tokens=400, overlap_tokens=60, min_section_tokens=10)
    assert len(chunks) >= 2
    for c in chunks:
        assert c.n_tokens <= 400 + 1  # rounding slack
    overlap_words = 45  # 60 tokens * 0.75
    for prev, nxt in zip(chunks, chunks[1:], strict=False):
        tail = prev.text.split()[-overlap_words:]
        assert nxt.text.split()[:overlap_words] == tail


def test_overlap_does_not_cross_sections():
    md = f"# A\n\n{para('a', 250)}\n\n# B\n\n{para('b', 250)}"
    chunks = chunk_document(md, max_tokens=400, overlap_tokens=60, min_section_tokens=10)
    b_chunks = [c for c in chunks if c.heading == "B"]
    assert b_chunks and not any(w.startswith("a") for w in b_chunks[0].text.split())


def test_small_sections_are_merged_not_dropped():
    md = "# Intro\n\nShort intro.\n\n# Details\n\n" + para("d", 100)
    chunks = chunk_document(md, max_tokens=400, overlap_tokens=60, min_section_tokens=60)
    assert len(chunks) == 1
    assert "Short intro." in chunks[0].text
    assert chunks[0].heading == "Intro / Details"


def test_oversized_paragraph_is_split():
    md = "# Wall\n\n" + para("x", 1000)
    chunks = chunk_document(md, max_tokens=400, overlap_tokens=60)
    assert len(chunks) >= 3
    assert all(c.n_tokens <= 401 for c in chunks)
    # every word survives
    seen = {w for c in chunks for w in c.text.split()}
    assert {f"x{i}" for i in range(1000)} <= seen


def test_list_items_are_kept():
    md = "# Action items\n\n" + "\n".join(f"- item {i} " + para("li", 10) for i in range(8))
    text = chunk_document(md, min_section_tokens=10)[0].text
    assert all(f"item {i}" in text for i in range(8))


def test_empty_document():
    assert chunk_document("") == []
    assert split_sections("# Only a heading\n") == []


def test_embedding_text_prepends_title_and_heading():
    assert embedding_text("AWS S3", "Root cause", "body") == "AWS S3 — Root cause\nbody"
    assert embedding_text("AWS S3", "", "body") == "AWS S3\nbody"


def test_n_tokens_approximation():
    assert n_tokens(para("w", 75)) == 100


def test_display_title_prefixes_company_only_when_missing():
    from app.ingest.chunk import display_title

    assert display_title("Google", "Service Health") == "Google: Service Health"
    assert display_title("GitHub", "GitHub Availability Report") == "GitHub Availability Report"
    assert display_title(None, "Title") == "Title"
    assert display_title("Acme", "") == "Acme"


def test_near_duplicate_shingles():
    from app.ingest.run import jaccard, shingles

    base = " ".join(f"word{i}" for i in range(300))
    archived = "Archived copy banner. " + base + " footer"
    other = " ".join(f"other{i}" for i in range(300))
    assert jaccard(shingles(base), shingles(archived)) > 0.9
    assert jaccard(shingles(base), shingles(other)) == 0.0
