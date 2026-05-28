#!/usr/bin/env python3
"""Watch Re-Searcher indexing progress from snapshot and checkpoint files."""

import argparse
import json
import re
import time
from datetime import timedelta
from pathlib import Path
from typing import Any, Dict

import yaml
from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text


def load_json(path: Path) -> Dict[str, Any]:
    """Load JSON from a file if present."""
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def resolve_paths(config_path: Path) -> tuple[Path, Path, Path]:
    """Resolve snapshot, checkpoint, and stop-flag paths from config."""
    with open(config_path, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    output_dir = Path(config.get("output_folder", "./output"))
    storage = config.get("storage", {}) or {}
    collection_name = str(storage.get("collection_name", "research_library"))
    collection_slug = "".join(
        ch if ch.isalnum() or ch in "._-" else "_"
        for ch in collection_name
    ).strip("_")
    if not collection_slug:
        collection_slug = "research_library"

    dashboard_cfg = config.get("indexing", {}).get("dashboard", {}) or {}
    snapshot_path = Path(dashboard_cfg.get("snapshot_file", "indexing_dashboard.json"))
    if not snapshot_path.is_absolute():
        snapshot_path = output_dir / snapshot_path

    checkpoint_path = output_dir / f"indexing_progress.{collection_slug}.json"

    stop_cfg = config.get("indexing", {}).get("stop_after_batch", {}) or {}
    stop_flag_path = Path(stop_cfg.get("flag_file", "stop_after_batch.flag"))
    if not stop_flag_path.is_absolute():
        stop_flag_path = output_dir / stop_flag_path

    return snapshot_path, checkpoint_path, stop_flag_path


def format_elapsed(seconds: float) -> str:
    """Format seconds as HH:MM:SS."""
    return str(timedelta(seconds=int(max(seconds, 0))))


def build_view(snapshot: Dict[str, Any], checkpoint: Dict[str, Any], stop_flag_path: Path) -> Panel:
    """Render a progress dashboard panel."""
    table = Table.grid(padding=(0, 1))
    table.add_column()

    updated_at = snapshot.get("updated_at")
    header = Text("Re-Searcher Progress Dashboard", style="bold cyan")
    if updated_at:
        header.append(f"  updated {updated_at}", style="dim")
    table.add_row(header)
    table.add_row("")

    stage = snapshot.get("stage", "Waiting for snapshot...")
    stage_num = snapshot.get("stage_num", 0)
    total_stages = snapshot.get("total_stages", 0)
    table.add_row(f"Stage: {stage_num}/{total_stages}  {stage}")

    activity = snapshot.get("activity", {}) or {}
    activity_message = activity.get("message") or "No current activity reported"
    table.add_row(f"Activity: {activity_message}")
    batch_match = re.search(r"batch\s+(\d+)\s*(?:/|of)\s*(\d+)", activity_message, re.IGNORECASE)
    if batch_match:
        table.add_row(f"Batch: {batch_match.group(1)} / {batch_match.group(2)}")
    activity_detail = activity.get("detail")
    if activity_detail:
        table.add_row(Text(str(activity_detail), style="dim"))

    if stop_flag_path.exists():
        table.add_row(Text(f"Stop requested after current batch: {stop_flag_path}", style="yellow"))

    summary = snapshot.get("summary", {}) or {}
    elapsed_seconds = float(summary.get("elapsed_seconds", 0.0) or 0.0)
    table.add_row(f"Elapsed: {format_elapsed(elapsed_seconds)}")
    table.add_row("")

    stats = checkpoint.get("stats", {}) or {}
    stats_table = Table(title="Checkpoint", expand=False)
    stats_table.add_column("Metric", style="bold")
    stats_table.add_column("Value", justify="right")
    stats_table.add_row("Documents stored", str(stats.get("documents_stored", 0)))
    stats_table.add_row(
        "Pending / incomplete",
        str(max(int(stats.get("total_documents", 0)) - int(stats.get("documents_stored", 0)), 0)),
    )
    stats_table.add_row("Documents chunked", str(stats.get("documents_chunked", 0)))
    stats_table.add_row("Documents embedded", str(stats.get("documents_embedded", 0)))
    stats_table.add_row("Errors", str(stats.get("errors", 0)))
    stats_table.add_row("Total documents", str(stats.get("total_documents", 0)))
    table.add_row(stats_table)

    sources = snapshot.get("sources", {}) or {}
    if sources:
        source_table = Table(title="Sources", expand=False)
        source_table.add_column("Source", style="bold")
        source_table.add_column("Processed", justify="right")
        source_table.add_column("Total", justify="right")
        source_table.add_column("%", justify="right")
        for name, src in sources.items():
            source_table.add_row(
                name,
                str(src.get("processed", 0)),
                str(src.get("total", 0)),
                f"{float(src.get('progress_pct', 0.0)):.1f}",
            )
        table.add_row(source_table)

    return Panel(table, border_style="blue")


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch live indexing progress")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Refresh interval in seconds (default: 1.0)",
    )
    args = parser.parse_args()

    if not args.config.exists():
        print(f"[ERROR] Configuration file not found: {args.config}")
        return 1

    snapshot_path, checkpoint_path, stop_flag_path = resolve_paths(args.config)
    console = Console()

    refresh_per_second = max(1, int(1 / max(args.interval, 0.1)))
    with Live(
        console=console,
        refresh_per_second=refresh_per_second,
        transient=False,
    ) as live:
        while True:
            snapshot = load_json(snapshot_path)
            checkpoint = load_json(checkpoint_path)
            live.update(build_view(snapshot, checkpoint, stop_flag_path))
            time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
