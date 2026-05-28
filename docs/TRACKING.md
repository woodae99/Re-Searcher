# Tracking / Keep-an-eye-on list

This file is a lightweight board for things we do not want to lose track of.

## Active
- [x] **Stage 2 (diversity/dedupe)**: prevent repeated near-identical chunks from same source in final top-k.
- [ ] **Expose runtime overrides to agent tools**: ensure MCP tool schemas include diversity/rerank overrides and guidance (done for search tool; keep an eye on new tools).
- [ ] **Stage 3 (noise reduction)**: mojibake + PDF extraction garbage; review filters and adopt a safer cleanup strategy.
  - Fulltext-first routing and large-PDF fallback were added, but scanned/OCR cleanup still needs stronger normalization.
- [ ] **Reranker tuning**: choose a small reliable rerank model and confirm LM Studio loading/unloading works smoothly.
- [x] **Stage 2.5 (filters)**: author/title/source_type/year/zotero_key filtering wired through CLI + MCP.
- [x] **k_recall override**: CLI + MCP support to bound post-filters.
- [ ] **MCP docs**: keep tool descriptions accurate as new knobs are added.
- [ ] **Delta ingest guardrails**: add bounded and dry-run delta mode before broad reprocessing.
- [ ] **SQLite/Zotero mode policy**: codify "Zotero closed for heavy ingest" in workflow docs and CLI messaging.
- [ ] **Indexer runbook**: add a short stop/resume-safe runbook linked from docs.
- [ ] **Delta semantic parity (API vs SQLite)**: unify change-detection semantics so transport changes do not change what counts as "changed".
- [ ] **Delta bootstrap behavior**: when delta state is missing/corrupt, do controlled bootstrap (bounded pass) instead of broad "everything changed".
- [ ] **Delta state reliability**: ensure `zotero_delta_state.json` is always created/updated atomically and validated on startup.
- [ ] **Progress observability**: expose live chunk-level embed/store counters (not only doc status transitions) during long runs.
- [ ] **Resume accounting clarity**: distinguish "re-embedded due to interrupted batch" vs "newly embedded" in progress output.

## Repo hygiene / tests
- [ ] Some tests fail in this environment due to missing local services/config.
  - Action: decide whether to skip/integration-gate them, or provide test config + mock services.
- [ ] Replace `rg` usage in local dev notes/scripts with `grep`, or add a dependency note when `rg` is missing.

## Environment / ops
- [ ] Rotate LM Studio dev keys periodically; avoid pasting keys in chat.
- [ ] Confirm Chroma Windows service binding/firewall stability across reboots.
