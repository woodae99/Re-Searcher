"""Quality-gated extraction router for Zotero PDF/fulltext documents."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from src.extraction_quality import (
    QualityProfile,
    QualityThresholds,
    deterministic_clean,
    profile_text,
)

from .extractors import BaseExtractor, PdfminerExtractor, ZoteroFulltextExtractor
from .models import ExtractionInput, ExtractionOutput


class ExtractionRouter:
    """Run extractor candidates until one passes the quality gate."""

    def __init__(
        self,
        config: Dict[str, Any],
        *,
        candidates: Optional[List[BaseExtractor]] = None,
        dictionary: Optional[frozenset] = None,
        thresholds: Optional[QualityThresholds] = None,
    ):
        self.config = config
        extraction_cfg = config.get("extraction", {}) or {}
        router_cfg = extraction_cfg.get("router", {}) or {}
        self.enabled = bool(router_cfg.get("enabled", True))
        self.marker_enabled = bool(router_cfg.get("marker_enabled", False))
        self.dictionary = dictionary
        self.thresholds = thresholds

        if candidates is not None:
            self.candidates = candidates
        else:
            pdfminer_timeout = int(router_cfg.get("pdfminer_timeout_seconds", 60))
            self.candidates = [
                ZoteroFulltextExtractor(),
                PdfminerExtractor(timeout_seconds=pdfminer_timeout),
            ]

    def extract(self, source: ExtractionInput) -> ExtractionOutput:
        """Extract text and return the first candidate that passes the gate."""
        if not self.enabled:
            output = PdfminerExtractor().extract(source)
            return self._score_output(output, route_suffix="router_disabled")

        fallbacks: List[Dict[str, Any]] = []
        last_output: Optional[ExtractionOutput] = None

        for candidate in self.candidates:
            output = candidate.extract(source)
            output = self._score_output(output)
            output.fallbacks = list(fallbacks)
            last_output = output

            if output.ok:
                return output

            fallbacks.append(
                {
                    "extractor": output.extractor,
                    "route": output.route,
                    "action": output.action,
                    "quality": (
                        output.quality_profile.grade
                        if output.quality_profile is not None
                        else ""
                    ),
                    "errors": output.errors,
                    "warnings": output.warnings,
                }
            )

        if last_output is None:
            last_output = ExtractionOutput(
                text="",
                extractor="none",
                action="reject",
                route="no_candidates",
                errors=["no extractor candidates configured"],
            )

        if not self.marker_enabled and last_output.action == "escalate":
            last_output.warnings.append("marker escalation disabled by config")
            last_output.route = f"{last_output.route}_marker_disabled"

        last_output.fallbacks = fallbacks
        return last_output

    def _profile(self, text: str) -> QualityProfile:
        return profile_text(
            text,
            dictionary=self.dictionary,
            thresholds=self.thresholds,
        )

    def _score_output(
        self,
        output: ExtractionOutput,
        *,
        route_suffix: str = "",
    ) -> ExtractionOutput:
        if output.errors:
            output.action = "reject"
            if route_suffix:
                output.route = f"{output.route}_{route_suffix}" if output.route else route_suffix
            return output

        if not output.text or not output.text.strip():
            output.action = "reject"
            if route_suffix:
                output.route = f"{output.route}_{route_suffix}" if output.route else route_suffix
            return output

        profile = self._profile(output.text)
        output.quality_profile = profile

        if profile.action == "accept":
            output.action = "accept"
            output.route = self._route(output.extractor, "passed", route_suffix)
            return output

        if profile.action == "clean":
            cleaned = deterministic_clean(output.text)
            cleaned_profile = self._profile(cleaned)
            output.text = cleaned
            output.quality_profile = cleaned_profile
            output.warnings.extend(profile.notes)
            if cleaned_profile.action == "accept":
                output.action = "clean"
                output.route = self._route(output.extractor, "cleaned", route_suffix)
                return output
            if cleaned_profile.action == "clean":
                output.action = "clean"
                output.route = self._route(output.extractor, "cleaned_borderline", route_suffix)
                return output

        output.action = "escalate" if profile.action == "escalate" else "reject"
        output.route = self._route(output.extractor, profile.action, route_suffix)
        output.warnings.extend(profile.notes)
        return output

    @staticmethod
    def _route(extractor: str, decision: str, suffix: str = "") -> str:
        route = f"{extractor}_{decision}".replace("-", "_")
        return f"{route}_{suffix}" if suffix else route
