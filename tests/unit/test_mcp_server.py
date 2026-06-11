import asyncio
import time
from pathlib import Path
from types import SimpleNamespace

from src.mcp_server import MCPServerBusyError, ResearchMCPServer


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


def _server_with_collection(collection, output_dir=None):
    server = ResearchMCPServer(Path("config.yaml"))
    server.pipeline = SimpleNamespace(
        vector_store=_FakeVectorStore(collection),
        output_dir=output_dir or Path("output"),
    )
    return server


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


def test_list_sources_aggregates_batches_filters_and_caches():
    async def run_test():
        collection = _FakeCollection(
            [
                {
                    "id": "z-1",
                    "document": "a",
                    "metadata": {
                        "source_type": "zotero_fulltext",
                        "zotero_key": "Z1",
                        "title": "Alpha Coaching",
                        "authors": "Whitehead",
                        "chunk_level": "mid",
                        "indexed_at": "2026-06-10T12:00:00Z",
                    },
                },
                {
                    "id": "z-2",
                    "document": "b",
                    "metadata": {
                        "source_type": "zotero_fulltext",
                        "zotero_key": "Z1",
                        "title": "Alpha Coaching",
                        "authors": "Whitehead",
                        "chunk_level": "fine",
                    },
                },
                {
                    "id": "o-1",
                    "document": "c",
                    "metadata": {
                        "source_type": "obsidian",
                        "source_id": "obsidian-B.md",
                        "title": "Beta Notes",
                        "authors": "Colin",
                        "chunk_level": "mid",
                    },
                },
            ]
        )
        server = _server_with_collection(collection)

        first = await server._list_sources({"author": "white", "limit": 10})
        second = await server._list_sources({"title_contains": "alpha", "limit": 10})

        assert "Total Sources: 1" in first[0].text
        assert "Identity: zotero_key=Z1" in first[0].text
        assert "mid=1" in first[0].text
        assert "fine=1" in first[0].text
        assert "Freshness: 2026-06-10T12:00:00Z" in first[0].text
        assert "Total Sources: 1" in second[0].text
        assert collection.count_calls == 2
        metadata_scans = [
            call
            for call in collection.get_calls
            if call["include"] == ["metadatas"] and call["limit"] == 2000
        ]
        assert len(metadata_scans) == 1

    asyncio.run(run_test())


def test_list_sources_cache_rebuilds_when_collection_count_changes():
    async def run_test():
        collection = _FakeCollection(
            [
                {
                    "id": "o-1",
                    "document": "c",
                    "metadata": {
                        "source_type": "obsidian",
                        "source_id": "obsidian-B.md",
                        "title": "Beta Notes",
                        "chunk_level": "mid",
                    },
                },
            ]
        )
        server = _server_with_collection(collection)

        await server._list_sources({})
        collection.records.append(
            {
                "id": "o-2",
                "document": "d",
                "metadata": {
                    "source_type": "obsidian",
                    "source_id": "obsidian-C.md",
                    "title": "Gamma Notes",
                    "chunk_level": "mid",
                },
            }
        )
        result = await server._list_sources({})

        assert "Total Sources: 2" in result[0].text
        metadata_scans = [
            call
            for call in collection.get_calls
            if call["include"] == ["metadatas"] and call["limit"] == 2000
        ]
        assert len(metadata_scans) == 2

    asyncio.run(run_test())


def test_list_sources_large_collection_starts_background_cache(tmp_path):
    async def run_test():
        collection = _FakeCollection(
            [
                {
                    "id": "o-1",
                    "document": "c",
                    "metadata": {
                        "source_type": "obsidian",
                        "source_id": "obsidian-B.md",
                        "title": "Beta Notes",
                        "chunk_level": "mid",
                    },
                },
            ],
            count_override=100_000,
        )
        server = _server_with_collection(collection, output_dir=tmp_path)

        result = await server._list_sources({})

        assert "Source register cache is building in the background" in result[0].text
        assert server._source_cache_future is not None
        assert server._source_cache_future.result(timeout=5)
        assert (tmp_path / "mcp_source_cache.json").exists()
        second = await server._list_sources({})
        assert "=== Source Register ===" in second[0].text
        assert "Identity: source_id=obsidian-B.md" in second[0].text

    asyncio.run(run_test())
