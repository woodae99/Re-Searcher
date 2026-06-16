#!/usr/bin/env python3
"""Manage the persistent vLLM retrieval stack (embedder + reranker).

Bulk pipeline jobs (index/reindex) stand vLLM up and down on their own. This command
runs the *retrieval-time* servers persistently so CLI search and the MCP server can
embed queries and rerank — all from config, no raw vLLM flags:

    python scripts/vllm_service.py start     # stand up embedder (+ reranker), wait ready
    python scripts/vllm_service.py status    # are they serving?
    python scripts/vllm_service.py stop       # tear down

Manages the embedder when `embedding.provider: vllm`, and the cross-encoder reranker
when `retrieval.rerank.type: cross_encoder`. Settings come from `embedding.vllm.managed`
and `retrieval.rerank.cross_encoder.managed`. See docs/EMBEDDING_BACKEND.md.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import List, Tuple

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.embedding.vllm_server import VLLMServer, reranker_server


def _servers(config: dict) -> List[Tuple[str, VLLMServer]]:
    """The vLLM servers this config implies for retrieval (embedder, reranker)."""
    out: List[Tuple[str, VLLMServer]] = []
    if (config.get("embedding", {}) or {}).get("provider") == "vllm":
        out.append(("embedder", VLLMServer(config)))
    rr = (config.get("retrieval", {}) or {}).get("rerank", {}) or {}
    if rr.get("enabled") and rr.get("type") == "cross_encoder":
        out.append(("reranker", reranker_server(config)))
    return out


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("action", choices=["start", "stop", "status"])
    p.add_argument("--config", type=Path, default=Path("config.yaml"))
    args = p.parse_args()

    if not args.config.exists():
        print(f"[ERROR] config not found: {args.config}")
        return 1
    config = yaml.safe_load(args.config.read_text()) or {}
    servers = _servers(config)
    if not servers:
        print("[vllm] no vLLM retrieval servers configured "
              "(embedding.provider != vllm and rerank.type != cross_encoder).")
        return 0

    rc = 0
    for label, server in servers:
        server.keep_up = True  # persistent
        if args.action == "status":
            running = server._container_running()
            ready = server.is_ready() if running else False
            print(f"{label:9} '{server.container_name}' @ {server.base_url}: "
                  f"{'ready' if ready else ('running, not ready' if running else 'absent')}")
            rc = rc or (0 if ready else 1)
        elif args.action == "stop":
            server.stop()
            print(f"[vllm] stopped {label} '{server.container_name}'")
        else:  # start
            print(f"[vllm] starting {label} '{server.container_name}' ({server.model}) "
                  f"on port {server.port} — first run downloads/compiles …", flush=True)
            server.start()
            print(f"[vllm] {label} ready at {server.base_url}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
