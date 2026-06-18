#!/usr/bin/env python3
"""Compare PDF text extraction candidates on local Zotero PDFs."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import List


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from extract_text import extract_pdf_text  # noqa: E402


_LIGATURES = set("ﬀﬁﬂﬃﬄ")
_REVERSED_WORD_RE = re.compile(r"[A-Za-z]{6,}")
# Supported v0.6 extractors (run by default / "all"). Docling and pymupdf4llm
# were evaluated and dropped from the stack (see docs/EXTRACTION_QUALITY_GATE.md);
# they remain selectable for ad-hoc re-comparison if reinstalled.
DEFAULT_EXTRACTORS = ["zotero-ft-cache", "pdfminer", "marker"]
KNOWN_EXTRACTORS = [*DEFAULT_EXTRACTORS, "docling", "pymupdf4llm"]


def main() -> int:
    args = parse_args()
    pdfs = list(args.pdfs) if args.pdfs else discover_sample_pdfs(args.source_root, args.limit, args.max_mb)
    if not pdfs:
        print("No PDFs found for bake-off.", file=sys.stderr)
        return 2

    extractors = DEFAULT_EXTRACTORS if args.extractors == ["all"] else args.extractors
    binaries = {
        "docling": find_executable(args.docling_bin, "docling"),
        "marker": find_executable(args.marker_bin, "marker_single"),
    }

    results = []
    for pdf in pdfs:
        pdf = pdf.resolve()
        print(f"\n== {pdf.name} ==")
        results.append(run_one(pdf, extractors, binaries, args))

    print("\n== Summary ==")
    print(json.dumps(results, indent=2))
    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.output_json}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdfs", nargs="*", type=Path, help="PDF paths. If omitted, sample from --source-root.")
    parser.add_argument(
        "--source-root",
        type=Path,
        default=REPO_ROOT / "test_sources" / "Zotero" / "storage",
        help="Root to auto-discover PDFs from when no PDF paths are given.",
    )
    parser.add_argument("--limit", type=int, default=3, help="Number of discovered PDFs to test.")
    parser.add_argument("--max-mb", type=float, default=2.0, help="Maximum size for auto-discovered PDFs.")
    parser.add_argument(
        "--extractors",
        nargs="+",
        default=["all"],
        choices=[*KNOWN_EXTRACTORS, "all"],
        help="Extractors to run.",
    )
    parser.add_argument("--timeout", type=int, default=180, help="Timeout per extractor in seconds.")
    parser.add_argument("--output-json", type=Path, help="Write JSON summary to this path.")
    parser.add_argument("--keep-output", type=Path, help="Keep extractor text outputs in this directory.")
    parser.add_argument("--docling-bin", help="Path to the docling CLI.")
    parser.add_argument("--docling-device", default="cuda", choices=["auto", "cpu", "cuda"], help="Docling device.")
    parser.add_argument("--docling-ocr", action="store_true", help="Enable Docling OCR.")
    parser.add_argument("--docling-tables", action="store_true", help="Enable Docling table extraction.")
    parser.add_argument("--docling-num-threads", type=int, default=2, help="Docling worker threads.")
    parser.add_argument(
        "--docling-image-export-mode",
        default="placeholder",
        choices=["placeholder", "embedded", "referenced"],
        help="Docling image export mode. Placeholder avoids base64 payloads.",
    )
    parser.add_argument("--marker-bin", help="Path to marker_single.")
    parser.add_argument("--marker-ocr", action="store_true", help="Allow Marker OCR. Default disables OCR.")
    parser.add_argument("--pymupdf4llm-ocr", action="store_true", help="Allow PyMuPDF4LLM OCR. Default disables OCR.")
    return parser.parse_args()


def discover_sample_pdfs(root: Path, limit: int, max_mb: float) -> List[Path]:
    max_bytes = int(max_mb * 1024 * 1024)
    candidates = sorted(
        (path for path in root.rglob("*.pdf") if path.is_file() and path.stat().st_size <= max_bytes),
        key=lambda path: (path.stat().st_size, str(path)),
    )
    return candidates[:limit]


def find_executable(configured: str | None, name: str) -> str | None:
    if configured:
        return configured
    found = shutil.which(name)
    if found:
        return found
    candidate = REPO_ROOT / ".venv" / "bin" / name
    return str(candidate) if candidate.exists() else None


def run_one(pdf: Path, extractors: list[str], binaries: dict[str, str | None], args: argparse.Namespace) -> dict:
    result = {"pdf": str(pdf), "size_mb": round(pdf.stat().st_size / (1024 * 1024), 3)}
    for extractor in extractors:
        print(f"starting {extractor}...", flush=True)
        if extractor == "zotero-ft-cache":
            summary = timed_zotero_ft_cache(pdf, args.keep_output)
        elif extractor == "pdfminer":
            summary = timed_pdfminer(pdf, args.timeout, args.keep_output)
        elif extractor == "pymupdf4llm":
            summary = timed_pymupdf4llm(pdf, args)
        elif extractor == "marker":
            summary = timed_marker(pdf, binaries["marker"], args)
        elif extractor == "docling":
            summary = timed_docling(pdf, binaries["docling"], args)
        else:
            raise ValueError(f"unknown extractor: {extractor}")
        result[extractor] = summary
        print(
            f"{extractor}: {summary['elapsed_seconds']:.2f}s, "
            f"{summary['searchable_chars']} searchable chars, artifacts={summary['artifact_flags']}",
            flush=True,
        )
    return result


def timed_zotero_ft_cache(pdf: Path, output_root: Path | None) -> dict:
    start = time.perf_counter()
    cache = pdf.parent / ".zotero-ft-cache"
    if not cache.exists():
        return summarize_text("", time.perf_counter() - start, ok=False, error="missing .zotero-ft-cache")
    text = cache.read_text(encoding="utf-8", errors="replace")
    elapsed = time.perf_counter() - start
    write_output(output_root, "zotero-ft-cache", pdf, ".txt", text)
    return summarize_text(text, elapsed, ok=bool(text.strip()), error=None if text.strip() else "empty")


def timed_pdfminer(pdf: Path, timeout: int, output_root: Path | None) -> dict:
    start = time.perf_counter()
    text = extract_pdf_text(pdf, timeout_seconds=timeout)
    elapsed = time.perf_counter() - start
    write_output(output_root, "pdfminer", pdf, ".txt", text)
    return summarize_text(text, elapsed, ok=bool(text.strip()), error=None if text.strip() else "empty")


def timed_pymupdf4llm(pdf: Path, args: argparse.Namespace) -> dict:
    with tempfile.TemporaryDirectory(prefix="researcher-pymupdf4llm-") as tmp:
        output_path = Path(tmp) / f"{pdf.stem}.md"
        command = [
            sys.executable,
            "-c",
            (
                "import sys; from pathlib import Path; import pymupdf4llm; "
                "pdf, out, use_ocr = sys.argv[1], sys.argv[2], sys.argv[3] == '1'; "
                "Path(out).write_text(pymupdf4llm.to_markdown(pdf, use_ocr=use_ocr), encoding='utf-8')"
            ),
            str(pdf),
            str(output_path),
            "1" if args.pymupdf4llm_ocr else "0",
        ]
        return timed_subprocess_text(
            command,
            output_path,
            timeout=args.timeout,
            output_root=args.keep_output,
            extractor="pymupdf4llm",
            pdf=pdf,
            suffix=".md",
        )


def timed_marker(pdf: Path, marker_bin: str | None, args: argparse.Namespace) -> dict:
    if not marker_bin:
        return summarize_text("", 0.0, ok=False, error="marker_single executable not found")
    with tempfile.TemporaryDirectory(prefix="researcher-marker-") as tmp:
        output_dir = Path(tmp)
        command = [
            marker_bin,
            str(pdf),
            "--output_dir",
            str(output_dir),
            "--output_format",
            "markdown",
            "--disable_tqdm",
            "--disable_image_extraction",
        ]
        if not args.marker_ocr:
            command.append("--disable_ocr")
        start = time.perf_counter()
        proc = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout + 60)
        elapsed = time.perf_counter() - start
        if proc.returncode != 0:
            return summarize_text("", elapsed, ok=False, error=(proc.stderr or proc.stdout)[-2000:])
        md_path = find_first_markdown(output_dir, pdf)
        if not md_path:
            return summarize_text("", elapsed, ok=False, error="marker output markdown not found")
        text = md_path.read_text(encoding="utf-8", errors="replace")
        write_output(args.keep_output, "marker", pdf, ".md", text)
        return summarize_text(text, elapsed, ok=bool(text.strip()), error=None if text.strip() else "empty")


def timed_docling(pdf: Path, docling_bin: str | None, args: argparse.Namespace) -> dict:
    if not docling_bin:
        return summarize_text("", 0.0, ok=False, error="docling executable not found")
    with tempfile.TemporaryDirectory(prefix="researcher-docling-") as tmp:
        output_dir = Path(tmp)
        command = [
            docling_bin,
            str(pdf),
            "--to",
            "md",
            "--output",
            str(output_dir),
            "--device",
            args.docling_device,
            "--num-threads",
            str(args.docling_num_threads),
            "--document-timeout",
            str(args.timeout),
            "--image-export-mode",
            args.docling_image_export_mode,
        ]
        command.append("--ocr" if args.docling_ocr else "--no-ocr")
        command.append("--tables" if args.docling_tables else "--no-tables")
        return timed_subprocess_markdown_dir(command, output_dir, pdf, args.timeout + 60, args.keep_output, "docling")


def timed_subprocess_markdown_dir(
    command: list[str], output_dir: Path, pdf: Path, timeout: int, output_root: Path | None, extractor: str
) -> dict:
    start = time.perf_counter()
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        return summarize_text("", elapsed, ok=False, error=(proc.stderr or proc.stdout)[-2000:])
    md_path = find_first_markdown(output_dir, pdf)
    if not md_path:
        return summarize_text("", elapsed, ok=False, error=f"{extractor} output markdown not found")
    text = md_path.read_text(encoding="utf-8", errors="replace")
    write_output(output_root, extractor, pdf, ".md", text)
    return summarize_text(text, elapsed, ok=bool(text.strip()), error=None if text.strip() else "empty")


def timed_subprocess_text(
    command: list[str],
    output_path: Path,
    *,
    timeout: int,
    output_root: Path | None,
    extractor: str,
    pdf: Path,
    suffix: str,
) -> dict:
    start = time.perf_counter()
    proc = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    elapsed = time.perf_counter() - start
    if proc.returncode != 0:
        return summarize_text("", elapsed, ok=False, error=(proc.stderr or proc.stdout)[-2000:])
    if not output_path.exists():
        return summarize_text("", elapsed, ok=False, error=f"{extractor} output not found")
    text = output_path.read_text(encoding="utf-8", errors="replace")
    write_output(output_root, extractor, pdf, suffix, text)
    return summarize_text(text, elapsed, ok=bool(text.strip()), error=None if text.strip() else "empty")


def find_first_markdown(output_dir: Path, pdf: Path) -> Path | None:
    exact = output_dir / f"{pdf.stem}.md"
    if exact.exists():
        return exact
    matches = sorted(output_dir.rglob("*.md"))
    return matches[0] if matches else None


def write_output(output_root: Path | None, extractor: str, pdf: Path, suffix: str, text: str) -> None:
    if not output_root:
        return
    output_dir = output_root / extractor
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / f"{pdf.stem}{suffix}").write_text(text, encoding="utf-8")


def summarize_text(text: str, elapsed: float, *, ok: bool, error: str | None) -> dict:
    data_image_lines = sum(1 for line in text.splitlines() if line.startswith("![Image](data:image/"))
    searchable_text = strip_data_image_lines(text)
    lines = [line.strip() for line in searchable_text.splitlines() if line.strip()]
    return {
        "ok": ok,
        "elapsed_seconds": round(elapsed, 3),
        "chars": len(text),
        "searchable_chars": len(searchable_text),
        "nonempty_lines": len(lines),
        "data_image_lines": data_image_lines,
        "artifact_flags": scan_text_artifacts(searchable_text),
        "error": error,
        "preview": " ".join(lines[:3])[:300],
    }


def strip_data_image_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("![Image](data:image/"))


def scan_text_artifacts(text: str) -> dict:
    """Per-document presence flags (0/1), not match counts.

    Each value is 1 if the artifact appears anywhere in the document text and 0
    otherwise. These are a coarse triage signal for the bake-off; the per-chunk
    artifact *counts* used by the acceptance gate live in src/acceptance_harness.py.
    """
    words = _REVERSED_WORD_RE.findall(text or "")
    vowel_light = sum(1 for word in words if sum(ch.lower() in "aeiou" for ch in word) <= 1)
    return {
        "line_hyphenation": int(bool(re.search(r"\w-\s*\n\s*\w", text or ""))),
        "ligature_space": int(bool(re.search(r"[ﬀﬁﬂﬃﬄ]\s+\w", text or ""))),
        "raw_ligature": int(any(ch in (text or "") for ch in _LIGATURES)),
        "letter_spaced_words": int(bool(re.search(r"(?:\b[A-Za-z]\s+){6,}[A-Za-z]\b", text or ""))),
        "reversed_text_suspect": int(bool(words) and len(words) >= 12 and (vowel_light / len(words)) >= 0.55),
    }


if __name__ == "__main__":
    raise SystemExit(main())
