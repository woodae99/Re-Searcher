"""Unit tests for src/passage_eval.py span arithmetic and metrics."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from src.passage_eval import (  # noqa: E402
    ChunkRef,
    PassageProbe,
    compare_passage_configs,
    covered_len,
    evaluate_passage,
    expansion_window,
    overlap,
    union_len,
)


def test_overlap():
    assert overlap(0, 10, 5, 15) == 5
    assert overlap(0, 10, 10, 20) == 0  # touching, half-open
    assert overlap(0, 10, 20, 30) == 0
    assert overlap(0, 100, 30, 40) == 10


def test_union_len_merges_overlaps():
    assert union_len([(0, 10), (5, 15)]) == 15  # not 20
    assert union_len([(0, 10), (20, 30)]) == 20
    assert union_len([(0, 10), (10, 20)]) == 20  # adjacent merge
    assert union_len([]) == 0


def test_covered_len_clips_to_gold():
    # gold [100, 200); a window span [50, 150) covers 50 of it
    assert covered_len([(50, 150)], 100, 200) == 50
    # two spans covering the whole gold
    assert covered_len([(100, 160), (150, 220)], 100, 200) == 100
    # span entirely outside gold
    assert covered_len([(0, 50)], 100, 200) == 0


def _index(spans):
    """Build a single-source chunk_index from (ordinal,start,end) tuples."""
    chunks = [ChunkRef("S", o, s, e) for (o, s, e) in spans]
    return {"S": chunks}, chunks


def test_expansion_window_picks_neighbours():
    _, chunks = _index([(0, 0, 100), (1, 100, 200), (2, 200, 300), (3, 300, 400)])
    win0 = expansion_window(chunks, 1, 0)
    assert [c.ordinal for c in win0] == [1]
    win1 = expansion_window(chunks, 1, 1)
    assert [c.ordinal for c in win1] == [0, 1, 2]
    # clamps at edges
    win_edge = expansion_window(chunks, 0, 1)
    assert [c.ordinal for c in win_edge] == [0, 1]


def test_completeness_rises_and_density_falls_with_expansion():
    # Four contiguous 100-char chunks; gold span [120, 280) (160 chars) straddles
    # chunks 1,2,3. Center on chunk 2 (best overlap).
    index, chunks = _index([(0, 0, 100), (1, 100, 200), (2, 200, 300), (3, 300, 400)])
    probe = PassageProbe(query="q", source_id="S", gold_start=120, gold_end=280, probe_id="p")

    def search_fn(query, k):
        # return chunk 2 first (the best-overlapping), then a distractor
        return [chunks[2], ChunkRef("OTHER", 0, 0, 100)][:k]

    report = evaluate_passage(search_fn, [probe], index, expansions=(0, 1, 2), k_values=(1, 3))
    comp = report["completeness"]
    dens = report["density"]
    # m=0: only chunk2 [200,300) covers [200,280)=80 of 160 -> 0.5
    assert comp[0] == 0.5
    # expansion strictly increases completeness here
    assert comp[1] > comp[0]
    assert comp[2] >= comp[1]
    # density falls as we read more off-target text
    assert dens[0] >= dens[1] >= dens[2]
    assert report["passage_found_rate"] == 1.0


def test_passage_miss_when_only_distractors_returned():
    index, chunks = _index([(0, 0, 100), (1, 100, 200)])
    probe = PassageProbe(query="q", source_id="S", gold_start=120, gold_end=180, probe_id="p")

    def search_fn(query, k):
        return [ChunkRef("OTHER", 0, 0, 100)][:k]

    report = evaluate_passage(search_fn, [probe], index, k_values=(1, 3))
    assert report["passage_hit_at"]["hit@3"] == 0.0
    assert report["passage_found_rate"] == 0.0


def test_wrong_passage_same_source_does_not_complete():
    # Right book, wrong chapter: retrieved chunk 0 is far from gold [500,560).
    index, chunks = _index([(0, 0, 100), (5, 500, 600)])
    probe = PassageProbe(query="q", source_id="S", gold_start=500, gold_end=560, probe_id="p")

    def search_fn(query, k):
        return [chunks[0]][:k]  # only the wrong-chapter chunk

    report = evaluate_passage(search_fn, [probe], index, expansions=(0, 1), k_values=(1, 3))
    # chunk 0 does not overlap the gold span -> passage not found
    assert report["passage_found_rate"] == 0.0
    assert report["passage_hit_at"]["hit@1"] == 0.0


def test_compare_ranks_by_hit_then_density():
    a = {
        "passage_hit_at": {"hit@5": 0.9}, "passage_mrr": 0.8,
        "strict_hit_at": {"hit@5": 0.7}, "passage_found_rate": 0.9,
        "completeness": {1: 0.95}, "density": {1: 0.4}, "read_chars": {1: 1500},
    }
    b = {
        "passage_hit_at": {"hit@5": 0.9}, "passage_mrr": 0.8,
        "strict_hit_at": {"hit@5": 0.7}, "passage_found_rate": 0.9,
        "completeness": {1: 0.95}, "density": {1: 0.6}, "read_chars": {1: 1000},
    }
    table = compare_passage_configs({"a": a, "b": b}, headline_k=5, headline_m=1)
    # equal hit@5 -> higher density wins
    assert table[0]["config"] == "b"
