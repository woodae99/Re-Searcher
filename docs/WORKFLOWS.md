# Research Workflows (Minimum Viable)

These workflows describe how to use this repo effectively for research/writing *without* adding more complexity to the CLI/UI.

The guiding principle is:
- keep the CLI simple and reliable,
- let an “agent” (human or automated) orchestrate multiple queries when a task requires depth, comparison, or synthesis.

---

## Workflow 1 — Broad scan → shortlist (survey mode, implicitly)

**Use when:** you want coverage across the literature (e.g. “coaching is a social process”).

**Steps**
1) Run a broad semantic query with a moderately large recall:
   - set `retrieval.k_recall` high enough to cast the net (e.g. 50–200)
   - return a smaller top‑k (e.g. 10)
2) Skim the returned sources (titles/authors/source_type) and shortlist.
3) Re-query using additional terms from the best hits (names, keywords, related theories).

**Success looks like:** diversity of sources, not 10 chunks from the same paper.

---

## Workflow 2 — Depth-first on a single author/work

Tip: if diversity/dedupe is hiding depth, run with:
- `--no-diversity` (allow many chunks from the same source)
- or `--max-per-source 10` (controlled depth)

**Use when:** you want a focused explanation of one author’s concept (e.g. “How does Deleuze conceive of intensities?”).

**Steps**
1) Query for the concept broadly (don’t over-filter initially).
2) Identify the most relevant primary works and/or high-quality commentaries from the results.
3) Drill down by iterating queries with tighter phrasing and disambiguators:
   - include work titles / key terms
   - include “definition”, “means”, “in Deleuze”, “Difference and Repetition”, etc.
4) When you have the key passages, switch from “recall” to “evidence collection”:
   - gather multiple chunks from the same source
   - keep note of backlinks for citation/verification.

**Success looks like:** multiple complementary passages, not just repeated headings.

---

## Workflow 3 — Concept hunting (analogues across vocabularies)

**Use when:** you want conceptual equivalents rather than keyword matches (e.g. analogues of “intensities” in other philosophers).

**Steps**
1) Start with a *definition-building query*:
   - “Deleuze intensities definition”
   - “what are intensities Deleuze difference repetition”
2) Extract candidate paraphrases / attributes (what intensities *do*, how they relate to difference, affect, individuation, etc.).
3) Use those attributes as new queries (not the original term):
   - e.g. “gradations of difference”, “pre-individual singularities”, “intensive magnitude”, “affect intensity”, etc.
4) Use results to expand your hypothesis space:
   - collect authors/terms you didn’t expect
   - re-query with their terminology.

**Success looks like:** novel but defensible connections, supported by passages.

---

## Workflow 4 — Writing support (retrieve → outline → draft)

**Use when:** you want a short section/paragraph drafted with traceable support.

**Steps**
1) Retrieve: run queries to collect evidence chunks.
2) Outline: create a brief structure (claims → evidence) before drafting.
3) Draft: write using the outline, weaving in evidence.
4) Verify: click backlinks (Zotero/Obsidian) for any claim you’ll publish.

**Notes**
- In this repo, “synthesis” is not the CLI’s job yet. Treat synthesis as a separate agent step that consumes retrieved chunks.

---

## Operational tips
- Expect a **warm-up delay** on large Chroma collections (minutes) on first access.
- If reranking is enabled, keep candidate text snippets bounded to avoid truncation.
- For deep work, it’s normal to run multiple queries iteratively.
