"""Managed vLLM server lifecycle — stand up, serve, stand down.

vLLM is the production embedding backend (see docs/EMBEDDER_BAKEOFF.md), but it
should never have to be hand-driven from the CLI. This wraps the container in a
config-driven lifecycle:

  * bulk pipeline jobs (index/reindex) wrap their run in ``managed_embedding_backend``
    — the container is started, the job runs, and the container is torn down;
  * interactive retrieval uses a persistent server started once via
    ``scripts/vllm_service.py start`` (keep_up=True).

It is idempotent: if a healthy container with the configured name is already serving,
it is reused rather than restarted (so a persistent retrieval server and a bulk job
don't fight over the port). All settings come from ``embedding.vllm.managed``.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import time
import urllib.request
from typing import Any, Dict, List, Optional


class VLLMServer:
    """Start/stop a vLLM embedding container from config (docker)."""

    def __init__(self, config: Dict[str, Any], *, managed: Optional[Dict[str, Any]] = None,
                 base_url: Optional[str] = None, model: Optional[str] = None):
        # Defaults read the embedding.vllm block; callers can override the managed
        # block / base_url / model to drive any vLLM container (e.g. the reranker).
        emb = config.get("embedding", {}) or {}
        vllm = emb.get("vllm", {}) or {}
        m = managed if managed is not None else (vllm.get("managed", {}) or {})
        self.base_url = base_url or vllm.get("base_url", "http://localhost:8002/v1")
        self.model = model or vllm.get("model") or emb.get("model")
        self.image = m.get("image", "vllm/vllm-openai:v0.20.0")
        self.container_name = m.get("container_name", "researcher-vllm-embed")
        self.port = int(m.get("port", 8002))
        self.gpus = m.get("gpus", "all")
        self.runner = m.get("runner", "pooling")
        self.max_model_len = int(m.get("max_model_len", 1024))
        self.gpu_memory_utilization = float(m.get("gpu_memory_utilization", 0.3))
        self.hf_cache = os.path.expanduser(m.get("hf_cache", "~/.cache/huggingface"))
        self.startup_timeout = int(m.get("startup_timeout", 900))
        self.keep_up = bool(m.get("keep_up", False))
        self.extra_args: List[str] = list(m.get("extra_args", []) or [])

    # -- docker helpers --------------------------------------------------------
    def _docker(self, *args: str, capture: bool = False) -> subprocess.CompletedProcess:
        return subprocess.run(["docker", *args], check=False,
                              capture_output=capture, text=True)

    def _container_running(self) -> bool:
        r = self._docker("ps", "--filter", f"name=^{self.container_name}$",
                         "--format", "{{.Names}}", capture=True)
        return self.container_name in (r.stdout or "")

    def docker_run_args(self) -> List[str]:
        """The full `docker run` argv — pure function, unit-testable."""
        return [
            "run", "-d", "--name", self.container_name,
            "--gpus", self.gpus,
            "-p", f"{self.port}:8000",
            "-v", f"{self.hf_cache}:/root/.cache/huggingface",
            self.image,
            self.model,
            "--runner", self.runner,
            "--max-model-len", str(self.max_model_len),
            "--gpu-memory-utilization", str(self.gpu_memory_utilization),
            *self.extra_args,
        ]

    # -- readiness -------------------------------------------------------------
    def is_ready(self) -> bool:
        try:
            with urllib.request.urlopen(f"{self.base_url}/models", timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    def wait_ready(self, timeout: Optional[int] = None) -> None:
        deadline = time.time() + (timeout or self.startup_timeout)
        while time.time() < deadline:
            if self.is_ready():
                return
            # Fail fast if the container died (e.g. bad args / OOM).
            if not self._container_running():
                logs = self._docker("logs", "--tail", "30", self.container_name, capture=True)
                raise RuntimeError(
                    f"vLLM container '{self.container_name}' exited during startup.\n"
                    f"{(logs.stdout or '') + (logs.stderr or '')}")
            time.sleep(5)
        raise TimeoutError(
            f"vLLM not ready at {self.base_url} within {timeout or self.startup_timeout}s")

    # -- lifecycle -------------------------------------------------------------
    def start(self) -> "VLLMServer":
        if self._container_running():
            if self.is_ready():
                return self  # reuse healthy existing server (idempotent)
        else:
            # Remove any stopped/leftover container with the same name, then run.
            self._docker("rm", "-f", self.container_name, capture=True)
            run = self._docker(*self.docker_run_args(), capture=True)
            if run.returncode != 0:
                raise RuntimeError(f"docker run failed: {(run.stderr or run.stdout or '').strip()}")
        self.wait_ready()
        return self

    def stop(self) -> None:
        self._docker("rm", "-f", self.container_name, capture=True)

    def __enter__(self) -> "VLLMServer":
        return self.start()

    def __exit__(self, exc_type, exc, tb) -> None:
        if not self.keep_up:
            self.stop()


def reranker_server(config: Dict[str, Any]) -> "VLLMServer":
    """A VLLMServer bound to the cross-encoder reranker block."""
    ce = ((config.get("retrieval", {}) or {}).get("rerank", {}) or {}).get("cross_encoder", {}) or {}
    return VLLMServer(config, managed=ce.get("managed", {}) or {},
                      base_url=ce.get("base_url"), model=ce.get("model"))


def _uses_managed_vllm(config: Dict[str, Any]) -> bool:
    emb = config.get("embedding", {}) or {}
    if emb.get("provider") != "vllm":
        return False
    return bool(((emb.get("vllm", {}) or {}).get("managed", {}) or {}).get("enabled", False))


def _uses_managed_reranker(config: Dict[str, Any]) -> bool:
    rr = (config.get("retrieval", {}) or {}).get("rerank", {}) or {}
    if not rr.get("enabled", False) or rr.get("type") != "cross_encoder":
        return False
    return bool(((rr.get("cross_encoder", {}) or {}).get("managed", {}) or {}).get("enabled", False))


@contextlib.contextmanager
def managed_embedding_backend(config: Dict[str, Any]):
    """Context manager for bulk jobs: stand up vLLM if (and only if) configured for
    managed vLLM, then tear it down. A no-op for any other backend, so LM Studio /
    persistent-vLLM workflows are unaffected."""
    if not _uses_managed_vllm(config):
        yield None
        return
    server = VLLMServer(config)
    print(f"[vllm] standing up embedding backend '{server.container_name}' "
          f"({server.model}) on port {server.port} …", flush=True)
    server.start()
    print(f"[vllm] ready at {server.base_url}", flush=True)
    try:
        yield server
    finally:
        if not server.keep_up:
            print(f"[vllm] standing down '{server.container_name}'", flush=True)
            server.stop()


@contextlib.contextmanager
def managed_reranker_backend(config: Dict[str, Any]):
    """Like managed_embedding_backend, but for the cross-encoder reranker container.
    No-op unless rerank.type==cross_encoder with a managed lifecycle enabled."""
    if not _uses_managed_reranker(config):
        yield None
        return
    server = reranker_server(config)
    print(f"[vllm] standing up reranker '{server.container_name}' "
          f"({server.model}) on port {server.port} …", flush=True)
    server.start()
    print(f"[vllm] reranker ready at {server.base_url}", flush=True)
    try:
        yield server
    finally:
        if not server.keep_up:
            print(f"[vllm] standing down reranker '{server.container_name}'", flush=True)
            server.stop()
