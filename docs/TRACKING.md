# Tracking / Keep-an-eye-on list

This file is a lightweight board for things we don’t want to lose track of.

## Active
- [x] **Stage 2 (diversity/dedupe)**: prevent repeated near-identical chunks from same source in final top‑k.
- [ ] **Expose runtime overrides to agent tools**: ensure MCP tool schemas include diversity/rerank overrides and guidance (done for search tool; keep an eye if new tools are added).
- [ ] **Stage 3 (noise reduction)**: mojibake + PDF extraction garbage; review existing filters and decide on a safer approach.
- [ ] **Reranker tuning**: choose a small, reliable rerank model and confirm LM Studio loading/unloading works smoothly under real use.
- [x] **Stage 2.5 (filters)**: author/title/source_type/year/zotero_key filtering wired through CLI + MCP.
- [x] **k_recall override**: CLI+MCP support to bound post-filters.
- [ ] **MCP docs**: ensure tool descriptions remain accurate as new knobs are added (cookbook added; keep aligned).

## Repo hygiene / tests
- [ ] Several existing tests fail in this environment due to missing `config.yaml` and lack of local Chroma in CI context.
  - Action: decide whether to (a) skip/integration-gate them, or (b) provide a test config + mock services.
- [ ] Replace `rg` usage in local dev notes/scripts with `grep` (ripgrep not installed here) OR add a dev dependency note.

## Environment / ops
- [ ] Rotate LM Studio dev keys periodically; avoid pasting keys in chat.
- [ ] Confirm Chroma Windows service binding/firewall stays stable over reboots.
