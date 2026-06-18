# Sparky Operations

This is the production layout for the v0.6 cutover on Sparky after merge to
`main`. Keep runtime data outside the repository so code deploys, Chroma state,
registries, and copied research sources have separate lifecycles.

## Runtime Layout

Use this data root:

```bash
/home/colin/.local/share/re-searcher/
```

Recommended subdirectories:

```bash
/home/colin/.local/share/re-searcher/chroma/live/
/home/colin/.local/share/re-searcher/output/live/
/home/colin/.local/share/re-searcher/sources/zotero/
/home/colin/.local/share/re-searcher/sources/obsidian-vault/
```

- `chroma/live/`: blank production Chroma store for the initial v0.6 rebuild.
- `output/live/`: registry SQLite, run reports, dashboards, and checkpoints.
- `sources/zotero/`: copied Zotero data directory used for initial build and later
  delta runs.
- `sources/obsidian-vault/`: copied Obsidian vault used for initial build and
  later delta runs.

`deploy/systemd/researcher-chroma.service` defaults Chroma to
`/home/colin/.local/share/re-searcher/chroma/live`. Production `config.yaml`
should set:

```yaml
output_folder: /home/colin/.local/share/re-searcher/output/live
zotero:
  data_directory: /home/colin/.local/share/re-searcher/sources/zotero
obsidian:
  vault_path: /home/colin/.local/share/re-searcher/sources/obsidian-vault
storage:
  endpoint: http://localhost:8000
  collection_name: research_library
```

## Services

Install the user units from `deploy/systemd/` into
`~/.config/systemd/user/`, then reload:

```bash
systemctl --user daemon-reload
systemctl --user start researcher.target
systemctl --user status researcher.target
```

The stack is:

- `researcher-chroma.service`: local Chroma on `127.0.0.1:8000`.
- `researcher-vllm.service`: idempotent vLLM embedder + reranker containers.
- `researcher-mcp.service`: HTTP MCP on `127.0.0.1:8001`.
- `researcher-serve.service`: optional Tailscale serve exposure plus warmup query.

Use `scripts/researcher_svc.sh status` for a quick stack health check.

## Cutover Checklist

1. Merge `v0.6-rebuild` to `main`.
2. Create the runtime directories above.
3. Copy the production Zotero data directory and Obsidian vault into the
   `sources/` paths.
4. Write production `config.yaml` with outside-repo paths and
   `indexing.ledger.execute: true`.
5. Start `researcher.target`.
6. Run a forced initial build into blank Chroma + blank registry.
7. Run `scripts/sources.py status --json` and confirm:
   - registry chunk count equals Chroma count;
   - `ledger_drift.ok` is true;
   - unexpected chunkless units are zero;
   - expected chunkless units are treated as coverage accounting.
8. Run `tests/integration/test_mission_surface_parity.py` against the live stack.
9. Point external agents at Sparky MCP: `http://sparky:8001/mcp/`.

## Periodic Updates

Periodic delta runs should use the same copied source paths and the same
outside-repo Chroma/registry state. Add a user systemd timer after cutover, once
the initial build and agent validation are complete.
