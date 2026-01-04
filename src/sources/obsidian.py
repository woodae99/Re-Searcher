"""Obsidian vault data source for extracting markdown notes."""

import re
from pathlib import Path
from typing import Any, Dict, Iterator, List

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

        for md_file in md_files:
            try:
                document = self._process_markdown_file(md_file)
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

    def _process_markdown_file(self, md_file: Path) -> Document:
        """Process a single markdown file."""
        with open(md_file, "r", encoding="utf-8") as f:
            content = f.read()

        # Parse frontmatter and extract metadata
        frontmatter, body = self._parse_frontmatter(content)

        # Extract wikilinks and backlinks
        wikilinks = self._extract_wikilinks(body)

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
            "backlink": f"obsidian://open?vault={self.vault_path.name}&file={relative_path}",
        }

        # Add frontmatter fields to metadata
        for key, value in frontmatter.items():
            if key not in metadata:
                metadata[key] = value

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

                # Simple YAML parsing (key: value format)
                for line in frontmatter_text.split("\n"):
                    if ":" in line:
                        key, value = line.split(":", 1)
                        key = key.strip()
                        value = value.strip()

                        # Try to parse lists
                        if value.startswith("[") and value.endswith("]"):
                            # Simple list parsing
                            value = [v.strip().strip('"\'') for v in value[1:-1].split(",")]
                        # Try to parse booleans
                        elif value.lower() in ("true", "false"):
                            value = value.lower() == "true"
                        # Try to parse numbers
                        elif value.isdigit():
                            value = int(value)

                        frontmatter[key] = value

        return frontmatter, body

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
