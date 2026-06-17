# Agent Cookbook — Re-Searcher (CLI + MCP)

This document is written for an **agent** (human or automated) that needs to complete research/writing “missions” using this repo.

It describes:
- what tools exist,
- how to choose parameters,
- how to break a mission into steps,
- what “done” means.

---

## Core idea

The v0.6 library is indexed into Chroma as **chunks** (`mid` for text/markdown,
`atomic` for Zotero annotations). A good mission usually follows a loop:

1) **Recall** (semantic search): pull candidate chunks
2) **Refine** (filters/diversity/rerank): improve precision and coverage
3) **Expand** (context): enumerate neighbouring chunks from the same source
4) **Synthesize** (optional): draft answer with citations/backlinks

---

## Available interfaces

### 1) MCP tools (preferred for agents)

Tool: `survey_research_sources`

Use this for broad survey / candidate discovery. It searches `mid` chunks, groups
hits by registry identity, and returns source rows with hit counts, best scores,
selection metadata, and representative chunk IDs/snippets.

Tool: `search_research_library`

Inputs (key ones):
- `query` (string, required)
- `k` (int): how many results to return
- `k_recall` (int): how many candidates to recall before rerank/diversity (useful to bound post-filters)

Rerank/diversity controls:
- `no_rerank` (bool): disable reranking for reliability/debugging
- `no_diversity` (bool): allow many chunks from same source (deep dive)
- `max_per_source` (int): controlled depth (1 for breadth, 10+ for deep)

Filters (deep dives):
- `source_type` (string): e.g. `zotero_fulltext`, `zotero_note`, `zotero_annotation`, `obsidian`
- `zotero_key` (string): exact item deep dive
- `author` (string): post-filter where metadata `authors` contains string
- `title_contains` (string): post-filter where metadata `title` contains string
- `year_min` / `year_max` (int)
- `where` (object): advanced raw Chroma where dict (ANDed with other filters)

Tool: `get_chunk_context`
- `chunk_id` (string, required)
- `include_parent` (bool, default true)

Tool: `list_sources`
- Build a source register with identity fields, titles/authors, and chunk counts.
- Use `source_type`, `title_contains`, `author`, `limit`, and `offset` to scope the register.
- Zotero rows use `zotero_key`; Obsidian/local rows use `source_id`.

Tool: `get_source_chunks`
- Enumerate chunks from one source with stable pagination.
- Pass Zotero rows with `zotero_key=<identity_value>`.
- Pass Obsidian/local rows with `source_path=<identity_value>`.
- Use `chunk_level=mid` for most systematic extraction passes.


### 2) CLI (good for humans / debugging)

`scripts/query.py` supports the same concepts, plus:
- `--no-rerank`
- `--no-diversity`
- `--max-per-source N`
- `--k-recall N`
- `--source-type ...`
- `--zotero-key ...`
- `--author ...`
- `--title-contains ...`
- `--year-min/--year-max`

---

## Parameter strategy (how to choose knobs)

### k vs k_recall
- `k` = how many you show to the user.
- `k_recall` = how many candidates you consider before rerank/diversity.

Rules of thumb:
- Breadth scan: `k=10`, `k_recall=50`
- Hard query / concept hunting: `k=10`, `k_recall=200`
- Post-filter by author/title: start with `k_recall=100` (avoid 500 unless needed)

### Diversity controls
- Default (recommended): diversity ON, `max_per_source=2`
- Breadth scan: `max_per_source=1`
- Deep study: `no_diversity=true` OR `max_per_source=10..50`

### Filters
Prefer store-level filters when possible:
- `zotero_key`, `source_type`, `year_min/max` are cheap (Chroma where).

Use post-filters when needed:
- `author`, `title_contains` (substring filters) can be slower; keep `k_recall` bounded.

---

## Mission patterns (templates)

### Pattern A — Broad literature scan
**Goal:** Find a diverse set of relevant sources.

1) Query breadth-first:
- `max_per_source=1`, `k=10`, `k_recall=50`
2) If results are shallow, increase recall:
- `k_recall=200`
3) Extract candidate sources (titles/authors/backlinks) for shortlist.

Done when:
- at least 5–10 distinct sources are identified,
- and you can name 2–4 clusters/themes.

---

### Pattern B — Deep study of a single author (e.g. Merleau-Ponty)
**Goal:** Understand an author’s concept(s) and collect evidence.

1) Start with author post-filter (bounded):
- `author="Merleau-Ponty"`, `k_recall=100`, `no_diversity=true`, `k=10`
2) From results, identify key works via `zotero_key`.
3) Switch to exact deep dive:
- `zotero_key=<key>`, `no_diversity=true`, `k=20`, `k_recall=200`
4) Expand context for the best fine chunks:
- call `get_chunk_context(chunk_id)` to fetch parent (mid/coarse) context.

Done when:
- you can write a 3–5 point outline of the author’s position,
- you have 3–8 quotable passages (fine chunks) with backlinks,
- you can cite which work(s) the claims come from.

---

### Pattern C — Notes-only scan (your own notes)
**Goal:** Search only your authored notes/annotations.

1) Restrict to notes:
- `source_type=zotero_note` (and/or `obsidian`) and query
2) Use diversity ON unless you want depth.

Done when:
- you’ve located the relevant note(s) and can link back.

---

### Pattern D — Concept hunting (analogues across vocabularies)
**Goal:** Find conceptual equivalents where vocabulary differs.

1) Definition-building query:
- no author filter, diversity ON, `k_recall=200`
2) Extract attributes/phrases from best hits.
3) Run follow-up queries on those attributes.
4) When a candidate author emerges, re-run with `author` post-filter.

Done when:
- you can propose at least 2–3 plausible analogues,
- with evidence chunks for each.

---

### Pattern E — Systematic per-source mining
**Goal:** Screen or extract from a closed corpus where coverage and explicit nulls matter.

1) Build and freeze a source register:
- call `list_sources` with the required `source_type` / title / author filters
- record each row's identity field, identity value, title, authors, and chunk counts
2) For each source, enumerate rather than search:
- Zotero: `get_source_chunks(zotero_key=<identity_value>, chunk_level="mid")`
- Obsidian/local: `get_source_chunks(source_path=<identity_value>, chunk_level="mid")`
- page with `limit` / `offset` until every mid chunk is processed once
3) Ask a bounded per-source question:
- extract only what is present in that source's chunks
- write an explicit null when the source does not contain the requested material
4) Persist records to disk:
- include source identity, chunk ids reviewed, positive findings, nulls, and uncertainty
5) Run a coverage audit:
- compare processed source count and reviewed chunk count against the frozen register
6) Synthesize only after the audit passes.

Search is for identifying promising sources or terms. Enumeration is what makes
absence claims valid, because top-k search cannot prove a source lacks something.

Done when:
- every source in the frozen register has a processed record,
- every paginated chunk window is accounted for exactly once,
- positive findings and explicit nulls are separated.

---

## Mission template (copy/paste)

When assigning a task, capture the intent in a structured way. This massively improves agent reliability.

```text
MISSION:
- Question / goal:
- Audience:
- Deliverable format: (e.g. 3 paragraphs / 1-page brief / outline / quote pack / annotated bib)
- Scope:
  - Author/work focus (if any):
  - Sources allowed: (primary only / primary+secondary / notes only)
  - Time range (if any):
- Evidence requirements:
  - # of direct quotes:
  - Prefer chunk levels: (fine for quotes; mid/coarse for framing)
  - Include Zotero/Obsidian backlinks for every quote: (yes/no)
- Process constraints:
  - Max time / max iterations:
  - Avoid: (web search / speculation / etc.)
- DONE WHEN:
  - Stopping rules (clear, measurable):

OUTPUT:
- Structure:
- Tone:
- Citation style:
```

---

## Default strategy loop (agent algorithm)

Use this loop unless the mission explicitly asks otherwise.

### Step 0 — Restate the mission as a plan
- Convert the mission into: (a) query plan, (b) evidence plan, (c) stopping rules.
- Decide starting parameters:
  - breadth scan: `max_per_source=1`, `k_recall=50..200`
  - deep dive: `no_diversity=true`, `k_recall=100..250`

### Step 1 — Initial recall query (broad)
Call `search_research_library` with:
- query = user intent phrased descriptively (include attributes, not just keywords)
- `k=10`
- `k_recall=50` (increase if needed)
- diversity on (default) unless deep dive

### Step 2 — Identify candidate sources
From the results, extract:
- top titles/authors
- any obvious primary sources
- any `zotero_key` values (or infer from backlink if available)

If the mission is author/work-specific:
- move to Step 3 quickly (deep dive).

### Step 3 — Deepen / tighten (iterate)
Run 1–N follow-up searches. Examples:
- add `source_type=zotero_fulltext` (primary)
- add `author="Merleau-Ponty"` (post-filter; keep `k_recall` bounded)
- once a work is identified: switch to `zotero_key=<key>` for precision
- for depth: `no_diversity=true` or `max_per_source=10..50`

Guideline: **prefer exact filters** (`zotero_key`, `source_type`, year bounds) over post-filters.

### Step 4 — Expand context for the best evidence
For fine chunks that look like quotable evidence:
- call `get_chunk_context(chunk_id)` to fetch parent context
- prefer quoting from the expanded parent if the fine chunk is clipped

### Step 5 — Draft the output
- Use mid/coarse chunks to frame the explanation.
- Use fine chunks as evidence.
- Every quote should include a backlink.

### Step 6 — Check DONE WHEN rules
Stop when the stopping rules are satisfied.
If not satisfied:
- increase `k_recall` slightly
- loosen filters
- run another targeted query

### Step 7 — Report uncertainty
If you cannot find enough primary quotes:
- say so explicitly
- show what you did try (queries + filters)
- propose next best actions (e.g. ingest missing works)

---

## Error handling (what to do if things go wrong)

### Reranker errors
If rerank fails:
- reranking is designed to fall back to un-reranked results.
- If you suspect rerank is harming latency or relevance, set `no_rerank=true`.

### Too slow / timeouts
- Reduce `k_recall`.
- Avoid broad post-filters (`author`, `title_contains`) unless necessary.
- Prefer exact filters (`zotero_key`, `source_type`).

### No results
- Remove/loosen filters.
- Increase `k_recall`.
- Try a more descriptive query (attributes rather than keywords).

---

## Definition of “done”

A mission is complete when:
1) the user’s request is answered in the requested format (summary/outline/bibliography/etc.),
2) claims are supported by retrieved evidence (quotes/snippets) with backlinks,
3) any remaining uncertainty is stated clearly (what wasn’t found / what needs manual verification).
