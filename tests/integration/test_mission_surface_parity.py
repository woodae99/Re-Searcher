"""Mission-anchored validation of the live v0.6 build on Sparky.

These are *small test units* derived from the process-in-coaching mission spec
(`test_sources/.../research-missions/2026-06-process-in-coaching/mission-spec.md`).
They do NOT run the mission — they prove that the system the mission depends on is
present, that the CLI and MCP surfaces are functionally equivalent, and that both
exploit the v0.6 two-plane architecture (register as control plane, Chroma as a
single working grain).

Each unit is pinned to a hand-picked sample source matching the pilot composition
(§8: journal articles + book chapters + at least one likely-null). They run against
the *live* collection named in the project `config.yaml`, so they are guarded by a
service-availability skip rather than the synthetic `integration_config` fixture.

Run:  .venv/bin/pytest tests/integration/test_mission_surface_parity.py -v
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = PROJECT_ROOT / "config.yaml"
PY = sys.executable

# --- Sample texts (mirror the §8 pilot composition) -------------------------
# Hand-picked from the live register, not sampled — one of each kind the mission
# screens, including a likely-null so the null-honesty path is exercised.
BOOK = "8Y47IM26"            # "A critical introduction to coaching and mentoring" (book, ~1763 mid)
ARTICLE_SUBSTANTIVE = "E8IX37DE"   # "A call for clarity and pragmatism in coach education" (journalArticle)
ARTICLE_NULLISH = "K6VBL8CB"       # "1st international Congress ... update" (journalArticle, likely incidental)
OBSIDIAN = "obsidian-Daily Notes/2025-11-26.md"  # non-Zotero identity rule

ZOTERO_SAMPLES = [BOOK, ARTICLE_SUBSTANTIVE, ARTICLE_NULLISH]


# --- Service-availability guard --------------------------------------------
def _services_up() -> tuple[bool, str]:
    """Return (ok, reason). Checks the live Chroma + vLLM embed endpoints."""
    if not CONFIG_PATH.exists():
        return False, f"missing {CONFIG_PATH}"
    cfg = yaml.safe_load(CONFIG_PATH.read_text())
    chroma = cfg.get("storage", {}).get("endpoint", "http://localhost:8000")
    vllm = cfg.get("embedding", {}).get("vllm", {}).get("base_url", "")
    try:
        import urllib.request

        urllib.request.urlopen(f"{chroma}/api/v2/heartbeat", timeout=3).read()
    except Exception as exc:  # pragma: no cover - environment dependent
        return False, f"ChromaDB not reachable at {chroma}: {exc}"
    if vllm:
        try:
            import urllib.request

            urllib.request.urlopen(f"{vllm}/models", timeout=3).read()
        except Exception as exc:  # pragma: no cover
            return False, f"vLLM embed not reachable at {vllm}: {exc}"
    return True, "ok"


_UP, _REASON = _services_up()
pytestmark = [
    pytest.mark.integration,
    pytest.mark.requires_chromadb,
    pytest.mark.skipif(not _UP, reason=f"live Sparky stack down: {_REASON}"),
]


# --- Surface drivers: run the REAL CLI and the REAL MCP handler -------------
def _parse_pristine_json(proc: subprocess.CompletedProcess) -> dict:
    """Parse stdout as JSON with NO slicing.

    Diagnostics/banners go to stderr, so stdout must be machine-clean JSON. Parsing
    the whole of stdout (rather than slicing from the first `{`) makes these tests
    fail loudly if any status line ever leaks back onto stdout.
    """
    assert proc.returncode == 0, f"CLI failed: {proc.stderr or proc.stdout}"
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - failure path
        raise AssertionError(
            f"stdout is not pristine JSON (status line leaked?): {exc}\n"
            f"--- stdout ---\n{proc.stdout[:500]}"
        )


def run_cli(*args: str) -> dict:
    """Invoke `scripts/sources.py --json` exactly as a user would; return the payload."""
    proc = subprocess.run(
        [PY, "scripts/sources.py", *args, "--json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    return _parse_pristine_json(proc)


def run_query_cli(*args: str) -> dict:
    """Invoke `scripts/query.py --json` (search/survey surface); return the payload."""
    proc = subprocess.run(
        [PY, "scripts/query.py", *args, "--json"],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=180,
    )
    return _parse_pristine_json(proc)


@pytest.fixture(scope="module")
def mcp_server():
    """A live MCP server bound to the project config (the real tool surface)."""
    from src.mcp_server import ResearchMCPServer

    return ResearchMCPServer(CONFIG_PATH)


def mcp_text(server, method_name: str, arguments: dict) -> str:
    """Call an MCP tool handler and return its TextContent string."""
    method = getattr(server, method_name)
    result = asyncio.run(method(arguments))
    assert result, f"{method_name} returned no content"
    return result[0].text


# ===========================================================================
# Unit group 1 — Interface availability
# ===========================================================================
class TestInterfaceAvailability:
    """Both surfaces exist and answer against the live build."""

    def test_cli_status_reports_live_collection(self):
        payload = run_cli("status")
        assert payload["registry"]["chunk_count"] > 0
        # Chroma and the registry agree: the control plane mirrors the vector store.
        assert payload["drift"] == 0

    def test_mcp_exposes_the_six_tools(self, mcp_server):
        # The handlers the mission relies on are all wired in dispatch.
        for name in (
            "_search_research_library",
            "_survey_research_sources",
            "_get_chunk_context",
            "_get_source_chunks",
            "_list_sources",
            "_index_status",
        ):
            assert callable(getattr(mcp_server, name))

    def test_mcp_index_status_matches_cli(self, mcp_server):
        cli = run_cli("status")
        text = mcp_text(mcp_server, "_index_status", {})
        # Same registry chunk count on both surfaces.
        assert f"{cli['registry']['chunk_count']:,}" in text


# ===========================================================================
# Unit group 2 — CLI / MCP functional equivalence (the parity claim)
# ===========================================================================
class TestCliMcpEquivalence:
    """Same operation, both surfaces, identical answer — on sample texts."""

    @pytest.mark.parametrize("zkey", ZOTERO_SAMPLES)
    def test_enumeration_parity(self, mcp_server, zkey):
        # CLI census of one source...
        cli = run_cli("chunks", "--zotero-key", zkey, "--no-text", "--limit", "200")
        total = cli["total_matching"]
        cli_ids = {c["chunk_id"] for c in cli["chunks"]}
        assert total > 0

        # ...vs the MCP handler over the identical identity.
        text = mcp_text(
            mcp_server,
            "_get_source_chunks",
            {"zotero_key": zkey, "include_text": False, "limit": 200},
        )
        # MCP reports the same population and the same chunk identities.
        assert str(total) in text
        for cid in list(cli_ids)[:5]:
            assert cid in text, f"MCP output missing chunk {cid}"

    def test_register_listing_parity(self, mcp_server):
        cli = run_cli("list", "--item-type", "book", "--limit", "5")
        text = mcp_text(
            mcp_server, "_list_sources", {"item_type": "book", "limit": 5}
        )
        # Same population total and same source identities on both planes.
        assert str(cli["total_sources"]) in text
        for src in cli["sources"][:3]:
            assert src["identity_value"] in text


# ===========================================================================
# Unit group 3 — New architecture exercised through the mission
# ===========================================================================
class TestSingleGrainRetrievalPlane:
    """Chroma is one working grain: mid only, no hierarchy, no parent_id."""

    @pytest.mark.parametrize("zkey", ZOTERO_SAMPLES)
    def test_only_mid_chunks_exist(self, zkey):
        cli = run_cli("chunks", "--zotero-key", zkey, "--no-text", "--limit", "200")
        for chunk in cli["chunks"]:
            assert "-mid-" in chunk["chunk_id"], f"non-mid grain: {chunk['chunk_id']}"
            # The retired hierarchy navigation must be gone from the chunk plane.
            assert "parent_id" not in chunk["metadata"]

    def test_register_records_single_grain_counts(self):
        cli = run_cli("list", "--item-type", "journalArticle", "--limit", "10")
        for src in cli["sources"]:
            counts = src.get("chunk_counts", {})
            assert counts.get("coarse", 0) == 0
            assert counts.get("fine", 0) == 0
            assert counts.get("mid", 0) >= 0


class TestRegisterAsControlPlane:
    """§3 register selection runs off metadata, not a collection scan."""

    def test_w8_selection_metadata_present(self):
        cli = run_cli("list", "--item-type", "book", "--limit", "3")
        assert cli["sources"], "no book sources in register"
        src = cli["sources"][0]
        # The systematic-review filter plane: item_type drives selection.
        assert src.get("item_type") == "book"

    def test_provenance_supports_screening_eligibility(self):
        # §decisions: "items without indexed full text are excluded + flagged".
        # That decision must be computable from register provenance alone. Only
        # extracted full-text sources carry provenance — notes/annotations have no
        # extraction step (asserted separately below), so scope this to fulltext.
        cli = run_cli(
            "list", "--source-type", "zotero_fulltext", "--limit", "10"
        )
        assert cli["sources"], "no zotero_fulltext sources in register"
        for src in cli["sources"]:
            assert src.get("extractor"), (
                f"no extractor recorded for {src['identity_value']}"
            )
            assert src.get("extract_quality"), "no extract_quality for screening gate"

    def test_note_only_sources_carry_no_extraction_provenance(self):
        # Correctness boundary: a note-only source (source_type exactly
        # "zotero_note", no fulltext attachment) has no extraction step, so empty
        # provenance is correct and must not be read as a failed extraction during
        # screening eligibility. (Sources that ALSO have fulltext legitimately show
        # the fulltext extractor.)
        cli = run_cli("list", "--source-type", "zotero_note", "--limit", "20")
        note_only = [s for s in cli["sources"] if s.get("source_type") == "zotero_note"]
        assert note_only, "no note-only sources found to exercise the boundary"
        for src in note_only:
            assert src.get("extractor", "") == "", (
                f"note-only source {src['identity_value']} has unexpected extractor"
            )


class TestScreeningEnumeration:
    """§5 screening pass: 'over all mid-level chunks' with a deterministic stop."""

    @pytest.mark.parametrize("zkey", ZOTERO_SAMPLES)
    def test_enumeration_is_complete_and_terminating(self, zkey):
        # First page tells us the full population.
        head = run_cli("chunks", "--zotero-key", zkey, "--no-text", "--limit", "1")
        total = head["total_matching"]
        assert total > 0

        # Page through to exhaustion exactly as a screening job would; collect
        # every mid chunk, then assert the deterministic stopping rule: we saw
        # all of them, with no duplicates and no overrun.
        seen: set[str] = set()
        offset, page = 0, 200
        while offset < total:
            payload = run_cli(
                "chunks", "--zotero-key", zkey, "--no-text",
                "--limit", str(page), "--offset", str(offset),
            )
            ids = [c["chunk_id"] for c in payload["chunks"]]
            assert ids, f"empty page at offset {offset} (total={total})"
            seen.update(ids)
            offset += page

        assert len(seen) == total, f"enumeration incomplete: {len(seen)}/{total}"

    def test_obsidian_source_enumerates_by_source_id(self):
        # The non-Zotero identity rule: group by source_id, not zotero_key.
        cli = run_cli("chunks", "--source-path", OBSIDIAN, "--no-text", "--limit", "50")
        assert cli["total_matching"] >= 1
        assert cli["source"]["identity_field"] == "source_id"


class TestSurveyAndScreeningRetrieval:
    """§7 survey-by-source and §8 pinned-source screening retrieval.

    query.py now emits a structured `--json` payload built from the same shared
    formatter the MCP search/survey tools use, so these assert on the machine
    surface directly and check CLI↔MCP agreement.
    """

    def test_survey_aggregates_mid_chunks_by_source(self):
        # The v0.6 broad-survey replacement for coarse search: aggregate mid hits
        # into source rows (the sense-distribution-by-source the mission needs).
        payload = run_query_cli(
            "process philosophy becoming flux",
            "--survey", "--max-per-source", "1", "-k", "12",
        )
        sources = payload.get("sources")
        assert sources, f"survey returned no source rows: {list(payload)}"
        # Aggregation is over the single working grain.
        for rep in sources[0].get("representative_chunks", []):
            assert rep.get("chunk_level") == "mid"

    def test_survey_parity_cli_vs_mcp(self, mcp_server):
        query = "process philosophy becoming flux"
        cli = run_query_cli(query, "--survey", "--max-per-source", "1", "-k", "12")
        mcp = mcp_text(
            mcp_server,
            "_survey_research_sources",
            {"query": query, "max_per_source": 1, "k": 12},
        )
        # Both surfaces surface the same top source identity for the same query.
        top = cli["sources"][0]["identity_value"]
        assert top in mcp, "MCP survey disagrees on top source"

    def test_pinned_source_screening_search(self):
        # §8 pilot approximation: search scoped to one source, no diversity/rerank,
        # high recall — every returned hit must belong to the pinned source.
        payload = run_query_cli(
            "how is process used in coaching",
            "--zotero-key", ARTICLE_SUBSTANTIVE,
            "--no-diversity", "--no-rerank", "-k", "10",
        )
        results = payload["results"]
        assert results, f"pinned-source search returned nothing: {payload}"
        for hit in results:
            # The pinned key shows up in the per-hit Zotero backlink.
            assert ARTICLE_SUBSTANTIVE in hit.get("backlink", ""), (
                f"leaked non-pinned source: {hit.get('backlink')}"
            )

    def test_search_json_matches_mcp_formatter(self):
        # The CLI machine surface is built from the same shared formatter as MCP
        # search_research_library: a hit carries rank/id/score/title + backlink.
        payload = run_query_cli(
            "coaching process", "--zotero-key", ARTICLE_SUBSTANTIVE,
            "--no-rerank", "-k", "3",
        )
        assert payload["query"] and "results" in payload
        for hit in payload["results"]:
            assert {"rank", "id", "score", "title", "chunk_level"} <= set(hit)
