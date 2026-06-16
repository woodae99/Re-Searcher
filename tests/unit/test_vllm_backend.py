"""Unit tests for the vLLM embedding backend: query instruction + lifecycle.

No network or docker — the OpenAI client and subprocess/urllib calls are mocked.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.embedding.vllm import VLLMEmbedding  # noqa: E402
from src.embedding.vllm_server import VLLMServer, managed_embedding_backend  # noqa: E402
from src.factories.embedding_factory import create_embedder  # noqa: E402


def _vllm_config(**embedding_overrides):
    cfg = {
        "embedding": {
            "provider": "vllm",
            "model": "Qwen/Qwen3-Embedding-0.6B",
            "query_instruction": "Instruct: do the thing\nQuery:",
            "batch_size": 128,
            "max_concurrent_requests": 8,
            "vllm": {
                "base_url": "http://localhost:8002/v1",
                "model": "Qwen/Qwen3-Embedding-0.6B",
                "managed": {
                    "enabled": True,
                    "image": "vllm/vllm-openai:v0.20.0",
                    "container_name": "test-vllm",
                    "port": 8002,
                    "max_model_len": 1024,
                    "gpu_memory_utilization": 0.3,
                    "hf_cache": "/tmp/hf",
                },
            },
        }
    }
    cfg["embedding"].update(embedding_overrides)
    return cfg


# --- provider: query instruction is asymmetric -------------------------------

def _capture_inputs(emb):
    """Patch the OpenAI embeddings client to record the inputs it was sent."""
    sent = {}

    def fake_create(model, input, **kw):
        sent["input"] = input
        # minimal OpenAI-shaped response
        item = mock.Mock(); item.embedding = [0.1, 0.2, 0.3]; item.index = 0
        resp = mock.Mock(); resp.data = [item]
        return resp

    emb.client = mock.Mock()
    emb.client.embeddings.create.side_effect = fake_create
    return sent


def test_vllm_embedding_points_at_vllm_endpoint_and_model():
    emb = VLLMEmbedding(_vllm_config())
    assert emb.endpoint == "http://localhost:8002/v1"
    assert emb.model == "Qwen/Qwen3-Embedding-0.6B"
    assert emb.query_instruction == "Instruct: do the thing\nQuery:"


def test_query_gets_instruction_prefix():
    emb = VLLMEmbedding(_vllm_config())
    sent = _capture_inputs(emb)
    emb.embed_query("what is coaching")
    assert sent["input"] == ["Instruct: do the thing\nQuery:what is coaching"]


def test_documents_are_embedded_raw_no_prefix():
    emb = VLLMEmbedding(_vllm_config())
    sent = _capture_inputs(emb)
    emb.embed_texts(["a document about coaching"])
    assert sent["input"] == ["a document about coaching"]  # no instruction


def test_empty_instruction_is_noop():
    emb = VLLMEmbedding(_vllm_config(query_instruction=""))
    sent = _capture_inputs(emb)
    emb.embed_query("plain query")
    assert sent["input"] == ["plain query"]


def test_factory_selects_vllm_provider():
    emb = create_embedder(_vllm_config())
    assert isinstance(emb, VLLMEmbedding)


# --- lifecycle: docker args + managed context manager ------------------------

def test_docker_run_args_match_proven_invocation():
    server = VLLMServer(_vllm_config())
    args = server.docker_run_args()
    assert args[0] == "run" and "-d" in args
    assert "--name" in args and "test-vllm" in args
    assert "-p" in args and "8002:8000" in args
    assert "/tmp/hf:/root/.cache/huggingface" in args
    assert "vllm/vllm-openai:v0.20.0" in args
    assert "Qwen/Qwen3-Embedding-0.6B" in args
    assert args[args.index("--runner") + 1] == "pooling"
    assert args[args.index("--max-model-len") + 1] == "1024"
    assert args[args.index("--gpu-memory-utilization") + 1] == "0.3"


def test_managed_backend_noop_for_lmstudio():
    cfg = {"embedding": {"provider": "lmstudio"}}
    with mock.patch.object(VLLMServer, "start") as start:
        with managed_embedding_backend(cfg) as srv:
            assert srv is None
        start.assert_not_called()


def test_managed_backend_starts_and_stops_for_vllm():
    cfg = _vllm_config()
    with mock.patch.object(VLLMServer, "start", return_value=None) as start, \
         mock.patch.object(VLLMServer, "stop") as stop:
        with managed_embedding_backend(cfg) as srv:
            assert isinstance(srv, VLLMServer)
            start.assert_called_once()
            stop.assert_not_called()  # only on exit
        stop.assert_called_once()  # torn down on exit (keep_up False)


def test_managed_backend_keep_up_does_not_stop():
    cfg = _vllm_config()
    cfg["embedding"]["vllm"]["managed"]["keep_up"] = True
    with mock.patch.object(VLLMServer, "start", return_value=None), \
         mock.patch.object(VLLMServer, "stop") as stop:
        with managed_embedding_backend(cfg):
            pass
        stop.assert_not_called()


def test_start_reuses_healthy_running_container():
    server = VLLMServer(_vllm_config())
    with mock.patch.object(server, "_container_running", return_value=True), \
         mock.patch.object(server, "is_ready", return_value=True), \
         mock.patch.object(server, "_docker") as docker:
        server.start()
        docker.assert_not_called()  # no docker run when already healthy
