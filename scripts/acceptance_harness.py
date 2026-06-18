#!/usr/bin/env python3
"""Run v0.6 acceptance harness checks against a configured collection."""

import argparse
import json
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.acceptance_harness import QuoteProbe, run_registry_harness
from src.registry import SourceRegistry, registry_path_for
from src.storage.chroma import ChromaVectorStore


def _load_config(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"[ERROR] Configuration file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _load_quote_probes(path: Path) -> list[QuoteProbe]:
    if not path:
        return []
    if not path.exists():
        raise SystemExit(f"[ERROR] Quote probe file not found: {path}")
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    probes = raw.get("quotes", raw if isinstance(raw, list) else [])
    return [
        QuoteProbe(
            chunk_id=str(item["chunk_id"]),
            quote=str(item["quote"]),
            identity_field=item.get("identity_field"),
            identity_value=item.get("identity_value"),
        )
        for item in probes
    ]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run registry, duplication, quote, and artifact checks."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("config.yaml"),
        help="Path to config.yaml (default: config.yaml)",
    )
    parser.add_argument(
        "--quote-probes",
        type=Path,
        default=None,
        help="Optional JSON file with quote probes",
    )
    parser.add_argument(
        "--artifact-sample-limit",
        type=int,
        default=1000,
        help="Number of chunks to scan for text artifacts (default: 1000)",
    )
    parser.add_argument(
        "--artifact-tolerance",
        type=float,
        default=0.0,
        help=(
            "Max fraction of scanned chunks that may carry a given artifact "
            "before it fails the gate (default: 0.0 = strict). On a real PDF "
            "corpus try 0.05 so cosmetic line-break hyphenation does not fail "
            "an otherwise-clean rebuild."
        ),
    )
    parser.add_argument(
        "--limit-sources",
        type=int,
        default=None,
        help="Optional source limit for quick smoke runs",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Emit JSON only",
    )
    args = parser.parse_args()

    config = _load_config(args.config)
    registry = SourceRegistry(registry_path_for(config))
    store = ChromaVectorStore(config)
    report = run_registry_harness(
        registry,
        store.collection,
        artifact_sample_limit=args.artifact_sample_limit,
        artifact_tolerance=args.artifact_tolerance,
        quote_probes=_load_quote_probes(args.quote_probes),
        limit_sources=args.limit_sources,
    )

    if args.json:
        print(json.dumps(report, indent=2))
    else:
        status = "PASS" if report["pass"] else "FAIL"
        print(f"v0.6 acceptance harness: {status}")
        print(json.dumps(report["checks"], indent=2))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
