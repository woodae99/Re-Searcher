#!/usr/bin/env python3
"""Derive hard extraction fixtures from existing clean PDFs.

Colin's library is mostly clean born-digital PDFs, so genuine OCR-needed cases
are rare. This builds them deterministically from clean sources, keeping the
original text as ground truth so OCR output can be *scored*, not just eyeballed.

Variants:
  image_only         rasterize each page, rebuild a PDF with NO text layer
  image_only_lowdpi  same, lower DPI (harder OCR)
  rotated_noisy      low DPI + small rotation + gaussian noise (worst case)

Outputs (default under output/hard-fixtures/, which is gitignored):
  <stem>.<variant>.pdf      the fixture (image-only, no extractable text layer)
  <stem>.ground_truth.txt   pdfminer text of the ORIGINAL (reference)
  manifest.json             what was built, from what, with which settings
"""

from __future__ import annotations

import argparse
import io
import json
import sys
import time
from pathlib import Path
from typing import List

import fitz  # PyMuPDF

REPO_ROOT = Path(__file__).resolve().parents[1]

VARIANTS = ("image_only", "image_only_lowdpi", "rotated_noisy")


def render_image_pdf(src: Path, dest: Path, *, dpi: int, max_pages: int,
                     rotate_deg: float = 0.0, noise_sigma: float = 0.0) -> int:
    """Rasterize src into an image-only PDF (no text layer). Returns page count."""
    doc = fitz.open(str(src))
    out = fitz.open()
    n = min(len(doc), max_pages) if max_pages else len(doc)
    for i in range(n):
        pix = doc[i].get_pixmap(dpi=dpi)
        png = pix.tobytes("png")
        if rotate_deg or noise_sigma:
            png = _degrade(png, rotate_deg, noise_sigma)
            img = fitz.open("png", png)
            rect = fitz.Rect(0, 0, img[0].rect.width, img[0].rect.height)
            page = out.new_page(width=rect.width, height=rect.height)
        else:
            rect = fitz.Rect(0, 0, pix.width, pix.height)
            page = out.new_page(width=rect.width, height=rect.height)
        page.insert_image(page.rect, stream=png)
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.save(str(dest), deflate=True, garbage=4)
    out.close(); doc.close()
    return n


def _degrade(png_bytes: bytes, rotate_deg: float, noise_sigma: float) -> bytes:
    from PIL import Image
    import numpy as np
    img = Image.open(io.BytesIO(png_bytes)).convert("L")
    if rotate_deg:
        img = img.rotate(rotate_deg, expand=True, fillcolor=255, resample=Image.BICUBIC)
    arr = np.asarray(img).astype("float32")
    if noise_sigma:
        arr = arr + np.random.normal(0, noise_sigma, arr.shape)
    arr = arr.clip(0, 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def ground_truth_text(src: Path, max_pages: int) -> str:
    """True reference text: the ORIGINAL born-digital text layer, page-matched
    to the fixture so OCR recall/length are scored over the same pages."""
    doc = fitz.open(str(src))
    n = min(len(doc), max_pages) if max_pages else len(doc)
    text = "\n".join(doc[i].get_text("text") for i in range(n))
    doc.close()
    return text


def has_text_layer(pdf: Path, sample_pages: int = 3) -> int:
    """Sanity check: characters extractable from the fixture's text layer."""
    doc = fitz.open(str(pdf))
    chars = sum(len(doc[i].get_text("text")) for i in range(min(sample_pages, len(doc))))
    doc.close()
    return chars


def main() -> int:
    args = parse_args()
    if not args.sources:
        print("No source PDFs given.", file=sys.stderr)
        return 2
    manifest = {"created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "fixtures": []}
    for src in args.sources:
        src = src.resolve()
        if not src.exists():
            print(f"  skip (missing): {src}", file=sys.stderr); continue
        stem = src.stem[:60]
        print(f"\n== {src.name} ==")

        gt_path = args.out / f"{stem}.ground_truth.txt"
        gt = ground_truth_text(src, args.max_pages)
        gt_path.parent.mkdir(parents=True, exist_ok=True)
        gt_path.write_text(gt, encoding="utf-8")
        print(f"  ground truth: {len(gt):,} chars (first {args.max_pages} pages) -> {gt_path.name}")

        for variant in args.variants:
            dpi = args.dpi if variant == "image_only" else args.low_dpi
            rot = args.rotate if variant == "rotated_noisy" else 0.0
            noise = args.noise if variant == "rotated_noisy" else 0.0
            dest = args.out / f"{stem}.{variant}.pdf"
            t = time.perf_counter()
            pages = render_image_pdf(src, dest, dpi=dpi, max_pages=args.max_pages,
                                     rotate_deg=rot, noise_sigma=noise)
            leftover = has_text_layer(dest)
            size_mb = round(dest.stat().st_size / 1e6, 2)
            print(f"  {variant:18} dpi={dpi:<4} pages={pages} {size_mb}MB "
                  f"text_layer_chars={leftover} ({time.perf_counter()-t:.1f}s)")
            manifest["fixtures"].append({
                "source": str(src), "variant": variant, "fixture": str(dest),
                "ground_truth": str(gt_path), "dpi": dpi, "pages": pages,
                "rotate_deg": rot, "noise_sigma": noise,
                "residual_text_layer_chars": leftover, "size_mb": size_mb,
            })
    (args.out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"\nWrote {args.out / 'manifest.json'} ({len(manifest['fixtures'])} fixtures)")
    return 0


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("sources", nargs="*", type=Path, help="Clean source PDFs to derive fixtures from.")
    p.add_argument("--out", type=Path, default=REPO_ROOT / "output" / "hard-fixtures")
    p.add_argument("--variants", nargs="+", default=list(VARIANTS), choices=VARIANTS)
    p.add_argument("--dpi", type=int, default=200, help="DPI for image_only.")
    p.add_argument("--low-dpi", type=int, default=120, help="DPI for low-dpi/noisy variants.")
    p.add_argument("--rotate", type=float, default=1.5, help="Degrees for rotated_noisy.")
    p.add_argument("--noise", type=float, default=12.0, help="Gaussian sigma for rotated_noisy.")
    p.add_argument("--max-pages", type=int, default=12, help="Cap pages per fixture (keep OCR fast).")
    return p.parse_args()


if __name__ == "__main__":
    raise SystemExit(main())
