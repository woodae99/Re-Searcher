"""Passage-level retrieval evaluation for the v0.6 chunk-size decision.

The source-level eval (src/retrieval_eval.py) answers "which source?" and
saturates: with topically-distinct sources the right document is easy to find,
so it cannot separate chunk sizes. This module answers the two questions chunk
size actually governs, both invisible to a source-level metric:

  1. Passage retrieval — given a query whose answer is a known *span* of a known
     source, does a chunk overlapping that span rank in the top k (competing
     against the *other* passages of the same source plus a large distractor
     pool)?  -> passage_hit@k, passage_mrr.

  2. The completeness / density trade-off under read-time neighbour expansion.
     Around the best-overlapping retrieved chunk we widen the reading window by
     m neighbours each side (modelling get_chunk_context) and measure:
       * completeness(m) = gold span covered / gold span length
                           ("did we recover the whole answer?")
       * density(m)      = gold span covered / total chars in the window
                           ("answer per char read" — the don't-overwhelm lever)

This is pure span arithmetic: deterministic, no LLM judge, and therefore free.
It is collection- and embedder-agnostic so it unit-tests against fakes and runs
against a real ephemeral collection in scripts/eval_passage.py. Raw vector
retrieval only — upstream of reranking, to isolate the chunking variable.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import mean
from typing import Callable, Dict, List, Optional, Sequence, Tuple


@dataclass(frozen=True)
class ChunkRef:
    """A chunk located by its half-open char span [start, end) within a source.

    ordinal is the chunk's 0-based position in document order within its source,
    used to fetch neighbours for read-time expansion.
    """

    source_id: str
    ordinal: int
    start: int
    end: int  # exclusive
    text: str = ""


@dataclass(frozen=True)
class PassageProbe:
    """A query whose answer is the span [gold_start, gold_end) of source_id."""

    query: str
    source_id: str
    gold_start: int
    gold_end: int
    probe_id: str = ""
    query_source: str = ""

    @property
    def gold_len(self) -> int:
        return max(0, self.gold_end - self.gold_start)


# search_fn(query, k) -> chunks, best-first. Each must carry source/ordinal/span.
ChunkSearchFn = Callable[[str, int], List[ChunkRef]]


def _merge(spans: Sequence[Tuple[int, int]]) -> List[Tuple[int, int]]:
    """Merge overlapping/adjacent [start, end) intervals."""
    ordered = sorted((s, e) for s, e in spans if e > s)
    merged: List[Tuple[int, int]] = []
    for s, e in ordered:
        if merged and s <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], e))
        else:
            merged.append((s, e))
    return merged


def union_len(spans: Sequence[Tuple[int, int]]) -> int:
    """Total length covered by a set of spans, counting overlaps once."""
    return sum(e - s for s, e in _merge(spans))


def covered_len(spans: Sequence[Tuple[int, int]], g0: int, g1: int) -> int:
    """Length of [g0, g1) covered by the union of spans."""
    if g1 <= g0:
        return 0
    clipped = [(max(s, g0), min(e, g1)) for s, e in spans]
    return union_len([(s, e) for s, e in clipped if e > s])


def overlap(a0: int, a1: int, b0: int, b1: int) -> int:
    return max(0, min(a1, b1) - max(a0, b0))


def expansion_window(
    source_chunks: Sequence[ChunkRef], center_ordinal: int, m: int
) -> List[ChunkRef]:
    """The center chunk plus m neighbours each side, in document order.

    source_chunks must be the chunks of one source. Selection is by ordinal so
    it is robust to the list not being densely indexed.
    """
    lo, hi = center_ordinal - m, center_ordinal + m
    return sorted(
        (c for c in source_chunks if lo <= c.ordinal <= hi),
        key=lambda c: c.ordinal,
    )


def _best_overlapping(
    results: Sequence[ChunkRef], probe: PassageProbe, k: int
) -> Optional[Tuple[int, ChunkRef]]:
    """Highest-overlap chunk from the expected source within top-k.

    Returns (rank, chunk) where rank is 1-based, or None if no top-k chunk from
    the expected source touches the gold span. Ties on overlap break toward the
    better (lower) rank.
    """
    best: Optional[Tuple[int, ChunkRef]] = None
    best_ov = 0
    for rank, c in enumerate(results[:k], start=1):
        if c.source_id != probe.source_id:
            continue
        ov = overlap(c.start, c.end, probe.gold_start, probe.gold_end)
        if ov > best_ov:
            best_ov, best = ov, (rank, c)
    return best


def _first_passage_rank(
    results: Sequence[ChunkRef], probe: PassageProbe, min_frac: float
) -> Optional[int]:
    """1-based rank of the first chunk overlapping the gold span by >= min_frac
    of the gold length (min_frac=0 means any overlap)."""
    need = max(1, int(probe.gold_len * min_frac)) if min_frac > 0 else 1
    for rank, c in enumerate(results, start=1):
        if c.source_id != probe.source_id:
            continue
        if overlap(c.start, c.end, probe.gold_start, probe.gold_end) >= need:
            return rank
    return None


def evaluate_passage(
    search_fn: ChunkSearchFn,
    probes: Sequence[PassageProbe],
    chunk_index: Dict[str, List[ChunkRef]],
    *,
    k_values: Sequence[int] = (1, 3, 5, 10),
    expansions: Sequence[int] = (0, 1, 2),
    strict_frac: float = 0.5,
) -> Dict:
    """Run probes and return passage retrieval + completeness/density metrics.

    chunk_index maps source_id -> that source's chunks (for neighbour lookup).
    Completeness/density are conditioned on the passage being found (an expected
    chunk overlapping the gold span within max(k_values)); passage_found_rate
    reports how often that holds, so grain quality is not confounded with misses.
    """
    max_k = max(k_values)
    per_probe: List[Dict] = []
    touch_ranks: List[Optional[int]] = []
    strict_ranks: List[Optional[int]] = []
    # completeness/density samples per expansion level, over found probes only
    comp: Dict[int, List[float]] = {m: [] for m in expansions}
    dens: Dict[int, List[float]] = {m: [] for m in expansions}
    read: Dict[int, List[int]] = {m: [] for m in expansions}

    for probe in probes:
        results = search_fn(probe.query, max_k) or []
        touch = _first_passage_rank(results, probe, 0.0)
        strict = _first_passage_rank(results, probe, strict_frac)
        touch_ranks.append(touch)
        strict_ranks.append(strict)

        row = {
            "probe_id": probe.probe_id,
            "query": probe.query,
            "source_id": probe.source_id,
            "passage_rank": touch,
            "strict_rank": strict,
        }

        best = _best_overlapping(results, probe, max_k)
        if best is not None:
            _, center = best
            row["center_ordinal"] = center.ordinal
            source_chunks = chunk_index.get(probe.source_id, [])
            for m in expansions:
                window = expansion_window(source_chunks, center.ordinal, m)
                spans = [(c.start, c.end) for c in window]
                cov = covered_len(spans, probe.gold_start, probe.gold_end)
                read_chars = union_len(spans)
                completeness = cov / probe.gold_len if probe.gold_len else 0.0
                density = cov / read_chars if read_chars else 0.0
                comp[m].append(completeness)
                dens[m].append(density)
                read[m].append(read_chars)
                row[f"completeness@{m}"] = round(completeness, 3)
                row[f"density@{m}"] = round(density, 3)
                row[f"read_chars@{m}"] = read_chars
        per_probe.append(row)

    n = len(probes)

    def hit_at(ranks: Sequence[Optional[int]]) -> Dict[str, float]:
        return {
            f"hit@{k}": round(sum(1 for r in ranks if r is not None and r <= k) / n, 3)
            for k in k_values
        } if n else {}

    def mrr(ranks: Sequence[Optional[int]]) -> float:
        return round(mean((1.0 / r) if r else 0.0 for r in ranks), 4) if n else 0.0

    found = len(comp[list(expansions)[0]]) if expansions else 0
    return {
        "probes": n,
        "passage_hit_at": hit_at(touch_ranks),
        "passage_mrr": mrr(touch_ranks),
        "strict_hit_at": hit_at(strict_ranks),
        "strict_mrr": mrr(strict_ranks),
        "passage_found_rate": round(found / n, 3) if n else 0.0,
        "completeness": {m: round(mean(v), 3) if v else 0.0 for m, v in comp.items()},
        "density": {m: round(mean(v), 3) if v else 0.0 for m, v in dens.items()},
        "read_chars": {m: round(mean(v), 1) if v else 0.0 for m, v in read.items()},
        "per_probe": per_probe,
    }


def compare_passage_configs(
    results_by_config: Dict[str, Dict], *, headline_k: int = 5, headline_m: int = 1
) -> List[Dict]:
    """Flatten per-config passage results into a ranking table.

    Ranked by passage_hit@headline_k then density at headline_m (the
    don't-overwhelm tie-breaker), so the table surfaces the grain that both
    retrieves the right passage and reads cleanest.
    """
    rows = []
    for name, report in results_by_config.items():
        rows.append({
            "config": name,
            f"passage_hit@{headline_k}": report["passage_hit_at"].get(f"hit@{headline_k}"),
            "passage_mrr": report["passage_mrr"],
            f"strict_hit@{headline_k}": report["strict_hit_at"].get(f"hit@{headline_k}"),
            "found_rate": report["passage_found_rate"],
            f"completeness@{headline_m}": report["completeness"].get(headline_m),
            f"density@{headline_m}": report["density"].get(headline_m),
            f"read_chars@{headline_m}": report["read_chars"].get(headline_m),
            **{k: v for k, v in report.get("meta", {}).items()},
        })
    rows.sort(
        key=lambda r: (
            r.get(f"passage_hit@{headline_k}") or 0,
            r.get(f"density@{headline_m}") or 0,
        ),
        reverse=True,
    )
    return rows
