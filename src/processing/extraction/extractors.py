"""Extractor candidates for the v0.6 PDF extraction router."""

from __future__ import annotations

import time
from importlib import metadata
from typing import Protocol

from src.extract_text import extract_pdf_text

from .models import ExtractionInput, ExtractionOutput


class BaseExtractor(Protocol):
    name: str

    def extract(self, source: ExtractionInput) -> ExtractionOutput:
        """Extract text from one source."""


def _package_version(package: str) -> str:
    try:
        return metadata.version(package)
    except metadata.PackageNotFoundError:
        return ""


class ZoteroFulltextExtractor:
    """Use Zotero's local indexed fulltext as the zero-cost first candidate."""

    name = "zotero-ft-cache"

    def extract(self, source: ExtractionInput) -> ExtractionOutput:
        start = time.perf_counter()
        if source.fulltext_fetcher is None:
            return ExtractionOutput(
                text="",
                extractor=self.name,
                action="reject",
                route="zotero_ft_unavailable",
                errors=["no fulltext fetcher configured"],
                elapsed_seconds=time.perf_counter() - start,
            )

        try:
            text = source.fulltext_fetcher(source.attachment_key)
        except Exception as exc:
            return ExtractionOutput(
                text="",
                extractor=self.name,
                action="reject",
                route="zotero_ft_error",
                errors=[str(exc)],
                elapsed_seconds=time.perf_counter() - start,
            )

        if not text or not text.strip():
            return ExtractionOutput(
                text="",
                extractor=self.name,
                action="reject",
                route="zotero_ft_missing",
                errors=["empty or missing Zotero fulltext"],
                elapsed_seconds=time.perf_counter() - start,
            )

        if source.partial_fulltext_checker and source.partial_fulltext_checker(text):
            return ExtractionOutput(
                text=text,
                extractor=self.name,
                action="reject",
                route="zotero_ft_partial",
                errors=["Zotero fulltext appears partial for this attachment"],
                elapsed_seconds=time.perf_counter() - start,
            )

        return ExtractionOutput(
            text=text,
            extractor=self.name,
            extractor_version="zotero-local-api",
            action="accept",
            route="zotero_ft_candidate",
            elapsed_seconds=time.perf_counter() - start,
        )


class PdfminerExtractor:
    """Existing fast PDF extractor wrapped behind the seam."""

    name = "pdfminer"

    def __init__(self, timeout_seconds: int = 60):
        self.timeout_seconds = int(timeout_seconds)

    def extract(self, source: ExtractionInput) -> ExtractionOutput:
        start = time.perf_counter()
        try:
            text = extract_pdf_text(source.file_path, timeout_seconds=self.timeout_seconds)
        except Exception as exc:
            return ExtractionOutput(
                text="",
                extractor=self.name,
                extractor_version=_package_version("pdfminer.six"),
                action="reject",
                route="pdfminer_error",
                errors=[str(exc)],
                elapsed_seconds=time.perf_counter() - start,
            )

        if not text or not text.strip():
            return ExtractionOutput(
                text="",
                extractor=self.name,
                extractor_version=_package_version("pdfminer.six"),
                action="reject",
                route="pdfminer_empty",
                errors=["empty pdfminer extraction"],
                elapsed_seconds=time.perf_counter() - start,
            )

        return ExtractionOutput(
            text=text,
            extractor=self.name,
            extractor_version=_package_version("pdfminer.six"),
            action="accept",
            route="pdfminer_candidate",
            elapsed_seconds=time.perf_counter() - start,
        )
