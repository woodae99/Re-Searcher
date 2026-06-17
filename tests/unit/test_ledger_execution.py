"""Tests for P3 ledger-driven execution behavior."""

from pathlib import Path

from src.indexing import IndexingProgress
from src.pipeline import ResearchRAGPipeline
from src.registry import SourceRegistry
from src.sources.base import Document, UnitState
from src.sources.zotero import ZoteroSource


class _ProgressDisplay:
    def set_stage(self, *args, **kwargs):
        pass

    def set_activity(self, *args, **kwargs):
        pass


class _PassThroughGuard:
    def process(self, chunk_data):
        return chunk_data

    def get_stats(self):
        class Stats:
            split = 0
            truncated = 0
            skipped = 0

            def summary(self):
                return ""

        return Stats()


class _PassThroughQuality:
    def process_with_ids(self, chunks, metadatas, ids):
        return chunks, metadatas, ids

    def write_report(self):
        pass


class _OneChunker:
    def chunk_with_metadata(self, content, metadata):
        chunk_metadata = dict(metadata)
        chunk_metadata.setdefault("chunk_level", "atomic")
        chunk_metadata.setdefault("chunk_index", 0)
        return [(content, chunk_metadata)]


class _Embedder:
    def __init__(self):
        self.calls = []

    def embed_texts(self, chunks):
        self.calls.append(list(chunks))
        return [[0.1, 0.2] for _ in chunks]


class _VectorStore:
    def __init__(self):
        self.deletes = []
        self.added = []
        self.metadata_updates = []
        self.records = {}

    def delete_where(self, where):
        self.deletes.append(where)

    def add_documents(self, texts, embeddings, metadatas, ids=None):
        self.added.append(
            {"texts": list(texts), "metadatas": list(metadatas), "ids": list(ids)}
        )
        for doc_id, text, metadata in zip(ids, texts, metadatas):
            self.records[doc_id] = (text, metadata)

    def get_by_ids(self, ids):
        return [
            (doc_id, self.records[doc_id][0], self.records[doc_id][1])
            for doc_id in ids
            if doc_id in self.records
        ]

    def update_metadata(self, ids, metadatas):
        self.metadata_updates.append({"ids": list(ids), "metadatas": list(metadatas)})
        for doc_id, metadata in zip(ids, metadatas):
            text = self.records.get(doc_id, ("", {}))[0]
            self.records[doc_id] = (text, metadata)

    def get_collection_stats(self):
        return {
            "collection_name": "test",
            "document_count": len(self.records),
            "endpoint": "fake",
        }


class _FakeZoteroSource(ZoteroSource):
    def __init__(self, world, docs_by_call):
        super().__init__({"zotero": {"enabled": True}})
        self._world = world
        self.docs_by_call = docs_by_call
        self.fetch_calls = []

    def is_enabled(self):
        return True

    def validate_config(self):
        return True

    def enumerate_state(self):
        return self._world

    def fetch_item_documents(self, item_key, *, kinds=None, attachment_keys=None):
        self.fetch_calls.append(
            {
                "item_key": item_key,
                "kinds": set(kinds or []),
                "attachment_keys": set(attachment_keys or []),
            }
        )
        yield from self.docs_by_call.get(
            (item_key, frozenset(kinds or []), frozenset(attachment_keys or [])),
            [],
        )


def _unit(unit_id, key, kind, fingerprint):
    return UnitState(
        unit_id=unit_id,
        identity_field="zotero_key",
        identity_value=key,
        unit_kind=kind,
        fingerprint=fingerprint,
    )


def _pipeline(tmp_path, source, registry, vector_store):
    pipeline = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipeline.config = {"indexing": {"ledger": {"execute": True}}, "chunking": {}}
    pipeline.output_dir = tmp_path
    pipeline.config_path = Path("config.yaml")
    pipeline.sources = [source]
    pipeline.registry = registry
    pipeline.vector_store = vector_store
    pipeline.progress = IndexingProgress(tmp_path / "progress.json")
    pipeline.progress_display = _ProgressDisplay()
    pipeline.chunker = _OneChunker()
    pipeline.oversize_guard = _PassThroughGuard()
    pipeline.quality_filter = _PassThroughQuality()
    pipeline.embedder = _Embedder()
    pipeline.batch_size = 50
    pipeline.stop_flag_path = tmp_path / "stop.flag"
    return pipeline


def test_new_note_does_not_fetch_or_embed_unchanged_attachment(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.sqlite")
    registry.record_unit_states(
        [
            {
                "unit_id": "zotero:P1:attachment:A1",
                "identity_field": "zotero_key",
                "identity_value": "P1",
                "unit_kind": "attachment",
                "source_fingerprint": "hash:old",
            }
        ]
    )
    registry.record_chunks(
        ["existing-attachment"],
        [
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "P1",
                "source_id": "zotero-1-attachment-10",
                "attachment_key": "A1",
                "chunk_level": "mid",
                "chunk_index": 0,
            }
        ],
    )

    note_doc = Document(
        "new note text",
        {
            "source_type": "zotero_note",
            "zotero_key": "P1",
            "note_key": "N1",
            "content_version": "note-v1",
        },
        doc_id="zotero-1-note-20",
    )
    source = _FakeZoteroSource(
        {
            "zotero:P1:attachment:A1": _unit(
                "zotero:P1:attachment:A1", "P1", "attachment", "hash:old"
            ),
            "zotero:P1:note:N1": _unit("zotero:P1:note:N1", "P1", "note", "note-v1"),
        },
        {("P1", frozenset({"note", "annotation"}), frozenset()): [note_doc]},
    )
    vector_store = _VectorStore()
    pipeline = _pipeline(tmp_path, source, registry, vector_store)

    pipeline._run_ledger_work_plan()

    assert source.fetch_calls == [
        {"item_key": "P1", "kinds": {"note", "annotation"}, "attachment_keys": set()}
    ]
    assert pipeline.embedder.calls == [["new note text"]]
    assert all(
        not (
            where.get("$and")
            and {"source_type": "zotero_fulltext"} in where.get("$and", [])
        )
        for where in vector_store.deletes
    )
    assert registry.get_unit_states()["zotero:P1:note:N1"] == "note-v1"
    assert registry.get_unit_states()["zotero:P1:attachment:A1"] == "hash:old"


def test_parent_meta_only_updates_metadata_without_embedding(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.sqlite")
    registry.record_unit_states(
        [
            {
                "unit_id": "zotero:P1:meta",
                "identity_field": "zotero_key",
                "identity_value": "P1",
                "unit_kind": "parent_meta",
                "source_fingerprint": "mod:old",
            }
        ]
    )
    registry.record_chunks(
        ["chunk-1"],
        [
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "P1",
                "source_id": "zotero-1-attachment-10",
                "attachment_key": "A1",
                "chunk_level": "mid",
                "chunk_index": 0,
                "title": "Old",
            }
        ],
    )
    source = _FakeZoteroSource(
        {"zotero:P1:meta": _unit("zotero:P1:meta", "P1", "parent_meta", "mod:new")},
        {},
    )
    vector_store = _VectorStore()
    vector_store.records["chunk-1"] = (
        "existing text",
        {
            "source_type": "zotero_fulltext",
            "zotero_key": "P1",
            "source_id": "zotero-1-attachment-10",
            "attachment_key": "A1",
            "chunk_level": "mid",
            "chunk_index": 0,
            "title": "Old",
        },
    )
    pipeline = _pipeline(tmp_path, source, registry, vector_store)
    pipeline._fetch_zotero_metadata_base = lambda _source, _key: {
        "source_type": "zotero",
        "zotero_key": "P1",
        "title": "New Title",
        "tags": ["fresh"],
    }

    pipeline._run_ledger_work_plan()

    assert pipeline.embedder.calls == []
    assert vector_store.added == []
    assert vector_store.metadata_updates[0]["ids"] == ["chunk-1"]
    updated = vector_store.metadata_updates[0]["metadatas"][0]
    assert updated["source_type"] == "zotero_fulltext"
    assert updated["title"] == "New Title"
    assert updated["tags"] == ["fresh"]
    assert registry.get_unit_states()["zotero:P1:meta"] == "mod:new"


def test_attachment_update_fetches_only_changed_attachment(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.sqlite")
    registry.record_unit_states(
        [
            {
                "unit_id": "zotero:P1:attachment:A1",
                "identity_field": "zotero_key",
                "identity_value": "P1",
                "unit_kind": "attachment",
                "source_fingerprint": "hash:old",
            },
            {
                "unit_id": "zotero:P1:attachment:A2",
                "identity_field": "zotero_key",
                "identity_value": "P1",
                "unit_kind": "attachment",
                "source_fingerprint": "hash:sibling",
            },
        ]
    )
    registry.record_chunks(
        ["a1-old", "a2-old"],
        [
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "P1",
                "source_id": "zotero-1-attachment-10",
                "attachment_key": "A1",
                "chunk_level": "mid",
                "chunk_index": 0,
            },
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "P1",
                "source_id": "zotero-1-attachment-11",
                "attachment_key": "A2",
                "chunk_level": "mid",
                "chunk_index": 0,
            },
        ],
    )
    attachment_doc = Document(
        "replacement attachment text",
        {
            "source_type": "zotero_fulltext",
            "zotero_key": "P1",
            "attachment_key": "A1",
            "content_version": "hash:new",
        },
        doc_id="zotero-1-attachment-10",
    )
    source = _FakeZoteroSource(
        {
            "zotero:P1:attachment:A1": _unit(
                "zotero:P1:attachment:A1", "P1", "attachment", "hash:new"
            ),
            "zotero:P1:attachment:A2": _unit(
                "zotero:P1:attachment:A2", "P1", "attachment", "hash:sibling"
            ),
        },
        {("P1", frozenset({"attachment"}), frozenset({"A1"})): [attachment_doc]},
    )
    vector_store = _VectorStore()
    pipeline = _pipeline(tmp_path, source, registry, vector_store)

    pipeline._run_ledger_work_plan()

    assert source.fetch_calls == [
        {"item_key": "P1", "kinds": {"attachment"}, "attachment_keys": {"A1"}}
    ]
    assert pipeline.embedder.calls == [["replacement attachment text"]]
    assert any(
        {"attachment_key": "A1"} in where.get("$and", [])
        for where in vector_store.deletes
    )
    remaining_chunks = {
        row["chunk_id"] for row in registry.chunk_records_for_source("zotero_key", "P1")
    }
    assert "a2-old" in remaining_chunks
    assert registry.get_unit_states()["zotero:P1:attachment:A1"] == "hash:new"
    assert registry.get_unit_states()["zotero:P1:attachment:A2"] == "hash:sibling"


def test_parent_delete_removes_source_without_embedding(tmp_path):
    registry = SourceRegistry(tmp_path / "registry.sqlite")
    registry.record_unit_states(
        [
            {
                "unit_id": "zotero:P1:meta",
                "identity_field": "zotero_key",
                "identity_value": "P1",
                "unit_kind": "parent_meta",
                "source_fingerprint": "mod:old",
            },
            {
                "unit_id": "zotero:P1:attachment:A1",
                "identity_field": "zotero_key",
                "identity_value": "P1",
                "unit_kind": "attachment",
                "source_fingerprint": "hash:old",
            },
        ]
    )
    registry.record_chunks(
        ["chunk-1"],
        [
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "P1",
                "source_id": "zotero-1-attachment-10",
                "attachment_key": "A1",
                "chunk_level": "mid",
                "chunk_index": 0,
            }
        ],
    )
    source = _FakeZoteroSource({}, {})
    vector_store = _VectorStore()
    pipeline = _pipeline(tmp_path, source, registry, vector_store)

    pipeline._run_ledger_work_plan()

    assert pipeline.embedder.calls == []
    assert source.fetch_calls == []
    assert vector_store.deletes == [
        {
            "$and": [
                {"zotero_key": {"$in": ["P1"]}},
                {
                    "source_type": {
                        "$in": [
                            "zotero",
                            "zotero_note",
                            "zotero_fulltext",
                            "zotero_annotation",
                        ]
                    }
                },
            ]
        }
    ]
    assert registry.get_unit_states() == {}
    assert registry.chunk_count() == 0
