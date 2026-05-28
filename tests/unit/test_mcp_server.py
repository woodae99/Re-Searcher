import asyncio
import time
from pathlib import Path

from src.mcp_server import MCPServerBusyError, ResearchMCPServer


class _FakePipeline:
    def __init__(self, delay_seconds: float = 0.15):
        self.delay_seconds = delay_seconds

    def query(self, **kwargs):
        time.sleep(self.delay_seconds)
        return [("doc-1", "text", 0.9, {"title": "Example"})]


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
