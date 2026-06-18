"""Unit tests for Chroma storage helper behavior."""

from src.storage.chroma import ChromaVectorStore


def test_normalize_where_wraps_multi_field_equality_filter():
    where = {
        "zotero_key": "Z1",
        "source_type": "zotero_fulltext",
        "attachment_key": "ATT1",
    }

    assert ChromaVectorStore._normalize_where(where) == {
        "$and": [
            {"zotero_key": "Z1"},
            {"source_type": "zotero_fulltext"},
            {"attachment_key": "ATT1"},
        ]
    }


def test_normalize_where_preserves_operator_filters():
    where = {
        "$and": [
            {"zotero_key": {"$in": ["Z1", "Z2"]}},
            {"source_type": {"$in": ["zotero_note"]}},
        ]
    }

    assert ChromaVectorStore._normalize_where(where) is where


def test_sanitize_metadatas_converts_lists_and_none():
    assert ChromaVectorStore._sanitize_metadatas(
        [{"tags": ["a", "b"], "missing": None, "ok": True}]
    ) == [{"tags": "a, b", "missing": "", "ok": True}]
