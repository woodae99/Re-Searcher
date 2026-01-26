#!/usr/bin/env python3
"""HTTP MCP Server for Re-Searcher (streamable HTTP transport)."""

import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from src.mcp_server import DEFAULT_CONFIG_PATH, ResearchMCPServer


def create_app(config_path: Path | None = None) -> Starlette:
    if config_path is None:
        config_env = os.getenv("MCP_CONFIG_PATH")
        config_path = Path(config_env) if config_env else DEFAULT_CONFIG_PATH

    server = ResearchMCPServer(config_path)
    session_manager = StreamableHTTPSessionManager(server.server)

    @asynccontextmanager
    async def lifespan(_: Starlette):
        async with session_manager.run():
            yield

    async def health(_: object) -> JSONResponse:
        return JSONResponse({"status": "ok"})

    routes = [
        Mount("/mcp", app=session_manager.handle_request),
        Route("/healthz", endpoint=health, methods=["GET"]),
    ]
    return Starlette(routes=routes, lifespan=lifespan)


app = create_app()


def main() -> None:
    config_path = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    host = os.getenv("MCP_HTTP_HOST", "127.0.0.1")
    port = int(os.getenv("MCP_HTTP_PORT", "8001"))

    uvicorn.run(
        create_app(config_path),
        host=host,
        port=port,
        log_level=os.getenv("MCP_HTTP_LOG_LEVEL", "info"),
    )


if __name__ == "__main__":
    main()
