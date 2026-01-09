"""Unit tests for the quality filter guard."""

from src.processing.quality_filter import QualityFilterGuard, is_low_info


def test_min_unique_char_ratio():
    cfg = {
        "chunking": {
            "quality_filter": {
                "min_alnum_chars": 0,
                "min_alnum_ratio": 0.0,
                "max_whitespace_ratio": 1.0,
                "min_token_est": 0,
                "min_unique_char_ratio": 0.5,
                "max_line_repeat_ratio": 1.0,
                "drop_if_matches": [],
            }
        }
    }
    drop, reasons = is_low_info("aaaaaaaaaaaaaaaab", cfg["chunking"]["quality_filter"])
    assert drop is True
    assert "min_unique_char_ratio" in reasons


def test_max_line_repeat_ratio():
    cfg = {
        "chunking": {
            "quality_filter": {
                "min_alnum_chars": 0,
                "min_alnum_ratio": 0.0,
                "max_whitespace_ratio": 1.0,
                "min_token_est": 0,
                "min_unique_char_ratio": 0.0,
                "max_line_repeat_ratio": 0.30,
                "drop_if_matches": [],
            }
        }
    }
    text = "header\nheader\nheader\ncontent line with enough chars"
    drop, reasons = is_low_info(text, cfg["chunking"]["quality_filter"])
    assert drop is True
    assert "max_line_repeat_ratio" in reasons


def test_dry_run_keeps_chunks():
    cfg = {
        "chunking": {
            "quality_filter": {
                "enabled": False,
                "dry_run": True,
                "min_alnum_chars": 100,
                "min_alnum_ratio": 0.9,
                "max_whitespace_ratio": 0.1,
                "min_token_est": 50,
                "min_unique_char_ratio": 0.9,
                "max_line_repeat_ratio": 0.1,
                "drop_if_matches": ["^\\*{3,}$"],
            }
        }
    }
    guard = QualityFilterGuard(cfg)
    chunks = ["***", "valid chunk with enough content to pass filters"]
    metas = [{"source_id": "a"}, {"source_id": "b"}]
    ids = ["a-1", "b-1"]
    out_chunks, out_metas, out_ids = guard.process_with_ids(chunks, metas, ids)
    assert out_chunks == chunks
    assert out_metas == metas
    assert out_ids == ids
    assert guard.stats.dropped == 0
    assert guard.stats.dropped_candidates > 0


def test_whitelist_source_id_keeps_chunk():
    cfg = {
        "chunking": {
            "quality_filter": {
                "enabled": True,
                "dry_run": False,
                "whitelist_source_ids": ["keep-me"],
                "min_alnum_chars": 100,
                "min_alnum_ratio": 0.9,
                "max_whitespace_ratio": 0.1,
                "min_token_est": 50,
                "min_unique_char_ratio": 0.9,
                "max_line_repeat_ratio": 0.1,
                "drop_if_matches": [],
            }
        }
    }
    guard = QualityFilterGuard(cfg)
    chunks = ["***"]
    metas = [{"source_id": "keep-me"}]
    ids = ["keep-me-1"]
    out_chunks, out_metas, out_ids = guard.process_with_ids(chunks, metas, ids)
    assert out_chunks == chunks
    assert out_metas == metas
    assert out_ids == ids
    assert guard.stats.dropped == 0
