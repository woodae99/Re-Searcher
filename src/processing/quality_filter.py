"""Quality filter guard for dropping low-information chunks."""

from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Tuple


_WHITESPACE_RE = re.compile(r"\s+")


@dataclass
class QualityFilterStats:
    """Aggregated stats for quality filtering."""

    total_input: int = 0
    kept: int = 0
    dropped: int = 0
    dropped_candidates: int = 0
    reason_counts: Counter = field(default_factory=Counter)
    source_dropped: Counter = field(default_factory=Counter)
    source_kept: Counter = field(default_factory=Counter)
    source_reason_counts: Dict[str, Counter] = field(default_factory=lambda: defaultdict(Counter))
    source_previews: Dict[str, List[str]] = field(default_factory=lambda: defaultdict(list))

    def reset(self) -> None:
        self.total_input = 0
        self.kept = 0
        self.dropped = 0
        self.dropped_candidates = 0
        self.reason_counts.clear()
        self.source_dropped.clear()
        self.source_kept.clear()
        self.source_reason_counts.clear()
        self.source_previews.clear()


def _normalize_text(text: str) -> str:
    """Strip unicode whitespace and collapse whitespace runs."""
    return _WHITESPACE_RE.sub(" ", text.strip())


def is_low_info(text: str, cfg: Dict[str, Any]) -> Tuple[bool, List[str]]:
    """Return (drop, reasons) for low-information detection."""
    reasons: List[str] = []

    if not isinstance(text, str):
        return True, ["non_string"]

    stripped = text.strip()
    normalized = _normalize_text(stripped)

    total_chars = len(normalized)
    whitespace_chars = sum(1 for ch in stripped if ch.isspace())
    alnum_chars = sum(1 for ch in stripped if ch.isalnum())
    alnum_ratio = (alnum_chars / len(stripped)) if stripped else 0.0
    whitespace_ratio = (whitespace_chars / len(stripped)) if stripped else 1.0
    token_est = len(stripped) // 4 if stripped else 0

    min_alnum_chars = int(cfg.get("min_alnum_chars", 40))
    min_alnum_ratio = float(cfg.get("min_alnum_ratio", 0.20))
    max_whitespace_ratio = float(cfg.get("max_whitespace_ratio", 0.85))
    min_token_est = int(cfg.get("min_token_est", 20))

    if alnum_chars < min_alnum_chars:
        reasons.append("min_alnum_chars")
    if alnum_ratio < min_alnum_ratio:
        reasons.append("min_alnum_ratio")
    if whitespace_ratio > max_whitespace_ratio:
        reasons.append("max_whitespace_ratio")
    if token_est < min_token_est:
        reasons.append("min_token_est")

    # Unique character ratio
    min_unique_char_ratio = float(cfg.get("min_unique_char_ratio", 0.08))
    if total_chars > 0:
        unique_ratio = len(set(normalized)) / total_chars
        if unique_ratio < min_unique_char_ratio:
            reasons.append("min_unique_char_ratio")
    else:
        reasons.append("min_unique_char_ratio")

    # Line repeat ratio
    max_line_repeat_ratio = float(cfg.get("max_line_repeat_ratio", 0.30))
    lines = [line.strip() for line in stripped.splitlines()]
    lines = [line for line in lines if line]
    if lines:
        normalized_lines = [
            _WHITESPACE_RE.sub(" ", line).lower() for line in lines
        ]
        short_lines = [line for line in normalized_lines if len(line) <= 60]
        if short_lines:
            counts = Counter(short_lines)
            most_common = counts.most_common(1)[0][1]
            ratio = most_common / len(lines)
            if ratio > max_line_repeat_ratio:
                reasons.append("max_line_repeat_ratio")

    # Regex checks
    patterns = cfg.get("drop_if_matches", []) or []
    for pattern in patterns:
        try:
            if re.search(pattern, normalized, flags=re.IGNORECASE):
                reasons.append(f"regex:{pattern}")
        except re.error:
            continue

    return bool(reasons), reasons


class QualityFilterGuard:
    """Guard that drops low-information chunks before embedding."""

    def __init__(self, config: Dict[str, Any]):
        chunking_cfg = config.get("chunking", {})
        self.cfg = chunking_cfg.get("quality_filter", {}) or {}
        self.enabled = bool(self.cfg.get("enabled", False))
        self.dry_run = bool(self.cfg.get("dry_run", False))
        self.keep_if_metadata_present = set(
            self.cfg.get("keep_if_metadata_present", []) or []
        )
        self.skip_source_types = set(self.cfg.get("skip_source_types", []) or [])
        self.whitelist_source_ids = set(self.cfg.get("whitelist_source_ids", []) or [])
        self.blacklist_source_ids = set(self.cfg.get("blacklist_source_ids", []) or [])
        self.report_cfg = self.cfg.get("report", {}) or {}
        self.stats = QualityFilterStats()

    def is_active(self) -> bool:
        return self.enabled or self.dry_run or bool(self.blacklist_source_ids)

    def process(self, chunks: List[Tuple[str, Dict[str, Any]]]) -> List[Tuple[str, Dict[str, Any]]]:
        filtered = self._process_batch(chunks)
        return filtered

    def process_with_ids(
        self,
        chunks: List[str],
        metadatas: List[Dict[str, Any]],
        ids: List[str],
        batch_label: str = "",
    ) -> Tuple[List[str], List[Dict[str, Any]], List[str]]:
        combined = list(zip(chunks, metadatas, ids))
        processed = self._process_batch(combined, batch_label=batch_label, with_ids=True)
        out_chunks: List[str] = []
        out_metas: List[Dict[str, Any]] = []
        out_ids: List[str] = []
        for item in processed:
            text, meta, chunk_id = item
            out_chunks.append(text)
            out_metas.append(meta)
            out_ids.append(chunk_id)
        return out_chunks, out_metas, out_ids

    def _process_batch(
        self,
        items: Iterable,
        batch_label: str = "",
        with_ids: bool = False,
    ) -> List:
        if not self.is_active():
            return list(items)

        batch_total = 0
        batch_dropped = 0
        batch_reason_counts: Counter = Counter()
        batch_source_counts: Counter = Counter()

        kept_items = []

        for item in items:
            if with_ids:
                text, meta, chunk_id = item
            else:
                text, meta = item
                chunk_id = None

            batch_total += 1
            self.stats.total_input += 1

            source_id = meta.get("source_id", "unknown") if isinstance(meta, dict) else "unknown"
            source_type = meta.get("source_type") if isinstance(meta, dict) else None

            # Whitelist/blacklist handling
            if source_id in self.whitelist_source_ids:
                self.stats.kept += 1
                self.stats.source_kept[source_id] += 1
                kept_items.append(item)
                continue

            if source_id in self.blacklist_source_ids:
                self._record_drop(
                    source_id,
                    text,
                    ["blacklisted_source"],
                    is_candidate=False,
                )
                batch_dropped += 1
                batch_reason_counts.update(["blacklisted_source"])
                batch_source_counts[source_id] += 1
                if not self.dry_run:
                    continue
                self.stats.kept += 1
                self.stats.source_kept[source_id] += 1
                kept_items.append(item)
                continue

            if source_type in self.skip_source_types:
                self.stats.kept += 1
                self.stats.source_kept[source_id] += 1
                kept_items.append(item)
                continue

            if self.keep_if_metadata_present and isinstance(meta, dict):
                if any(meta.get(key) for key in self.keep_if_metadata_present):
                    self.stats.kept += 1
                    self.stats.source_kept[source_id] += 1
                    kept_items.append(item)
                    continue

            drop, reasons = is_low_info(text, self.cfg)
            if drop:
                self._record_drop(
                    source_id,
                    text,
                    reasons,
                    is_candidate=self.dry_run,
                )
                batch_dropped += 1
                batch_reason_counts.update(reasons)
                batch_source_counts[source_id] += 1
                if not self.dry_run:
                    continue

            self.stats.kept += 1
            self.stats.source_kept[source_id] += 1
            kept_items.append(item)

        self._log_batch_summary(
            batch_label,
            batch_total,
            batch_dropped,
            batch_source_counts,
            batch_reason_counts,
        )

        return kept_items

    def _record_drop(self, source_id: str, text: str, reasons: List[str], is_candidate: bool) -> None:
        if is_candidate:
            self.stats.dropped_candidates += 1
        else:
            self.stats.dropped += 1

        self.stats.reason_counts.update(reasons)
        self.stats.source_dropped[source_id] += 1
        self.stats.source_reason_counts[source_id].update(reasons)

        previews = self.stats.source_previews[source_id]
        if len(previews) < 5:
            preview = text.strip().replace("\n", " ")[:120] if isinstance(text, str) else ""
            previews.append(preview)

    def _log_batch_summary(
        self,
        batch_label: str,
        batch_total: int,
        batch_dropped: int,
        batch_source_counts: Counter,
        batch_reason_counts: Counter,
    ) -> None:
        if batch_total == 0:
            return

        dropped_pct = (batch_dropped / batch_total) * 100.0
        top_offenders = [f"{sid}:{count}" for sid, count in batch_source_counts.most_common(3)]
        top_reasons = [f"{reason}:{count}" for reason, count in batch_reason_counts.most_common(3)]

        label = f" {batch_label}" if batch_label else ""
        mode = "dry_run" if self.dry_run else "live"
        print(
            f"[INFO] QualityFilter{label}: {batch_dropped}/{batch_total} "
            f"({dropped_pct:.1f}%) dropped ({mode}) | "
            f"top_offenders={top_offenders} | top_reasons={top_reasons}"
        )

    def write_report(self) -> None:
        if not self.report_cfg.get("enabled", False):
            return

        output_path = self.report_cfg.get("output_path", "runs/latest/quality_report.json")
        run_id = os.getenv("RUN_ID", "latest")
        output_path = output_path.replace("{RUN_ID}", run_id)
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)

        top_n = int(self.report_cfg.get("top_n", 50))
        offenders = []
        for source_id, count in self.stats.source_dropped.most_common(top_n):
            offenders.append(
                {
                    "source_id": source_id,
                    "dropped_count": count,
                    "kept_count": self.stats.source_kept.get(source_id, 0),
                    "reason_counts": dict(self.stats.source_reason_counts.get(source_id, Counter())),
                    "previews": self.stats.source_previews.get(source_id, [])[:5],
                }
            )

        report = {
            "run_id": run_id,
            "dry_run": self.dry_run,
            "totals": {
                "input": self.stats.total_input,
                "kept": self.stats.kept,
                "dropped": self.stats.dropped,
                "dropped_candidates": self.stats.dropped_candidates,
            },
            "reason_counts": dict(self.stats.reason_counts),
            "top_offenders": offenders,
        }

        path.write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding="utf-8")


def create_quality_filter_guard(config: Dict[str, Any]) -> QualityFilterGuard:
    """Create a QualityFilterGuard from configuration."""
    return QualityFilterGuard(config)
