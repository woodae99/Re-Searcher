"""Hermetic tests for the retrieval-eval metrics (fake search_fn, no embeddings)."""

from src.retrieval_eval import EvalProbe, compare_configs, evaluate
from src.pipeline import ResearchRAGPipeline
from src.registry import SourceRegistry
from src.retrieval.survey import aggregate_hits_by_source


def _fake_search(ranking_by_query):
    """Build a search_fn returning a fixed source-id ranking per query."""
    def search_fn(query, k):
        return [{"source_id": sid} for sid in ranking_by_query.get(query, [])[:k]]
    return search_fn


def test_hit_at_and_mrr_basic():
    probes = [
        EvalProbe("q1", frozenset({"A"}), probe_id="p1"),
        EvalProbe("q2", frozenset({"B"}), probe_id="p2"),
    ]
    # q1: A is rank 1; q2: B is rank 3
    search = _fake_search({"q1": ["A", "X", "Y"], "q2": ["X", "Y", "B", "Z"]})
    report = evaluate(search, probes, k_values=(1, 3, 5))

    assert report["hit_at"]["hit@1"] == 0.5      # only q1
    assert report["hit_at"]["hit@3"] == 1.0      # both within 3
    assert report["mrr"] == round((1.0 + 1.0 / 3) / 2, 4)
    assert report["mean_first_rank"] == 2.0      # (1 + 3) / 2


def test_miss_counts_as_zero():
    probes = [EvalProbe("q", frozenset({"A"}))]
    search = _fake_search({"q": ["X", "Y", "Z"]})  # A absent
    report = evaluate(search, probes, k_values=(1, 3))

    assert report["hit_at"]["hit@3"] == 0.0
    assert report["mrr"] == 0.0
    assert report["found_rate"] == 0.0
    assert report["mean_first_rank"] is None
    assert report["per_probe"][0]["first_relevant_rank"] is None


def test_multiple_expected_sources_any_counts():
    probes = [EvalProbe("q", frozenset({"A", "B"}))]
    search = _fake_search({"q": ["X", "B", "A"]})  # B at rank 2 satisfies
    report = evaluate(search, probes, k_values=(1, 3))
    assert report["hit_at"]["hit@1"] == 0.0
    assert report["hit_at"]["hit@3"] == 1.0
    assert report["per_probe"][0]["first_relevant_rank"] == 2


def test_empty_probes_safe():
    report = evaluate(_fake_search({}), [], k_values=(1, 5))
    assert report["probes"] == 0
    assert report["hit_at"] == {}


def test_compare_configs_ranks_by_headline_k():
    a = evaluate(_fake_search({"q": ["A"]}), [EvalProbe("q", frozenset({"A"}))], k_values=(1, 5))
    b = evaluate(_fake_search({"q": ["X", "Y", "Z", "Q", "A"]}),
                 [EvalProbe("q", frozenset({"A"}))], k_values=(1, 5))
    rows = compare_configs({"good": a, "worse": b}, headline_k=1)
    assert rows[0]["config"] == "good"
    assert rows[0]["hit@1"] == 1.0
    assert rows[1]["config"] == "worse"


def test_survey_aggregates_hits_by_source_and_ranks(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_chunks(
        ["z1-c1", "z1-c2", "z2-c1"],
        [
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "Z1",
                "source_id": "zotero-Z1-attachment-1",
                "chunk_level": "mid",
                "chunk_index": 0,
                "title": "Alpha",
                "authors": "A. Author",
                "item_type": "book",
                "language": "en",
                "tags": ["Process"],
                "abstractNote": "Alpha abstract.",
            },
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "Z1",
                "source_id": "zotero-Z1-attachment-1",
                "chunk_level": "mid",
                "chunk_index": 1,
                "title": "Alpha",
                "authors": "A. Author",
                "item_type": "book",
                "language": "en",
                "tags": ["Process"],
            },
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "Z2",
                "source_id": "zotero-Z2-attachment-1",
                "chunk_level": "mid",
                "chunk_index": 0,
                "title": "Beta",
                "authors": "B. Author",
                "item_type": "book",
                "language": "en",
                "tags": ["Process"],
            },
        ],
    )
    registry.refresh_sources()

    results = [
        (
            "z1-c1",
            "best alpha text",
            0.91,
            {"source_type": "zotero_fulltext", "zotero_key": "Z1", "chunk_level": "mid", "chunk_index": 0},
        ),
        (
            "z2-c1",
            "beta text",
            0.88,
            {"source_type": "zotero_fulltext", "zotero_key": "Z2", "chunk_level": "mid", "chunk_index": 0},
        ),
        (
            "z1-c2",
            "second alpha text",
            0.70,
            {"source_type": "zotero_fulltext", "zotero_key": "Z1", "chunk_level": "mid", "chunk_index": 1},
        ),
    ]

    payload = aggregate_hits_by_source(
        results,
        registry,
        item_type="book",
        language="en",
        tag="Process",
    )

    assert payload["total_sources"] == 2
    assert [row["identity_value"] for row in payload["sources"]] == ["Z1", "Z2"]
    first = payload["sources"][0]
    assert first["hit_count"] == 2
    assert first["best_score"] == 0.91
    assert first["title"] == "Alpha"
    assert first["item_type"] == "book"
    assert first["abstract"] == "Alpha abstract."
    assert first["representative_chunks"][0]["chunk_id"] == "z1-c1"
    assert first["representative_chunks"][0]["snippet"] == "best alpha text"


def test_survey_hit_count_breaks_score_ties(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_chunks(
        ["z1-c1", "z1-c2", "z2-c1"],
        [
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "Z1",
                "source_id": "zotero-Z1-attachment-1",
                "chunk_level": "mid",
                "chunk_index": 0,
                "title": "Alpha",
            },
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "Z1",
                "source_id": "zotero-Z1-attachment-1",
                "chunk_level": "mid",
                "chunk_index": 1,
                "title": "Alpha",
            },
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "Z2",
                "source_id": "zotero-Z2-attachment-1",
                "chunk_level": "mid",
                "chunk_index": 0,
                "title": "Beta",
            },
        ],
    )
    registry.refresh_sources()

    payload = aggregate_hits_by_source(
        [
            ("z2-c1", "beta", 0.9, {"source_type": "zotero_fulltext", "zotero_key": "Z2"}),
            ("z1-c1", "alpha one", 0.9, {"source_type": "zotero_fulltext", "zotero_key": "Z1"}),
            ("z1-c2", "alpha two", 0.5, {"source_type": "zotero_fulltext", "zotero_key": "Z1"}),
        ],
        registry,
    )

    assert [row["identity_value"] for row in payload["sources"]] == ["Z1", "Z2"]


def test_pipeline_survey_sources_uses_mid_recall_and_returns_sources(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_chunks(
        ["z1-c1"],
        [
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "Z1",
                "source_id": "zotero-Z1-attachment-1",
                "chunk_level": "mid",
                "chunk_index": 0,
                "title": "Alpha",
            }
        ],
    )
    registry.refresh_sources()

    class Embedder:
        def embed_query(self, query):
            assert query == "process coaching"
            return [0.1, 0.2]

    class VectorStore:
        def __init__(self):
            self.calls = []

        def search(self, embedding, *, k, filter=None):
            self.calls.append({"embedding": embedding, "k": k, "filter": filter})
            return [
                (
                    "z1-c1",
                    "alpha evidence",
                    0.93,
                    {
                        "source_type": "zotero_fulltext",
                        "zotero_key": "Z1",
                        "chunk_level": "mid",
                        "chunk_index": 0,
                    },
                )
            ]

    pipeline = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipeline.config = {"retrieval": {"k_recall": 10, "telemetry": {"enabled": False}}}
    pipeline.embedder = Embedder()
    pipeline.vector_store = VectorStore()
    pipeline.registry = registry

    payload = pipeline.survey_sources("process coaching", k=5)

    assert pipeline.vector_store.calls[0]["filter"] == {"chunk_level": "mid"}
    assert payload["sources"][0]["identity_value"] == "Z1"
    assert payload["sources"][0]["representative_chunks"][0]["chunk_id"] == "z1-c1"
