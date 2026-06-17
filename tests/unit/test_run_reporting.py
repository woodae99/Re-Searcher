"""Tests for structured indexing run reports."""

import json

from src.pipeline import ResearchRAGPipeline
from src.processing.oversize_guard import OversizeGuard
from src.processing.quality_filter import QualityFilterGuard
from src.run_reporting import RunReporter


def _read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_run_reporter_writes_jsonl_and_summary(tmp_path):
    reporter = RunReporter(tmp_path, run_id="test-run")

    reporter.record(
        stage="embedding",
        severity="error",
        remediation="embedder_limit",
        message="Embedding failed",
        metadata={"source_type": "zotero_fulltext", "zotero_key": "ABC"},
        document_id="doc-1",
        chunk_id="chunk-1",
        text_length=120,
        token_estimate=30,
        exception=RuntimeError("too large"),
    )
    reporter.write_summary()

    events = _read_jsonl(tmp_path / "runs" / "test-run" / "events.jsonl")
    assert len(events) == 1
    assert events[0]["run_id"] == "test-run"
    assert events[0]["stage"] == "embedding"
    assert events[0]["severity"] == "error"
    assert events[0]["identity_field"] == "zotero_key"
    assert events[0]["identity_value"] == "ABC"
    assert events[0]["exception_class"] == "RuntimeError"

    summary = json.loads(
        (tmp_path / "runs" / "test-run" / "summary.json").read_text(
            encoding="utf-8"
        )
    )
    assert summary["total_events"] == 1
    assert summary["severity_counts"] == {"error": 1}
    assert summary["stage_counts"] == {"embedding": 1}


def test_oversize_guard_reports_split_event(tmp_path):
    reporter = RunReporter(tmp_path, run_id="oversize")
    guard = OversizeGuard(max_tokens=10, policy="split", reporter=reporter)

    guard.process(
        [
            (
                " ".join(["alpha"] * 120),
                {
                    "source_id": "doc-1",
                    "source_type": "obsidian",
                    "chunk_level": "mid",
                },
            )
        ]
    )

    events = _read_jsonl(tmp_path / "runs" / "oversize" / "events.jsonl")
    assert events[0]["stage"] == "oversize_guard"
    assert events[0]["severity"] == "warn"
    assert events[0]["remediation"] == "embedder_limit"
    assert events[0]["identity_value"] == "doc-1"
    assert events[0]["extra"]["action"] == "split"


def test_quality_filter_reports_dropped_chunk(tmp_path):
    reporter = RunReporter(tmp_path, run_id="quality")
    cfg = {
        "chunking": {
            "quality_filter": {
                "enabled": True,
                "dry_run": False,
                "min_alnum_chars": 100,
                "min_alnum_ratio": 0.9,
                "max_whitespace_ratio": 0.1,
                "min_token_est": 50,
                "min_unique_char_ratio": 0.9,
                "max_line_repeat_ratio": 0.1,
                "drop_if_matches": [],
            }
        }
    }
    guard = QualityFilterGuard(cfg, reporter=reporter)

    out_chunks, _, _ = guard.process_with_ids(
        ["***"],
        [{"source_id": "doc-1", "source_type": "obsidian"}],
        ["chunk-1"],
    )

    assert out_chunks == []
    events = _read_jsonl(tmp_path / "runs" / "quality" / "events.jsonl")
    assert events[0]["stage"] == "quality_filter"
    assert events[0]["severity"] == "warn"
    assert events[0]["chunk_id"] == "chunk-1"
    assert events[0]["extra"]["reasons"]


def test_pipeline_reports_registry_write_warning(tmp_path):
    class BrokenRegistry:
        def record_chunks(self, ids, metadatas):
            raise RuntimeError("sqlite locked")

    pipeline = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipeline.reporter = RunReporter(tmp_path, run_id="registry")
    pipeline.registry = BrokenRegistry()

    pipeline._record_registry_chunks(
        ["chunk-1"],
        [
            {
                "source_id": "doc-1",
                "source_type": "obsidian",
                "chunk_level": "mid",
            }
        ],
    )

    events = _read_jsonl(tmp_path / "runs" / "registry" / "events.jsonl")
    assert events[0]["stage"] == "registry_write"
    assert events[0]["severity"] == "error"
    assert events[0]["remediation"] == "registry"
    assert events[0]["exception_class"] == "RuntimeError"
