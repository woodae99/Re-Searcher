#!/usr/bin/env python3
"""Profile extraction quality across the local Zotero corpus.

Answers the v0.6 routing questions with real data instead of n=2:

  * What fraction of sources are 'accept' straight from the zero-cost Zotero
    full-text cache, 'clean' (deterministic cleanup only), or 'escalate'
    (need a heavier extractor)?
  * Of the escalate/missing set, how many recover with cheap pdfminer?
  * What does that imply for rebuild throughput?

Cheap by design: the Zotero FT cache pass is ~milliseconds per source, so the
whole corpus profiles in seconds. pdfminer fallback runs only on the subset
that needs it.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from extraction_quality import load_dictionary, profile_text  # noqa: E402

DEFAULT_SOURCE_ROOT = Path("/home/colin/Dev/Sources/Zotero/storage")


def find_pdfs(root: Path, limit: Optional[int]) -> List[Path]:
    pdfs = sorted(p for p in root.rglob("*.pdf") if p.is_file())
    return pdfs[:limit] if limit else pdfs


def read_ft_cache(pdf: Path) -> Optional[str]:
    cache = pdf.parent / ".zotero-ft-cache"
    if not cache.exists():
        return None
    return cache.read_text(encoding="utf-8", errors="replace")


def main() -> int:
    args = parse_args()
    dic = load_dictionary()
    pdfs = find_pdfs(args.source_root, args.limit)
    if not pdfs:
        print(f"No PDFs under {args.source_root}", file=sys.stderr)
        return 2
    print(f"Profiling {len(pdfs)} PDFs via Zotero FT cache (dict={len(dic):,} words)...")

    records = []
    t0 = time.perf_counter()
    for i, pdf in enumerate(pdfs, 1):
        text = read_ft_cache(pdf)
        if text is None:
            records.append({"pdf": str(pdf), "ft_status": "missing", "action": "escalate",
                            "grade": "no-cache", "overall_score": 0.0,
                            "fundamental_penalty": 1.0, "recoverable_penalty": 0.0})
            continue
        pr = profile_text(text, dictionary=dic)
        records.append({
            "pdf": str(pdf), "ft_status": "present",
            "action": pr.action, "grade": pr.grade,
            "overall_score": pr.overall_score,
            "fundamental_penalty": pr.fundamental_penalty,
            "recoverable_penalty": pr.recoverable_penalty,
            "real_word_ratio": round(pr.signals["real_word_ratio"], 3),
            "double_space_ratio": round(pr.signals["double_space_ratio"], 3),
            "chars": int(pr.signals["chars"]),
            "notes": pr.notes[:3],
        })
        if i % 100 == 0:
            print(f"  {i}/{len(pdfs)}...", flush=True)
    ft_elapsed = time.perf_counter() - t0

    # Optional pdfminer fallback on the FT-failing subset.
    fallback = []
    if args.run_pdfminer_on_escalate:
        from extract_text import extract_pdf_text  # noqa: E402
        need = [r for r in records if r["action"] == "escalate"]
        print(f"\nRunning pdfminer fallback on {len(need)} escalate/missing sources...")
        for i, r in enumerate(need, 1):
            pdf = Path(r["pdf"])
            t = time.perf_counter()
            try:
                text = extract_pdf_text(pdf, timeout_seconds=args.pdfminer_timeout)
            except Exception as exc:  # noqa: BLE001
                fallback.append({"pdf": r["pdf"], "pdfminer_error": str(exc)[:200], "action": "escalate"})
                continue
            elapsed = time.perf_counter() - t
            pr = profile_text(text, dictionary=dic)
            fallback.append({"pdf": r["pdf"], "pdfminer_seconds": round(elapsed, 2),
                             "action": pr.action, "grade": pr.grade,
                             "overall_score": pr.overall_score,
                             "fundamental_penalty": pr.fundamental_penalty})
            if i % 25 == 0:
                print(f"  pdfminer {i}/{len(need)}...", flush=True)

    summary = summarize(records, fallback, ft_elapsed)
    print("\n" + json.dumps(summary, indent=2))

    if args.output_json:
        out = {"summary": summary, "records": records, "pdfminer_fallback": fallback}
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(json.dumps(out, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.output_json}")
    return 0


def summarize(records: list, fallback: list, ft_elapsed: float) -> dict:
    n = len(records)
    actions = Counter(r["action"] for r in records)
    grades = Counter(r["grade"] for r in records)
    ft_status = Counter(r["ft_status"] for r in records)
    scored = [r["overall_score"] for r in records if r["ft_status"] == "present"]

    fb_actions = Counter(r["action"] for r in fallback) if fallback else Counter()
    recovered = sum(1 for r in fallback if r.get("action") in {"accept", "clean"})

    return {
        "total_sources": n,
        "ft_cache_status": dict(ft_status),
        "ft_actions": dict(actions),
        "ft_action_pct": {k: round(100 * v / n, 1) for k, v in actions.items()},
        "ft_grades": dict(grades),
        "ft_score_quartiles": _quartiles(scored),
        "ft_pass_elapsed_seconds": round(ft_elapsed, 2),
        "pdfminer_fallback": {
            "attempted": len(fallback),
            "actions": dict(fb_actions),
            "recovered_to_accept_or_clean": recovered,
            "still_escalate": len(fallback) - recovered,
        } if fallback else None,
    }


def _quartiles(values: list) -> dict:
    if not values:
        return {}
    s = sorted(values)
    q = statistics.quantiles(s, n=4) if len(s) >= 4 else [s[0], statistics.median(s), s[-1]]
    return {"min": round(s[0], 3), "p25": round(q[0], 3), "median": round(q[1], 3),
            "p75": round(q[2], 3), "max": round(s[-1], 3), "mean": round(statistics.mean(s), 3)}


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source-root", type=Path, default=DEFAULT_SOURCE_ROOT)
    p.add_argument("--limit", type=int, default=None, help="Cap number of PDFs (debug).")
    p.add_argument("--run-pdfminer-on-escalate", action="store_true",
                   help="Run pdfminer on the FT-failing subset and measure recovery.")
    p.add_argument("--pdfminer-timeout", type=int, default=120)
    p.add_argument("--output-json", type=Path, default=REPO_ROOT / "output" / "corpus-quality-profile.json")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
