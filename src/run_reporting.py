"""Structured run reporting for long indexing jobs."""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from .durable_write import write_json_durable_safe


SEVERITIES = {"info", "warn", "error"}
REMEDIATION_CATEGORIES = {
    "source_data",
    "extraction_quality",
    "chunking",
    "embedder_limit",
    "vector_store",
    "registry",
    "ledger",
    "code_bug",
}


def utc_now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def source_identity(metadata: Optional[Dict[str, Any]]) -> Dict[str, str]:
    metadata = metadata or {}
    source_type = str(metadata.get("source_type") or "")
    if source_type.startswith("zotero") and metadata.get("zotero_key"):
        return {"identity_field": "zotero_key", "identity_value": str(metadata["zotero_key"])}
    if metadata.get("source_id"):
        return {"identity_field": "source_id", "identity_value": str(metadata["source_id"])}
    if metadata.get("zotero_key"):
        return {"identity_field": "zotero_key", "identity_value": str(metadata["zotero_key"])}
    return {"identity_field": "", "identity_value": ""}


class RunReporter:
    """Append-friendly JSONL event stream plus summary for one indexing run."""

    def __init__(
        self,
        output_dir: Path,
        *,
        run_id: Optional[str] = None,
        enabled: bool = True,
        events_file: str = "events.jsonl",
        summary_file: str = "summary.json",
    ):
        self.enabled = enabled
        self.run_id = run_id or os.getenv("RUN_ID") or default_run_id()
        self.run_dir = Path(output_dir) / "runs" / self.run_id
        self.events_path = self.run_dir / events_file
        self.summary_path = self.run_dir / summary_file
        self.total_events = 0
        self.severity_counts: Counter = Counter()
        self.stage_counts: Counter = Counter()
        self.remediation_counts: Counter = Counter()
        self._warned_write_failure = False

    @classmethod
    def from_config(cls, config: Dict[str, Any], output_dir: Path) -> "RunReporter":
        cfg = config.get("run_reporting", {}) or {}
        return cls(
            output_dir,
            run_id=cfg.get("run_id") or os.getenv("RUN_ID"),
            enabled=bool(cfg.get("enabled", True)),
            events_file=cfg.get("events_file", "events.jsonl"),
            summary_file=cfg.get("summary_file", "summary.json"),
        )

    def record(
        self,
        *,
        stage: str,
        severity: str,
        remediation: str,
        message: str,
        metadata: Optional[Dict[str, Any]] = None,
        document_id: Optional[str] = None,
        chunk_id: Optional[str] = None,
        text_length: Optional[int] = None,
        token_estimate: Optional[int] = None,
        exception: Optional[BaseException] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        if not self.enabled:
            return

        severity = severity if severity in SEVERITIES else "warn"
        remediation = (
            remediation if remediation in REMEDIATION_CATEGORIES else "code_bug"
        )
        metadata = metadata or {}
        identity = source_identity(metadata)

        record: Dict[str, Any] = {
            "run_id": self.run_id,
            "timestamp": utc_now_iso(),
            "stage": stage,
            "severity": severity,
            "remediation": remediation,
            "message": message,
            **identity,
            "document_id": str(document_id or metadata.get("source_id") or ""),
            "source_type": str(metadata.get("source_type") or ""),
            "attachment_key": str(metadata.get("attachment_key") or ""),
            "note_key": str(metadata.get("note_key") or ""),
            "annotation_key": str(metadata.get("annotation_key") or ""),
            "chunk_id": str(chunk_id or ""),
            "text_length": text_length,
            "token_estimate": token_estimate,
            "extractor": str(metadata.get("extractor") or ""),
            "extractor_version": str(metadata.get("extractor_version") or ""),
            "extract_quality": str(metadata.get("extract_quality") or ""),
            "extract_action": str(metadata.get("extract_action") or ""),
        }
        if exception is not None:
            record["exception_class"] = exception.__class__.__name__
            record["exception_message"] = str(exception)
        if extra:
            record["extra"] = extra

        self.total_events += 1
        self.severity_counts[severity] += 1
        self.stage_counts[stage] += 1
        self.remediation_counts[remediation] += 1

        self._append_jsonl(record)

    def record_exception(
        self,
        *,
        stage: str,
        remediation: str,
        message: str,
        exception: BaseException,
        metadata: Optional[Dict[str, Any]] = None,
        document_id: Optional[str] = None,
        chunk_id: Optional[str] = None,
        text_length: Optional[int] = None,
        token_estimate: Optional[int] = None,
        extra: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.record(
            stage=stage,
            severity="error",
            remediation=remediation,
            message=message,
            metadata=metadata,
            document_id=document_id,
            chunk_id=chunk_id,
            text_length=text_length,
            token_estimate=token_estimate,
            exception=exception,
            extra=extra,
        )

    def write_summary(self) -> None:
        if not self.enabled:
            return
        summary = {
            "run_id": self.run_id,
            "updated_at": utc_now_iso(),
            "events_path": str(self.events_path),
            "total_events": self.total_events,
            "severity_counts": dict(self.severity_counts),
            "stage_counts": dict(self.stage_counts),
            "remediation_counts": dict(self.remediation_counts),
        }
        # Best-effort durable write — a corrupted summary is a lost
        # report, not a crashed indexing run.
        try:
            write_json_durable_safe(
                self.summary_path, summary, indent=2, fallback_direct=True
            )
        except Exception as exc:
            self._warn_once(exc)

    def _append_jsonl(self, record: Dict[str, Any]) -> None:
        try:
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with self.events_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=True, sort_keys=True))
                f.write("\n")
                f.flush()
        except Exception as exc:
            self._warn_once(exc)

    def _warn_once(self, exc: BaseException) -> None:
        if self._warned_write_failure:
            return
        self._warned_write_failure = True
        print(f"[WARN] Run reporter write failed: {exc}")
