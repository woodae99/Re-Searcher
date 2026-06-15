"""Hermetic tests for the retrieval-eval metrics (fake search_fn, no embeddings)."""

from src.retrieval_eval import EvalProbe, compare_configs, evaluate


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
