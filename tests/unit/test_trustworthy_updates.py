"""Phase 2 tests: trustworthy updates.

Covers Obsidian per-file delta + deletes, version-keyed progress, batched
Zotero deletes (no >N skip), and the /deleted handling helpers.
"""

import sqlite3
from pathlib import Path

from src.indexing import DocumentStatus, IndexingProgress
from src.pipeline import ResearchRAGPipeline
from src.registry import SourceRegistry
from src.sources.obsidian import ObsidianSource
from src.sources import zotero as zotero_module
from src.sources.zotero import ZoteroSource


class _FakeVectorStore:
    def __init__(self):
        self.deletes = []

    def delete_where(self, where):
        self.deletes.append(where)


def _make_pipeline(tmp_path, vault_path=None):
    """Bare pipeline instance with just the attributes the update logic needs."""
    pipeline = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipeline.config = {
        "indexing": {"delta": {"enabled": True, "delete_batch_size": 2}},
        "obsidian": {"enabled": True, "vault_path": str(vault_path)} if vault_path else {},
    }
    pipeline.vector_store = _FakeVectorStore()
    pipeline.registry = SourceRegistry(tmp_path / "registry.sqlite")
    pipeline.progress = IndexingProgress(tmp_path / "progress.json")
    if vault_path:
        pipeline.sources = [ObsidianSource(pipeline.config)]
    else:
        pipeline.sources = []
    return pipeline


def _make_vault(tmp_path, files):
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    for name, content in files.items():
        path = vault / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return vault


# ---------------------------------------------------------------- deletes


def test_zotero_deletes_are_batched_with_no_key_limit(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    keys = [f"KEY{i}" for i in range(5)]  # batch size 2 -> 3 delete calls

    pipeline._delete_existing_zotero_chunks(keys)

    assert len(pipeline.vector_store.deletes) == 3
    first = pipeline.vector_store.deletes[0]
    assert first["$and"][0] == {"zotero_key": {"$in": ["KEY0", "KEY1"]}}
    # the source_type guard keeps citing Obsidian notes out of the deletion
    assert "$in" in first["$and"][1]["source_type"]
    all_deleted = [
        key
        for where in pipeline.vector_store.deletes
        for key in where["$and"][0]["zotero_key"]["$in"]
    ]
    assert all_deleted == keys


def test_obsidian_delete_clears_store_registry_progress_and_state(tmp_path):
    pipeline = _make_pipeline(tmp_path)
    source_id = "obsidian-Notes/gone.md"
    pipeline.registry.record_chunks(
        ["c1"],
        [{"source_type": "obsidian", "source_id": source_id, "chunk_level": "mid", "chunk_index": 0}],
    )
    pipeline.registry.set_vault_state_entries({"Notes/gone.md": (1.0, 10)})
    pipeline.progress.set_document_status(source_id, DocumentStatus.STORED)

    pipeline._delete_obsidian_sources(["Notes/gone.md"], removing=True)

    assert pipeline.vector_store.deletes == [{"source_id": source_id}]
    assert pipeline.registry.chunk_count() == 0
    assert pipeline.progress.get_status(source_id) is None
    assert pipeline.registry.get_vault_state() == {}


# ------------------------------------------------------------- vault delta


def test_obsidian_delta_detects_new_changed_deleted(tmp_path):
    vault = _make_vault(tmp_path, {"a.md": "alpha", "b.md": "beta"})
    pipeline = _make_pipeline(tmp_path, vault_path=vault)

    source = pipeline.sources[0]
    disk = source.get_file_states()
    # Snapshot taken when a.md and a now-deleted c.md were current; b.md changed since.
    pipeline.registry.set_vault_state_entries(
        {
            "a.md": disk["a.md"],
            "b.md": (disk["b.md"][0] - 100, 1),  # stale state -> changed
            "c.md": (1.0, 5),  # not on disk -> deleted
        }
    )

    delta = pipeline._collect_obsidian_delta_changes()

    assert delta["bootstrap"] is False
    assert delta["changed"] == ["b.md"]
    assert delta["deleted"] == ["c.md"]


def test_obsidian_delta_bootstrap_uses_registry_freshness(tmp_path):
    vault = _make_vault(tmp_path, {"new.md": "n", "stale.md": "s", "current.md": "c"})
    pipeline = _make_pipeline(tmp_path, vault_path=vault)

    # No vault snapshot; registry knows two notes with indexed_at stamps.
    pipeline.registry.record_chunks(
        ["s1", "c1", "g1"],
        [
            {
                "source_type": "obsidian",
                "source_id": "obsidian-stale.md",
                "chunk_level": "mid",
                "chunk_index": 0,
                "indexed_at": "2000-01-01T00:00:00Z",  # long before file mtime
            },
            {
                "source_type": "obsidian",
                "source_id": "obsidian-current.md",
                "chunk_level": "mid",
                "chunk_index": 0,
                "indexed_at": "2999-01-01T00:00:00Z",  # after file mtime
            },
            {
                "source_type": "obsidian",
                "source_id": "obsidian-ghost.md",  # not on disk
                "chunk_level": "mid",
                "chunk_index": 0,
                "indexed_at": "2000-01-01T00:00:00Z",
            },
        ],
    )
    pipeline.registry.refresh_sources()

    delta = pipeline._collect_obsidian_delta_changes()

    assert delta["bootstrap"] is True
    assert delta["changed"] == ["new.md", "stale.md"]
    assert delta["deleted"] == ["ghost.md"]


def test_persist_vault_state_only_records_stored_changed_files(tmp_path):
    vault = _make_vault(tmp_path, {"done.md": "d", "failed.md": "f", "same.md": "s"})
    pipeline = _make_pipeline(tmp_path, vault_path=vault)
    source = pipeline.sources[0]
    disk = source.get_file_states()

    # done.md stored at the current version; failed.md errored.
    pipeline.progress.set_document_status(
        "obsidian-done.md",
        DocumentStatus.STORED,
        content_version=ObsidianSource.content_version_for_state(disk["done.md"]),
    )
    pipeline.progress.set_document_status(
        "obsidian-failed.md", DocumentStatus.ERROR, error_msg="boom"
    )

    delta = {
        "changed": ["done.md", "failed.md"],
        "deleted": [],
        "disk": disk,
        "bootstrap": False,
    }
    pipeline._persist_vault_state(delta)

    state = pipeline.registry.get_vault_state()
    assert "done.md" in state
    assert "same.md" in state  # unchanged files are seeded
    assert "failed.md" not in state  # must stay 'changed' for the next run


# ------------------------------------------------------ version-keyed progress


def test_version_keyed_progress_detects_changed_content(tmp_path):
    progress = IndexingProgress(tmp_path / "p.json")
    progress.set_document_status("doc", DocumentStatus.STORED, content_version="v1")

    assert progress.has_completed_status("doc", "v1") is True
    assert progress.has_completed_status("doc", "v2") is False  # changed -> reprocess
    assert progress.has_completed_status("doc") is True  # caller without version

    # Legacy records without a version are trusted (no mass re-index on upgrade)
    progress.set_document_status("legacy", DocumentStatus.STORED)
    assert progress.has_completed_status("legacy", "v9") is True


# ------------------------------------------------------------ zotero /deleted


class _FakeResponse:
    def __init__(self, payload, version="42"):
        self.status_code = 200
        self._payload = payload
        self.headers = {"Last-Modified-Version": version}

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def _zotero_source():
    return ZoteroSource({"zotero": {"enabled": False}})


def test_fetch_deleted_item_keys_uses_deleted_endpoint(monkeypatch):
    calls = []

    def fake_get(url, params=None, headers=None, timeout=None):
        calls.append((url, params))
        return _FakeResponse({"items": ["DEAD1", "DEAD2"], "collections": ["X"]})

    monkeypatch.setattr(zotero_module.requests, "get", fake_get)
    source = _zotero_source()

    keys = source._fetch_deleted_item_keys(100)

    assert keys == ["DEAD1", "DEAD2"]
    assert calls[0][0].endswith("/deleted")
    assert calls[0][1] == {"since": 100}


def test_fetch_deleted_item_keys_skips_bootstrap(monkeypatch):
    def fail_get(*args, **kwargs):
        raise AssertionError("must not call the API on bootstrap")

    monkeypatch.setattr(zotero_module.requests, "get", fail_get)
    assert _zotero_source()._fetch_deleted_item_keys(0) == []


def test_resolve_parent_keys_retains_purged_keys(tmp_path):
    """Keys absent from zotero.sqlite (trash emptied) must survive resolution
    so their chunks can still be deleted from the index."""
    db_path = tmp_path / "zotero.sqlite"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, key TEXT);
        CREATE TABLE itemAttachments (itemID INTEGER, parentItemID INTEGER);
        CREATE TABLE itemNotes (itemID INTEGER, parentItemID INTEGER);
        CREATE TABLE itemAnnotations (itemID INTEGER, parentItemID INTEGER);
        INSERT INTO items VALUES (1, 'PARENT'), (2, 'CHILD');
        INSERT INTO itemAttachments VALUES (2, 1);
        """
    )
    conn.commit()
    conn.close()

    source = ZoteroSource(
        {"zotero": {"enabled": True, "data_directory": str(tmp_path)}}
    )
    # data dir exists but has no storage folder; that's fine for this method
    resolved = source._resolve_parent_keys_for_any_item_keys(["CHILD", "GONE"])

    assert "PARENT" in resolved  # child resolved to its parent
    assert "GONE" in resolved  # purged key retained for chunk deletion
    assert "CHILD" not in resolved


# ------------------------------------------- annotation identity attribution


def _zotero_fixture_db(tmp_path):
    db_path = tmp_path / "zfix" / "zotero.sqlite"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE itemTypes (itemTypeID INTEGER PRIMARY KEY, typeName TEXT);
        CREATE TABLE items (itemID INTEGER PRIMARY KEY, itemTypeID INTEGER,
                            dateAdded TEXT, dateModified TEXT, key TEXT);
        CREATE TABLE deletedItems (itemID INTEGER PRIMARY KEY, dateDeleted TEXT);
        CREATE TABLE itemAttachments (itemID INTEGER PRIMARY KEY, parentItemID INTEGER,
                                      path TEXT, contentType TEXT);
        CREATE TABLE itemNotes (itemID INTEGER PRIMARY KEY, parentItemID INTEGER, note TEXT);
        CREATE TABLE itemAnnotations (itemID INTEGER PRIMARY KEY, parentItemID INTEGER,
                                      text TEXT, comment TEXT, sortIndex TEXT, pageLabel TEXT);
        CREATE TABLE itemData (itemID INTEGER, fieldID INTEGER, valueID INTEGER);
        CREATE TABLE itemDataValues (valueID INTEGER PRIMARY KEY, value TEXT);
        CREATE TABLE fields (fieldID INTEGER PRIMARY KEY, fieldName TEXT);
        CREATE TABLE itemCreators (itemID INTEGER, creatorID INTEGER, orderIndex INTEGER);
        CREATE TABLE creators (creatorID INTEGER PRIMARY KEY, firstName TEXT, lastName TEXT);
        CREATE TABLE itemTags (itemID INTEGER, tagID INTEGER);
        CREATE TABLE tags (tagID INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE collectionItems (collectionID INTEGER, itemID INTEGER);
        CREATE TABLE collections (collectionID INTEGER PRIMARY KEY, collectionName TEXT);
        INSERT INTO itemTypes VALUES (1, 'book'), (2, 'attachment'), (3, 'annotation'), (4, 'note');
        -- top item 10 (PARENT) with attachment 20 (ATTACH) carrying annotation 30
        INSERT INTO items VALUES (10, 1, '', '2026-01-01 00:00:00', 'PARENT');
        INSERT INTO items VALUES (20, 2, '', '2026-01-01 00:00:00', 'ATTACH');
        INSERT INTO items VALUES (30, 3, '', '2026-01-01 00:00:00', 'ANNOT');
        INSERT INTO items VALUES (40, 4, '', '2026-01-01 00:00:00', 'NOTEKEY');
        INSERT INTO fields VALUES
            (1, 'title'),
            (2, 'DOI'),
            (3, 'abstractNote'),
            (4, 'publicationTitle'),
            (5, 'language'),
            (6, 'date');
        INSERT INTO itemDataValues VALUES
            (1, 'The Parent Book'),
            (2, '10.1234/example'),
            (3, 'A useful abstract.'),
            (4, 'Coaching Studies'),
            (5, 'en'),
            (6, '2026');
        INSERT INTO itemData VALUES
            (10, 1, 1),
            (10, 2, 2),
            (10, 3, 3),
            (10, 4, 4),
            (10, 5, 5),
            (10, 6, 6);
        INSERT INTO tags VALUES (1, 'Process');
        INSERT INTO itemTags VALUES (10, 1);
        INSERT INTO itemAttachments VALUES (20, 10, 'storage:test.pdf', 'application/pdf');
        INSERT INTO itemAnnotations VALUES (30, 20, 'highlighted text', 'my comment', '0001', '12');
        INSERT INTO itemNotes VALUES (40, 10, '<p>note text</p>');
        """
    )
    conn.commit()
    conn.close()
    return db_path


def test_get_all_items_returns_only_top_level(tmp_path):
    db_path = _zotero_fixture_db(tmp_path)
    source = ZoteroSource({"zotero": {"enabled": True, "data_directory": str(db_path.parent)}})
    conn = source._get_db_connection()
    try:
        rows = source._get_all_items(conn)
    finally:
        conn.close()

    keys = [row["key"] for row in rows]
    assert keys == ["PARENT"]  # attachment/annotation rows are not items to process


def test_annotations_attributed_to_parent_item_key(tmp_path):
    db_path = _zotero_fixture_db(tmp_path)
    source = ZoteroSource({"zotero": {"enabled": True, "data_directory": str(db_path.parent)}})
    conn = source._get_db_connection()
    try:
        docs = list(
            source._process_annotations(
                conn, 10, {"source_type": "zotero", "zotero_key": "PARENT"}
            )
        )
    finally:
        conn.close()

    assert len(docs) == 1
    doc = docs[0]
    assert doc.metadata["zotero_key"] == "PARENT"  # NOT the attachment's key
    assert doc.metadata["source_type"] == "zotero_annotation"
    assert doc.metadata["annotation_key"] == "ANNOT"
    assert doc.metadata["has_comment"] is True
    assert "highlighted text" in doc.content
    assert doc.doc_id == "zotero-10-annotation-30"


def test_item_metadata_includes_selection_fields(tmp_path):
    db_path = _zotero_fixture_db(tmp_path)
    source = ZoteroSource({"zotero": {"enabled": True, "data_directory": str(db_path.parent)}})
    conn = source._get_db_connection()
    try:
        metadata = source._get_item_metadata(conn, 10)
    finally:
        conn.close()

    assert metadata["item_type"] == "book"
    assert metadata["DOI"] == "10.1234/example"
    assert metadata["abstractNote"] == "A useful abstract."
    assert metadata["publicationTitle"] == "Coaching Studies"
    assert metadata["language"] == "en"
    assert metadata["tags"] == ["Process"]


def test_partial_zotero_fetch_selects_notes_and_child_keys(tmp_path):
    db_path = _zotero_fixture_db(tmp_path)
    source = ZoteroSource({"zotero": {"enabled": True, "data_directory": str(db_path.parent)}})
    docs = list(source.fetch_item_documents("PARENT", kinds={"note"}))

    assert len(docs) == 1
    assert docs[0].metadata["source_type"] == "zotero_note"
    assert docs[0].metadata["note_key"] == "NOTEKEY"
    assert "note text" in docs[0].content


# ------------------------------------------------------------- bulk helpers


def test_progress_forget_with_prefix_single_write(tmp_path):
    progress = IndexingProgress(tmp_path / "p.json")
    for i in range(5):
        progress.set_document_status(f"obsidian-n{i}.md", DocumentStatus.STORED)
    progress.set_document_status("zotero-1-note-1", DocumentStatus.STORED)

    forgotten = progress.forget_with_prefix("obsidian-")

    assert forgotten == 5
    assert progress.get_status("zotero-1-note-1") == DocumentStatus.STORED
    assert progress.get_status("obsidian-n0.md") is None
    assert progress.data["stats"]["documents_stored"] == 1


def test_registry_delete_sources_like(tmp_path):
    registry = SourceRegistry(tmp_path / "r.sqlite")
    registry.record_chunks(
        ["o1", "o2", "z1"],
        [
            {"source_type": "obsidian", "source_id": "obsidian-a.md", "chunk_level": "mid", "chunk_index": 0},
            {"source_type": "obsidian", "source_id": "obsidian-b.md", "chunk_level": "mid", "chunk_index": 0},
            {"source_type": "zotero_fulltext", "zotero_key": "Z1", "source_id": "zotero-1-attachment-1",
             "chunk_level": "mid", "chunk_index": 0},
        ],
    )
    registry.refresh_sources()

    removed = registry.delete_sources_like("source_id", "obsidian-%")

    assert removed == 2
    assert registry.chunk_count() == 1
    payload = registry.list_sources_payload()
    assert payload["total_sources"] == 1
    assert payload["sources"][0]["identity_value"] == "Z1"
