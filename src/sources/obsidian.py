"""Obsidian vault data source for extracting markdown notes."""

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List, Set

import yaml

from .base import DataSource, Document


class ObsidianSource(DataSource):
    """Data source for Obsidian vault."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.obsidian_config = config.get("obsidian", {})
        self.vault_path = None

        if self.is_enabled():
            vault_path_str = self.obsidian_config.get("vault_path", "")
            if vault_path_str:
                self.vault_path = Path(vault_path_str).expanduser()

    def is_enabled(self) -> bool:
        """Check if Obsidian source is enabled."""
        return self.obsidian_config.get("enabled", False)

    def validate_config(self) -> bool:
        """Validate Obsidian configuration."""
        if not self.is_enabled():
            return True

        if not self.vault_path:
            print("⚠️  Obsidian vault path not configured")
            return False

        if not self.vault_path.exists():
            print(f"⚠️  Obsidian vault not found: {self.vault_path}")
            return False

        return True

    def fetch_documents(self) -> Iterator[Document]:
        """
        Fetch all markdown documents from Obsidian vault.

        Yields:
            Document objects for each markdown file in the vault.
        """
        if not self.validate_config():
            return

        include_folders = self.obsidian_config.get("include_folders", [])
        exclude_folders = set(self.obsidian_config.get("exclude_folders", []))

        # Find all markdown files
        md_files = self._find_markdown_files(include_folders, exclude_folders)
        print(f"📔 Found {len(md_files)} markdown files in Obsidian vault")

        link_map = self._build_link_map(md_files)

        for md_file in md_files:
            try:
                document = self._process_markdown_file(md_file, link_map)
                if document:
                    yield document
            except Exception as e:
                print(f"  ⚠️  Error processing {md_file.name}: {e}")

    def _find_markdown_files(
        self, include_folders: List[str], exclude_folders: set
    ) -> List[Path]:
        """Find all markdown files in the vault."""
        md_files = []

        if include_folders:
            # Search only in specified folders
            for folder in include_folders:
                folder_path = self.vault_path / folder
                if folder_path.exists():
                    md_files.extend(folder_path.rglob("*.md"))
        else:
            # Search entire vault
            md_files = list(self.vault_path.rglob("*.md"))

        # Filter out excluded folders
        filtered_files = []
        for md_file in md_files:
            # Check if any part of the path is in excluded folders
            relative_path = md_file.relative_to(self.vault_path)
            parts = relative_path.parts

            excluded = False
            for part in parts:
                if part in exclude_folders:
                    excluded = True
                    break

            if not excluded:
                filtered_files.append(md_file)

        return filtered_files

    def _process_markdown_file(
        self, md_file: Path, link_map: Dict[str, str]
    ) -> Document:
        """Process a single markdown file."""
        with open(md_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse frontmatter and extract metadata
        frontmatter, body = self._parse_frontmatter(content)

        # Extract wikilinks and backlinks
        wikilinks = self._extract_wikilinks(body)
        resolved_links = self._resolve_wikilinks(wikilinks, link_map)
        inline_tags = self._extract_inline_tags(body)
        frontmatter_tags = self._extract_frontmatter_tags(frontmatter)
        tags = self._normalize_tags(frontmatter_tags | inline_tags)
        zotero_keys = self._extract_zotero_keys(frontmatter, body)

        # Get relative path from vault root
        relative_path = md_file.relative_to(self.vault_path)

        # Build metadata
        metadata = {
            "source_type": "obsidian",
            "vault_path": str(self.vault_path),
            "file_path": str(md_file),
            "relative_path": str(relative_path),
            "file_name": md_file.name,
            "title": frontmatter.get("title", md_file.stem),
            "wikilinks": wikilinks,
            "links_out": resolved_links,
            "tags": tags,
            "backlink": f"obsidian://open?vault={self.vault_path.name}&file={relative_path}",
            "frontmatter": frontmatter,
        }

        # Add frontmatter fields to metadata
        for key, value in frontmatter.items():
            if key not in metadata:
                metadata[key] = value

        if zotero_keys:
            metadata["zotero_key"] = zotero_keys[0] if len(zotero_keys) == 1 else zotero_keys

        return Document(
            content=body,
            metadata=metadata,
            doc_id=f"obsidian-{relative_path}",
        )

    def _parse_frontmatter(self, content: str) -> tuple[Dict[str, Any], str]:
        """
        Parse YAML frontmatter from markdown content.

        Returns:
            Tuple of (frontmatter_dict, body_without_frontmatter)
        """
        frontmatter = {}
        body = content

        # Check for YAML frontmatter
        if content.startswith("---"):
            parts = content.split("---", 2)
            if len(parts) >= 3:
                frontmatter_text = parts[1].strip()
                body = parts[2].strip()

                try:
                    loaded = yaml.safe_load(frontmatter_text)
                    if isinstance(loaded, dict):
                        frontmatter = loaded
                except yaml.YAMLError:
                    frontmatter = {}

        return frontmatter, body

    def _extract_inline_tags(self, content: str) -> Set[str]:
        pattern = re.compile(r"(?<!\w)#([\w/-]+)")
        return set(pattern.findall(content))

    def _extract_frontmatter_tags(self, frontmatter: Dict[str, Any]) -> Set[str]:
        tags = frontmatter.get("tags", [])
        if isinstance(tags, str):
            return {tags}
        if isinstance(tags, list):
            return {str(tag) for tag in tags}
        return set()

    def _normalize_tags(self, tags: Set[str]) -> List[str]:
        normalized = []
        for tag in tags:
            tag_text = str(tag).lstrip("#").strip().lower()
            if tag_text:
                normalized.append(tag_text)
        return sorted(set(normalized))

    def _extract_zotero_keys(self, frontmatter: Dict[str, Any], content: str) -> List[str]:
        zotero_keys = []
        frontmatter_key = frontmatter.get("zotero_key") or frontmatter.get("citekey")
        if frontmatter_key:
            if isinstance(frontmatter_key, list):
                zotero_keys.extend(str(key) for key in frontmatter_key)
            else:
                zotero_keys.append(str(frontmatter_key))

        inline_keys = re.findall(r"@([A-Za-z0-9_:-]+)", content)
        zotero_keys.extend(inline_keys)

        return list(dict.fromkeys(zotero_keys))

    def _extract_wikilinks(self, content: str) -> List[str]:
        """
        Extract wikilinks from markdown content.

        Examples:
            [[Note Name]]
            [[Note Name|Display Text]]
            [[Folder/Note Name]]
        """
        # Pattern for wikilinks: [[link]] or [[link|alias]]
        pattern = r"\[\[([^\]|]+)(?:\|[^\]]+)?\]\]"
        matches = re.findall(pattern, content)
        return matches

    def _build_link_map(self, md_files: List[Path]) -> Dict[str, str]:
        link_map = {}
        for md_file in md_files:
            try:
                relative_path = md_file.relative_to(self.vault_path)
                link_map[md_file.stem] = str(relative_path)
            except ValueError:
                continue
        return link_map

    def _resolve_wikilinks(self, wikilinks: List[str], link_map: Dict[str, str]) -> List[str]:
        resolved = []
        for link in wikilinks:
            key = Path(link).stem
            resolved.append(link_map.get(key, link))
        return resolved
