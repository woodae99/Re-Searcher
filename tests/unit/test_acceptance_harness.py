from src.acceptance_harness import (
    QuoteProbe,
    compare_registry_to_collection,
    normalize_quote_text,
    run_registry_harness,
    scan_text_artifacts,
    verify_quotes,
)
from src.registry import SourceRegistry


class _FakeCollection:
    def __init__(self, records):
        self.records = list(records)

    def get(self, ids=None, where=None, include=None, limit=None, offset=None):
        records = self.records
        if ids is not None:
            wanted = set(ids)
            records = [record for record in records if record["id"] in wanted]
        if where:
            records = [
                record
                for record in records
                if all(record["metadata"].get(key) == value for key, value in where.items())
            ]
        if offset is not None:
            records = records[offset:]
        if limit is not None:
            records = records[:limit]

        include = include or []
        result = {"ids": [record["id"] for record in records]}
        if "documents" in include:
            result["documents"] = [record.get("document", "") for record in records]
        if "metadatas" in include:
            result["metadatas"] = [record.get("metadata", {}) for record in records]
        return result


def _record(chunk_id, key, text, ordinal=0, source_id=None):
    metadata = {
        "source_type": "zotero_fulltext",
        "zotero_key": key,
        "source_id": source_id or f"zotero-{key}-attachment",
        "chunk_level": "mid",
        "chunk_index": ordinal,
        "title": f"Title {key}",
    }
    return {"id": chunk_id, "document": text, "metadata": metadata}


def _registry(tmp_path, records):
    registry = SourceRegistry(tmp_path / "registry.test.sqlite")
    registry.record_chunks(
        [record["id"] for record in records],
        [record["metadata"] for record in records],
    )
    registry.refresh_sources()
    return registry


def test_normalize_quote_text_handles_ligature_and_line_hyphenation():
    text = "The ﬂ uid process actu-\nally works."

    assert normalize_quote_text("fluid process actually") in normalize_quote_text(text)


def test_verify_quotes_checks_id_source_and_normalized_substring():
    collection = _FakeCollection(
        [_record("c1", "Z1", "The coaching process actu-\nally unfolds here.")]
    )

    report = verify_quotes(
        collection,
        [
            QuoteProbe(
                chunk_id="c1",
                quote="process actually unfolds",
                identity_field="zotero_key",
                identity_value="Z1",
            )
        ],
    )

    assert report["pass"] is True
    assert report["failed"] == 0


def test_verify_quotes_reports_membership_mismatch():
    collection = _FakeCollection([_record("c1", "Z1", "Quoted text.")])

    report = verify_quotes(
        collection,
        [
            QuoteProbe(
                chunk_id="c1",
                quote="Quoted text",
                identity_field="zotero_key",
                identity_value="Z2",
            )
        ],
    )

    assert report["pass"] is False
    assert report["failures"][0]["reason"] == "source_membership_mismatch"


def test_scan_text_artifacts_flags_known_pdf_artifacts():
    report = scan_text_artifacts(
        [
            ("c1", "The word actu-\nally is broken."),
            ("c2", "A raw ﬂ uid ligature remains."),
            ("c3", "Plain clean sentence."),
        ]
    )

    assert report["pass"] is False
    assert report["artifact_counts"]["line_hyphenation"] == 1
    assert report["artifact_counts"]["raw_ligature"] == 1


def test_scan_text_artifacts_flags_letter_spaced_words():
    report = scan_text_artifacts(
        [("c1", "scrambled cover letters A B C D E F G appear here.")]
    )

    assert report["artifact_counts"]["letter_spaced_words"] == 1
    assert "letter_spaced_words" in report["over_tolerance"]
    assert report["pass"] is False


def test_scan_text_artifacts_tolerance_allows_low_artifact_rate():
    records = [("clean%d" % i, "Plain clean sentence.") for i in range(9)]
    records.append(("noisy", "The word actu-\nally is broken."))

    strict = scan_text_artifacts(records)
    assert strict["pass"] is False
    assert strict["artifact_rates"]["line_hyphenation"] == 0.1

    tolerant = scan_text_artifacts(records, tolerance=0.2)
    assert tolerant["pass"] is True
    assert tolerant["over_tolerance"] == []


def test_compare_registry_to_collection_passes_exact_membership(tmp_path):
    records = [
        _record("c1", "Z1", "one", ordinal=0),
        _record("c2", "Z1", "two", ordinal=1),
        _record("c3", "Z2", "three", ordinal=0),
    ]
    registry = _registry(tmp_path, records)

    report = compare_registry_to_collection(registry, _FakeCollection(records))

    assert report["pass"] is True
    assert report["checked_sources"] == 2


def test_compare_registry_to_collection_reports_drift(tmp_path):
    records = [_record("c1", "Z1", "one", ordinal=0)]
    registry = _registry(tmp_path, records)

    report = compare_registry_to_collection(registry, _FakeCollection([]))

    assert report["pass"] is False
    assert report["mismatches"][0]["missing_from_collection"] == ["c1"]


def test_run_registry_harness_combines_p0_checks(tmp_path):
    records = [
        _record("c1", "Z1", "The coaching process actually unfolds.", ordinal=0),
        _record("c2", "Z1", "Another clean chunk.", ordinal=1),
    ]
    registry = _registry(tmp_path, records)

    report = run_registry_harness(
        registry,
        _FakeCollection(records),
        quote_probes=[QuoteProbe("c1", "process actually unfolds", "zotero_key", "Z1")],
    )

    assert report["pass"] is True
    assert report["checks"]["duplicates"]["duplicate_slots"] == 0
