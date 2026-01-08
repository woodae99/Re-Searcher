"""Progress display for Re-Searcher indexing pipeline.

This module provides thread-safe progress tracking with two display modes:
- RichProgressDisplay: Interactive terminal with live updates (rich.Live)
- PlainProgressDisplay: Simple line-based output for CI/non-TTY environments
"""

import sys
import threading
import time
from abc import ABC, abstractmethod
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Deque, Dict, Optional

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.table import Table
from rich.text import Text


class IndexingStage(Enum):
    """Stages of the indexing pipeline."""

    INITIALIZING = "Initializing"
    FETCHING = "Fetching documents"
    CHUNKING = "Chunking documents"
    EMBEDDING = "Generating embeddings"
    STORING = "Storing vectors"
    COMPLETE = "Complete"


@dataclass
class SourceStats:
    """Statistics for a single data source."""

    name: str
    total: int = 0
    processed: int = 0
    new: int = 0
    updated: int = 0
    skipped: int = 0
    errors: int = 0

    @property
    def progress_pct(self) -> float:
        """Return progress as percentage (0-100)."""
        if self.total == 0:
            return 0.0
        return (self.processed / self.total) * 100


@dataclass
class TimingTracker:
    """Track timing for ETA calculations."""

    started_at: datetime = field(default_factory=datetime.now)
    item_times: Deque[float] = field(default_factory=lambda: deque(maxlen=100))

    def record_item(self, elapsed: float) -> None:
        """Record time taken for a single item."""
        self.item_times.append(elapsed)

    def average_time(self) -> float:
        """Return average time per item."""
        if not self.item_times:
            return 0.0
        return sum(self.item_times) / len(self.item_times)

    def estimate_remaining(self, items_left: int) -> timedelta:
        """Estimate time remaining for given number of items."""
        avg = self.average_time()
        return timedelta(seconds=avg * items_left)

    def elapsed(self) -> timedelta:
        """Return elapsed time since start."""
        return datetime.now() - self.started_at


@dataclass
class CurrentActivity:
    """Current activity being performed."""

    message: str = ""
    detail: str = ""
    file_name: str = ""
    file_size_mb: float = 0.0
    progress_pct: float = 0.0  # For large file progress


class ProgressDisplay(ABC):
    """Abstract base class for progress displays."""

    @abstractmethod
    def start(self) -> None:
        """Start the progress display."""
        pass

    @abstractmethod
    def stop(self) -> None:
        """Stop the progress display."""
        pass

    @abstractmethod
    def set_stage(self, stage: IndexingStage, stage_num: int, total_stages: int) -> None:
        """Set the current pipeline stage."""
        pass

    @abstractmethod
    def init_source(self, name: str, total: int) -> None:
        """Initialize a source with its total item count."""
        pass

    @abstractmethod
    def update_source(
        self,
        name: str,
        processed: int = 0,
        new: int = 0,
        updated: int = 0,
        skipped: int = 0,
        errors: int = 0,
    ) -> None:
        """Update statistics for a source."""
        pass

    @abstractmethod
    def set_activity(
        self,
        message: str,
        file_name: str = "",
        file_size_mb: float = 0.0,
        detail: str = "",
    ) -> None:
        """Set the current activity message."""
        pass

    @abstractmethod
    def update_file_progress(self, progress_pct: float) -> None:
        """Update progress for current large file."""
        pass


class RichProgressDisplay(ProgressDisplay):
    """Interactive progress display using rich.Live."""

    def __init__(self):
        self.console = Console()
        self._lock = threading.Lock()
        self._live: Optional[Live] = None

        # State
        self.stage = IndexingStage.INITIALIZING
        self.stage_num = 0
        self.total_stages = 4
        self.sources: Dict[str, SourceStats] = {}
        self.timing = TimingTracker()
        self.activity = CurrentActivity()

    def start(self) -> None:
        """Start the live display."""
        self._live = Live(
            self._render(),
            console=self.console,
            refresh_per_second=2,
            transient=False,
        )
        self._live.start()

    def stop(self) -> None:
        """Stop the live display."""
        if self._live:
            self._live.stop()
            self._live = None

    def set_stage(self, stage: IndexingStage, stage_num: int, total_stages: int) -> None:
        """Set the current pipeline stage."""
        with self._lock:
            self.stage = stage
            self.stage_num = stage_num
            self.total_stages = total_stages
            self._refresh()

    def init_source(self, name: str, total: int) -> None:
        """Initialize a source with its total item count."""
        with self._lock:
            self.sources[name] = SourceStats(name=name, total=total)
            self._refresh()

    def update_source(
        self,
        name: str,
        processed: int = 0,
        new: int = 0,
        updated: int = 0,
        skipped: int = 0,
        errors: int = 0,
    ) -> None:
        """Update statistics for a source (incremental)."""
        with self._lock:
            if name not in self.sources:
                self.sources[name] = SourceStats(name=name)

            stats = self.sources[name]
            stats.processed += processed
            stats.new += new
            stats.updated += updated
            stats.skipped += skipped
            stats.errors += errors
            self._refresh()

    def set_activity(
        self,
        message: str,
        file_name: str = "",
        file_size_mb: float = 0.0,
        detail: str = "",
    ) -> None:
        """Set the current activity message."""
        with self._lock:
            self.activity.message = message
            self.activity.detail = detail
            self.activity.file_name = file_name
            self.activity.file_size_mb = file_size_mb
            self.activity.progress_pct = 0.0
            self._refresh()

    def update_file_progress(self, progress_pct: float) -> None:
        """Update progress for current large file."""
        with self._lock:
            self.activity.progress_pct = progress_pct
            self._refresh()

    def _refresh(self) -> None:
        """Refresh the display."""
        if self._live:
            self._live.update(self._render())

    def _render(self) -> Panel:
        """Render the progress display."""
        # Build content
        content = Table.grid(padding=(0, 1))
        content.add_column()

        # Stage header
        stage_text = Text()
        stage_text.append(f"Stage {self.stage_num}/{self.total_stages}: ", style="bold")
        stage_text.append(self.stage.value, style="cyan")
        content.add_row(stage_text)
        content.add_row("")

        # Source progress bars
        for name, stats in self.sources.items():
            progress_bar = self._make_progress_bar(stats)
            content.add_row(progress_bar)

        if self.sources:
            content.add_row("")

        # Current activity
        if self.activity.message:
            activity_text = Text()
            activity_text.append("Current: ", style="dim")
            activity_text.append(self.activity.message)
            if self.activity.file_name:
                activity_text.append(f" ({self.activity.file_name}", style="dim")
                if self.activity.file_size_mb > 0:
                    activity_text.append(f", {self.activity.file_size_mb:.1f}MB", style="dim")
                activity_text.append(")", style="dim")
            content.add_row(activity_text)

            if self.activity.detail:
                detail_text = Text()
                detail_text.append(self.activity.detail)
                content.add_row(detail_text)

            # Large file progress bar
            if self.activity.file_size_mb > 10 and self.activity.progress_pct > 0:
                file_bar = self._make_file_progress_bar()
                content.add_row(file_bar)

            content.add_row("")

        # Stats summary
        total_new = sum(s.new for s in self.sources.values())
        total_updated = sum(s.updated for s in self.sources.values())
        total_skipped = sum(s.skipped for s in self.sources.values())
        total_errors = sum(s.errors for s in self.sources.values())

        stats_text = Text()
        stats_text.append("Stats: ", style="dim")
        stats_text.append(f"{total_new} new", style="green")
        stats_text.append(" | ")
        stats_text.append(f"{total_updated} updated", style="yellow")
        stats_text.append(" | ")
        stats_text.append(f"{total_skipped} skipped", style="dim")
        stats_text.append(" | ")
        if total_errors > 0:
            stats_text.append(f"{total_errors} errors", style="red bold")
        else:
            stats_text.append(f"{total_errors} errors")
        content.add_row(stats_text)

        # Timing
        elapsed = self.timing.elapsed()
        elapsed_str = str(elapsed).split(".")[0]  # Remove microseconds

        # Calculate ETA
        total_items = sum(s.total for s in self.sources.values())
        processed_items = sum(s.processed for s in self.sources.values())
        remaining_items = total_items - processed_items

        timing_text = Text()
        timing_text.append("Time: ", style="dim")
        timing_text.append(f"{elapsed_str} elapsed")

        if remaining_items > 0 and processed_items > 0:
            avg_seconds = elapsed.total_seconds() / processed_items
            eta_seconds = avg_seconds * remaining_items
            eta = timedelta(seconds=int(eta_seconds))
            eta_str = str(eta)
            eta_clock = datetime.now() + eta
            eta_clock_str = eta_clock.strftime("%H:%M:%S")
            timing_text.append(f" | Remaining: {eta_str}", style="dim")
            timing_text.append(f" | ETA: {eta_clock_str}", style="dim")

        content.add_row(timing_text)

        return Panel(
            content,
            title="Re-Searcher Indexing Pipeline",
            border_style="blue",
        )

    def _make_progress_bar(self, stats: SourceStats) -> Text:
        """Create a progress bar for a source."""
        bar_width = 20
        filled = int((stats.progress_pct / 100) * bar_width)
        empty = bar_width - filled

        text = Text()
        text.append(f"  {stats.name:10s} ", style="bold")
        text.append("[")
        text.append("=" * filled, style="green")
        text.append("-" * empty, style="dim")
        text.append("]  ")
        text.append(f"{stats.processed}/{stats.total} items", style="dim")
        return text

    def _make_file_progress_bar(self) -> Text:
        """Create a progress bar for current large file."""
        bar_width = 20
        filled = int((self.activity.progress_pct / 100) * bar_width)
        empty = bar_width - filled

        text = Text()
        text.append("           ")  # Indent
        text.append("[")
        text.append("=" * filled, style="cyan")
        text.append("-" * empty, style="dim")
        text.append("]  ")
        text.append(f"{self.activity.progress_pct:.0f}%", style="dim")
        return text


class PlainProgressDisplay(ProgressDisplay):
    """Simple line-based progress display for non-TTY environments."""

    def __init__(self, update_interval: float = 5.0):
        """
        Initialize plain progress display.

        Args:
            update_interval: Minimum seconds between progress updates
        """
        self._lock = threading.Lock()
        self.update_interval = update_interval
        self._last_update = 0.0

        # State
        self.stage = IndexingStage.INITIALIZING
        self.stage_num = 0
        self.total_stages = 4
        self.sources: Dict[str, SourceStats] = {}
        self.timing = TimingTracker()
        self.activity = CurrentActivity()

    def start(self) -> None:
        """Start the progress display."""
        print("=" * 65)
        print("Re-Searcher Indexing Pipeline")
        print("=" * 65)

    def stop(self) -> None:
        """Stop the progress display."""
        self._print_final_summary()

    def set_stage(self, stage: IndexingStage, stage_num: int, total_stages: int) -> None:
        """Set the current pipeline stage."""
        with self._lock:
            self.stage = stage
            self.stage_num = stage_num
            self.total_stages = total_stages
            print(f"\n[Stage {stage_num}/{total_stages}] {stage.value}")

    def init_source(self, name: str, total: int) -> None:
        """Initialize a source with its total item count."""
        with self._lock:
            self.sources[name] = SourceStats(name=name, total=total)
            print(f"  {name}: {total} items")

    def update_source(
        self,
        name: str,
        processed: int = 0,
        new: int = 0,
        updated: int = 0,
        skipped: int = 0,
        errors: int = 0,
    ) -> None:
        """Update statistics for a source (incremental)."""
        with self._lock:
            if name not in self.sources:
                self.sources[name] = SourceStats(name=name)

            stats = self.sources[name]
            stats.processed += processed
            stats.new += new
            stats.updated += updated
            stats.skipped += skipped
            stats.errors += errors

            self._maybe_print_progress()

    def set_activity(
        self,
        message: str,
        file_name: str = "",
        file_size_mb: float = 0.0,
        detail: str = "",
    ) -> None:
        """Set the current activity message."""
        with self._lock:
            self.activity.message = message
            self.activity.detail = detail
            self.activity.file_name = file_name
            self.activity.file_size_mb = file_size_mb
            # Don't print every activity in plain mode - too noisy

    def update_file_progress(self, progress_pct: float) -> None:
        """Update progress for current large file."""
        with self._lock:
            self.activity.progress_pct = progress_pct
            # Don't print file progress in plain mode

    def _maybe_print_progress(self) -> None:
        """Print progress if enough time has passed."""
        now = time.time()
        if now - self._last_update < self.update_interval:
            return

        self._last_update = now
        self._print_progress_line()

    def _print_progress_line(self) -> None:
        """Print a single progress line."""
        parts = []

        # Stage
        parts.append(f"[Stage {self.stage_num}/{self.total_stages}]")

        # Source progress
        for name, stats in self.sources.items():
            pct = stats.progress_pct
            parts.append(f"{name}: {stats.processed}/{stats.total} ({pct:.0f}%)")

        # Stats
        total_new = sum(s.new for s in self.sources.values())
        total_errors = sum(s.errors for s in self.sources.values())
        parts.append(f"{total_new} new, {total_errors} errors")

        # ETA
        total_items = sum(s.total for s in self.sources.values())
        processed_items = sum(s.processed for s in self.sources.values())
        remaining_items = total_items - processed_items

        if remaining_items > 0 and self.timing.item_times:
            eta = self.timing.estimate_remaining(remaining_items)
            eta_str = str(eta).split(".")[0]
            parts.append(f"ETA: {eta_str}")

        print("  " + " | ".join(parts))

    def _print_final_summary(self) -> None:
        """Print final summary."""
        print("\n" + "=" * 65)
        print("Indexing Complete")
        print("=" * 65)

        elapsed = self.timing.elapsed()
        elapsed_str = str(elapsed).split(".")[0]
        print(f"Total time: {elapsed_str}")

        for name, stats in self.sources.items():
            print(f"  {name}: {stats.processed} processed ({stats.new} new, {stats.errors} errors)")

        print("=" * 65)


class QuietProgressDisplay(ProgressDisplay):
    """No-op progress display for quiet mode."""

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def set_stage(self, stage: IndexingStage, stage_num: int, total_stages: int) -> None:
        pass

    def init_source(self, name: str, total: int) -> None:
        pass

    def update_source(
        self,
        name: str,
        processed: int = 0,
        new: int = 0,
        updated: int = 0,
        skipped: int = 0,
        errors: int = 0,
    ) -> None:
        pass

    def set_activity(
        self,
        message: str,
        file_name: str = "",
        file_size_mb: float = 0.0,
        detail: str = "",
    ) -> None:
        pass

    def update_file_progress(self, progress_pct: float) -> None:
        pass


def create_progress_display(mode: str = "auto") -> ProgressDisplay:
    """
    Create a progress display based on the specified mode.

    Args:
        mode: Display mode - "auto", "rich", "plain", or "quiet"

    Returns:
        Appropriate ProgressDisplay instance
    """
    if mode == "quiet":
        return QuietProgressDisplay()
    elif mode == "plain":
        return PlainProgressDisplay()
    elif mode == "rich":
        return RichProgressDisplay()
    elif mode == "auto":
        # Auto-detect based on TTY
        if sys.stdout.isatty():
            return RichProgressDisplay()
        else:
            return PlainProgressDisplay()
    else:
        raise ValueError(f"Unknown progress display mode: {mode}")
