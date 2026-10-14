import json
from pathlib import Path

from ai_platform.retrieval.chunker import Chunk, chunk_document
from ai_platform.retrieval.corpus import Document, load_corpus
from ai_platform.retrieval.embed import HashEmbedder
from ai_platform.retrieval.index import Index, tokenize
from ai_platform.retrieval.recall import QUESTIONS_FILE, load_questions, measure
from ai_platform.retrieval.search import format_context, search


def test_markdown_chunks_carry_heading_path_and_source():
    doc = Document(source="X.md", title="X", kind="markdown",
                   text="# Top\nintro\n\n## 2026-10-01 — Study\nbody one\n\n### Detail\nbody two")
    chunks = chunk_document(doc)
    assert [c.heading for c in chunks] == ["Top", "Top > 2026-10-01 — Study", "Top > 2026-10-01 — Study > Detail"]
    assert all(c.text.startswith("X.md > ") for c in chunks)
    assert chunks[1].body == "body one"


def test_long_sections_split_with_overlap():
    body = "\n\n".join(f"paragraph {i} " + "x" * 300 for i in range(10))
    doc = Document(source="Y.md", title="Y", kind="markdown", text="## H\n" + body)
    chunks = chunk_document(doc, max_chars=1000, overlap=100)
    assert len(chunks) > 1 and all(len(c.body) <= 1000 for c in chunks)
    assert chunks[1].body[:100] == chunks[0].body[-100:]


def test_tokenizer_indexes_compound_numbers_and_parts():
    toks = tokenize("Donchian 80/40 at 0.80% round trip, mean-reversion")
    assert {"80/40", "80", "40", "0.80", "mean-reversion", "reversion"} <= set(toks)


def test_hybrid_search_returns_sources_and_prefers_keyword_match(tmp_path):
    docs = [Document(source="a.md", title="a", kind="markdown", text="## Fees\nThe maker fee round trip is 0.80% on Coinbase."),
            Document(source="b.md", title="b", kind="markdown", text="## Observer\nThe observer will never touch a state file."),
            Document(source="ledger/SPY", title="SPY", kind="ledger", text="Ledger SPY\nSPY trade 1: exit reason RSI fell back through 65, net +0.82%")]
    from ai_platform.retrieval.chunker import chunk_corpus
    emb = HashEmbedder()
    chunks = chunk_corpus(docs)
    idx = Index(chunks, emb.encode([c.text for c in chunks]), {"embedder": emb.name}, emb)
    hits = search(idx, "what is the maker fee round trip", k=2)
    assert hits[0].source == "a.md" and hits[0].keyword_rank == 1
    ctx = format_context(hits)
    assert ctx.startswith("<context>") and "source: a.md > Fees" in ctx
    assert search(idx, "state file", k=1, mode="keyword")[0].source == "b.md"
    assert search(idx, "RSI 65", k=1, mode="vector")[0].source  # vector mode always returns something


def test_index_roundtrip_and_embedder_mismatch_guard(tmp_path):
    docs = [Document(source="a.md", title="a", kind="markdown", text="## H\nhello world")]
    from ai_platform.retrieval.chunker import chunk_corpus
    emb = HashEmbedder()
    chunks = chunk_corpus(docs)
    idx = Index(chunks, emb.encode([c.text for c in chunks]), {"embedder": emb.name, "embedder_kind": "hash"}, emb)
    idx.save(tmp_path)
    loaded = Index.load(tmp_path, embedder=HashEmbedder())
    assert len(loaded.chunks) == 1 and loaded.embeddings.shape == idx.embeddings.shape

    class Other(HashEmbedder):
        name = "other"
    try:
        Index.load(tmp_path, embedder=Other())
        assert False
    except RuntimeError:
        pass


def test_corpus_loads_real_docs_and_twenty_labelled_questions_are_well_formed():
    docs = load_corpus()
    sources = {d.source for d in docs}
    assert "DECISIONS.md" in sources and "OBSERVER.md" in sources
    assert any(s.startswith("experiments/") for s in sources)
    qs = load_questions()
    assert len(qs) == 20 and len({q["id"] for q in qs}) == 20
    for q in qs:
        assert q["question"] and q["relevant"] and all({"source", "contains"} <= set(r) for r in q["relevant"])


def test_recall_measurement_runs_with_hash_embedder():
    emb = HashEmbedder()
    idx = Index.build(embedder=emb, save_to=None)
    rep = measure(idx, load_questions()[:5], ks=(1, 5), modes=("keyword", "hybrid"))
    assert set(rep["recall_at_k"]) == {"keyword", "hybrid"}
    assert 0.0 <= rep["recall_at_k"]["hybrid"]["@5"] <= 1.0
