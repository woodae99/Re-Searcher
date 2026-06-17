"""Unit tests for the extraction quality profile.

Hermetic: each test supplies its own small dictionary so results do not depend
on the system wordlists being installed.
"""

from src.extraction_quality import (
    QualityThresholds,
    _is_latin,
    deterministic_clean,
    load_dictionary,
    profile_text,
    strip_non_text,
)
from src.processing.extraction import ExtractionInput, ExtractionOutput, ExtractionRouter
from src.processing.extraction.extractors import ZoteroFulltextExtractor
from src.sources.zotero import ExtractionTask, ZoteroSource

CLEAN_WORDS = [
    "the", "coaching", "process", "helps", "people", "grow", "and", "learn",
    "over", "time", "with", "support", "from", "a", "skilled", "reflective",
    "practitioner", "who", "listens", "carefully",
]
DICT = frozenset(CLEAN_WORDS)


def _clean_text(repeats: int = 6) -> str:
    sentence = " ".join(CLEAN_WORDS)
    return ". ".join([sentence] * repeats) + "."


def test_clean_prose_is_accepted():
    profile = profile_text(_clean_text(), dictionary=DICT)
    assert profile.action == "accept"
    assert profile.grade == "good"
    assert profile.overall_score >= 0.9
    assert profile.signals["real_word_ratio"] >= 0.9


def test_empty_text_escalates():
    profile = profile_text("only three words here", dictionary=DICT)
    assert profile.grade == "empty"
    assert profile.action == "escalate"
    assert profile.overall_score == 0.0


def test_double_spacing_is_recoverable_not_escalated():
    # Clean words, but every gap is a double space (Docling/pdfminer noise).
    text = ".  ".join(["  ".join(CLEAN_WORDS)] * 6) + "."
    profile = profile_text(text, dictionary=DICT)
    assert profile.signals["double_space_ratio"] > 0.5
    assert profile.recoverable_penalty > profile.fundamental_penalty
    assert profile.action == "clean"  # cleanup, not a heavier extractor


def test_garbage_latin_tokens_escalate():
    garbage = " ".join(["xqwzk", "frbnp", "zzxqv", "mwktl", "vbnpq", "kxzwf"] * 10)
    profile = profile_text(garbage, dictionary=DICT)
    assert profile.signals["real_word_ratio"] < 0.2
    assert profile.penalties["garbage"] >= 0.9
    assert profile.action == "escalate"


def test_non_latin_script_skips_dictionary_check():
    # Clean Cyrillic prose would read as 'garbage' to a Latin-only dictionary;
    # the script guard must skip the dictionary penalty instead of escalating.
    cyrillic = " ".join(
        ["коучинг", "процесс", "помогает", "людям", "расти", "учиться", "развиваться"] * 8
    )
    profile = profile_text(cyrillic, dictionary=DICT)
    assert profile.signals["latin_token_ratio"] < 0.5
    assert profile.penalties["garbage"] == 0.0
    assert profile.action != "escalate"
    assert any("non-Latin" in note for note in profile.notes)


def test_strip_non_text_removes_image_payload_and_placeholders():
    text = (
        "Real heading\n"
        "![Image](data:image/png;base64,AAAABBBB)\n"
        "**==> picture [85 x 113] intentionally omitted <==**\n"
        "Real body line"
    )
    stripped = strip_non_text(text)
    assert "data:image" not in stripped
    assert "intentionally omitted" not in stripped
    assert "Real heading" in stripped and "Real body line" in stripped


def test_is_latin_distinguishes_scripts():
    assert _is_latin("coaching")
    assert _is_latin("réflexion")  # accented Latin
    assert not _is_latin("коучинг")  # Cyrillic
    assert not _is_latin("教练")  # CJK


def test_thresholds_are_overridable():
    strict = QualityThresholds(accept_at=0.999, clean_at=0.99)
    profile = profile_text(_clean_text(), dictionary=DICT, thresholds=strict)
    # With a near-perfect accept bar, ordinary prose drops below "good".
    assert profile.grade in {"borderline", "poor", "good"}


def test_deterministic_clean_promotes_clean_to_accept():
    # Double-spaced prose with a hyphenated line break -> recoverable noise only.
    noisy = ".  ".join(["  ".join(CLEAN_WORDS)] * 6) + "."
    noisy = noisy.replace("coaching", "coach-\ning", 1)
    before = profile_text(noisy, dictionary=DICT)
    after = profile_text(deterministic_clean(noisy), dictionary=DICT)
    assert before.action == "clean"
    assert after.action == "accept"
    assert after.recoverable_penalty < before.recoverable_penalty


def test_deterministic_clean_dehyphenates_and_collapses_spaces():
    cleaned = deterministic_clean("exam-\nple    text\n\n\n\nend  ")
    assert "example" in cleaned
    assert "    " not in cleaned
    assert "\n\n\n" not in cleaned


def test_load_dictionary_handles_missing_paths():
    # Non-existent paths must not raise; they just contribute no words.
    assert load_dictionary(("/nonexistent/wordlist",)) == frozenset()


def test_zotero_fulltext_extractor_uses_fetcher(tmp_path):
    extractor = ZoteroFulltextExtractor()
    source = ExtractionInput(
        file_path=tmp_path / "paper.pdf",
        attachment_key="ATT1",
        fulltext_fetcher=lambda key: _clean_text() if key == "ATT1" else None,
    )

    output = extractor.extract(source)

    assert output.text == _clean_text()
    assert output.extractor == "zotero-ft-cache"
    assert output.route == "zotero_ft_candidate"


def test_extraction_router_accepts_good_zotero_fulltext(tmp_path):
    router = ExtractionRouter(
        {"extraction": {"router": {"enabled": True}}},
        dictionary=DICT,
    )
    source = ExtractionInput(
        file_path=tmp_path / "paper.pdf",
        attachment_key="ATT1",
        fulltext_fetcher=lambda _key: _clean_text(),
        partial_fulltext_checker=lambda _text: False,
    )

    output = router.extract(source)

    assert output.ok
    assert output.extractor == "zotero-ft-cache"
    assert output.action == "accept"
    assert output.provenance()["extractor"] == "zotero-ft-cache"
    assert output.provenance()["extract_quality"].startswith("good:accept:")


def test_extraction_router_falls_back_to_pdfminer_when_zotero_missing(tmp_path):
    class MissingZotero:
        name = "zotero-ft-cache"

        def extract(self, source):
            return ExtractionOutput(
                text="",
                extractor=self.name,
                action="reject",
                route="zotero_ft_missing",
                errors=["missing"],
            )

    class GoodPdfminer:
        name = "pdfminer"

        def extract(self, source):
            return ExtractionOutput(
                text=_clean_text(),
                extractor=self.name,
                route="pdfminer_candidate",
            )

    router = ExtractionRouter(
        {"extraction": {"router": {"enabled": True}}},
        candidates=[MissingZotero(), GoodPdfminer()],
        dictionary=DICT,
    )

    output = router.extract(
        ExtractionInput(file_path=tmp_path / "paper.pdf", attachment_key="ATT1")
    )

    assert output.ok
    assert output.extractor == "pdfminer"
    assert output.fallbacks[0]["extractor"] == "zotero-ft-cache"
    assert output.route == "pdfminer_passed"


def test_extraction_router_falls_back_when_zotero_fulltext_is_partial(tmp_path):
    class GoodPdfminer:
        name = "pdfminer"

        def extract(self, source):
            return ExtractionOutput(text=_clean_text(), extractor=self.name)

    router = ExtractionRouter(
        {"extraction": {"router": {"enabled": True}}},
        candidates=[ZoteroFulltextExtractor(), GoodPdfminer()],
        dictionary=DICT,
    )
    source = ExtractionInput(
        file_path=tmp_path / "paper.pdf",
        attachment_key="ATT1",
        fulltext_fetcher=lambda _key: _clean_text(),
        partial_fulltext_checker=lambda _text: True,
    )

    output = router.extract(source)

    assert output.ok
    assert output.extractor == "pdfminer"
    assert output.fallbacks[0]["route"] == "zotero_ft_partial"


def test_extraction_router_cleans_recoverable_noise(tmp_path):
    noisy = ".  ".join(["  ".join(CLEAN_WORDS)] * 6) + "."

    class NoisyCandidate:
        name = "pdfminer"

        def extract(self, source):
            return ExtractionOutput(text=noisy, extractor=self.name)

    router = ExtractionRouter(
        {"extraction": {"router": {"enabled": True}}},
        candidates=[NoisyCandidate()],
        dictionary=DICT,
    )

    output = router.extract(
        ExtractionInput(file_path=tmp_path / "paper.pdf", attachment_key="ATT1")
    )

    assert output.ok
    assert output.action == "clean"
    assert output.route == "pdfminer_cleaned"
    assert "  " not in output.text


def test_extraction_router_marks_bad_text_for_escalation(tmp_path):
    class BadCandidate:
        name = "pdfminer"

        def extract(self, source):
            garbage = " ".join(["xqwzk", "frbnp", "zzxqv", "mwktl", "vbnpq", "kxzwf"] * 10)
            return ExtractionOutput(text=garbage, extractor=self.name)

    router = ExtractionRouter(
        {"extraction": {"router": {"enabled": True, "marker_enabled": False}}},
        candidates=[BadCandidate()],
        dictionary=DICT,
    )

    output = router.extract(
        ExtractionInput(file_path=tmp_path / "paper.pdf", attachment_key="ATT1")
    )

    assert not output.ok
    assert output.action == "escalate"
    assert output.extractor == "pdfminer"
    assert "marker escalation disabled by config" in output.warnings


def test_zotero_attachment_document_metadata_carries_extraction_provenance(tmp_path):
    pdf_path = tmp_path / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")
    source = ZoteroSource({"zotero": {"enabled": False}, "extraction": {"router": {"enabled": True}}})
    source.extraction_router = ExtractionRouter(
        {"extraction": {"router": {"enabled": True}}},
        dictionary=DICT,
    )
    source._fetch_local_api_fulltext = lambda _key: _clean_text()  # type: ignore[method-assign]
    source._collect_attachment_tasks = lambda *_args, **_kwargs: [  # type: ignore[method-assign]
        ExtractionTask(
            file_path=pdf_path,
            attachment_id=10,
            attachment_key="ATT1",
            filename="paper.pdf",
            content_type="application/pdf",
            file_size_mb=0.01,
            zotero_item_id=1,
        )
    ]

    docs = list(source._process_attachments(None, 1, {"zotero_key": "Z1", "title": "Title"}))

    assert len(docs) == 1
    metadata = docs[0].metadata
    assert metadata["source_type"] == "zotero_fulltext"
    assert metadata["extractor"] == "zotero-ft-cache"
    assert metadata["extract_action"] == "accept"
    assert metadata["extract_quality"].startswith("good:accept:")
    assert metadata["extract_route"] == "zotero_ft_cache_passed"
