from src.retrieval.diversity import apply_diversity


def test_apply_diversity_limits_per_source_id():
    results = []
    for i in range(5):
        results.append((
            f"id{i}",
            "txt",
            1.0,
            {"source_id": "A"},
        ))
    out = apply_diversity(results, max_per_key=2)
    assert len(out) == 2


def test_apply_diversity_preserves_order_and_allows_multiple_sources():
    results = [
        ("id1", "t", 1.0, {"source_id": "A"}),
        ("id2", "t", 0.9, {"source_id": "B"}),
        ("id3", "t", 0.8, {"source_id": "A"}),
        ("id4", "t", 0.7, {"source_id": "A"}),
        ("id5", "t", 0.6, {"source_id": "B"}),
    ]
    out = apply_diversity(results, max_per_key=2)
    assert [r[0] for r in out] == ["id1", "id2", "id3", "id5"]


def test_apply_diversity_fallback_key_title():
    results = [
        ("id1", "t", 1.0, {"title": "X"}),
        ("id2", "t", 0.9, {"title": "X"}),
        ("id3", "t", 0.8, {"title": "X"}),
    ]
    out = apply_diversity(results, key_priority=["source_id", "title"], max_per_key=1)
    assert [r[0] for r in out] == ["id1"]
