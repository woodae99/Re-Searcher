"""Dataclasses for the PDF extraction seam."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from src.extraction_quality import QualityProfile


FulltextFetcher = Callable[[str], Optional[str]]
PartialFulltextChecker = Callable[[str], bool]


@dataclass(frozen=True)
class ExtractionInput:
    """Input passed to one-method extractors."""

    file_path: Path
    attachment_key: str
    content_type: str = ""
    source_metadata: Dict[str, Any] = field(default_factory=dict)
    file_size_mb: float = 0.0
    fulltext_fetcher: Optional[FulltextFetcher] = None
    partial_fulltext_checker: Optional[PartialFulltextChecker] = None


@dataclass
class ExtractionOutput:
    """Output from an extractor or the quality-gated router."""

    text: str
    extractor: str
    extractor_version: str = ""
    quality_profile: Optional[QualityProfile] = None
    action: str = "reject"
    route: str = ""
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    elapsed_seconds: float = 0.0
    fallbacks: List[Dict[str, Any]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return bool(self.text and self.text.strip()) and self.action in {
            "accept",
            "clean",
        }

    def provenance(self) -> Dict[str, Any]:
        """Metadata fields persisted on fulltext chunks and source rows."""
        quality = ""
        if self.quality_profile is not None:
            quality = (
                f"{self.quality_profile.grade}:"
                f"{self.quality_profile.action}:"
                f"{self.quality_profile.overall_score:.4f}"
            )
        return {
            "extractor": self.extractor,
            "extractor_version": self.extractor_version,
            "extract_quality": quality,
            "extract_action": self.action,
            "extract_route": self.route,
            "extract_warnings": "; ".join(self.warnings),
            "extract_elapsed_seconds": round(float(self.elapsed_seconds), 4),
            "extract_fallbacks": str(self.fallbacks) if self.fallbacks else "",
        }
