"""Retrieval evaluation core for the v0.6 chunking decisions.

The extraction decisions were made on measured data; chunking (grain, size,
overlap) should be too. This module is the measuring stick: given a search
function and a set of probes (query -> the source(s) that should come back), it
computes known-item retrieval metrics (hit@k, MRR, first-rank). It is collection-
and embedder-agnostic so it unit-tests against a fake search_fn and runs against
a real ephemeral collection in `scripts/eval_chunking.py`.

A "probe" is deliberately known-item: each query targets specific source(s), so
the metric is whether a chunk from an expected source appears, and how high. This
isolates the chunking variable — it is upstream of reranking, which is a separate
stage and a separate eval.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Callable, Dict, List, Optional, Sequence

# search_fn(query, k) -> ordered list of result dicts, best first; each result
# must carry "source_id". Anything else (chunk_id, score, distance) is ignored.
SearchFn = Callable[[str, int], List[Dict]]


@dataclass(frozen=True)
class EvalProbe:
    query: str
    expected_source_ids: frozenset
    probe_id: str = ""
    note: str = ""


def _first_relevant_rank(results: Sequence[Dict], expected: frozenset) -> Optional[int]:
    """1-based rank of the first result from an expected source, else None."""
    for idx, result in enumerate(results, start=1):
        if str(result.get("source_id")) in expected:
            return idx
    return None


def evaluate(
    search_fn: SearchFn,
    probes: Sequence[EvalProbe],
    *,
    k_values: Sequence[int] = (1, 3, 5, 10),
) -> Dict:
    """Run all probes and return per-probe ranks plus aggregate metrics.

    Metrics (known-item retrieval):
      * hit@k  — fraction of probes with an expected source in the top k
      * mrr    — mean reciprocal rank of the first expected source (0 if missed
                 within max(k_values))
      * found_rate / mean_first_rank — over probes where any expected source was
                 retrieved at all (within max(k_values))
    """
    if not probes:
        return {"probes": 0, "hit_at": {}, "mrr": 0.0, "found_rate": 0.0,
                "mean_first_rank": None, "per_probe": []}

    max_k = max(k_values)
    per_probe: List[Dict] = []
    ranks: List[Optional[int]] = []

    for probe in probes:
        results = search_fn(probe.query, max_k) or []
        rank = _first_relevant_rank(results, probe.expected_source_ids)
        ranks.append(rank)
        per_probe.append({
            "probe_id": probe.probe_id,
            "query": probe.query,
            "expected_source_ids": sorted(probe.expected_source_ids),
            "first_relevant_rank": rank,
            "top_source_ids": [str(r.get("source_id")) for r in results[:max_k]],
        })

    n = len(probes)
    hit_at = {
        f"hit@{k}": round(sum(1 for r in ranks if r is not None and r <= k) / n, 3)
        for k in k_values
    }
    mrr = round(mean((1.0 / r) if r else 0.0 for r in ranks), 4)
    found = [r for r in ranks if r is not None]
    return {
        "probes": n,
        "hit_at": hit_at,
        "mrr": mrr,
        "found_rate": round(len(found) / n, 3),
        "mean_first_rank": round(mean(found), 2) if found else None,
        "per_probe": per_probe,
    }


def compare_configs(results_by_config: Dict[str, Dict], *, headline_k: int = 5) -> List[Dict]:
    """Flatten per-config eval results into a ranking table on hit@headline_k."""
    rows = []
    for name, report in results_by_config.items():
        rows.append({
            "config": name,
            f"hit@{headline_k}": report["hit_at"].get(f"hit@{headline_k}"),
            "mrr": report["mrr"],
            "found_rate": report["found_rate"],
            "mean_first_rank": report["mean_first_rank"],
            **{k: v for k, v in report.get("meta", {}).items()},
        })
    rows.sort(key=lambda r: (r.get(f"hit@{headline_k}") or 0, r.get("mrr") or 0), reverse=True)
    return rows
