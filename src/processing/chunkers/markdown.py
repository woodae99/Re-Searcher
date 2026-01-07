"""Markdown-aware chunker with Obsidian semantics."""

import re
from typing import Any, Dict, List, Tuple

from src.processing.chunker import TextChunker


class MarkdownChunker:
    """Chunk markdown documents by headings with safe fallback splitting."""

    def __init__(self, config: Dict[str, Any]):
        chunking_config = config.get("chunking", {})
        defaults = chunking_config.get("defaults", chunking_config)
        markdown_config = chunking_config.get("markdown", {})

        self.text_chunker = TextChunker({"chunking": defaults})
        self.header_levels = set(markdown_config.get("header_levels", [1, 2, 3]))
        self.max_section_tokens = markdown_config.get("max_section_tokens", 900)

    def chunk_with_metadata(
        self, text: str, base_metadata: Dict[str, Any]
    ) -> List[Tuple[str, Dict[str, Any]]]:
        if not text or not text.strip():
            return []

        sections = self._split_by_headings(text)
        chunks: List[Tuple[str, Dict[str, Any]]] = []
        for section in sections:
            section_chunks = self._chunk_section(section)
            for chunk_text, contains_code in section_chunks:
                chunk_metadata = base_metadata.copy()
                chunk_metadata.update(
                    {
                        "chunk_level": "mid",
                        "heading_path": section["heading_path"],
                        "contains_code": contains_code,
                    }
                )
                chunks.append((chunk_text, chunk_metadata))

        for idx, (_, metadata) in enumerate(chunks):
            metadata["chunk_index"] = idx
            metadata["total_chunks"] = len(chunks)

        return chunks

    def _split_by_headings(self, text: str) -> List[Dict[str, Any]]:
        lines = text.splitlines()
        sections = []
        current_lines: List[str] = []
        heading_stack: List[str] = []
        current_heading_path = ""
        in_code_block = False
        fence_pattern = re.compile(r"^(```|~~~)")
        heading_pattern = re.compile(r"^(#{1,6})\s+(.*)$")

        def flush_section():
            if current_lines:
                sections.append(
                    {
                        "heading_path": current_heading_path,
                        "content": "\n".join(current_lines).strip(),
                    }
                )

        for line in lines:
            if fence_pattern.match(line.strip()):
                in_code_block = not in_code_block

            heading_match = heading_pattern.match(line) if not in_code_block else None
            if heading_match:
                level = len(heading_match.group(1))
                if level in self.header_levels:
                    flush_section()
                    current_lines = [line]
                    heading_text = heading_match.group(2).strip()

                    while len(heading_stack) >= level:
                        heading_stack.pop()
                    heading_stack.append(heading_text)
                    current_heading_path = " > ".join(heading_stack)
                    continue

            current_lines.append(line)

        flush_section()
        return sections

    def _chunk_section(self, section: Dict[str, Any]) -> List[Tuple[str, bool]]:
        content = section.get("content", "")
        if not content.strip():
            return []

        if self._estimate_tokens(content) <= self.max_section_tokens:
            contains_code = self._contains_code_block(content)
            return [(content.strip(), contains_code)]

        return self._split_large_section(content)

    def _split_large_section(self, content: str) -> List[Tuple[str, bool]]:
        segments = self._segment_by_code_blocks(content)
        chunk_size_chars = self.text_chunker.chunk_size
        chunks: List[Tuple[str, bool]] = []
        current_parts: List[str] = []
        current_size = 0
        current_contains_code = False

        for segment_text, is_code in segments:
            segment_size = len(segment_text)
            if current_parts and current_size + segment_size > chunk_size_chars:
                chunk_text = "\n".join(current_parts).strip()
                if chunk_text:
                    chunks.append((chunk_text, current_contains_code))
                current_parts = []
                current_size = 0
                current_contains_code = False

            current_parts.append(segment_text)
            current_size += segment_size
            current_contains_code = current_contains_code or is_code

        if current_parts:
            chunk_text = "\n".join(current_parts).strip()
            if chunk_text:
                chunks.append((chunk_text, current_contains_code))

        if not chunks:
            return [(content.strip(), self._contains_code_block(content))]

        return chunks

    def _segment_by_code_blocks(self, content: str) -> List[Tuple[str, bool]]:
        lines = content.splitlines()
        segments: List[Tuple[str, bool]] = []
        buffer: List[str] = []
        in_code_block = False
        fence_pattern = re.compile(r"^(```|~~~)")

        def flush_buffer(is_code: bool):
            if buffer:
                segments.append(("\n".join(buffer).strip(), is_code))
                buffer.clear()

        for line in lines:
            if fence_pattern.match(line.strip()):
                if in_code_block:
                    buffer.append(line)
                    flush_buffer(True)
                    in_code_block = False
                    continue
                flush_buffer(False)
                in_code_block = True
                buffer.append(line)
                continue

            buffer.append(line)

        flush_buffer(in_code_block)

        paragraph_segments: List[Tuple[str, bool]] = []
        for segment_text, is_code in segments:
            if is_code:
                paragraph_segments.append((segment_text, True))
                continue
            for paragraph in re.split(r"\n\s*\n", segment_text):
                if paragraph.strip():
                    paragraph_segments.append((paragraph.strip(), False))

        return paragraph_segments

    def _contains_code_block(self, text: str) -> bool:
        return bool(re.search(r"(^|\n)```", text)) or bool(re.search(r"(^|\n)~~~", text))

    def _estimate_tokens(self, text: str) -> int:
        return max(1, len(text) // 4)
