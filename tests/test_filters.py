from src.retrieval.filters import build_where_filter, apply_post_filters


def test_build_where_filter_basic():
    where = build_where_filter(source_type="zotero_fulltext", zotero_key="ABC", year_min=2000, year_max=2020)
    assert where["source_type"] == "zotero_fulltext"
    assert where["zotero_key"] == "ABC"
    assert where["year"]["$gte"] == 2000
    assert where["year"]["$lte"] == 2020


def test_apply_post_filters_author_and_title():
    results = [
        ("id1", "t", 1.0, {"authors": "Maurice Merleau-Ponty", "title": "Phenomenology of Perception"}),
        ("id2", "t", 1.0, {"authors": "Deleuze", "title": "Difference and Repetition"}),
    ]
    out = apply_post_filters(results, author_contains="merleau", title_contains="perception")
    assert [r[0] for r in out] == ["id1"]
