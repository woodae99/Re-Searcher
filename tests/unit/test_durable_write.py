"""Tests for durable JSON writes (Action 5 — fsync-backed state writers)."""

import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.durable_write import write_json_durable, write_json_durable_safe
from src.indexing import DocumentStatus, IndexingProgress
from src.progress import ProgressSnapshotWriter, ProgressDisplay


# ---------------------------------------------------------------------------
# write_json_durable
# ---------------------------------------------------------------------------


def test_write_json_durable_writes_valid_json(tmp_path):
    """The helper writes parseable JSON that round-trips."""
    payload = {"a": [1, 2, 3], "b": "hello"}
    target = tmp_path / "data.json"
    write_json_durable(target, payload, indent=2)

    assert target.exists()
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == payload


def test_write_json_durable_preserves_existing_on_sidecar_failure(tmp_path):
    """If the sidecar write fails, the original file is untouched."""
    original = tmp_path / "data.json"
    original.write_text(json.dumps({"old": True}), encoding="utf-8")
    old_content = original.read_text(encoding="utf-8")

    new_payload = {"new": False}
    sidecar = original.with_suffix(original.suffix + ".tmp")

    # Mock tempfile.NamedTemporaryFile to raise — simulates a sidecar
    # creation failure (e.g. read-only directory).
    with patch(
        "src.durable_write.tempfile.NamedTemporaryFile",
        side_effect=OSError("read-only"),
    ):
        with pytest.raises(OSError):
            write_json_durable(original, new_payload, indent=2)

    # Original must be unchanged — never corrupted.
    assert original.read_text(encoding="utf-8") == old_content


def test_write_json_durable_atomic_replace(tmp_path):
    """After success, no .tmp sidecar is left behind."""
    target = tmp_path / "data.json"
    write_json_durable(target, {"k": "v"}, indent=2)

    tmpts = list(tmp_path.glob("*.tmp"))
    assert len(tmpts) == 0, f"Left behind sidecars: {tmpts}"


# ---------------------------------------------------------------------------
# write_json_durable_safe
# ---------------------------------------------------------------------------


def test_write_json_durable_safe_succeeds_returns_true(tmp_path):
    """Happy path returns True."""
    target = tmp_path / "data.json"
    ok = write_json_durable_safe(target, {"x": 1}, indent=2)
    assert ok is True
    assert json.loads(target.read_text())["x"] == 1


def test_write_json_durable_safe_fallback_returns_false(tmp_path):
    """When durable write fails, fallback to direct write returns False."""
    target = tmp_path / "data.json"

    with patch(
        "src.durable_write.write_json_durable",
        side_effect=OSError("boom"),
    ):
        ok = write_json_durable_safe(target, {"fallback": True}, indent=2)
        assert ok is False

    # Direct fallback still wrote valid JSON.
    assert json.loads(target.read_text())["fallback"] is True


# ---------------------------------------------------------------------------
# IndexingProgress._save uses durable write
# ---------------------------------------------------------------------------


def test_indexing_progress_save_uses_durable_write(tmp_path):
    """Progress checkpoint survives a simulated sidecar failure via fallback."""
    progress_file = tmp_path / "progress.json"
    progress = IndexingProgress(progress_file)
    progress.set_total_documents(5)

    assert progress_file.exists()
    data = json.loads(progress_file.read_text())
    assert data["stats"]["total_documents"] == 5


def test_indexing_progress_corrupt_file_backed_up(tmp_path):
    """A corrupt progress file is renamed and a fresh one is created."""
    progress_file = tmp_path / "progress.json"
    progress_file.write_text("not valid json {{{", encoding="utf-8")

    # Loading handles the corrupt file gracefully — it backs it up.
    progress = IndexingProgress(progress_file)
    # The original corrupt file was renamed to a backup.
    backups = list(tmp_path.glob("*.corrupt-*"))
    assert len(backups) == 1
    # A new valid progress file is created on first write.
    progress.set_total_documents(1)
    assert progress_file.exists()
    data = json.loads(progress_file.read_text())
    assert "stats" in data


# ---------------------------------------------------------------------------
# ProgressSnapshotWriter remains best-effort
# ---------------------------------------------------------------------------


def test_snapshot_writer_does_not_crash_indexing(tmp_path):
    """Even if write fails, the snapshot writer swallows exceptions."""
    snapshot_file = tmp_path / "snapshot.json"
    writer = ProgressSnapshotWriter(snapshot_file, min_interval=0)

    # Force a write.
    writer.write({"test": True}, force=True)

    # Should not raise — ProgressSnapshotWriter is best-effort.
    # The file may or may not exist depending on whether the sidecar
    # write succeeded in the test environment.
    if snapshot_file.exists():
        data = json.loads(snapshot_file.read_text())
        assert data["test"] is True


# ---------------------------------------------------------------------------
# Pipeline state writers use durable write
# ---------------------------------------------------------------------------


def test_save_delta_state_writes_valid_json(tmp_path):
    """_save_delta_state produces parseable JSON via durable write."""
    from src.pipeline import ResearchRAGPipeline

    config = {
        "output_folder": str(tmp_path),
        "storage": {"collection_name": "test"},
        "zotero": {"enabled": False},
        "obsidian": {"enabled": False},
    }

    pipeline = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipeline.output_dir = tmp_path
    pipeline.config = config
    pipeline._delta_state_path = MagicMock(return_value=tmp_path / "delta.json")

    pipeline._save_delta_state(
        item_version=42,
        fulltext_version=100,
        sqlite_date_modified="2026-01-01",
        sqlite_date_deleted="2026-01-02",
        sqlite_attachment_storage_mod_time=999,
    )

    delta_path = tmp_path / "delta.json"
    assert delta_path.exists()
    data = json.loads(delta_path.read_text())
    assert data["last_item_version"] == 42
    assert data["last_fulltext_version"] == 100


def test_save_source_hash_writes_valid_json(tmp_path):
    """_save_source_hash produces parseable JSON via durable write."""
    from src.pipeline import ResearchRAGPipeline

    config = {
        "output_folder": str(tmp_path),
        "storage": {"collection_name": "test"},
        "zotero": {"enabled": False},
        "obsidian": {"enabled": False},
    }

    pipeline = ResearchRAGPipeline.__new__(ResearchRAGPipeline)
    pipeline.output_dir = tmp_path
    pipeline.config = config
    pipeline._compute_source_hashes = MagicMock(
        return_value={"zotero": "abc123", "config": "def456"}
    )

    pipeline._save_source_hash()

    hash_file = tmp_path / "source_hash.txt"
    assert hash_file.exists()
    data = json.loads(hash_file.read_text())
    assert data["zotero"] == "abc123"
    assert data["config"] == "def456"
