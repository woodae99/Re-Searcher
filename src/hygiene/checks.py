"""Quality check functions for document hygiene scanning."""

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


@dataclass
class Issue:
    """Represents a single quality issue found in a document."""

    check: str  # e.g., "no_text_extracted", "high_garbage_ratio"
    severity: str  # "error", "warning", "info"
    message: str  # Human-readable description
    suggestion: str  # How to fix
    details: Dict[str, Any] = field(default_factory=dict)  # Additional context


# Common mojibake patterns (UTF-8 decoded as Latin-1 or similar)
# These are byte sequences that appear when UTF-8 text is misinterpreted
MOJIBAKE_PATTERNS = [
    r"\xc3\xa9",  # UTF-8 é misread
    r"\xc3\xa8",  # UTF-8 è misread
    r"\xc3\xa0",  # UTF-8 à misread
    r"\xc3\xa2",  # UTF-8 â misread
    r"\xc3\xae",  # UTF-8 î misread
    r"\xc3\xb4",  # UTF-8 ô misread
    r"\xc3\xbb",  # UTF-8 û misread
    r"\xc3\xa7",  # UTF-8 ç misread
    r"\xc3\xb1",  # UTF-8 ñ misread
    r"\xc3\xbc",  # UTF-8 ü misread
    r"\xc3\xb6",  # UTF-8 ö misread
    r"\xc3\xa4",  # UTF-8 ä misread
    r"\xef\xbb\xbf",  # UTF-8 BOM
    r"\ufffd",  # Unicode replacement character
    # Common smart quote mojibake patterns
    r"\xe2\x80\x99",  # ' (right single quote)
    r"\xe2\x80\x9c",  # " (left double quote)
    r"\xe2\x80\x9d",  # " (right double quote)
    r"\xe2\x80\x93",  # – (en dash)
    r"\xe2\x80\x94",  # — (em dash)
]

# Compile regex for efficiency
MOJIBAKE_REGEX = re.compile("|".join(MOJIBAKE_PATTERNS))

# Pattern for excessive repeated characters
REPEATED_CHARS_REGEX = re.compile(r"(.)\1{10,}")  # Same char 10+ times

# Pattern for repeated sequences (like "......" or "======")
REPEATED_SEQUENCES_REGEX = re.compile(r"(.{2,5})\1{5,}")  # 2-5 char sequence repeated 5+ times


def check_pdf_quality(
    content: str,
    file_path: Optional[Path] = None,
    file_size_bytes: Optional[int] = None,
    extraction_timeout: bool = False,
    extraction_error: Optional[str] = None,
) -> List[Issue]:
    """
    Check PDF-specific quality issues.

    Args:
        content: Extracted text content from PDF
        file_path: Path to the PDF file (for context)
        file_size_bytes: Size of the PDF file in bytes
        extraction_timeout: Whether extraction timed out
        extraction_error: Any error message from extraction

    Returns:
        List of Issue objects describing problems found
    """
    issues = []

    # Check for extraction timeout
    if extraction_timeout:
        issues.append(
            Issue(
                check="extraction_timeout",
                severity="error",
                message="PDF extraction timed out (likely scanned/image-heavy)",
                suggestion="Run OCR using Adobe Acrobat, ocrmypdf, or similar tool",
                details={"file_path": str(file_path) if file_path else None},
            )
        )
        return issues  # No point checking content if extraction failed

    # Check for extraction error
    if extraction_error:
        issues.append(
            Issue(
                check="extraction_error",
                severity="error",
                message=f"PDF extraction failed: {extraction_error}",
                suggestion="Check if PDF is corrupted, password-protected, or uses unsupported encoding",
                details={"error": extraction_error},
            )
        )
        return issues

    # Check for no text extracted
    stripped_content = content.strip() if content else ""
    if not stripped_content:
        issues.append(
            Issue(
                check="no_text_extracted",
                severity="error",
                message="No text could be extracted from PDF",
                suggestion="PDF may be scanned without OCR layer. Run OCR using Adobe Acrobat or ocrmypdf",
            )
        )
        return issues

    # Check for very low text density (suggests partial OCR or mostly images)
    if file_size_bytes and file_size_bytes > 0:
        text_density = len(stripped_content) / file_size_bytes
        if text_density < 0.001:  # Less than 1 char per KB
            issues.append(
                Issue(
                    check="low_text_density",
                    severity="warning",
                    message=f"Very low text density ({text_density:.4f} chars/byte)",
                    suggestion="PDF may be partially OCR'd or contain mostly images",
                    details={
                        "text_length": len(stripped_content),
                        "file_size": file_size_bytes,
                        "density": text_density,
                    },
                )
            )

    # Check for very short content (might be just headers/metadata)
    if len(stripped_content) < 100:
        issues.append(
            Issue(
                check="minimal_content",
                severity="warning",
                message=f"PDF contains very little text ({len(stripped_content)} characters)",
                suggestion="Check if PDF is a cover page, placeholder, or needs OCR",
                details={"text_length": len(stripped_content)},
            )
        )

    return issues


def check_text_quality(content: str) -> List[Issue]:
    """
    Check for garbage characters, encoding issues, and text quality problems.

    Args:
        content: Text content to check

    Returns:
        List of Issue objects describing problems found
    """
    issues = []

    if not content:
        return issues

    content_length = len(content)

    # Count non-printable characters (excluding common whitespace)
    non_printable_count = sum(
        1
        for c in content
        if not c.isprintable() and c not in "\n\r\t"
    )
    non_printable_ratio = non_printable_count / content_length if content_length > 0 else 0

    if non_printable_ratio > 0.05:  # More than 5% non-printable
        issues.append(
            Issue(
                check="high_non_printable_ratio",
                severity="warning",
                message=f"High ratio of non-printable characters ({non_printable_ratio:.1%})",
                suggestion="Text may have encoding issues or contain binary data",
                details={
                    "non_printable_count": non_printable_count,
                    "total_chars": content_length,
                    "ratio": non_printable_ratio,
                },
            )
        )

    # Check for control characters (excluding common ones)
    # Form feed (0x0C) is common in PDFs for page breaks, so we allow that too
    control_chars = sum(
        1
        for c in content
        if ord(c) < 32 and c not in "\n\r\t\x0c"
    )
    # Use a ratio-based threshold: more than 0.1% control chars is suspicious
    control_ratio = control_chars / content_length if content_length > 0 else 0
    if control_ratio > 0.001 or control_chars > 100:  # Either >0.1% or >100 absolute
        issues.append(
            Issue(
                check="control_character_spam",
                severity="warning",  # Downgrade to warning since many PDFs have some
                message=f"Contains {control_chars} control characters ({control_ratio:.2%} of content)",
                suggestion="Text may have extraction artifacts - review if content looks garbled",
                details={
                    "control_char_count": control_chars,
                    "ratio": control_ratio,
                },
            )
        )

    # Check for mojibake patterns
    mojibake_matches = MOJIBAKE_REGEX.findall(content)
    if len(mojibake_matches) > 5:
        issues.append(
            Issue(
                check="mojibake_detected",
                severity="warning",
                message=f"Detected {len(mojibake_matches)} encoding error patterns (mojibake)",
                suggestion="Source file may need re-encoding or re-extraction with correct charset",
                details={
                    "match_count": len(mojibake_matches),
                    "sample_matches": mojibake_matches[:5],
                },
            )
        )

    # Check for excessive repeated characters
    repeated_matches = list(REPEATED_CHARS_REGEX.finditer(content))
    if len(repeated_matches) > 3:
        issues.append(
            Issue(
                check="excessive_repeated_chars",
                severity="warning",
                message=f"Found {len(repeated_matches)} instances of excessively repeated characters",
                suggestion="Text may have extraction artifacts or formatting issues",
                details={
                    "match_count": len(repeated_matches),
                    "samples": [m.group()[:20] for m in repeated_matches[:3]],
                },
            )
        )

    # Check for repeated sequences (like "......" or "======")
    sequence_matches = list(REPEATED_SEQUENCES_REGEX.finditer(content))
    total_sequence_length = sum(len(m.group()) for m in sequence_matches)
    if total_sequence_length > content_length * 0.1:  # More than 10% is repeated sequences
        issues.append(
            Issue(
                check="excessive_repeated_sequences",
                severity="warning",
                message="Large portion of text is repeated character sequences",
                suggestion="Text may have OCR artifacts or table formatting issues",
                details={
                    "sequence_count": len(sequence_matches),
                    "total_length": total_sequence_length,
                    "ratio": total_sequence_length / content_length,
                },
            )
        )

    return issues


def check_chunk_potential(
    content: str,
    chunk_size: int = 2048,
    chunk_overlap: int = 256,
) -> List[Issue]:
    """
    Check if content will produce problematic chunks.

    Args:
        content: Text content to analyze
        chunk_size: Target chunk size from config
        chunk_overlap: Overlap between chunks

    Returns:
        List of Issue objects describing chunking problems
    """
    issues = []

    if not content:
        return issues

    content_length = len(content)

    # Count paragraph breaks
    paragraph_breaks = content.count("\n\n")
    expected_breaks = content_length / 3000  # Roughly one break per 3000 chars

    # Check for no paragraph breaks in long content
    if content_length > 5000 and paragraph_breaks < 3:
        issues.append(
            Issue(
                check="no_paragraph_breaks",
                severity="warning",
                message=f"Long text ({content_length} chars) with only {paragraph_breaks} paragraph breaks",
                suggestion="May produce oversized chunks. Consider adding structure or using different chunking strategy",
                details={
                    "content_length": content_length,
                    "paragraph_breaks": paragraph_breaks,
                    "expected_breaks": int(expected_breaks),
                },
            )
        )

    # Simulate chunking to find oversized chunks
    # Simple simulation: split on double newlines, check resulting sizes
    paragraphs = content.split("\n\n")
    oversized_paragraphs = [p for p in paragraphs if len(p) > chunk_size * 2]

    if oversized_paragraphs:
        issues.append(
            Issue(
                check="oversized_paragraphs",
                severity="warning",
                message=f"Found {len(oversized_paragraphs)} paragraphs larger than 2x chunk size",
                suggestion="These will be force-split mid-sentence. Consider adding more line breaks in source",
                details={
                    "oversized_count": len(oversized_paragraphs),
                    "largest_paragraph": max(len(p) for p in oversized_paragraphs),
                    "chunk_size": chunk_size,
                },
            )
        )

    # Check for single giant block
    if content_length > 50000 and paragraph_breaks < 3:
        issues.append(
            Issue(
                check="single_giant_block",
                severity="error",
                message=f"Massive text block ({content_length} chars) with minimal structure",
                suggestion="This will produce many poorly-bounded chunks. Add paragraph breaks or exclude from indexing",
                details={
                    "content_length": content_length,
                    "paragraph_breaks": paragraph_breaks,
                },
            )
        )

    # Estimate chunk count
    estimated_chunks = max(1, (content_length - chunk_overlap) // (chunk_size - chunk_overlap))
    if estimated_chunks > 100:
        issues.append(
            Issue(
                check="high_chunk_count",
                severity="info",
                message=f"Document will produce approximately {estimated_chunks} chunks",
                suggestion="Consider if this level of granularity is needed or if document should be summarized",
                details={
                    "estimated_chunks": estimated_chunks,
                    "content_length": content_length,
                },
            )
        )

    return issues


def check_obsidian_quality(
    content: str,
    metadata: Dict[str, Any],
    vault_files: Optional[Set[str]] = None,
) -> List[Issue]:
    """
    Check Obsidian-specific quality issues.

    Args:
        content: Markdown content
        metadata: Parsed metadata including frontmatter
        vault_files: Set of all file stems in vault (for orphan link detection)

    Returns:
        List of Issue objects describing problems found
    """
    issues = []

    if not content:
        issues.append(
            Issue(
                check="empty_file",
                severity="warning",
                message="File is empty",
                suggestion="Remove empty file or add content",
            )
        )
        return issues

    stripped_content = content.strip()

    # Check for near-empty files
    if len(stripped_content) < 50:
        issues.append(
            Issue(
                check="near_empty_file",
                severity="warning",
                message=f"File contains very little content ({len(stripped_content)} characters)",
                suggestion="Consider expanding content or removing stub file",
                details={"content_length": len(stripped_content)},
            )
        )

    # Check for malformed frontmatter
    if content.startswith("---"):
        try:
            # Find closing delimiter
            second_delimiter = content.find("---", 3)
            if second_delimiter == -1:
                issues.append(
                    Issue(
                        check="unclosed_frontmatter",
                        severity="warning",
                        message="Frontmatter block is not closed",
                        suggestion="Add closing '---' after frontmatter",
                    )
                )
            else:
                frontmatter_block = content[3:second_delimiter].strip()
                # Check for common YAML issues
                if ": " not in frontmatter_block and frontmatter_block:
                    issues.append(
                        Issue(
                            check="malformed_frontmatter",
                            severity="warning",
                            message="Frontmatter doesn't appear to contain valid YAML",
                            suggestion="Check YAML syntax (key: value format)",
                            details={"frontmatter_preview": frontmatter_block[:100]},
                        )
                    )
        except Exception as e:
            issues.append(
                Issue(
                    check="frontmatter_parse_error",
                    severity="warning",
                    message=f"Error parsing frontmatter: {str(e)}",
                    suggestion="Check YAML syntax in frontmatter block",
                )
            )

    # Check for missing title
    title = metadata.get("title", "")
    file_name = metadata.get("file_name", "")
    if not title and file_name:
        # Check if filename is cryptic (like a UUID or timestamp)
        if re.match(r"^[0-9a-f-]{20,}$", file_name.lower()) or re.match(
            r"^\d{10,}", file_name
        ):
            issues.append(
                Issue(
                    check="missing_title_cryptic_name",
                    severity="info",
                    message="No title in frontmatter and filename appears auto-generated",
                    suggestion="Add 'title:' to frontmatter for better searchability",
                    details={"file_name": file_name},
                )
            )

    # Check for orphan wikilinks
    if vault_files is not None:
        wikilinks = metadata.get("wikilinks", [])
        if isinstance(wikilinks, str):
            wikilinks = [w.strip() for w in wikilinks.split(",") if w.strip()]

        orphan_links = []
        for link in wikilinks:
            # Normalize link (remove aliases, anchors)
            link_target = link.split("|")[0].split("#")[0].strip()
            if link_target and link_target.lower() not in vault_files:
                orphan_links.append(link_target)

        if orphan_links:
            issues.append(
                Issue(
                    check="orphan_wikilinks",
                    severity="info",
                    message=f"Found {len(orphan_links)} links to non-existent notes",
                    suggestion="Create linked notes or fix broken links",
                    details={
                        "orphan_count": len(orphan_links),
                        "orphan_links": orphan_links[:10],  # First 10
                    },
                )
            )

    return issues
