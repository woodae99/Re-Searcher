# Chunking vNext

## Design statement

Obsidian notes are treated as Markdown with an additional semantic layer (frontmatter, tags, links). All such semantics must be parsed, preserved, and surfaced as metadata. Generic Markdown parsing is insufficient.

## Router rules

The chunking router selects a chunker based on content and metadata:

1. **Zotero annotations** (`source_type == "zotero_annotation"`) use the atomic chunker.
2. **Obsidian notes** or Markdown-looking content use the Markdown chunker.
3. **Huge documents** (token estimate above `chunking.huge_docs.huge_doc_tokens`) use the hierarchical chunker.
4. **Fallback** uses the default `TextChunker`.

## Chunk levels

Chunks include a `chunk_level` metadata field:

- `atomic`
- `coarse`
- `mid`
- `fine`

Hierarchical chunks include `parent_id` so fine chunks can resolve to mid, and mid can resolve to coarse when enabled.

## Markdown semantics

Markdown chunking respects the author’s structure:

- YAML frontmatter is parsed and preserved in metadata.
- Headings (`#`, `##`, `###`) define section boundaries.
- Code blocks are preserved and never split across chunk boundaries.
- Tags and wikilinks are extracted and stored in metadata (`tags`, `links_out`).
- `heading_path` captures section context (e.g., `H1 > H2`).

## Configuration knobs

See `config.example.yaml` for the full configuration:

- `chunking.router_enabled`
- `chunking.id_strategy`
- `chunking.defaults`
- `chunking.markdown`
- `chunking.huge_docs`
