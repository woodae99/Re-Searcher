#!/usr/bin/env python3
"""Compare current PDF extraction with Docling on local test PDFs."""

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
from typing import Iterable, List


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = REPO_ROOT / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from extract_text import extract_pdf_text  # noqa: E402


_LIGATURES = set("ﬀﬁﬂﬃﬄ")
_REVERSED_WORD_RE = re.compile(r"[A-Za-z]{6,}")


def main() -> int:
    args = parse_args()
    pdfs = list(args.pdfs) if args.pdfs else discover_sample_pdfs(args.source_root, args.limit, args.max_mb)
    if not pdfs:
        print("No PDFs found for bake-off.", file=sys.stderr)
        return 2

    docling_bin = args.docling_bin or shutil.which("docling")
    if not docling_bin:
        candidate = REPO_ROOT / ".venv" / "bin" / "docling"
        if candidate.exists():
            docling_bin = str(candidate)
    if not docling_bin:
        print("Could not find docling executable.", file=sys.stderr)
        return 2

    results = []
    for pdf in pdfs:
        pdf = pdf.resolve()
        print(f"\n== {pdf.name} ==")
        results.append(run_one(pdf, docling_bin, args))

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
    parser.add_argument("--device", default="cuda", choices=["auto", "cpu", "cuda"], help="Docling device.")
    parser.add_argument("--ocr", action="store_true", help="Enable Docling OCR.")
    parser.add_argument("--tables", action="store_true", help="Enable Docling table structure extraction.")
    parser.add_argument("--num-threads", type=int, default=2, help="Docling worker threads.")
    parser.add_argument("--timeout", type=int, default=180, help="Timeout per extractor in seconds.")
    parser.add_argument("--docling-bin", help="Path to the docling CLI.")
    parser.add_argument("--output-json", type=Path, help="Write JSON summary to this path.")
    parser.add_argument("--keep-output", type=Path, help="Keep Docling markdown outputs in this directory.")
    parser.add_argument("--keep-pdfminer-output", type=Path, help="Write pdfminer text outputs to this directory.")
    parser.add_argument("--skip-pdfminer", action="store_true", help="Only run Docling.")
    parser.add_argument("--skip-docling", action="store_true", help="Only run pdfminer.")
    return parser.parse_args()


def discover_sample_pdfs(root: Path, limit: int, max_mb: float) -> List[Path]:
    max_bytes = int(max_mb * 1024 * 1024)
    candidates = sorted(
        (path for path in root.rglob("*.pdf") if path.is_file() and path.stat().st_size <= max_bytes),
        key=lambda path: (path.stat().st_size, str(path)),
    )
    return candidates[:limit]


def run_one(pdf: Path, docling_bin: str, args: argparse.Namespace) -> dict:
    pdfminer = None
    if not args.skip_pdfminer:
        print("starting pdfminer...", flush=True)
        pdfminer = timed_pdfminer(pdf, args.timeout, args.keep_pdfminer_output)
        print(
            f"pdfminer: {pdfminer['elapsed_seconds']:.2f}s, "
            f"{pdfminer['chars']} chars, artifacts={pdfminer['artifact_counts']}",
            flush=True,
        )

    docling = None
    if not args.skip_docling:
        print(
            f"starting docling device={args.device} ocr={args.ocr} tables={args.tables}...",
            flush=True,
        )
        docling = timed_docling(pdf, docling_bin, args)
        print(
            f"docling:  {docling['elapsed_seconds']:.2f}s, "
            f"{docling['chars']} chars, artifacts={docling['artifact_counts']}",
            flush=True,
        )

    return {
        "pdf": str(pdf),
        "size_mb": round(pdf.stat().st_size / (1024 * 1024), 3),
        "pdfminer": pdfminer,
        "docling": docling,
    }


def timed_pdfminer(pdf: Path, timeout: int, output_dir: Path | None) -> dict:
    start = time.perf_counter()
    text = extract_pdf_text(pdf, timeout_seconds=timeout)
    elapsed = time.perf_counter() - start
    if output_dir:
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / f"{pdf.stem}.txt").write_text(text, encoding="utf-8")
    return summarize_text(text, elapsed, ok=bool(text.strip()), error=None if text.strip() else "empty")


def timed_docling(pdf: Path, docling_bin: str, args: argparse.Namespace) -> dict:
    with tempfile.TemporaryDirectory(prefix="researcher-docling-") as tmp:
        output_dir = args.keep_output or Path(tmp)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = [
            docling_bin,
            str(pdf),
            "--to",
            "md",
            "--output",
            str(output_dir),
            "--device",
            args.device,
            "--num-threads",
            str(args.num_threads),
            "--document-timeout",
            str(args.timeout),
        ]
        command.append("--ocr" if args.ocr else "--no-ocr")
        command.append("--tables" if args.tables else "--no-tables")

        start = time.perf_counter()
        proc = subprocess.run(command, capture_output=True, text=True, timeout=args.timeout + 60)
        elapsed = time.perf_counter() - start
        if proc.returncode != 0:
            return summarize_text(
                "",
                elapsed,
                ok=False,
                error=(proc.stderr or proc.stdout or f"docling exited {proc.returncode}")[-2000:],
            )

        md_path = find_docling_markdown(output_dir, pdf)
        if not md_path:
            return summarize_text("", elapsed, ok=False, error="docling output markdown not found")
        return summarize_text(md_path.read_text(encoding="utf-8", errors="replace"), elapsed, ok=True, error=None)


def find_docling_markdown(output_dir: Path, pdf: Path) -> Path | None:
    exact = output_dir / f"{pdf.stem}.md"
    if exact.exists():
        return exact
    matches = sorted(output_dir.glob("*.md"))
    return matches[0] if matches else None


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
        "artifact_counts": scan_text_artifacts(searchable_text),
        "error": error,
        "preview": " ".join(lines[:3])[:300],
    }


def strip_data_image_lines(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if not line.startswith("![Image](data:image/"))


def scan_text_artifacts(text: str) -> dict:
    words = _REVERSED_WORD_RE.findall(text or "")
    vowel_light = sum(1 for word in words if sum(ch.lower() in "aeiou" for ch in word) <= 1)
    return {
        "line_hyphenation": int(bool(re.search(r"\w-\s*\n\s*\w", text or ""))),
        "ligature_space": int(bool(re.search(r"[ﬀﬁﬂﬃﬄ]\s+\w", text or ""))),
        "raw_ligature": int(any(ch in (text or "") for ch in _LIGATURES)),
        "reversed_text_suspect": int(bool(words) and len(words) >= 12 and (vowel_light / len(words)) >= 0.55),
    }


if __name__ == "__main__":
    raise SystemExit(main())
