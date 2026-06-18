"""Tests for the index-ledger reconciliation planner (P2)."""

import sqlite3

from src.reconcile import build_work_plan, reconcile
from src.pipeline import ResearchRAGPipeline
from src.registry import SourceRegistry
from src.sources.base import UnitState
from src.sources.zotero import ZoteroSource


def _state(unit_id, fingerprint="v1", identity_value="Z1", kind="note"):
    return UnitState(
        unit_id=unit_id,
        identity_field="zotero_key",
        identity_value=identity_value,
        unit_kind=kind,
        fingerprint=fingerprint,
    )


def test_reconcile_create_only():
    world = {"zotero:Z1:note:N1": _state("zotero:Z1:note:N1")}

    plan = reconcile(world, {})

    assert plan.creates == [world["zotero:Z1:note:N1"]]
    assert plan.updates == []
    assert plan.deletes == []
    assert plan.unchanged == 0
    assert plan.is_empty() is False


def test_reconcile_update_only():
    state = _state("zotero:Z1:note:N1", fingerprint="v2")

    plan = reconcile({"zotero:Z1:note:N1": state}, {"zotero:Z1:note:N1": "v1"})

    assert plan.creates == []
    assert plan.updates == [state]
    assert plan.deletes == []
    assert plan.unchanged == 0


def test_reconcile_delete_only():
    plan = reconcile({}, {"zotero:Z1:note:N1": "v1"})

    assert plan.creates == []
    assert plan.updates == []
    assert plan.deletes == ["zotero:Z1:note:N1"]
    assert plan.unchanged == 0


def test_reconcile_noop():
    state = _state("zotero:Z1:note:N1")

    plan = reconcile({"zotero:Z1:note:N1": state}, {"zotero:Z1:note:N1": "v1"})

    assert plan.is_empty() is True
    assert plan.unchanged == 1


def test_reconcile_mixed_and_touched_identities():
    unchanged = _state("zotero:Z1:note:N1", identity_value="Z1")
    updated = _state("zotero:Z2:note:N2", fingerprint="v2", identity_value="Z2")
    created = _state("zotero:Z3:note:N3", identity_value="Z3")
    world = {
        unchanged.unit_id: unchanged,
        updated.unit_id: updated,
        created.unit_id: created,
    }
    ledger = {
        unchanged.unit_id: "v1",
        updated.unit_id: "v1",
        "zotero:Z4:note:N4": "v1",
    }

    plan = reconcile(world, ledger)

    assert plan.creates == [created]
    assert plan.updates == [updated]
    assert plan.deletes == ["zotero:Z4:note:N4"]
    assert plan.unchanged == 1
    assert plan.touched_identities() == {("zotero_key", "Z2"), ("zotero_key", "Z3")}


def test_reconcile_empty_world_full_ledger_all_deletes():
    plan = reconcile({}, {"a": "1", "b": "2"})

    assert plan.creates == []
    assert plan.updates == []
    assert plan.deletes == ["a", "b"]
    assert plan.unchanged == 0


def test_reconcile_full_world_empty_ledger_all_creates():
    world = {"a": _state("a"), "b": _state("b")}

    plan = reconcile(world, {})

    assert plan.creates == [world["a"], world["b"]]
    assert plan.updates == []
    assert plan.deletes == []
    assert plan.unchanged == 0


class _FakeSource:
    def __init__(self, enabled, state):
        self._enabled = enabled
        self._state = state

    def is_enabled(self):
        return self._enabled

    def enumerate_state(self):
        return self._state


def test_build_work_plan_merges_enabled_sources(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_unit_states(
        [
            {
                "unit_id": "zotero:Z1:note:N1",
                "identity_field": "zotero_key",
                "identity_value": "Z1",
                "unit_kind": "note",
                "source_fingerprint": "v1",
            }
        ]
    )
    enabled = _FakeSource(True, {"zotero:Z1:note:N1": _state("zotero:Z1:note:N1", "v2")})
    disabled = _FakeSource(False, {"zotero:Z2:note:N2": _state("zotero:Z2:note:N2")})

    plan = build_work_plan([enabled, disabled], registry)

    assert [unit.unit_id for unit in plan.updates] == ["zotero:Z1:note:N1"]
    assert plan.creates == []


def test_pipeline_ledger_shadow_logs_parity_and_deletes(tmp_path, capsys):
    pipeline = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipeline.config = {"indexing": {"ledger": {"shadow": True}}}
    pipeline.registry = SourceRegistry(tmp_path / "r.sqlite")
    pipeline.registry.record_unit_states(
        [
            {
                "unit_id": "zotero:Z1:note:N1",
                "identity_field": "zotero_key",
                "identity_value": "Z1",
                "unit_kind": "note",
                "source_fingerprint": "v1",
            },
            {
                "unit_id": "zotero:Z2:note:N2",
                "identity_field": "zotero_key",
                "identity_value": "Z2",
                "unit_kind": "note",
                "source_fingerprint": "v1",
            },
            {
                "unit_id": "zotero:Z3:note:N3",
                "identity_field": "zotero_key",
                "identity_value": "Z3",
                "unit_kind": "note",
                "source_fingerprint": "v1",
            },
        ]
    )
    pipeline.sources = [
        _FakeSource(
            True,
            {
                "zotero:Z1:note:N1": _state("zotero:Z1:note:N1", "v1", "Z1"),
                "zotero:Z2:note:N2": _state("zotero:Z2:note:N2", "v2", "Z2"),
            },
        )
    ]

    pipeline._run_ledger_shadow({"changed_item_keys": ["Z2", "Z3"]})

    out = capsys.readouterr().out
    assert "Ledger shadow: 0 creates, 1 updates, 1 deletes, 1 unchanged units" in out
    assert "Zotero modify/create parent set matches current delta path" in out
    assert "Ledger shadow deletions: 1 Zotero parent(s) absent from world" in out


def _zotero_db(tmp_path):
    data_dir = tmp_path / "Zotero"
    storage = data_dir / "storage"
    storage.mkdir(parents=True)
    db_path = data_dir / "zotero.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INTEGER,
                            dateAdded TEXT, dateModified TEXT, key TEXT);
        CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TEXT);
        CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INTEGER,
                                      path TEXT, contentType TEXT,
                                      storageModTime INTEGER, storageHash TEXT);
        CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INTEGER, note TEXT);
        CREATE TABLE itemAnnotations (itemID INTEGER PRIMARY KEY, parentItemID INTEGER,
                                      text TEXT, comment TEXT);
        INSERT INTO itemTypes VALUES (1,'book'),(2,'attachment'),(3,'annotation'),(4,'note');

        INSERT INTO items VALUES (10, 1, '', '2026-01-01 00:00:00', 'P1');
        INSERT INTO items VALUES (20, 2, '', '2026-01-01 00:00:00', 'ATT_HASH');
        INSERT INTO items VALUES (30, 3, '', '2026-01-01 00:00:00', 'ANN1');
        INSERT INTO items VALUES (40, 4, '', '2026-01-02 00:00:00', 'NOTE1');
        INSERT INTO itemAttachments VALUES (20, 10, 'storage:doc.pdf', 'application/pdf', 111, 'deadbeef');
        INSERT INTO itemAnnotations VALUES (30, 20, 'hi', 'cmt');
        INSERT INTO itemNotes VALUES (40, 10, '<p>n</p>');

        INSERT INTO items VALUES (11, 1, '', '2026-01-03 00:00:00', 'P2');
        INSERT INTO items VALUES (21, 2, '', '2026-01-03 00:00:00', 'ATT_FILE');
        INSERT INTO itemAttachments VALUES (21, 11, 'storage:present.pdf', 'application/pdf', NULL, NULL);
        """
    )
    conn.commit()
    conn.close()

    attachment = storage / "ATT_FILE" / "present.pdf"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"hello world")
    return data_dir


def _source(data_dir):
    return ZoteroSource(
        {"zotero": {"enabled": True, "data_directory": str(data_dir)}}
    )


def _ledger_row(unit):
    return {
        "unit_id": unit.unit_id,
        "identity_field": unit.identity_field,
        "identity_value": unit.identity_value,
        "unit_kind": unit.unit_kind,
        "source_fingerprint": unit.fingerprint,
    }


def _zotero_parent_from_unit_id(unit_id):
    parts = unit_id.split(":")
    if len(parts) >= 3 and parts[0] == "zotero":
        return parts[1]
    return None


def test_reconcile_shadow_parity_matches_existing_sqlite_delta_for_modifications(tmp_path):
    data_dir = _zotero_db(tmp_path)
    source = _source(data_dir)
    initial_world = source.enumerate_state()
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_unit_states([_ledger_row(unit) for unit in initial_world.values()])

    conn = sqlite3.connect(data_dir / "zotero.sqlite")
    conn.execute(
        "UPDATE items SET dateModified='2026-06-17 12:00:00' WHERE key='NOTE1'"
    )
    conn.execute("INSERT INTO deletedItems VALUES (11, '2026-06-17 12:30:00')")
    conn.commit()
    conn.close()

    plan = build_work_plan([source], registry)
    ledger_parent_changes = {
        value
        for field, value in plan.touched_identities()
        if field == "zotero_key"
    }
    (
        changed_keys,
        _max_modified,
        deleted_keys,
        _max_deleted,
        attachment_keys,
        _max_storage,
    ) = source._fetch_changed_parent_item_keys_sqlite(
        "2026-01-03 00:00:00",
        "2026-01-03 00:00:00",
        111,
    )
    sqlite_modified_changes = set(changed_keys) | set(attachment_keys)
    ledger_deleted_parents = {
        parent
        for parent in (_zotero_parent_from_unit_id(unit_id) for unit_id in plan.deletes)
        if parent
    }

    assert ledger_parent_changes == sqlite_modified_changes
    assert set(deleted_keys) <= ledger_deleted_parents
