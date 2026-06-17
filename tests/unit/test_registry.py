"""Tests for the source registry and its checkpointed backfill."""

from pathlib import Path

import pytest

from src.registry import (
    SourceRegistry,
    backfill_from_collection,
    registry_path_for,
    source_identity_for_metadata,
)
from src.registry_audit import audit_duplicates


class _FakeCollection:
    """Minimal Chroma-like collection supporting paged metadata gets."""

    def __init__(self, records, fail_after_gets=None):
        self.records = list(records)
        self.get_calls = 0
        self.fail_after_gets = fail_after_gets

    def count(self):
        return len(self.records)

    def get(self, ids=None, where=None, include=None, limit=None, offset=None):
        self.get_calls += 1
        if self.fail_after_gets is not None and self.get_calls > self.fail_after_gets:
            raise KeyboardInterrupt("simulated interruption")
        records = self.records
        if offset is not None:
            records = records[offset:]
        if limit is not None:
            records = records[:limit]
        return {
            "ids": [record["id"] for record in records],
            "metadatas": [record["metadata"] for record in records],
        }


def _zotero_chunk(chunk_id, key, level="mid", ordinal=0, **extra):
    metadata = {
        "source_type": "zotero_fulltext",
        "zotero_key": key,
        "source_id": f"zotero-{key}-attachment-1",
        "chunk_level": level,
        "chunk_index": ordinal,
        "title": f"Title {key}",
        "authors": f"Author {key}",
    }
    metadata.update(extra)
    return {"id": chunk_id, "metadata": metadata}


def test_source_identity_rule():
    assert source_identity_for_metadata(
        {"source_type": "zotero_fulltext", "zotero_key": "Z1", "source_id": "x"}
    ) == ("zotero_key", "Z1")
    assert source_identity_for_metadata(
        {"source_type": "obsidian", "source_id": "obsidian-A.md"}
    ) == ("source_id", "obsidian-A.md")
    assert source_identity_for_metadata({}) == ("source_id", None)


def test_registry_path_is_scoped_per_collection(tmp_path):
    config = {
        "output_folder": str(tmp_path),
        "storage": {"collection_name": "my collection!"},
    }
    assert registry_path_for(config) == tmp_path / "registry.my_collection.sqlite"


def test_record_refresh_and_list_roundtrip(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_chunks(
        ["c1", "c2", "c3"],
        [
            _zotero_chunk("c1", "Z1", level="mid", ordinal=0, indexed_at="2026-06-01T00:00:00Z")["metadata"],
            _zotero_chunk("c2", "Z1", level="fine", ordinal=1)["metadata"],
            {
                "source_type": "obsidian",
                "source_id": "obsidian-A.md",
                "chunk_level": "mid",
                "chunk_index": 0,
                "title": "A Note",
            },
        ],
    )
    registry.refresh_sources()

    payload = registry.list_sources_payload()
    assert payload["total_sources"] == 2
    titles = [source["title"] for source in payload["sources"]]
    assert titles == sorted(titles, key=str.lower)

    zotero_row = next(
        source for source in payload["sources"] if source["identity_value"] == "Z1"
    )
    assert zotero_row["identity_field"] == "zotero_key"
    assert zotero_row["chunk_counts"] == {"mid": 1, "fine": 1}
    assert zotero_row["total_chunks"] == 2
    assert zotero_row["freshness"] == "2026-06-01T00:00:00Z"


def test_record_chunks_is_idempotent(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    ids = ["c1", "c2"]
    metadatas = [
        _zotero_chunk("c1", "Z1", ordinal=0)["metadata"],
        _zotero_chunk("c2", "Z1", ordinal=1)["metadata"],
    ]
    registry.record_chunks(ids, metadatas)
    registry.record_chunks(ids, metadatas)
    assert registry.chunk_count() == 2


def test_delete_source_chunks_removes_source(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_chunks(
        ["c1", "c2"],
        [
            _zotero_chunk("c1", "Z1")["metadata"],
            _zotero_chunk("c2", "Z2")["metadata"],
        ],
    )
    registry.refresh_sources()

    registry.delete_source_chunks("zotero_key", "Z1")
    registry.refresh_sources()

    payload = registry.list_sources_payload()
    assert payload["total_sources"] == 1
    assert payload["sources"][0]["identity_value"] == "Z2"
    assert registry.chunk_count() == 1


def test_attrs_improve_from_placeholder(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_chunks(
        ["c1"],
        [_zotero_chunk("c1", "Z1", title="Untitled", authors="Unknown")["metadata"]
         | {"title": "Untitled", "authors": "Unknown"}],
    )
    registry.record_chunks(
        ["c2"],
        [_zotero_chunk("c2", "Z1", ordinal=1)["metadata"]
         | {"title": "Real Title", "authors": "Real Author"}],
    )
    registry.refresh_sources()

    row = registry.list_sources_payload()["sources"][0]
    assert row["title"] == "Real Title"
    assert row["authors"] == "Real Author"


def test_backfill_full_scan(tmp_path):
    records = [_zotero_chunk(f"c{i}", f"Z{i % 3}", ordinal=i) for i in range(25)]
    collection = _FakeCollection(records)
    registry = SourceRegistry(tmp_path / "r.sqlite")

    result = backfill_from_collection(
        registry, collection, batch_size=10, progress=None
    )

    assert result["skipped"] is False
    assert registry.chunk_count() == 25
    assert registry.source_count() == 3
    assert registry.get_meta("backfill_complete") == "1"


def test_backfill_resumes_from_checkpoint(tmp_path):
    records = [_zotero_chunk(f"c{i}", f"Z{i % 3}", ordinal=i) for i in range(25)]
    registry = SourceRegistry(tmp_path / "r.sqlite")

    # First run dies after two get() calls (20 chunks committed).
    flaky = _FakeCollection(records, fail_after_gets=2)
    with pytest.raises(KeyboardInterrupt):
        backfill_from_collection(registry, flaky, batch_size=10, progress=None)

    assert registry.get_meta("backfill_complete") != "1"
    committed = registry.chunk_count()
    assert 0 < committed < 25
    assert int(registry.get_meta("backfill_offset")) == committed

    # Second run resumes from the committed offset and completes.
    healthy = _FakeCollection(records)
    result = backfill_from_collection(
        registry, healthy, batch_size=10, progress=None
    )

    assert result["skipped"] is False
    assert registry.chunk_count() == 25
    assert registry.source_count() == 3
    assert registry.get_meta("backfill_complete") == "1"
    # Resume must not have re-scanned from zero.
    assert result["chunks_recorded"] == 25 - committed


def test_backfill_skips_when_complete_and_restart_rebuilds(tmp_path):
    records = [_zotero_chunk(f"c{i}", "Z1", ordinal=i) for i in range(5)]
    collection = _FakeCollection(records)
    registry = SourceRegistry(tmp_path / "r.sqlite")

    backfill_from_collection(registry, collection, batch_size=10, progress=None)
    skipped = backfill_from_collection(registry, collection, batch_size=10, progress=None)
    assert skipped["skipped"] is True

    restarted = backfill_from_collection(
        registry, collection, batch_size=10, restart=True, progress=None
    )
    assert restarted["skipped"] is False
    assert registry.chunk_count() == 5


def test_audit_duplicates_detects_double_slots(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    base = _zotero_chunk("dup-a", "Z1", ordinal=0)["metadata"]
    registry.record_chunks(
        ["dup-a", "dup-b", "ok-1"],
        [
            dict(base),
            dict(base),  # same (source_id, level, ordinal, variant) -> duplicate slot
            _zotero_chunk("ok-1", "Z1", ordinal=1)["metadata"],
        ],
    )

    report = audit_duplicates(registry)
    assert report["duplicate_slots"] == 1
    assert report["extra_chunks"] == 1
    assert report["affected_sources"] == 1


def _unit(unit_id, key, kind, fingerprint, **extra):
    unit = {
        "unit_id": unit_id,
        "identity_field": "zotero_key",
        "identity_value": key,
        "unit_kind": kind,
        "source_fingerprint": fingerprint,
    }
    unit.update(extra)
    return unit


def test_ledger_roundtrip_and_upsert(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    assert registry.get_unit_states() == {}

    n = registry.record_unit_states(
        [
            _unit("zotero-Z1-attachment-1", "Z1", "attachment", "hashA"),
            _unit("zotero-Z1-note-9", "Z1", "note", "2026-06-01"),
        ]
    )
    assert n == 2
    assert registry.get_unit_states() == {
        "zotero-Z1-attachment-1": "hashA",
        "zotero-Z1-note-9": "2026-06-01",
    }

    # Re-recording the same unit_id with a new fingerprint updates in place.
    registry.record_unit_states([_unit("zotero-Z1-attachment-1", "Z1", "attachment", "hashB")])
    states = registry.get_unit_states()
    assert states["zotero-Z1-attachment-1"] == "hashB"
    assert len(states) == 2


def test_ledger_meta_cursor_roundtrip(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_unit_states(
        [_unit("zotero-Z1-note-1", "Z1", "note", "v1")],
        meta_updates={"zotero_item_version": "4321"},
    )
    assert registry.get_meta("zotero_item_version") == "4321"


def test_delete_units_and_for_source(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_unit_states(
        [
            _unit("zotero-Z1-attachment-1", "Z1", "attachment", "h1"),
            _unit("zotero-Z1-note-9", "Z1", "note", "n9"),
            _unit("zotero-Z2-attachment-1", "Z2", "attachment", "h2"),
        ]
    )

    assert registry.delete_units(["zotero-Z1-note-9", "missing-id"]) == 1
    assert "zotero-Z1-note-9" not in registry.get_unit_states()

    assert registry.delete_units_for_source("zotero_key", "Z1") == 1
    remaining = registry.get_unit_states()
    assert set(remaining) == {"zotero-Z2-attachment-1"}


def test_delete_source_chunks_also_clears_ledger(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_chunks(["c1"], [_zotero_chunk("c1", "Z1")["metadata"]])
    registry.record_unit_states([_unit("zotero-Z1-attachment-1", "Z1", "attachment", "h1")])

    registry.delete_source_chunks("zotero_key", "Z1")
    assert registry.get_unit_states() == {}


def test_reset_clears_ledger(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_unit_states([_unit("zotero-Z1-note-1", "Z1", "note", "v1")])
    registry.reset()
    assert registry.get_unit_states() == {}


def test_additive_migration_adds_ledger_to_v1_registry(tmp_path):
    """An existing v1 registry (no index_units) gains the table on re-open."""
    import sqlite3

    db_path = tmp_path / "r.sqlite"
    SourceRegistry(db_path)  # creates current schema

    # Simulate a pre-ledger (v1) database: drop the table, mark schema_version=1.
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute("DROP TABLE index_units")
        conn.execute("UPDATE meta SET value='1' WHERE key='schema_version'")

    # Re-opening runs the additive migration.
    reopened = SourceRegistry(db_path)
    assert reopened.get_meta("schema_version") == "3"
    # Round-trips, proving the table is back.
    reopened.record_unit_states([_unit("zotero-Z1-note-1", "Z1", "note", "v1")])
    assert reopened.get_unit_states() == {"zotero-Z1-note-1": "v1"}


def test_registry_records_and_deletes_child_key_chunks(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_chunks(
        ["a1", "a2", "n1"],
        [
            _zotero_chunk("a1", "Z1", source_id="zotero-1-attachment-10")["metadata"]
            | {"attachment_key": "ATT1"},
            _zotero_chunk("a2", "Z1", source_id="zotero-1-attachment-11")["metadata"]
            | {"attachment_key": "ATT2"},
            {
                "source_type": "zotero_note",
                "zotero_key": "Z1",
                "source_id": "zotero-1-note-20",
                "chunk_level": "atomic",
                "chunk_index": 0,
                "note_key": "NOTE1",
            },
        ],
    )

    rows = registry.chunk_records_for_source("zotero_key", "Z1")
    assert {row["chunk_id"]: row["attachment_key"] for row in rows}["a1"] == "ATT1"

    deleted = registry.delete_chunks_matching(
        "zotero_key",
        "Z1",
        source_types=["zotero_fulltext"],
        attachment_key="ATT1",
    )

    assert deleted == 1
    remaining = {row["chunk_id"] for row in registry.chunk_records_for_source("zotero_key", "Z1")}
    assert remaining == {"a2", "n1"}


def test_list_sources_collection_filter(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    in_process = _zotero_chunk("c1", "Z1")["metadata"]
    in_process["collections"] = ["Process", "Theory"]
    other = _zotero_chunk("c2", "Z2")["metadata"]
    other["collections"] = "Methods"
    registry.record_chunks(["c1", "c2"], [in_process, other])
    registry.refresh_sources()

    scoped = registry.list_sources_payload(collection="process")
    assert scoped["total_sources"] == 1
    assert scoped["sources"][0]["identity_value"] == "Z1"
    assert scoped["sources"][0]["collections"] == "Process, Theory"

    unscoped = registry.list_sources_payload()
    assert unscoped["total_sources"] == 2
