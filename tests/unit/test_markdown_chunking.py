import pytest

from src.processing.chunkers.markdown import MarkdownChunker


@pytest.mark.unit
def test_markdown_chunking_respects_headings_and_code_blocks():
    config = {
        "chunking": {
            "defaults": {"chunk_size": 80, "chunk_overlap": 0, "strategy": "recursive"},
            "markdown": {"header_levels": [1, 2], "max_section_tokens": 10},
        }
    }
    chunker = MarkdownChunker(config)
    text = """
# Heading One

Intro paragraph.

```python
print("hello")
```

## Subheading

More text here.
""".strip()

    chunks = chunker.chunk_with_metadata(text, {"source_type": "obsidian"})

    assert chunks
    assert any(metadata.get("heading_path") == "Heading One" for _, metadata in chunks)
    assert any(metadata.get("contains_code") for _, metadata in chunks)
    assert any("```python" in chunk_text and "```" in chunk_text for chunk_text, _ in chunks)
