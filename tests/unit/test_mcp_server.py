import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from src.mcp_server import MCPServerBusyError, ResearchMCPServer
from src.registry import SourceRegistry


class _FakePipeline:
    def __init__(self, delay_seconds: float = 0.15):
        self.delay_seconds = delay_seconds

    def query(self, **kwargs):
        time.sleep(self.delay_seconds)
        return [("doc-1", "text", 0.9, {"title": "Example"})]


class _FakeCollection:
    def __init__(self, records, count_override=None):
        self.records = list(records)
        self.count_override = count_override
        self.get_calls = []
        self.count_calls = 0

    def count(self):
        self.count_calls += 1
        if self.count_override is not None:
            return self.count_override
        return len(self.records)

    def get(self, ids=None, where=None, include=None, limit=None, offset=None):
        self.get_calls.append(
            {"ids": ids, "where": where, "include": include, "limit": limit, "offset": offset}
        )
        records = self.records

        if ids is not None:
            wanted = set(ids)
            records = [record for record in records if record["id"] in wanted]
        if where and "$and" in where:
            clauses = where["$and"]
            records = [
                record
                for record in records
                if all(
                    all(record["metadata"].get(key) == value for key, value in clause.items())
                    for clause in clauses
                )
            ]
        elif where:
            records = [
                record
                for record in records
                if all(record["metadata"].get(key) == value for key, value in where.items())
            ]
        if offset is not None:
            records = records[offset:]
        if limit is not None:
            records = records[:limit]

        result = {"ids": [record["id"] for record in records]}
        include = include or []
        if "metadatas" in include:
            result["metadatas"] = [record["metadata"] for record in records]
        if "documents" in include:
            result["documents"] = [record["document"] for record in records]
        return result


class _FakeVectorStore:
    def __init__(self, collection):
        self.collection = collection

    def get_collection_stats(self):
        return {
            "collection_name": "test_collection",
            "document_count": self.collection.count(),
            "endpoint": "http://localhost:8000",
        }


def _server_with_collection(collection, output_dir=None, registry=None):
    server = ResearchMCPServer(Path("config.yaml"))
    server.pipeline = SimpleNamespace(
        vector_store=_FakeVectorStore(collection),
        output_dir=output_dir or Path("output"),
        registry=registry,
    )
    return server


def _registry_with_sources(tmp_path):
    """Registry seeded with one Zotero item (note + fulltext chunks) and one note."""
    registry = SourceRegistry(tmp_path / "registry.test.sqlite")
    registry.record_chunks(
        ["z-note-1", "z-full-1", "z-full-2", "o-1"],
        [
            {
                "source_type": "zotero_note",
                "zotero_key": "Z1",
                "source_id": "zotero-1-note-9",
                "title": "Alpha Coaching",
                "authors": "Whitehead",
                "chunk_level": "mid",
                "chunk_index": 0,
            },
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "Z1",
                "source_id": "zotero-1-attachment-7",
                "title": "Alpha Coaching",
                "authors": "Whitehead",
                "chunk_level": "mid",
                "chunk_index": 0,
                "indexed_at": "2026-06-10T12:00:00Z",
            },
            {
                "source_type": "zotero_fulltext",
                "zotero_key": "Z1",
                "source_id": "zotero-1-attachment-7",
                "title": "Alpha Coaching",
                "authors": "Whitehead",
                "chunk_level": "fine",
                "chunk_index": 1,
            },
            {
                "source_type": "obsidian",
                "source_id": "obsidian-B.md",
                "title": "Beta Notes",
                "authors": "Colin",
                "chunk_level": "mid",
                "chunk_index": 0,
            },
        ],
    )
    registry.refresh_sources()
    return registry


def test_mcp_server_defaults_to_single_search_slot():
    server = ResearchMCPServer(Path("config.yaml"))

    assert server.max_concurrent_searches == 1
    assert server.search_acquire_timeout_seconds == 900


def test_mcp_server_rejects_parallel_search_bursts():
    async def run_test():
        server = ResearchMCPServer(Path("config.yaml"))
        server.pipeline = _FakePipeline()
        server.search_acquire_timeout_seconds = 0.01

        first = asyncio.create_task(server._run_search_query(query_text="first"))
        await asyncio.sleep(0.02)

        try:
            await server._run_search_query(query_text="second")
        except MCPServerBusyError:
            pass
        else:
            raise AssertionError("Expected a busy error for overlapping search")

        results = await first
        assert results[0][0] == "doc-1"

    asyncio.run(run_test())


def test_mcp_server_queues_parallel_searches_by_default():
    async def run_test():
        server = ResearchMCPServer(Path("config.yaml"))
        server.pipeline = _FakePipeline(delay_seconds=0.02)

        first = asyncio.create_task(server._run_search_query(query_text="first"))
        second = asyncio.create_task(server._run_search_query(query_text="second"))

        results = await asyncio.gather(first, second)
        assert [result[0][0] for result in results] == ["doc-1", "doc-1"]

    asyncio.run(run_test())


def test_get_source_chunks_zotero_filter_level_and_stable_pagination():
    async def run_test():
        collection = _FakeCollection(
            [
                {
                    "id": "chunk-2",
                    "document": "two",
                    "metadata": {
                        "zotero_key": "Z1",
                        "chunk_level": "mid",
                        "chunk_index": 2,
                        "title": "Paper",
                    },
                },
                {
                    "id": "chunk-1",
                    "document": "one",
                    "metadata": {
                        "zotero_key": "Z1",
                        "chunk_level": "mid",
                        "chunk_index": 1,
                        "title": "Paper",
                        "indexed_at": "2026-06-10T12:00:00Z",
                    },
                },
                {
                    "id": "chunk-3",
                    "document": "three",
                    "metadata": {
                        "zotero_key": "Z1",
                        "chunk_level": "fine",
                        "chunk_index": 3,
                    },
                },
            ]
        )
        server = _server_with_collection(collection)

        first = await server._get_source_chunks(
            {"zotero_key": "Z1", "chunk_level": "mid", "limit": 1, "offset": 0}
        )
        second = await server._get_source_chunks(
            {"zotero_key": "Z1", "chunk_level": "mid", "limit": 1, "offset": 1}
        )

        assert "Chunk ID: chunk-1" in first[0].text
        assert "Text:\none" in first[0].text
        assert "Freshness: 2026-06-10T12:00:00Z" in first[0].text
        assert "Chunk ID: chunk-2" in second[0].text
        assert "Text:\ntwo" in second[0].text
        assert "Chunk ID: chunk-3" not in first[0].text
        assert "Chunk ID: chunk-3" not in second[0].text
        assert collection.get_calls[0]["where"] == {"zotero_key": "Z1"}

    asyncio.run(run_test())


def test_get_source_chunks_include_text_false_omits_documents():
    async def run_test():
        collection = _FakeCollection(
            [
                {
                    "id": "chunk-1",
                    "document": "hidden",
                    "metadata": {"source_id": "obsidian-note", "chunk_index": 0},
                }
            ]
        )
        server = _server_with_collection(collection)

        result = await server._get_source_chunks(
            {"source_path": "obsidian-note", "include_text": False}
        )

        assert "Chunk ID: chunk-1" in result[0].text
        assert "hidden" not in result[0].text
        assert all("documents" not in (call["include"] or []) for call in collection.get_calls)

    asyncio.run(run_test())


def test_get_source_chunks_errors_when_identity_is_ambiguous():
    async def run_test():
        server = _server_with_collection(_FakeCollection([]))

        neither = await server._get_source_chunks({})
        both = await server._get_source_chunks({"zotero_key": "Z1", "source_path": "S1"})

        assert "Exactly one of zotero_key or source_path is required" in neither[0].text
        assert "Exactly one of zotero_key or source_path is required" in both[0].text

    asyncio.run(run_test())


def test_list_sources_reads_registry(tmp_path):
    async def run_test():
        registry = _registry_with_sources(tmp_path)
        server = _server_with_collection(_FakeCollection([]), registry=registry)

        by_author = await server._list_sources({"author": "white", "limit": 10})
        by_title = await server._list_sources({"title_contains": "alpha", "limit": 10})

        assert "Total Sources: 1" in by_author[0].text
        assert "Identity: zotero_key=Z1" in by_author[0].text
        assert "mid=2" in by_author[0].text
        assert "fine=1" in by_author[0].text
        assert "Freshness: 2026-06-10T12:00:00Z" in by_author[0].text
        assert "Total Sources: 1" in by_title[0].text

    asyncio.run(run_test())


def test_list_sources_source_type_filter_uses_membership(tmp_path):
    """An item whose first-seen chunk was a note must still match fulltext filters."""

    async def run_test():
        registry = _registry_with_sources(tmp_path)
        server = _server_with_collection(_FakeCollection([]), registry=registry)

        fulltext = await server._list_sources({"source_type": "zotero_fulltext"})
        notes = await server._list_sources({"source_type": "zotero_note"})
        obsidian = await server._list_sources({"source_type": "obsidian"})

        assert "Identity: zotero_key=Z1" in fulltext[0].text
        assert "Identity: zotero_key=Z1" in notes[0].text
        assert "Identity: source_id=obsidian-B.md" in obsidian[0].text
        assert "zotero_key=Z1" not in obsidian[0].text

    asyncio.run(run_test())


def test_list_sources_pagination_is_disjoint_and_complete(tmp_path):
    async def run_test():
        registry = _registry_with_sources(tmp_path)
        server = _server_with_collection(_FakeCollection([]), registry=registry)

        first = await server._list_sources({"limit": 1, "offset": 0})
        second = await server._list_sources({"limit": 1, "offset": 1})

        # Sorted by title: Alpha Coaching (Z1) then Beta Notes (obsidian-B.md)
        assert "Identity: zotero_key=Z1" in first[0].text
        assert "obsidian-B.md" not in first[0].text
        assert "Identity: source_id=obsidian-B.md" in second[0].text
        assert "zotero_key=Z1" not in second[0].text
        assert "Total Sources: 2" in first[0].text

    asyncio.run(run_test())


def test_list_sources_empty_registry_returns_guidance(tmp_path):
    async def run_test():
        registry = SourceRegistry(tmp_path / "registry.empty.sqlite")
        server = _server_with_collection(_FakeCollection([]), registry=registry)

        result = await server._list_sources({})

        assert "build_registry.py" in result[0].text
        assert "checkpointed" in result[0].text

    asyncio.run(run_test())


def test_index_status_reports_drift(tmp_path):
    async def run_test():
        registry = _registry_with_sources(tmp_path)  # 4 chunks recorded
        collection = _FakeCollection([], count_override=6)
        server = _server_with_collection(collection, registry=registry)

        result = await server._index_status({})

        assert "Chroma Chunks: 6" in result[0].text
        assert "Registry Chunks: 4" in result[0].text
        assert "Drift (chroma - registry): +2" in result[0].text
        assert "DRIFT DETECTED" in result[0].text

    asyncio.run(run_test())


def test_index_status_reports_sync_ok(tmp_path):
    async def run_test():
        registry = _registry_with_sources(tmp_path)  # 4 chunks recorded
        collection = _FakeCollection([], count_override=4)
        server = _server_with_collection(collection, registry=registry)

        result = await server._index_status({})

        assert "Drift (chroma - registry): +0" in result[0].text
        assert "Sync: OK" in result[0].text

    asyncio.run(run_test())
