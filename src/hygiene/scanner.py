"""Document hygiene scanner for pre-indexing quality checks."""

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Set

from .checks import (
    Issue,
    check_chunk_potential,
    check_obsidian_quality,
    check_pdf_quality,
    check_text_quality,
)


@dataclass
class DocumentIssue:
    """Represents a document with quality issues."""

    doc_id: str
    source: str  # "zotero" or "obsidian"
    title: str
    file_path: str
    metadata: Dict[str, Any]
    issues: List[Issue]

    @property
    def severity(self) -> str:
        """Return the highest severity among all issues."""
        if any(i.severity == "error" for i in self.issues):
            return "error"
        if any(i.severity == "warning" for i in self.issues):
            return "warning"
        return "info"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "doc_id": self.doc_id,
            "source": self.source,
            "title": self.title,
            "file_path": self.file_path,
            "severity": self.severity,
            "issues": [
                {
                    "check": i.check,
                    "severity": i.severity,
                    "message": i.message,
                    "suggestion": i.suggestion,
                    "details": i.details,
                }
                for i in self.issues
            ],
        }


@dataclass
class HygieneReport:
    """Complete hygiene scan report."""

    scan_date: datetime
    config: Dict[str, Any]
    documents: List[DocumentIssue] = field(default_factory=list)

    @property
    def summary(self) -> Dict[str, int]:
        """Generate summary statistics."""
        errors = sum(1 for d in self.documents if d.severity == "error")
        warnings = sum(1 for d in self.documents if d.severity == "warning")
        infos = sum(1 for d in self.documents if d.severity == "info")
        return {
            "total_with_issues": len(self.documents),
            "errors": errors,
            "warnings": warnings,
            "info": infos,
        }

    @property
    def exclusion_list(self) -> List[str]:
        """Return list of doc_ids with errors (recommended for exclusion)."""
        return [d.doc_id for d in self.documents if d.severity == "error"]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "scan_date": self.scan_date.isoformat(),
            "config": self.config,
            "summary": self.summary,
            "documents": [d.to_dict() for d in self.documents],
            "exclusion_list": self.exclusion_list,
        }

    def to_json(self, path: Path) -> None:
        """Write report to JSON file."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, ensure_ascii=False)

    def to_markdown(self, path: Path) -> None:
        """Write report to Markdown file."""
        path.parent.mkdir(parents=True, exist_ok=True)

        lines = []
        lines.append("# Document Hygiene Report")
        lines.append(f"\nGenerated: {self.scan_date.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        # Summary
        summary = self.summary
        lines.append("## Summary")
        lines.append("")
        lines.append("| Status | Count |")
        lines.append("|--------|-------|")
        lines.append(f"| Errors | {summary['errors']} |")
        lines.append(f"| Warnings | {summary['warnings']} |")
        lines.append(f"| Info | {summary['info']} |")
        lines.append(f"| **Total with issues** | **{summary['total_with_issues']}** |")
        lines.append("")

        # Group by severity
        errors = [d for d in self.documents if d.severity == "error"]
        warnings = [d for d in self.documents if d.severity == "warning"]
        infos = [d for d in self.documents if d.severity == "info"]

        # Errors section
        if errors:
            lines.append(f"## Errors ({len(errors)} documents)")
            lines.append("")
            lines.append("These documents have critical issues and are recommended for exclusion.")
            lines.append("")
            for doc in errors:
                lines.extend(self._format_document_issues(doc))

        # Warnings section
        if warnings:
            lines.append(f"## Warnings ({len(warnings)} documents)")
            lines.append("")
            lines.append("These documents have quality concerns but may still be usable.")
            lines.append("")
            for doc in warnings:
                lines.extend(self._format_document_issues(doc))

        # Info section
        if infos:
            lines.append(f"## Info ({len(infos)} documents)")
            lines.append("")
            lines.append("These documents have minor notes for your awareness.")
            lines.append("")
            for doc in infos:
                lines.extend(self._format_document_issues(doc))

        # Exclusion list
        if self.exclusion_list:
            lines.append("## Exclusion List")
            lines.append("")
            lines.append("To skip these documents during indexing, add to `config.yaml`:")
            lines.append("")
            lines.append("```yaml")
            lines.append("indexing:")
            lines.append("  exclude_ids:")
            for doc_id in self.exclusion_list[:20]:  # Limit to first 20
                lines.append(f"    - \"{doc_id}\"")
            if len(self.exclusion_list) > 20:
                lines.append(f"    # ... and {len(self.exclusion_list) - 20} more (see JSON report)")
            lines.append("```")
            lines.append("")

        with open(path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _format_document_issues(self, doc: DocumentIssue) -> List[str]:
        """Format a single document's issues for markdown."""
        lines = []
        lines.append(f"### {doc.title or 'Untitled'}")
        lines.append("")
        lines.append(f"- **Source:** {doc.source}")
        lines.append(f"- **File:** `{doc.file_path}`")
        if doc.doc_id:
            lines.append(f"- **ID:** `{doc.doc_id}`")
        lines.append("")

        for issue in doc.issues:
            severity_icon = {"error": "X", "warning": "!", "info": "i"}[issue.severity]
            lines.append(f"**[{severity_icon}] {issue.check}**: {issue.message}")
            lines.append(f"  - *Suggestion:* {issue.suggestion}")
            lines.append("")

        lines.append("---")
        lines.append("")
        return lines


class DocumentHygieneScanner:
    """Scans documents for quality issues before indexing."""

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize the scanner.

        Args:
            config: Configuration dictionary (same format as config.yaml)
        """
        self.config = config
        self.chunk_size = config.get("chunking", {}).get("chunk_size", 2048)
        self.chunk_overlap = config.get("chunking", {}).get("chunk_overlap", 256)

        # Track vault files for orphan link detection
        self._vault_files: Optional[Set[str]] = None

    def scan_all(
        self,
        limit: Optional[int] = None,
        zotero_only: bool = False,
        obsidian_only: bool = False,
        verbose: bool = False,
        progress_callback=None,
    ) -> HygieneReport:
        """
        Scan all configured sources for quality issues.

        Args:
            limit: Maximum number of documents to scan per source
            zotero_only: Only scan Zotero documents
            obsidian_only: Only scan Obsidian documents
            verbose: Print progress information
            progress_callback: Optional callback(current, total, message)

        Returns:
            HygieneReport with all issues found
        """
        report = HygieneReport(
            scan_date=datetime.now(),
            config={
                "zotero_enabled": self.config.get("zotero", {}).get("enabled", False),
                "obsidian_enabled": self.config.get("obsidian", {}).get("enabled", False),
                "chunk_size": self.chunk_size,
                "chunk_overlap": self.chunk_overlap,
            },
        )

        # Scan Zotero
        if not obsidian_only and self.config.get("zotero", {}).get("enabled", False):
            if verbose:
                print("\nScanning Zotero documents...")
            for doc_issue in self._scan_zotero(limit=limit, verbose=verbose, progress_callback=progress_callback):
                report.documents.append(doc_issue)

        # Scan Obsidian
        if not zotero_only and self.config.get("obsidian", {}).get("enabled", False):
            if verbose:
                print("\nScanning Obsidian documents...")
            for doc_issue in self._scan_obsidian(limit=limit, verbose=verbose, progress_callback=progress_callback):
                report.documents.append(doc_issue)

        return report

    def _scan_zotero(
        self,
        limit: Optional[int] = None,
        verbose: bool = False,
        progress_callback=None,
    ) -> Iterator[DocumentIssue]:
        """Scan Zotero documents for issues."""
        from ..sources.zotero import ZoteroSource

        zotero = ZoteroSource(self.config)
        if not zotero.validate_config():
            if verbose:
                print("  Zotero configuration invalid, skipping...")
            return

        # Count items first for progress
        count = 0
        for doc in zotero.fetch_documents():
            count += 1
            if limit and count > limit:
                break

            issues = []

            # Get file info for PDF checks
            file_path = doc.metadata.get("file_path")
            file_size = None
            if file_path:
                try:
                    file_size = Path(file_path).stat().st_size
                except (OSError, FileNotFoundError):
                    pass

            # PDF quality checks
            source_type = doc.metadata.get("source_type", "")
            if source_type == "zotero_fulltext":
                pdf_issues = check_pdf_quality(
                    content=doc.content,
                    file_path=Path(file_path) if file_path else None,
                    file_size_bytes=file_size,
                )
                issues.extend(pdf_issues)

            # Text quality checks (for all content types)
            if doc.content:
                text_issues = check_text_quality(doc.content)
                issues.extend(text_issues)

                # Chunk potential checks
                chunk_issues = check_chunk_potential(
                    doc.content,
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                )
                issues.extend(chunk_issues)

            # Only yield if there are issues
            if issues:
                yield DocumentIssue(
                    doc_id=doc.doc_id,
                    source="zotero",
                    title=doc.metadata.get("title", "Untitled"),
                    file_path=file_path or "",
                    metadata=doc.metadata,
                    issues=issues,
                )

            if progress_callback:
                progress_callback(count, None, f"Scanned: {doc.metadata.get('title', 'Untitled')[:50]}")

    def _scan_obsidian(
        self,
        limit: Optional[int] = None,
        verbose: bool = False,
        progress_callback=None,
    ) -> Iterator[DocumentIssue]:
        """Scan Obsidian documents for issues."""
        from ..sources.obsidian import ObsidianSource

        obsidian = ObsidianSource(self.config)
        if not obsidian.validate_config():
            if verbose:
                print("  Obsidian configuration invalid, skipping...")
            return

        # Build vault file index for orphan link detection
        if self._vault_files is None:
            self._vault_files = self._build_vault_index(obsidian)

        count = 0
        for doc in obsidian.fetch_documents():
            count += 1
            if limit and count > limit:
                break

            issues = []

            # Obsidian-specific checks
            obsidian_issues = check_obsidian_quality(
                content=doc.content,
                metadata=doc.metadata,
                vault_files=self._vault_files,
            )
            issues.extend(obsidian_issues)

            # Text quality checks
            if doc.content:
                text_issues = check_text_quality(doc.content)
                issues.extend(text_issues)

                # Chunk potential checks
                chunk_issues = check_chunk_potential(
                    doc.content,
                    chunk_size=self.chunk_size,
                    chunk_overlap=self.chunk_overlap,
                )
                issues.extend(chunk_issues)

            # Only yield if there are issues
            if issues:
                yield DocumentIssue(
                    doc_id=doc.doc_id,
                    source="obsidian",
                    title=doc.metadata.get("title", doc.metadata.get("file_name", "Untitled")),
                    file_path=doc.metadata.get("file_path", ""),
                    metadata=doc.metadata,
                    issues=issues,
                )

            if progress_callback:
                progress_callback(count, None, f"Scanned: {doc.metadata.get('title', 'Untitled')[:50]}")

    def _build_vault_index(self, obsidian_source) -> Set[str]:
        """Build index of all files in vault for orphan link detection."""
        vault_files = set()
        vault_path = Path(obsidian_source.vault_path)

        for md_file in vault_path.rglob("*.md"):
            # Add both with and without extension, lowercased for comparison
            vault_files.add(md_file.stem.lower())
            vault_files.add(md_file.name.lower())

        return vault_files
