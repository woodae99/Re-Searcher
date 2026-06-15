#!/usr/bin/env python3
"""Measure the escalate path on image-only fixtures, scored against ground truth.

For each fixture in a make_hard_fixtures manifest:
  * cheap path: pdfminer (no OCR) -> should yield ~nothing -> gate 'escalate'
  * escalate path: Marker with OCR (surya) -> should recover readable text
Both are profiled with the v0.6 quality gate and scored against the ORIGINAL
text (token recall + length ratio), so we can answer two questions with data:
  1. does the gate correctly escalate when the text layer is gone?
  2. does the OCR escalation actually pay off (recover accept-grade text)?
"""

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

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
from extract_text import extract_pdf_text  # noqa: E402
from extraction_quality import load_dictionary, profile_text  # noqa: E402

_TOK = re.compile(r"[^\W\d_]+", re.UNICODE)


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOK.findall(text or "")]


def similarity(ground_truth: str, candidate: str) -> dict:
    gt, cand = tokens(ground_truth), tokens(candidate)
    gt_set, cand_set = set(gt), set(cand)
    recall = len(gt_set & cand_set) / len(gt_set) if gt_set else 0.0
    return {
        "token_recall": round(recall, 3),
        "len_ratio": round((len(candidate) / len(ground_truth)) if ground_truth else 0.0, 3),
        "gt_chars": len(ground_truth),
        "cand_chars": len(candidate),
    }


def run_marker_ocr(pdf: Path, marker_bin: str, timeout: int) -> tuple[str, float, str | None]:
    with tempfile.TemporaryDirectory(prefix="ocr-marker-") as tmp:
        out_dir = Path(tmp)
        cmd = [marker_bin, str(pdf), "--output_dir", str(out_dir),
               "--output_format", "markdown", "--disable_tqdm", "--disable_image_extraction"]
        # NOTE: OCR is ON (we do not pass --disable_ocr).
        t = time.perf_counter()
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        elapsed = time.perf_counter() - t
        if proc.returncode != 0:
            return "", elapsed, (proc.stderr or proc.stdout)[-500:]
        mds = sorted(out_dir.rglob("*.md"))
        if not mds:
            return "", elapsed, "no markdown produced"
        return mds[0].read_text(encoding="utf-8", errors="replace"), elapsed, None


def main() -> int:
    args = parse_args()
    manifest = json.loads(args.manifest.read_text())
    fixtures = manifest["fixtures"]
    if args.variant:
        fixtures = [f for f in fixtures if f["variant"] in args.variant]
    if args.limit:
        fixtures = fixtures[: args.limit]
    dic = load_dictionary()
    marker_bin = args.marker_bin or shutil.which("marker_single") or str(REPO_ROOT / ".venv" / "bin" / "marker_single")

    rows = []
    for f in fixtures:
        fx, gt_text = Path(f["fixture"]), Path(f["ground_truth"]).read_text(encoding="utf-8", errors="replace")
        name = f"{fx.stem}"
        print(f"\n== {name} ({f['variant']}, dpi={f['dpi']}) ==", flush=True)

        # Cheap path (no OCR).
        t = time.perf_counter()
        cheap_text = extract_pdf_text(fx, timeout_seconds=60)
        cheap_secs = time.perf_counter() - t
        cheap_p = profile_text(cheap_text, dictionary=dic)
        print(f"  pdfminer(no-ocr): {len(cheap_text)} chars, action={cheap_p.action} ({cheap_secs:.1f}s)")

        # Escalate path (Marker + OCR).
        ocr_text, ocr_secs, err = run_marker_ocr(fx, marker_bin, args.timeout)
        if err:
            print(f"  marker(ocr): ERROR {err}")
            ocr_p = None; sim = {}
        else:
            ocr_p = profile_text(ocr_text, dictionary=dic)
            sim = similarity(gt_text, ocr_text)
            print(f"  marker(ocr): {len(ocr_text)} chars, score={ocr_p.overall_score} action={ocr_p.action} "
                  f"recall={sim['token_recall']} ({ocr_secs:.1f}s)")

        rows.append({
            "fixture": f["fixture"], "variant": f["variant"], "dpi": f["dpi"],
            "cheap": {"chars": len(cheap_text), "action": cheap_p.action, "score": cheap_p.overall_score,
                      "seconds": round(cheap_secs, 1), "similarity": similarity(gt_text, cheap_text)},
            "ocr": None if err else {"chars": len(ocr_text), "action": ocr_p.action, "score": ocr_p.overall_score,
                                     "fundamental": ocr_p.fundamental_penalty, "seconds": round(ocr_secs, 1),
                                     "similarity": sim, "notes": ocr_p.notes[:3]},
            "ocr_error": err,
        })

    args.output_json.parent.mkdir(parents=True, exist_ok=True)
    args.output_json.write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {args.output_json}")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--manifest", type=Path, default=REPO_ROOT / "output" / "hard-fixtures" / "manifest.json")
    p.add_argument("--variant", nargs="*", help="Only these variants.")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--marker-bin")
    p.add_argument("--timeout", type=int, default=600)
    p.add_argument("--output-json", type=Path, default=REPO_ROOT / "output" / "ocr-recovery.json")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
